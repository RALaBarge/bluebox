#!/usr/bin/env python3
"""Tap-Ctrl dictation (macOS, Linux, Windows): tap Ctrl to start recording,
tap Ctrl again to stop, transcribe, and deliver the text to the focused window.

    python hotkey.py                  # tap Ctrl to start, tap Ctrl to finish
    python hotkey.py --paste          # paste instead of typing
    python hotkey.py --clipboard      # clipboard only

Run it with pythonw.exe on Windows for no console window. Ctrl+C in the
console exits.

A tap is Ctrl pressed and released within TAP_SECS with no other key in
between, so Ctrl+C, Ctrl+V and the like never trigger it. Mouse clicks are
not seen by the key listener, so a quick Ctrl+click can count as a tap.
Needs `pip install pynput`; recording, transcription and delivery are the
same code `dictate.py` uses, and transcription goes through the daemon when
it is up.

Platform notes
  macOS    Grant the terminal Input Monitoring (to see Ctrl) plus the
           Microphone and Accessibility permissions dictate.py already needs.
  Linux    X11 only. pynput cannot see global keys under Wayland; there,
           bind `dictate --auto` to a key in the desktop environment.
  Windows  Works as is.

A short beep marks each state: high = recording started, low = stopped and
working, double low = nothing to deliver.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time

import dictate
import delivery

TAP_SECS = 0.4


def beep(freq: int, ms: int = 90) -> None:
    """Audible cue. Windows gets a real tone; elsewhere the terminal bell."""
    try:
        if sys.platform == "win32":
            import winsound
            winsound.Beep(freq, ms)
        else:
            sys.stderr.write("")
            sys.stderr.flush()
            time.sleep(ms / 1000)
    except Exception:  # noqa: BLE001 — a missing beep must not stop dictation
        pass


class Recorder:
    """Mic capture that starts and stops on demand."""

    def __init__(self) -> None:
        self._chunks: list = []
        self._stream = None

    @property
    def active(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        import sounddevice as sd
        self._chunks = []
        self._stream = sd.InputStream(
            samplerate=dictate.SAMPLE_RATE, channels=1, dtype="float32",
            callback=lambda indata, *_: self._chunks.append(indata.copy()))
        self._stream.start()

    def stop(self):
        import numpy as np
        stream, self._stream = self._stream, None
        stream.stop()
        stream.close()
        if not self._chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._chunks).flatten()


def deliver(audio, mode: str) -> None:
    if len(audio) < dictate.SAMPLE_RATE * 0.3:
        print("no audio captured", file=sys.stderr)
        beep(300), beep(300)
        return
    try:
        text = dictate.transcribe(dictate.to_wav_bytes(audio), quiet=True)
    except Exception as e:  # noqa: BLE001 — keep the hotkey loop alive
        print(f"transcription failed: {e}", file=sys.stderr)
        beep(300), beep(300)
        return
    if not text:
        print("(empty transcript)", file=sys.stderr)
        beep(300), beep(300)
        return
    ok, note = delivery.insert(text, mode)
    if ok:
        print(f"{note}: {text}", file=sys.stderr)
    else:
        # Never lose the transcript just because delivery failed.
        print(f"delivery failed ({note}); transcript:\n{text}", file=sys.stderr)
        beep(300), beep(300)


def main() -> None:
    ap = argparse.ArgumentParser(description="tap-Ctrl dictation")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--paste", action="store_true",
                      help="paste instead of typing")
    mode.add_argument("--clipboard", action="store_true",
                      help="clipboard only, no keystroke")
    args = ap.parse_args()
    how = "paste" if args.paste else "clipboard" if args.clipboard else "type"

    try:
        from pynput import keyboard
    except ImportError as e:
        # pynput's own message names the real cause, e.g. a missing Xlib.
        sys.exit(f"hotkey.py needs pynput (pip install pynput; on the BSDs "
                 f"also python-xlib): {e}")

    ctrl_keys = {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r}
    rec = Recorder()
    busy = threading.Lock()
    ctrl_down_at: list[float | None] = [None]   # None = no tap in progress

    def on_tap() -> None:
        with busy:
            if not rec.active:
                try:
                    rec.start()
                except Exception as e:  # noqa: BLE001
                    print(f"cannot open microphone: {e}", file=sys.stderr)
                    beep(300), beep(300)
                    return
                print("recording...", file=sys.stderr)
                beep(880)
            else:
                audio = rec.stop()
                beep(440)
                threading.Thread(target=deliver, args=(audio, how),
                                 daemon=True).start()

    def on_press(key) -> None:
        if key in ctrl_keys:
            # Key auto-repeat re-sends press; keep the first.
            if ctrl_down_at[0] is None:
                ctrl_down_at[0] = time.monotonic()
        else:
            ctrl_down_at[0] = None   # Ctrl was a modifier, not a tap

    def on_release(key) -> None:
        if key in ctrl_keys:
            t, ctrl_down_at[0] = ctrl_down_at[0], None
            if t is not None and time.monotonic() - t <= TAP_SECS:
                threading.Thread(target=on_tap, daemon=True).start()

    print(f"Tap Ctrl to start recording, tap again to stop and {how}. "
          "Ctrl+C to quit.", file=sys.stderr)
    with keyboard.Listener(on_press=on_press, on_release=on_release) as ls:
        try:
            ls.join()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
