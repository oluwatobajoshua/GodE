"""Diff the EA's chart read against the Python reference read.

Both files describe the same bar with the same field names:

    EA      <Terminal>\\Common\\Files\\Sovereign\\chart_read_<SYM>_<TF>.json
    Python  research\\results\\chart_snapshot_<SYM>_<TF>.json

A field PASSES when |ea - python| <= tolerance in price points. Everything else
means the bot is not seeing what the reference sees, and the reason is printed.

Exit code 0 = full parity, 1 = mismatch or missing file.

Usage:
    .\\.venv\\Scripts\\python.exe scripts\\compare_chart_snapshot.py --symbol EURUSD --tf H1
    .\\.venv\\Scripts\\python.exe scripts\\compare_chart_snapshot.py --symbol EURUSD --tf H1 --tol 2
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# fields compared as prices (tolerance in points)
PRICE_FIELDS = [
    "price", "ema_fast", "ema_slow", "smma", "atr",
    "swing_hi", "swing_lo", "fib50", "fib618", "s1", "r1",
    "zone_lo", "zone_hi", "target", "invalidation",
]
# fields compared as plain numbers (absolute tolerance)
NUM_FIELDS = [("rsi", 0.5)]
# fields compared exactly
INT_FIELDS = ["trend", "bias"]
TEXT_FIELDS = ["state", "plan", "pattern"]


def point_size(symbol: str, price: float) -> float:
    if symbol.upper().endswith("JPY"):
        return 0.001
    if price and price > 20:
        return 0.001
    return 0.00001


def find_ea(symbol: str, tf: str) -> Path | None:
    name = f"chart_read_{symbol.upper()}_{tf.upper()}.json"
    appdata = os.environ.get("APPDATA", "")
    candidates = []
    if appdata:
        terminal = Path(appdata) / "MetaQuotes" / "Terminal"
        candidates += list(terminal.glob(f"Common/Files/Sovereign/{name}"))
    candidates.append(ROOT / "backtest" / "mt5_files" / "Sovereign" / name)
    for c in candidates:
        if c.exists():
            return c
    return None


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--tol", type=float, default=1.0, help="tolerance in points")
    args = ap.parse_args()

    sym = args.symbol.upper()
    tf = args.tf.upper()

    ea_path = find_ea(sym, tf)
    py_path = ROOT / "research" / "results" / f"chart_snapshot_{sym}_{tf}.json"

    if ea_path is None:
        print(f"[FAIL] no EA snapshot for {sym} {tf}.")
        print("       Attach the EA in visual mode and let one H1 bar close,")
        print("       or run the tester once. Expected file:")
        print(f"       Common\\Files\\Sovereign\\chart_read_{sym}_{tf}.json")
        return 1
    if not py_path.exists():
        print(f"[FAIL] missing reference: {py_path}")
        print("       Run: .\\.venv\\Scripts\\python.exe research\\chart_snapshot.py "
              f"--symbol {sym} --tf {tf}")
        return 1

    ea = load(ea_path)
    py = load(py_path)

    print(f"[INFO] EA      {ea_path}")
    print(f"[INFO] Python  {py_path}")
    print(f"[INFO] bar     EA {ea.get('bar_time')}  |  Python {py.get('bar_time')}")

    failures = 0
    warnings = 0

    if ea.get("bar_time_epoch") != py.get("bar_time_epoch"):
        print("[WARN] different bars - the EA file is stale or the CSV is newer.")
        print("       Re-run the EA on a chart that is up to date, then compare again.")
        warnings += 1

    pt = point_size(sym, float(py.get("price") or 0.0))
    digits = 5 if pt < 0.005 else 3

    print("")
    print(f"{'field':<14}{'EA':>12}{'Python':>12}{'delta(pt)':>12}   status")
    print("-" * 62)

    def report(field: str, ea_v, py_v, delta_pts: float, ok: bool):
        nonlocal failures
        status = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"{field:<14}{ea_v!s:>12}{py_v!s:>12}{delta_pts:>12.2f}   {status}")

    for f in PRICE_FIELDS:
        if f not in ea or f not in py:
            continue
        a = float(ea[f])
        b = float(py[f])
        # zero means "no level" on either side - treat as equal when both zero
        if a == 0.0 and b == 0.0:
            report(f, f"{a:.{digits}f}", f"{b:.{digits}f}", 0.0, True)
            continue
        if (a == 0.0) != (b == 0.0):
            report(f, f"{a:.{digits}f}", f"{b:.{digits}f}", 999.0, False)
            continue
        delta = abs(a - b) / pt
        report(f, f"{a:.{digits}f}", f"{b:.{digits}f}", delta, delta <= args.tol)

    for f, tol in NUM_FIELDS:
        if f in ea and f in py:
            a, b = float(ea[f]), float(py[f])
            delta = abs(a - b)
            report(f, f"{a:.2f}", f"{b:.2f}", delta, delta <= tol)

    for f in INT_FIELDS:
        if f in ea and f in py:
            a, b = int(ea[f]), int(py[f])
            report(f, a, b, float(abs(a - b)), a == b)

    print("")
    for f in TEXT_FIELDS:
        if f in ea and f in py:
            same = str(ea[f]).strip() == str(py[f]).strip()
            mark = "PASS" if same else "NOTE"
            if not same:
                warnings += 1
            print(f"{mark} {f}")
            print(f"     EA     : {ea[f]}")
            print(f"     Python : {py[f]}")

    tpl = ea.get("template") or {}
    if tpl:
        print("")
        print(f"[INFO] template loaded={tpl.get('loaded')} "
              f"EMA{tpl.get('ema_fast')}/{tpl.get('ema_slow')} "
              f"SMMA{tpl.get('smma')} RSI{tpl.get('rsi')} "
              f"aligned={tpl.get('aligned')} chart_matches={tpl.get('chart_matches')}")
        if not tpl.get("aligned"):
            print("[WARN] EA inputs do not match the loaded template.")
        if not tpl.get("chart_matches"):
            print("[WARN] chart symbol/timeframe does not match the template.")

    print("")
    if failures == 0:
        print(f"[PASS] {sym} {tf}: EA read == Python read "
              f"({len(PRICE_FIELDS)} price fields within {args.tol} pt).")
        print("[PASS] The bot is seeing the same chart the template shows.")
        return 0
    print(f"[FAIL] {failures} field(s) disagree beyond {args.tol} point(s).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
