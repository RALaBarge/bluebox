#!/usr/bin/env python3.12
"""Client for the TTS daemon. Queues text to be spoken; returns immediately.

    ./say "build finished, three tickets left"
    ./say --source triage "batch done"
    echo "piped text" | ./say
    ./say --status | ./say --skip | ./say --clear

Deliberately dumb + stdlib-only so any session/script can call it.
Fails soft: if the daemon is down it prints a one-line warning and exits 0,
so `./say` never breaks the pipeline that called it.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8740"


def _req(path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if data is not None or path in ("/skip", "/clear") else "GET",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def main() -> None:
    ap = argparse.ArgumentParser(description="queue text for speech")
    ap.add_argument("text", nargs="*", help="text to speak (or pipe via stdin)")
    ap.add_argument("--voice", default=None, help="kokoro voice (default af_heart)")
    ap.add_argument("--speed", type=float, default=None)
    ap.add_argument("--source", default=None, help="label shown in /status")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--skip", action="store_true", help="stop current utterance")
    ap.add_argument("--clear", action="store_true", help="drop queued utterances")
    args = ap.parse_args()

    try:
        if args.status:
            print(json.dumps(_req("/status"), indent=2))
            return
        if args.skip:
            _req("/skip", {})
            print("skipped")
            return
        if args.clear:
            print(json.dumps(_req("/clear", {})))
            return

        text = " ".join(args.text).strip()
        if not text and not sys.stdin.isatty():
            text = sys.stdin.read().strip()
        if not text:
            ap.error("no text given")
        out = _req("/say", {
            "text": text, "voice": args.voice,
            "speed": args.speed, "source": args.source or "cli",
        })
        print(f"queued (depth {out.get('queued')})")
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        print(f"[say] tts daemon unreachable ({e}) — text NOT spoken", file=sys.stderr)
        sys.exit(0)  # fail soft by design


if __name__ == "__main__":
    main()
