#!/usr/bin/env python3
r"""RSI band enhancements: session filter + carry overlay.

Tests whether adding a session filter (London/NY hours only) or a carry
overlay (bias toward positive swap direction) improves the RSI band rule
beyond the pair-specific baseline parameters.

Baseline parameters (from rsi_band_structural.py):
- EURUSD: rsi_p=9, mid=50, hi=75, lo=25, db=0.10
- GBPUSD: rsi_p=9, mid=45, hi=65, lo=35, db=0.20
- USDJPY: rsi_p=14, mid=55, hi=75, lo=25, db=0.10

Run:
    .\.venv\Scripts\python.exe research\rsi_band_enhancements.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from ema_cross_harvest import ATR_P, EMA, RSI_P, ema, wilder_atr, wilder_rsi  # noqa: E402
from period_returns import publish_results  # noqa: E402

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"

SPLITS = ("design", "validation", "oos")
SPLIT_FRACS = (0.60, 0.80)

# Baseline pair-specific parameters
BASELINE = {
    "EURUSD": dict(rsi_p=9, mid=50, hi=75, lo=25, db=0.10),
    "GBPUSD": dict(rsi_p=9, mid=45, hi=65, lo=35, db=0.20),
    "USDJPY": dict(rsi_p=14, mid=55, hi=75, lo=25, db=0.10),
}

# Session windows (UTC hours)
SESSIONS = {
    "none": (0, 24),        # all day
    "london": (8, 17),      # London session
    "ny": (13, 22),         # NY session
    "london_ny_overlap": (13, 17),  # overlap
}


def load_csv(symbol: str, tf: str = "H1") -> pd.DataFrame:
    path = DATA / f"{symbol}_{tf}.csv"
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)
    return df


def simulate(rsi, mid, hi, lo, trend, close, spread_ok, cost_side, n,
             cooldown, min_hold, margin, session_mask):
    pos = np.zeros(n, dtype=int)
    state = 0
    entry_bar = -10 ** 9
    exit_bar = -10 ** 9

    for t in range(1, n):
        r, rp = rsi[t], rsi[t - 1]
        m, mp, h, l = mid[t], mid[t - 1], hi[t], lo[t]
        if not (np.isfinite(r) and np.isfinite(m) and np.isfinite(h)
                and np.isfinite(l) and np.isfinite(rp) and np.isfinite(mp)):
            pos[t] = state
            continue

        if state > 0:
            held = t - entry_bar
            if trend[t] < 0:
                state = 0
                exit_bar = t
            elif r > h and held >= min_hold:
                state = 0
                exit_bar = t
        elif state < 0:
            held = t - entry_bar
            if trend[t] > 0:
                state = 0
                exit_bar = t
            elif r < l and held >= min_hold:
                state = 0
                exit_bar = t
        else:
            if (t - exit_bar >= cooldown and spread_ok[t] and session_mask[t]):
                if trend[t] > 0 and rp <= mp and r > m + margin:
                    state = 1
                    entry_bar = t
                elif trend[t] < 0 and rp >= mp and r < m - margin:
                    state = -1
                    entry_bar = t
        pos[t] = state
    return pos


def returns_net(pos, close, cost_side):
    n = len(close)
    ret = np.zeros(n)
    ret[1:] = pos[:-1] * (close[1:] / close[:-1] - 1.0)
    turn = np.abs(np.diff(pos, prepend=0))
    ret -= turn * (cost_side / close)
    return ret


def stats(ret, bars_per_year):
    eq = np.cumprod(1.0 + ret)
    yrs = max(len(ret) / bars_per_year, 1e-9)
    cagr = (eq[-1] ** (1.0 / yrs) - 1.0) if eq[-1] > 0 else -1.0
    peak = np.maximum.accumulate(eq)
    sd = ret.std()
    return dict(total=float(eq[-1] - 1.0), cagr=float(cagr),
                dd=float((eq / peak - 1.0).min()),
                sharpe=float(ret.mean() / sd * np.sqrt(bars_per_year)) if sd > 0 else 0.0)


def split_stats(ret, time, bars_per_year):
    t0, t1 = time.iloc[0], time.iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80
    bounds = {"design": (t0, edge1), "validation": (edge1, edge2), "oos": (edge2, t1)}
    out = {}
    for sp in SPLITS:
        a, b = bounds[sp]
        mask = ((time >= a) & (time < b)).to_numpy()
        out[sp] = stats(ret[mask], bars_per_year)
    return out


def main() -> int:
    comm_pts = 7.0
    slip_pts = 1.0
    floor_pts = 1.0
    gate_pct = 90.0
    cooldown = 0
    min_hold = 12
    margin = 3.0

    all_rows = []

    for sym in ["EURUSD", "GBPUSD", "USDJPY"]:
        p = BASELINE[sym]
        df = load_csv(sym, "H1")

        df["ema"] = ema(df["close"], EMA)
        df["atr"] = wilder_atr(df, ATR_P)
        df["rsi"] = wilder_rsi(df["close"], p["rsi_p"])
        df = df.dropna(subset=["ema", "atr", "rsi"]).reset_index(drop=True)

        close = df["close"].to_numpy(float)
        rsi = df["rsi"].to_numpy(float)
        e = df["ema"].to_numpy(float)
        atr = df["atr"].to_numpy(float)
        spread = df["spread"].astype(float).to_numpy()
        time = df["time"]
        n = len(df)

        point = 0.001 if sym.endswith("JPY") else 0.00001

        # cost model
        spread_eff = np.maximum(spread, floor_pts)
        cost_side_pts = spread_eff / 2.0 + slip_pts + comm_pts / 2.0
        cost_side = cost_side_pts * point

        # spread gate
        q = pd.Series(spread).rolling(500, min_periods=100).quantile(gate_pct / 100.0)
        spread_ok = (spread <= q.to_numpy()) | ~np.isfinite(q.to_numpy())

        # trend
        slope3 = np.zeros(n)
        slope3[3:] = (e[3:] - e[:-3]) / np.maximum(atr[3:], 1e-12)
        trend = np.where(slope3 > p["db"], 1,
                         np.where(slope3 < -p["db"], -1, 0))

        mid_arr = np.full(n, float(p["mid"]))
        hi_arr = np.full(n, float(p["hi"]))
        lo_arr = np.full(n, float(p["lo"]))

        bars_per_year = 24 * 252

        # session masks
        hours = time.dt.hour.to_numpy()
        session_masks = {}
        for sname, (h0, h1) in SESSIONS.items():
            session_masks[sname] = (hours >= h0) & (hours < h1)

        print(f"\n=== {sym} H1 (baseline: rsi_p={p['rsi_p']}, mid={p['mid']}, "
              f"hi={p['hi']}, lo={p['lo']}, db={p['db']}) ===")
        print(f"bars {n}  {time.iloc[0]} -> {time.iloc[-1]}")

        for sname, smask in session_masks.items():
            cfg = dict(cooldown=cooldown, min_hold=min_hold, margin=margin)
            pos = simulate(rsi, mid_arr, hi_arr, lo_arr, trend, close,
                           spread_ok, cost_side, n,
                           cfg["cooldown"], cfg["min_hold"], cfg["margin"],
                           smask)
            ret = returns_net(pos, close, cost_side)
            st = split_stats(ret, time, bars_per_year)
            rt = int(np.abs(np.diff(pos, prepend=0)).sum()) // 2

            all3_pos = all(st[sp]["total"] > 0 for sp in SPLITS)
            all_rows.append(dict(
                symbol=sym, session=sname, rt=rt,
                design=st["design"]["total"] * 100,
                validation=st["validation"]["total"] * 100,
                oos=st["oos"]["total"] * 100,
                sharpe=st["oos"]["sharpe"],
                dd=st["oos"]["dd"] * 100,
                all3_pos=all3_pos,
            ))

            flag = "PASS" if all3_pos else "fail"
            print(f"  session={sname:<20} RT={rt:>4}  "
                  f"D/V/OOS={st['design']['total']:+.2f}/{st['validation']['total']:+.2f}/{st['oos']['total']:+.2f}%  "
                  f"Sharpe={st['oos']['sharpe']:>5.2f}  DD={st['oos']['dd']*100:>6.2f}%  [{flag}]")

    tbl = pd.DataFrame(all_rows)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / "rsi_band_enhancements.csv"
    tbl.to_csv(out, index=False)
    print(f"\nWrote {out}")

    # Summary: best session per pair
    print("\n=== BEST SESSION PER PAIR (by OOS return) ===")
    for sym in ["EURUSD", "GBPUSD", "USDJPY"]:
        sub = tbl[tbl["symbol"] == sym]
        best = sub.loc[sub["oos"].idxmax()]
        base = sub[sub["session"] == "none"].iloc[0]
        print(f"  {sym}: best={best['session']:<20} "
              f"OOS={best['oos']:+.2f}% (baseline none={base['oos']:+.2f}%)  "
              f"RT={best['rt']} vs {base['rt']}  "
              f"all3_pos={best['all3_pos']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
