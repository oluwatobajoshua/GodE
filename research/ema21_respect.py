#!/usr/bin/env python3
"""EMA21 cross persistence research.

Question: after price crosses EMA21 (up = long bias, down = short bias),
how many bars does price respect that direction before crossing back?

For each symbol/timeframe:
- Detect EMA21 crosses (close above/below EMA21, prior close on opposite side)
- Count consecutive bars price stays on the crossed side
- Report distribution: median, p75, p90 of 'respect bars'
- Also report win-rate of holding for N bars after cross

Run:
    .\.venv\Scripts\python.exe research\ema21_respect.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "ema21_respect"
OUT.mkdir(parents=True, exist_ok=True)

SYMS_TFS = [
    ("EURUSD", "H1"),
    ("GBPUSD", "H1"),
    ("USDJPY", "H1"),
    ("BTCUSD", "H1"),
]


def load_csv(sym: str, tf: str) -> pd.DataFrame:
    p = DATA / f"{sym}_{tf}.csv"
    if not p.exists():
        p = DATA / "mt5" / f"{sym}_{tf}.csv"
    d = pd.read_csv(p, parse_dates=["time"])
    d["time"] = pd.to_datetime(d["time"], utc=True, errors="coerce").dt.tz_convert(None)
    return d.set_index("time").sort_index()


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def analyze(df: pd.DataFrame, sym: str, tf: str) -> list[dict]:
    c = df["close"].astype(float)
    e21 = ema(c, 21)
    n = len(df)

    above = (c > e21).fillna(False)
    above_prev = above.shift(1).fillna(False)

    # Cross up: close above EMA21, prev close below or equal
    cross_up = above & ~above_prev
    # Cross down: close below EMA21, prev close above or equal
    cross_down = ~above & above_prev

    results = []

    for side_name, cross_mask in (("LONG (cross up)", cross_up), ("SHORT (cross down)", cross_down)):
        idxs = np.flatnonzero(cross_mask.values)
        respect_bars = []

        for i in idxs:
            if i >= n - 1:
                continue
            started_above = bool(above.iloc[i])
            # Count bars after the cross bar where close stays on the crossed side
            k = 0
            j = i + 1
            while j < n:
                cj = float(c.iloc[j])
                ej = float(e21.iloc[j])
                if pd.isna(ej):
                    break
                if started_above:
                    if cj > ej:
                        k += 1
                        j += 1
                    else:
                        break
                else:
                    if cj < ej:
                        k += 1
                        j += 1
                    else:
                        break
            respect_bars.append(k)

        respect_bars = np.asarray(respect_bars, dtype=float)
        if respect_bars.size == 0:
            continue

        # Hold-for-N forward return: buy at close of cross bar, sell N bars later
        forward_rets = {}
        for hold_n in (1, 2, 3, 5, 8, 12, 24, 48):
            rets = []
            for i in idxs:
                if i + hold_n >= n:
                    continue
                entry = float(c.iloc[i])
                exit_px = float(c.iloc[i + hold_n])
                if side_name.startswith("LONG"):
                    r = (exit_px - entry) / entry
                else:
                    r = (entry - exit_px) / entry
                rets.append(r)
            if rets:
                arr = np.asarray(rets)
                forward_rets[hold_n] = {
                    "n": int(arr.size),
                    "winrate": float((arr > 0).mean() * 100),
                    "mean_ret_pct": float(arr.mean() * 100),
                    "median_ret_pct": float(np.median(arr) * 100),
                }

        results.append({
            "symbol": sym, "tf": tf, "side": side_name,
            "n_crosses": int(respect_bars.size),
            "respect_min": int(respect_bars.min()),
            "respect_p25": float(np.percentile(respect_bars, 25)),
            "respect_median": float(np.percentile(respect_bars, 50)),
            "respect_p75": float(np.percentile(respect_bars, 75)),
            "respect_p90": float(np.percentile(respect_bars, 90)),
            "respect_max": int(respect_bars.max()),
            "respect_mean": float(respect_bars.mean()),
            "immediate_revert_pct": float((respect_bars == 0).mean() * 100),
            "forward_returns": forward_rets,
        })

    return results


def main() -> int:
    all_rows = []
    for sym, tf in SYMS_TFS:
        try:
            df = load_csv(sym, tf)
        except FileNotFoundError:
            print(f"SKIP {sym}_{tf}: file not found")
            continue
        print(f"\n=== {sym} {tf} ({len(df)} bars) ===")
        res = analyze(df, sym, tf)
        for r in res:
            fr = r.pop("forward_returns")
            all_rows.append({**r, **{f"hold{k}_win": v["winrate"] for k, v in fr.items()},
                              **{f"hold{k}_ret": v["mean_ret_pct"] for k, v in fr.items()}})
            print(
                "%s: n=%d med_respect=%.0f p75=%.0f p90=%.0f mean=%.1f "
                "immediate_revert=%.1f%% | hold5 win=%.1f%% ret=%+.2fbps | "
                "hold12 win=%.1f%% ret=%+.2fbps | hold24 win=%.1f%% ret=%+.2fbps"
                % (
                    r["side"], r["n_crosses"], r["respect_median"], r["respect_p75"],
                    r["respect_p90"], r["respect_mean"], r["immediate_revert_pct"],
                    fr[5]["winrate"], fr[5]["mean_ret_pct"] * 100,
                    fr[12]["winrate"], fr[12]["mean_ret_pct"] * 100,
                    fr[24]["winrate"], fr[24]["mean_ret_pct"] * 100,
                )
            )

    df_out = pd.DataFrame(all_rows)
    df_out.to_csv(OUT / "ema21_respect_summary.csv", index=False)
    print(f"\nWrote {OUT / 'ema21_respect_summary.csv'}")

    # Recommendation: what min consecutive closes would filter bad crosses?
    print("\n=== RECOMMENDATION (min consecutive closes above/below EMA21) ===")
    for sym, tf in SYMS_TFS:
        sub = df_out[(df_out["symbol"] == sym) & (df_out["tf"] == tf)]
        for _, r in sub.iterrows():
            # p75 of respect bars: require at least this many to capture most good moves
            rec = max(2, int(np.floor(r["respect_p75"])))
            print(
                "%s %s %s: respect_p75=%.0f -> require min %d consecutive closes"
                % (sym, tf, r["side"], r["respect_p75"], rec)
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
