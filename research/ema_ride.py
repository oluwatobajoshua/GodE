"""Extract every "clean ride" in the template's own terms, then measure how much
of each move a rule set actually captures.

Definition (matches how the chart is read by eye):

    A ride is a run of H1 bars where price stays on one side of EMA21.
    It is CLEAN when
        - it lasts at least --min-bars bars, and
        - price does not dip back through EMA21 on more than --clean-max
          of those bars (intrabar wick counted).

For every clean ride we record the move that was actually available
(entry close -> last close of the run, in ATR) and then simulate several
entry / management / exit rule sets bar by bar. The gap between what was
available and what a rule captured is the money left on the table.

Everything is split chronologically:
    design       first 60%   (where a rule may be chosen)
    validation   next 20%    (does it survive on unseen bars?)
    oos          last 20%    (final read; never used for selection)

Outputs
    research/results/ema_ride_segments.csv   one row per ride
    research/results/ema_ride_rules.csv      rule x split metrics
    research/results/ema_ride_<rule>_monthly.csv / _daily.csv

Usage
    .\\.venv\\Scripts\\python.exe research\\ema_ride.py --symbol EURUSD --tf H1
    .\\.venv\\Scripts\\python.exe research\\ema_ride.py --symbol EURUSD --tf H1 --min-bars 8
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from period_returns import publish_results  # noqa: E402

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"

EMA_PERIOD = 21
ATR_PERIOD = 14


# ------------------------------------------------------------------ helpers
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


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


@dataclass
class Ride:
    side: int
    i0: int
    i1: int
    entry_time: pd.Timestamp
    entry: float
    atr: float
    bars: int
    violations: int
    available_atr: float
    mfe_atr: float
    mae_atr: float
    split: str = ""

    @property
    def violation_ratio(self) -> float:
        return self.violations / max(1, self.bars)


@dataclass
class Rule:
    name: str
    entry: str          # flip | retest | limit
    stop_atr: float
    mode: str           # fixed | ema | ema_be | partial
    tp_atr: float = 0.0
    retest_window: int = 12


RULES = [
    Rule("flip_fixed_1_2", "flip", 1.0, "fixed", tp_atr=2.0),
    Rule("flip_ema_exit", "flip", 1.0, "ema"),
    Rule("flip_ema_exit_be", "flip", 1.5, "ema_be"),
    Rule("flip_partial_trail", "flip", 1.0, "partial", tp_atr=1.0),
    Rule("retest_ema_exit", "retest", 1.0, "ema"),
    Rule("retest_partial_trail", "retest", 1.0, "partial", tp_atr=1.0),
    Rule("limit_ema_exit", "limit", 1.0, "ema"),
]


# ------------------------------------------------------------------ rides
def find_rides(df: pd.DataFrame, ema_col: str, min_bars: int, clean_max: float) -> list[Ride]:
    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    e = df[ema_col].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    t = df["time"].to_numpy()

    side = np.where(close > e, 1, np.where(close < e, -1, 0))
    # carry the previous side through exact-touch bars
    for i in range(1, len(side)):
        if side[i] == 0:
            side[i] = side[i - 1]

    rides: list[Ride] = []
    i = 1
    n = len(df)
    while i < n:
        if side[i] == 0 or np.isnan(atr[i]) or atr[i] <= 0:
            i += 1
            continue
        s = int(side[i])
        j = i
        while j + 1 < n and side[j + 1] == s:
            j += 1

        bars = j - i + 1
        if bars >= min_bars:
            viol = 0
            for k in range(i, j + 1):
                if s > 0 and low[k] < e[k]:
                    viol += 1
                elif s < 0 and high[k] > e[k]:
                    viol += 1
            ratio = viol / bars
            if ratio <= clean_max:
                entry = float(close[i])
                a = float(atr[i])
                avail = (float(close[j]) - entry) * s / a
                if s > 0:
                    mfe = (float(high[i : j + 1].max()) - entry) / a
                    mae = (entry - float(low[i : j + 1].min())) / a
                else:
                    mfe = (entry - float(low[i : j + 1].min())) / a
                    mae = (float(high[i : j + 1].max()) - entry) / a
                rides.append(
                    Ride(
                        side=s, i0=i, i1=j,
                        entry_time=pd.Timestamp(t[i]),
                        entry=entry, atr=a, bars=bars,
                        violations=viol, available_atr=avail,
                        mfe_atr=mfe, mae_atr=mae,
                    )
                )
        i = j + 1
    return rides


# ------------------------------------------------------------------ simulation
def simulate(df: pd.DataFrame, r: Ride, rule: Rule, cost_atr: float) -> float | None:
    """Return realised P&L in ATR units (1 ATR = 1R) or None if no fill."""
    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    e = df["ema"].to_numpy(float)

    s = r.side
    atr = r.atr
    i0 = r.i0

    # --- entry
    entry = r.entry
    start = i0 + 1
    if rule.entry == "retest":
        filled = False
        limit = min(i0 + rule.retest_window, r.i1)
        for k in range(i0 + 1, limit + 1):
            touched = (low[k] <= e[k]) if s > 0 else (high[k] >= e[k])
            holds = (close[k] > e[k]) if s > 0 else (close[k] < e[k])
            if touched and holds:
                entry = float(close[k])
                start = k + 1
                filled = True
                break
        if not filled:
            return None
    elif rule.entry == "limit":
        filled = False
        limit = min(i0 + rule.retest_window, r.i1)
        for k in range(i0 + 1, limit + 1):
            touched = (low[k] <= e[k]) if s > 0 else (high[k] >= e[k])
            if touched:
                entry = float(e[k])
                start = k + 1
                filled = True
                break
        if not filled:
            return None

    stop = entry - s * rule.stop_atr * atr
    tp = entry + s * rule.tp_atr * atr if rule.tp_atr > 0 else None
    moved_be = False
    half_done = False
    pnl = 0.0

    for k in range(start, r.i1 + 1):
        # --- stop first (conservative when both levels are inside one bar)
        hit_stop = (low[k] <= stop) if s > 0 else (high[k] >= stop)
        if hit_stop:
            pnl += (stop - entry) * s / atr
            if rule.mode == "partial" and not half_done:
                pass  # half never reached target; whole position stopped
            return pnl - cost_atr

        # --- partial take
        if rule.mode == "partial" and not half_done and tp is not None:
            hit_tp = (high[k] >= tp) if s > 0 else (low[k] <= tp)
            if hit_tp:
                pnl += 0.5 * (tp - entry) * s / atr
                half_done = True
                # move stop to breakeven on the remainder
                stop = entry
                moved_be = True
                continue

        # --- fixed target for the remaining position
        if rule.mode == "fixed" and tp is not None:
            hit_tp = (high[k] >= tp) if s > 0 else (low[k] <= tp)
            if hit_tp:
                pnl += (tp - entry) * s / atr
                return pnl - cost_atr

        # --- breakeven move
        if rule.mode == "ema_be" and not moved_be:
            if (close[k] - entry) * s >= 1.0 * atr:
                stop = entry
                moved_be = True

        # --- EMA cross exit (the ride-break rule)
        if rule.mode in ("ema", "ema_be", "partial"):
            broken = (close[k] < e[k]) if s > 0 else (close[k] > e[k])
            if broken:
                frac = 0.5 if (rule.mode == "partial" and half_done) else 1.0
                pnl += frac * (float(close[k]) - entry) * s / atr
                return pnl - cost_atr

    # ride ended without an exit signal: close at the last bar
    frac = 0.5 if (rule.mode == "partial" and half_done) else 1.0
    pnl += frac * (float(close[r.i1]) - entry) * s / atr
    return pnl - cost_atr


# ------------------------------------------------------------------ metrics
def metrics(pnls: list[float]) -> dict:
    if not pnls:
        return dict(n=0, win=0.0, avg=0.0, total=0.0, pf=0.0, dd=0.0)
    a = np.array(pnls, float)
    wins = a[a > 0].sum()
    losses = -a[a < 0].sum()
    eq = np.cumsum(a)
    peak = np.maximum.accumulate(eq)
    dd = float((eq - peak).min()) if len(eq) else 0.0
    return dict(
        n=len(a),
        win=float((a > 0).mean() * 100.0),
        avg=float(a.mean()),
        total=float(a.sum()),
        pf=float(wins / losses) if losses > 0 else float("inf"),
        dd=dd,
    )


def split_of(ts: pd.Timestamp, edges: tuple[pd.Timestamp, pd.Timestamp]) -> str:
    if ts < edges[0]:
        return "design"
    if ts < edges[1]:
        return "validation"
    return "oos"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--min-bars", type=int, default=6)
    ap.add_argument("--clean-max", type=float, default=0.25)
    ap.add_argument("--cost-pts", type=float, default=12.0,
                    help="round-trip cost in price points (spread + slippage)")
    args = ap.parse_args()

    sym, tf = args.symbol.upper(), args.tf.upper()
    path = DATA / f"{sym}_{tf}.csv"
    if not path.exists():
        raise SystemExit(f"missing data: {path}")

    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)
    df["ema"] = ema(df["close"], EMA_PERIOD)
    df["atr"] = wilder_atr(df, ATR_PERIOD)
    df = df.dropna(subset=["ema", "atr"]).reset_index(drop=True)

    point = 0.001 if sym.endswith("JPY") else 0.00001
    med_atr = float(df["atr"].median())
    cost_atr = args.cost_pts * point / med_atr if med_atr > 0 else 0.0

    rides = find_rides(df, "ema", args.min_bars, args.clean_max)
    if not rides:
        print(f"[{sym} {tf}] no clean rides found with min_bars={args.min_bars} "
              f"clean_max={args.clean_max}")
        return 1

    t0, t1 = df["time"].iloc[0], df["time"].iloc[-1]
    span = t1 - t0
    edge1 = t0 + span * 0.60
    edge2 = t0 + span * 0.80
    for r in rides:
        r.split = split_of(r.entry_time, (edge1, edge2))

    print(f"=== EMA{EMA_PERIOD} ride extraction  {sym} {tf} ===")
    print("MODE: ORACLE. Rides are selected AFTER the fact (they lasted >=")
    print(f"      {args.min_bars} bars and stayed clean), so every number below is a")
    print("      CEILING on what is capturable, not a tradable result.")
    print("      The tradable version is research/ema_cross_harvest.py.")
    print("")
    print(f"bars {len(df)}  {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}")
    print(f"clean rides {len(rides)}  (min {args.min_bars} bars, "
          f"violations <= {args.clean_max:.0%})")
    print(f"median ATR {med_atr:.5f}  round-trip cost {args.cost_pts:.0f} pts "
          f"= {cost_atr:.2f} ATR")

    seg = pd.DataFrame(
        [
            dict(
                symbol=sym, tf=tf, side=r.side, split=r.split,
                entry_time=r.entry_time, bars=r.bars,
                violations=r.violations,
                violation_ratio=round(r.violation_ratio, 3),
                entry=r.entry, atr=r.atr,
                available_atr=round(r.available_atr, 3),
                mfe_atr=round(r.mfe_atr, 3),
                mae_atr=round(r.mae_atr, 3),
            )
            for r in rides
        ]
    )
    RESULTS.mkdir(parents=True, exist_ok=True)
    seg_path = RESULTS / f"ema_ride_segments_{sym}_{tf}.csv"
    seg.to_csv(seg_path, index=False)

    print("")
    print("--- available move per ride (ATR units) ---")
    for sp in ("design", "validation", "oos"):
        sub = seg[seg.split == sp]
        if sub.empty:
            continue
        print(f"  {sp:<11} rides {len(sub):>4}   bars avg {sub.bars.mean():5.1f}   "
              f"available sum {sub.available_atr.sum():7.1f} ATR   "
              f"median {sub.available_atr.median():5.2f} ATR")

    rows = []
    trade_returns: dict[str, pd.Series] = {}
    for rule in RULES:
        pnls: dict[str, list[float]] = {"design": [], "validation": [], "oos": []}
        times: dict[str, list[pd.Timestamp]] = {"design": [], "validation": [], "oos": []}
        for r in rides:
            p = simulate(df, r, rule, cost_atr)
            if p is None:
                continue
            pnls[r.split].append(p)
            times[r.split].append(r.entry_time)

        avail = {sp: float(seg[seg.split == sp].available_atr.sum()) for sp in pnls}
        for sp in ("design", "validation", "oos"):
            m = metrics(pnls[sp])
            cap = (m["total"] / avail[sp]) if avail[sp] > 0 else 0.0
            rows.append(
                dict(
                    symbol=sym, tf=tf, rule=rule.name, entry=rule.entry,
                    mode=rule.mode, stop_atr=rule.stop_atr, tp_atr=rule.tp_atr,
                    split=sp, trades=m["n"], win_pct=round(m["win"], 1),
                    avg_r=round(m["avg"], 3), total_r=round(m["total"], 2),
                    pf=round(m["pf"], 2) if m["pf"] != float("inf") else 99.99,
                    max_dd_r=round(m["dd"], 2),
                    available_r=round(avail[sp], 1),
                    capture_pct=round(cap * 100.0, 1),
                )
            )
        # full-period trade returns for the monthly/daily proof
        all_p = pnls["design"] + pnls["validation"] + pnls["oos"]
        all_t = times["design"] + times["validation"] + times["oos"]
        if all_p:
            trade_returns[rule.name] = pd.Series(
                np.array(all_p) * 0.01, index=pd.DatetimeIndex(all_t)
            ).sort_index()

    rules_df = pd.DataFrame(rows)
    rules_path = RESULTS / f"ema_ride_rules_{sym}_{tf}.csv"
    rules_df.to_csv(rules_path, index=False)

    print("")
    print("--- rule results by chronological split (R = 1 ATR of risk) ---")
    print("    ORACLE: conditioned on the ride being clean and long enough. Ceiling only.")
    hdr = f"{'rule':<24}{'split':<12}{'n':>5}{'win%':>7}{'avgR':>8}{'totalR':>9}{'PF':>7}{'DD R':>8}{'capture':>9}"
    print(hdr)
    print("-" * len(hdr))
    for rule in RULES:
        sub = rules_df[rules_df.rule == rule.name]
        for _, row in sub.iterrows():
            print(f"{row.rule:<24}{row.split:<12}{int(row.trades):>5}{row.win_pct:>7.1f}"
                  f"{row.avg_r:>8.2f}{row.total_r:>9.1f}{row.pf:>7.2f}"
                  f"{row.max_dd_r:>8.1f}{row.capture_pct:>8.1f}%")
        print("")

    # pick the rule that is best on DESIGN only, then report its validation/OOS
    design = rules_df[rules_df.split == "design"].sort_values("total_r", ascending=False)
    if not design.empty:
        best = design.iloc[0].rule
        print(f"--- selected on design only: {best} ---")
        sub = rules_df[rules_df.rule == best].set_index("split")
        for sp in ("design", "validation", "oos"):
            if sp in sub.index:
                r = sub.loc[sp]
                print(f"  {sp:<11} n {int(r.trades):>4}  totalR {r.total_r:>7.1f}  "
                      f"PF {r.pf:>5.2f}  capture {r.capture_pct:>5.1f}%  "
                      f"left on table {100 - r.capture_pct:>5.1f}%")
        if best in trade_returns:
            print("")
            print(f"--- monthly/daily proof for {best} (risk 1% per trade) ---")
            publish_results(f"ema_ride_{best}_{sym}_{tf}", trade_returns[best],
                            last_months=12, last_days=21)

    print("")
    print(f"wrote {seg_path}")
    print(f"wrote {rules_path}")
    print("")
    print("Reminder: nothing here is deployable until a rule is positive in design,")
    print("validation AND oos after costs. If it is not, the negative result stays.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
