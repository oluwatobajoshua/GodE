#!/usr/bin/env python3
r"""Portfolio-level research: compression regime + cross-pair confluence.

Tests:
1. COMPRESSION REGIME FILTER: MA compression (EMA8/21/50 spread < compress_atr × ATR)
   per pair. Trade RSI band only when compressed (chop regime) or expanded (trend regime).
2. CROSS-PAIR RSI BAND CONFLUENCE: Trade only when >=2 of 3 pairs show same direction.

Run:
    .\.venv\Scripts\python.exe research/portfolio_regime.py
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

PAIRS = ["EURUSD", "GBPUSD", "USDJPY"]
SPLITS = ("design", "validation", "oos")

RSI_PARAMS = {
    "EURUSD": dict(rsi_p=9, mid=50, hi=75, lo=25, db=0.10),
    "GBPUSD": dict(rsi_p=9, mid=45, hi=65, lo=35, db=0.20),
    "USDJPY": dict(rsi_p=14, mid=55, hi=75, lo=25, db=0.10),
}

COMPRESS_ATR = {"EURUSD": 0.3, "GBPUSD": 1.0, "USDJPY": 1.0}
EXT_ATR = 2.5


def load_csv(symbol: str, tf: str = "H1") -> pd.DataFrame:
    path = DATA / f"{symbol}_{tf}.csv"
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)
    return df


def compute_indicators(df, symbol, rsi_p, compress_atr, ext_atr,
                       ema_fast=8, ema_mid=21, ema_slow=50, atr_period=14):
    """Add all indicators to df (no dropna). Returns arrays aligned to df."""
    e8 = ema(df["close"], ema_fast)
    e21 = ema(df["close"], ema_mid)
    e50 = ema(df["close"], ema_slow)
    atr = wilder_atr(df, atr_period)
    rsi = wilder_rsi(df["close"], rsi_p)

    ma_spread = np.maximum.reduce([
        np.abs(e8 - e21), np.abs(e21 - e50), np.abs(e8 - e50)
    ]) / np.maximum(atr, 1e-12)
    compressed = ma_spread < compress_atr
    ma_center = (e8 + e21 + e50) / 3.0
    band = ext_atr * atr

    return dict(
        close=df["close"].to_numpy(float),
        rsi=rsi.to_numpy(float),
        ema=e21.to_numpy(float),
        atr=atr.to_numpy(float),
        spread=df["spread"].astype(float).to_numpy(),
        time=df["time"],
        compressed=compressed.to_numpy(bool),
        ma_center=ma_center.to_numpy(float),
        band=band.to_numpy(float),
    )


def simulate_rsi_band(symbol, ind, rsi_p, mid, hi, lo, db, compressed_mask=None,
                      comm_pts=7.0, slip_pts=1.0, floor_pts=1.0,
                      gate_pct=90.0, cooldown=0, min_hold=12, margin=3.0,
                      max_bars=0):
    """Simulate RSI band on precomputed indicator arrays. No dropna."""
    close = ind["close"]
    rsi = ind["rsi"]
    e = ind["ema"]
    atr = ind["atr"]
    spread = ind["spread"]
    time = ind["time"]
    compressed = ind["compressed"]
    n = len(close)

    point = 0.001 if symbol.endswith("JPY") else 0.00001

    spread_eff = np.maximum(spread, floor_pts)
    cost_side_pts = spread_eff / 2.0 + slip_pts + comm_pts / 2.0
    cost_side = cost_side_pts * point

    q = pd.Series(spread).rolling(500, min_periods=100).quantile(gate_pct / 100.0)
    spread_ok = (spread <= q.to_numpy()) | ~np.isfinite(q.to_numpy())

    slope3 = np.zeros(n)
    slope3[3:] = (e[3:] - e[:-3]) / np.maximum(atr[3:], 1e-12)
    trend = np.where(slope3 > db, 1, np.where(slope3 < -db, -1, 0))

    mid_arr = np.full(n, float(mid))
    hi_arr = np.full(n, float(hi))
    lo_arr = np.full(n, float(lo))

    if compressed_mask is not None:
        mask = compressed_mask[-n:] if len(compressed_mask) >= n else compressed_mask
        spread_ok = spread_ok & mask

    pos = np.zeros(n, dtype=int)
    state = 0
    entry_bar = -10 ** 9
    exit_bar = -10 ** 9

    for t in range(1, n):
        r, rp = rsi[t], rsi[t - 1]
        m, mp, h, l = mid_arr[t], mid_arr[t - 1], hi_arr[t], lo_arr[t]
        if not (np.isfinite(r) and np.isfinite(m) and np.isfinite(h)
                and np.isfinite(l) and np.isfinite(rp) and np.isfinite(mp)):
            pos[t] = state
            continue

        if state > 0:
            held = t - entry_bar
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
            if t - exit_bar >= cooldown and spread_ok[t]:
                if trend[t] > 0 and rp <= mp and r > m + margin:
                    state = 1
                    entry_bar = t
                elif trend[t] < 0 and rp >= mp and r < m - margin:
                    state = -1
                    entry_bar = t
        pos[t] = state

    ret = np.zeros(n)
    ret[1:] = pos[:-1] * (close[1:] / close[:-1] - 1.0)
    turn = np.abs(np.diff(pos, prepend=0))
    ret -= turn * (cost_side / close)
    return ret, pos


def symbol_ends_jpy(ind):
    return False  # placeholder, will use symbol name directly


def stats(ret, bars_per_year):
    valid = ret[~np.isnan(ret)]
    if len(valid) == 0 or valid.std() == 0:
        return dict(total=0.0, cagr=0.0, dd=0.0, sharpe=0.0)
    eq = np.cumprod(1.0 + valid)
    yrs = max(len(valid) / bars_per_year, 1e-9)
    cagr = (eq[-1] ** (1.0 / yrs) - 1.0) if eq[-1] > 0 else -1.0
    peak = np.maximum.accumulate(eq)
    sd = valid.std()
    return dict(total=float(eq[-1] - 1.0), cagr=float(cagr),
                dd=float((eq / peak - 1.0).min()),
                sharpe=float(valid.mean() / sd * np.sqrt(bars_per_year)) if sd > 0 else 0.0)


def split_stats(ret, time, bars_per_year):
    t_arr = np.asarray(time)
    t0, t1 = t_arr[0], t_arr[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80
    bounds = {"design": (t0, edge1), "validation": (edge1, edge2), "oos": (edge2, t1)}
    out = {}
    for sp in SPLITS:
        a, b = bounds[sp]
        mask = (t_arr >= a) & (t_arr < b)
        out[sp] = stats(ret[mask], bars_per_year)
    return out


def main() -> int:
    bars_per_year = 24 * 252
    comm_pts = 7.0
    slip_pts = 1.0
    floor_pts = 1.0

    # Load all 3 pairs + compute indicators
    inds = {}
    for sym in PAIRS:
        df = load_csv(sym, "H1")
        p = RSI_PARAMS[sym]
        inds[sym] = compute_indicators(df, sym, p["rsi_p"], COMPRESS_ATR[sym], EXT_ATR)

    # Align to common time index
    common_times = None
    for sym in PAIRS:
        t = set(inds[sym]["time"])
        common_times = t if common_times is None else (common_times & t)
    common_times = sorted(common_times)
    n_common = len(common_times)
    print(f"Common bars: {n_common}")

    # Reindex each pair's indicators to common times
    aligned_inds = {}
    for sym in PAIRS:
        df_ind = pd.DataFrame({
            "time": inds[sym]["time"],
            "close": inds[sym]["close"],
            "rsi": inds[sym]["rsi"],
            "ema": inds[sym]["ema"],
            "atr": inds[sym]["atr"],
            "spread": inds[sym]["spread"],
            "compressed": inds[sym]["compressed"],
        }).set_index("time").reindex(common_times).reset_index()
        aligned_inds[sym] = {
            "close": df_ind["close"].to_numpy(float),
            "rsi": df_ind["rsi"].to_numpy(float),
            "ema": df_ind["ema"].to_numpy(float),
            "atr": df_ind["atr"].to_numpy(float),
            "spread": df_ind["spread"].astype(float).to_numpy(),
            "time": df_ind["time"],
            "compressed": df_ind["compressed"].to_numpy(bool),
        }

    # --- Test 1: Baseline RSI band per pair ---
    print("\n=== TEST 1: Baseline RSI band per pair ===")
    baseline = {}
    for sym in PAIRS:
        p = RSI_PARAMS[sym]
        ind = aligned_inds[sym]
        ret, pos = simulate_rsi_band(sym, ind, p["rsi_p"], p["mid"], p["hi"], p["lo"], p["db"],
                                     comm_pts=comm_pts, slip_pts=slip_pts, floor_pts=floor_pts)
        st = split_stats(ret, ind["time"], bars_per_year)
        baseline[sym] = st
        rt = int(np.abs(np.diff(pos, prepend=0)).sum()) // 2
        print(f"  {sym}: RT={rt:>4}  D/V/OOS="
              f"{st['design']['total']:+.2f}/{st['validation']['total']:+.2f}/{st['oos']['total']:+.2f}%  "
              f"Sharpe={st['oos']['sharpe']:>5.2f}  DD={st['oos']['dd']*100:>6.2f}%")

    # --- Test 2: RSI band gated by compression (trade only when ALL pairs compressed) ---
    print("\n=== TEST 2: RSI band gated by portfolio compression (all 3 compressed) ===")
    all_compressed = np.ones(n_common, dtype=bool)
    for sym in PAIRS:
        all_compressed &= aligned_inds[sym]["compressed"]

    for sym in PAIRS:
        p = RSI_PARAMS[sym]
        ind = aligned_inds[sym]
        ret, pos = simulate_rsi_band(sym, ind, p["rsi_p"], p["mid"], p["hi"], p["lo"], p["db"],
                                     compressed_mask=all_compressed,
                                     comm_pts=comm_pts, slip_pts=slip_pts, floor_pts=floor_pts)
        st = split_stats(ret, ind["time"], bars_per_year)
        rt = int(np.abs(np.diff(pos, prepend=0)).sum()) // 2
        base = baseline[sym]
        better = st["oos"]["total"] > base["oos"]["total"]
        flag = "PASS" if all(st[sp]["total"] > 0 for sp in SPLITS) else "fail"
        print(f"  {sym}: RT={rt:>4}  D/V/OOS="
              f"{st['design']['total']:+.2f}/{st['validation']['total']:+.2f}/{st['oos']['total']:+.2f}%  "
              f"Sharpe={st['oos']['sharpe']:>5.2f}  DD={st['oos']['dd']*100:>6.2f}%  [{flag}] "
              f"{'BETTER' if better else ''} (baseline OOS={base['oos']['total']:+.2f}%)")

    # --- Test 3: Cross-pair confluence (2-of-3 same direction) ---
    print("\n=== TEST 3: Cross-pair RSI band confluence (2-of-3 agreement) ===")
    all_positions = {}
    all_returns = {}
    for sym in PAIRS:
        p = RSI_PARAMS[sym]
        ind = aligned_inds[sym]
        ret, pos = simulate_rsi_band(sym, ind, p["rsi_p"], p["mid"], p["hi"], p["lo"], p["db"],
                                     comm_pts=comm_pts, slip_pts=slip_pts, floor_pts=floor_pts)
        all_positions[sym] = pos
        all_returns[sym] = ret

    confluence_pos = np.zeros(n_common, dtype=int)
    for t in range(n_common):
        long_count = sum(1 for s in PAIRS if all_positions[s][t] > 0)
        short_count = sum(1 for s in PAIRS if all_positions[s][t] < 0)
        if long_count >= 2:
            confluence_pos[t] = 1
        elif short_count >= 2:
            confluence_pos[t] = -1

    # Portfolio return: equal weight, each pair contributes only when it agrees
    portfolio_ret = np.zeros(n_common)
    for sym in PAIRS:
        for t in range(n_common):
            if confluence_pos[t] != 0 and all_positions[sym][t] == confluence_pos[t]:
                portfolio_ret[t] += all_returns[sym][t] / len(PAIRS)

    st_conf = split_stats(portfolio_ret, np.array(common_times), bars_per_year)
    conf_rt = int(np.abs(np.diff(confluence_pos, prepend=0)).sum()) // 2
    flag = "PASS" if all(st_conf[sp]["total"] > 0 for sp in SPLITS) else "fail"
    avg_base_oos = np.mean([baseline[s]["oos"]["total"] for s in PAIRS])
    print(f"  Confluence (2-of-3): RT={conf_rt:>4}  "
          f"D/V/OOS={st_conf['design']['total']:+.2f}/{st_conf['validation']['total']:+.2f}/{st_conf['oos']['total']:+.2f}%  "
          f"Sharpe={st_conf['oos']['sharpe']:>5.2f}  DD={st_conf['oos']['dd']*100:>6.2f}%  [{flag}] "
          f"{'BETTER' if st_conf['oos']['total'] > avg_base_oos else ''} "
          f"(baseline avg OOS={avg_base_oos:+.2f}%)")

    # --- Test 4: Confluence + compression filter (skip when all compressed) ---
    print("\n=== TEST 4: Confluence + skip when all pairs compressed ===")
    conf_with_filter = confluence_pos.copy()
    for t in range(n_common):
        if all_compressed[t]:
            conf_with_filter[t] = 0

    portfolio_ret_cf = np.zeros(n_common)
    for sym in PAIRS:
        for t in range(n_common):
            if conf_with_filter[t] != 0 and all_positions[sym][t] == conf_with_filter[t]:
                portfolio_ret_cf[t] += all_returns[sym][t] / len(PAIRS)

    st_cf = split_stats(portfolio_ret_cf, np.array(common_times), bars_per_year)
    cf_rt = int(np.abs(np.diff(conf_with_filter, prepend=0)).sum()) // 2
    flag = "PASS" if all(st_cf[sp]["total"] > 0 for sp in SPLITS) else "fail"
    print(f"  Confluence+chop-filter: RT={cf_rt:>4}  "
          f"D/V/OOS={st_cf['design']['total']:+.2f}/{st_cf['validation']['total']:+.2f}/{st_cf['oos']['total']:+.2f}%  "
          f"Sharpe={st_cf['oos']['sharpe']:>5.2f}  DD={st_cf['oos']['dd']*100:>6.2f}%  [{flag}] "
          f"{'BETTER' if st_cf['oos']['total'] > st_conf['oos']['total'] else ''} "
          f"(confluence OOS={st_conf['oos']['total']:+.2f}%)")

    print("\nDone.")


if __name__ == "__main__":
    raise SystemExit(main())
