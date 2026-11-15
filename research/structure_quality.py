#!/usr/bin/env python3
r"""Validate SovereignStructure look-left S/R grading on real OOS data.

Tests the hypothesis: entries taken when structure grade == GOOD have better
forward returns than entries when grade == BAD, across design/validation/OOS splits.

Usage:
    .\.venv\Scripts\python.exe research\structure_quality.py --symbol USDJPY --tf H1
    .\.venv\Scripts\python.exe research\structure_quality.py --symbol EURUSD --tf H1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from ema_cross_harvest import ATR_P, EMA, EMA_MID, SMMA, RSI_P, wilder_atr, wilder_rsi, ema, smma
from period_returns import publish_results

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"
SPLITS = ("design", "validation", "oos")

K = 2                      # fractal half-width (confirmation lag)
COST_PTS = 12.0            # round-trip cost in points
HORIZONS = (5, 10, 20)     # forward return horizons in bars


def swings(high: np.ndarray, low: np.ndarray, k: int):
    """Detect k-bar fractals (confirmed k bars later)."""
    n = len(high)
    up = np.zeros(n, bool)
    dn = np.zeros(n, bool)
    for i in range(k, n - k):
        win_h = high[i - k:i + k + 1]
        win_l = low[i - k:i + k + 1]
        if high[i] >= win_h.max() and high[i] > win_h[:k].max():
            up[i] = True
        if low[i] <= win_l.min() and low[i] < win_l[:k].min():
            dn[i] = True
    return up, dn


def build_levels(high, low, close, time_idx, k, tol_atr, atr_series, max_age_bars=500,
                 causal=True):
    """Build look-left S/R level list up to each bar (causal, no lookahead).

    Returns a list of (bar_index, levels_list) where levels_list contains
    dicts with price, kind (+1 resistance / -1 support), touches, last_touch_idx.

    causal=True (default) freezes each bar's snapshot by copying the level dicts.
    causal=False reproduces the original reference-aliasing behaviour, which leaks
    future touches and future breaks into past grades - kept only so the size of
    that bias can be measured.
    """
    n = len(high)
    up, dn = swings(high, low, k)
    levels_per_bar = []
    active_levels = []  # list of level dicts

    for t in range(n):
        #--- confirm swings at bar t-k (confirmation lag)
        i_confirm = t - k
        if i_confirm >= k and i_confirm < n - k:
            if up[i_confirm]:
                px = high[i_confirm]
                #--- find or create level
                found = False
                for lv in active_levels:
                    if lv["kind"] == 1 and abs(lv["price"] - px) <= tol_atr * atr_series[t]:
                        lv["touches"] += 1
                        lv["last_touch"] = t
                        found = True
                        break
                if not found:
                    active_levels.append({
                        "price": px, "kind": 1, "time": time_idx[i_confirm],
                        "touches": 1, "last_touch": t, "broken": False})
            if dn[i_confirm]:
                px = low[i_confirm]
                found = False
                for lv in active_levels:
                    if lv["kind"] == -1 and abs(lv["price"] - px) <= tol_atr * atr_series[t]:
                        lv["touches"] += 1
                        lv["last_touch"] = t
                        found = True
                        break
                if not found:
                    active_levels.append({
                        "price": px, "kind": -1, "time": time_idx[i_confirm],
                        "touches": 1, "last_touch": t, "broken": False})

        #--- update distances, check breaks/touches, prune stale
        atr = atr_series[t]
        px = close[t]
        survivors = []
        for lv in active_levels:
            lv["dist_atr"] = abs(px - lv["price"]) / atr if atr > 0 else 999.0
            # touch
            if abs(px - lv["price"]) <= tol_atr * atr:
                if t > lv["last_touch"]:
                    lv["touches"] += 1
                    lv["last_touch"] = t
            # break
            if lv["kind"] == 1 and px > lv["price"] + 0.5 * atr:
                lv["broken"] = True
            if lv["kind"] == -1 and px < lv["price"] - 0.5 * atr:
                lv["broken"] = True
            # age
            age = (time_idx[t] - lv["time"]) / np.timedelta64(1, "h")  # hours for H1
            if not lv["broken"] and age < max_age_bars:
                survivors.append(lv)
        active_levels = survivors
        #--- IMPORTANT: snapshot COPIES, not references.
        #    A bare list(active_levels) stores the same dict objects, so a touch
        #    counted on a future bar, or a level that breaks later, would mutate
        #    the snapshot used to grade a past bar. That is lookahead. Copying
        #    the dict freezes each bar's view of the world at that bar.
        if causal:
            levels_per_bar.append([dict(lv) for lv in active_levels])
        else:
            levels_per_bar.append(list(active_levels))

    return levels_per_bar


def nearest_levels(levels, price, kind_filter, atr):
    """Find nearest level of given kind above/below price."""
    nearest = None
    nearest_dist = 1e9
    nearest_touches = 0
    for lv in levels:
        if lv["broken"]:
            continue
        if kind_filter == 1 and lv["kind"] == 1 and lv["price"] > price:  # resistance above
            d = (lv["price"] - price) / atr
            if d < nearest_dist:
                nearest_dist = d
                nearest = lv
        if kind_filter == -1 and lv["kind"] == -1 and lv["price"] < price:  # support below
            d = (price - lv["price"]) / atr
            if d < nearest_dist:
                nearest_dist = d
                nearest = lv
    if nearest is not None:
        nearest_touches = nearest["touches"]
    return nearest, nearest_dist, nearest_touches


def grade_structure(side, price, atr, levels, confluence, stack_aligned):
    """Grade structure: GOOD(2) / WEAK(1) / BAD(0)."""
    nearest, dist, touches = nearest_levels(levels, price, side, atr)
    score = 0
    score += min(confluence, 3)
    if stack_aligned:
        score += 2
    if dist > 1.5:
        score += 1
    if dist > 3.0:
        score += 1
    if touches <= 1:
        score += 1
    elif touches >= 5:
        score -= 1
    if score >= 5:
        return 2  # GOOD
    if score >= 3:
        return 1  # WEAK
    return 0  # BAD


def confluence_count(levels, price, atr, kind_filter):
    """Count TFs with levels near price (simplified: count levels within 2 ATR)."""
    c = 0
    for lv in levels:
        if lv["broken"]:
            continue
        d = abs(price - lv["price"]) / atr
        if d <= 2.0:
            if kind_filter == 1 and lv["kind"] == 1:  # resistance -> confluence for shorts
                c += 1
            if kind_filter == -1 and lv["kind"] == -1:  # support -> confluence for longs
                c += 1
    return c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="USDJPY")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--k", type=int, default=2)
    ap.add_argument("--cost-pts", type=float, default=12.0)
    ap.add_argument("--tol-atr", type=float, default=0.35)
    ap.add_argument("--legacy-snapshot", action="store_true",
                    help="reproduce the original reference-aliasing (leaks future "
                         "touches/breaks into past grades); use only to measure the bias")
    ap.add_argument("--legacy-entry", action="store_true",
                    help="reproduce the original entry lag (used the swing at t-1, "
                         "which a k-bar fractal cannot know until t); bias measurement only")
    args = ap.parse_args()

    sym = args.symbol.upper()
    tf = args.tf.upper()
    path = DATA / f"{sym}_{tf}.csv"
    if not path.exists():
        raise SystemExit(f"missing data: {path}")

    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)

    #--- indicators
    close = df["close"].astype(float).to_numpy()
    openp = df["open"].astype(float).to_numpy()
    high = df["high"].astype(float).to_numpy()
    low = df["low"].astype(float).to_numpy()
    time_idx = df["time"].to_numpy()
    n = len(df)

    # ATR
    tr = np.maximum(high - low,
                    np.maximum(np.abs(high - np.roll(close, 1)),
                               np.abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    atr = pd.Series(tr).rolling(ATR_P).mean().to_numpy()

    #--- EMA stack
    e21 = ema(pd.Series(close), 21).to_numpy()
    e50 = ema(pd.Series(close), EMA_MID).to_numpy()
    s100 = smma(pd.Series(close), SMMA).to_numpy()

    point = 0.001 if sym.endswith("JPY") else 0.00001
    cost_px = args.cost_pts * point

    #--- build look-left levels (causal)
    levels_per_bar = build_levels(high, low, close, time_idx, args.k,
                                  args.tol_atr, atr, max_age_bars=500,
                                  causal=not args.legacy_snapshot)

    #--- find entry signals (structure ride: swing low + close up / swing high + close down)
    up, dn = swings(high, low, args.k)
    entries = []  # (bar_idx, side, grade, close_price)

    for t in range(args.k + 2, n - max(HORIZONS)):
        if atr[t] <= 0:
            continue
        #--- the most recent swing that is actually CONFIRMED at bar t.
        #    A k-bar fractal at index i needs bars i+1..i+k to exist, so it is
        #    only knowable at i+k. Using t-1 was one bar too early (lookahead).
        i_confirm = t - 1 if args.legacy_entry else t - args.k
        if i_confirm < args.k or i_confirm >= n - args.k:
            continue
        swing_low = dn[i_confirm]
        swing_high = up[i_confirm]

        side = 0
        if swing_low and close[t] > close[t - 1]:
            side = 1
        elif swing_high and close[t] < close[t - 1]:
            side = -1

        if side == 0:
            continue

        #--- stack alignment
        stack_aligned = True
        if side > 0:
            stack_aligned = (close[t] > e21[t] and e21[t] > e50[t] and e50[t] > s100[t])
        else:
            stack_aligned = (close[t] < e21[t] and e21[t] < e50[t] and e50[t] < s100[t])

        #--- confluence + grade
        levels = levels_per_bar[t]
        c_long = confluence_count(levels, close[t], atr[t], -1)
        c_short = confluence_count(levels, close[t], atr[t], 1)
        confluence = c_long if side > 0 else c_short
        grade = grade_structure(side, close[t], atr[t], levels, confluence, stack_aligned)

        entries.append((t, side, grade, close[t]))

    if not entries:
        print("No entries found.")
        return 0

    print(f"=== structure quality validation: {sym} {tf} ===")
    print(f"bars={n}  k={args.k}  entries={len(entries)}  cost={args.cost_pts:.0f} pts")
    print("")

    #--- count by grade
    grade_counts = {2: 0, 1: 0, 0: 0}
    for _, _, g, _ in entries:
        grade_counts[g] += 1
    print(f"Grade GOOD(2): {grade_counts[2]}  WEAK(1): {grade_counts[1]}  BAD(0): {grade_counts[0]}")
    print("")

    #--- forward returns by grade
    for h in HORIZONS:
        print(f"--- forward {h}-bar return by structure grade ---")
        for gname, gval in (("GOOD", 2), ("WEAK", 1), ("BAD", 0)):
            rets = []
            for t, side, grade, px in entries:
                if grade != gval:
                    continue
                j = min(t + h, n - 1)
                ret = (close[j] - close[t]) / close[t]
                if side < 0:
                    ret = -ret
                rets.append(ret - cost_px / close[t])
            if rets:
                arr = np.array(rets)
                print(f"  {gname:<6} n={len(arr):>4}  mean={arr.mean()*100:>7.3f}%  "
                      f"win={100*(arr>0).mean():>5.1f}%  sum={arr.sum()*100:>7.2f}%")
            else:
                print(f"  {gname:<6} n=0")
        print("")

    #--- split analysis for GOOD vs BAD
    t0, t1 = time_idx[0], time_idx[-1]
    span = t1 - t0
    edge1 = t0 + span * 0.60
    edge2 = t0 + span * 0.80

    def split_of(ts):
        return "design" if ts < edge1 else ("validation" if ts < edge2 else "oos")

    print("--- OOS: GOOD vs BAD structure forward 20-bar return ---")
    for gname, gval in (("GOOD", 2), ("BAD", 0)):
        print(f"  {gname} structure:")
        for sp in SPLITS:
            rets = []
            for t, side, grade, px in entries:
                if grade != gval:
                    continue
                ts = time_idx[t]
                if split_of(ts) != sp:
                    continue
                j = min(t + 20, n - 1)
                ret = (close[j] - close[t]) / close[t]
                if side < 0:
                    ret = -ret
                rets.append(ret - cost_px / close[t])
            if rets:
                arr = np.array(rets)
                print(f"    {sp:<11} n={len(arr):>3}  mean={arr.mean()*100:>7.3f}%  "
                      f"win={100*(arr>0).mean():>5.1f}%")
            else:
                print(f"    {sp:<11} n=0")
        print("")

    #--- publish to period_returns for calendar reporting
    for gname, gval in (("good", 2), ("weak", 1), ("bad", 0)):
        rets = []
        times = []
        for t, side, grade, px in entries:
            if grade != gval:
                continue
            j = min(t + 20, n - 1)
            ret = (close[j] - close[t]) / close[t]
            if side < 0:
                ret = -ret
            rets.append(ret - cost_px / close[t])
            times.append(time_idx[t])
        if rets:
            s = pd.Series(rets, index=pd.DatetimeIndex(times))
            tag = f"struct_quality_{sym}_{tf}_{gname}_h20"
            publish_results(tag, s, append_to_bundle=False)
            print(f"published {tag} ({len(s)} entries)")

    print("")
    print("Read: if GOOD structure mean >> BAD structure mean on OOS, the look-left")
    print("S/R filter is adding real information. If they are similar, structure")
    print("grade is not predictive on this symbol.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

