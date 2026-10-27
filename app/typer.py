import platform
import subprocess
import sys
import time
import shutil
from pathlib import Path

_IS_MACOS = platform.system() == "Darwin"

# delivery.py lives at the repo root; this file is app/typer.py, so the root
# is one level up and isn't on sys.path by default.
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_delivery():
    """Import the shared delivery module, or None if unavailable.

    Returns None rather than raising so a broken import degrades to 'text not
    delivered' instead of killing the HID event loop.
    """
    try:
        if str(_REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(_REPO_ROOT))
        import delivery
        return delivery
    except ImportError as e:
        print(f"[typer] delivery module unavailable ({e}); "
              "text will not be delivered")
        return None

def deliver_text(text: str) -> bool:
    """Paste text into the focused window through the shared delivery module."""
    delivery = _load_delivery()
    if delivery is None:
        return False
    ok, note = delivery.insert(text, "paste")
    if not ok:
        print(f"[typer] delivery failed: {note}")
    return ok


if _IS_MACOS:
    from .typer_macos import (
        insert_text_macos,
        get_open_windows_macos,
        _get_active_window_title_macos as _get_title_macos,
    )

    def get_open_windows():
        return get_open_windows_macos()

    def get_active_window_title():
        return _get_title_macos() or ""

    def insert_text(text: str, target_window_title: str = "") -> bool:
        return insert_text_macos(text, target_window_title)

else:
    _XDOTOOL_BIN = shutil.which("xdotool")

    def get_open_windows():
        if not _XDOTOOL_BIN:
            return []
        try:
            out = subprocess.check_output(
                [_XDOTOOL_BIN, "search", "--name", "."],
                stderr=subprocess.DEVNULL,
            ).decode()
            windows = []
            for wid in out.strip().splitlines():
                wid = wid.strip()
                if not wid:
                    continue
                try:
                    name = subprocess.check_output(
                        [_XDOTOOL_BIN, "getwindowname", wid],
                        stderr=subprocess.DEVNULL,
                    ).decode().strip()
                    if name:
                        windows.append({"id": wid, "title": name})
                except subprocess.CalledProcessError:
                    pass
            return windows
        except (subprocess.CalledProcessError, FileNotFoundError):
            return []

    def get_active_window_title():
        if not _XDOTOOL_BIN:
            return ""
        try:
            wid = subprocess.check_output(
                [_XDOTOOL_BIN, "getactivewindow"], stderr=subprocess.DEVNULL
            ).decode().strip()
            name = subprocess.check_output(
                [_XDOTOOL_BIN, "getwindowname", wid], stderr=subprocess.DEVNULL
            ).decode().strip()
            return name
        except (subprocess.CalledProcessError, FileNotFoundError):
            return ""

    def activate_window(title: str):
        if not _XDOTOOL_BIN:
            return
        try:
            wid = subprocess.check_output(
                [_XDOTOOL_BIN, "search", "--name", title, "--limit", "1"],
                stderr=subprocess.DEVNULL,
            ).decode().strip()
            if wid:
                subprocess.run(
                    [_XDOTOOL_BIN, "windowactivate", wid],
                    stderr=subprocess.DEVNULL,
                )
        except subprocess.CalledProcessError:
            pass

    def insert_text(text: str, target_window_title: str = "") -> bool:
        if not text:
            return False
        if target_window_title:
            activate_window(target_window_title)
            time.sleep(0.3)
        return type_text(text)

    def type_text(text: str):
        """Delegate to delivery.py, which owns text delivery for the repo.

        This used to carry its own copy of the tool chain, with two bugs the
        shared module doesn't have: `ydotool type <text>` and `wtype <text>`
        were called without a `--` separator, so a transcript beginning with
        a dash was parsed as a flag; and there was no clipboard save/restore.
        Window activation stays here — delivery.py deliberately doesn't know
        about windows.
        """
        return deliver_text(text)