"""Detect the turn BEFORE the EMA21 cross, then measure whether entering there
beats waiting for the cross.

The idea, in chart terms:

    A downtrend starts failing before it actually crosses EMA21.
      - price makes a lower low, RSI makes a higher low   (divergence)
      - candles shrink, wicks get longer on the downside  (momentum decay)
      - distance to EMA21 compresses                      (coiling)
      - ATR contracts                                     (energy stored)
      - EMA21 slope stops falling                         (trend dying)
    Then a bar closes back above the previous bar's high -> that is the trigger.
    The actual EMA21 cross comes later and confirms it.

For every such setup we enter at the trigger close, stop under the recent swing
(plus a small ATR buffer), and exit on the opposite EMA21 cross. We then compare
against the same trade taken at the cross (offset 1) on identical bars.

All conditions use only bars up to and including the trigger bar. The cross is
never used to build the setup - only to score it afterwards (lead time).

Usage
    .\\.venv\\Scripts\\python.exe research\\ema_turn_precursor.py --symbol EURUSD --tf H1
    .\\.venv\\Scripts\\python.exe research\\ema_turn_precursor.py --symbol USDJPY --tf H1 --min-score 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from period_returns import publish_results  # noqa: E402

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"

EMA = 21
EMA_MID = 50
SMMA = 100
RSI_P = 9
ATR_P = 14
SPLITS = ("design", "validation", "oos")


# ------------------------------------------------------------------ indicators
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def smma(series: pd.Series, period: int) -> pd.Series:
    v = series.to_numpy(float)
    out = np.full(len(v), np.nan)
    if len(v) >= period:
        prev = v[:period].mean()
        out[period - 1] = prev
        for i in range(period, len(v)):
            prev = (prev * (period - 1) + v[i]) / period
            out[i] = prev
    return pd.Series(out, index=series.index)


def wilder_rsi(series: pd.Series, period: int) -> pd.Series:
    v = series.to_numpy(float)
    n = len(v)
    out = np.full(n, np.nan)
    if n <= period:
        return pd.Series(out, index=series.index)
    g = np.zeros(n)
    l = np.zeros(n)
    for i in range(1, n):
        d = v[i] - v[i - 1]
        g[i] = max(d, 0.0)
        l[i] = max(-d, 0.0)
    ag = g[1 : period + 1].mean()
    al = l[1 : period + 1].mean()
    out[period] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    for i in range(period + 1, n):
        ag = (ag * (period - 1) + g[i]) / period
        al = (al * (period - 1) + l[i]) / period
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return pd.Series(out, index=series.index)


def wilder_atr(df: pd.DataFrame, period: int) -> pd.Series:
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    n = len(h)
    tr = np.empty(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    out = np.full(n, np.nan)
    if n <= period:
        return pd.Series(out, index=df.index)
    prev = tr[1 : period + 1].mean()
    out[period] = prev
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return pd.Series(out, index=df.index)


# ------------------------------------------------------------------ swings
def swing_flags(low: np.ndarray, high: np.ndarray, k: int = 2):
    """3/5-bar Bill Williams style swing flags."""
    n = len(low)
    sl = np.zeros(n, bool)
    sh = np.zeros(n, bool)
    for i in range(k, n - k):
        if low[i] == low[i - k : i + k + 1].min():
            sl[i] = True
        if high[i] == high[i - k : i + k + 1].max():
            sh[i] = True
    return sl, sh


def last_two(flags: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For each bar, the two most recent swing indices at or before it."""
    n = len(flags)
    a = np.full(n, -1, int)
    b = np.full(n, -1, int)
    p1 = p2 = -1
    for i in range(n):
        if flags[i]:
            p2 = p1
            p1 = i
        a[i] = p1
        b[i] = p2
    return a, b


