#!/usr/bin/env python3.12
"""Self-test: inject a synthetic key-down/key-up event; tap must catch it.

Proves the CGEventTap pipeline (create + run-loop + callback) works at all,
independent of user timing. If this logs the injected key, the tap is lifting
events and the earlier empties were simply "nobody pressed during the window".
"""
import time

from Foundation import CFRunLoopRun, CFRunLoopStop
from Quartz import (
    CGEventTapCreate,
    CGEventGetIntegerValueField,
    CGEventGetType,
    CGEventGetFlags,
    kCGEventTapDisabledByTimeout,
    kCGEventTapDisabledByUserInput,
    kCGHeadInsertEventTap,
    kCGHIDEventTap,
    kCGKeyboardEventKeycode,
    CGEventTapEnable,
    CGEventPost,
    CGEventSourceCreate,
    kCGEventSourceStatePrivate,
    CGEventCreateKeyboardEvent,
    kCGHIDEventTap,
)
from PyObjCTools import AppHelper

SEEN = []


def callback(*args):
    try:
        proxy, typ, event, refcon = args
    except ValueError:
        return args[-1] if args else None
    vkey = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
    typ = int(CGEventGetType(event))
    SEEN.append((typ, vkey))
    print(f"CAUGHT type={typ} vkey=0x{vkey:02x}", flush=True)
    return event


def main():
    tap = CGEventTapCreate(
        kCGHIDEventTap, kCGHeadInsertEventTap,
        0,  # listen-only
        0xFFFFFFFF, callback, None,
    )
    if not tap:
        print("NO TAP — perm issue", flush=True)
        return 1
    CGEventTapEnable(tap, True)

    # Post a synthetic 'D' keypress so the tap has something to catch.
    src = CGEventSourceCreate(kCGEventSourceStatePrivate)
    e = CGEventCreateKeyboardEvent(src, 0x02, True)   # D down
    CGEventPost(kCGHIDEventTap, e)
    e2 = CGEventCreateKeyboardEvent(src, 0x02, False)  # D up
    CGEventPost(kCGHIDEventTap, e2)

    AppHelper.runEventLoop()
    time.sleep(3)
    print(f"TAP SAW {len(SEEN)} events (expect >=2 if working)", flush=True)
    return 0 if SEEN else 1


if __name__ == "__main__":
    import time
    main()