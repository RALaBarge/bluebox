#!/usr/bin/env python3.12
"""Standalone R1 report sniffer — prints distinct reports it sees, with counts.

Used to identify which physical button sends which HID report, INDEPENDENT of
the daemon (stop the daemon first so this gets the R1). Safe: read-only, only
targets 0x004c:0x014c, never grabs the internal trackpad.

Run: .venv/bin/python3.12 sniff_codes.py [seconds]
"""
import sys
import time
import collections

import hid

R1_VID = 0x004C
R1_PID = 0x014C


def sniff(seconds: int = 30):
    dev = None
    for d in hid.enumerate():
        if d["vendor_id"] == R1_VID and d["product_id"] == R1_PID:
            if d.get("usage_page") == 12:  # consumer interface carries buttons
                path = d["path"]
                if isinstance(path, str):
                    path = path.encode()
                dev = hid.device()
                try:
                    dev.open_path(path)
                except Exception as e:
                    print(f"open failed (is the daemon holding it?): {e}")
                    return
                break
    if dev is None:
        print("R1 not found")
        return
    print(f"sniffing {seconds}s — press buttons one at a time", flush=True)
    counts = collections.Counter()
    t0 = time.time()
    while time.time() - t0 < seconds:
        r = dev.read(256, timeout_ms=200)
        if r:
            counts[bytes(r).hex()] += 1
    print("--- reports seen ---")
    for k, v in sorted(counts.items()):
        print(f"  {k}  x{v}")


if __name__ == "__main__":
    sniff(int(sys.argv[1]) if len(sys.argv) > 1 else 30)
