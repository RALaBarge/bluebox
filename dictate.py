#!/usr/bin/env python3.12
"""Push-to-talk dictation -> types into whatever window has focus.

Records the mic, transcribes locally, delivers the text. Works on macOS,
Linux, Windows and the BSDs; the platform differences live in
platform_speech.py (engines) and delivery.py (getting text into a window).

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
SILENCE_SECS = 1.2        # --auto stops after this much quiet following speech
NO_SPEECH_SECS = 8.0      # ...or gives up if nothing is said for this long
CALIBRATE_SECS = 0.4      # noise floor is measured over the first stretch
MIN_SPEECH_RMS = 0.004    # speech threshold never goes below this
MAX_SPEECH_RMS = 0.02     # ...or above this, so early speech can't raise it


# --------------------------------------------------------------------------
# record
# --------------------------------------------------------------------------

class EndpointDetector:
    """Decides when the speaker has finished, from per-chunk RMS levels.

    Microphone levels differ a lot (a VM's emulated mic peaked near 0.05), so
    the speech threshold comes from the noise floor measured at the start
    rather than a fixed number. Silence only counts once speech has been
    heard, so waiting a few seconds before talking does not end the recording.
    """

    def __init__(self, silence_secs: float = SILENCE_SECS,
                 no_speech_secs: float = NO_SPEECH_SECS,
                 calibrate_secs: float = CALIBRATE_SECS) -> None:
        self.silence_secs = silence_secs
        self.no_speech_secs = no_speech_secs
        self.calibrate_secs = calibrate_secs
        self.threshold: float | None = None
        self.heard = False
        self._start: float | None = None
        self._floor: list[float] = []
        self._quiet_since: float | None = None

    def feed(self, now: float, rms: float) -> bool:
        """Report one chunk's level at time `now`; True means stop recording."""
        if self._start is None:
            self._start = now
        elapsed = now - self._start
        if elapsed < self.calibrate_secs:
            self._floor.append(rms)
            return False
        if self.threshold is None:
            floor = sorted(self._floor)[len(self._floor) // 2] if self._floor else 0.0
            self.threshold = min(MAX_SPEECH_RMS, max(MIN_SPEECH_RMS, floor * 3))
        if rms >= self.threshold:
            self.heard = True
            self._quiet_since = None
            return False
        if not self.heard:
            return elapsed >= self.no_speech_secs
        if self._quiet_since is None:
            self._quiet_since = now
        return now - self._quiet_since >= self.silence_secs


def record(auto: bool, seconds: float | None) -> "object":
    import numpy as np
    import sounddevice as sd

    # Nobody can press Enter when stdin is a pipe or a file (it would read
    # EOF at once and stop the recording), so stop on silence instead.
    if not auto and seconds is None and not sys.stdin.isatty():
        auto = True

    chunks: list = []
    stop = threading.Event()
    detector = EndpointDetector() if auto else None

    def cb(indata, frames, t, status):
        chunks.append(indata.copy())
        if detector is not None:
            rms = float(np.sqrt((indata ** 2).mean()))
            if detector.feed(time.monotonic(), rms):
                stop.set()

    if not auto and seconds is None:
        threading.Thread(
            target=lambda: (sys.stdin.readline(), stop.set()),
            daemon=True).start()
        print("recording... press Enter to stop", file=sys.stderr)
    else:
        print("recording...", file=sys.stderr)

    started = time.time()
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                        dtype="float32", callback=cb):
        while not stop.is_set():
            time.sleep(0.05)
            if seconds is not None and time.time() - started >= seconds:
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
    """POST to the warm daemon. Returns None only if it isn't there.

    A daemon that answers with an error, or does not answer in time, raises
    instead: loading a second copy of the model in this process would fail the
    same way, only slower.
    """
    req = urllib.request.Request(
        DAEMON + "/transcribe", data=wav,
        headers={"Content-Type": "audio/wav"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read()).get("text", "").strip()
    except urllib.error.HTTPError as e:   # a URLError subclass: catch it first
        try:
            detail = json.loads(e.read()).get("error")
        except (ValueError, AttributeError, OSError):
            detail = None
        raise RuntimeError(f"daemon error {e.code}: {detail or e.reason}") from None
    except TimeoutError:
        raise RuntimeError("daemon did not answer within 120s") from None
    except (urllib.error.URLError, ConnectionError, OSError):
        return None
    except json.JSONDecodeError:
        raise RuntimeError("daemon sent a reply that is not JSON") from None


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

    try:
        text = transcribe(to_wav_bytes(audio))
    except RuntimeError as e:
        print(f"transcription failed: {e}", file=sys.stderr)
        sys.exit(1)
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
