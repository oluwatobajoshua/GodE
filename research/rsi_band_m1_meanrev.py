#!/usr/bin/env python3
r"""M1 RSI band research — testing mean reversion from template RSI levels 20/80.

Tests what the fxmopol.tpl actually shows on chart:
  - Buy oversold: RSI crosses back UP through lo (20)
  - Sell overbought: RSI crosses back DOWN through hi (80)
  - Exit: RSI crosses back through mid (50)
  - Filter: EMA21 slope (deadband) + optional EMA21/EMA50 alignment

Run:
    .\.venv\Scripts\python.exe research\rsi_band_m1_meanrev.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from ema_cross_harvest import ema, wilder_atr, wilder_rsi

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY"]

# Template anchor: fxmopol.tpl -> EMA21/50, RSI9, levels 20/50/80
TEMPLATE = dict(ema_fast=21, rsi_period=9, rsi_lo=20, rsi_mid=50, rsi_hi=80)


def load_csv(symbol: str, tf: str = "M1") -> pd.DataFrame:
    path = DATA / f"{symbol}_{tf}.csv"
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)
    return df


def stats(ret):
    n = len(ret)
    if n == 0 or ret.std() == 0:
        return dict(total=0.0, cagr=0.0, dd=0.0, sharpe=0.0)
    eq = np.cumprod(1.0 + ret)
    yrs = max(n / (24 * 60 * 252), 1e-9)
    cagr = (eq[-1] ** (1.0 / yrs) - 1.0) if eq[-1] > 0 else -1.0
    peak = np.maximum.accumulate(eq)
    sd = ret.std()
    return dict(
        total=float(eq[-1] - 1.0),
        cagr=float(cagr),
        dd=float((eq / peak - 1.0).min()),
        sharpe=float(ret.mean() / sd * np.sqrt(24 * 60 * 252)),
    )


def simulate_meanrev(df, close, atr, rsi, trend, spread_ok, cost_side,
                     lo, hi, mid, min_hold, margin, use_aligned):
    n = len(df)
    pos = np.zeros(n, dtype=int)
    state = 0
    entry_bar = -10 ** 9
    exit_bar = -10 ** 9

    for t in range(1, n):
        r, rp = rsi[t], rsi[t - 1]
        tr = trend[t] if not use_aligned else trend[t]

        if state > 0:  # long
            held = t - entry_bar
            # exit: slope flip down OR RSI crosses back above mid
            if tr < 0:
                state = 0
                exit_bar = t
            elif held >= min_hold and rp >= mid and r < mid:
                state = 0
                exit_bar = t
            elif held >= min_hold and rp <= hi and r > hi:
                state = 0
                exit_bar = t

        elif state < 0:  # short
            held = t - entry_bar
            if tr > 0:
                state = 0
                exit_bar = t
            elif held >= min_hold and rp <= mid and r > mid:
                state = 0
                exit_bar = t
            elif held >= min_hold and rp >= lo and r < lo:
                state = 0
                exit_bar = t

        else:  # flat
            if spread_ok[t]:
                if use_aligned:
                    # trend filter with EMA alignment
                    if tr > 0 and rp <= lo + margin and r > lo + margin:
                        state = 1; entry_bar = t
                    elif tr < 0 and rp >= hi - margin and r < hi - margin:
                        state = -1; entry_bar = t
                else:
                    # no alignment filter, just slope
                    if tr > 0 and rp <= lo + margin and r > lo + margin:
                        state = 1; entry_bar = t
                    elif tr < 0 and rp >= hi - margin and r < hi - margin:
                        state = -1; entry_bar = t

        pos[t] = state

    ret = np.zeros(n)
    ret[1:] = pos[:-1] * (close[1:] / close[:-1] - 1.0)
    turn = np.abs(np.diff(pos, prepend=0))
    ret -= turn * (cost_side / close)
    return ret, pos


def main() -> int:
    all_rows = []
    for sym in SYMBOLS:
        df = load_csv(sym, "M1")
        n = len(df)
        close = df["close"].to_numpy(float)
        atr = wilder_atr(df, 14).to_numpy(float)
        rsi = wilder_rsi(df["close"], TEMPLATE["rsi_period"]).to_numpy(float)
        spread = df["spread"].astype(float).to_numpy()
        time = df["time"]

        # cost model
        point = 0.001 if sym.endswith("JPY") else 0.00001
        spread_eff = np.maximum(spread, 1.0)
        cost_side_pts = spread_eff / 2.0 + 1.0 + 7.0 / 2.0
        cost_side = cost_side_pts * point

        # spread gate p90
        q = pd.Series(spread).rolling(500, min_periods=100).quantile(0.90)
        spread_ok_base = (spread <= q.to_numpy()) | ~np.isfinite(q.to_numpy())

        # EMA trend
        e21 = ema(df["close"], TEMPLATE["ema_fast"]).to_numpy(float)
        e50 = ema(df["close"], 50).to_numpy(float)
        slope3 = np.zeros(n)
        slope3[3:] = (e21[3:] - e21[:-3]) / np.maximum(atr[3:], 1e-12)

        lo, hi, mid = TEMPLATE["rsi_lo"], TEMPLATE["rsi_hi"], TEMPLATE["rsi_mid"]

        rows = []
        for db in [0.0, 0.05, 0.10, 0.20]:
            trend = np.where(slope3 > db, 1, np.where(slope3 < -db, -1, 0))
            for min_hold in [3, 6, 12]:
                for margin in [0.0, 1.0, 3.0]:
                    for use_aligned in [False, True]:
                        t = trend.copy()
                        if use_aligned:
                            for tt in range(n):
                                if trend[tt] > 0 and e21[tt] <= e50[tt]:
                                    t[tt] = 0
                                if trend[tt] < 0 and e21[tt] >= e50[tt]:
                                    t[tt] = 0
                        ret, pos = simulate_meanrev(
                            df, close, atr, rsi, t, spread_ok_base, cost_side,
                            lo, hi, mid, min_hold, margin, use_aligned)
                        rt = int(np.abs(np.diff(pos, prepend=0)).sum()) // 2
                        st = stats(ret)
                        rows.append(dict(
                            sym=sym, db=db, min_hold=min_hold, margin=margin,
                            aligned=use_aligned, rt=rt,
                            net_total=st["total"] * 100,
                            net_sharpe=st["sharpe"],
                            net_dd=st["dd"] * 100,
                        ))

        tbl = pd.DataFrame(rows)
        best = tbl.loc[tbl["net_total"].idxmax()]
        print(f"\n=== {sym} M1 (mean reversion from RSI {lo}/{mid}/{hi}) ===")
        print(f"configs: {len(tbl)}, best total: {best['net_total']:+.2f}% "
              f"(db={best['db']} mh={best['min_hold']} margin={best['margin']} "
              f"aligned={best['aligned']} RT={best['rt']} Sharpe={best['net_sharpe']:.2f} DD={best['net_dd']:.2f}%)")
        # Top 5
        g = tbl.sort_values("net_total", ascending=False).head(5)
        for _, r in g.iterrows():
            print(f"  db={r.db} mh={r.min_hold} margin={r.margin} aligned={r.aligned} "
                  f"RT={r.rt} total={r.net_total:+.2f}% Sharpe={r.net_sharpe:.2f} DD={r.net_dd:.2f}%")
        all_rows.append(tbl)

    out = pd.concat(all_rows, ignore_index=True)
    out_path = RESULTS / "rsi_band_m1_meanrev.csv"
    out.to_csv(out_path, index=False)
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
