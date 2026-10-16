#!/usr/bin/env python3.12
"""Backend selection and audio playback for the voice daemon.

Keeps "which engine" decisions in one place so voice_daemon.py stays about
queueing and HTTP. Every backend is imported lazily and every probe is
defensive: a machine is allowed to have speech-out and no speech-in, or the
reverse, without the daemon refusing to start.

TTS preference
  1. mlx-audio + Kokoro-82M      Apple silicon (what this repo used before)
  2. kokoro-onnx                 any platform, ~same voices, ONNX runtime
  3. piper                       any platform, per-voice .onnx models
  4. macOS `say` / Linux `spd-say`   last resort, always present-ish

STT preference
  1. parakeet-mlx                Apple silicon (English, very fast)
  2. faster-whisper              CUDA/CPU, best cross-platform option
  3. whisper.cpp (`whisper-cli`) plain binary, no Python deps
  4. openai-whisper CLI          slow but everywhere

Playback goes through sounddevice when present, otherwise falls back to
writing a temp WAV and handing it to a system player — headless Debian boxes
often have aplay/paplay but no PortAudio.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
import threading
import wave

IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"
IS_WINDOWS = platform.system() == "Windows"

# The currently-playing system-player child, if any. Guarded because the
# speech worker and an HTTP thread can both touch it (a /skip landing while
# the worker is mid-utterance).
_player = None
_player_lock = threading.Lock()

# Chosen once at import; both are cheap string probes.
_TTS_BACKEND: str | None = None
_STT_BACKEND: str | None = None


def _has(mod: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def log(msg: str) -> None:
    print(f"[speech] {msg}", flush=True)


# --------------------------------------------------------------------------
# playback
# --------------------------------------------------------------------------

def _play_array(audio, rate: int) -> None:
    """Play a float32 numpy array, blocking until finished."""
    try:
        import numpy as np
        import sounddevice as sd
        sd.play(np.asarray(audio, dtype=np.float32), rate, blocking=True)
        return
    except Exception:  # noqa: BLE001 — fall through to a system player
        pass

    # No PortAudio: write a temp WAV and hand it to whatever exists.
    import numpy as np
    arr = np.asarray(audio, dtype=np.float32)
    pcm = (np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())

    _play_wav_blocking(path)


def _play_wav_blocking(path: str) -> None:
    """Play a WAV with a system player, remembering the child so we can stop it.

    `pkill -f <name>` was the previous approach and is wrong twice over: it
    matches any process whose command line merely contains the name (so an
    unrelated `aplay -q something.wav` or a script under a directory called
    `.../aplayer/` gets killed), and on macOS `say` shells out to `afplay`
    internally, so the daemon would kill its own in-flight speech. Tracking
    the real PID has neither problem.
    """
    global _player
    for cmd in (["paplay"], ["aplay", "-q"],
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
                ["afplay"]):
        exe = shutil.which(cmd[0])
        if not exe:
            continue
        try:
            proc = subprocess.Popen([exe] + cmd[1:] + [path],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except OSError:
            continue
        with _player_lock:
            _player = proc
        try:
            proc.wait(timeout=300)
        except subprocess.TimeoutExpired:
            proc.kill()
        finally:
            with _player_lock:
                if _player is proc:
                    _player = None
        return
    log(f"no audio player found; wrote {path}")


def stop_playback() -> None:
    """Stop whatever is currently playing. Never raises.

    Only ever signals the child process this module started — never a
    name-matched global kill.
    """
    try:
        import sounddevice as sd
        sd.stop()
    except Exception:  # noqa: BLE001
        pass

    global _player
    with _player_lock:
        proc, _player = _player, None
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        except OSError:
            pass


# --------------------------------------------------------------------------
# TTS
# --------------------------------------------------------------------------

class _MlxTTS:
    name = "mlx-audio (Kokoro-82M)"
    sample_rate = 24_000

    def __init__(self):
        from mlx_audio.tts.utils import load_model
        self.model = load_model("mlx-community/Kokoro-82M-bf16")

    def speak(self, text, voice, speed, should_stop):
        # Kokoro yields segments; stream them so long text starts fast and a
        # skip can land between segments.
        for seg in self.model.generate(text=text, voice=voice, speed=speed):
            if should_stop():
                return
            _play_array(seg.audio, self.sample_rate)


class _KokoroOnnxTTS:
    name = "kokoro-onnx"
    sample_rate = 24_000

    def __init__(self):
        from kokoro_onnx import Kokoro
        model = os.environ.get("KOKORO_ONNX_MODEL", "kokoro-v1.0.onnx")
        voices = os.environ.get("KOKORO_ONNX_VOICES", "voices-v1.0.bin")
        self.kokoro = Kokoro(model, voices)

    def speak(self, text, voice, speed, should_stop):
        samples, rate = self.kokoro.create(
            text, voice=voice or "af_heart", speed=speed, lang="en-us")
        self.sample_rate = rate
        if not should_stop():
            _play_array(samples, rate)


class _PiperTTS:
    name = "piper"
    sample_rate = 22_050

    def __init__(self):
        exe = shutil.which("piper")
        if not exe:
            raise RuntimeError("piper not on PATH")
        self.exe = exe
        self.model = os.environ.get("PIPER_MODEL")
        if not self.model:
            raise RuntimeError("set PIPER_MODEL to a .onnx voice file")

    def speak(self, text, voice, speed, should_stop):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            out = f.name
        cmd = [self.exe, "--model", self.model, "--output_file", out]
        if speed:
            cmd += ["--length_scale", f"{1.0 / float(speed):.3f}"]
        try:
            subprocess.run(cmd, input=text.encode(), check=True, timeout=300)
            if not should_stop():
                _play_wav_blocking(out)
        finally:
            try:
                os.unlink(out)
            except OSError:
                pass


class _SayTTS:
    """The OS's own voice. Ugly, but present on every Mac and most desktops."""
    sample_rate = 22_050

    def __init__(self):
        if IS_MACOS:
            self.cmd = ["say"]
        elif IS_WINDOWS:
            # Windows always has SAPI, so this never needs a fallback tool.
            # Spoken through PowerShell's System.Speech rather than a console
            # binary, since there is no `say` equivalent on PATH.
            exe = shutil.which("powershell") or shutil.which("powershell.exe")
            if not exe:
                raise RuntimeError("no PowerShell for System.Speech")
            self.cmd = [exe]
        else:
            exe = shutil.which("spd-say") or shutil.which("espeak-ng") \
                or shutil.which("espeak")
            if not exe:
                raise RuntimeError("no system TTS (say/spd-say/espeak)")
            self.cmd = [exe]

    @property
    def name(self):
        if IS_WINDOWS:
            return "system (Windows SAPI)"
        return f"system ({os.path.basename(self.cmd[0])})"

    def speak(self, text, voice, speed, should_stop):
        if IS_WINDOWS:
            # Escape single quotes for the PowerShell string literal.
            safe = text.replace("'", "''")
            rate = 0
            if speed:
                # SAPI rate is -10..10, roughly 1.0 == 0.
                rate = max(-10, min(10, int((float(speed) - 1.0) * 10)))
            ps = ("Add-Type -AssemblyName System.Speech; "
                  "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  f"$s.Rate = {rate}; "
                  f"$s.Speak('{safe}')")
            try:
                subprocess.run(self.cmd + ["-NoProfile", "-Command", ps],
                               check=True, timeout=300)
            except (OSError, subprocess.SubprocessError) as e:
                log(f"system TTS failed: {e}")
            return

        cmd = list(self.cmd)
        if speed and self.cmd[0] == "say":
            cmd += ["-r", str(int(200 * float(speed)))]
        try:
            subprocess.run(cmd + [text], check=True, timeout=300)
        except (OSError, subprocess.SubprocessError) as e:
            log(f"system TTS failed: {e}")


