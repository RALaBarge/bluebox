#!/usr/bin/env python3.12
"""Voice daemon — one warm process for speech in and speech out.

Replaces the old split (tts_daemon.py on :8740 + finger_mouse_stt_app's
stt_server.py on :8741), which loaded two copies of the world and meant
dictation paid a cold model load on every single invocation.

    POST /say        {"text": "...", "voice": "af_heart", "speed": 1.0,
                      "source": "tickets-session"}  -> {"ok": true, "queued": N}
    POST /transcribe body = raw WAV (audio/wav)     -> {"text": "..."}
                     OR {"audio_b64": "...", "format": "wav"}
    POST /skip       stop the current utterance
    POST /clear      drop the queue (keeps the current utterance)
    GET  /status     queue depth, now playing, models loaded
    GET  /health     {"ok": true, ...}

Speech queues; transcription does not. Overlapping TTS is genuinely bad (two
sessions talking over each other), but overlapping transcription is fine and
should stay parallel — so the queue is on /say only.

Run:    python3.12 voice_daemon.py            (port 8740)
Client: say.py  /  dictate.py

Backends, by direction:
  TTS  Apple silicon -> mlx-audio (Kokoro-82M)
       Linux/macOS  -> kokoro-onnx, else piper, else say(1)
  STT  Apple silicon -> parakeet-mlx
       Linux/macOS  -> faster-whisper, else whisper.cpp, else whisper CLI

Nothing is imported until a request needs it, so a Linux box without MLX
still starts and still serves whichever direction it can.
"""

from __future__ import annotations

import base64
import json
import os
import platform
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import platform_speech as ps  # local module: backend selection + helpers

PORT = 8740
SAMPLE_RATE = 24_000
DEFAULT_VOICE = "af_heart"
DEFAULT_SPEED = 1.0          # was 2.0 — Kokoro's usable range is ~0.7–1.3
MAX_TEXT = 5000              # chars per request; longer gets truncated
MAX_AUDIO = 32 * 1024 * 1024  # 32 MB per transcription

_q: "queue.Queue[dict]" = queue.Queue()
_state = {
    "now_playing": None,
    "spoken": 0,
    "transcribed": 0,
    "started_at": time.time(),
}
_skip = threading.Event()
_tts_lock = threading.Lock()
_stt_lock = threading.Lock()
_tts = None   # cached backend object
_stt = None


def log(msg: str) -> None:
    print(f"[voice] {msg}", flush=True)


# --------------------------------------------------------------------------
# TTS
# --------------------------------------------------------------------------

def _get_tts():
    global _tts
    with _tts_lock:
        if _tts is None:
            log(f"loading TTS backend ({ps.tts_backend_name()}) ...")
            _tts = ps.load_tts()
            log("TTS ready")
    return _tts


def _speak(item: dict) -> None:
    """Generate and play one utterance. Blocks until done or skipped.

    The backend lookup is inside the try: if it moves outside, a raising
    _get_tts() escapes _worker and kills the speech thread for the lifetime
    of the process, which leaves the daemon accepting requests forever and
    never speaking again.
    """
    try:
        tts = _get_tts()
        text = item["text"]
        voice = item.get("voice") or DEFAULT_VOICE
        speed = float(item.get("speed") or DEFAULT_SPEED)
        tts.speak(text, voice=voice, speed=speed, should_stop=_skip.is_set)
    except Exception as e:  # noqa: BLE001 — daemon must not die on one bad item
        log(f"error speaking ({item.get('source')}): {e}")
    finally:
        ps.stop_playback()


def _worker(warm: bool = True) -> None:
    # Warm the TTS model before the first request so the first utterance isn't
    # slow. STT warms on first use instead: it's the bigger model and a
    # headless box may never dictate.
    if warm:
        try:
            _get_tts()
        except Exception as e:  # noqa: BLE001
            log(f"TTS unavailable at startup: {e}")
    while True:
        item = _q.get()
        _skip.clear()
        _state["now_playing"] = item
        # Belt and braces: even if _speak grows a new failure mode, the loop
        # must survive it. Losing the worker silently is the worst outcome.
        try:
            _speak(item)
        except Exception as e:  # noqa: BLE001
            log(f"worker recovered from unexpected error: {e}")
        _state["now_playing"] = None
        _state["spoken"] += 1
        _q.task_done()


# --------------------------------------------------------------------------
# STT
# --------------------------------------------------------------------------

def _get_stt():
    global _stt
    with _stt_lock:
        if _stt is None:
            log(f"loading STT backend ({ps.stt_backend_name()}) ...")
            _stt = ps.load_stt()
            log("STT ready")
    return _stt


