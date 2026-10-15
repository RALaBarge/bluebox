#!/usr/bin/env python3.12
"""Push-to-talk dictation → types into whatever window has focus.

Flow: run it (hotkey/alias), it records the mic until you press Enter
(or --seconds N of audio, or auto-stop on silence with --auto), transcribes
locally with parakeet-mlx (Apple silicon), then delivers the text:

  default   paste into the frontmost window (clipboard + Cmd-V via
            System Events; original clipboard is restored after)
  --type    keystroke the text instead of pasting (slower; for apps that
            block paste)
  --print   just print the transcript to stdout (pipe it anywhere)
  --say     also speak it back through the TTS daemon (confirmation)

    ./dictate            # record, Enter to stop, paste into focused window
    ./dictate --auto     # stop after ~1.2s of silence
    ./dictate --print    # transcript to stdout only

Needs macOS permissions the first time: Microphone + Accessibility
(for the paste keystroke) for the terminal app running it.

Model: mlx-community/parakeet-tdt-0.6b-v2 (~0.6B, very fast, English).
"""

from __future__ import annotations

import argparse
import queue
import subprocess
import sys
import threading
import time

SAMPLE_RATE = 16_000
MODEL = "mlx-community/parakeet-tdt-0.6b-v2"
SILENCE_RMS = 0.012   # tune if --auto cuts off too early/late
SILENCE_SECS = 1.2
MIN_RECORD_SECS = 0.8


def record(auto: bool, seconds: float | None) -> "np.ndarray":
    import numpy as np
    import sounddevice as sd

    chunks: list[np.ndarray] = []
    stop = threading.Event()
    q: queue.Queue = queue.Queue()

    def cb(indata, frames, t, status):
        q.put(indata.copy())

    if not auto and seconds is None:
        threading.Thread(target=lambda: (sys.stdin.readline(), stop.set()),
                         daemon=True).start()
        print("recording... press Enter to stop", file=sys.stderr)
    else:
        print("recording...", file=sys.stderr)

    silent_for = 0.0
    started = time.time()
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                        dtype="float32", callback=cb):
        while not stop.is_set():
            try:
                chunk = q.get(timeout=0.25)
            except queue.Empty:
                continue
            chunks.append(chunk)
            elapsed = time.time() - started
            if seconds is not None and elapsed >= seconds:
                break
            if auto and elapsed > MIN_RECORD_SECS:
                rms = float(np.sqrt((chunk ** 2).mean()))
                dur = len(chunk) / SAMPLE_RATE
                silent_for = silent_for + dur if rms < SILENCE_RMS else 0.0
                if silent_for >= SILENCE_SECS:
                    break
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).flatten()


def transcribe(audio) -> str:
    import tempfile

    import soundfile as sf
    from parakeet_mlx import from_pretrained

    print("transcribing...", file=sys.stderr)
    model = from_pretrained(MODEL)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, audio, SAMPLE_RATE)
        result = model.transcribe(f.name)
    return result.text.strip()


def paste_into_focused(text: str) -> None:
    """Clipboard + Cmd-V into the frontmost window, restoring clipboard."""
    old = subprocess.run(["pbpaste"], capture_output=True).stdout
    subprocess.run(["pbcopy"], input=text.encode(), check=True)
    time.sleep(0.15)
    subprocess.run([
        "osascript", "-e",
        'tell application "System Events" to keystroke "v" using command down',
    ], check=True)
    time.sleep(0.4)  # let the paste land before restoring
    subprocess.run(["pbcopy"], input=old)


def type_into_focused(text: str) -> None:
    esc = text.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run([
        "osascript", "-e",
        f'tell application "System Events" to keystroke "{esc}"',
    ], check=True)


def speak(text: str) -> None:
    import json
    import urllib.request
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8740/say",
            data=json.dumps({"text": text, "source": "dictate"}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass  # fail soft


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
    ap.add_argument("--say", action="store_true",
                    help="speak the transcript back via the TTS daemon")
    args = ap.parse_args()

    audio = record(args.auto, args.seconds)
    if len(audio) < SAMPLE_RATE * 0.3:
        print("no audio captured", file=sys.stderr)
        sys.exit(1)
    text = transcribe(audio)
    if not text:
        print("(empty transcript)", file=sys.stderr)
        sys.exit(1)

    if args.print_only:
        print(text)
    elif args.use_type:
        type_into_focused(text)
        print(f"typed: {text}", file=sys.stderr)
    else:
        paste_into_focused(text)
        print(f"pasted: {text}", file=sys.stderr)

    if args.say:
        speak(text)


if __name__ == "__main__":
    main()
