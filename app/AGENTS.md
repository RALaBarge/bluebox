# Lessons Learned

## Cross-Platform HID / Input

- **Linux**: evdev is the natural choice — `InputDevice.grab()` + `read_loop()` for exclusive access, `ecodes.EV_KEY` for button events. Device enumeration via `evdev.list_devices()`.
- **macOS**: evdev doesn't exist. Use `hidapi` (`pip install hidapi`). Devices are enumerated via `hid.enumerate()`, filtered by `vendor_id`/`product_id`/`usage_page`. Reading uses `dev.read(64)` which returns raw bytes. HID mouse button state is in byte 0 (bitmask: bit 0 = left, 1 = right, 2 = middle, 3+ = side buttons). Need to diff with previous state to detect press/release edges.
- **Key gotcha**: hidapi on macOS needs "Input Monitoring" permission in System Settings → Privacy & Security. Without it, `hid.enumerate()` returns nothing or reads fail silently.
- **Platform dispatch pattern**: Do `platform.system()` at import time in the shared module, conditionally import platform-specific backends, and define a uniform API. Works cleanly since the two platforms never share a runtime.

## Cross-Platform Text Insertion

- **Linux**: `ydotool type` (Wayland), `xdotool type --clearmodifiers` (X11), `wtype` (wlroots Wayland), `xclip` + `xdotool key ctrl+v` (last resort). All are optional; chain through them in preference order.
- **macOS**: `osascript` with `keystroke` works but needs "Accessibility" permission. PyAutoGUI as fallback. osascript's `keystroke` doesn't handle multiline text well; newlines need `\n` embedded (which does work).
- **Window activation**: Linux uses `xdotool search --name <title> --limit 1` + `xdotool windowactivate <wid>`. macOS uses osascript `tell application <name> to activate` but matching by title substring is fragile — processes can have different names than windows.
- **Click-to-map**: Hide app (`self.hide()`), poll active window title every 500ms, require 3 consecutive stable readings (1.5s) to confirm. Works on both platforms with platform-specific backends.

## GUI / PyQt6

- **System tray**: `QSystemTrayIcon` works but warns "No Icon set" if no icon provided. Need at least a 16x16 or 22x22 PNG. Set via `tray_icon.setIcon(QIcon("icon.png"))`.
- **Window mapping widgets**: Use `QListWidget` with custom `QWidget` items and `QListWidgetItem.setSizeHint()`. Each row has combo boxes, line edits, and buttons. Save state `QComboBox.currentData()` for button codes, `QLineEdit.text()` for window titles/URLs.
- **Deferred save**: Use a 300ms single-shot `QTimer` to batch writes to config JSON on any mapping change, avoiding write amplification during typing.

## Audio / STT

- **sounddevice**: `sounddevice.query_devices()` returns dict-like objects with `name` and `index`. Works identically on Linux and macOS.
- **STT engines**: HTTP POST (remote server) is simplest cross-platform. Whisper CLI needs `whisper` package and ~1GB VRAM. Vosk needs model download. Make the engine pluggable with a factory + `transcribe(audio_data, sample_rate)` method.

## Project Structure

- Single flat package `app/` — no inner package directory — with platform-dispatch at import time in `device.py` and `typer.py`.
- macOS backends in `_macos.py` files that are only imported on Darwin — avoids `hidapi`/`evdev` ImportError.
- The `__init__.py` doesn't need to re-export; callers import directly from the submodules.
- `main.py` is the GUI entry point, run as `python3 -m app.main`; it just creates `QApplication` and shows `MainWindow`.