def load_tts():
    """Build the best available TTS backend. Raises if none work."""
    global _TTS_BACKEND
    errors = []

    if IS_MACOS and _has("mlx_audio"):
        try:
            be = _MlxTTS()
            _TTS_BACKEND = be.name
            return be
        except Exception as e:  # noqa: BLE001
            errors.append(f"mlx-audio: {e}")

    if _has("kokoro_onnx"):
        try:
            be = _KokoroOnnxTTS()
            _TTS_BACKEND = be.name
            return be
        except Exception as e:  # noqa: BLE001
            errors.append(f"kokoro-onnx: {e}")

    if shutil.which("piper") and os.environ.get("PIPER_MODEL"):
        try:
            be = _PiperTTS()
            _TTS_BACKEND = be.name
            return be
        except Exception as e:  # noqa: BLE001
            errors.append(f"piper: {e}")

    try:
        be = _SayTTS()
        _TTS_BACKEND = be.name
        return be
    except Exception as e:  # noqa: BLE001
        errors.append(f"system: {e}")

    if errors:
        detail = "; ".join(errors)
    else:
        detail = ("nothing installed — try `pip install kokoro-onnx` "
                  "(cross-platform), or install espeak-ng/spd-say")
    raise RuntimeError("no TTS backend available: " + detail)


