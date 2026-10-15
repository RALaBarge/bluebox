import platform
import subprocess
import time
import shutil

_IS_MACOS = platform.system() == "Darwin"

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
    _TOOLS = ["ydotool", "xdotool", "wtype", "xclip"]

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
        for tool in _TOOLS:
            path = shutil.which(tool)
            if not path:
                continue

            try:
                if tool == "ydotool":
                    subprocess.run(
                        [path, "type", text],
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                elif tool == "xdotool":
                    subprocess.run(
                        [path, "type", "--clearmodifiers", text],
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                elif tool == "wtype":
                    subprocess.run(
                        [path, text],
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                elif tool == "xclip":
                    p = subprocess.Popen(
                        [path, "-selection", "clipboard"],
                        stdin=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                    )
                    p.communicate(input=text.encode(), timeout=5)
                    subprocess.run(
                        [shutil.which("xdotool") or "true",
                         "key", "--clearmodifiers",
                         "ctrl+v"],
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                return
            except (subprocess.CalledProcessError, FileNotFoundError, OSError):
                continue