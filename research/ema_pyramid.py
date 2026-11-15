"""Pyramid inside an EMA21 direction, exit when the direction is about to change.

The mandate: EMA21 facing up means longs only, facing down means shorts only.
Inside that direction we want as many entries as the leg offers - pullbacks to
the EMA, retests, continuation breakouts - and we hold the whole book until the
direction is about to turn.

So there are two separate decisions, and this script measures them separately:

    REGIME   which side is allowed.  EMA21 slope with a deadband, so a flat or
             noisy EMA does not flip the book.  A leg = one continuous regime.

    ADDS     how many entries we take inside that leg, and where.

    EXIT     regime flip (default), or an invalidation break back through EMA21.

Baseline is "single": exactly one entry at the start of the leg. Every pyramid
variant is compared against it on the same legs, same cost, same exit.

Accounting is a fixed-notional book (1 unit per add, no compounding) so that a
pyramid that doubles exposure cannot look good just because it is leveraged.
Cost is charged per unit per round trip.

Usage
    .\\.venv\\Scripts\\python.exe research\\ema_pyramid.py --symbol EURUSD --tf H1
    .\\.venv\\Scripts\\python.exe research\\ema_pyramid.py --symbol EURUSD --resample D1
    .\\.venv\\Scripts\\python.exe research\\ema_pyramid.py --symbol USDJPY --resample D1 --max-adds 5
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


def regime_series(slope3: np.ndarray, db: float) -> np.ndarray:
    raw = np.where(slope3 > db, 1, np.where(slope3 < -db, -1, 0))
    out = np.zeros(len(raw), int)
    last = 1 if raw[0] >= 0 else -1
    for i in range(len(raw)):
        if raw[i] != 0:
            last = int(raw[i])
        out[i] = last
    return out


def legs_of(reg: np.ndarray) -> list[tuple[int, int]]:
    out = []
    start = 0
    for i in range(1, len(reg)):
        if reg[i] != reg[i - 1]:
            out.append((start, i - 1))
            start = i
    out.append((start, len(reg) - 1))
    return out


def swing_flags(low: np.ndarray, high: np.ndarray, k: int = 2):
    n = len(low)
    sl = np.zeros(n, bool)
    sh = np.zeros(n, bool)
    for i in range(k, n - k):
        if low[i] == low[i - k : i + k + 1].min():
            sl[i] = True
        if high[i] == high[i - k : i + k + 1].max():
            sh[i] = True
    return sl, sh


def last_two(flags: np.ndarray):
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


def precursor_exit(df: pd.DataFrame, rsi: np.ndarray, slope3: np.ndarray,
                   pre_slope: float, pre_dist: float):
    """Two masks, using only past bars.

    warn_up[i]   = a long leg is probably ending (bearish divergence, or momentum
                   decay while price is stretched above EMA21 and the slope has
                   flattened). Only a long book reacts to this.
    warn_down[i] = the mirror, only a short book reacts.
    """
    close = df["close"].to_numpy(float)
    openp = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    n = len(df)

    body = np.abs(close - openp)
    dist = np.abs(close - e) / np.maximum(atr, 1e-12)
    sl_flag, sh_flag = swing_flags(low, high, 2)
    sl1, sl2 = last_two(sl_flag)
    sh1, sh2 = last_two(sh_flag)

    warn_up = np.zeros(n, bool)
    warn_down = np.zeros(n, bool)

    for i in range(40, n):
        decay = body[i - 2 : i + 1].mean() < 0.8 * body[i - 5 : i - 2].mean()
        stretched = dist[i] > pre_dist

        bear_div = False
        if sh1[i] > 0 and sh2[i] > 0:
            bear_div = (high[sh1[i]] > high[sh2[i]]
                        and rsi[sh1[i]] < rsi[sh2[i]] - 1.0)
        bull_div = False
        if sl1[i] > 0 and sl2[i] > 0:
            bull_div = (low[sl1[i]] < low[sl2[i]]
                        and rsi[sl1[i]] > rsi[sl2[i]] + 1.0)

        warn_up[i] = bear_div or (decay and stretched and slope3[i] < pre_slope)
        warn_down[i] = bull_div or (decay and stretched and slope3[i] > -pre_slope)

    return warn_up, warn_down


# ------------------------------------------------------------------ engine
def run_leg(df: pd.DataFrame, start: int, end: int, exit_i: int, reg: int,
            mode: str, max_adds: int, add_stop: float, touch: float,
            min_gap: int, invalidation: float, cost_px: float,
            lookback: int = 10, warn: np.ndarray | None = None,
            ctx: dict | None = None) -> list[dict]:
    """Return one record per unit traded inside this leg.

    `end` is the last bar of the regime. `exit_i` is the bar at which the regime
    flip is actually observable (end + 1), so the book is closed at close[exit_i]
    and never at a price we could not have traded on the information we had.
    """
    close = df["close"].to_numpy(float)
    openp = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    atr = df["atr"].to_numpy(float)

    units: list[dict] = []

    def open_unit(i: int, kind: str = "sat"):
        units.append(dict(
            entry_i=i, entry=float(close[i]), kind=kind,
            w=ctx.get("w_core" if kind == "core" else "w_sat", 1.0) if ctx else 1.0,
            stop=float(close[i] - reg * add_stop * atr[i]),
            exit_i=None, exit=None,
        ))

    if mode == "core_sat":
        open_unit(start, "core")
        open_unit(start, "sat")
    else:
        open_unit(start)

    for i in range(start + 1, exit_i + 1):
        # ---- stops first (the core unit has no stop: it rides to the regime end)
        for u in units:
            if u["exit"] is not None or u["kind"] == "core":
                continue
            hit = (low[i] <= u["stop"]) if reg > 0 else (high[i] >= u["stop"])
            if hit:
                u["exit_i"] = i
                u["exit"] = u["stop"]

        # ---- the direction is warning us it is about to turn: leave the book
        if warn is not None and warn[i] and (close[i] - e[i]) * reg > 0:
            for u in units:
                if u["exit"] is None:
                    u["exit_i"] = i
                    u["exit"] = float(close[i])
            break

        # ---- the regime flip is now visible: close the whole book here
        if i >= exit_i:
            for u in units:
                if u["exit"] is None:
                    u["exit_i"] = i
                    u["exit"] = float(close[i])
            break

        # ---- invalidation: price breaks back through EMA21 against the regime
        if invalidation > 0:
            if (close[i] - e[i]) * reg < -invalidation * atr[i]:
                for u in units:
                    if u["exit"] is None:
                        u["exit_i"] = i
                        u["exit"] = float(close[i])
                break

        # ---- chunk mode: take profit into strength, re-enter on the pullback
        if mode in ("chunk", "core_sat"):
            sats = [u for u in units if u["kind"] == "sat"]
            flat = all(u["exit"] is not None for u in sats)
            if flat:
                last_exit = max(u["exit_i"] for u in sats)
                if (i - last_exit >= min_gap
                        and (close[i - 1] - e[i - 1]) * reg <= touch * atr[i - 1]
                        and (close[i] - e[i]) * reg > 0
                        and (close[i] - openp[i]) * reg > 0):
                    open_unit(i)
            else:
                d = ctx["dist"][i]
                decay = False
                if i >= 5:
                    decay = (ctx["body"][i - 2 : i + 1].mean()
                             < 0.8 * ctx["body"][i - 5 : i - 2].mean())
                exhausted = ctx["run_up"][i] >= ctx["run"] if reg > 0 else ctx["run_dn"][i] >= ctx["run"]
                rsi_hit = False
                if ctx.get("rsi") is not None and ctx.get("chunk_rsi", 0) > 0:
                    lvl = ctx["chunk_rsi"]
                    rsi_hit = (ctx["rsi"][i] > lvl) if reg > 0 else (ctx["rsi"][i] < 100.0 - lvl)
                if rsi_hit or (d > ctx["chunk_dist"] and decay) or exhausted:
                    for u in sats:
                        if u["exit"] is None:
                            u["exit_i"] = i
                            u["exit"] = float(close[i])
            continue

        if mode == "single":
            continue
        if sum(1 for u in units if u["exit"] is None) >= max_adds:
            continue

        last_entry = max(u["entry_i"] for u in units)
        if i - last_entry < min_gap:
            continue

        add = False
        if mode == "retest":
            near = (close[i - 1] - e[i - 1]) * reg <= touch * atr[i - 1]
            reclaim = (close[i] - e[i]) * reg > 0 and (close[i] - openp[i]) * reg > 0
            add = near and reclaim
        elif mode == "pullback":
            red = (close[i - 1] - openp[i - 1]) * reg < 0
            resume = (close[i] - close[i - 1]) * reg > 0
            above = (close[i] - e[i]) * reg > 0
            add = red and resume and above
        elif mode == "breakout":
            k = min(lookback, i - start)
            if k >= 3:
                ref = close[i - k : i].max() if reg > 0 else close[i - k : i].min()
                add = (close[i] - ref) * reg > 0
        if add:
            open_unit(i)

    for u in units:
        if u["exit"] is None:
            u["exit_i"] = exit_i
            u["exit"] = float(close[exit_i])
    return units


def book_curve(df: pd.DataFrame, legs: list[dict], p0: float,
               cost_px: float, weight: float = 1.0) -> np.ndarray:
    """Fixed-notional equity: `weight` per add, P&L in price, no compounding."""
    n = len(df)
    close = df["close"].to_numpy(float)
    eq = np.ones(n)
    realized = 0.0
    open_units: list[dict] = []
    by_start: dict[int, list[dict]] = {}
    for lg in legs:
        for u in lg["units"]:
            by_start.setdefault(u["entry_i"], []).append((lg["reg"], u))
    by_exit: dict[int, list[dict]] = {}
    for lg in legs:
        for u in lg["units"]:
            by_exit.setdefault(u["exit_i"], []).append((lg["reg"], u))

    for i in range(n):
        for reg, u in by_start.get(i, []):
            open_units.append(dict(reg=reg, entry=u["entry"],
                                   w=u.get("w", weight), kind=u.get("kind", "sat")))
            realized -= u.get("w", weight) * cost_px / p0
        for reg, u in by_exit.get(i, []):
            for k in range(len(open_units) - 1, -1, -1):
                if (open_units[k]["entry"] == u["entry"]
                        and open_units[k]["kind"] == u.get("kind", "sat")):
                    realized += (open_units[k]["w"]
                                 * (u["exit"] - open_units[k]["entry"]) * reg / p0)
                    open_units.pop(k)
                    break
        mtm = sum(o["w"] * (close[i] - o["entry"]) * o["reg"] for o in open_units) / p0
        eq[i] = 1.0 + realized + mtm
    return eq


def max_exposure(legs: list[dict], n: int) -> int:
    """Peak number of simultaneously open units."""
    delta = np.zeros(n + 1, int)
    for lg in legs:
        for u in lg["units"]:
            delta[u["entry_i"]] += 1
            delta[min(u["exit_i"] + 1, n)] -= 1
    return int(np.maximum.accumulate(delta[:-1]).max())


def seg_metrics(time: pd.Series, eq: np.ndarray, lo, hi) -> dict:
    m = (time >= lo) & (time < hi)
    if m.sum() < 10:
        return dict(ret=0.0, cagr=0.0, sharpe=0.0, dd=0.0)
    seg = eq[m] / eq[m][0]
    years = max((time[m].iloc[-1] - time[m].iloc[0]).days / 365.25, 1e-9)
    s = pd.Series(seg, index=pd.DatetimeIndex(time[m]))
    daily = s.resample("1D").last().dropna()
    r = daily.pct_change().dropna()
    sharpe = float(r.mean() / r.std() * np.sqrt(252)) if len(r) > 5 and r.std() > 0 else 0.0
    peak = np.maximum.accumulate(seg)
    return dict(ret=float(seg[-1] - 1.0),
                cagr=float(seg[-1] ** (1 / years) - 1) if seg[-1] > 0 else -1.0,
                sharpe=sharpe, dd=float((seg / peak - 1).min()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--resample", default="", choices=["", "H4", "D1"])
    ap.add_argument("--deadband", type=float, default=0.10)
    ap.add_argument("--max-adds", type=int, default=99)
    ap.add_argument("--add-stop", type=float, default=1.5)
    ap.add_argument("--touch", type=float, default=0.35)
    ap.add_argument("--min-gap", type=int, default=3)
    ap.add_argument("--invalidation", type=float, default=0.0)
    ap.add_argument("--cost-pts", type=float, default=12.0)
    ap.add_argument("--norm", action="store_true",
                    help="size each add at 1/max_adds so peak exposure stays <= 1x")
    ap.add_argument("--precursor", action="store_true",
                    help="exit the whole book when the direction warns it is turning")
    ap.add_argument("--pre-slope", type=float, default=0.10)
    ap.add_argument("--pre-dist", type=float, default=1.0)
    ap.add_argument("--chunk-run", type=int, default=5,
                    help="consecutive same-direction closes that count as exhaustion")
    ap.add_argument("--chunk-dist", type=float, default=1.5,
                    help="stretch from EMA21 in ATR that counts as 'into strength'")
    ap.add_argument("--chunk-rsi", type=float, default=0.0,
                    help="also exit a long when RSI9 exceeds this (short below 100-x)")
    ap.add_argument("--core-w", type=float, default=0.5,
                    help="weight of the core unit in core_sat mode (rest goes to the satellite)")
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
        df = (df.set_index("time").resample(rule)
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
    e = df["ema"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    time = df["time"]
    n = len(df)

    point = 0.001 if sym.endswith("JPY") else 0.00001
    cost_px = args.cost_pts * point

    slope3 = np.zeros(n)
    slope3[3:] = (e[3:] - e[:-3]) / np.maximum(atr[3:], 1e-12)
    reg = regime_series(slope3, args.deadband)
    legs = legs_of(reg)

    if args.precursor:
        warn_up, warn_down = precursor_exit(df, df["rsi"].to_numpy(float),
                                            slope3, args.pre_slope, args.pre_dist)
    else:
        warn_up = warn_down = None

    dist_a = np.abs(close - e) / np.maximum(atr, 1e-12)
    body_a = np.abs(close - df["open"].to_numpy(float))
    run_up = np.zeros(n, int)
    run_dn = np.zeros(n, int)
    for i in range(1, n):
        run_up[i] = run_up[i - 1] + 1 if close[i] > close[i - 1] else 0
        run_dn[i] = run_dn[i - 1] + 1 if close[i] < close[i - 1] else 0
    ctx = dict(dist=dist_a, body=body_a, run_up=run_up, run_dn=run_dn,
               run=args.chunk_run, chunk_dist=args.chunk_dist,
               rsi=df["rsi"].to_numpy(float), chunk_rsi=args.chunk_rsi,
               w_core=args.core_w, w_sat=1.0 - args.core_w)

    t0, t1 = time.iloc[0], time.iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80
    bounds = {"design": (t0, edge1), "validation": (edge1, edge2), "oos": (edge2, t1)}

    p0 = float(close[0])
    avail = sum(abs(close[b] - close[a]) / p0 for a, b in legs)

    print(f"=== pyramid inside EMA{EMA} direction  {sym} {tf} ===")
    print(f"bars {n}  {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}   "
          f"deadband {args.deadband:.2f} ATR   cost {args.cost_pts:.0f} pts/unit")
    print(f"direction legs {len(legs)}   median leg "
          f"{int(np.median([b - a + 1 for a, b in legs]))} bars   "
          f"available move (sum of legs) {avail * 100:.0f}% of price")
    print("")

    results = {}
    for mode in ("single", "retest", "chunk", "core_sat", "pullback", "breakout"):
        recs = []
        for a, b in legs:
            units = run_leg(df, a, b, min(b + 1, n - 1), int(reg[a]), mode,
                            args.max_adds, args.add_stop, args.touch,
                            args.min_gap, args.invalidation, cost_px,
                            warn=(warn_up if reg[a] > 0 else warn_down)
                            if args.precursor else None, ctx=ctx)
            recs.append(dict(start=a, end=b, reg=int(reg[a]), units=units,
                             avail=abs(close[b] - close[a]) / p0))
        w = (1.0 / max(1, args.max_adds)) if (args.norm and mode != "single") else 1.0
        eq = book_curve(df, recs, p0, cost_px, w)
        pnl = float(eq[-1] - 1.0)
        nunits = sum(len(r["units"]) for r in recs)
        results[mode] = dict(recs=recs, eq=eq, pnl=pnl, units=nunits)
        print(f"--- {mode:<9} units {nunits:>5}  "
              f"adds/leg {nunits / len(legs):>4.1f}  "
              f"peak exposure {max_exposure(recs, n)}x  "
              f"total {pnl * 100:>+7.1f}%  capture {100 * pnl / avail if avail else 0:>5.1f}%")
        for sp in SPLITS:
            m = seg_metrics(time, eq, *bounds[sp])
            print(f"      {sp:<11} return {m['ret'] * 100:>+7.1f}%  "
                  f"CAGR {m['cagr'] * 100:>+6.2f}%  Sharpe {m['sharpe']:>5.2f}  "
                  f"maxDD {m['dd'] * 100:>6.1f}%")
        print("")

    # ---------------------------------------------------- adds grid
    print("--- how many adds are actually worth taking (retest mode) ---")
    print(f"{'max_adds':<10}{'units':>7}{'adds/leg':>10}{'peak':>6}{'full%':>9}{'capture%':>10}{'Sharpe':>9}{'maxDD%':>9}")
    print("-" * 70)
    grid = []
    for ma in (1, 2, 3, 5, 10, 99):
        recs = []
        for a, b in legs:
            units = run_leg(df, a, b, min(b + 1, n - 1), int(reg[a]), "retest", ma,
                            args.add_stop, args.touch, args.min_gap,
                            args.invalidation, cost_px)
            recs.append(dict(start=a, end=b, reg=int(reg[a]), units=units,
                             avail=abs(close[b] - close[a]) / p0))
        w = (1.0 / ma) if args.norm else 1.0
        eq = book_curve(df, recs, p0, cost_px, w)
        m = seg_metrics(time, eq, t0, t1)
        nu = sum(len(r["units"]) for r in recs)
        pnl = float(eq[-1] - 1.0)
        print(f"{ma:<10}{nu:>7}{nu / len(legs):>10.1f}{max_exposure(recs, n):>5}x"
              f"{pnl * 100:>+9.1f}"
              f"{100 * pnl / avail if avail else 0:>9.1f}%{m['sharpe']:>9.2f}{m['dd'] * 100:>9.1f}")
        grid.append((ma, eq))

    # publish the best-on-design variant
    best = None
    for ma, eq in grid:
        m = seg_metrics(time, eq, *bounds["design"])
        if best is None or m["sharpe"] > best[1]:
            best = (ma, m["sharpe"], eq)
    ma_best = best[0]
    print("")
    print(f"max_adds picked on design Sharpe: {ma_best}")
    for sp in SPLITS:
        m = seg_metrics(time, best[2], *bounds[sp])
        print(f"  {sp:<11} return {m['ret'] * 100:>+7.1f}%  CAGR {m['cagr'] * 100:>+6.2f}%  "
              f"Sharpe {m['sharpe']:>5.2f}  maxDD {m['dd'] * 100:>6.1f}%")

    s = pd.Series(np.diff(np.r_[1.0, best[2]]) / np.r_[1.0, best[2]][:-1],
                  index=pd.DatetimeIndex(time)).dropna()
    publish_results(f"ema_pyramid_{sym}_{tf}", s, last_months=12, last_days=21)

    out = RESULTS / f"ema_pyramid_{sym}_{tf}.csv"
    pd.DataFrame({"time": time, "close": close, "regime": reg,
                  "eq_single": results["single"]["eq"],
                  "eq_retest": results["retest"]["eq"],
                  "eq_best": best[2]}).to_csv(out, index=False)
    print("")
    print(f"wrote {out}")
    print("")
    print("Adds are only worth taking if total and capture both rise versus")
    print("'single' on validation AND oos. More units alone is not an edge -")
    print("each unit pays the spread.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