# ------------------------------------------------------------------ setups
def build_setups(df: pd.DataFrame, min_score: int, need_div: bool) -> pd.DataFrame:
    close = df["close"].to_numpy(float)
    openp = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    time = df["time"].to_numpy()
    n = len(df)

    body = np.abs(close - openp)
    dist = np.abs(close - e) / np.maximum(atr, 1e-12)
    slope3 = np.zeros(n)
    slope3[3:] = (e[3:] - e[:-3]) / np.maximum(atr[3:], 1e-12)

    sl_flag, sh_flag = swing_flags(low, high, 2)
    sl1, sl2 = last_two(sl_flag)
    sh1, sh2 = last_two(sh_flag)

    rows = []
    for i in range(40, n - 1):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue

        # ---------------- long setup (downtrend failing)
        long_div = False
        if sl1[i] > 0 and sl2[i] > 0:
            long_div = (low[sl1[i]] < low[sl2[i]] and rsi[sl1[i]] > rsi[sl2[i]] + 1.0)
        if close[i] < e[i] and (long_div or not need_div):
            score = 0
            if long_div:
                score += 1
            decay = body[max(0, i - 2) : i + 1].mean() < 0.8 * body[max(0, i - 5) : i - 2].mean()
            if decay:
                score += 1
            if dist[i] < dist[i - 5] and dist[i] < 1.2:
                score += 1
            if a < atr[i - 20]:
                score += 1
            if slope3[i] > -0.15:
                score += 1
            trigger = close[i] > high[i - 1]
            if score >= min_score and trigger:
                rows.append(dict(i=i, side=1, score=score, divergence=int(long_div),
                                 time=pd.Timestamp(time[i]), entry=close[i],
                                 atr=a, dist=dist[i]))

        # ---------------- short setup (uptrend failing)
        short_div = False
        if sh1[i] > 0 and sh2[i] > 0:
            short_div = (high[sh1[i]] > high[sh2[i]] and rsi[sh1[i]] < rsi[sh2[i]] - 1.0)
        if close[i] > e[i] and (short_div or not need_div):
            score = 0
            if short_div:
                score += 1
            decay = body[max(0, i - 2) : i + 1].mean() < 0.8 * body[max(0, i - 5) : i - 2].mean()
            if decay:
                score += 1
            if dist[i] < dist[i - 5] and dist[i] < 1.2:
                score += 1
            if a < atr[i - 20]:
                score += 1
            if slope3[i] < 0.15:
                score += 1
            trigger = close[i] < low[i - 1]
            if score >= min_score and trigger:
                rows.append(dict(i=i, side=-1, score=score, divergence=int(short_div),
                                 time=pd.Timestamp(time[i]), entry=close[i],
                                 atr=a, dist=dist[i]))

    return pd.DataFrame(rows)


# ------------------------------------------------------------------ trade sim
def run_trade(df: pd.DataFrame, i0: int, side: int, stop_px: float,
              cost_atr: float, max_bars: int = 200) -> tuple[float, int, float]:
    """Enter at close[i0]. Exit on opposite EMA21 close or stop. Returns (R, bars, entry)."""
    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    n = len(df)

    entry = float(close[i0])
    risk = abs(entry - stop_px)
    if risk <= 0:
        return 0.0, 0, entry
    a = float(atr[i0])

    last = min(i0 + max_bars, n - 1)
    for k in range(i0 + 1, last + 1):
        hit = (low[k] <= stop_px) if side > 0 else (high[k] >= stop_px)
        if hit:
            return (stop_px - entry) * side / risk - cost_atr * a / risk, k - i0, entry
        broken = (close[k] < e[k]) if side > 0 else (close[k] > e[k])
        if broken:
            return (close[k] - entry) * side / risk - cost_atr * a / risk, k - i0, entry
    return (close[last] - entry) * side / risk - cost_atr * a / risk, last - i0, entry


