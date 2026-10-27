import io
import json
import os
import subprocess
import tempfile
import urllib.request
import urllib.error
from typing import Optional


class STTEngine:
    def __init__(self, engine_type: str = "remote_server", server_url: str = ""):
        self.engine_type = engine_type
        self.server_url = server_url

    def transcribe(self, audio_data: bytes, sample_rate: int = 16000) -> Optional[str]:
        if self.engine_type == "remote_server":
            return self._transcribe_remote(audio_data, sample_rate)
        elif self.engine_type == "whisper_local":
            return self._transcribe_whisper_local(audio_data, sample_rate)
        elif self.engine_type == "vosk":
            return self._transcribe_vosk(audio_data, sample_rate)
        return None

    def _transcribe_remote(self, audio_data: bytes, sample_rate: int) -> Optional[str]:
        """POST WAV bytes to the voice daemon's /transcribe.

        Returns None on failure rather than an error string: callers must not
        type "[STT error: ...]" into the user's focused window, which is what
        the previous version did.
        """
        if not self.server_url:
            return None
        try:
            import soundfile as sf
        except ImportError:
            return None
        try:
            buf = io.BytesIO()
            try:
                sf.write(buf, audio_data, sample_rate, format="WAV")
            except Exception:
                return None
            audio_bytes = buf.getvalue()

            req = urllib.request.Request(
                self.server_url.rstrip("/") + "/transcribe",
                data=audio_bytes,
                headers={"Content-Type": "audio/wav"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
                text = result.get("text")
                return text.strip() if text else None
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError,
                OSError, subprocess.CalledProcessError) as e:
            print(f"[stt] remote transcription failed: {e}")
            return None

    def _transcribe_whisper_local(self, audio_data: bytes, sample_rate: int) -> Optional[str]:
        try:
            import soundfile as sf
        except ImportError:
            print("[stt] local whisper needs soundfile")
            return None

        # The whisper CLI writes <name>.json into --output_dir; it prints no JSON.
        with tempfile.TemporaryDirectory() as d:
            wav = os.path.join(d, "audio.wav")
            sf.write(wav, audio_data, sample_rate)
            try:
                result = subprocess.run(
                    ["whisper", wav, "--model", "base",
                     "--output_format", "json", "--output_dir", d],
                    capture_output=True, text=True, timeout=120,
                )
            except FileNotFoundError:
                print("[stt] whisper CLI not found — install openai-whisper")
                return None
            except subprocess.TimeoutExpired:
                print("[stt] whisper timed out")
                return None
            if result.returncode != 0:
                print(f"[stt] whisper failed: {result.stderr.strip()[:200]}")
                return None
            try:
                with open(os.path.join(d, "audio.json")) as f:
                    text = json.load(f).get("text", "").strip()
            except (OSError, ValueError):
                print("[stt] whisper wrote no readable result")
                return None
            return text or None

    def _transcribe_vosk(self, audio_data: bytes, sample_rate: int) -> Optional[str]:
        print("[stt] vosk engine not implemented")
        return None

    def is_available(self) -> bool:
        if self.engine_type == "remote_server" and self.server_url:
            return True
        if self.engine_type == "whisper_local":
            try:
                subprocess.run(["whisper", "--help"], capture_output=True, timeout=5)
                return True
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return False
        return False