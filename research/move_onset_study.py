"""The vertical-line study: read every value where a move starts, then compare
the moves that ran against the moves that died.

This is the manual method turned into a measurement:

  1. Put the line at the bar a move begins (the EMA21 cross, offset 1 and 2).
  2. Record every value that is visible at that bar.
  3. Label the move afterwards: CLEAN if it stays on one side of EMA21 for at
     least --min-bars bars with few violations, FAKE otherwise.
  4. Ask, value by value: does it separate clean from fake, and does the
     direction of the separation hold in design, validation AND out of sample?

Part 4 does the mirror question - what changes on the bar a good move dies -
which is the "when good moves start failing" half of the method.

Nothing here changes an entry. It is a description of what the chart looks like
at the moment of the decision, measured.

Usage
    .\\.venv\\Scripts\\python.exe research/move_onset_study.py --symbol EURUSD
    .\\.venv\\Scripts\\python.exe research/move_onset_study.py --symbol USDJPY --resample D1
    .\\.venv\\Scripts\\python.exe research/move_onset_study.py --symbol EURUSD --horizon 5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from ema_cross_harvest import (  # noqa: E402
    ATR_P, EMA, EMA_MID, RSI_P, SMMA,
    auc, build_events, ema, fit_logistic, predict, smma, wilder_atr, wilder_rsi,
)
from period_returns import publish_results  # noqa: E402

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"
SPLITS = ("design", "validation", "oos")

# feature -> how to read it on the chart
LABEL = {
    "slope3": "EMA21 slope over 3 bars (ATR)",
    "stack": "how many of 21/50/100 are lined up (0-3)",
    "dist21": "distance from EMA21 (ATR)",
    "dist50": "distance from EMA50 (ATR)",
    "dist100": "distance from SMMA100 (ATR)",
    "body": "candle body in the move direction (ATR)",
    "rsi_adj": "RSI9 measured in the move direction (0-50)",
    "atr_ratio": "ATR now / ATR 20 bars ago",
    "prev_bars_f": "length of the previous move (bars)",
    "hour": "hour of day",
    "dow": "day of week",
    "wick": "wick against the move / range",
    "closepos": "close position inside the bar (0=low, 1=high)",
    "range_atr": "bar range in ATR",
    "gap_atr": "gap from the previous close in ATR",
}


def prep(sym: str, tf: str, resample: str) -> tuple[pd.DataFrame, str]:
    path = DATA / f"{sym}_{tf}.csv"
    if not path.exists():
        raise SystemExit(f"missing data: {path}")
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)
    if resample:
        rule = {"H4": "4h", "D1": "1D"}[resample]
        df = (df.set_index("time").resample(rule)
                .agg({"open": "first", "high": "max", "low": "min",
                      "close": "last", "tick_volume": "sum",
                      "spread": "median"})
                .dropna(subset=["open"]).reset_index())
        tf = resample
    df["ema"] = ema(df["close"], EMA)
    df["ema50"] = ema(df["close"], EMA_MID)
    df["smma"] = smma(df["close"], SMMA)
    df["rsi"] = wilder_rsi(df["close"], RSI_P)
    df["atr"] = wilder_atr(df, ATR_P)
    return df.dropna(subset=["ema", "ema50", "smma", "rsi", "atr"]).reset_index(drop=True), tf


def add_bar_shape(df: pd.DataFrame, ev: pd.DataFrame, off: int) -> pd.DataFrame:
    """Extra values that are only visible if you look at the candle itself."""
    openp = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    atr = df["atr"].to_numpy(float)

    wick, cpos, rng, gap = [], [], [], []
    for _, r in ev.iterrows():
        k = int(r[f"o{off}_e_i"])
        s = int(r["side"])
        if k < 1 or k >= len(df) or not np.isfinite(atr[k]) or atr[k] <= 0:
            wick.append(np.nan)
            cpos.append(np.nan)
            rng.append(np.nan)
            gap.append(np.nan)
            continue
        span = max(high[k] - low[k], 1e-12)
        wick.append(((high[k] - close[k]) if s > 0 else (close[k] - low[k])) / span)
        cpos.append(((close[k] - low[k]) / span) if s > 0 else ((high[k] - close[k]) / span))
        rng.append(span / atr[k])
        gap.append((openp[k] - close[k - 1]) * s / atr[k])

    ev[f"o{off}_wick"] = wick
    ev[f"o{off}_closepos"] = cpos
    ev[f"o{off}_range_atr"] = rng
    ev[f"o{off}_gap_atr"] = gap
    return ev


def univariate(ev: pd.DataFrame, off: int) -> pd.DataFrame:
    feats = [f"o{off}_{c}" for c in LABEL]
    rows = []
    for f in feats:
        sub = ev[[f, "real", "split"]].dropna()
        if len(sub) < 100:
            continue
        clean = sub.loc[sub.real == 1, f]
        fake = sub.loc[sub.real == 0, f]
        if clean.empty or fake.empty:
            continue
        sd = sub[f].std() + 1e-12
        rec = dict(feature=f.replace(f"o{off}_", ""), n=len(sub),
                   clean=clean.mean(), fake=fake.mean(),
                   gap_sd=(clean.mean() - fake.mean()) / sd,
                   auc_all=auc(sub["real"].to_numpy(int), sub[f].to_numpy(float)))
        per = {}
        for sp in SPLITS:
            s = sub[sub.split == sp]
            per[sp] = (auc(s["real"].to_numpy(int), s[f].to_numpy(float))
                       if len(s) > 60 and s.real.nunique() == 2 else np.nan)
        rec.update({f"auc_{sp}": per[sp] for sp in SPLITS})
        vals = [v for v in per.values() if np.isfinite(v)]
        rec["stable"] = (len(vals) == 3
                         and all(v > 0.52 for v in vals) or
                         len(vals) == 3 and all(v < 0.48 for v in vals))
        rows.append(rec)
    return pd.DataFrame(rows).sort_values("auc_all", key=lambda s: (s - 0.5).abs(),
                                          ascending=False)


def death_bars(df: pd.DataFrame, ev: pd.DataFrame) -> pd.DataFrame:
    """What changes on the bar a clean move dies, versus the middle of the move."""
    close = df["close"].to_numpy(float)
    openp = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    n = len(df)

    rows = []
    for _, r in ev[ev.real == 1].iterrows():
        a = int(r["i"])
        b = min(a + int(r["run_bars"]) - 1, n - 1)
        if b - a < 4:
            continue
        s = int(r["side"])
        mid = a + (b - a) // 2
        for tag, k in (("mid", mid), ("last", b)):
            if k < 3 or atr[k] <= 0:
                break
            span = max(high[k] - low[k], 1e-12)
            rows.append(dict(
                run=int(len(rows)), tag=tag, side=s,
                dist21=(close[k] - e[k]) * s / atr[k],
                slope3=(e[k] - e[k - 3]) * s / atr[k],
                body=(close[k] - openp[k]) * s / atr[k],
                rsi_adj=(rsi[k] - 50.0) * s,
                range_atr=span / atr[k],
                wick=((high[k] - close[k]) if s > 0 else (close[k] - low[k])) / span,
                atr_ratio=atr[k] / atr[max(k - 20, 0)],
            ))
    d = pd.DataFrame(rows)
    if d.empty:
        return d
    return (d.groupby("tag")[["dist21", "slope3", "body", "rsi_adj",
                              "range_atr", "wick", "atr_ratio"]]
             .mean().T.assign(delta=lambda x: x["last"] - x["mid"]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--resample", default="", choices=["", "H4", "D1"])
    ap.add_argument("--min-bars", type=int, default=5)
    ap.add_argument("--clean-max", type=float, default=0.25)
    ap.add_argument("--horizon", type=int, default=5,
                    help="fixed holding period used for the calendar P&L")
    ap.add_argument("--cost-pts", type=float, default=12.0)
    ap.add_argument("--no-publish", action="store_true")
    args = ap.parse_args()

    sym = args.symbol.upper()
    df, tf = prep(sym, args.tf.upper(), args.resample)
    point = 0.001 if sym.endswith("JPY") else 0.00001
    cost_px = args.cost_pts * point

    ev = build_events(df, args.min_bars, args.clean_max)
    if ev.empty:
        print("no events")
        return 1
    for off in (1, 2):
        ev = add_bar_shape(df, ev, off)

    t0, t1 = df["time"].iloc[0], df["time"].iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80

    def split_of(ts):
        if pd.isna(ts):
            return "none"
        return "design" if ts < edge1 else ("validation" if ts < edge2 else "oos")

    ev["split"] = ev["o1_e_time"].apply(split_of)

    print(f"=== the vertical line: what separates a clean move from a fake one ===")
    print(f"{sym} {tf}   bars {len(df)}   {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}")
    print(f"clean = stays on one side of EMA{EMA} for >= {args.min_bars} bars with "
          f"<= {args.clean_max:.0%} violations")
    print("")

    print("--- 1. base rate ---")
    for sp in SPLITS:
        sub = ev[ev.split == sp]
        if sub.empty:
            continue
        print(f"  {sp:<11} moves {len(sub):>5}   clean {int(sub.real.sum()):>4} "
              f"({sub.real.mean() * 100:5.1f}%)   median run {sub.run_bars.median():4.1f} bars")
    print("")

    for off in (1, 2):
        tbl = univariate(ev, off)
        print(f"--- 2.{off} every value at the bar the move starts "
              f"(offset {off} after the cross) ---")
        print(f"{'value':<36}{'clean':>9}{'fake':>9}{'gap(SD)':>9}{'AUC':>7}"
              f"{'design':>8}{'valid':>8}{'oos':>8}  flag")
        print("-" * 104)
        for _, r in tbl.iterrows():
            print(f"{LABEL.get(r['feature'], r['feature']):<36}"
                  f"{r['clean']:>9.3f}{r['fake']:>9.3f}{r['gap_sd']:>9.2f}"
                  f"{r['auc_all']:>7.3f}{r['auc_design']:>8.3f}"
                  f"{r['auc_validation']:>8.3f}{r['auc_oos']:>8.3f}"
                  f"  {'STABLE' if r['stable'] else ''}")
        print("")

    # -------------------------------------------------- 3. combined read
    feats = [f"o2_{c}" for c in ("slope3", "stack", "dist21", "dist50", "dist100",
                                 "body", "rsi_adj", "atr_ratio", "prev_bars_f",
                                 "wick", "closepos", "range_atr", "gap_atr")]
    tr = ev[(ev.split == "design") & (ev.o2_e_ok == 1)].dropna(subset=feats)
    print("--- 3. the values combined (trained on design only) ---")
    if len(tr) < 100:
        print("  not enough design events")
    else:
        w, mu, sd = fit_logistic(tr[feats].to_numpy(float),
                                 tr["real"].to_numpy(int), l2=2.0)
        for sp in SPLITS:
            sub = ev[(ev.split == sp) & (ev.o2_e_ok == 1)].dropna(subset=feats)
            if sub.empty:
                continue
            p = predict(w, mu, sd, sub[feats].to_numpy(float))
            top = p >= np.quantile(p, 0.75)
            print(f"  {sp:<11} n {len(sub):>5}  AUC {auc(sub['real'].to_numpy(int), p):.3f}  "
                  f"base clean {sub.real.mean() * 100:5.1f}%  "
                  f"top quartile clean {sub.real[top].mean() * 100:5.1f}%  "
                  f"({int(top.sum())} moves)")
        print("")
        print("  what the model leans on (standardised weight):")
        for name, wv in sorted(zip(feats, w[1:]), key=lambda kv: -abs(kv[1]))[:6]:
            print(f"    {LABEL.get(name.replace('o2_', ''), name):<36}{wv:+.3f}")
        print("")

    # -------------------------------------------------- 4. death bars
    d = death_bars(df, ev)
    if not d.empty:
        print(f"--- 4. what changes on the bar a clean move dies ---")
        print(f"{'value':<20}{'middle':>10}{'death bar':>12}{'change':>10}")
        print("-" * 52)
        for name, r in d.iterrows():
            print(f"{LABEL.get(name, name):<20}{r['mid']:>10.3f}{r['last']:>12.3f}"
                  f"{r['delta']:>10.3f}")
        print("")
        print("  positive change = the value is higher on the death bar than mid-move.")
        print("")

    # -------------------------------------------------- 5. does the lean pay?
    close = df["close"].to_numpy(float)
    h = args.horizon
    rows = []
    for _, r in ev.iterrows():
        if int(r["o2_e_ok"]) != 1:
            continue
        k = int(r["o2_e_i"])
        j = min(k + h, len(df) - 1)
        s = int(r["side"])
        rows.append(dict(time=pd.Timestamp(r["o2_e_time"]), side=s, k=int(k),
                         dist=float(r["o2_dist21"]), wick=float(r["o2_wick"]),
                         slope=float(r["o2_slope3"]), rsi=float(r["o2_rsi_adj"]),
                         px_ret=(close[j] - close[k]) * s / close[k]
                         - cost_px / close[k],
                         real=int(r["real"])))
    tr2 = pd.DataFrame(rows)
    if not tr2.empty:
        tr2["split"] = tr2["time"].apply(split_of)
        tr2["dist_q"] = pd.qcut(tr2["dist"], 4, labels=False, duplicates="drop")
        tr2["wick_q"] = pd.qcut(tr2["wick"], 4, labels=False, duplicates="drop")

        print(f"--- 5. does the read at the line actually pay? "
              f"fixed {h}-bar hold, net of cost ---")
        print(f"{'distance from EMA21 at the line':<34}{'n':>6}{'design':>9}"
              f"{'valid':>9}{'oos':>9}{'all':>8}{'win%':>7}")
        print("-" * 82)
        names = ["nearest quartile", "q2", "q3", "farthest quartile"]
        for q, g in tr2.groupby("dist_q", observed=True):
            rec = {sp: g.loc[g.split == sp, "px_ret"].mean() * 100 for sp in SPLITS}
            print(f"{names[int(q)]:<34}{len(g):>6}{rec['design']:>9.2f}"
                  f"{rec['validation']:>9.2f}{rec['oos']:>9.2f}"
                  f"{g['px_ret'].mean() * 100:>8.2f}"
                  f"{100 * (g['px_ret'] > 0).mean():>7.1f}")
        print("")
        print(f"{'wick against the move at the line':<34}{'n':>6}{'design':>9}"
              f"{'valid':>9}{'oos':>9}{'all':>8}{'win%':>7}")
        print("-" * 82)
        for q, g in tr2.groupby("wick_q", observed=True):
            rec = {sp: g.loc[g.split == sp, "px_ret"].mean() * 100 for sp in SPLITS}
            print(f"{'quartile ' + str(int(q) + 1):<34}{len(g):>6}{rec['design']:>9.2f}"
                  f"{rec['validation']:>9.2f}{rec['oos']:>9.2f}"
                  f"{g['px_ret'].mean() * 100:>8.2f}"
                  f"{100 * (g['px_ret'] > 0).mean():>7.1f}")
        print("")
        print("  If the far/clean-wick quartiles are positive in all three splits,")
        print("  the read at the line is real edge. If only the top quartile is")
        print("  positive and it is small, the read is mostly description.")
        print("")

        if not args.no_publish:
            for label, sub in (("all_moves", tr2), ("clean_only", tr2[tr2.real == 1]),
                               ("farthest_dist", tr2[tr2.dist_q == tr2.dist_q.max()])):
                if sub.empty:
                    continue
                publish_results(
                    f"onset_{sym}_{tf}_{label}_h{h}",
                    pd.Series(sub["px_ret"].to_numpy(),
                              index=pd.DatetimeIndex(sub["time"])),
                    append_to_bundle=False,
                )

    # -------------------------------------------------- 6. exit on the death signature
    if not tr2.empty:
        openp = df["open"].to_numpy(float)
        high = df["high"].to_numpy(float)
        low = df["low"].to_numpy(float)
        e = df["ema"].to_numpy(float)
        atr = df["atr"].to_numpy(float)
        nbar = len(df)
        res = []
        for _, r in ev.iterrows():
            if int(r["o2_e_ok"]) != 1:
                continue
            k = int(r["o2_e_i"])
            s = int(r["side"])
            if atr[k] <= 0:
                continue
            entry = close[k]
            stop = entry - s * 1.5 * atr[k]
            fixed = (close[min(k + h, nbar - 1)] - entry) * s / close[k] - cost_px / close[k]
            px = None
            for j in range(k + 1, min(k + 40, nbar)):
                if (low[j] <= stop) if s > 0 else (high[j] >= stop):
                    px = stop
                    break
                dead = ((close[j] - openp[j]) * s < 0
                        and (close[j] - e[j]) * s < 0.6 * atr[j])
                if dead:
                    px = close[j]
                    break
            if px is None:
                px = close[min(k + 39, nbar - 1)]
            res.append(dict(time=pd.Timestamp(r["o2_e_time"]),
                            fixed=fixed,
                            death=(px - entry) * s / close[k] - cost_px / close[k]))
        rr = pd.DataFrame(res)
        if not rr.empty:
            rr["split"] = rr["time"].apply(split_of)
            print("--- 6. same entries, exit on the death signature vs fixed hold ---")
            print(f"{'exit rule':<22}{'n':>6}{'design':>9}{'valid':>9}{'oos':>9}"
                  f"{'all':>8}{'win%':>7}")
            print("-" * 70)
            for col, name in (("fixed", f"fixed {h}-bar hold"),
                              ("death", "exit on death signature")):
                rec = {sp: rr.loc[rr.split == sp, col].mean() * 100 for sp in SPLITS}
                print(f"{name:<22}{len(rr):>6}{rec['design']:>9.2f}"
                      f"{rec['validation']:>9.2f}{rec['oos']:>9.2f}"
                      f"{rr[col].mean() * 100:>8.2f}"
                      f"{100 * (rr[col] > 0).mean():>7.1f}")
            print("")
            print("  death signature = body against the move AND price back within")
            print("  0.6 ATR of EMA21. That is the bar where a clean move stops")
            print("  being clean, taken from the table above rather than fitted.")
            print("")

    # -------------------------------------------------- 7. horizon sensitivity
    if not tr2.empty:
        print("--- 7. horizon sensitivity: mean % from the line, by distance quartile ---")
        hdrs = [5, 10, 20, 40]
        print(f"{'quartile':<20}" + "".join(f"{'h' + str(x):>9}" for x in hdrs))
        print("-" * 56)
        for q, g in tr2.groupby("dist_q", observed=True):
            line = f"{names[int(q)]:<20}"
            for hz in hdrs:
                vals = []
                for _, r in g.iterrows():
                    k = int(r["k"])
                    j = min(k + hz, len(df) - 1)
                    s = int(r["side"])
                    vals.append((close[j] - close[k]) * s / close[k] - cost_px / close[k])
                line += f"{np.mean(vals) * 100:>9.2f}"
            print(line)
        print("")
        print("  Same test by split at h=20 (the horizon where a 'clean move' would")
        print("  have to show up if it exists):")
        print(f"{'quartile':<20}{'design':>9}{'valid':>9}{'oos':>9}{'win%':>7}")
        print("-" * 54)
        for q, g in tr2.groupby("dist_q", observed=True):
            rec = {}
            for sp in SPLITS:
                sub = g[g.split == sp]
                rec[sp] = np.mean([
                    (close[min(int(r["k"]) + 20, len(df) - 1)] - close[int(r["k"])])
                    * int(r["side"]) / close[int(r["k"])] - cost_px / close[int(r["k"])]
                    for _, r in sub.iterrows()
                ]) * 100 if len(sub) else np.nan
            wins = np.mean([
                ((close[min(int(r["k"]) + 20, len(df) - 1)] - close[int(r["k"])])
                 * int(r["side"]) / close[int(r["k"])] - cost_px / close[int(r["k"])]) > 0
                for _, r in g.iterrows()])
            print(f"{names[int(q)]:<20}{rec['design']:>9.2f}{rec['validation']:>9.2f}"
                  f"{rec['oos']:>9.2f}{wins * 100:>7.1f}")
        print("")

    out = RESULTS / f"move_onset_{sym}_{tf}.csv"
    RESULTS.mkdir(parents=True, exist_ok=True)
    ev.to_csv(out, index=False)
    print(f"wrote {out}")
    print("")
    print("Read this as a description, not a strategy: 'clean' is only known after")
    print("the move is over. The point is which values at the line already lean one")
    print("way, and whether that lean survives out of sample.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
