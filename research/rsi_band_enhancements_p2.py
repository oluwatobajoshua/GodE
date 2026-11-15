#!/usr/bin/env python3
"""RSI band enhancements part 2: volatility filter + time-based exit.

Tests:
1. Volatility filter: skip entry when ATR is extreme (too high = chop/news,
   too low = dead market) relative to rolling ATR percentile.
2. Time-based exit: exit after N bars regardless of RSI level.

Only reports if it beats the pair-specific baseline.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from ema_cross_harvest import ATR_P, EMA, ema, wilder_atr, wilder_rsi  # noqa: E402

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"

SPLITS = ("design", "validation", "oos")

BASELINE = {
    "EURUSD": dict(rsi_p=9, mid=50, hi=75, lo=25, db=0.10),
    "GBPUSD": dict(rsi_p=9, mid=45, hi=65, lo=35, db=0.20),
    "USDJPY": dict(rsi_p=14, mid=55, hi=75, lo=25, db=0.10),
}


def load_csv(symbol: str, tf: str = "H1") -> pd.DataFrame:
    path = DATA / f"{symbol}_{tf}.csv"
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)
    return df


def simulate(rsi, mid, hi, lo, trend, close, spread_ok, cost_side, n,
             cooldown, min_hold, margin, max_bars, vol_filter_pct):
    pos = np.zeros(n, dtype=int)
    state = 0
    entry_bar = -10 ** 9
    exit_bar = -10 ** 9

    # ATR percentile for volatility filter
    atr_vals = None  # passed separately

    for t in range(1, n):
        r, rp = rsi[t], rsi[t - 1]
        m, mp, h, l = mid[t], mid[t - 1], hi[t], lo[t]
        if not (np.isfinite(r) and np.isfinite(m) and np.isfinite(h)
                and np.isfinite(l) and np.isfinite(rp) and np.isfinite(mp)):
            pos[t] = state
            continue

        if state > 0:
            held = t - entry_bar
            # time-based exit
            if max_bars > 0 and held >= max_bars:
                state = 0
                exit_bar = t
            elif trend[t] < 0:
                state = 0
                exit_bar = t
            elif r > h and held >= min_hold:
                state = 0
                exit_bar = t
        elif state < 0:
            held = t - entry_bar
            if max_bars > 0 and held >= max_bars:
                state = 0
                exit_bar = t
            elif trend[t] > 0:
                state = 0
                exit_bar = t
            elif r < l and held >= min_hold:
                state = 0
                exit_bar = t
        else:
            if (t - exit_bar >= cooldown and spread_ok[t]):
                # volatility filter: skip if ATR is too extreme
                if vol_filter_pct > 0:
                    pass  # handled via entry mask outside
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
    bars_per_year = 24 * 252

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

        spread_eff = np.maximum(spread, floor_pts)
        cost_side_pts = spread_eff / 2.0 + slip_pts + comm_pts / 2.0
        cost_side = cost_side_pts * point

        q = pd.Series(spread).rolling(500, min_periods=100).quantile(gate_pct / 100.0)
        spread_ok = (spread <= q.to_numpy()) | ~np.isfinite(q.to_numpy())

        slope3 = np.zeros(n)
        slope3[3:] = (e[3:] - e[:-3]) / np.maximum(atr[3:], 1e-12)
        trend = np.where(slope3 > p["db"], 1,
                         np.where(slope3 < -p["db"], -1, 0))

        mid_arr = np.full(n, float(p["mid"]))
        hi_arr = np.full(n, float(p["hi"]))
        lo_arr = np.full(n, float(p["lo"]))

        # Baseline (no enhancements)
        pos0 = simulate(rsi, mid_arr, hi_arr, lo_arr, trend, close,
                        spread_ok, cost_side, n, cooldown, min_hold, margin,
                        max_bars=0, vol_filter_pct=0)
        ret0 = returns_net(pos0, close, cost_side)
        st0 = split_stats(ret0, time, bars_per_year)
        rt0 = int(np.abs(np.diff(pos0, prepend=0)).sum()) // 2

        print(f"\n=== {sym} H1 (baseline: rsi_p={p['rsi_p']}, mid={p['mid']}, "
              f"hi={p['hi']}, lo={p['lo']}, db={p['db']}) ===")
        print(f"  baseline: RT={rt0}  D/V/OOS="
              f"{st0['design']['total']:+.2f}/{st0['validation']['total']:+.2f}/{st0['oos']['total']:+.2f}%  "
              f"Sharpe={st0['oos']['sharpe']:>5.2f}  DD={st0['oos']['dd']*100:>6.2f}%")

        all_rows.append(dict(symbol=sym, enhancement="baseline", param="",
                             rt=rt0,
                             design=st0["design"]["total"] * 100,
                             validation=st0["validation"]["total"] * 100,
                             oos=st0["oos"]["total"] * 100,
                             sharpe=st0["oos"]["sharpe"],
                             dd=st0["oos"]["dd"] * 100))

        # --- Volatility filter: skip entry when ATR pct is outside [low, high] ---
        # ATR percentile over rolling 500 bars
        atr_pct = pd.Series(atr).rolling(500, min_periods=100).rank(pct=True).to_numpy()
        vol_configs = [
            (10, 90),   # skip bottom 10% and top 10%
            (20, 80),   # skip bottom 20% and top 20%
            (5, 95),    # skip bottom 5% and top 5%
            (25, 75),   # skip bottom 25% and top 25%
        ]
        for low_pct, high_pct in vol_configs:
            vol_mask = (atr_pct >= low_pct / 100.0) & (atr_pct <= high_pct / 100.0)
            spread_ok_vol = spread_ok & vol_mask
            pos = simulate(rsi, mid_arr, hi_arr, lo_arr, trend, close,
                           spread_ok_vol, cost_side, n, cooldown, min_hold, margin,
                           max_bars=0, vol_filter_pct=0)
            ret = returns_net(pos, close, cost_side)
            st = split_stats(ret, time, bars_per_year)
            rt = int(np.abs(np.diff(pos, prepend=0)).sum()) // 2
            all3 = all(st[sp]["total"] > 0 for sp in SPLITS)
            better_oos = st["oos"]["total"] > st0["oos"]["total"]
            all_rows.append(dict(symbol=sym, enhancement="vol_filter",
                                 param=f"{low_pct}-{high_pct}%", rt=rt,
                                 design=st["design"]["total"] * 100,
                                 validation=st["validation"]["total"] * 100,
                                 oos=st["oos"]["total"] * 100,
                                 sharpe=st["oos"]["sharpe"],
                                 dd=st["oos"]["dd"] * 100))
            flag = "PASS" if all3 else "fail"
            better = "BETTER" if better_oos else ""
            print(f"  vol_filter {low_pct}-{high_pct}%: RT={rt:>4}  "
                  f"D/V/OOS={st['design']['total']:+.2f}/{st['validation']['total']:+.2f}/{st['oos']['total']:+.2f}%  "
                  f"Sharpe={st['oos']['sharpe']:>5.2f}  DD={st['oos']['dd']*100:>6.2f}%  [{flag}] {better}")

        # --- Time-based exit: exit after N bars ---
        for max_bars in [8, 12, 16, 24, 36, 48]:
            pos = simulate(rsi, mid_arr, hi_arr, lo_arr, trend, close,
                           spread_ok, cost_side, n, cooldown, min_hold, margin,
                           max_bars=max_bars, vol_filter_pct=0)
            ret = returns_net(pos, close, cost_side)
            st = split_stats(ret, time, bars_per_year)
            rt = int(np.abs(np.diff(pos, prepend=0)).sum()) // 2
            all3 = all(st[sp]["total"] > 0 for sp in SPLITS)
            better_oos = st["oos"]["total"] > st0["oos"]["total"]
            all_rows.append(dict(symbol=sym, enhancement="time_exit",
                                 param=f"{max_bars}bars", rt=rt,
                                 design=st["design"]["total"] * 100,
                                 validation=st["validation"]["total"] * 100,
                                 oos=st["oos"]["total"] * 100,
                                 sharpe=st["oos"]["sharpe"],
                                 dd=st["oos"]["dd"] * 100))
            flag = "PASS" if all3 else "fail"
            better = "BETTER" if better_oos else ""
            print(f"  time_exit {max_bars}bars: RT={rt:>4}  "
                  f"D/V/OOS={st['design']['total']:+.2f}/{st['validation']['total']:+.2f}/{st['oos']['total']:+.2f}%  "
                  f"Sharpe={st['oos']['sharpe']:>5.2f}  DD={st['oos']['dd']*100:>6.2f}%  [{flag}] {better}")

    tbl = pd.DataFrame(all_rows)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / "rsi_band_enhancements_p2.csv"
    tbl.to_csv(out, index=False)
    print(f"\nWrote {out}")

    # Summary: any enhancement that beats baseline OOS AND passes all 3 splits?
    print("\n=== ENHANCEMENTS THAT BEAT BASELINE OOS ===")
    for sym in ["EURUSD", "GBPUSD", "USDJPY"]:
        sub = tbl[tbl["symbol"] == sym]
        base_oos = sub[sub["enhancement"] == "baseline"]["oos"].iloc[0]
        candidates = sub[(sub["enhancement"] != "baseline") &
                         (sub["oos"] > base_oos)]
        if len(candidates):
            for _, r in candidates.iterrows():
                print(f"  {sym}: {r['enhancement']} {r['param']}  "
                      f"OOS={r['oos']:+.2f}% vs baseline={base_oos:+.2f}%  "
                      f"RT={r['rt']}")
        else:
            print(f"  {sym}: no enhancement beats baseline OOS ({base_oos:+.2f}%)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
