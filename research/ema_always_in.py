"""Always-in EMA21 flipper: accept or reject each cross.

The mandate: never be flat. Long while EMA21 faces up, short while it faces down.
So the only decision that matters is whether a fresh cross is real or fake:

    real cross -> flip the position
    fake cross -> hold the current direction through the noise

That is a different question from "when do I enter", and it is the question that
actually costs money, because every accepted fake pays the spread and then pays
it again on the way back.

Variants compared on identical bars, identical cost:

  naive        flip on every close beyond EMA21 (the baseline that loses)
  confirm_N    flip only after N consecutive closes on the new side
  stack        flip only when EMA21/EMA50/SMMA100 are stacked in the new direction
  ml           flip only when P(real cross) from a logistic model clears a
               threshold chosen on the design window only
  (each combined with)  pure | trail2 | be1

Always in: after a protective exit we re-enter on the next close in the direction
EMA21 faces, so time in market stays ~100%.

Cost: one full spread (12 pts default) per position change.

Metrics: mark-to-market equity per bar, then per chronological split
(design 60% / validation 20% / oos 20%) -> return, CAGR, Sharpe, max DD,
flips, cost drag. Buy-and-hold on the same segment is printed for reference.

Usage
    .\\.venv\\Scripts\\python.exe research\\ema_always_in.py --symbol EURUSD --tf H1
    .\\.venv\\Scripts\\python.exe research\\ema_always_in.py --symbol USDJPY --tf H1 --cost-pts 12
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


# ------------------------------------------------------------------ labels
def label_runs(close: np.ndarray, high: np.ndarray, low: np.ndarray,
               e: np.ndarray, min_bars: int, clean_max: float):
    """For every bar: which run it belongs to, and whether that run was real."""
    n = len(close)
    side = np.where(close > e, 1, np.where(close < e, -1, 0))
    for i in range(1, n):
        if side[i] == 0:
            side[i] = side[i - 1]

    run_id = np.full(n, -1, int)
    run_bars = np.zeros(n, int)
    run_ok = np.zeros(n, int)
    is_flip = np.zeros(n, bool)

    rid = 0
    i = 1
    while i < n:
        if side[i] == side[i - 1]:
            run_id[i] = rid
            i += 1
            continue
        # a new run starts at i
        start = i
        j = i
        while j + 1 < n and side[j + 1] == side[i]:
            j += 1
        bars = j - start + 1
        viol = 0
        for k in range(start, j + 1):
            if side[i] > 0 and low[k] < e[k]:
                viol += 1
            elif side[i] < 0 and high[k] > e[k]:
                viol += 1
        ok = 1 if (bars >= min_bars and viol / bars <= clean_max) else 0
        is_flip[start] = True
        rid += 1
        for k in range(start, j + 1):
            run_id[k] = rid
            run_bars[k] = bars
            run_ok[k] = ok
        i = j + 1

    return side, is_flip, run_bars, run_ok


def features(df: pd.DataFrame, side: np.ndarray, is_flip: np.ndarray,
             run_bars: np.ndarray) -> pd.DataFrame:
    """Entry-time features at every bar (only past information)."""
    close = df["close"].to_numpy(float)
    openp = df["open"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    e50 = df["ema50"].to_numpy(float)
    s100 = df["smma"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    time = df["time"]
    n = len(df)

    slope3 = np.zeros(n)
    slope3[3:] = (e[3:] - e[:-3]) / np.maximum(atr[3:], 1e-12)

    # whipsaw context: flips in the last 20 bars, bars since last flip
    flips20 = np.zeros(n, int)
    since_flip = np.full(n, 999, int)
    last = -1
    cnt = 0
    q = []
    for i in range(n):
        if is_flip[i]:
            q.append(i)
            last = i
        while q and q[0] < i - 20:
            q.pop(0)
        flips20[i] = len(q)
        since_flip[i] = i - last if last >= 0 else 999

    out = pd.DataFrame({
        "time": time,
        "side": side,
        "is_flip": is_flip,
        "slope3": slope3,
        "stack": (((close - e) * side > 0).astype(int)
                  + ((e - e50) * side > 0).astype(int)
                  + ((e50 - s100) * side > 0).astype(int)),
        "dist21": (close - e) * side / np.maximum(atr, 1e-12),
        "dist50": (close - e50) * side / np.maximum(atr, 1e-12),
        "dist100": (close - s100) * side / np.maximum(atr, 1e-12),
        "body": (close - openp) * side / np.maximum(atr, 1e-12),
        "rsi_adj": (rsi - 50.0) * side,
        "atr_ratio": atr / np.r_[np.full(20, np.nan), atr[:-20]],
        "flips20": flips20,
        "since_flip": since_flip,
        "hour": time.dt.hour,
        "dow": time.dt.dayofweek,
        "prev_bars": np.r_[np.zeros(1, int), run_bars[:-1]],
    })
    return out


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0,
                 iters: int = 2000, lr: float = 0.5):
    mu = X.mean(0)
    sd = X.std(0) + 1e-9
    Z = np.hstack([np.ones((len(X), 1)), (X - mu) / sd])
    w = np.zeros(Z.shape[1])
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-np.clip(Z @ w, -30, 30)))
        g = Z.T @ (p - y) / len(y)
        g[1:] += l2 * w[1:] / len(y)
        w -= lr * g
    return w, mu, sd


def predict(w, mu, sd, X: np.ndarray) -> np.ndarray:
    Z = np.hstack([np.ones((len(X), 1)), (X - mu) / sd])
    return 1.0 / (1.0 + np.exp(-np.clip(Z @ w, -30, 30)))


def auc(y: np.ndarray, p: np.ndarray) -> float:
    y = np.asarray(y, int)
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return 0.5
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p), float)
    ranks[order] = np.arange(1, len(p) + 1)
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


# ------------------------------------------------------------------ engine
def run_curve(close: np.ndarray, atr: np.ndarray, side: np.ndarray,
              accept: np.ndarray, cost_px: float, mode: str,
              trail_atr: float = 2.0, be_atr: float = 1.0,
              max_wait: int = 5, min_hold: int = 0) -> dict:
    """Mark-to-market equity, always in the market.

    accept[i] is only consulted on flip bars. False = hold the current direction,
    but if the new EMA side persists for max_wait bars we catch up anyway so the
    position can never be stuck on the wrong side forever.
    """
    n = len(close)
    eq = np.ones(n)
    pos = 0
    pos_start = 0
    entry_px = 0.0
    best = 0.0
    stop_px = 0.0
    flat_until = -1
    wrong_since = -1
    flips = 0
    stops = 0
    cost_total = 0.0

    for i in range(1, n):
        eq[i] = eq[i - 1] * (1.0 + pos * (close[i] - close[i - 1]) / close[i - 1])

        # ---- protective management
        if pos != 0:
            if mode == "trail2":
                best = max(best, close[i]) if pos > 0 else min(best, close[i])
                if (close[i] - best) * pos <= -trail_atr * atr[i]:
                    eq[i] *= (1.0 - cost_px / close[i])
                    cost_total += cost_px / close[i]
                    pos = 0
                    stops += 1
                    flat_until = i + 1
            elif mode == "be1":
                if stop_px == entry_px and (close[i] - entry_px) * pos >= be_atr * atr[i]:
                    stop_px = entry_px
                if (close[i] - stop_px) * pos <= 0:
                    eq[i] *= (1.0 - cost_px / close[i])
                    cost_total += cost_px / close[i]
                    pos = 0
                    stops += 1
                    flat_until = i + 1

        # ---- re-enter after a protective exit
        if pos == 0 and i >= flat_until and side[i] != 0:
            pos = int(side[i])
            pos_start = i
            entry_px = close[i]
            best = close[i]
            stop_px = close[i] - pos * trail_atr * atr[i] if mode == "be1" else 0.0
            if mode == "be1":
                stop_px = close[i] - pos * trail_atr * atr[i]
            eq[i] *= (1.0 - cost_px / close[i])
            cost_total += cost_px / close[i]
            flips += 1
            wrong_since = -1
            continue

        # ---- flip decision
        if pos != 0 and side[i] != pos:
            if wrong_since < 0:
                wrong_since = i
            accept_now = bool(accept[i]) or (max_wait > 0 and i - wrong_since >= max_wait)
            if min_hold > 0 and (i - pos_start) < min_hold:
                accept_now = False
            if accept_now:
                eq[i] *= (1.0 - cost_px / close[i])
                cost_total += cost_px / close[i]
                pos = int(side[i])
                pos_start = i
                entry_px = close[i]
                best = close[i]
                stop_px = close[i] - pos * trail_atr * atr[i] if mode == "be1" else 0.0
                flips += 1
                wrong_since = -1
        else:
            wrong_since = -1

    return dict(eq=eq, flips=flips, stops=stops, cost=cost_total)


def segment_metrics(time: pd.Series, eq: np.ndarray, lo, hi) -> dict:
    m = (time >= lo) & (time < hi)
    if m.sum() < 10:
        return dict(ret=0.0, cagr=0.0, sharpe=0.0, dd=0.0)
    seg = eq[m]
    seg = seg / seg[0]
    days = (time[m].iloc[-1] - time[m].iloc[0]).days
    years = max(days / 365.25, 1e-9)
    ret = float(seg[-1] - 1.0)
    cagr = float((seg[-1]) ** (1.0 / years) - 1.0) if seg[-1] > 0 else -1.0
    # daily
    s = pd.Series(seg, index=pd.DatetimeIndex(time[m]))
    daily = s.resample("1D").last().dropna()
    r = daily.pct_change().dropna()
    sharpe = float(r.mean() / r.std() * np.sqrt(252)) if len(r) > 5 and r.std() > 0 else 0.0
    peak = np.maximum.accumulate(seg)
    dd = float((seg / peak - 1.0).min())
    return dict(ret=ret, cagr=cagr, sharpe=sharpe, dd=dd)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--min-bars", type=int, default=5)
    ap.add_argument("--clean-max", type=float, default=0.25)
    ap.add_argument("--cost-pts", type=float, default=12.0)
    ap.add_argument("--confirm", type=int, default=2)
    ap.add_argument("--trail-atr", type=float, default=2.0)
    ap.add_argument("--max-wait", type=int, default=5)
    ap.add_argument("--min-hold", type=int, default=0,
                    help="minimum bars held before a direction flip is allowed")
    ap.add_argument("--resample", default="", choices=["", "H4", "D1"],
                    help="aggregate the broker H1 tape to a slower clock")
    args = ap.parse_args()

    sym, tf = args.symbol.upper(), args.tf.upper()
    path = DATA / f"{sym}_{tf}.csv"
    if not path.exists():
        raise SystemExit(f"missing data: {path}")

    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)
    if args.resample:
        rule = {"H4": "4h", "D1": "1D"}[args.resample]
        df = (df.set_index("time")
                .resample(rule)
                .agg({"open": "first", "high": "max", "low": "min",
                      "close": "last", "tick_volume": "sum"})
                .dropna().reset_index())
        tf = args.resample
    df["ema"] = ema(df["close"], EMA)
    df["ema50"] = ema(df["close"], EMA_MID)
    df["smma"] = smma(df["close"], SMMA)
    df["rsi"] = wilder_rsi(df["close"], RSI_P)
    df["atr"] = wilder_atr(df, ATR_P)
    df = df.dropna(subset=["ema", "ema50", "smma", "rsi", "atr"]).reset_index(drop=True)

    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    n = len(df)

    point = 0.001 if sym.endswith("JPY") else 0.00001
    cost_px = args.cost_pts * point

    side, is_flip, run_bars, run_ok = label_runs(close, high, low, e,
                                                 args.min_bars, args.clean_max)
    F = features(df, side, is_flip, run_bars)
    time = df["time"]

    t0, t1 = time.iloc[0], time.iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80
    bounds = {"design": (t0, edge1), "validation": (edge1, edge2), "oos": (edge2, t1)}

    # ---------------------------------------------------- naive baseline
    accept_all = np.ones(n, bool)
    base = run_curve(close, atr, side, accept_all, cost_px, "pure")

    print(f"=== always-in EMA{EMA} flipper  {sym} {tf} ===")
    print(f"bars {n}  {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}   "
          f"cost {args.cost_pts:.0f} pts per change")
    print(f"crosses {int(is_flip.sum())}   real runs (>= {args.min_bars} bars, clean) "
          f"{int(run_ok[is_flip].mean() * 100):.1f}%   "
          f"median run {int(np.median(run_bars[is_flip]))} bars")
    print("")
    print("--- 0. naive: flip on every cross (pure, no stop) ---")
    hdr = f"{'split':<12}{'return%':>10}{'CAGR%':>8}{'Sharpe':>8}{'maxDD%':>8}{'flips':>7}"
    print(hdr)
    print("-" * len(hdr))
    for sp in SPLITS:
        m = segment_metrics(time, base["eq"], *bounds[sp])
        print(f"{sp:<12}{m['ret'] * 100:>10.1f}{m['cagr'] * 100:>8.2f}"
              f"{m['sharpe']:>8.2f}{m['dd'] * 100:>8.1f}{base['flips']:>7}")
    m = segment_metrics(time, base["eq"], t0, t1)
    print(f"{'full':<12}{m['ret'] * 100:>10.1f}{m['cagr'] * 100:>8.2f}"
          f"{m['sharpe']:>8.2f}{m['dd'] * 100:>8.1f}{base['flips']:>7}")
    print(f"  cost drag {base['cost'] * 100:.1f}% of equity over the full period")

    # buy and hold reference
    bh = close / close[0]
    m = segment_metrics(time, bh, t0, t1)
    print(f"{'buy&hold':<12}{m['ret'] * 100:>10.1f}{m['cagr'] * 100:>8.2f}"
          f"{m['sharpe']:>8.2f}{m['dd'] * 100:>8.1f}{1:>7}")

    # ---------------------------------------------------- "EMA facing" baseline
    # The literal mandate: long while EMA21 faces up, short while it faces down.
    # Facing = the EMA's own slope, not the price's side. A price cross with no
    # slope turn is then by definition a fake and never flips the position.
    print("")
    print("--- 0b. position follows EMA21 slope (facing up / facing down) ---")
    slope3 = F["slope3"].to_numpy(float)
    print(f"{'deadband':<12}{'split':<12}{'return%':>10}{'CAGR%':>8}{'Sharpe':>8}{'maxDD%':>8}{'flips':>7}")
    print("-" * 65)
    for db in (0.0, 0.05, 0.10, 0.20):
        raw = np.where(slope3 > db, 1, np.where(slope3 < -db, -1, 0))
        ssl = np.zeros(n, int)
        last = 1 if close[0] > e[0] else -1
        for i in range(n):
            if raw[i] != 0:
                last = int(raw[i])
            ssl[i] = last
        flips_slope = np.r_[False, ssl[1:] != ssl[:-1]]
        cv = run_curve(close, atr, ssl, np.ones(n, bool), cost_px, "pure")
        for sp in SPLITS:
            m = segment_metrics(time, cv["eq"], *bounds[sp])
            print(f"{db:<12.2f}{sp:<12}{m['ret'] * 100:>10.1f}{m['cagr'] * 100:>8.2f}"
                  f"{m['sharpe']:>8.2f}{m['dd'] * 100:>8.1f}{int(flips_slope.sum()):>7}")
        m = segment_metrics(time, cv["eq"], t0, t1)
        print(f"{db:<12.2f}{'full':<12}{m['ret'] * 100:>10.1f}{m['cagr'] * 100:>8.2f}"
              f"{m['sharpe']:>8.2f}{m['dd'] * 100:>8.1f}{int(flips_slope.sum()):>7}")
        print("")

    # ---------------------------------------------------- confirm N bars
    print("")
    print(f"--- 1. flip only after {args.confirm} consecutive closes on the new side ---")
    accept_conf = np.zeros(n, bool)
    for i in range(args.confirm, n):
        if is_flip[i - args.confirm + 1]:
            j = i - args.confirm + 1
            if np.all(side[j : i + 1] == side[i]):
                accept_conf[i] = True
    cur = run_curve(close, atr, side, accept_conf, cost_px, "pure")
    print(hdr)
    print("-" * len(hdr))
    for sp in SPLITS:
        m = segment_metrics(time, cur["eq"], *bounds[sp])
        print(f"{sp:<12}{m['ret'] * 100:>10.1f}{m['cagr'] * 100:>8.2f}"
              f"{m['sharpe']:>8.2f}{m['dd'] * 100:>8.1f}{cur['flips']:>7}")
    m = segment_metrics(time, cur["eq"], t0, t1)
    print(f"{'full':<12}{m['ret'] * 100:>10.1f}{m['cagr'] * 100:>8.2f}"
          f"{m['sharpe']:>8.2f}{m['dd'] * 100:>8.1f}{cur['flips']:>7}")

    # ---------------------------------------------------- stack filter
    print("")
    print("--- 2. flip only when EMA stack is aligned with the new side (>= 2 of 3) ---")
    accept_stack = is_flip & (F["stack"].to_numpy() >= 2)
    cur = run_curve(close, atr, side, accept_stack, cost_px, "pure",
                    min_hold=args.min_hold)
    print(hdr)
    print("-" * len(hdr))
    for sp in SPLITS:
        m = segment_metrics(time, cur["eq"], *bounds[sp])
        print(f"{sp:<12}{m['ret'] * 100:>10.1f}{m['cagr'] * 100:>8.2f}"
              f"{m['sharpe']:>8.2f}{m['dd'] * 100:>8.1f}{cur['flips']:>7}")
    m = segment_metrics(time, cur["eq"], t0, t1)
    print(f"{'full':<12}{m['ret'] * 100:>10.1f}{m['cagr'] * 100:>8.2f}"
          f"{m['sharpe']:>8.2f}{m['dd'] * 100:>8.1f}{cur['flips']:>7}")

    # ---------------------------------------------------- ML filter
    print("")
    print("--- 3. flip only when P(real cross) from a logistic model clears a threshold ---")
    feats = ["slope3", "stack", "dist21", "dist50", "dist100", "body",
             "rsi_adj", "atr_ratio", "flips20", "since_flip", "hour", "dow", "prev_bars"]
    flip_idx = np.where(is_flip)[0]
    fl = F.iloc[flip_idx].dropna(subset=feats)
    y = run_ok[fl.index.to_numpy()]

    tr = (time.iloc[fl.index.to_numpy()] < edge1)
    if tr.sum() < 300:
        print("  not enough design crosses to fit")
    else:
        w, mu, sd = fit_logistic(fl[feats].to_numpy(float)[tr], y[tr])
        p_all = predict(w, mu, sd, fl[feats].to_numpy(float))
        for sp in SPLITS:
            mm = (time.iloc[fl.index.to_numpy()] >= bounds[sp][0]) & \
                 (time.iloc[fl.index.to_numpy()] < bounds[sp][1])
            if mm.sum() == 0:
                continue
            print(f"  {sp:<11} crosses {int(mm.sum()):>5}  AUC {auc(y[mm], p_all[mm]):.3f}  "
                  f"base {y[mm].mean() * 100:5.1f}%")

        # threshold chosen on design Sharpe only
        p_flip = np.zeros(n)
        p_flip[fl.index.to_numpy()] = p_all
        best = None
        for thr in (0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70):
            acc = is_flip & (p_flip >= thr)
            cv = run_curve(close, atr, side, acc, cost_px, "pure")
            md = segment_metrics(time, cv["eq"], *bounds["design"])
            if best is None or md["sharpe"] > best[1]:
                best = (thr, md["sharpe"], cv)
        thr = best[0]
        print(f"  threshold picked on design Sharpe: {thr:.2f}")
        cur = best[2]
        print(hdr)
        print("-" * len(hdr))
        for sp in SPLITS:
            m = segment_metrics(time, cur["eq"], *bounds[sp])
            print(f"{sp:<12}{m['ret'] * 100:>10.1f}{m['cagr'] * 100:>8.2f}"
                  f"{m['sharpe']:>8.2f}{m['dd'] * 100:>8.1f}{cur['flips']:>7}")
        m = segment_metrics(time, cur["eq"], t0, t1)
        print(f"{'full':<12}{m['ret'] * 100:>10.1f}{m['cagr'] * 100:>8.2f}"
              f"{m['sharpe']:>8.2f}{m['dd'] * 100:>8.1f}{cur['flips']:>7}")

        # ---------------------------------------------------- with management
        print("")
        print(f"--- 4. ml filter + trail {args.trail_atr:.1f} ATR (re-enter next bar) ---")
        acc = is_flip & (p_flip >= thr)
        cur = run_curve(close, atr, side, acc, cost_px, "trail2",
                        trail_atr=args.trail_atr)
        print(hdr)
        print("-" * len(hdr))
        for sp in SPLITS:
            m = segment_metrics(time, cur["eq"], *bounds[sp])
            print(f"{sp:<12}{m['ret'] * 100:>10.1f}{m['cagr'] * 100:>8.2f}"
                  f"{m['sharpe']:>8.2f}{m['dd'] * 100:>8.1f}{cur['flips']:>7}")
        m = segment_metrics(time, cur["eq"], t0, t1)
        print(f"{'full':<12}{m['ret'] * 100:>10.1f}{m['cagr'] * 100:>8.2f}"
              f"{m['sharpe']:>8.2f}{m['dd'] * 100:>8.1f}{cur['flips']:>7}")
        print(f"  protective exits {cur['stops']}  cost drag {cur['cost'] * 100:.1f}%")

        # publish the naive and the filtered curves
        for label, cv in (("naive", base), ("mlfilter", best[2])):
            s = pd.Series(np.diff(np.r_[1.0, cv["eq"]]) / np.r_[1.0, cv["eq"]][:-1],
                          index=pd.DatetimeIndex(time)).dropna()
            publish_results(f"ema_always_in_{label}_{sym}_{tf}", s,
                            last_months=12, last_days=21)

        out = RESULTS / f"ema_always_in_{sym}_{tf}.csv"
        pd.DataFrame({
            "time": time, "close": close, "side": side, "flip": is_flip,
            "real": run_ok, "p_real": p_flip,
            "eq_naive": base["eq"], "eq_ml": best[2]["eq"],
        }).to_csv(out, index=False)
        print("")
        print(f"wrote {out}")

    print("")
    print("Verdict rule: a filter only counts if design, validation AND oos all")
    print("improve on the naive flipper after cost. Otherwise the crosses stay noise.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
