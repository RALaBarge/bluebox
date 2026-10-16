#!/usr/bin/env python3.12
"""Push-to-talk dictation -> types into whatever window has focus.

Records the mic, transcribes locally, delivers the text. Works on macOS and
Debian-family Linux; the platform differences live in platform.py.

    ./dictate                 # record, Enter to stop, paste into focused window
    ./dictate --auto          # stop after ~1.2s of silence
    ./dictate --print         # transcript to stdout only (pipe it anywhere)
    ./dictate --type          # keystroke instead of paste
    ./dictate --clipboard     # clipboard only, no keystroke
    ./dictate --say           # also speak it back through the daemon
    ./dictate --check         # report which backends are usable

Transcription goes through the voice daemon when it's running, which is the
whole point: the model loads once instead of on every invocation. If the
daemon isn't up we fall back to an in-process backend so dictation still
works standalone — it's just slower to start.

Recording never needs the daemon, so `--print` on a machine with no audio
stack at all still does something sensible.

Permissions (first run only)
  macOS  Microphone + Accessibility (for the paste keystroke)
  Linux  ydotool needs ydotoold running and your user in the input group;
         wtype/xdotool need your session to be the one they're typing into.
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
import urllib.error
import urllib.request

DAEMON = "http://127.0.0.1:8740"
SAMPLE_RATE = 16_000
SILENCE_RMS = 0.012   # tune if --auto cuts off too early/late
SILENCE_SECS = 1.2
MIN_RECORD_SECS = 0.8


# --------------------------------------------------------------------------
# record
# --------------------------------------------------------------------------

def record(auto: bool, seconds: float | None) -> "object":
    import numpy as np
    import sounddevice as sd

    chunks: list = []
    stop = threading.Event()

    def cb(indata, frames, t, status):
        chunks.append(indata.copy())

    if not auto and seconds is None:
        threading.Thread(
            target=lambda: (sys.stdin.readline(), stop.set()),
            daemon=True).start()
        print("recording... press Enter to stop", file=sys.stderr)
    else:
        print("recording...", file=sys.stderr)

    silent_for = 0.0
    started = time.time()
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                        dtype="float32", callback=cb):
        while not stop.is_set():
            time.sleep(0.05)
            elapsed = time.time() - started
            if seconds is not None and elapsed >= seconds:
                break
            if auto and elapsed > MIN_RECORD_SECS and chunks:
                chunk = chunks[-1]
                rms = float(np.sqrt((chunk ** 2).mean()))
                dur = len(chunk) / SAMPLE_RATE
                silent_for = silent_for + dur if rms < SILENCE_RMS else 0.0
                if silent_for >= SILENCE_SECS:
                    break

    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).flatten()


def to_wav_bytes(audio) -> bytes:
    import io
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, format="WAV")
    return buf.getvalue()


# --------------------------------------------------------------------------
# transcribe — daemon first, in-process fallback
# --------------------------------------------------------------------------

def transcribe_via_daemon(wav: bytes) -> str | None:
    """POST to the warm daemon. Returns None if it isn't there."""
    req = urllib.request.Request(
        DAEMON + "/transcribe", data=wav,
        headers={"Content-Type": "audio/wav"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read()).get("text", "").strip()
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return None
    except json.JSONDecodeError:
        return None


def transcribe_in_process(wav: bytes) -> str:
    """Cold fallback: spin up a backend here and now."""
    import tempfile
    import platform_speech as ps

    stt = ps.load_stt()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav)
        path = f.name
    try:
        return (stt.transcribe(path) or "").strip()
    finally:
        import os
        try:
            os.unlink(path)
        except OSError:
            pass


def transcribe(wav: bytes, quiet: bool = False) -> str:
    print("transcribing...", file=sys.stderr)
    text = transcribe_via_daemon(wav)
    if text is not None:
        return text
    if not quiet:
        print("[dictate] daemon not running; loading a model in-process "
              "(start voice_daemon.py for instant transcription)",
              file=sys.stderr)
    return transcribe_in_process(wav)


# --------------------------------------------------------------------------
# speak-back
# --------------------------------------------------------------------------

def speak(text: str) -> None:
    req = urllib.request.Request(
        DAEMON + "/say",
        data=json.dumps({"text": text, "source": "dictate"}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=3)
    except Exception:  # noqa: BLE001 — fail soft
        pass


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def check() -> int:
    import platform as p

    import delivery
    import platform_speech as ps
    from delivery import have_delivery

    ok, why = have_delivery()
    print(f"platform : {p.system()}")
    print(f"delivery : {'OK' if ok else 'NO'} — {why}")
    print(f"tts      : {ps.tts_backend_name()}")
    print(f"stt      : {ps.stt_backend_name()}")
    try:
        with urllib.request.urlopen(DAEMON + "/health", timeout=2) as r:
            h = json.loads(r.read())
        print(f"daemon   : up — tts={h.get('tts_backend')} "
              f"stt={h.get('stt_backend')}")
    except Exception:  # noqa: BLE001
        print(f"daemon   : down ({DAEMON}) — dictation will load a model "
              "in-process each run")
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(description="local push-to-talk dictation")
    ap.add_argument("--auto", action="store_true",
                    help="stop on ~1.2s of silence instead of Enter")
    ap.add_argument("--seconds", type=float, default=None,
                    help="record a fixed duration")
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="print transcript to stdout, don't touch any window")
    ap.add_argument("--type", dest="use_type", action="store_true",
                    help="keystroke instead of paste")
    ap.add_argument("--clipboard", action="store_true",
                    help="clipboard only, no keystroke")
    ap.add_argument("--say", action="store_true",
                    help="speak the transcript back via the daemon")
    ap.add_argument("--check", action="store_true",
                    help="report backends and exit")
    args = ap.parse_args()

    if args.check:
        sys.exit(check())

    audio = record(args.auto, args.seconds)
    if len(audio) < SAMPLE_RATE * 0.3:
        print("no audio captured", file=sys.stderr)
        sys.exit(1)

    text = transcribe(to_wav_bytes(audio))
    if not text:
        print("(empty transcript)", file=sys.stderr)
        sys.exit(1)

    if args.print_only:
        print(text)
    else:
        import delivery
        mode = "type" if args.use_type else (
            "clipboard" if args.clipboard else "paste")
        ok, note = delivery.insert(text, mode)
        if ok:
            print(f"{note}: {text}", file=sys.stderr)
        else:
            # Never lose the transcript just because delivery failed.
            print(f"delivery failed ({note}); transcript:\n{text}",
                  file=sys.stderr)
            sys.exit(2)

    if args.say:
        speak(text)


if __name__ == "__main__":
    main()
