import platform
import threading
import time
from typing import Optional, Callable

ButtonEvent = tuple[int, bool]

_IS_MACOS = platform.system() == "Darwin"

try:
    import hid
    HAVE_HIDAPI = True
except ImportError:
    HAVE_HIDAPI = False


def _is_internal_apple(d: dict) -> bool:
    """Never expose Apple-internal devices (trackpad/keyboard/backlight).
    Grabbing one seizes it from the system (hit live 2026-08-20 — internal
    trackpad stopped clicking while the app held its HID interface).
    R1 (0x004c) and other BT/HID peripherals are never filtered.
    """
    name = (d.get("product_string") or "") + " " + (d.get("manufacturer_string") or "")
    if "Apple Internal" in name:
        return True
    if d.get("vendor_id") == 0x05AC:  # Apple Inc. product (external Apple devices too — never grab)
        return True
    if d.get("vendor_id") == 0 and d.get("product_id") == 0 and d.get("bus_type", 0) == 0:
        # unnamed USB system device without a bus — safest to skip
        return True
    return False


def list_devices_macos() -> list[dict]:
    if not HAVE_HIDAPI:
        return []
    devices = []
    try:
        for d in hid.enumerate():
            usage = (d.get("usage_page", 0), d.get("usage", 0))
            is_mouse = usage == (1, 2)
            is_keyboard = usage == (1, 6)
            is_consumer = usage == (12, 1)     # volume/media keys — R1 uses this
            is_joystick = usage == (1, 4)
            is_gamepad = usage == (1, 5)
            if not (is_mouse or is_keyboard or is_consumer or is_joystick or is_gamepad):
                continue
            if _is_internal_apple(d):
                continue
            path = d["path"]
            if isinstance(path, bytes):
                path = path.decode("utf-8", errors="replace")
            devices.append({
                "path": path,
                "name": d.get("product_string", "Unknown HID Device"),
                "phys": d.get("manufacturer_string", ""),
                "id": f"{d['vendor_id']:04x}:{d['product_id']:04x}",
                "usage_page": d.get("usage_page", 0),
                "usage": d.get("usage", 0),
            })
    except Exception:
        pass
    return devices


class DeviceMonitorMacOS(threading.Thread):
    def __init__(self, device_path: str, on_button: Callable[[ButtonEvent], None],
                 usage_page: int = 1, usage: int = 2):
        super().__init__(daemon=True)
        self.device_path = device_path
        self.on_button = on_button
        self._stop = threading.Event()
        self._prev_buttons = 0
        self._prev_keys: set[int] = set()
        self._usage_page = usage_page
        self._usage = usage

    def stop(self):
        self._stop.set()

    def _emit_edges(self, prev: set[int], cur: set[int]):
        """Emit (code, is_down) for every code that changed state."""
        for code in cur - prev:
            self.on_button((code, True))
        for code in prev - cur:
            self.on_button((code, False))

    def run(self):
        if not HAVE_HIDAPI:
            return
        try:
            dev = hid.device()
            path = self.device_path
            if isinstance(path, str):
                path = path.encode("utf-8")
            dev.open_path(path)
            dev.set_nonblocking(False)
        except Exception as e:
            self.on_button(("error", str(e)))
            return

        try:
            while not self._stop.is_set():
                report = dev.read(64, timeout_ms=500)
                if report is None or len(report) == 0:
                    continue

                # --- pointer interface (usage 1,2): byte0 bitmask of buttons ---
                if self._usage_page == 1 and self._usage == 2:
                    buttons = report[0] & 0x07
                    changed = self._prev_buttons ^ buttons
                    if changed:
                        for bit in range(3):
                            if changed & (1 << bit):
                                is_down = bool(buttons & (1 << bit))
                                self.on_button((bit + 1, is_down))
                    self._prev_buttons = buttons
                    continue

                # --- keyboard interface (1,6): bytes 2+ hold pressed keycodes ---
                if self._usage_page == 1 and self._usage == 6:
                    cur: set[int] = set()
                    for b in report[2:8]:
                        if b:
                            cur.add(b)
                    self._emit_edges(self._prev_keys, cur)
                    self._prev_keys = cur
                    continue

                # --- consumer interface (12,1): 16-bit LE consumer code ---
                if self._usage_page == 12 and self._usage == 1:
                    code = int.from_bytes(report[1:3], "little")
                    cur_c: set[int] = {code} if code else set()
                    self._emit_edges(set(), cur_c)  # no reliable release -> treat as one-shot with immediate release
                    continue

                # --- generic: diff whole report bytes against previous ---
                cur = {i for i, b in enumerate(report) if b}
                if cur != getattr(self, "_prev_generic", set()):
                    self._emit_edges(getattr(self, "_prev_generic", set()), cur)
                    self._prev_generic = cur
        finally:
            try:
                dev.close()
            except Exception:
                pass