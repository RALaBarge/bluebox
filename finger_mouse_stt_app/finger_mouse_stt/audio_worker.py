import numpy as np
import queue
import sounddevice as sd
import threading
import time
from typing import Optional, Callable


class AudioRecorder(threading.Thread):
    def __init__(self, on_audio: Callable[[np.ndarray], None],
                 sample_rate: int = 16000, device: Optional[str] = None):
        super().__init__(daemon=True)
        self.sample_rate = sample_rate
        self.device = device
        self.on_audio = on_audio
        self._recording = threading.Event()
        self._stop = threading.Event()
        self._audio_queue: queue.Queue = queue.Queue()

    def start_recording(self):
        self._recording.set()

    def stop_recording(self):
        self._recording.clear()

    def is_recording(self) -> bool:
        return self._recording.is_set()

    def run(self):
        def callback(indata, frames, time_info, status):
            if self._recording.is_set() and not self._stop.is_set():
                self._audio_queue.put(indata.copy())

        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                device=self.device,
                channels=1,
                dtype="float32",
                callback=callback,
            ):
                while not self._stop.is_set():
                    time.sleep(0.05)
                    if not self._recording.is_set():
                        while not self._audio_queue.empty():
                            self._audio_queue.get_nowait()

        except Exception as e:
            print(f"Audio error: {e}")

    def get_recorded_audio(self) -> Optional[np.ndarray]:
        chunks = []
        while not self._audio_queue.empty():
            chunks.append(self._audio_queue.get_nowait())
        if not chunks and not self._recording.is_set():
            return None
        if chunks:
            return np.concatenate(chunks, axis=0).flatten()
        return None

    def stop(self):
        self._stop.set()


def default_input_device_index() -> Optional[int]:
    try:
        idx = sd.default.device[0]
        return idx if idx is not None and idx >= 0 else None
    except Exception:
        return None


def list_audio_devices() -> list[dict]:
    try:
        devices = sd.query_devices()
        return [
            {"index": i, "name": d["name"], "channels": d["max_input_channels"]}
            for i, d in enumerate(devices)
            if d["max_input_channels"] > 0
        ]
    except Exception:
        return []