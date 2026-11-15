#!/usr/bin/env python3
"""Make the RSI band rule survive its own cost: real spread, fewer round trips.

The measured problem: on EURUSD H1 the rule is gross-positive in all three splits
(+8.3 / +2.8 / +2.6%, Sharpe 0.56) and takes 673 round trips. At 12 points all-in
per round trip that is about 1% of price per year, which cancels the entire gross
edge. Break-even was ~11 points.

So there are only two honest levers, and this script tests both:

  A. CHARGE THE REAL COST. Not a flat 12 points - the actual spread on the bar,
     plus commission, plus slippage, each side. `research/data/mt5/*.csv` carries
     the quoted spread per bar. It is sparse (median 0 on EURUSD, p95 = 50), so
     the model floors it at a realistic minimum rather than believing the zeros.

  B. TAKE FEWER, BETTER TRADES. Four independent knobs:
       --cooldown N     bars to wait after an exit before re-entering
       --min-hold M     bars a trade must last before the RSI-high exit may fire
                        (a slope flip still exits immediately - that is risk)
       --entry-margin D the RSI must cross the mid band by D, not just touch it
       --gate-pct P     skip the entry when the spread is above the P-th
                        percentile of the last 500 bars (avoids news and rollover)

Selection discipline, because sweeping four knobs is a search and a search will
always find something:
  * configs are ranked by the WORST of design and validation - never by OOS
  * OOS is printed after the choice and is not used to pick anything
  * the number of configs tried is printed, so the multiple-comparison risk is
    visible rather than hidden
  * a config is only called a survivor if all THREE splits are positive

The last point is not enough on its own, and this script measures why. Ranking 100
configs on the worst of design+validation selects the EXTREME of that surface, and
the extreme is the least likely member to generalise: on the extended tape the
EURUSD winner has OOS -1.35% while the equal-weight average of the whole family is
+0.75%. So `--sweep` also reports and publishes that ensemble
(`rsi_band_ens_<SYM>_<TF>`), which removes the selection step entirely. The book in
`portfolio_combine.py` trades the ensemble, not the winner.

The RSI levels are arguments, not constants, so a pair can be tested with its own
certified set (`--rsi-period/--mid/--hi/--lo`; defaults are the EURUSD 9/50/75/25).
GBPUSD H1 with 9/45/65/35 and `--deadband 0.2` reproduces its documented
+3.37 / +1.81 / +0.05%. `--from-date/--to-date` pin the tape so a result can be
reproduced after the tape grows.

Usage
    .\\.venv\\Scripts\\python.exe research/rsi_band_net.py --symbol EURUSD --tf H1
    .\\.venv\\Scripts\\python.exe research/rsi_band_net.py --symbol EURUSD --sweep
    .\\.venv\\Scripts\\python.exe research/rsi_band_net.py --symbol EURUSD --comm-pts 7 --slip-pts 1
"""

from __future__ import annotations

import argparse
import itertools
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


def simulate(rsi, mid, hi, lo, trend, close, spread_ok, cost_side, n, cfg,
             open_px=None, low_px=None, high_px=None, ema_px=None,
             structure_filter=False):
    """One position at a time, with cooldown / min-hold / entry margin / spread gate."""
    pos = np.zeros(n, dtype=int)
    state = 0
    entry_bar = -10 ** 9
    exit_bar = -10 ** 9
    cooldown = cfg["cooldown"]
    min_hold = cfg["min_hold"]
    margin = cfg["margin"]

    # --- market structure tracking ---
    # red_open_ema_above: open of most recent red candle where open > EMA21
    # green_close_ema_below: close of most recent green candle where close < EMA21
    red_open_ema_above = np.nan
    green_close_ema_below = np.nan

    for t in range(1, n):
        r, rp = rsi[t], rsi[t - 1]
        m, mp, h, l = mid[t], mid[t - 1], hi[t], lo[t]
        if not (np.isfinite(r) and np.isfinite(m) and np.isfinite(h)
                and np.isfinite(l) and np.isfinite(rp) and np.isfinite(mp)):
            pos[t] = state
            continue

        #--- update structure trackers on every bar ---
        if structure_filter and open_px is not None and ema_px is not None:
            o, c, e = open_px[t], close[t], ema_px[t]
            is_red = c < o
            is_green = c > o
            if is_red and o > e:
                red_open_ema_above = o
            if is_green and c < e:
                green_close_ema_below = c

        if state > 0:
            held = t - entry_bar
            if trend[t] < 0:                      # risk exit, never delayed
                state = 0
                exit_bar = t
            elif r > h and held >= min_hold:
                state = 0
                exit_bar = t
            elif structure_filter and not np.isnan(red_open_ema_above):
                # long structure break: green candle failed to reclaim above
                # prior red open above EMA21 -> sellers stepping in
                curr_green = close[t] > open_px[t]
                if curr_green and close[t] <= red_open_ema_above:
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
            elif structure_filter and not np.isnan(green_close_ema_below):
                # short structure break: red candle failed to break below
                # prior green close below EMA21 -> buyers stepping in
                curr_red = close[t] < open_px[t]
                if curr_red and close[t] >= green_close_ema_below:
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
    return pos


