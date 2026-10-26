#!/usr/bin/env python3
"""GUI entry point: `python3 -m app.main` from the repo root."""
import os
import sys

if __package__ in (None, ""):
    # Run as a plain script rather than -m; make the repo root importable so
    # `app` resolves as a package.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app.gui import run
else:
    from .gui import run

if __name__ == "__main__":
    run()
