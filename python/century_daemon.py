#!/usr/bin/env python3
"""Hourly adapter daemon — writes century_directives.json for the EA."""
from __future__ import annotations

import os
import time

from century_adapt import run_once

try:
    from fxmopol_adapt import main as fxmopol_adapt_once
except ImportError:
    fxmopol_adapt_once = None

try:
    from market_ingest import run_once as market_ingest_once
except ImportError:
    market_ingest_once = None

INTERVAL = float(os.environ.get("SOVEREIGN_ADAPT_INTERVAL_SEC", "3600"))


def main() -> int:
    print(f"[century_daemon] interval={INTERVAL}s")
    while True:
        try:
            run_once()
            if fxmopol_adapt_once is not None:
                fxmopol_adapt_once()
            if market_ingest_once is not None:
                market_ingest_once()
        except Exception as exc:
            print(f"[century_daemon] error: {exc}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    raise SystemExit(main())
