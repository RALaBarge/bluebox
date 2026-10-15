#!/usr/bin/env python3.12
"""Event-tap listener — captures key presses at the OS level.

hidapi raw reads come up empty because macOS consumes HID reports before a raw
device reads them. A listen-only CGEventTap (system-wide HID tap) sees events
after dispatch. PyObjC requires the CoreFoundation run loop to be pumping for
the callback to fire — we run CFRunLoopRun for N seconds with a stop timer.

    usage: .venv/bin/python3.12 tap.py [seconds]

Needs Accessibility/Input Monitoring perms (granted for terminals).
"""

import sys
import time

from Foundation import CFRunLoopRun, CFRunLoopStop
from Quartz import (
    CGEventTapCreate,
    CGEventGetIntegerValueField,
    CGEventGetType,
    CGEventGetFlags,
    kCGEventTapOptionListenOnly,
    kCGHeadInsertEventTap,
    kCGHIDEventTap,
    kCGKeyboardEventKeycode,
    kCGEventTapDisabledByTimeout,
    kCGEventTapDisabledByUserInput,
)

KEY_MAP = {
    0x00: "A", 0x0B: "B", 0x08: "C", 0x02: "D", 0x0E: "E", 0x03: "F",
    0x05: "G", 0x04: "H", 0x22: "I", 0x26: "J", 0x28: "K", 0x25: "L",
    0x2E: "M", 0x2D: "N", 0x1F: "O", 0x23: "P", 0x0C: "Q", 0x0F: "R",
    0x01: "S", 0x11: "T", 0x20: "U", 0x09: "V", 0x0D: "W", 0x07: "X",
    0x06: "Y", 0x10: "Z", 0x1E: "Num1", 0x1D: "Num2",
    0x24: "Return", 0x35: "Escape", 0x31: "Space", 0x33: "Delete",
    0x7B: "Left", 0x7C: "Right", 0x7D: "Down", 0x7E: "Up",
}


def key_name(code: int) -> str:
    return KEY_MAP.get(code, f"0x{code:02x}")


def callback(*args):
    # CGEventTap tail callback gets: (proxy, eventType, event, refcon)
    try:
        proxy, typ, event, refcon = args
    except ValueError:
        return args[-1] if args else None
    vkey = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
    flags = CGEventGetFlags(event)
    note = ""
    if flags & kCGEventTapDisabledByTimeout:
        note = " [TAP-TIMEOUT]"
    elif flags & kCGEventTapDisabledByUserInput:
        note = " [TAP-USER]"
    name = key_name(vkey) if int(typ) in (12, 13) else f"(type {int(typ)})"
    print(f"type={typ:>3d} key={name} 0x{vkey:02x}{note}", flush=True)
    return event


def _stop(_timer=None):
    CFRunLoopStop()


def main():
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    tap = CGEventTapCreate(
        kCGHIDEventTap,
        kCGHeadInsertEventTap,
        kCGEventTapOptionListenOnly,
        0xFFFFFFFF,
        callback,
        None,
    )
    if not tap:
        print("failed to create tap — check Input Monitoring/Accessibility perm")
        return 1
    from PyObjCTools import AppHelper
    AppHelper.callLater(secs, AppHelper.stopEventLoop)
    print(f"event tap running {secs}s — press the R1 buttons now", flush=True)
    AppHelper.runEventLoop()
    return 0


if __name__ == "__main__":
    main()