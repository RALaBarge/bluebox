import platform
import threading
from typing import Optional, Callable

ButtonEvent = tuple[int, bool]

_IS_MACOS = platform.system() == "Darwin"

if _IS_MACOS:
    from .device_macos import list_devices_macos, DeviceMonitorMacOS

    def list_devices():
        return list_devices_macos()

    class DeviceMonitor(threading.Thread):
        def __init__(self, device_path: str, on_button: Callable[[ButtonEvent], None],
                     usage_page: int = 1, usage: int = 2):
            super().__init__(daemon=True)
            self._impl = DeviceMonitorMacOS(device_path, on_button, usage_page, usage)

        def stop(self):
            self._impl.stop()

        def run(self):
            self._impl.run()
else:
    try:
        import evdev
        from evdev import InputDevice, ecodes
        HAVE_EVDEV = True
    except ImportError:
        HAVE_EVDEV = False

    def list_devices():
        if not HAVE_EVDEV:
            return []
        devices = []
        for path in evdev.list_devices():
            try:
                d = InputDevice(path)
                devices.append({
                    "path": path,
                    "name": d.name or "Unknown",
                    "phys": d.phys or "",
                    "id": f"{d.info.vendor:04x}:{d.info.product:04x}",
                })
                d.close()
            except (OSError, PermissionError):
                pass
        return devices

    class DeviceMonitor(threading.Thread):
        def __init__(self, device_path: str, on_button: Callable[[ButtonEvent], None]):
            super().__init__(daemon=True)
            self.device_path = device_path
            self.on_button = on_button
            self._stop = threading.Event()

        def stop(self):
            self._stop.set()

        def run(self):
            if not HAVE_EVDEV:
                return
            try:
                dev = InputDevice(self.device_path)
                dev.grab()
            except (OSError, PermissionError) as e:
                self.on_button(("error", str(e)))
                return

            for event in dev.read_loop():
                if self._stop.is_set():
                    break
                if event.type == ecodes.EV_KEY:
                    is_down = event.value in (1, 2)
                    self.on_button((event.code, is_down))

            try:
                dev.ungrab()
                dev.close()
            except OSError:
                pass