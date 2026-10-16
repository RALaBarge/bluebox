#!/usr/bin/env python3.12
"""Always-on Magicsee R1 button daemon — bind-to-window + toggle talk.

CONSTRAINT (measured 2026-08-21): the R1 is MOMENTARY-ONLY at the HID level —
even a 2-second physical hold sends down+release in the same instant. So
press-and-hold cannot work. Interaction model is built on momentary taps:

  Ctrl + tap  -> bind   this button to the currently-frontmost window
  tap         -> focus  the window bound to this button (jump to it)
  PTT button  -> toggle dictation: tap to start talking, auto-stops on
                 ~1.2s silence (or tap again), transcribes, pastes into the
                 focused window.

The PTT button code defaults to 0x20 (the central stick click); override with
--ptt CODE. All other buttons are treated as window keys (bind/focus).

The HID event loop never blocks on network/STT — focus + transcribe run in
worker threads so the hotpath stays instant. Safety: only ever seizes the R1's
own vid/pid; Apple-internal (or any non-R1) HID is never touched.
"""
import argparse
import json
import os
import queue
import threading
import time
from typing import Optional

# macOS: the parakeet STT model shells out to the ffmpeg binary, which lives in
# /opt/homebrew/bin. The .venv interpreter does not put that dir on PATH, so
# shutil.which('ffmpeg') resolves to nothing there. Prepend it so ffmpeg is
# found regardless of how the daemon is launched (venv vs system python).
os.environ["PATH"] = (
    "/opt/homebrew/bin:" + os.environ.get("PATH", "")
)

import hid

try:
    import Quartz
except Exception:  # pragma: no cover
    Quartz = None

from .stt import STTEngine

R1_VID = 0x004C
R1_PID = 0x014C
PTT_DEFAULT = 0x20   # central analogue / talk button (momentary)
STT_URL = "http://127.0.0.1:8740"   # unified daemon: /transcribe and /say
TTS_URL = "http://127.0.0.1:8740"
SAMPLE_RATE = 16000
SILENCE_RMS = 0.012
SILENCE_SECS = 1.2
MIN_RECORD_SECS = 0.8
SPEECH_RMS = 0.008      # window RMS above this counts as real speech
MAX_RECORD_SECS = 45    # never record forever if nothing is said

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "finger-mouse-stt")
BIND_FILE = os.path.join(CONFIG_DIR, "r1_bindings.json")
LOG_FILE = os.path.join("/tmp", "r1_daemon.log")


