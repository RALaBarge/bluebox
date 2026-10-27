import json
import os
from dataclasses import dataclass, field, fields, asdict
from typing import Optional


CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "finger-mouse-stt")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
LOG_DIR = os.path.join(os.path.expanduser("~"), ".cache", "bluebox")


def append_log(name: str, line: str) -> None:
    """Append a line to a per-user log. Never raises.

    The daemons used to log to /tmp, which other users can read, and the logs
    can name windows and hold dictated text. This directory and file are
    private to the user.
    """
    try:
        os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)
        fd = os.open(os.path.join(LOG_DIR, name),
                     os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


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
            known = {f.name for f in fields(cls)}
            return cls(**{k: v for k, v in data.items() if k in known})
        except OSError:
            return cls()
        except (json.JSONDecodeError, TypeError, AttributeError):
            # Keep the unreadable file: the next save() would overwrite it.
            try:
                os.replace(CONFIG_FILE, CONFIG_FILE + ".bak")
            except OSError:
                pass
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
