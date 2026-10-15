#!/usr/bin/env python3.12
"""Local STT server — speaks the protocol finger_mouse_stt expects.

The finger-mouse app's remote_server engine POSTs raw WAV bytes to
{url}/transcribe (Content-Type: audio/wav) and reads {"text": ...}.
That placeholder pointed at 192.168.1.100:5000 on another machine; this
serves the same contract on localhost using the same parakeet model the
voice stack already uses (dictate.py).

    POST /transcribe   body = raw WAV  -> {"text": "..."}
    GET  /health                        -> {"ok": true, "model_loaded": bool}

Run:  python3.12 tools/voice/finger_mouse_stt_app/stt_server.py  (port 8741)
Launchd: com.ai-stack.stt-server
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# macOS: parakeet shells out to the ffmpeg binary in /opt/homebrew/bin, but
# launchd runs this service with a minimal PATH that does NOT include it, so
# the model's ffmpeg lookup fails (HTTP 500). Prepend it so ffmpeg is found
# regardless of how the service is launched.
os.environ["PATH"] = (
    "/opt/homebrew/bin:" + os.environ.get("PATH", "")
)

PORT = 8741
SAMPLE_RATE = 16_000
MAX_BYTES = 32 * 1024 * 1024  # 32 MB audio cap

_model = None
_model_lock = threading.Lock()


def _load_model():
    global _model
    with _model_lock:
        if _model is None:
            from parakeet_mlx import from_pretrained
            print("[stt] loading parakeet-tdt-0.6b-v2 ...", flush=True)
            _model = from_pretrained("mlx-community/parakeet-tdt-0.6b-v2")
            print("[stt] model ready", flush=True)
    return _model


def _transcribe(wav_bytes: bytes) -> str:
    """Write WAV to temp, run parakeet, return transcribed text."""
    model = _load_model()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
        f.write(wav_bytes)
        f.flush()
        result = model.transcribe(f.name)
    return (result.text or "").strip()


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
        else:
            self._json(404, {"ok": False, "error": "unknown path"})

    def do_POST(self):  # noqa: N802
        if not self.path.rstrip("/").endswith("/transcribe"):
            self._json(404, {"ok": False, "error": "unknown path"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > MAX_BYTES:
                self._json(400, {"ok": False, "error": f"bad length {length}"})
                return
            data = self.rfile.read(length)
        except (ValueError, OSError) as e:
            self._json(400, {"ok": False, "error": f"read failed: {e}"})
            return
        try:
            text = _transcribe(data)
        except Exception as e:  # noqa: BLE001 — server must not die on bad audio
            self._json(500, {"ok": False, "error": str(e)})
            return
        self._json(200, {"text": text})

    def log_message(self, fmt, *args):  # quiet
        pass


def main() -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[stt] listening on http://127.0.0.1:{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()