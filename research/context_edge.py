"""Which background conditions separate good legs from bad legs?

The claim on the chart is that "nothing just happens in the market" - that good
moves and bad moves carry a background signature: the hour, the session, the
spread, the volatility regime, the state of the EMA stack, the bar before.

This script does not assume any of that. It labels every EMA21 direction leg as
good or bad, then measures the conditional base rate of each background variable
against that label, split into design / validation / out-of-sample.

Why this shape:

  * The label is the *leg outcome*, not a subjective read. A leg is the stretch
    of bars where the EMA21 slope keeps one sign. Its outcome is the move from
    the entry close to the exit close, measured in ATR so symbols are comparable.
  * Every feature is computed **at the entry bar only** (no lookahead). If a
    feature needs history it uses a trailing window that ends on the entry bar.
  * Each cell reports n, mean ATR and win rate *per split*. A cell that is
    positive overall but negative in one split is noise, and is marked as such.

The multiple-comparison problem is real and is stated in the output: roughly
8 features x 5 buckets = 40 cells are examined, so ~2 cells will look good by
chance at the 5% level. Only cells that are same-sign in all three splits with
n >= 30 each are treated as candidates, and even those are then re-tested as a
filter whose thresholds are chosen on the design split only.

Usage
    .\\.venv\\Scripts\\python.exe research\\context_edge.py --symbol EURUSD
    .\\.venv\\Scripts\\python.exe research\\context_edge.py --symbol USDJPY --resample D1
    .\\.venv\\Scripts\\python.exe research\\context_edge.py --symbol EURUSD --tf H1 --no-publish
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from ema_pyramid import (  # noqa: E402
    EMA, EMA_MID, SMMA,
    ema, smma, wilder_atr, wilder_rsi, regime_series, legs_of,
)
from period_returns import publish_results  # noqa: E402

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"
SPLITS = ("design", "validation", "oos")

SESSIONS = [
    ("asia", 0, 7),
    ("london", 7, 12),
    ("overlap", 12, 16),
    ("newyork", 16, 21),
    ("late", 21, 24),
]
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ----------------------------------------------------------------- helpers
def session_of(hour: int) -> str:
    for name, lo, hi in SESSIONS:
        if lo <= hour < hi:
            return name
    return "late"


def qbucket(s: pd.Series, k: int, names: list[str]) -> pd.Series:
    """Quartile buckets that survive heavy ties (spread is 0 most of the time)."""
    codes = pd.qcut(s, k, labels=False, duplicates="drop")
    return codes.map({i: names[i] for i in range(len(names))}).astype("object")


def bucket_stats(ep: pd.DataFrame, col: str, min_n: int = 30) -> pd.DataFrame:
    """Mean leg outcome per bucket, per split, plus a consistency flag."""
    rows = []
    for key, g in ep.groupby(col, observed=True):
        rec = dict(bucket=key, n=len(g), atr=g["pnl_atr"].mean(),
                   win=100.0 * (g["pnl_atr"] > 0).mean())
        signs = []
        for sp in SPLITS:
            sub = g[g.split == sp]
            rec[sp] = sub["pnl_atr"].mean() if len(sub) else np.nan
            rec[f"n_{sp}"] = len(sub)
            if len(sub) >= 15:
                signs.append(np.sign(rec[sp]))
        strong = [s for s in signs if s != 0]
        rec["consistent"] = (len(strong) == 3 and len(set(strong)) == 1
                             and rec["n"] >= min_n)
        rows.append(rec)
    out = pd.DataFrame(rows).sort_values("atr", ascending=False)
    return out


def show(title: str, tbl: pd.DataFrame, note: str = "") -> None:
    print(f"--- {title} ---")
    print(f"{'bucket':<22}{'n':>6}{'ATR':>8}{'win%':>7}"
          f"{'design':>9}{'valid':>9}{'oos':>9}  flag")
    print("-" * 80)
    for _, r in tbl.iterrows():
        flag = "OK" if r["consistent"] else ""
        print(f"{str(r['bucket']):<22}{int(r['n']):>6}{r['atr']:>8.2f}"
              f"{r['win']:>7.1f}{r['design']:>9.2f}{r['validation']:>9.2f}"
              f"{r['oos']:>9.2f}  {flag}")
    if note:
        print(note)
    print("")


# ----------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--resample", default="", choices=["", "H4", "D1"])
    ap.add_argument("--deadband", type=float, default=0.10)
    ap.add_argument("--cost-pts", type=float, default=12.0)
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--no-publish", action="store_true")
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
                      "close": "last", "tick_volume": "sum",
                      "spread": "median"})
                .dropna(subset=["open"]).reset_index())
        tf = args.resample

    df["ema"] = ema(df["close"], EMA)
    df["ema_mid"] = ema(df["close"], EMA_MID)
    df["smma"] = smma(df["close"], SMMA)
    df["atr"] = wilder_atr(df, 14)
    df["rsi"] = wilder_rsi(df["close"], 9)
    df = df.dropna(subset=["ema", "ema_mid", "smma", "atr", "rsi"]).reset_index(drop=True)

    close = df["close"].to_numpy(float)
    openp = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    em = df["ema_mid"].to_numpy(float)
    sm = df["smma"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    spread = df["spread"].to_numpy(float)
    vol = df["tick_volume"].to_numpy(float)
    time = df["time"]
    n = len(df)

    slope3 = np.zeros(n)
    slope3[3:] = (e[3:] - e[:-3]) / np.maximum(atr[3:], 1e-12)
    reg = regime_series(slope3, args.deadband)
    legs = legs_of(reg)

    point = 0.001 if sym.endswith("JPY") else 0.00001
    cost_px = args.cost_pts * point

    t0, t1 = time.iloc[0], time.iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80
    bounds = {"design": (t0, edge1), "validation": (edge1, edge2), "oos": (edge2, t1)}

    def split_of(ts):
        return "design" if ts < edge1 else ("validation" if ts < edge2 else "oos")

    # ---------------------------------------------------------- label the legs
    # trailing background measures, all ending on the entry bar
    atr_pct = atr / close * 10000.0
    atr_ratio = atr_pct / pd.Series(atr_pct).rolling(200, min_periods=50).mean().to_numpy()
    spread_ratio = spread / (pd.Series(spread).rolling(200, min_periods=50).mean().to_numpy() + 1.0)
    vol_ratio = vol / (pd.Series(vol).rolling(200, min_periods=50).mean().to_numpy() + 1.0)
    stack_gap = (e - em) / np.maximum(atr, 1e-12)
    stack_gap2 = (em - sm) / np.maximum(atr, 1e-12)

    rows = []
    prev_len = np.nan
    prev_atr = np.nan
    for k, (a, b) in enumerate(legs):
        if a < 220 or b + 1 >= n:
            continue
        side = int(reg[a])
        exit_i = min(b + 1, n - 1)
        pnl_atr = (close[exit_i] - close[a]) * side / atr[a]
        pnl_atr -= cost_px / atr[a]
        px_ret = (close[exit_i] - close[a]) * side / close[a] - cost_px / close[a]
        aligned = (np.sign(stack_gap[a]) == side and np.sign(stack_gap2[a]) == side)
        rows.append(dict(
            time=time.iloc[a], side=side, bars=b - a + 1,
            pnl_atr=float(pnl_atr), px_ret=float(px_ret),
            hour=int(time.iloc[a].hour),
            session=session_of(int(time.iloc[a].hour)),
            dow=DOW[int(time.iloc[a].dayofweek)],
            month=int(time.iloc[a].month),
            spread=float(spread[a]),
            spread_rel=float(spread_ratio[a]),
            atr_pct=float(atr_pct[a]),
            atr_rel=float(atr_ratio[a]),
            vol_rel=float(vol_ratio[a]),
            dist=float((close[a] - e[a]) * side / atr[a]),
            slope=float(slope3[a] * side),
            stack=float(stack_gap[a] * side),
            aligned=bool(aligned),
            prior_len=float(prev_len) if np.isfinite(prev_len) else np.nan,
            prior_atr=float(prev_atr) if np.isfinite(prev_atr) else np.nan,
            gap=float((openp[a] - close[a - 1]) / atr[a]),
            rng5=float((high[a - 4:a + 1].max() - low[a - 4:a + 1].min()) / atr[a]),
            rsi=float(df["rsi"].iloc[a]),
        ))
        prev_len = b - a + 1
        prev_atr = pnl_atr

    ep = pd.DataFrame(rows)
    ep["split"] = ep["time"].apply(split_of)
    ep["hour_band"] = pd.cut(ep["hour"], [-1, 2, 6, 9, 12, 15, 18, 21, 23],
                             labels=["00-02", "03-06", "07-09", "10-12",
                                     "13-15", "16-18", "19-21", "22-23"])
    ep["atr_bucket"] = qbucket(ep["atr_rel"], 4, ["vol low", "vol 2", "vol 3", "vol high"])
    ep["spread_bucket"] = qbucket(ep["spread_rel"], 4, ["spr low", "spr 2", "spr 3", "spr high"])
    ep["dist_bucket"] = qbucket(ep["dist"], 4, ["near", "dist 2", "dist 3", "far"])
    ep["slope_bucket"] = qbucket(ep["slope"], 4, ["flat", "slope 2", "slope 3", "steep"])
    ep["prior_bucket"] = qbucket(ep["prior_atr"], 4, ["prev worst", "prev 2", "prev 3", "prev best"])
    ep["vol_bucket"] = qbucket(ep["vol_rel"], 4, ["quiet", "v2", "v3", "busy"])

    print(f"=== background conditions of EMA{EMA} legs  {sym} {tf} ===")
    print(f"bars {n}  {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}   legs {len(ep)}   "
          f"deadband {args.deadband:.2f} ATR   cost {args.cost_pts:.0f} pts")
    print(f"label = move from entry close to exit close, in ATR, net of cost")
    print(f"design < {edge1:%Y-%m-%d}  |  validation < {edge2:%Y-%m-%d}  |  oos after")
    print("")

    base = ep["pnl_atr"]
    print(f"BASELINE  n {len(ep)}   mean {base.mean():+.2f} ATR   "
          f"win {100 * (base > 0).mean():.1f}%   median {base.median():+.2f} ATR")
    for sp in SPLITS:
        s = ep.loc[ep.split == sp, "pnl_atr"]
        print(f"   {sp:<11} n {len(s):>4}   mean {s.mean():+.2f} ATR   "
              f"win {100 * (s > 0).mean():.1f}%")
    print("")

    show("by hour band (server clock in the data, UTC)", bucket_stats(ep, "hour_band"))
    show("by session", bucket_stats(ep, "session"))
    show("by weekday", bucket_stats(ep, "dow"))
    show("by spread regime (spread / trailing 200-bar mean)", bucket_stats(ep, "spread_bucket"))
    show("by volatility regime (ATR% / trailing 200-bar mean)", bucket_stats(ep, "atr_bucket"))
    show("by EMA stack alignment at entry (21/50/100 all with the leg)",
         bucket_stats(ep, "aligned"))
    show("by distance from EMA21 at entry", bucket_stats(ep, "dist_bucket"))
    show("by slope strength at entry", bucket_stats(ep, "slope_bucket"))
    show("by previous leg outcome (does a good leg follow a good leg?)",
         bucket_stats(ep, "prior_bucket"))
    show("by activity regime (tick volume / trailing mean)", bucket_stats(ep, "vol_bucket"))

    # ------------------------------------------------------- design-only filter
    # Pick the single best binary condition on DESIGN ONLY, then report the rest.
    print("--- candidate filters, thresholds chosen on design only ---")
    print(f"{'condition':<34}{'kept':>6}{'design':>9}{'valid':>9}{'oos':>9}{'all':>8}")
    print("-" * 76)

    cands = {
        "spread_rel <= median": ep["spread_rel"] <= ep["spread_rel"].median(),
        "spread_rel <= 1.0": ep["spread_rel"] <= 1.0,
        "atr_rel >= 1.0": ep["atr_rel"] >= 1.0,
        "atr_rel <= 1.0": ep["atr_rel"] <= 1.0,
        "stack aligned": ep["aligned"],
        "stack misaligned": ~ep["aligned"],
        "session in (london, overlap)": ep["session"].isin(["london", "overlap"]),
        "session not late": ep["session"] != "late",
        "hour band 07-09 or 13-15": ep["hour_band"].isin(["07-09", "13-15"]),
        "not Friday": ep["dow"] != "Fri",
        "prev leg was good": ep["prior_atr"] > 0,
        "prev leg was bad": ep["prior_atr"] <= 0,
        "dist >= 0.5 ATR": ep["dist"] >= 0.5,
        "dist <= 1.5 ATR": ep["dist"] <= 1.5,
        "slope >= median": ep["slope"] >= ep["slope"].median(),
        "rsi in 45..70 (with leg)": ((ep["rsi"] - 50) * ep["side"]).between(-5, 20),
    }
    best = None
    for name, mask in cands.items():
        g = ep[mask]
        if len(g) < 40:
            continue
        rec = {sp: g.loc[g.split == sp, "pnl_atr"].mean() for sp in SPLITS}
        allm = g["pnl_atr"].mean()
        print(f"{name:<34}{len(g):>6}{rec['design']:>9.2f}{rec['validation']:>9.2f}"
              f"{rec['oos']:>9.2f}{allm:>8.2f}")
        if best is None or rec["design"] > best[1]:
            best = (name, rec["design"], mask)
    print("")

    if best is None:
        print("no candidate filter had enough legs to evaluate")
        return 0

    name, _, mask = best
    chosen = ep[mask].sort_values("time")
    print(f"filter picked on design: {name}   legs {len(chosen)}")
    for sp in SPLITS:
        g = chosen[chosen.split == sp]
        if len(g):
            print(f"   {sp:<11} n {len(g):>4}   mean {g['pnl_atr'].mean():+.2f} ATR   "
                  f"win {100 * (g['pnl_atr'] > 0).mean():.1f}%   "
                  f"compounded {100 * ((1 + g['px_ret']).prod() - 1):+.1f}%")
    print("")

    # full book: every leg vs filtered legs, compounded, in calendar months
    if not args.no_publish:
        for label, sub in (("all_legs", ep), (f"filtered_{name}", chosen)):
            s = pd.Series(sub["px_ret"].to_numpy(),
                          index=pd.DatetimeIndex(sub["time"]))
            publish_results(f"context_edge_{sym}_{tf}_{label}", s,
                            append_to_bundle=False)

    out = RESULTS / f"context_edge_{sym}_{tf}.csv"
    RESULTS.mkdir(parents=True, exist_ok=True)
    ep.to_csv(out, index=False)
    print(f"wrote {out}")
    print("")
    print("How to read this: a bucket is only a candidate if the 'flag' column says")
    print("OK - same sign in design, validation AND oos, with enough legs in each.")
    print(f"About {len(cands)} filters and ~40 buckets were examined; at the 5% level")
    print("roughly two will look good by chance. Treat any single OK cell as a")
    print("hypothesis, not a finding, until it also survives a threshold sweep.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