def metrics(pnl: list[float]) -> dict:
    if not pnl:
        return dict(n=0, win=0.0, avg=0.0, total=0.0, pf=0.0, dd=0.0)
    a = np.asarray(pnl, float)
    wins = a[a > 0].sum()
    losses = -a[a < 0].sum()
    eq = np.cumsum(a)
    dd = float((eq - np.maximum.accumulate(eq)).min()) if len(eq) else 0.0
    return dict(n=len(a), win=float((a > 0).mean() * 100), avg=float(a.mean()),
                total=float(a.sum()),
                pf=float(wins / losses) if losses > 0 else 99.99, dd=dd)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--min-score", type=int, default=3)
    ap.add_argument("--need-div", type=int, default=1,
                    help="1 = require RSI divergence, 0 = score only")
    ap.add_argument("--cost-pts", type=float, default=12.0)
    ap.add_argument("--swing-buf", type=float, default=0.25)
    args = ap.parse_args()

    sym, tf = args.symbol.upper(), args.tf.upper()
    path = DATA / f"{sym}_{tf}.csv"
    if not path.exists():
        raise SystemExit(f"missing data: {path}")

    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)
    df["ema"] = ema(df["close"], EMA)
    df["ema50"] = ema(df["close"], EMA_MID)
    df["smma"] = smma(df["close"], SMMA)
    df["rsi"] = wilder_rsi(df["close"], RSI_P)
    df["atr"] = wilder_atr(df, ATR_P)
    df = df.dropna(subset=["ema", "ema50", "smma", "rsi", "atr"]).reset_index(drop=True)

    point = 0.001 if sym.endswith("JPY") else 0.00001
    med_atr = float(df["atr"].median())
    cost_atr = args.cost_pts * point / med_atr

    setups = build_setups(df, args.min_score, bool(args.need_div))
    if setups.empty:
        print("no pre-cross setups found; relax --min-score")
        return 1

    t0, t1 = df["time"].iloc[0], df["time"].iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80

    def split_of(ts):
        return "design" if ts < edge1 else ("validation" if ts < edge2 else "oos")

    setups["split"] = setups["time"].apply(split_of)

    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    n = len(df)

    # ---- simulate the pre-cross entries
    pre_r, pre_bars, pre_lead, pre_improve = [], [], [], []
    for _, r in setups.iterrows():
        i = int(r["i"])
        s = int(r["side"])
        if s > 0:
            stop_px = low[max(0, i - 3) : i + 1].min() - args.swing_buf * r["atr"]
        else:
            stop_px = high[max(0, i - 3) : i + 1].max() + args.swing_buf * r["atr"]
        rr, bars, entry = run_trade(df, i, s, float(stop_px), cost_atr)
        pre_r.append(rr)
        pre_bars.append(bars)

        # lead time: bars until price closes on the other side of EMA21
        lead = -1
        for k in range(i + 1, min(i + 40, n)):
            if (close[k] < e[k]) if s > 0 else (close[k] > e[k]):
                lead = k - i
                break
        pre_lead.append(lead)
        if lead > 0:
            pre_improve.append((close[i + lead] - entry) * s / atr[i])

    setups["R"] = pre_r
    setups["bars"] = pre_bars
    setups["lead"] = pre_lead

    print(f"=== pre-cross turn detector  {sym} {tf} ===")
    print(f"bars {len(df)}  {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}")
    print(f"cost {args.cost_pts:.0f} pts = {cost_atr:.3f} ATR   "
          f"stop = swing +- {args.swing_buf:.2f} ATR")
    print(f"setups {len(setups)}  (score >= {args.min_score}, divergence required={args.need_div})")
    print("")

    print("--- A. pre-cross entry (trigger bar close) ---")
    hdr = f"{'split':<12}{'n':>5}{'win%':>7}{'avgR':>8}{'totalR':>9}{'PF':>7}{'DD R':>8}{'avgLead':>9}"
    print(hdr)
    print("-" * len(hdr))
    for sp in SPLITS:
        sub = setups[setups.split == sp]
        m = metrics(sub["R"].tolist())
        lead = sub.loc[sub.lead > 0, "lead"].mean() if (sub.lead > 0).any() else float("nan")
        print(f"{sp:<12}{m['n']:>5}{m['win']:>7.1f}{m['avg']:>8.2f}{m['total']:>9.1f}"
              f"{m['pf']:>7.2f}{m['dd']:>8.1f}{lead:>9.1f}")

    print("")
    print("--- B. same bars, entered at the EMA21 cross (offset 1) ---")
    print(hdr)
    print("-" * len(hdr))
    for sp in SPLITS:
        sub = setups[setups.split == sp]
        pnl = []
        for _, r in sub.iterrows():
            i = int(r["i"])
            s = int(r["side"])
            # first bar where the close crosses EMA21
            k = -1
            for j in range(i + 1, min(i + 40, n)):
                if (close[j] > e[j]) if s > 0 else (close[j] < e[j]):
                    k = j
                    break
            if k < 0 or k + 1 >= n:
                continue
            stop_px = close[k] - s * 1.0 * atr[k]
            rr, _, _ = run_trade(df, k, s, float(stop_px), cost_atr)
            pnl.append(rr)
        m = metrics(pnl)
        print(f"{sp:<12}{m['n']:>5}{m['win']:>7.1f}{m['avg']:>8.2f}{m['total']:>9.1f}"
              f"{m['pf']:>7.2f}{m['dd']:>8.1f}{'-':>9}")

    print("")
    print("--- C. did entering early get a better price? ---")
    if pre_improve:
        arr = np.asarray(pre_improve, float)
        print(f"  entry vs the eventual cross close: mean {arr.mean():+.2f} ATR, "
              f"median {np.median(arr):+.2f} ATR")
        print(f"  positive (we bought lower / sold higher) in {100.0 * (arr > 0).mean():.1f}% of setups")
    print(f"  lead time to the cross: median {np.median([x for x in pre_lead if x > 0]):.1f} bars")

    print("")
    print("--- D. by divergence / score ---")
    for label, mask in (
        ("divergence", setups.divergence == 1),
        ("no divergence", setups.divergence == 0),
        ("score 3", setups.score == 3),
        ("score 4+", setups.score >= 4),
    ):
        m = metrics(setups.loc[mask, "R"].tolist())
        if m["n"] == 0:
            continue
        print(f"  {label:<14} n {m['n']:>4}  win {m['win']:>5.1f}%  avgR {m['avg']:>5.2f}  "
              f"totalR {m['total']:>7.1f}  PF {m['pf']:>5.2f}")

    out = RESULTS / f"ema_turn_precursor_{sym}_{tf}.csv"
    RESULTS.mkdir(parents=True, exist_ok=True)
    setups[["time", "split", "side", "score", "divergence", "entry", "atr", "dist",
            "R", "bars", "lead"]].to_csv(out, index=False)

    print("")
    print("--- monthly/daily proof for the pre-cross rule (risk 1% per trade) ---")
    s = pd.Series(np.asarray(pre_r) * 0.01,
                  index=pd.DatetimeIndex(setups["time"])).sort_index()
    publish_results(f"ema_precross_{sym}_{tf}", s, last_months=12, last_days=21)
    print("")
    print(f"wrote {out}")
    print("")
    print("A pre-cross rule is only interesting if A beats B on validation AND oos,")
    print("not just on design. If it does not, the cross was not the problem.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
