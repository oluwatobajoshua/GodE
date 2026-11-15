#!/usr/bin/env python3
r"""MA Compression Rubberband — parameter sweep across compress_atr x ext_atr.
Tests all 3 pairs on chronological design/validation/OOS splits with real cost model.

Proof bar: the config must clear PF >= 1.05, Sharpe >= 0.25 and maxDD >= -30%
in ALL THREE splits. Selection is on design+validation only; OOS is the exam.
An earlier revision selected on OOS and called 44/48 "passes" - those were
OOS-only and are not results.

Run:
    .\.venv\Scripts\python.exe research\ma_compression_sweep.py
"""

from __future__ import annotations

import itertools
import math
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_core import simulate_stops, trade_metrics

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "mt5"
OUT = HERE / "results" / "ma_compression_sweep.csv"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY"]

SPLITS = {
    "design": ("2018-01-01", "2022-01-01"),
    "validation": ("2022-01-01", "2024-01-01"),
    "OOS": ("2024-01-01", "2026-12-31"),
}

PASS_PF = 1.05
PASS_SHARPE = 0.25
PASS_MAXDD = -30.0
BARS_PER_YEAR_H1 = 24 * 252

PARAM_GRID = list(itertools.product(
    [0.3, 0.5, 0.7, 1.0],   # compress_atr
    [1.5, 2.0, 2.5, 3.0],   # ext_atr
))


