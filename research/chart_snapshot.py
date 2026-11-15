"""Recompute, in Python, exactly what the EA reads off the chart.

This is the other half of the proof harness. The EA writes
``MQL5/Files/Sovereign/chart_read_<SYM>_<TF>.json`` once per bar with every
value it used. This script recomputes the same fields from the same broker
bars (``research/data/mt5/<SYM>_<TF>.csv``) and writes:

    research/results/chart_snapshot_<SYM>_<TF>.json   machine diff input
    research/results/chart_snapshot_<SYM>_<TF>.md     human-readable proof

``scripts/compare_chart_snapshot.py`` then diffs the EA file against this one
field by field. Agreement within one point means the bot's read of the chart is
the same read a human has with ``templates/fxmopol.tpl`` loaded.

The indicator maths here deliberately mirrors the MQL5 code, including the
MT5 SMMA seed (SMA of the first ``period`` values, then recursive) and Wilder
RSI/ATR smoothing.

Usage:
    .\\.venv\\Scripts\\python.exe research\\chart_snapshot.py --symbol EURUSD --tf H1
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "research" / "data" / "mt5"
OUT = ROOT / "research" / "results"

# Must match the EA inputs / templates/fxmopol.tpl
EMA_FAST = 21
EMA_SLOW = 50
SMMA_PERIOD = 100
RSI_PERIOD = 9
ATR_PERIOD = 14
SWING_BARS = 40
EXT_ATR = 1.15
ZIGZAG_BARS = 160
ZIGZAG_ATR = 0.80


# ---------------------------------------------------------------- indicators
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def smma(series: pd.Series, period: int) -> pd.Series:
    """MT5 MODE_SMMA: seed = SMA(period), then SMMA = (prev*(p-1) + price)/p."""
    vals = series.to_numpy(dtype=float)
    res = [math.nan] * len(vals)
    if len(vals) >= period:
        prev = vals[:period].mean()
        res[period - 1] = prev
        for i in range(period, len(vals)):
            prev = (prev * (period - 1) + vals[i]) / period
            res[i] = prev
    return pd.Series(res, index=series.index)


def wilder_rsi(series: pd.Series, period: int) -> pd.Series:
    vals = series.to_numpy(dtype=float)
    n = len(vals)
    out = [math.nan] * n
    if n <= period:
        return pd.Series(out, index=series.index)
    gain = [0.0] * n
    loss = [0.0] * n
    for i in range(1, n):
        d = vals[i] - vals[i - 1]
        gain[i] = max(d, 0.0)
        loss[i] = max(-d, 0.0)
    avg_g = sum(gain[1 : period + 1]) / period
    avg_l = sum(loss[1 : period + 1]) / period
    out[period] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    for i in range(period + 1, n):
        avg_g = (avg_g * (period - 1) + gain[i]) / period
        avg_l = (avg_l * (period - 1) + loss[i]) / period
        out[i] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    return pd.Series(out, index=series.index)


def wilder_atr(df: pd.DataFrame, period: int) -> pd.Series:
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    n = len(h)
    tr = [0.0] * n
    for i in range(n):
        if i == 0:
            tr[i] = h[i] - l[i]
        else:
            tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    out = [math.nan] * n
    if n <= period:
        return pd.Series(out, index=df.index)
    prev = sum(tr[1 : period + 1]) / period
    out[period] = prev
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return pd.Series(out, index=df.index)


# ---------------------------------------------------------------- structure
def zigzag(high, low, times, atr_value, bars=ZIGZAG_BARS, mult=ZIGZAG_ATR):
    """Mirror of CSovereignPatterns::BuildZigZag (oldest bar first)."""
    n = min(len(high), bars)
    h = high[-n:]
    l = low[-n:]
    t = times[-n:]
    thr = atr_value * max(0.1, mult)

    piv: list[dict] = []
    kind = 0
    ext = 0.0
    ext_t = None
    for i in range(n):
        if kind == 0:
            kind, ext, ext_t = 1, h[i], t[i]
            continue
        if kind > 0:
            if h[i] > ext:
                ext, ext_t = h[i], t[i]
            elif l[i] < ext - thr:
                piv.append({"time": ext_t, "price": float(ext), "kind": 1})
                kind, ext, ext_t = -1, l[i], t[i]
        else:
            if l[i] < ext:
                ext, ext_t = l[i], t[i]
            elif h[i] > ext + thr:
                piv.append({"time": ext_t, "price": float(ext), "kind": -1})
                kind, ext, ext_t = 1, h[i], t[i]
    piv.append({"time": ext_t, "price": float(ext), "kind": kind})
    return piv[-8:]


def between(v, lo, hi):
    return lo <= v <= hi


def match_harmonic(piv: list[dict]) -> str:
    if len(piv) < 5:
        return "none"
    x, a, b, c, d = (p["price"] for p in piv[-5:])
    kinds = [p["kind"] for p in piv[-5:]]
    for i in range(4):
        if kinds[i] == kinds[i + 1]:
            return "none"

    xa, ab, bc, cd = abs(a - x), abs(b - a), abs(c - b), abs(d - c)
    xd = abs(d - a)
    if min(xa, ab, bc, cd) <= 0:
        return "none"
    z, y, w, xdr = ab / xa, bc / ab, cd / bc, xd / xa

    name = ""
    if between(z, 0.55, 0.68) and between(y, 0.35, 0.90) and between(w, 1.10, 1.70) and between(xdr, 0.70, 0.86):
        name = "Gartley"
    elif between(z, 0.35, 0.52) and between(y, 0.35, 0.90) and between(w, 1.55, 2.70) and between(xdr, 0.84, 0.94):
        name = "Bat"
    elif between(z, 0.74, 0.82) and between(y, 0.35, 0.90) and between(w, 1.55, 2.30) and between(xdr, 1.20, 1.65):
        name = "Butterfly"
    elif between(z, 0.35, 0.65) and between(y, 0.35, 0.90) and between(w, 2.55, 3.70) and between(xdr, 1.55, 1.70):
        name = "Crab"
    elif between(z, 0.35, 0.65) and between(y, 1.25, 1.65) and between(w, 0.60, 1.05):
        name = "ABCD"
    if not name:
        return "none"
    side = "bull" if kinds[-1] < 0 else "bear"
    return f"{name} {side}  B{z:.2f} C{y:.2f} D{w:.2f} XD{xdr:.2f}  [context only - not an entry trigger]"


# ---------------------------------------------------------------- snapshot
def build(symbol: str, tf: str) -> dict:
    path = DATA / f"{symbol}_{tf}.csv"
    if not path.exists():
        raise SystemExit(f"missing data: {path}")

    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.sort_values("time").reset_index(drop=True)

    df["ema_fast"] = ema(df["close"], EMA_FAST)
    df["ema_slow"] = ema(df["close"], EMA_SLOW)
    df["smma"] = smma(df["close"], SMMA_PERIOD)
    df["rsi"] = wilder_rsi(df["close"], RSI_PERIOD)
    df["atr"] = wilder_atr(df, ATR_PERIOD)

    # EA reads bar index 1 (last completed). CSV last row is that bar.
    row = df.iloc[-1]
    bar_time = row["time"].to_pydatetime()

    price = float(row["close"])
    e_fast = float(row["ema_fast"])
    e_slow = float(row["ema_slow"])
    e_smma = float(row["smma"])
    rsi_v = float(row["rsi"])
    atr_v = float(row["atr"])

    # swing window: EA uses bars 1..SWING_BARS-1 => last 39 completed bars
    win = df.iloc[-(SWING_BARS - 1) :]
    swing_hi = float(win["high"].max())
    swing_lo = float(win["low"].min())
    span = swing_hi - swing_lo
    fib50 = swing_lo + span * 0.5
    fib618 = swing_lo + span * 0.618

    # classic daily pivot from the previous completed day
    day = bar_time.date()
    daily = df.assign(day=df["time"].dt.date).groupby("day").agg(
        high=("high", "max"), low=("low", "min"), close=("close", "last")
    )
    prior = daily[daily.index < day]
    s1 = r1 = 0.0
    if len(prior) > 0:
        pr = prior.iloc[-1]
        pivot = (pr["high"] + pr["low"] + pr["close"]) / 3.0
        s1 = 2.0 * pivot - pr["high"]
        r1 = 2.0 * pivot - pr["low"]

    up = price > e_fast > e_slow > e_smma
    dn = price < e_fast < e_slow < e_smma
    trend = 1 if up else (-1 if dn else 0)
    extended = abs(price - e_fast) / atr_v > EXT_ATR if atr_v > 0 else False

    if up and rsi_v > 78.0:
        bias, plan = 0, "UPTREND but RSI overbought - wait pullback"
    elif up:
        bias = 1
        plan = ("LONG bias - wait pullback to EMA zone" if extended
                else "LONG bias - trend intact, buy near EMA")
    elif dn and rsi_v < 22.0:
        bias, plan = 0, "DOWNTREND but RSI oversold - wait bounce"
    elif dn:
        bias = -1
        plan = ("SHORT bias - wait bounce to EMA zone" if extended
                else "SHORT bias - trend intact, sell near EMA")
    else:
        bias, plan = 0, "NO TRADE - range; wait for stack/breakout"

    zone_lo = e_fast - atr_v * 0.25
    zone_hi = e_fast + atr_v * 0.25
    target = invalidation = 0.0
    if bias > 0:
        target = swing_hi if price < swing_hi else r1
        invalidation = min(swing_lo, e_fast - atr_v)
    elif bias < 0:
        target = swing_lo if price > swing_lo else s1
        invalidation = max(swing_hi, e_fast + atr_v)

    state = ("UPTREND  price>EMA>EMA>SMMA" if up
             else "DOWNTREND  price<EMA<EMA<SMMA" if dn
             else "RANGE / MIXED  stack not aligned")

    piv = zigzag(df["high"].to_numpy(), df["low"].to_numpy(),
                 df["time"].tolist(), atr_v)

    return {
        "schema_version": 1,
        "source": "python",
        "symbol": symbol,
        "period": tf,
        "bar_time": bar_time.strftime("%Y.%m.%d %H:%M"),
        "bar_time_epoch": int(bar_time.timestamp()),
        "price": price,
        "ema_fast": e_fast,
        "ema_slow": e_slow,
        "smma": e_smma,
        "rsi": rsi_v,
        "atr": atr_v,
        "swing_hi": swing_hi,
        "swing_lo": swing_lo,
        "fib50": fib50,
        "fib618": fib618,
        "s1": s1,
        "r1": r1,
        "trend": trend,
        "bias": bias,
        "state": state,
        "plan": plan,
        "zone_lo": zone_lo,
        "zone_hi": zone_hi,
        "target": target,
        "invalidation": invalidation,
        "pattern": match_harmonic(piv),
        "zigzag": [
            {"time": p["time"].strftime("%Y.%m.%d %H:%M"),
             "price": p["price"], "kind": p["kind"]}
            for p in piv
        ],
        "settings": {
            "ema_fast": EMA_FAST,
            "ema_slow": EMA_SLOW,
            "smma": SMMA_PERIOD,
            "rsi": RSI_PERIOD,
            "atr": ATR_PERIOD,
            "swing_bars": SWING_BARS,
            "zigzag_bars": ZIGZAG_BARS,
            "zigzag_atr": ZIGZAG_ATR,
        },
    }


def markdown(snap: dict) -> str:
    d = 5 if snap["price"] < 20 else 3
    lines = [
        f"# Chart snapshot - {snap['symbol']} {snap['period']} - {snap['bar_time']}",
        "",
        "Recomputed from `research/data/mt5/` with the template's own settings.",
        "Compare against the EA panel: the numbers must be identical.",
        "",
        "## Indicator read (the bot's eye)",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Close | {snap['price']:.{d}f} |",
        f"| EMA {EMA_FAST} | {snap['ema_fast']:.{d}f} |",
        f"| EMA {EMA_SLOW} | {snap['ema_slow']:.{d}f} |",
        f"| SMMA {SMMA_PERIOD} | {snap['smma']:.{d}f} |",
        f"| RSI {RSI_PERIOD} | {snap['rsi']:.2f} |",
        f"| ATR {ATR_PERIOD} | {snap['atr']:.{d}f} |",
        "",
        "## Structure",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Swing high (last {SWING_BARS - 1} bars) | {snap['swing_hi']:.{d}f} |",
        f"| Swing low | {snap['swing_lo']:.{d}f} |",
        f"| Fib 50% | {snap['fib50']:.{d}f} |",
        f"| Fib 61.8% | {snap['fib618']:.{d}f} |",
        f"| D1 pivot S1 | {snap['s1']:.{d}f} |",
        f"| D1 pivot R1 | {snap['r1']:.{d}f} |",
        "",
        "## The bot's conclusion",
        "",
        f"- **State:** {snap['state']}",
        f"- **Plan:** {snap['plan']}",
        f"- **Zone:** {snap['zone_lo']:.{d}f} - {snap['zone_hi']:.{d}f}",
        f"- **Target:** {snap['target']:.{d}f}" if snap["target"] else "- **Target:** none",
        f"- **Invalidation:** {snap['invalidation']:.{d}f}" if snap["invalidation"] else "- **Invalidation:** none",
        f"- **Pattern (context only):** {snap['pattern']}",
        "",
        "## Swing legs the zigzag sees",
        "",
        "| Time | Price | Kind |",
        "|---|---|---|",
    ]
    for p in snap["zigzag"]:
        lines.append(f"| {p['time']} | {p['price']:.{d}f} | {'high' if p['kind'] > 0 else 'low'} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    args = ap.parse_args()

    snap = build(args.symbol.upper(), args.tf.upper())
    OUT.mkdir(parents=True, exist_ok=True)

    stem = f"chart_snapshot_{snap['symbol']}_{snap['period']}"
    jpath = OUT / f"{stem}.json"
    mpath = OUT / f"{stem}.md"
    jpath.write_text(json.dumps(snap, indent=2) + "\n", encoding="utf-8")
    mpath.write_text(markdown(snap) + "\n", encoding="utf-8")

    d = 5 if snap["price"] < 20 else 3
    print(f"[snapshot] {snap['symbol']} {snap['period']} {snap['bar_time']}")
    print(f"[snapshot] close {snap['price']:.{d}f}  EMA{EMA_FAST} {snap['ema_fast']:.{d}f}  "
          f"EMA{EMA_SLOW} {snap['ema_slow']:.{d}f}  SMMA{SMMA_PERIOD} {snap['smma']:.{d}f}  "
          f"RSI{RSI_PERIOD} {snap['rsi']:.2f}")
    print(f"[snapshot] {snap['state']} | {snap['plan']}")
    print(f"[snapshot] wrote {jpath}")
    print(f"[snapshot] wrote {mpath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
