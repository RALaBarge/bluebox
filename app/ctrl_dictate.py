#!/usr/bin/env python3.12
"""Ctrl-tap dictation daemon (reliable, event-tap based).

Uses a global CGEventTap (works everywhere once Accessibility is granted —
verified 2026-08-21) to detect the Control modifier across ALL apps, regardless
of focus. Tap Control to start talking, tap again to stop; transcribes with the
local STT server and pastes into the window that was frontmost when you started.

  tap Control  -> start recording (beep)
  tap again    -> stop, transcribe, paste into the captured target window
                 (silent on success; double low beep if transcription failed)

Run:  python3 -m app.ctrl_dictate
"""
import json
import os
import sys
import threading
import time

os.environ["PATH"] = "/opt/homebrew/bin:" + os.environ.get("PATH", "")

from CoreFoundation import (CFRunLoopGetCurrent, kCFRunLoopDefaultMode,
                            CFRunLoopAddSource, CFRunLoopStop, CFRunLoopRun)
import CoreFoundation
import Quartz

from .stt import STTEngine

STT_URL = "http://127.0.0.1:8740"
SAMPLE_RATE = 16000
LOG_FILE = "/tmp/ctrl_dictate.log"
DOUBLE_TAP_MS = 0.45   # two Control presses this close = a double-tap (toggles)


def _beep(freq=880, dur=0.12):
    """Short non-speech tone on the output device as a record cue. Non-lexical,
    so it never becomes part of the transcription (unlike the old spoken
    "recording" cue, which the mic picked up and parakeet transcribed)."""
    import numpy as np
    import sounddevice as sd

    def go():
        try:
            sr = 20000
            t = np.linspace(0, dur, int(sr * dur), endpoint=False)
            tone = (np.sin(2 * np.pi * freq * t) * 0.4).astype(np.float32)
            sd.play(tone, sr)
            sd.wait()
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


def _error_beep():
    """Two low tones = transcription failed (no audio / no speech / error).
    Spoken feedback is retired per Ryan 2026-09-14 — never talk to him."""
    def go():
        try:
            _beep(freq=220, dur=0.15)
            time.sleep(0.18)
            _beep(freq=220, dur=0.15)
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


def _frontmost():
    from AppKit import NSWorkspace
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return {"pid": app.processIdentifier(), "name": app.localizedName()}
    except Exception:
        return None


def _activate(pid, name):
    from AppKit import NSRunningApplication
    if pid:
        try:
            apps = NSRunningApplication.runningApplicationsWithProcessIdentifier(pid)
            if apps and apps[0].activateWithOptions_(0):
                return True
        except Exception:
            pass
    if name:
        try:
            from .typer_macos import _osascript_focus
            return bool(_osascript_focus(name))
        except Exception:
            pass
    return False


def _paste_target(text: str, target):
    import subprocess
    if target:
        _activate(target.get("pid"), target.get("name", ""))
        time.sleep(0.25)
    old = subprocess.run(["pbpaste"], capture_output=True).stdout
    try:
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
        time.sleep(0.15)
        subprocess.run([
            "osascript", "-e",
            'tell application "System Events" to keystroke "v" using command down',
        ], check=True)
        time.sleep(0.4)
    except Exception:
        pass
    try:
        subprocess.run(["pbcopy"], input=old)
    except Exception:
        pass


class _Recorder:
    def __init__(self, rate=SAMPLE_RATE):
        self.rate = rate
        self.samples = []
        self._armed = False

    def _cb(self, indata, frames, t, status):
        if self._armed:
            self.samples.append(indata.copy())

    def arm(self):
        import sounddevice as sd
        if not getattr(self, "_stream", None):
            self._stream = sd.InputStream(samplerate=self.rate, channels=1,
                                          dtype="float32", callback=self._cb)
            self._stream.start()
        self._armed = True
        self.samples = []

    def disarm(self):
        self._armed = False

    def drain(self):
        import numpy as np
        if not self.samples:
            return None
        audio = np.concatenate([np.asarray(s).flatten() for s in self.samples])
        if len(audio) < int(self.rate * 0.3):
            return None
        return audio.astype(np.float32)


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def main():
    stt = STTEngine("remote_server", STT_URL)
    rec = _Recorder()
    state = {"recording": False, "ctrl": False, "target": None, "started": 0.0,
             "last_release": -1.0}

    def stop():
        if not state["recording"]:
            return
        state["recording"] = False
        rec.disarm()
        dur = time.time() - state["started"]
        audio = rec.drain()
        log(f"stop after {round(dur,1)}s len={len(rec.samples)}")
        if audio is None:
            _error_beep()
            return
        target = state["target"]

        def go():
            try:
                text = stt.transcribe(audio, SAMPLE_RATE) or ""
            except Exception as e:
                log(f"transcribe EXC {e!r}")
                _error_beep()
                return
            cleaned = text.replace("<unk>", "").replace("<|unk|>", "").strip()
            log(f"transcribe -> {text!r}")
            if not cleaned:
                _error_beep()
                return
            _paste_target(cleaned, target)
            # no spoken "done" — silence on success (Ryan 2026-09-14);
        threading.Thread(target=go, daemon=True).start()

    def on_flags(proxy, etype, event, userinfo):
        flags = Quartz.CGEventGetFlags(event)
        ctrl = bool(flags & Quartz.kCGEventFlagMaskControl)
        if ctrl == state["ctrl"]:
            return event
        was = state["ctrl"]
        state["ctrl"] = ctrl
        now = time.time()
        if ctrl and not was:
            # Control went DOWN — the SECOND press of a rapid double-tap toggles.
            gap = now - state["last_release"]
            if state["last_release"] > 0 and gap < DOUBLE_TAP_MS:
                # consume this double-tap so a 3rd press doesn't re-trigger
                state["last_release"] = -1.0
                if not state["recording"]:
                    state["recording"] = True
                    state["started"] = time.time()
                    state["target"] = _frontmost()
                    rec.arm()
                    log(f"talk start target={state['target']}")
                    _beep()  # non-speech cue so the mic never captures a word
                else:
                    log("double-tap during recording -> stop")
                    stop()
        else:
            # Control went UP — record when it happened, for double-tap timing.
            state["last_release"] = now
        return event

    mask = Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
    tap = Quartz.CGEventTapCreate(
        Quartz.kCGHIDEventTap, Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionListenOnly, mask,
        on_flags, None)
    if not tap:
        log("CGEventTapCreate FAILED — not trusted?")
        return
    source = CoreFoundation.CFMachPortCreateRunLoopSource(None, tap, 0)
    CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopDefaultMode)
    # install the callback via a persistent reference so it isn't GC'd
    log("ctrl-tap dictate (event tap) starting; tap Control to record, tap again to stop")
    try:
        CFRunLoopRun()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()