#!/usr/bin/env python3
"""Audit MT5 data plane: broker vs HistData tape, sessions, spread, stack states.

Run:
    .\\.venv\\Scripts\\python.exe research\\mt5_data_plane_audit.py
    .\\scripts\\sync_mt5_data_plane.ps1 -AuditOnly
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CONFIG = REPO / "configs" / "mt5_data_plane.json"
OUT = HERE / "results" / "mt5_data_plane_audit.json"
SNAP = REPO / "research" / "results" / "market_snapshot.json"
SNAP_BT = REPO / "backtest" / "mt5_files" / "Sovereign" / "market_snapshot.json"

from bar_store import (  # noqa: E402
    bar_source_meta,
    canonical_source,
    load_h1,
    load_h1_optional,
)
from fxmopol_chart_core import (  # noqa: E402
    DEFAULT_WEEKDAY_MASK,
    build_stack,
    chart_state_at,
    session_allows_new_entry,
    stack_long,
    stack_short,
    weekday_allows_new_entry,
)


def _pip_size(sym: str) -> float:
    sym = sym.upper()
    if sym.endswith("JPY") or sym.startswith("USDJPY"):
        return 0.01
    return 0.0001


def _load_config() -> dict:
    if not CONFIG.exists():
        return {}
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _session_histogram(idx: pd.DatetimeIndex) -> dict[str, float]:
    hours = idx.hour
    prime = sum(1 for h in hours if session_allows_new_entry(int(h), enabled=True))
    tue_thu = sum(
        1 for t in idx if weekday_allows_new_entry(int(t.dayofweek), DEFAULT_WEEKDAY_MASK)
    )
    n = max(len(idx), 1)
    return {
        "bars": len(idx),
        "pct_hour_07_11": float(((hours >= 7) & (hours <= 11)).mean()),
        "pct_hour_13_16": float(((hours >= 13) & (hours <= 16)).mean()),
        "pct_prime_session": prime / n,
        "pct_tue_thu": tue_thu / n,
    }


def _chart_states(h1: pd.DataFrame, ema_mid: int = 50, sample: int | None = 5000) -> pd.Series:
    frame = h1.reset_index()
    if "time" not in frame.columns:
        frame = frame.rename(columns={frame.columns[0]: "time"})
    st = build_stack(frame, ema_mid)
    c, e21, e50, s100, r, atr = st["close"], st["ema21"], st["ema50"], st["smma100"], st["rsi"], st["atr"]
    n = len(c)
    long_flags = [stack_long(c, e21, e50, s100, i) for i in range(n)]
    short_flags = [stack_short(c, e21, e50, s100, i) for i in range(n)]
    states = []
    idx_list = list(range(120, n))
    if sample and len(idx_list) > sample:
        idx_list = idx_list[:: max(1, len(idx_list) // sample)]
    for i in idx_list:
        state, _, _ = chart_state_at(i, c, e21, e50, s100, r, atr, long_flags, short_flags)
        states.append((h1.index[i], state))
    return pd.Series({t: s for t, s in states})


def _compare_tapes(mt5: pd.DataFrame, hist: pd.DataFrame, sym: str, t0: pd.Timestamp, t1: pd.Timestamp) -> dict:
    pip = _pip_size(sym)
    a = mt5.loc[(mt5.index >= t0) & (mt5.index < t1)].copy()
    b = hist.loc[(hist.index >= t0) & (hist.index < t1)].copy()
    merged = a[["close"]].join(b[["close"]], how="inner", rsuffix="_hist")
    if merged.empty:
        return {"overlap_bars": 0, "error": "no_overlap"}

    merged["diff_pips"] = (merged["close"] - merged["close_hist"]).abs() / pip
    out: dict = {
        "overlap_bars": len(merged),
        "close_diff_pips_mean": float(merged["diff_pips"].mean()),
        "close_diff_pips_p50": float(merged["diff_pips"].median()),
        "close_diff_pips_p99": float(merged["diff_pips"].quantile(0.99)),
        "close_diff_pips_max": float(merged["diff_pips"].max()),
    }

    sm = _chart_states(a)
    sh = _chart_states(b)
    common = sm.index.intersection(sh.index)
    if len(common) > 10:
        mismatch = (sm.loc[common] != sh.loc[common]).mean()
        out["chart_state_mismatch_pct"] = float(mismatch * 100.0)
    else:
        out["chart_state_mismatch_pct"] = None

    # hour histogram on overlap index
    out["session_mt5"] = _session_histogram(merged.index)
    return out


def _spread_block(h1: pd.DataFrame, sym: str) -> dict:
    if "spread" not in h1.columns:
        return {"available": False, "note": "no spread column — export from MT5 or use tester real ticks"}
    sp = pd.to_numeric(h1["spread"], errors="coerce").dropna()
    if sp.empty:
        return {"available": False}
    points = float(sp.median())
    pip = _pip_size(sym)
    # MT5 spread is in points; for 5-digit FX ~10 points = 1 pip
    spread_pips_est = points * 0.1 if pip == 0.0001 else points * 0.01
    return {
        "available": True,
        "median_points": points,
        "median_pips_est": round(spread_pips_est, 2),
        "p90_points": float(sp.quantile(0.9)),
    }


def _snapshot_freshness() -> dict:
    paths = [SNAP, SNAP_BT]
    best = None
    for p in paths:
        if not p.exists():
            continue
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        row = {
            "path": str(p),
            "generated_at": j.get("generated_at"),
            "expires_at": j.get("expires_at"),
            "knowledge_pct": (j.get("knowledge_coverage") or {}).get("coverage_pct"),
        }
        if best is None:
            best = row
    return best or {"path": None, "note": "run market_ingest or sync_mt5_data_plane.ps1"}


def audit_symbol(sym: str, cfg: dict) -> dict:
    sym = sym.upper()
    meta = bar_source_meta(sym)
    row: dict = {"symbol": sym, "bar_meta": meta}
    aud = cfg.get("audit") or {}
    t0 = pd.Timestamp(aud.get("overlap_from", "2024-01-01"))
    t1 = pd.Timestamp(aud.get("overlap_to", "2026-01-01"))

    mt5 = load_h1_optional(sym, "mt5")
    hist = load_h1_optional(sym, "histdata")
    if hist is None:
        hist = load_h1_optional(sym, "legacy")

    try:
        canon = load_h1(sym)
        row["canonical_bars"] = len(canon)
        row["canonical_range"] = [str(canon.index.min()), str(canon.index.max())]
        row["session_canonical"] = _session_histogram(canon.index)
        row["spread"] = _spread_block(canon, sym)
        row["last_bar"] = {
            "time": str(canon.index[-2]),
            "close": float(canon["close"].iloc[-2]),
            "hour_utc": int(canon.index[-2].hour),
            "dow": int(canon.index[-2].dayofweek),
        }
    except FileNotFoundError as exc:
        row["error"] = str(exc)
        row["data_plane_ok"] = False
        row["block_reason"] = "missing_h1"
        canon = None

    if mt5 is not None and hist is not None:
        row["tape_compare"] = _compare_tapes(mt5, hist, sym, t0, t1)
    elif mt5 is not None:
        row["tape_compare"] = {"note": "histdata missing — cannot compare broker vs research archive"}
    else:
        row["tape_compare"] = {"note": "mt5 export missing — run scripts/export_mt5_bars.py"}

    row["data_plane_ok"] = "error" not in row
    if aud.get("require_mt5_export_for_go") and mt5 is None:
        row["data_plane_ok"] = False
        row["block_reason"] = "missing_mt5_export"
    if meta.get("resolved_source") != "mt5" and canonical_source() == "mt5":
        row["data_plane_ok"] = False
        row["block_reason"] = "not_using_mt5_canonical"
    if meta.get("resolved_source") == "histdata" and canonical_source() == "mt5" and mt5 is None:
        row["warning"] = "canonical prefers mt5 but fell back to histdata"

    tc = row.get("tape_compare") or {}
    histdiv = False
    if tc.get("close_diff_pips_p99", 0) > float(aud.get("close_pips_p99_max", 3.0)):
        histdiv = True
        row["histdata_warning"] = "close_diff_p99_exceeded"
    mm = tc.get("chart_state_mismatch_pct")
    if mm is not None and mm > float(aud.get("chart_state_mismatch_pct_max", 5.0)):
        histdiv = True
        row["histdata_warning"] = row.get("histdata_warning") or "chart_state_mismatch"
    row["histdata_divergence"] = histdiv
    if histdiv and aud.get("fail_on_histdata_divergence"):
        row["data_plane_ok"] = False
        row["block_reason"] = row.get("histdata_warning")

    return row


def main() -> int:
    cfg = _load_config()
    symbols = cfg.get("symbols") or ["GBPUSD", "EURUSD", "USDJPY"]
    per = [audit_symbol(s, cfg) for s in symbols]

    report = {
        "canonical_bar_source": canonical_source(),
        "config": str(CONFIG),
        "snapshot": _snapshot_freshness(),
        "symbols": per,
        "data_plane_go": all(r.get("data_plane_ok") for r in per if "error" not in r),
        "guidance": [
            "Export broker H1: .\\scripts\\sync_mt5_data_plane.ps1 -Export",
            "Refresh professor: same script runs ingest + copies to MQL5/Files",
            "Tester should use same symbol history as research/data/mt5/",
            "If close_diff_p99 > 3 pips, do not trust HistData-vs-tester parity on H1",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print("=== MT5 Data Plane Audit ===")
    print(f"canonical_source={report['canonical_bar_source']} go={report['data_plane_go']}")
    for r in per:
        sym = r["symbol"]
        src = (r.get("bar_meta") or {}).get("resolved_source")
        print(f"\n{sym}: source={src} ok={r.get('data_plane_ok')}")
        if r.get("error"):
            print(f"  ERROR {r['error']}")
        if r.get("warning"):
            print(f"  WARN {r['warning']}")
        tc = r.get("tape_compare") or {}
        if tc.get("overlap_bars"):
            print(
                f"  overlap={tc['overlap_bars']} close_p99={tc.get('close_diff_pips_p99', 0):.2f} pips "
                f"state_mismatch={tc.get('chart_state_mismatch_pct')}%"
            )
        if r.get("histdata_divergence"):
            print("  HISTDATA divergence (expected) - do not train on histdata when testing in MT5")
        sp = r.get("spread") or {}
        if sp.get("available"):
            print(f"  spread median ~{sp.get('median_pips_est')} pips (points={sp.get('median_points')})")
    print(f"\nWrote {OUT}")
    return 0 if report["data_plane_go"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