# --------------------------------------------------------------------------- #
# Fire-and-forget helpers (never block the HID loop).                        #
# --------------------------------------------------------------------------- #
def _say(text: str):
    """TTS feedback via the 8740 daemon (non-blocking)."""
    import urllib.request

    def go():
        try:
            req = urllib.request.Request(
                TTS_URL + "/say",
                data=json.dumps({"text": text, "voice": "af_heart"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


def _paste_into_focused(text: str, target: Optional[dict] = None):
    """Clipboard + Cmd-V into a chosen target window; restore the old clipboard.
    Mirrors dictate.py's proven timed paste (sleeps before/after so the paste
    actually lands before the clipboard is restored / the Cmd-V fires).

    target: dict {pid, name} to activate first; if None, pastes into whatever
    is frontmost.
    """
    import subprocess
    import time

    if target and target.get("pid"):
        _activate_by_pid(target["pid"])
    elif target and target.get("name"):
        _activate_by_name(target["name"])
    if target:
        time.sleep(0.25)  # let activation settle before pasting

    def old_clip():
        try:
            return subprocess.run(["pbpaste"], capture_output=True).stdout
        except Exception:
            return b""

    old = old_clip()
    try:
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
        time.sleep(0.15)  # clipboard must be readable before Cmd-V
        subprocess.run([
            "osascript", "-e",
            'tell application "System Events" to keystroke "v" using command down',
        ], check=True)
        time.sleep(0.4)   # let the paste land before restoring the clipboard
    except Exception:
        pass
    try:
        subprocess.run(["pbcopy"], input=old)
    except Exception:
        pass


def _frontmost_app_info():
    from AppKit import NSWorkspace
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return {"pid": app.processIdentifier(), "name": app.localizedName()}
    except Exception:
        return None


def _activate_by_pid(pid):
    try:
        from AppKit import NSRunningApplication
        apps = NSRunningApplication.runningApplicationsWithProcessIdentifier(pid)
        if apps:
            return bool(apps[0].activateWithOptions_(0))
    except Exception:
        pass
    return False


def _activate_by_name(name):
    from .typer_macos import _osascript_focus
    try:
        return bool(_osascript_focus(name))
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Tiny audio capture with silence auto-stop (own buffer so we own the loop). #
# --------------------------------------------------------------------------- #
class TalkRecorder:
    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._cb_q: queue.Queue = queue.Queue()
        self._armed = threading.Event()
        self._running = False
        self.samples: list = []      # accumulated float32 samples while armed
        self.last_rms = 0.0          # rolling for silence detection
        self.saw_speech = False      # any window above SPEECH_RMS?
        self.max_rms = 0.0

    def _cb(self, indata, frames, t, status):
        if self._armed.is_set():
            self.samples.append(indata.copy())

    def arm(self):
        if not self._running:
            import sounddevice as sd
            self._stream = sd.InputStream(
                samplerate=self.sample_rate, channels=1,
                dtype="float32", callback=self._cb)
            self._stream.start()
            self._running = True
        self.samples = []
        self.last_rms = 0.0
        self.saw_speech = False
        self.max_rms = 0.0
        self._armed.set()

    def disarm(self):
        self._armed.clear()

    def recent_rms(self) -> float:
        """RMS of the most recent ~0.5s of captured audio (silence detection)."""
        import numpy as np
        if not self.samples:
            return 0.0
        # last ~0.5s worth of samples
        n = max(1, int(0.5 * self.sample_rate))
        recent = np.concatenate([np.asarray(s).flatten() for s in self.samples[-20:]])
        recent = recent[-n:]
        rms = float(np.sqrt((recent ** 2).mean()))
        self.last_rms = rms
        if rms > SPEECH_RMS:
            self.saw_speech = True
        if rms > self.max_rms:
            self.max_rms = rms
        return rms

    def is_silent(self, silence_rms: float = SILENCE_RMS) -> bool:
        return self.recent_rms() < silence_rms

    def has_real_speech(self) -> bool:
        return self.saw_speech

    def drain_audio(self):
        """Return the full captured waveform (or None if nothing meaningful)."""
        import numpy as np
        if not self.samples:
            return None
        audio = np.concatenate([np.asarray(s).flatten() for s in self.samples])
        audio = audio.astype(np.float32)
        if len(audio) < int(self.sample_rate * 0.3):
            return None
        return audio


# --------------------------------------------------------------------------- #
# The daemon.                                                                #
# --------------------------------------------------------------------------- #
class R1Daemon:
    def __init__(self, ptt_code: int = PTT_DEFAULT):
        self.ptt_code = ptt_code
        self._stop = threading.Event()
        self._bindings: dict[int, dict] = self._load_bindings()
        self._stt = STTEngine("remote_server", STT_URL)
        self._prev_buttons = 0
        self._press_ctrl: dict[int, bool] = {}
        self._rec = None  # TalkRecorder (created on first talk)
        self._recording = False
        self._rec_since = 0.0
        self._silent_for = 0.0

    # persistence ------------------------------------------------------------ #
    def _load_bindings(self) -> dict:
        try:
            with open(BIND_FILE) as f:
                return {int(k): v for k, v in json.load(f).items()}
        except (OSError, json.JSONDecodeError, ValueError):
            return {}

    def _save_bindings(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(BIND_FILE, "w") as f:
            json.dump(self._bindings, f, indent=2)

    def _next_r1_paths(self):
        out = []
        for d in hid.enumerate():
            if d.get("vendor_id") != R1_VID or d.get("product_id") != R1_PID:
                continue
            path = d["path"]
            if isinstance(path, str):
                path = path.encode()
            out.append((path, d.get("usage_page", 0), d.get("usage", 0)))
        return out

    # buttons ---------------------------------------------------------------- #
    def _buttons_from_report(self, report: bytes) -> int:
        if len(report) >= 2 and report[0] == 0x02:
            return report[1] & 0xFF
        return report[0] & 0xFF

    def _ctrl_held(self) -> bool:
        if Quartz is None:
            return False
        try:
            flags = Quartz.CGEventSourceFlagsState(
                Quartz.kCGEventSourceStateCombinedSessionState)
            return bool(flags & Quartz.kCGEventFlagMaskControl)
        except Exception:
            return False

    # actions ----------------------------------------------------------------- #
    def _bind_current(self, code: int):
        info = _frontmost_app_info()
        if not info:
            _say("no window")
            return
        self._bindings[code] = info
        self._save_bindings()
        _say(f"bound {info['name']}")

    def _focus_bound(self, code: int):
        b = self._bindings.get(code)
        if not b:
            _say("not bound")
            return
        ok = False
        if b.get("pid"):
            ok = _activate_by_pid(b["pid"])
        if not ok and b.get("name"):
            ok = _activate_by_name(b["name"])
        _say("focus" if ok else "no window")

    # talk -------------------------------------------------------------------- #
    def _start_talk(self):
        if self._rec is None:
            self._rec = TalkRecorder()
            self._rec.device = self.stream_device()
        self._talk_target = _frontmost_app_info()
        self._rec.arm()
        self._recording = True
        self._rec_since = time.time()
        self._log(f"talk start target={self._talk_target}")
        _say("recording")

    def _stop_talk(self):
        if not self._recording:
            return
        self._recording = False
        self._silent_for = 0.0
        self._rec.disarm()
        audio = self._rec.drain_audio()
        self._log(
            f"talk stop samples={len(self._rec.samples)} "
            f"rms={round(self._rec.max_rms, 4)}, saw_speech={self._rec.saw_speech}")
        if audio is None:
            _say("no audio")
            return
        if not self._rec.saw_speech and self._rec.max_rms < SPEECH_RMS:
            _say("no speech")
            return
        target = getattr(self, "_talk_target", None)
        threading.Thread(target=self._transcribe_paste,
                         args=(audio, target), daemon=True).start()

    def stream_device(self):
        try:
            from .audio_worker import default_input_device_index
            return default_input_device_index()
        except Exception:
            return None

    def _transcribe_paste(self, audio, target):
        try:
            text = self._stt.transcribe(audio, SAMPLE_RATE) or ""
        except Exception as e:
            self._log(f"transcribe EXC: {e!r}")
            _say("error")
            return
        cleaned = text.replace("<unk>", "").replace("<|unk|>", "").strip()
        self._log(f"transcribe -> {text!r} (cleaned {cleaned!r})")
        if not cleaned:
            _say("no speech")
            return
        _paste_into_focused(cleaned, target)
        _say("done")

    # ---- button edge dispatch ------------------------------------------------ #
    def _on_press(self, code: int):
        self._press_ctrl[code] = self._ctrl_held()
        self._log(f"press 0x{code:02x} ctrl={self._press_ctrl[code]}")

    def _on_release(self, code: int):
        ctrl = self._press_ctrl.pop(code, False)
        self._log(f"release 0x{code:02x}{' TALK' if code == self.ptt_code else ''}")
        if code == self.ptt_code:
            if self._recording:
                self._stop_talk()
            else:
                self._start_talk()
            return
        # window key: Ctrl+tap binds; plain tap focuses
        if ctrl:
            self._bind_current(code)
        else:
            self._focus_bound(code)

    # main --------------------------------------------------------------------- #
    def run(self):
        self._log(f"R1 daemon starting; bindings={len(self._bindings)} ptt=0x{self.ptt_code:02x}")
        _say("ready")
        last_no_dev = 0.0
        while not self._stop.is_set():
            handles = []
            try:
                for path, up, u in self._next_r1_paths():
                    dev = hid.device()
                    try:
                        dev.open_path(path)
                        handles.append((dev, up, u))
                        self._log(f"seized R1 interface up={up} u={u}")
                    except Exception as e:
                        self._log(f"open {up},{u} failed: {e}")
                if not handles:
                    if time.time() - last_no_dev > 30:
                        self._log("R1 not available — retrying...")
                        # no spoken "no device" — it spams every 30s whenever the
                        # R1 is unplugged (Ryan heard "bud ice"; retired 2026-09-14)
                        last_no_dev = time.time()
                    self._stop.wait(5)
                    continue

                while not self._stop.is_set():
                    mask = 0
                    for dev, up, u in handles:
                        try:
                            report = dev.read(64, timeout_ms=15)
                        except Exception as e:
                            self._log(f"read error: {e}")
                            raise
                        if report is None or len(report) == 0:
                            continue
                        mask |= self._buttons_from_report(bytes(report))

                    # talk auto-stop on silence (only once real speech has been
                    # heard, so a slow start isn't cut off) — plus a hard cap.
                    if self._recording and self._rec is not None:
                        elapsed = time.time() - self._rec_since
                        if elapsed >= MAX_RECORD_SECS:
                            self._log("talk max duration reached")
                            self._stop_talk()
                        elif self._rec.saw_speech:
                            if self._rec.is_silent():
                                self._silent_for += 0.015
                                if self._silent_for >= SILENCE_SECS:
                                    self._stop_talk()
                            else:
                                self._silent_for = 0.0

                    if mask == self._prev_buttons:
                        continue
                    changed = self._prev_buttons ^ mask
                    for bit in range(8):
                        if changed & (1 << bit):
                            if mask & (1 << bit):
                                self._on_press(1 << bit)
                            else:
                                self._on_release(1 << bit)
                    self._prev_buttons = mask
            except Exception as e:
                self._log(f"loop error: {e}")
            finally:
                for dev, up, u in handles:
                    try:
                        dev.close()
                    except Exception:
                        pass
                self._prev_buttons = 0
                if self._recording:
                    self._stop_talk()

    def _log(self, msg):
        try:
            with open(LOG_FILE, "a") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
        except Exception:
            pass
        print(msg, flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ptt", type=lambda s: int(s, 0), default=PTT_DEFAULT,
                    help="HID code of the talk (PTT) button, e.g. 0x20 (default)")
    args = ap.parse_args()
    d = R1Daemon(args.ptt)
    try:
        d.run()
    except KeyboardInterrupt:
        d._stop.set()