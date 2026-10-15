#!/usr/bin/env python3.12
"""Live-sniff the Magicsee R1 BT HID reports (SAFE — read-only, no grab).

Auto-discovers the R1 by vendor/product id (0x004c:0x014c) so it works even
after a reconnect renumbers the DevSrvsID path. Opens the device and prints
every raw report it sees with a timestamp, so we can learn what each button
and the finger-slide actually send.

Run: .venv/bin/python3.12 sniff_r1.py [seconds]
"""
import sys
import time

import hid

R1_VID = 0x004C
R1_PID = 0x014C


def find_r1_path() -> bytes:
    for d in hid.enumerate():
        if d["vendor_id"] == R1_VID and d["product_id"] == R1_PID:
            p = d["path"]
            if isinstance(p, str):
                p = p.encode()
            return p
    return b""


def sniff(seconds: int = 45):
    path = find_r1_path()
    if not path:
        print("R1 not found — is it powered on / paired?", flush=True)
        return
    print(f"R1 path: {path.decode(errors='replace')}", flush=True)

    dev = hid.device()
    try:
        dev.open_path(path)
    except Exception as e:
        print(f"open failed (check Input Monitoring / Bluetooth perms): {e}", flush=True)
        return
    dev.set_nonblocking(False)
    print(f"sniffing R1 for {seconds}s — NOW push each physical button one at a time, "
          f"then slide your finger across the pad. Hold each ~1s.", flush=True)

    t0 = time.time()
    cutoff = t0 + seconds
    while time.time() < cutoff:
        report = dev.read(256, timeout_ms=300)
        if report is None or len(report) == 0:
            continue
        dt = time.time() - t0
        print(f"t={dt:6.2f}s  len={len(report):3d}  {bytes(report).hex(' ')}", flush=True)
    dev.close()
    print("done", flush=True)


if __name__ == "__main__":
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 45
    sniff(secs)
