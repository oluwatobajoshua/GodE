#!/usr/bin/env python3
r"""3-Way Meta-Strategy: Carry + RSI Band + Compression at monthly level.

Tests blending carry_mom_filter (D1), RSI band (H1 per pair), and
compression rubberband (H1 per pair) with dynamic allocation based on
recent trailing Sharpe.

Run:
    .\.venv\Scripts\python.exe research/three_way_meta.py
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_core import simulate_stops

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY"]
BARS_PER_YEAR_H1 = 24 * 252

# Best compression params from sweep (OOS pass, high Sharpe)
COMPRESS_PARAMS = {
    "EURUSD": {"compress_atr": 0.5, "ext_atr": 2.0},
    "GBPUSD": {"compress_atr": 1.0, "ext_atr": 2.0},
    "USDJPY": {"compress_atr": 0.7, "ext_atr": 2.0},
}


def load_csv(symbol: str, tf: str = "H1") -> pd.DataFrame:
    path = DATA / f"{symbol}_{tf}.csv"
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)
    return df


def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def wilder_atr(df, period=14):
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = np.maximum(high - low, np.maximum(abs(high - prev_close), abs(low - prev_close)))
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def hyp_compression_rubberband(df, compress_atr=0.5, ext_atr=2.0,
                                ema_fast=8, ema_mid=21, ema_slow=50, atr_period=14):
    e8 = ema(df["close"], ema_fast)
    e21 = ema(df["close"], ema_mid)
    e50 = ema(df["close"], ema_slow)
    atr = wilder_atr(df, atr_period)
    close = df["close"]
    n = len(df)
    pos = pd.Series(0, index=df.index)
    state = 0
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
        elif state < 0:
            if c <= ma_center:
                state = 0
        else:
            if compressed:
                if c < ma_center - band:
                    state = 1
                elif c > ma_center + band:
                    state = -1
        pos.iloc[i] = state
    return pos


def backtest_compression(df, pos, symbol, comm_pts=7.0, slip_pts=1.0, floor_pts=1.0):
    """Delegate to the shared contract so the return series stays per-bar.

    This function used to build its own ``returns_arr`` and store
    ``(close[i] - entry_price)`` in it - the cumulative gain since entry. The
    same gain was then counted once per bar held, which is the bug retracted in
    docs/HANDOFF.md. ``simulate_stops`` marks to market bar by bar.
    """
    rets, _trades, _exit = simulate_stops(
        df, pos, symbol, comm_pts=comm_pts, slip_pts=slip_pts,
        floor_pts=floor_pts)
    return rets


def compression_monthly(symbol):
    df = load_csv(symbol)
    p = COMPRESS_PARAMS[symbol]
    pos = hyp_compression_rubberband(df, compress_atr=p["compress_atr"], ext_atr=p["ext_atr"])
    rets = backtest_compression(df, pos, symbol)
    m = rets.to_frame("ret")
    m["date"] = m.index.to_series().dt.to_period("M").dt.to_timestamp()
    monthly = m.groupby("date")["ret"].apply(lambda x: (1 + x).prod() - 1)
    monthly.name = symbol
    return monthly


def load_monthly_csv(path, ret_col="return_pct"):
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"]).dt.to_period("M").dt.to_timestamp()
    df = df.sort_values("date")
    df["ret"] = df[ret_col] / 100.0
    out = df.set_index("date")["ret"]
    out.name = path.stem
    return out


def metrics(returns):
    r = returns.dropna()
    if r.empty or r.std() == 0:
        return {"cagr": 0.0, "sharpe": 0.0, "maxdd": 0.0}
    eq = (1 + r).cumprod()
    years = max(len(r) / 12, 1e-6)
    cagr = eq.iloc[-1] ** (1 / years) - 1
    sharpe = r.mean() / r.std() * math.sqrt(12)
    peak = np.maximum.accumulate(eq)
    maxdd = (eq / peak - 1).min()
    return {"cagr": cagr * 100, "sharpe": sharpe, "maxdd": maxdd * 100}


def split_stats(returns):
    times = returns.index
    t0, t1 = times[0], times[-1]
    span = t1 - t0
    edge1 = t0 + span * 0.60
    edge2 = t0 + span * 0.80
    bounds = {"design": (t0, edge1), "validation": (edge1, edge2), "oos": (edge2, t1)}
    out = {}
    for sp in ("design", "validation", "oos"):
        a, b = bounds[sp]
        mask = (times >= a) & (times < b)
        out[sp] = metrics(returns[mask])
    return out


def main():
    # Load carry monthly
    carry = load_monthly_csv(RESULTS / "carry_mom_filter_monthly.csv")

    # Load RSI band monthly per pair
    rsi_monthly = {}
    for sym in PAIRS:
        p = RESULTS / f"rsi_band_net_{sym}_H1_monthly.csv"
        rsi_monthly[sym] = load_monthly_csv(p)

    # Compute compression monthly per pair
    comp_monthly = {}
    for sym in PAIRS:
        comp_monthly[sym] = compression_monthly(sym)

    # Find common date range (all must have data)
    all_idx = [carry.index]
    for sym in PAIRS:
        all_idx.append(rsi_monthly[sym].index)
        all_idx.append(comp_monthly[sym].index)
    for name, idx in [("carry", carry.index)] + [(f"rsi_{sym}", rsi_monthly[sym].index) for sym in PAIRS] + [(f"comp_{sym}", comp_monthly[sym].index) for sym in PAIRS]:
        print(f"  {name}: {len(idx)} months  {idx[0]} -> {idx[-1]}")
    common = all_idx[0]
    for idx in all_idx[1:]:
        common = common.intersection(idx)
    common = common.sort_values()
    print(f"Common months: {len(common)}  {common[0] if len(common) > 0 else 'NONE'} -> {common[-1] if len(common) > 0 else 'NONE'}")

    # Align all series
    c = carry.reindex(common)
    rsi_aligned = {sym: rsi_monthly[sym].reindex(common) for sym in PAIRS}
    comp_aligned = {sym: comp_monthly[sym].reindex(common) for sym in PAIRS}

    # Individual strategies
    print("\n=== INDIVIDUAL STRATEGIES (common period) ===")
    for name, ser in [("carry", c)] + [(f"rsi_{sym}", rsi_aligned[sym]) for sym in PAIRS] + [(f"comp_{sym}", comp_aligned[sym]) for sym in PAIRS]:
        st = split_stats(ser)
        print(f"  {name:12s} D/V/OOS={st['design']['cagr']:+.1f}/{st['validation']['cagr']:+.1f}/{st['oos']['cagr']:+.1f}%  "
              f"Sharpe={st['oos']['sharpe']:>5.2f}  DD={st['oos']['maxdd']:>6.1f}%")

    # Equal-weight blend of all 6 legs
    legs = [c] + [rsi_aligned[sym] for sym in PAIRS] + [comp_aligned[sym] for sym in PAIRS]
    legs_df = pd.DataFrame({f"leg{i}": l for i, l in enumerate(legs)})
    eq_weight = legs_df.mean(axis=1)
    st = split_stats(eq_weight)
    print(f"\n  EQUAL-WEIGHT (6 legs): D/V/OOS={st['design']['cagr']:+.1f}/{st['validation']['cagr']:+.1f}/{st['oos']['cagr']:+.1f}%  "
          f"Sharpe={st['oos']['sharpe']:>5.2f}  DD={st['oos']['maxdd']:>6.1f}%")

    # Carry + RSI band only (hedge blend, validated)
    hedge_legs = [c] + [rsi_aligned[sym] for sym in PAIRS]
    hedge_df = pd.DataFrame({f"leg{i}": l for i, l in enumerate(hedge_legs)})
    hedge_eq = hedge_df.mean(axis=1)
    st = split_stats(hedge_eq)
    print(f"  HEDGE BLEND (carry+RSI): D/V/OOS={st['design']['cagr']:+.1f}/{st['validation']['cagr']:+.1f}/{st['oos']['cagr']:+.1f}%  "
          f"Sharpe={st['oos']['sharpe']:>5.2f}  DD={st['oos']['maxdd']:>6.1f}%")

    # Dynamic allocation: weight by trailing 12m Sharpe, rebalanced monthly
    window = 12
    weight_dict = {}
    for i in range(len(legs)):
        ser = legs_df.iloc[:, i]
        roll_sharpe = ser.rolling(window).mean() / ser.rolling(window).std()
        weight_dict[i] = roll_sharpe.clip(lower=0)
    dyn_weights = pd.DataFrame(weight_dict, index=common)
    row_sums = dyn_weights.sum(axis=1)
    dyn_weights_norm = dyn_weights.div(row_sums, axis=0).fillna(1 / len(legs))
    dyn_ret = (legs_df * dyn_weights_norm).sum(axis=1)
    st = split_stats(dyn_ret)
    print(f"  DYNAMIC (trailing Sharpe): D/V/OOS={st['design']['cagr']:+.1f}/{st['validation']['cagr']:+.1f}/{st['oos']['cagr']:+.1f}%  "
          f"Sharpe={st['oos']['sharpe']:>5.2f}  DD={st['oos']['maxdd']:>6.1f}%")

    # Carry + Compression only (skip weak carry and weak RSI USDJPY)
    cc_legs = [comp_aligned[sym] for sym in PAIRS]
    cc_df = pd.DataFrame({f"leg{i}": l for i, l in enumerate(cc_legs)})
    cc_eq = cc_df.mean(axis=1)
    st = split_stats(cc_eq)
    print(f"  COMPRESSION ONLY (3 pairs): D/V/OOS={st['design']['cagr']:+.1f}/{st['validation']['cagr']:+.1f}/{st['oos']['cagr']:+.1f}%  "
          f"Sharpe={st['oos']['sharpe']:>5.2f}  DD={st['oos']['maxdd']:>6.1f}%")

    # Carry + RSI EURUSD + Compression (skip weak legs)
    strong_legs = [c, rsi_aligned["EURUSD"], comp_aligned["EURUSD"], comp_aligned["GBPUSD"], comp_aligned["USDJPY"]]
    strong_df = pd.DataFrame({f"leg{i}": l for i, l in enumerate(strong_legs)})
    strong_eq = strong_df.mean(axis=1)
    st = split_stats(strong_eq)
    print(f"  STRONG LEGS (carry+RSI_EURUSD+comp_all): D/V/OOS={st['design']['cagr']:+.1f}/{st['validation']['cagr']:+.1f}/{st['oos']['cagr']:+.1f}%  "
          f"Sharpe={st['oos']['sharpe']:>5.2f}  DD={st['oos']['maxdd']:>6.1f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
