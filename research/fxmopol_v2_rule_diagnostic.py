#!/usr/bin/env python3
"""
fxmopol_v2_rule_diagnostic.py

Simulates fxmopol_v2 entry rules (Short 1-5, Long 1-5, Reflex Short/Long)
on MT5-exported BTCUSD M5 CSV to show exactly why rules fire / skip.

Usage:
    .\.venv\Scripts\python.exe research\fxmopol_v2_rule_diagnostic.py path\to\BTCUSD_M5.csv
CSV columns expected: datetime,open,high,low,close (or time,open,high,low,close)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    # normalize column names
    cols = {c.lower().strip(): c for c in df.columns}
    time_col = cols.get("datetime") or cols.get("time") or cols.get("date")
    if time_col is None:
        raise ValueError("CSV must have a datetime/time column")
    df["datetime"] = pd.to_datetime(df[cols[time_col]])
    for c in ("open", "high", "low", "close"):
        if c not in cols:
            raise ValueError(f"CSV missing '{c}' column")
        df[c] = df[cols[c]].astype(float)
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def rsi(close: pd.Series, period: int = 9) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).rolling(period).mean()
    down = (-delta.clip(upper=0)).rolling(period).mean().replace(0, pd.NA)
    rs = up / down
    return 100 - 100 / (1 + rs)


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def fractal_high(h: pd.Series, i: int) -> bool:
    if i < 2 or i + 2 >= len(h):
        return False
    v = h.iloc[i]
    return bool(v > h.iloc[i - 1] and v > h.iloc[i - 2] and v > h.iloc[i + 1] and v > h.iloc[i + 2])


def fractal_low(l: pd.Series, i: int) -> bool:
    if i < 2 or i + 2 >= len(l):
        return False
    v = l.iloc[i]
    return bool(v < l.iloc[i - 1] and v < l.iloc[i - 2] and v < l.iloc[i + 1] and v < l.iloc[i + 2])


def diagnose(df: pd.DataFrame, symbol: str = "BTCUSD"):
    n = len(df)
    c = df["close"].values
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values

    e21 = ema(df["close"], 21).values
    r = rsi(df["close"], 9).values

    point = 0.01  # BTCUSD typical; adjust if your broker uses 0.1
    InpMinConsecutive = 2
    InpNearEmaPoints = 10
    InpMaxPositions = 5

    # counters (mirror EA logic)
    consec_above = 0
    consec_below = 0

    stats = {
        "short1_fire": 0, "short1_skip": 0,
        "short2_fire": 0, "short2_skip": 0,
        "short3_fire": 0, "short3_skip": 0,
        "short4_fire": 0, "short4_skip": 0,
        "short5_fire": 0, "short5_skip": 0,
        "reflex_short_fire": 0, "reflex_short_skip": 0,
        "long1_fire": 0, "long1_skip": 0,
        "long2_fire": 0, "long2_skip": 0,
        "long3_fire": 0, "long3_skip": 0,
        "long4_fire": 0, "long4_skip": 0,
        "long5_fire": 0, "long5_skip": 0,
        "reflex_long_fire": 0, "reflex_long_skip": 0,
    }

    # track positions
    has_short = False
    short_count = 0
    has_long = False
    long_count = 0

    print(f"=== fxmopol_v2 rule diagnostic | {symbol} | {n} bars ===")
    print(f"Defaults: minConsecutive={InpMinConsecutive}, nearEmaPoints={InpNearEmaPoints}, point={point}")
    print()

    # start after warmup (need EMA21 + RSI valid)
    start = 30
    examples = {}  # rule -> first few skip reasons

    for i in range(start, n):
        ci, oi, hi, li = c[i], o[i], h[i], l[i]
        emai = e21[i]
        rsi_i = r[i]
        green = ci > oi
        red = ci < oi
        green1 = c[i-1] > o[i-1]
        red1 = c[i-1] < o[i-1]

        # consecutive counters (previous bar state, before this bar updates)
        prev_above = consec_above
        prev_below = consec_below

        # update counters after capturing prev
        if ci > emai:
            consec_above += 1
            consec_below = 0
        elif ci < emai:
            consec_below += 1
            consec_above = 0
        else:
            consec_above = 0
            consec_below = 0

        # fractals (need future bars, so use available range)
        fh = fractal_high(pd.Series(h), i) if i + 2 < n else False
        fl = fractal_low(pd.Series(l), i) if i + 2 < n else False
        fh_val = h[i] if fh else 0.0
        fl_val = l[i] if fl else 0.0

        # ---------- SHORT 2 ----------
        s2_cond = (
            prev_below >= InpMinConsecutive
            and green
            and ci <= emai
            and ci >= emai - InpNearEmaPoints * point
            and 40.0 < rsi_i < 50.0
        )
        if s2_cond:
            stats["short2_fire"] += 1
            if len(examples.get("short2_fire", [])) < 5:
                examples.setdefault("short2_fire", []).append(
                    f"bar {i} {df['datetime'].iloc[i]} close={ci:.2f} ema21={emai:.2f} rsi={rsi_i:.1f}"
                )
        else:
            stats["short2_skip"] += 1
            reason = []
            if not (prev_below >= InpMinConsecutive):
                reason.append(f"prevConsecBelow={prev_below}<{InpMinConsecutive}")
            if not green:
                reason.append("candle not green")
            if not (ci <= emai):
                reason.append(f"close {ci:.2f} NOT <= ema21 {emai:.2f}")
            if not (ci >= emai - InpNearEmaPoints * point):
                reason.append(f"close {ci:.2f} not within {InpNearEmaPoints}pts of ema21")
            if not (40.0 < rsi_i < 50.0):
                reason.append(f"RSI={rsi_i:.1f} not in 40-50")
            examples.setdefault("short2_skip", []).append("; ".join(reason))

        # ---------- SHORT 1 ----------
        s1_cond = (
            prev_above >= InpMinConsecutive
            and red
            and ci < emai
            and 25.0 < rsi_i < 50.0
        )
        if s1_cond:
            stats["short1_fire"] += 1
        else:
            stats["short1_skip"] += 1

        # ---------- SHORT 3 ----------
        s3_cond = (
            prev_below >= InpMinConsecutive
            and red
            and ci <= emai
            and ci < o[i-1]
            and green1
        )
        if s3_cond:
            stats["short3_fire"] += 1
        else:
            stats["short3_skip"] += 1

        # ---------- SHORT 4 ----------
        if i >= 2:
            g1 = ci > oi and ci < emai
            g2 = c[i-1] > o[i-1] and c[i-1] < e21[i-1]
            g3 = c[i-2] > o[i-2] and c[i-2] < e21[i-2]
            s4_cond = g1 and g2 and g3
        else:
            s4_cond = False
        if s4_cond:
            stats["short4_fire"] += 1
        else:
            stats["short4_skip"] += 1

        # ---------- SHORT 5 ----------
        s5_cond = (
            not has_short
            and prev_below >= InpMinConsecutive
            and ci < emai
            and rsi_i > 40.0
            and ci >= emai - InpNearEmaPoints * point
            and ci <= emai + InpNearEmaPoints * point
        )
        if s5_cond:
            stats["short5_fire"] += 1
        else:
            stats["short5_skip"] += 1

        # ---------- REFLEX SHORT ----------
        if i >= 3:
            rs_cond = (
                not has_short
                and red
                and ci < emai
                and green1
                and c[i-1] > e21[i-1]
                and c[i-2] < e21[i-2]
                and c[i-3] < e21[i-3]
            )
        else:
            rs_cond = False
        if rs_cond:
            stats["reflex_short_fire"] += 1
        else:
            stats["reflex_short_skip"] += 1

        # ---------- LONG 2 ----------
        l2_cond = (
            prev_above >= InpMinConsecutive
            and red
            and ci >= emai
            and ci <= emai + InpNearEmaPoints * point
            and 50.0 < rsi_i < 60.0
        )
        if l2_cond:
            stats["long2_fire"] += 1
        else:
            stats["long2_skip"] += 1

        # ---------- LONG 1 ----------
        l1_cond = (
            prev_below >= InpMinConsecutive
            and green
            and ci > emai
            and 50.0 < rsi_i < 75.0
        )
        if l1_cond:
            stats["long1_fire"] += 1
        else:
            stats["long1_skip"] += 1

        # ---------- LONG 3 ----------
        if i >= 1:
            l3_cond = (
                prev_above >= InpMinConsecutive
                and green
                and ci >= emai
                and ci > o[i-1]
                and red1
            )
        else:
            l3_cond = False
        if l3_cond:
            stats["long3_fire"] += 1
        else:
            stats["long3_skip"] += 1

        # ---------- LONG 4 ----------
        if i >= 2:
            r1 = ci < oi and ci > emai
            r2 = c[i-1] < o[i-1] and c[i-1] > e21[i-1]
            r3 = c[i-2] < o[i-2] and c[i-2] > e21[i-2]
            l4_cond = r1 and r2 and r3
        else:
            l4_cond = False
        if l4_cond:
            stats["long4_fire"] += 1
        else:
            stats["long4_skip"] += 1

        # ---------- LONG 5 ----------
        l5_cond = (
            not has_long
            and prev_above >= InpMinConsecutive
            and ci > emai
            and rsi_i < 60.0
            and ci >= emai - InpNearEmaPoints * point
            and ci <= emai + InpNearEmaPoints * point
        )
        if l5_cond:
            stats["long5_fire"] += 1
        else:
            stats["long5_skip"] += 1

        # ---------- REFLEX LONG ----------
        if i >= 3:
            rl_cond = (
                not has_long
                and green
                and ci > emai
                and red1
                and c[i-1] < e21[i-1]
                and c[i-2] > e21[i-2]
                and c[i-3] > e21[i-3]
            )
        else:
            rl_cond = False
        if rl_cond:
            stats["reflex_long_fire"] += 1
        else:
            stats["reflex_long_skip"] += 1

    # print results
    print("=== RULE FIRE COUNTS ===")
    for rule in ["short1", "short2", "short3", "short4", "short5",
                 "reflex_short",
                 "long1", "long2", "long3", "long4", "long5",
                 "reflex_long"]:
        fire = stats[f"{rule}_fire"]
        skip = stats[f"{rule}_skip"]
        total = fire + skip
        pct = (100 * fire / total) if total else 0
        print(f"  {rule:14s}: fire={fire:5d}  skip={skip:5d}  fire_rate={pct:5.1f}%")

    print()
    print("=== SHORT 2 SKIP REASON EXAMPLES (first 5) ===")
    for ex in examples.get("short2_skip", [])[:5]:
        print(f"  {ex}")

    print()
    print("=== KEY INSIGHT ===")
    if stats["short2_skip"] > 0 and stats["short2_fire"] == 0:
        print("SHORT 2 NEVER FIRED. Most likely causes on BTCUSD M5:")
        print("  1. Close rarely within 10 points of EMA21 (BTCUSD is volatile)")
        print("  2. RSI rarely in narrow 40-50 band during downtrends")
        print("  3. prevConsecBelow < 2 (price pokes above EMA21 too often)")
        print("  4. Close not <= EMA21 (green candle closes above EMA21)")
        print()
        print("  Suggested fixes:")
        print("   - Increase InpNearEmaPoints from 10 to 50-200 (BTCUSD scale)")
        print("   - Widen RSI band: 35-55 or 30-60")
        print("   - Reduce InpMinConsecutive from 2 to 1")
        print("   - Check MT5 Journal for 'fxmopol_v2 SHORT2 skipped | ...' reason")


def main():
    if len(sys.argv) < 2:
        print("Usage: fxmopol_v2_rule_diagnostic.py <BTCUSD_M5.csv>")
        print("  Export from MT5: right-click BTCUSD M5 in Market Watch -> Save as...")
        sys.exit(1)
    path = sys.argv[1]
    if not Path(path).exists():
        print(f"File not found: {path}")
        sys.exit(1)
    df = load_csv(path)
    diagnose(df)

if __name__ == "__main__":
    main()