def tts_backend_name() -> str:
    if _TTS_BACKEND:
        return _TTS_BACKEND
    if IS_MACOS and _has("mlx_audio"):
        return "mlx-audio (Kokoro-82M)"
    if _has("kokoro_onnx"):
        return "kokoro-onnx"
    if shutil.which("piper"):
        return "piper"
    return "system"


# --------------------------------------------------------------------------
# STT
# --------------------------------------------------------------------------

class _ParakeetMlx:
    name = "parakeet-mlx (parakeet-tdt-0.6b-v2)"

    def __init__(self):
        from parakeet_mlx import from_pretrained
        self.model = from_pretrained("mlx-community/parakeet-tdt-0.6b-v2")

    def transcribe(self, path: str) -> str:
        return self.model.transcribe(path).text


class _FasterWhisper:
    def __init__(self):
        from faster_whisper import WhisperModel
        size = os.environ.get("VOICE_WHISPER_SIZE", "base.en")
        compute = os.environ.get("VOICE_WHISPER_COMPUTE", "default")
        requested = os.environ.get("VOICE_WHISPER_DEVICE", "auto")

        # device="auto" reports CUDA available whenever a GPU is visible, but
        # on a box without the CUDA runtime the model loads and then dies at
        # the first encode with "Library libcublas.so.12 is not found". That
        # hit WSL (which exposes the GPU but ships no CUDA libs) and would hit
        # any Debian machine with an NVIDIA card and no toolkit. So try CUDA,
        # and fall back to CPU on any load failure rather than crashing.
        attempts = [requested] if requested != "auto" else ["cuda", "cpu"]
        last: Exception | None = None
        for dev in attempts:
            try:
                self.model = WhisperModel(size, device=dev,
                                          compute_type=compute)
                self.device = dev
                break
            except Exception as e:  # noqa: BLE001 — fall through to CPU
                last = e
                log(f"faster-whisper on {dev} unavailable: {e}")
        else:
            raise RuntimeError(f"faster-whisper could not load: {last}")

        self.name = f"faster-whisper ({size}, {self.device})"

    def transcribe(self, path: str) -> str:
        try:
            segments, _info = self.model.transcribe(path, beam_size=1)
            return " ".join(s.text.strip() for s in segments).strip()
        except RuntimeError as e:
            # The encoder is where a missing CUDA library actually surfaces,
            # since loading only checks that a device is visible. Retry once
            # on CPU so a half-configured GPU box still transcribes.
            if self.device == "cpu" or "libcublas" not in str(e).lower():
                raise
            log(f"CUDA failed at encode time ({e}); retrying on CPU")
            from faster_whisper import WhisperModel
            size = os.environ.get("VOICE_WHISPER_SIZE", "base.en")
            self.model = WhisperModel(size, device="cpu", compute_type="default")
            self.device = "cpu"
            self.name = f"faster-whisper ({size}, cpu)"
            # /health reports stt_backend_name(), which is cached at module
            # level; update it too or the daemon keeps advertising "cuda".
            global _STT_BACKEND
            _STT_BACKEND = self.name
            segments, _info = self.model.transcribe(path, beam_size=1)
            return " ".join(s.text.strip() for s in segments).strip()


