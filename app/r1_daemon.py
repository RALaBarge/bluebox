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

from .config import append_log
from .stt import STTEngine
from .typer import deliver_text

R1_VID = 0x004C
R1_PID = 0x014C
PTT_DEFAULT = 0x20   # central analogue / talk button (momentary)
STT_URL = "http://127.0.0.1:8740"   # unified daemon: /transcribe
SAMPLE_RATE = 16000
SILENCE_RMS = 0.012
SILENCE_SECS = 1.2
MIN_RECORD_SECS = 0.8
SPEECH_RMS = 0.008      # window RMS above this counts as real speech
MAX_RECORD_SECS = 45    # never record forever if nothing is said

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "finger-mouse-stt")
BIND_FILE = os.path.join(CONFIG_DIR, "r1_bindings.json")

# name -> [(frequency Hz, seconds), ...]. Anything not listed is silent.
_CUES = {
    "start": [(880, 0.12)],
    "bound": [(660, 0.08)],
    "error": [(220, 0.15), (220, 0.15)],
}


# --------------------------------------------------------------------------- #
# Fire-and-forget helpers (never block the HID loop).                        #
# --------------------------------------------------------------------------- #
def _cue(kind: str):
    """Non-speech audio feedback (non-blocking).

    Spoken cues were retired: the mic picked "recording" up and it was
    transcribed into the user's window. A tone is not a word.
    """
    tones = _CUES.get(kind)
    if not tones:
        return

    def go():
        try:
            import numpy as np
            import sounddevice as sd
            sr = 20000
            for freq, dur in tones:
                t = np.linspace(0, dur, int(sr * dur), endpoint=False)
                sd.play((np.sin(2 * np.pi * freq * t) * 0.4).astype(np.float32), sr)
                sd.wait()
                time.sleep(0.03)
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


def _paste_into_focused(text: str, target: Optional[dict] = None) -> bool:
    """Bring the target window forward, then paste through delivery.py.

    target: dict {pid, name} to activate first; if None, pastes into whatever
    is frontmost. Returns False if the text could not be delivered.
    """
    if target and target.get("pid"):
        _activate_by_pid(target["pid"])
    elif target and target.get("name"):
        _activate_by_name(target["name"])
    if target:
        time.sleep(0.25)  # let activation settle before pasting
    return deliver_text(text)


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
        self._quiet_since = None   # when the current stretch of quiet began
        self._last_level = 0.0     # when the input level was last measured

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
            _cue("error")
            return
        self._bindings[code] = info
        self._save_bindings()
        _cue("bound")

    def _focus_bound(self, code: int):
        b = self._bindings.get(code)
        if not b:
            _cue("error")
            return
        ok = False
        if b.get("pid"):
            ok = _activate_by_pid(b["pid"])
        if not ok and b.get("name"):
            ok = _activate_by_name(b["name"])
        if not ok:
            _cue("error")

    # talk -------------------------------------------------------------------- #
    def _start_talk(self):
        if self._rec is None:
            self._rec = TalkRecorder()
            self._rec.device = self.stream_device()
        self._talk_target = _frontmost_app_info()
        self._rec.arm()
        self._recording = True
        self._rec_since = time.time()
        self._quiet_since = None
        self._last_level = 0.0
        self._log(f"talk start target={self._talk_target}")
        _cue("start")

    def _check_auto_stop(self, now: float):
        """End the recording on the time cap, or on quiet after real speech.

        recent_rms() is what notices speech (it sets saw_speech), so it has to
        run on every check; gating it behind saw_speech meant speech was never
        noticed and a silence stop never fired. It concatenates recent audio,
        so it is measured every 100 ms rather than on every 15 ms HID poll.
        """
        if not self._recording or self._rec is None:
            return
        if now - self._rec_since >= MAX_RECORD_SECS:
            self._log("talk max duration reached")
            self._stop_talk()
            return
        if now - self._last_level < 0.1:
            return
        self._last_level = now
        quiet = self._rec.is_silent()
        if not self._rec.saw_speech or not quiet:
            self._quiet_since = None
        elif self._quiet_since is None:
            self._quiet_since = now
        elif now - self._quiet_since >= SILENCE_SECS:
            self._stop_talk()

    def _stop_talk(self):
        if not self._recording:
            return
        self._recording = False
        self._quiet_since = None
        self._rec.disarm()
        audio = self._rec.drain_audio()
        self._log(
            f"talk stop samples={len(self._rec.samples)} "
            f"rms={round(self._rec.max_rms, 4)}, saw_speech={self._rec.saw_speech}")
        if audio is None:
            _cue("error")
            return
        if not self._rec.saw_speech and self._rec.max_rms < SPEECH_RMS:
            _cue("error")
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
            _cue("error")
            return
        cleaned = text.replace("<unk>", "").replace("<|unk|>", "").strip()
        self._log(f"transcribe -> {len(cleaned)} chars")
        if not cleaned:
            _cue("error")
            return
        if not _paste_into_focused(cleaned, target):
            # The only record of what was said; keep it so it is not lost.
            self._log(f"delivery failed; transcript: {cleaned!r}")
            _cue("error")

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
                    self._check_auto_stop(time.time())

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
        append_log("r1_daemon.log", f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}")
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