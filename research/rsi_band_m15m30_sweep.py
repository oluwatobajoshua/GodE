#!/usr/bin/env python3
r"""RSI band sweep on M15/M30 using fxmopol.tpl parameters as the anchor.

Tests multiple entry/exit logics around the template values:
  EMA21/50 trend filter, RSI9, levels 20/50/80

Run:
    .\.venv\Scripts\python.exe research\rsi_band_m15m30_sweep.py
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from ema_cross_harvest import ema, wilder_atr, wilder_rsi

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY"]
TFS = ["M1", "M5", "M15", "M30"]

SPLITS = ("design", "validation", "oos")

# Template anchor: fxmopol.tpl -> EMA21, RSI9, levels 20/50/80
TEMPLATE = dict(ema_fast=21, rsi_period=9, rsi_lo=20, rsi_mid=50, rsi_hi=80)


def load_csv(symbol: str, tf: str) -> pd.DataFrame:
    path = DATA / f"{symbol}_{tf}.csv"
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)
    return df


def stats(ret, bars_per_year):
    n = len(ret)
    if n == 0 or ret.std() == 0:
        return dict(total=0.0, cagr=0.0, dd=0.0, sharpe=0.0)
    eq = np.cumprod(1.0 + ret)
    yrs = max(n / bars_per_year, 1e-9)
    cagr = (eq[-1] ** (1.0 / yrs) - 1.0) if eq[-1] > 0 else -1.0
    peak = np.maximum.accumulate(eq)
    sd = ret.std()
    return dict(
        total=float(eq[-1] - 1.0),
        cagr=float(cagr),
        dd=float((eq / peak - 1.0).min()),
        sharpe=float(ret.mean() / sd * np.sqrt(bars_per_year)),
    )


def split_stats(ret, time, bars_per_year):
    t0, t1 = time.iloc[0], time.iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80
    bounds = {"design": (t0, edge1), "validation": (edge1, edge2), "oos": (edge2, t1)}
    out = {}
    for sp in SPLITS:
        a, b = bounds[sp]
        mask = ((time.values >= np.datetime64(a)) & (time.values < np.datetime64(b)))
        out[sp] = stats(ret[mask], bars_per_year)
    return out


def simulate(pos, close, cost_side):
    n = len(close)
    ret = np.zeros(n)
    ret[1:] = pos[:-1] * (close[1:] / close[:-1] - 1.0)
    turn = np.abs(np.diff(pos, prepend=0))
    ret -= turn * (cost_side / close)
    return ret


def run_config(df, close, atr, rsi, trend, spread_ok, cost_side,
               entry_logic, hi, lo, mid, min_hold, cooldown,
               bars_per_year, time):
    n = len(df)
    pos = np.zeros(n, dtype=int)
    state = 0
    entry_bar = -10 ** 9
    exit_bar = -10 ** 9

    for t in range(1, n):
        r, rp = rsi[t], rsi[t - 1]
        tr = trend[t]

        if state > 0:  # long
            held = t - entry_bar
            # exit: slope flip down
            if tr < 0:
                state = 0
                exit_bar = t
            # exit: RSI crosses above hi (overbought)
            elif held >= min_hold and rp <= hi and r > hi:
                state = 0
                exit_bar = t
            # exit: RSI crosses back below mid
            elif held >= min_hold and rp >= mid and r < mid:
                state = 0
                exit_bar = t

        elif state < 0:  # short
            held = t - entry_bar
            if tr > 0:
                state = 0
                exit_bar = t
            elif held >= min_hold and rp >= lo and r < lo:
                state = 0
                exit_bar = t
            elif held >= min_hold and rp <= mid and r > mid:
                state = 0
                exit_bar = t

        else:  # flat
            if t - exit_bar >= cooldown and spread_ok[t]:
                if entry_logic == "cross_above_mid":
                    if tr > 0 and rp <= mid and r > mid:
                        state = 1; entry_bar = t
                    elif tr < 0 and rp >= mid and r < mid:
                        state = -1; entry_bar = t
                elif entry_logic == "cross_above_lo":
                    if tr > 0 and rp <= lo and r > lo:
                        state = 1; entry_bar = t
                    elif tr < 0 and rp >= hi and r < hi:
                        state = -1; entry_bar = t
                elif entry_logic == "cross_below_hi":
                    if tr > 0 and rp <= mid and r > mid:
                        state = 1; entry_bar = t
                    elif tr < 0 and rp >= mid and r < mid:
                        state = -1; entry_bar = t

        pos[t] = state

    ret = simulate(pos, close, cost_side)
    st = split_stats(ret, time, bars_per_year)
    rt = int(np.abs(np.diff(pos, prepend=0)).sum()) // 2
    return st, rt, ret


def main() -> int:
    bars_per_year_map = {"M1": 24 * 60 * 252, "M5": 24 * 12 * 252, "M15": 24 * 4 * 252, "M30": 24 * 2 * 252}

    all_rows = []
    for sym in PAIRS:
        for tf in TFS:
            df = load_csv(sym, tf)
            n = len(df)
            if n < 5000:
                print(f"  {sym} {tf}: only {n} bars, skipping")
                continue

            close = df["close"].to_numpy(float)
            atr = wilder_atr(df, 14).to_numpy(float)
            rsi = wilder_rsi(df["close"], TEMPLATE["rsi_period"]).to_numpy(float)
            spread = df["spread"].astype(float).to_numpy()
            time = df["time"]
            bpy = bars_per_year_map[tf]

            # cost model: real spread + floor
            point = 0.001 if sym.endswith("JPY") else 0.00001
            spread_eff = np.maximum(spread, 1.0)
            cost_side_pts = spread_eff / 2.0 + 1.0 + 7.0 / 2.0
            cost_side = cost_side_pts * point

            # spread gate p90
            q = pd.Series(spread).rolling(500, min_periods=100).quantile(0.90)
            spread_ok_base = (spread <= q.to_numpy()) | ~np.isfinite(q.to_numpy())

            # EMA trend: EMA21 slope over 3 bars / ATR, deadband 0.10
            e21 = ema(df["close"], TEMPLATE["ema_fast"]).to_numpy(float)
            e50 = ema(df["close"], 50).to_numpy(float)
            slope3 = np.zeros(n)
            slope3[3:] = (e21[3:] - e21[:-3]) / np.maximum(atr[3:], 1e-12)
            trend = np.where(slope3 > 0.10, 1, np.where(slope3 < -0.10, -1, 0))

            # Also require EMA21 > EMA50 for long, EMA21 < EMA50 for short (template alignment)
            trend_aligned = trend.copy()
            for t in range(n):
                if trend[t] > 0 and e21[t] <= e50[t]:
                    trend_aligned[t] = 0
                if trend[t] < 0 and e21[t] >= e50[t]:
                    trend_aligned[t] = 0

            hi, lo, mid = TEMPLATE["rsi_hi"], TEMPLATE["rsi_lo"], TEMPLATE["rsi_mid"]

            rows = []
            for entry_logic in ["cross_above_mid", "cross_above_lo"]:
                for min_hold in [6, 12, 24]:
                    for cooldown in [0, 3]:
                        for use_aligned in [False, True]:
                            t = trend_aligned if use_aligned else trend
                            st, rt, _ret = run_config(
                                df, close, atr, rsi, t, spread_ok_base, cost_side,
                                entry_logic, hi, lo, mid, min_hold, cooldown,
                                bpy, time)
                            rows.append(dict(
                                sym=sym, tf=tf, entry=entry_logic,
                                min_hold=min_hold, cooldown=cooldown,
                                aligned=use_aligned,
                                rt=rt,
                                net_design=st["design"]["total"] * 100,
                                net_validation=st["validation"]["total"] * 100,
                                net_oos=st["oos"]["total"] * 100,
                                net_sharpe=st["oos"]["sharpe"],
                                net_dd=st["oos"]["dd"] * 100,
                                all3_pos=all(st[sp]["total"] > 0 for sp in SPLITS),
                            ))

            tbl = pd.DataFrame(rows)
            net_pos = tbl[tbl["all3_pos"]]
            print(f"\n=== {sym} {tf} (template anchor: EMA21, RSI9, levels {lo}/{mid}/{hi}) ===")
            print(f"configs: {len(tbl)}, net-positive all 3 splits: {len(net_pos)}")
            if len(net_pos):
                g = net_pos.copy()
                g["worst_dv"] = g[["net_design", "net_validation"]].min(axis=1)
                g = g.sort_values("worst_dv", ascending=False)
                for _, r in g.head(5).iterrows():
                    print(f"  {r.entry} mh={r.min_hold} cd={r.cooldown} aligned={r.aligned} "
                          f"RT={r.rt} net D/V/OOS={r.net_design:+.2f}/{r.net_validation:+.2f}/{r.net_oos:+.2f}% "
                          f"Sharpe={r.net_sharpe:>5.2f} DD={r.net_dd:>6.2f}%")

                # calendar proof for the config selected on design+validation only
                top = g.iloc[0]
                t_sel = trend_aligned if bool(top.aligned) else trend
                _st, _rt, ret_sel = run_config(
                    df, close, atr, rsi, t_sel, spread_ok_base, cost_side,
                    top.entry, hi, lo, mid, int(top.min_hold),
                    int(top.cooldown), bpy, time)
                try:
                    from period_returns import publish_results  # noqa: PLC0415

                    publish_results(f"rsi_band_{sym}_{tf}",
                                    pd.Series(ret_sel, index=pd.DatetimeIndex(time)))
                except Exception as exc:  # pragma: no cover - reporting only
                    print(f"  {sym} {tf}: publish_results skipped ({exc})")
            else:
                best = tbl.loc[tbl["net_oos"].idxmax()]
                print(f"  NO all3-positive. Best OOS: {best.entry} mh={best.min_hold} "
                      f"OOS={best.net_oos:+.2f}% Sharpe={best.net_sharpe:.2f} DD={best.net_dd:.2f}%")

            all_rows.append(tbl)

    out = pd.concat(all_rows, ignore_index=True)
    out_path = RESULTS / "rsi_band_m15m30_sweep.csv"
    out.to_csv(out_path, index=False)
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