def _transcribe(audio: bytes, fmt: str = "wav") -> str:
    stt = _get_stt()
    with tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False) as f:
        f.write(audio)
        path = f.name
    try:
        return (stt.transcribe(path) or "").strip()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "voice/2.0"

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- helpers ----------------------------------------------------------
    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return b""
        if length > MAX_AUDIO:
            raise ValueError(f"body too large ({length} bytes)")
        return self.rfile.read(length)

    # -- GET --------------------------------------------------------------
    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/health":
            self._json(200, {
                "ok": True,
                "tts_loaded": _tts is not None,
                "stt_loaded": _stt is not None,
                "tts_backend": ps.tts_backend_name(),
                "stt_backend": ps.stt_backend_name(),
                "platform": platform.system(),
            })
        elif path == "/status":
            np_item = _state["now_playing"]
            self._json(200, {
                "ok": True,
                "queue_depth": _q.qsize(),
                "now_playing": {
                    "source": np_item.get("source"),
                    "text": np_item["text"][:120],
                } if np_item else None,
                "spoken_total": _state["spoken"],
                "transcribed_total": _state["transcribed"],
                "uptime_s": int(time.time() - _state["started_at"]),
                "tts_backend": ps.tts_backend_name(),
                "stt_backend": ps.stt_backend_name(),
            })
        else:
            self._json(404, {"ok": False, "error": "unknown path"})

    # -- POST -------------------------------------------------------------
    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            body = self._read_body()
        except (ValueError, OSError) as e:
            self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/say":
            self._handle_say(body)
        elif path == "/transcribe":
            self._handle_transcribe(body)
        elif path == "/skip":
            _skip.set()
            self._json(200, {"ok": True})
        elif path == "/clear":
            dropped = 0
            try:
                while True:
                    _q.get_nowait()
                    _q.task_done()
                    dropped += 1
            except queue.Empty:
                pass
            self._json(200, {"ok": True, "dropped": dropped})
        else:
            self._json(404, {"ok": False, "error": "unknown path"})

    def _handle_say(self, body: bytes) -> None:
        try:
            data = json.loads(body or b"{}")
        except (ValueError, json.JSONDecodeError) as e:
            self._json(400, {"ok": False, "error": f"bad json: {e}"})
            return
        text = (data.get("text") or "").strip()
        if not text:
            self._json(400, {"ok": False, "error": "empty text"})
            return
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + " ... truncated."
        _q.put({
            "text": text,
            "voice": data.get("voice"),
            "speed": data.get("speed"),
            "source": data.get("source", "unknown"),
            "ts": time.time(),
        })
        self._json(200, {"ok": True, "queued": _q.qsize()})

    def _handle_transcribe(self, body: bytes) -> None:
        if not body:
            self._json(400, {"ok": False, "error": "empty body"})
            return

        ctype = (self.headers.get("Content-Type") or "").lower()
        fmt = "wav"
        audio = body

        # Two shapes: raw WAV bytes (what finger_mouse_stt posts) or JSON with
        # base64, which is easier to send from shells that dislike binary.
        if "json" in ctype or body.lstrip()[:1] == b"{":
            try:
                data = json.loads(body)
            except (ValueError, json.JSONDecodeError) as e:
                self._json(400, {"ok": False, "error": f"bad json: {e}"})
                return
            b64 = data.get("audio_b64")
            if not b64:
                self._json(400, {"ok": False, "error": "no audio_b64"})
                return
            try:
                # validate=True matters: b64decode silently discards characters
                # outside the alphabet, so garbage would otherwise sail through
                # and surface as a confusing 500 from the STT backend.
                audio = base64.b64decode(b64, validate=True)
            except (ValueError, TypeError, base64.binascii.Error) as e:
                self._json(400, {"ok": False, "error": f"bad base64: {e}"})
                return
            if not audio:
                self._json(400, {"ok": False, "error": "decoded audio is empty"})
                return
            fmt = (data.get("format") or "wav").lstrip(".")

        try:
            text = _transcribe(audio, fmt)
        except Exception as e:  # noqa: BLE001 — never die on bad audio
            self._json(500, {"ok": False, "error": str(e)})
            return
        _state["transcribed"] += 1
        self._json(200, {"text": text})

    def log_message(self, fmt, *args):
        pass


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="voice daemon (TTS + STT)")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-warm", action="store_true",
                    help="don't load models until first request")
    args = ap.parse_args()

    # Launchd/systemd start with a minimal PATH; the STT backends shell out to
    # ffmpeg, so make the usual install locations visible regardless.
    extra = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]
    os.environ["PATH"] = os.pathsep.join(
        [p for p in extra if os.path.isdir(p)] +
        [os.environ.get("PATH", "")]
    )

    # The worker always runs; --no-warm only skips the eager model load at
    # startup, so the first request pays the load instead. An earlier version
    # started no worker at all under --no-warm, which left /say accepting
    # requests that were never spoken — a silent black hole.
    threading.Thread(target=_worker, daemon=True,
                     kwargs={"warm": not args.no_warm}).start()

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    log(f"listening on http://127.0.0.1:{args.port}")
    log(f"  TTS: {ps.tts_backend_name()}   STT: {ps.stt_backend_name()}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