def load_csv(symbol: str) -> pd.DataFrame:
    path = DATA / f"{symbol}_H1.csv"
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce").dt.tz_convert(None)
    df = df.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
    return df


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def wilder_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = np.maximum(high - low, np.maximum(abs(high - prev_close), abs(low - prev_close)))
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def metrics(returns: pd.Series) -> dict:
    r = returns.dropna()
    if r.empty or r.std() == 0:
        return {"cagr": 0.0, "sharpe": 0.0, "maxdd": 0.0, "pf": 0.0, "winrate": 0.0, "n": 0}
    eq = (1 + r).cumprod()
    years = max(len(r) / BARS_PER_YEAR_H1, 1e-6)
    cagr = eq.iloc[-1] ** (1 / years) - 1
    sharpe = r.mean() / r.std() * math.sqrt(BARS_PER_YEAR_H1)
    peak = np.maximum.accumulate(eq)
    maxdd = (eq / peak - 1).min()
    wins = r[r > 0]
    losses = r[r < 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else np.nan
    return {
        "cagr": round(cagr * 100, 2),
        "sharpe": round(sharpe, 2),
        "maxdd": round(maxdd * 100, 2),
        "pf": round(pf, 2) if pd.notna(pf) else None,
        "winrate": round((r > 0).mean() * 100, 1),
        "n": len(r),
    }


def slice_split(r: pd.Series, start: str, end: str, times: pd.Series) -> pd.Series:
    mask = (times.values >= np.datetime64(pd.Timestamp(start))) & (times.values < np.datetime64(pd.Timestamp(end)))
    return r.iloc[mask]


def hyp_compression_rubberband(df: pd.DataFrame, compress_atr: float = 0.5,
                                ext_atr: float = 2.0,
                                ema_fast: int = 8, ema_mid: int = 21, ema_slow: int = 50,
                                atr_period: int = 14) -> pd.Series:
    e8 = ema(df["close"], ema_fast)
    e21 = ema(df["close"], ema_mid)
    e50 = ema(df["close"], ema_slow)
    atr = wilder_atr(df, atr_period)
    close = df["close"]
    n = len(df)
    pos = pd.Series(0, index=df.index)

    state = 0
    entry_bar = -10**9

    for i in range(ema_slow + atr_period, n):
        e8v, e21v, e50v = e8.iloc[i], e21.iloc[i], e50.iloc[i]
        av = atr.iloc[i]
        c = close.iloc[i]
        if not (np.isfinite(e8v) and np.isfinite(e21v) and np.isfinite(e50v)
                and np.isfinite(av) and av > 0):
            pos.iloc[i] = state
            continue

        ma_spread = max(abs(e8v - e21v), abs(e21v - e50v), abs(e8v - e50v)) / av
        compressed = ma_spread < compress_atr
        ma_center = (e8v + e21v + e50v) / 3.0
        band = ext_atr * av

        if state > 0:
            if c >= ma_center:
                state = 0
                entry_bar = -10**9
        elif state < 0:
            if c <= ma_center:
                state = 0
                entry_bar = -10**9
        else:
            if compressed:
                if c < ma_center - band:
                    state = 1
                    entry_bar = i
                elif c > ma_center + band:
                    state = -1
                    entry_bar = i
        pos.iloc[i] = state
    return pos


def backtest_positions(df: pd.DataFrame, pos: pd.Series, symbol: str,
                       comm_pts: float = 7.0, slip_pts: float = 1.0,
                       floor_pts: float = 1.0) -> tuple:
    """Delegate to the one shared contract in research/backtest_core.py.

    The local implementation here was the original source of the retracted
    result: it stored (close[i] - entry_price), the cumulative gain since
    entry, in the return series. simulate_stops marks to market bar by bar.
    """
    return simulate_stops(df, pos, symbol, comm_pts=comm_pts,
                          slip_pts=slip_pts, floor_pts=floor_pts)



def main() -> int:
    all_rows = []
    for sym in PAIRS:
        print(f"\n=== {sym} ===")
        df = load_csv(sym)
        print(f"  bars: {len(df)}  {df['time'].iloc[0]} -> {df['time'].iloc[-1]}")
        times = df["time"]

        for compress_atr, ext_atr in PARAM_GRID:
            pos = hyp_compression_rubberband(df, compress_atr=compress_atr, ext_atr=ext_atr)
            rets, trades, trade_exit = backtest_positions(df, pos, sym)

            trade_times = (times.iloc[trade_exit] if len(trade_exit)
                           else pd.Series(dtype="datetime64[ns]"))

            row = {"symbol": sym, "compress_atr": compress_atr, "ext_atr": ext_atr}
            for split, (start, end) in SPLITS.items():
                part = slice_split(rets, start, end, times)
                m = metrics(part)
                mask = ((trade_times.values >= np.datetime64(pd.Timestamp(start)))
                        & (trade_times.values < np.datetime64(pd.Timestamp(end))))
                tm = trade_metrics(trades[mask])
                row[f"{split}_pf"] = tm["pf"]
                row[f"{split}_winrate"] = tm["winrate"]
                row[f"{split}_n"] = tm["trades"]
                row[f"{split}_sharpe"] = m["sharpe"]
                row[f"{split}_maxdd"] = m["maxdd"]
                row[f"{split}_cagr"] = m["cagr"]

            def split_ok(s: str) -> bool:
                return (row[f"{s}_pf"] is not None
                        and row[f"{s}_pf"] >= PASS_PF
                        and row[f"{s}_sharpe"] >= PASS_SHARPE
                        and row[f"{s}_maxdd"] >= PASS_MAXDD)

            row["all3_pass"] = all(split_ok(s) for s in SPLITS)
            row["oos_pass"] = split_ok("OOS")
            all_rows.append(row)

            flag = "ALL3" if row["all3_pass"] else ("oos-only" if row["oos_pass"] else "fail")
            print(f"  compress={compress_atr} ext={ext_atr}: "
                  f"design PF={row['design_pf']} val PF={row['validation_pf']} "
                  f"OOS PF={row['OOS_pf']}  OOS Sharpe={row['OOS_sharpe']} "
                  f"OOS maxDD={row['OOS_maxdd']}% trades={row['OOS_n']} [{flag}]")

    df_out = pd.DataFrame(all_rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(OUT, index=False)

    print("\n=== BEST PARAMETERS PER PAIR (chosen on design+validation, never OOS) ===")
    for sym in PAIRS:
        sub = df_out[df_out["symbol"] == sym].copy()
        sub = sub[sub["design_pf"].notna() & sub["validation_pf"].notna()]
        if sub.empty:
            print(f"  {sym}: no data")
            continue
        sub["worst_dv"] = sub[["design_pf", "validation_pf"]].min(axis=1)
        best = sub.sort_values("worst_dv", ascending=False).iloc[0]
        print(f"  {sym}: compress={best['compress_atr']} ext={best['ext_atr']} "
              f"design PF={best['design_pf']} val PF={best['validation_pf']} "
              f"-> OOS PF={best['OOS_pf']} Sharpe={best['OOS_sharpe']} "
              f"maxDD={best['OOS_maxdd']}% trades={best['OOS_n']} "
              f"all3={best['all3_pass']}")

        #--- calendar proof for the selected config (user reporting rule)
        try:
            from period_returns import publish_results  # noqa: PLC0415

            df_b = load_csv(sym)  # the outer loop's df is the LAST symbol - do not reuse it
            pos_b = hyp_compression_rubberband(
                df_b, compress_atr=float(best["compress_atr"]),
                ext_atr=float(best["ext_atr"]))
            rets_b, _, _ = backtest_positions(df_b, pos_b, sym)
            publish_results(f"ma_compression_{sym}", rets_b)
        except Exception as exc:  # pragma: no cover - reporting only
            print(f"  {sym}: publish_results skipped ({exc})")

    n_all3 = int(df_out["all3_pass"].sum())
    n_oos = int(df_out["oos_pass"].sum())
    print(f"\nClears PF/Sharpe/maxDD in ALL THREE splits: {n_all3} / {len(df_out)}")
    print(f"OOS-only passes (NOT a result, never select on OOS): {n_oos} / {len(df_out)}")
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