class _WhisperCpp:
    def __init__(self):
        exe = (shutil.which("whisper-cli") or shutil.which("whisper-cpp")
               or shutil.which("main"))
        if not exe:
            raise RuntimeError("whisper.cpp binary not found")
        self.exe = exe
        self.model = os.environ.get("WHISPER_CPP_MODEL")
        if not self.model:
            raise RuntimeError("set WHISPER_CPP_MODEL to a ggml .bin model")
        self.name = f"whisper.cpp ({os.path.basename(self.model)})"

    def transcribe(self, path: str) -> str:
        r = subprocess.run(
            [self.exe, "-m", self.model, "-f", path, "-nt", "-np"],
            capture_output=True, text=True, timeout=600,
        )
        return r.stdout.strip()


class _WhisperCli:
    def __init__(self):
        exe = shutil.which("whisper")
        if not exe:
            raise RuntimeError("openai-whisper CLI not found")
        self.exe = exe
        self.size = os.environ.get("VOICE_WHISPER_SIZE", "base")
        self.name = f"openai-whisper ({self.size})"

    def transcribe(self, path: str) -> str:
        import json as _json
        r = subprocess.run(
            [self.exe, path, "--model", self.size,
             "--output_format", "json", "--output_dir", tempfile.gettempdir()],
            capture_output=True, text=True, timeout=900,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[:200])
        out = os.path.join(
            tempfile.gettempdir(),
            os.path.splitext(os.path.basename(path))[0] + ".json")
        with open(out) as f:
            return _json.load(f).get("text", "").strip()


def load_stt():
    """Build the best available STT backend. Raises if none work."""
    global _STT_BACKEND
    errors = []

    if IS_MACOS and _has("parakeet_mlx"):
        try:
            be = _ParakeetMlx()
            _STT_BACKEND = be.name
            return be
        except Exception as e:  # noqa: BLE001
            errors.append(f"parakeet-mlx: {e}")

    if _has("faster_whisper"):
        try:
            be = _FasterWhisper()
            _STT_BACKEND = be.name
            return be
        except Exception as e:  # noqa: BLE001
            errors.append(f"faster-whisper: {e}")

    if shutil.which("whisper-cli") or shutil.which("whisper-cpp"):
        try:
            be = _WhisperCpp()
            _STT_BACKEND = be.name
            return be
        except Exception as e:  # noqa: BLE001
            errors.append(f"whisper.cpp: {e}")

    if shutil.which("whisper"):
        try:
            be = _WhisperCli()
            _STT_BACKEND = be.name
            return be
        except Exception as e:  # noqa: BLE001
            errors.append(f"whisper: {e}")

    if errors:
        detail = "; ".join(errors)
    else:
        detail = ("nothing installed — try `pip install faster-whisper` "
                  "(cross-platform) or set WHISPER_CPP_MODEL for whisper.cpp")
    raise RuntimeError("no STT backend available: " + detail)


def stt_backend_name() -> str:
    if _STT_BACKEND:
        return _STT_BACKEND
    if IS_MACOS and _has("parakeet_mlx"):
        return "parakeet-mlx"
    if _has("faster_whisper"):
        return "faster-whisper"
    if shutil.which("whisper-cli"):
        return "whisper.cpp"
    if shutil.which("whisper"):
        return "openai-whisper"
    return "none"


if __name__ == "__main__":
    print(f"platform : {platform.system()}")
    print(f"tts      : {tts_backend_name()}")
    print(f"stt      : {stt_backend_name()}")
