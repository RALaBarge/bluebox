#!/usr/bin/env python3.12
"""TTS daemon — queueing text-to-speech for the whole machine.

Any process (opencode session, script, cron job) POSTs text here and it gets
spoken through the default output device. Requests queue: they play strictly
one-at-a-time in arrival order, so overlapping sessions never talk over each
other.

    POST /say    {"text": "...", "voice": "af_heart", "speed": 1.0,
                  "source": "tickets-session"}   -> {"ok": true, "queued": N}
    GET  /status                                 -> queue depth, now playing
    POST /skip                                   -> stop current utterance
    POST /clear                                  -> drop queue (keeps current)
    GET  /health                                 -> {"ok": true}

Run:  python3.12 tts_daemon.py  (port 8740)
Client: say.py  /  ./say "hello"

Model: mlx-community/Kokoro-82M-bf16 via mlx-audio (Apple-silicon local,
loaded once at startup, ~1s generation for a sentence).
"""

from __future__ import annotations

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8740
SAMPLE_RATE = 24_000
DEFAULT_VOICE = "af_heart"
# Suppress spoken confirmation at stream-close (user request, 2026-08-05)
SILENCE_CONFIRMATIONS = True  # set False to restore spoken feedback
# Also suppress spoken "done" at stream-close (user: Cuban Pete / Mambo Beat)
SUPPRESS_WORDS = {"done", "Done", "DONE"}
DEFAULT_SPEED = 2.0
MAX_TEXT = 5000  # chars per request; longer gets truncated with a note

_q: "queue.Queue[dict]" = queue.Queue()
_state = {
    "now_playing": None,   # dict of the item being spoken, or None
    "spoken": 0,
    "started_at": time.time(),
}
_skip = threading.Event()
_model = None
_model_lock = threading.Lock()


def _load_model():
    global _model
    with _model_lock:
        if _model is None:
            from mlx_audio.tts.utils import load_model
            print("[tts] loading Kokoro-82M ...", flush=True)
            _model = load_model("mlx-community/Kokoro-82M-bf16")
            print("[tts] model ready", flush=True)
    return _model


def _speak(item: dict) -> None:
    """Generate and play one utterance. Blocks until done or skipped."""
    import numpy as np
    import sounddevice as sd

    model = _load_model()
    text = item["text"]
    voice = item.get("voice") or DEFAULT_VOICE
    speed = float(item.get("speed") or DEFAULT_SPEED)

    # Kokoro yields segments; stream them so long texts start fast
    # and /skip can interrupt between segments.
    try:
        for seg in model.generate(text=text, voice=voice, speed=speed):
            if _skip.is_set():
                break
            audio = np.asarray(seg.audio, dtype=np.float32)
            sd.play(audio, SAMPLE_RATE, blocking=False)
            # poll so a skip lands mid-segment too
            while sd.get_stream().active:
                if _skip.is_set():
                    sd.stop()
                    break
                time.sleep(0.05)
    except Exception as e:  # noqa: BLE001 — daemon must not die on one bad item
        print(f"[tts] error speaking ({item.get('source')}): {e}", flush=True)
    finally:
        try:
            sd.stop()
        except Exception:
            pass


def _worker() -> None:
    _load_model()  # warm up before first request
    while True:
        item = _q.get()
        _skip.clear()
        _state["now_playing"] = item
        _speak(item)
        _state["now_playing"] = None
        _state["spoken"] += 1
        _q.task_done()


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._json(200, {"ok": True, "model_loaded": _model is not None})
        elif self.path == "/status":
            np_item = _state["now_playing"]
            self._json(200, {
                "ok": True,
                "queue_depth": _q.qsize(),
                "now_playing": {
                    "source": np_item.get("source"),
                    "text": np_item["text"][:120],
                } if np_item else None,
                "spoken_total": _state["spoken"],
                "uptime_s": int(time.time() - _state["started_at"]),
            })
        else:
            self._json(404, {"ok": False, "error": "unknown path"})

    def do_POST(self):  # noqa: N802
        if self.path == "/say":
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length) or b"{}")
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
        elif self.path == "/skip":
            _skip.set()
            self._json(200, {"ok": True})
        elif self.path == "/clear":
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

    def log_message(self, fmt, *args):  # quiet the per-request lines
        pass


def main() -> None:
    threading.Thread(target=_worker, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[tts] listening on http://127.0.0.1:{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
