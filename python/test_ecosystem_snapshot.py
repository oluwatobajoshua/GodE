#!/usr/bin/env python3
"""Smoke test — ecosystem professor snapshot schema + gates."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python"))

from ecosystem_professor import build_ecosystem_snapshot  # noqa: E402
from market_ingest import run_once  # noqa: E402


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_build_schema() -> None:
    snap = build_ecosystem_snapshot(["GBPUSD"], mt5_files=REPO / "backtest" / "mt5_files" / "Sovereign")
    _assert(snap.get("schema_version") == 2, "schema_version")
    _assert("knowledge_coverage" in snap, "knowledge_coverage")
    _assert(snap.get("role") == "sovereign_ecosystem_professor", "role")
    _assert("symbol_gates" in snap and "GBPUSD" in snap["symbol_gates"], "symbol_gates")
    _assert("microstructure" in snap, "microstructure top-level key")
    g = snap["symbol_gates"]["GBPUSD"]
    for key in ("risk_mult", "block_long", "block_short", "verdict", "professor_line"):
        _assert(key in g, f"missing gate key {key}")
    sym = snap["symbols"]["GBPUSD"]
    for pillar in (
        "technical",
        "fundamental",
        "sentiment",
        "session",
        "intermarket",
        "positioning",
        "events",
        "cross_section",
        "volatility",
        "liquidity",
        "gates",
    ):
        _assert(pillar in sym, f"missing pillar {pillar}")
    exp = datetime.fromisoformat(snap["expires_at"].replace("Z", "+00:00"))
    gen = datetime.fromisoformat(snap["generated_at"].replace("Z", "+00:00"))
    if gen.tzinfo is None:
        gen = gen.replace(tzinfo=timezone.utc)
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    _assert(exp > gen, "expires_at after generated_at")


def test_write_file() -> None:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "market_snapshot.json"
        snap = run_once(["EURUSD"], out_path=out)
        raw = json.loads(out.read_text(encoding="utf-8"))
        _assert(raw["symbols"]["EURUSD"]["fundamental"]["layers"]["rates"], "rates layer")
        _assert(snap["global"]["professor_headline"], "headline")


def main() -> int:
    try:
        test_build_schema()
        test_write_file()
    except AssertionError as exc:
        print(f"[test_ecosystem_snapshot] FAIL: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"[test_ecosystem_snapshot] SKIP data: {exc}", file=sys.stderr)
        return 1
    print("[test_ecosystem_snapshot] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
