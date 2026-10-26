import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional


CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "finger-mouse-stt")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


@dataclass
class ButtonMapping:
    device_id: str = ""
    button_code: int = 0
    button_label: str = ""
    target_window_title: str = ""
    stt_engine: str = "remote_server"
    stt_server_url: str = ""
    push_to_talk: bool = True


@dataclass
class AppConfig:
    selected_device: str = ""
    mappings: list = field(default_factory=list)
    default_stt_engine: str = "remote_server"
    # The unified voice daemon (voice_daemon.py) serves /transcribe on 8740,
    # the same process that serves /say. It used to be a separate stt_server
    # on 8741 holding a second copy of the model.
    default_stt_server_url: str = "http://127.0.0.1:8740"
    audio_device: Optional[str] = None
    audio_sample_rate: int = 16000

    @classmethod
    def load(cls):
        if not os.path.exists(CONFIG_FILE):
            return cls()
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            return cls(**data)
        except (json.JSONDecodeError, KeyError, TypeError):
            return cls()

    def save(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(asdict(self), f, indent=2)

    def get_mapping(self, device_id: str, button_code: int) -> Optional[ButtonMapping]:
        for m in self.mappings:
            if m["device_id"] == device_id and m["button_code"] == button_code:
                return ButtonMapping(**m)
        return None

    def set_mapping(self, mapping: dict):
        key = (mapping["device_id"], mapping["button_code"])
        for i, m in enumerate(self.mappings):
            if m["device_id"] == key[0] and m["button_code"] == key[1]:
                self.mappings[i] = mapping
                return
        self.mappings.append(mapping)