def returns(pos, close, cost_side):
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--resample", default="", choices=["", "H4", "D1"])
    ap.add_argument("--comm-pts", type=float, default=7.0, help="round-trip commission, points")
    ap.add_argument("--slip-pts", type=float, default=1.0, help="slippage per side, points")
    ap.add_argument("--floor-pts", type=float, default=1.0, help="minimum quoted spread, points")
    ap.add_argument("--gate-pct", type=float, default=90.0)
    ap.add_argument("--deadband", type=float, default=0.10)
    ap.add_argument("--rsi-period", type=int, default=RSI_P,
                    help="RSI lookback (default is the EURUSD 9)")
    ap.add_argument("--mid", type=float, default=50.0, help="RSI mid band")
    ap.add_argument("--hi", type=float, default=75.0, help="RSI overbought exit")
    ap.add_argument("--lo", type=float, default=25.0, help="RSI oversold exit")
    ap.add_argument("--sweep", action="store_true", help="sweep the four knobs")
    ap.add_argument("--cooldown", type=int, default=0)
    ap.add_argument("--min-hold", type=int, default=0)
    ap.add_argument("--margin", type=float, default=0.0)
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--structure-filter", action="store_true",
                    help="enable market structure break exits")
    ap.add_argument("--from-date", default="", help="drop bars before this date")
    ap.add_argument("--to-date", default="", help="drop bars after this date")
    ap.add_argument("--data-dir", default="",
                    help="override the tape directory (e.g. research/data/histdata)")
    args = ap.parse_args()

    sym, tf = args.symbol.upper(), args.tf.upper()
    data_dir = Path(args.data_dir) if args.data_dir else DATA
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    path = data_dir / f"{sym}_{tf}.csv"
    if not path.exists():
        raise SystemExit(f"missing data: {path}")

    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)
    if args.from_date:
        df = df[df["time"] >= pd.Timestamp(args.from_date)].reset_index(drop=True)
    if args.to_date:
        df = df[df["time"] <= pd.Timestamp(args.to_date)].reset_index(drop=True)
    if args.resample:
        rule = {"H4": "4h", "D1": "1D"}[args.resample]
        df = (df.set_index("time").resample(rule)
                .agg({"open": "first", "high": "max", "low": "min",
                      "close": "last", "tick_volume": "sum", "spread": "max"})
                .dropna(subset=["open"]).reset_index())
        tf = args.resample

    df["ema"] = ema(df["close"], EMA)
    df["rsi"] = wilder_rsi(df["close"], args.rsi_period)
    df["atr"] = wilder_atr(df, ATR_P)
    df = df.dropna(subset=["ema", "rsi", "atr"]).reset_index(drop=True)

    close = df["close"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    spread = df["spread"].astype(float).to_numpy()
    time = df["time"]
    n = len(df)

    #--- point size per symbol family: the cost model is expressed in points, so a
    #--- wrong point silently multiplies every cost by 10/100. BTCUSD quotes in
    #--- 0.001 (broker SYMBOL_POINT), metals in 0.01, JPY crosses in 0.001.
    _SYM_POINT = {"BTCUSD": 0.001, "XAUUSD": 0.01, "XAGUSD": 0.001,
                  "DXYUSD": 0.001, "VIXUSD": 0.001}
    point = _SYM_POINT.get(sym.upper(), 0.001 if sym.endswith("JPY") else 0.00001)

    slope3 = np.zeros(n)
    slope3[3:] = (e[3:] - e[:-3]) / np.maximum(atr[3:], 1e-12)
    trend = np.where(slope3 > args.deadband, 1,
                     np.where(slope3 < -args.deadband, -1, 0))

    # Levels default to the EURUSD set (50/75/25). Pairs that were certified with
    # their own parameters pass them explicitly; see AGENTS.md.
    mid = np.full(n, args.mid)
    hi = np.full(n, args.hi)
    lo = np.full(n, args.lo)

    # ---- real cost per side, in points, floored so the zeros are not believed
    spread_eff = np.maximum(spread, args.floor_pts)
    cost_side_pts = spread_eff / 2.0 + args.slip_pts + args.comm_pts / 2.0
    cost_side = cost_side_pts * point

    # ---- spread gate: is the spread inside the trailing p-th percentile?
    q = pd.Series(spread).rolling(500, min_periods=100).quantile(args.gate_pct / 100.0)
    spread_ok = (spread <= q.to_numpy()) | ~np.isfinite(q.to_numpy())

    t0, t1 = time.iloc[0], time.iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80
    bounds = {"design": (t0, edge1), "validation": (edge1, edge2), "oos": (edge2, t1)}
    #--- bars per calendar year, per timeframe (252 trading days x bars/day).
    #--- Intraday entries are needed so the intraday timeframes can be swept at
    #--- all: the dict previously only held H1/H4/D1 and raised KeyError on M5.
    _BPY = {"M1": 1440 * 252, "M5": 288 * 252, "M15": 96 * 252, "M30": 48 * 252,
            "H1": 24 * 252, "H4": 6 * 252, "D1": 252}
    bars_per_year = _BPY[tf]

    print(f"=== RSI band rule, net of a real cost model  {sym} {tf} ===")
    print(f"bars {n}  {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}")
    print(f"cost: spread/2 (floored at {args.floor_pts:.1f} pts) + {args.slip_pts:.1f} pts "
          f"slippage + {args.comm_pts / 2:.1f} pts half-commission, per side")
    print(f"  -> median all-in round trip "
          f"{2 * np.median(cost_side_pts):.1f} pts, mean {2 * cost_side_pts.mean():.1f} pts, "
          f"p95 {2 * np.percentile(cost_side_pts, 95):.1f} pts")
    print(f"  spread gate: skip entry when spread > p{args.gate_pct:.0f} of last 500 bars "
          f"({100 * (1 - spread_ok.mean()):.1f}% of bars blocked)")
    print("")

    open_px = df["open"].to_numpy(float)
    high_px = df["high"].to_numpy(float)
    low_px = df["low"].to_numpy(float)
    e = df["ema"].to_numpy(float)

    def run(cfg):
        pos = simulate(rsi, mid, hi, lo, trend, close, spread_ok, cost_side, n, cfg,
                       open_px=open_px, high_px=high_px, low_px=low_px, ema_px=e,
                       structure_filter=args.structure_filter)
        return pos, returns(pos, close, cost_side)

    def split_stats(ret):
        out = {}
        for sp in SPLITS:
            a, b = bounds[sp]
            mask = ((time >= a) & (time < b)).to_numpy()
            out[sp] = stats(ret[mask], bars_per_year)
        return out

    base_cfg = dict(cooldown=0, min_hold=0, margin=0.0)
    pos0, ret0 = run(base_cfg)
    rt0 = int(np.abs(np.diff(pos0, prepend=0)).sum()) // 2
    s0 = split_stats(ret0)
    full0 = stats(ret0, bars_per_year)
    print("--- baseline (no knobs) at the real cost model ---")
    print(f"  round trips {rt0} ({rt0 / (len(time) / bars_per_year):.0f}/yr)")
    for sp in SPLITS:
        print(f"  {sp:<11} {s0[sp]['total'] * 100:>+7.2f}%  Sharpe {s0[sp]['sharpe']:>5.2f}  "
              f"maxDD {s0[sp]['dd'] * 100:>6.2f}%")
    print(f"  {'full':<11} {full0['total'] * 100:>+7.2f}%  Sharpe {full0['sharpe']:>5.2f}  "
          f"maxDD {full0['dd'] * 100:>6.2f}%")
    print("")

    if not args.sweep:
        cfg = dict(cooldown=args.cooldown, min_hold=args.min_hold, margin=args.margin)
        pos, ret = run(cfg)
        st = split_stats(ret)
        full = stats(ret, bars_per_year)
        rt = int(np.abs(np.diff(pos, prepend=0)).sum()) // 2
        print(f"--- single config: cooldown {cfg['cooldown']} min-hold {cfg['min_hold']} "
              f"margin {cfg['margin']:.0f}   round trips {rt} "
              f"({rt / (len(time) / bars_per_year):.0f}/yr) ---")
        for sp in SPLITS:
            print(f"  {sp:<11} {st[sp]['total'] * 100:>+7.2f}%  CAGR {st[sp]['cagr'] * 100:>+6.2f}%  "
                  f"Sharpe {st[sp]['sharpe']:>5.2f}  maxDD {st[sp]['dd'] * 100:>6.2f}%")
        print(f"  {'full':<11} {full['total'] * 100:>+7.2f}%  CAGR {full['cagr'] * 100:>+6.2f}%  "
              f"Sharpe {full['sharpe']:>5.2f}  maxDD {full['dd'] * 100:>6.2f}%")
        ok = all(st[sp]["total"] > 0 for sp in SPLITS)
        print(f"  -> {'POSITIVE in all three splits' if ok else 'NOT positive in all three splits'}")
        if not args.no_publish:
            s = pd.Series(ret, index=pd.DatetimeIndex(time))
            publish_results(f"rsi_band_net_{sym}_{tf}", s, append_to_bundle=False)
            print(f"published rsi_band_net_{sym}_{tf}")
        return 0

    # ---------------------------------------------------------------- sweep
    cooldowns = (0, 5, 10, 20, 40)
    min_holds = (0, 3, 6, 12, 24)
    margins = (0.0, 1.0, 2.0, 3.0)
    rows = []
    # The equal-weight ensemble of the whole config family: trading the average
    # instead of the design+validation winner removes the knob-selection step
    # entirely. If the concept is real the average should survive OOS even when
    # the best-in-sample member does not.
    ret_ens = np.zeros(n)
    n_cfg = 0
    for cd, mh, mg in itertools.product(cooldowns, min_holds, margins):
        cfg = dict(cooldown=cd, min_hold=mh, margin=mg)
        pos, ret = run(cfg)
        ret_ens += ret
        n_cfg += 1
        rt = int(np.abs(np.diff(pos, prepend=0)).sum()) // 2
        st = split_stats(ret)
        rows.append(dict(cooldown=cd, min_hold=mh, margin=mg, rt=rt,
                         design=st["design"]["total"] * 100,
                         validation=st["validation"]["total"] * 100,
                         oos=st["oos"]["total"] * 100,
                         worst_dv=min(st["design"]["total"], st["validation"]["total"]) * 100,
                         sharpe=st["oos"]["sharpe"], dd=st["oos"]["dd"] * 100))
    tbl = pd.DataFrame(rows).sort_values("worst_dv", ascending=False)
    print(f"--- swept {len(tbl)} configs; ranked by the WORST of design and validation ---")
    print(f"{'cd':>3}{'hold':>5}{'marg':>5}{'RT':>5}{'design':>8}{'valid':>7}"
          f"{'oos':>7}{'Sharpe':>7}{'DD':>7}")
    print("-" * 54)
    for _, r in tbl.head(15).iterrows():
        print(f"{r.cooldown:>3.0f}{r.min_hold:>5.0f}{r.margin:>5.1f}{r.rt:>5.0f}"
              f"{r.design:>8.2f}{r.validation:>7.2f}{r.oos:>7.2f}"
              f"{r.sharpe:>7.2f}{r.dd:>7.2f}")
    print("")

    surv = tbl[(tbl.design > 0) & (tbl.validation > 0) & (tbl.oos > 0)]
    print(f"--- configs positive in ALL THREE splits: {len(surv)} of {len(tbl)} ---")
    if len(surv):
        for _, r in surv.sort_values("worst_dv", ascending=False).head(10).iterrows():
            print(f"  cooldown {r.cooldown:>2.0f}  min-hold {r.min_hold:>2.0f}  "
                  f"margin {r.margin:.0f}  RT {r.rt:>3.0f}  "
                  f"design {r.design:>+6.2f}%  valid {r.validation:>+6.2f}%  "
                  f"oos {r.oos:>+6.2f}%")
    else:
        print("  none - no combination of the four knobs is positive in all three")
        print("  splits at this cost model. The gross edge is real but the cost")
        print("  still wins. Do not deploy; report it as a cost-limited result.")
    print("")

    # ---- the no-selection baseline: average of the whole family
    ret_ens /= max(n_cfg, 1)
    st_ens = split_stats(ret_ens)
    full_ens = stats(ret_ens, bars_per_year)
    print(f"--- equal-weight ensemble of all {n_cfg} configs (no knob selection) ---")
    for sp in SPLITS:
        print(f"  {sp:<11} {st_ens[sp]['total'] * 100:>+7.2f}%  "
              f"Sharpe {st_ens[sp]['sharpe']:>5.2f}  maxDD {st_ens[sp]['dd'] * 100:>6.2f}%")
    print(f"  {'full':<11} {full_ens['total'] * 100:>+7.2f}%  "
          f"Sharpe {full_ens['sharpe']:>5.2f}  maxDD {full_ens['dd'] * 100:>6.2f}%")
    print(f"  -> {'POSITIVE in all three splits' if all(st_ens[sp]['total'] > 0 for sp in SPLITS) else 'NOT positive in all three splits'}")
    print("")

    # ---- detail on the chosen config (worst-of-D/V, never chosen on OOS)
    best = tbl.iloc[0]
    cfg = dict(cooldown=int(best.cooldown), min_hold=int(best.min_hold),
               margin=float(best.margin))
    pos, ret = run(cfg)
    st = split_stats(ret)
    full = stats(ret, bars_per_year)
    print(f"--- chosen on design+validation only: cooldown {cfg['cooldown']} "
          f"min-hold {cfg['min_hold']} margin {cfg['margin']:.0f} ---")
    for sp in SPLITS:
        print(f"  {sp:<11} {st[sp]['total'] * 100:>+7.2f}%  CAGR {st[sp]['cagr'] * 100:>+6.2f}%  "
              f"Sharpe {st[sp]['sharpe']:>5.2f}  maxDD {st[sp]['dd'] * 100:>6.2f}%")
    print(f"  {'full':<11} {full['total'] * 100:>+7.2f}%  CAGR {full['cagr'] * 100:>+6.2f}%  "
          f"Sharpe {full['sharpe']:>5.2f}  maxDD {full['dd'] * 100:>6.2f}%")
    print("")

    print("--- per calendar year, chosen config (net) ---")
    print(f"{'year':<7}{'net':>9}{'RT':>6}")
    print("-" * 22)
    for y in sorted(set(time.dt.year)):
        mask = (time.dt.year == y).to_numpy(copy=True)
        if mask.sum() < 200:
            continue
        rty = int(np.abs(np.diff(pos[mask], prepend=0)).sum()) // 2
        print(f"{y:<7}{stats(ret[mask], bars_per_year)['total'] * 100:>9.2f}{rty:>6}")
    print("")

    if not args.no_publish:
        s = pd.Series(ret, index=pd.DatetimeIndex(time))
        publish_results(f"rsi_band_net_{sym}_{tf}", s, append_to_bundle=False)
        print(f"published rsi_band_net_{sym}_{tf}")
        publish_results(f"rsi_band_ens_{sym}_{tf}",
                        pd.Series(ret_ens, index=pd.DatetimeIndex(time)),
                        append_to_bundle=False)
        print(f"published rsi_band_ens_{sym}_{tf}")

    out = RESULTS / f"rsi_band_net_{sym}_{tf}.csv"
    RESULTS.mkdir(parents=True, exist_ok=True)
    tbl.to_csv(out, index=False)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
