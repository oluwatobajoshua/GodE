"""Market structure: higher highs / higher lows and lower highs / lower lows.

The claim: good periods are the ones where the market is making HH+HL (or LH+LL),
and if we can find those periods early - and know *why* they occur - we can focus
only on them.

This script does three separate jobs and keeps them separate:

  1. DETECT the structure, honestly. Swings are k-bar fractals, and a fractal at
     bar i is only confirmed at bar i+k. The state series at bar t therefore uses
     only swings that were already confirmed by t. No peeking.

  2. PRICE the structure. From the bar a structure period starts, what is the
     forward return at 5 / 10 / 20 / 40 bars, net of cost, per split? This is the
     decisive test: if HH+HL periods do not pay, the structure is a description,
     not an edge. "Hold to the end of the period" is reported separately and
     labelled as an oracle, because the end is only known afterwards.

  3. EXPLAIN the periods. What is true at the bar an up-structure starts, and how
     does it differ from the up-structures that die immediately? Same shape as the
     vertical-line study: mean value, gap in SD, AUC, and split stability.

Plus the break signature: what changes on the bar a structure period ends.

Usage
    .\\.venv\\Scripts\\python.exe research/market_structure.py --symbol EURUSD
    .\\.venv\\Scripts\\python.exe research/market_structure.py --symbol USDJPY --resample D1
    .\\.venv\\Scripts\\python.exe research/market_structure.py --symbol EURUSD --k 3 --min-run 10
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
    auc, ema, smma, wilder_atr, wilder_rsi,
)
from period_returns import publish_results  # noqa: E402

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"
SPLITS = ("design", "validation", "oos")


def swings(high: np.ndarray, low: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
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


def structure(high: np.ndarray, low: np.ndarray, k: int) -> tuple[np.ndarray, list]:
    """State per bar using only swings confirmed by that bar (confirmation lag k)."""
    n = len(high)
    up, dn = swings(high, low, k)
    state = np.empty(n, dtype=object)
    last_sh = last_sl = None
    sh_up = sl_up = None
    for t in range(n):
        i = t - k
        if i >= k:
            if up[i] and (last_sh is None or high[i] != last_sh):
                sh_up = True if last_sh is None else bool(high[i] > last_sh)
                last_sh = float(high[i])
            if dn[i] and (last_sl is None or low[i] != last_sl):
                sl_up = True if last_sl is None else bool(low[i] > last_sl)
                last_sl = float(low[i])
        if last_sh is None or last_sl is None or sh_up is None or sl_up is None:
            state[t] = "mixed"
        elif sh_up and sl_up:
            state[t] = "up"
        elif (not sh_up) and (not sl_up):
            state[t] = "down"
        else:
            state[t] = "mixed"

    periods = []
    start = 0
    for t in range(1, n):
        if state[t] != state[start]:
            periods.append((start, t - 1, str(state[start])))
            start = t
    periods.append((start, n - 1, str(state[start])))
    return state, periods


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--resample", default="", choices=["", "H4", "D1"])
    ap.add_argument("--k", type=int, default=2, help="fractal half-width (confirmation lag)")
    ap.add_argument("--min-run", type=int, default=10,
                    help="bars a structure period must last to count as 'held'")
    ap.add_argument("--cost-pts", type=float, default=12.0)
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
    df["ema50"] = ema(df["close"], EMA_MID)
    df["smma"] = smma(df["close"], SMMA)
    df["rsi"] = wilder_rsi(df["close"], RSI_P)
    df["atr"] = wilder_atr(df, ATR_P)
    df = df.dropna(subset=["ema", "ema50", "smma", "rsi", "atr"]).reset_index(drop=True)

    close = df["close"].to_numpy(float)
    openp = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    e50 = df["ema50"].to_numpy(float)
    s100 = df["smma"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    spread = df["spread"].to_numpy(float)
    time = df["time"]
    n = len(df)

    point = 0.001 if sym.endswith("JPY") else 0.00001
    cost_px = args.cost_pts * point

    slope3 = np.zeros(n)
    slope3[3:] = (e[3:] - e[:-3]) / np.maximum(atr[3:], 1e-12)

    state, periods = structure(high, low, args.k)

    t0, t1 = time.iloc[0], time.iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80
    bounds = {"design": (t0, edge1), "validation": (edge1, edge2), "oos": (edge2, t1)}

    def split_of(ts):
        return "design" if ts < edge1 else ("validation" if ts < edge2 else "oos")

    print(f"=== market structure: HH/HL and LH/LL periods  {sym} {tf} ===")
    print(f"bars {n}  {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}   fractal k={args.k} "
          f"(a swing is confirmed {args.k} bars later)   cost {args.cost_pts:.0f} pts")
    print("")

    # ------------------------------------------------------------- 1. base rate
    print("--- 1. how much of the time is each structure in force ---")
    print(f"{'state':<10}{'bars':>9}{'share':>8}{'periods':>9}{'median run':>12}{'mean run':>10}")
    print("-" * 58)
    for st in ("up", "down", "mixed"):
        bars = int((state == st).sum())
        per = [p for p in periods if p[2] == st]
        lens = [b - a + 1 for a, b, _ in per]
        print(f"{st:<10}{bars:>9}{100 * bars / n:>7.1f}%{len(per):>9}"
              f"{np.median(lens) if lens else 0:>12.0f}"
              f"{np.mean(lens) if lens else 0:>10.1f}")
    print("")

    # ------------------------------------------------------- 2. does it pay?
    hdrs = (5, 10, 20, 40)
    print("--- 2. forward return from the bar a period starts, net of cost ---")
    print(f"    up structure -> long, down structure -> short, mixed -> stand aside")
    print(f"{'state':<10}{'periods':>9}" + "".join(f"{'h' + str(h):>9}" for h in hdrs)
          + f"{'win% h20':>10}")
    print("-" * 58)
    recs = []
    for st in ("up", "down"):
        per = [p for p in periods if p[2] == st]
        line = f"{st:<10}{len(per):>9}"
        side = 1 if st == "up" else -1
        h20 = []
        for h in hdrs:
            vals = []
            for a, b, _ in per:
                j = min(a + h, n - 1)
                vals.append((close[j] - close[a]) * side / close[a] - cost_px / close[a])
            line += f"{np.mean(vals) * 100:>9.3f}"
            if h == 20:
                h20 = vals
        line += f"{100 * np.mean(np.array(h20) > 0):>10.1f}"
        print(line)
        for a, b, _ in per:
            recs.append(dict(time=time.iloc[a], state=st, start=a, end=b,
                             bars=b - a + 1))
    print("")

    pr = pd.DataFrame(recs)
    pr["split"] = pr["time"].apply(split_of)
    print(f"{'state':<8}{'split':<13}{'periods':>9}{'h20 %':>9}{'h20 win%':>10}")
    print("-" * 50)
    for st in ("up", "down"):
        side = 1 if st == "up" else -1
        for sp in SPLITS:
            sub = pr[(pr.state == st) & (pr.split == sp)]
            if sub.empty:
                continue
            v = [(close[min(int(a) + 20, n - 1)] - close[int(a)]) * side / close[int(a)]
                 - cost_px / close[int(a)] for a in sub["start"]]
            print(f"{st:<8}{sp:<13}{len(sub):>9}{np.mean(v) * 100:>9.3f}"
                  f"{100 * np.mean(np.array(v) > 0):>10.1f}")
    print("")

    # ------------------------------------------- 3. is structure just the slope?
    agree = ((state == "up") & (slope3 > 0)) | ((state == "down") & (slope3 < 0))
    both = (state != "mixed")
    print("--- 3. is this anything more than the EMA21 slope? ---")
    if both.sum():
        print(f"  bars with a structure (up or down): {int(both.sum())} "
              f"({100 * both.mean():.1f}% of the tape)")
        print(f"  of those, EMA21 slope points the same way: "
              f"{100 * agree[both].mean():.1f}%")
        for label, mask in (("structure and slope agree", both & agree),
                            ("structure but slope disagrees", both & ~agree)):
            side = np.where(state == "up", 1, -1)
            vals = [(close[min(t + 20, n - 1)] - close[t]) * side[t] / close[t]
                    - cost_px / close[t] for t in np.where(mask)[0]]
            print(f"  {label:<32} bars {int(mask.sum()):>7}  "
                  f"h20 {np.mean(vals) * 100:>7.3f}%  "
                  f"win {100 * np.mean(np.array(vals) > 0):>5.1f}%")
    print("")

    # -------------------------------------------------- 4. why do they occur?
    rows = []
    for a, b, st in periods:
        if st not in ("up", "down") or a < 60 or a >= n - 45:
            continue
        side = 1 if st == "up" else -1
        stack = sum([(close[a] - e[a]) * side > 0,
                     (e[a] - e50[a]) * side > 0,
                     (e50[a] - s100[a]) * side > 0])
        span10 = max(high[a - 10:a + 1].max() - low[a - 10:a + 1].min(), 1e-12)
        rows.append(dict(
            start=a, side=side, bars=b - a + 1,
            time=time.iloc[a], split=split_of(time.iloc[a]),
            held=bool(b - a + 1 >= args.min_run),
            compression=span10 / atr[a],
            atr_ratio=atr[a] / atr[max(a - 20, 0)],
            dist21=(close[a] - e[a]) * side / atr[a],
            dist50=(close[a] - e50[a]) * side / atr[a],
            slope=(e[a] - e[a - 3]) * side / atr[a],
            stack=float(stack),
            rsi_adj=(rsi[a] - 50.0) * side,
            body=(close[a] - openp[a]) * side / atr[a],
            range_atr=(high[a] - low[a]) / atr[a],
            spread_rel=spread[a] / (np.mean(spread[max(a - 200, 0):a + 1]) + 1.0),
            hour=float(time.iloc[a].hour),
            prev_bars=float(b - a + 1),
            h20=(close[min(a + 20, n - 1)] - close[a]) * side / close[a]
                - cost_px / close[a],
        ))
    ep = pd.DataFrame(rows)

    if not ep.empty:
        print(f"--- 4. why do some structure periods hold and others fail? ---")
        print(f"    held = lasted >= {args.min_run} bars   "
              f"(held {int(ep.held.sum())} of {len(ep)}, "
              f"{100 * ep.held.mean():.1f}%)")
        feats = ["compression", "atr_ratio", "dist21", "dist50", "slope", "stack",
                 "rsi_adj", "body", "range_atr", "spread_rel", "hour"]
        print(f"{'value at the start':<30}{'held':>9}{'failed':>9}{'gap(SD)':>9}"
              f"{'AUC':>7}{'design':>8}{'valid':>8}{'oos':>8}  flag")
        print("-" * 96)
        out_rows = []
        for f in feats:
            sub = ep[[f, "held", "split"]].dropna()
            if len(sub) < 80 or sub.held.nunique() < 2:
                continue
            sd = sub[f].std() + 1e-12
            rec = dict(feature=f, n=len(sub),
                       held=sub.loc[sub.held, f].mean(),
                       failed=sub.loc[~sub.held, f].mean(),
                       gap_sd=(sub.loc[sub.held, f].mean()
                               - sub.loc[~sub.held, f].mean()) / sd,
                       auc_all=auc(sub["held"].to_numpy(int), sub[f].to_numpy(float)))
            for sp in SPLITS:
                s = sub[sub.split == sp]
                rec[f"auc_{sp}"] = (auc(s["held"].to_numpy(int), s[f].to_numpy(float))
                                    if len(s) > 50 and s.held.nunique() == 2 else np.nan)
            vals = [rec[f"auc_{sp}"] for sp in SPLITS]
            ok = [v for v in vals if np.isfinite(v)]
            rec["stable"] = (len(ok) == 3
                             and (all(v > 0.53 for v in ok) or all(v < 0.47 for v in ok)))
            out_rows.append(rec)
        tbl = pd.DataFrame(out_rows).sort_values(
            "auc_all", key=lambda s: (s - 0.5).abs(), ascending=False)
        for _, r in tbl.iterrows():
            print(f"{r['feature']:<30}{r['held']:>9.3f}{r['failed']:>9.3f}"
                  f"{r['gap_sd']:>9.2f}{r['auc_all']:>7.3f}{r['auc_design']:>8.3f}"
                  f"{r['auc_validation']:>8.3f}{r['auc_oos']:>8.3f}"
                  f"  {'STABLE' if r['stable'] else ''}")
        print("")

        # -------------------------------------------------- 5. break signature
        print("--- 5. what changes on the bar a structure period ends ---")
        print(f"{'value':<22}{'mid-period':>12}{'last bar':>10}{'change':>9}")
        print("-" * 54)
        mids, lasts = [], []
        for _, r in ep[ep.bars >= 8].iterrows():
            a = int(r["start"])
            b = int(r["start"] + r["bars"] - 1)
            s = int(r["side"])
            m = a + (b - a) // 2
            for k, bucket in ((m, mids), (b, lasts)):
                if k < 3 or atr[k] <= 0:
                    break
                span = max(high[k] - low[k], 1e-12)
                bucket.append(dict(
                    dist21=(close[k] - e[k]) * s / atr[k],
                    slope=(e[k] - e[k - 3]) * s / atr[k],
                    body=(close[k] - openp[k]) * s / atr[k],
                    rsi_adj=(rsi[k] - 50.0) * s,
                    wick=((high[k] - close[k]) if s > 0 else (close[k] - low[k])) / span,
                    atr_ratio=atr[k] / atr[max(k - 20, 0)],
                ))
        if mids and lasts:
            mid = pd.DataFrame(mids).mean()
            lst = pd.DataFrame(lasts).mean()
            for f in mid.index:
                print(f"{f:<22}{mid[f]:>12.3f}{lst[f]:>10.3f}{lst[f] - mid[f]:>9.3f}")
        print("")

        # -------------------------------------------------- 6. calendar proof
        if not args.no_publish:
            for st in ("up", "down"):
                sub = ep[ep.side == (1 if st == "up" else -1)]
                if sub.empty:
                    continue
                publish_results(
                    f"structure_{sym}_{tf}_{st}_h20",
                    pd.Series(sub["h20"].to_numpy(), index=pd.DatetimeIndex(sub["time"])),
                    append_to_bundle=False,
                )

        # -------------------------------------------------- 7. structure exit
        print("--- 7. hold the period vs exit when the structure breaks ---")
        r7 = []
        for _, r in ep.iterrows():
            a = int(r["start"])
            s = int(r["side"])
            entry = close[a]
            stop = entry - s * 1.5 * atr[a]
            px = None
            bars = 0
            for t in range(a + 1, min(a + 120, n)):
                bars += 1
                if (low[t] <= stop) if s > 0 else (high[t] >= stop):
                    px = stop
                    break
                if state[t] != ("up" if s > 0 else "down"):
                    px = close[t]
                    break
            if px is None:
                px = close[min(a + 119, n - 1)]
            r7.append(dict(time=time.iloc[a], split=r["split"], bars=bars,
                           hold=(close[min(a + 20, n - 1)] - entry) * s / close[a]
                                - cost_px / close[a],
                           brk=(px - entry) * s / close[a] - cost_px / close[a]))
        q7 = pd.DataFrame(r7)
        if not q7.empty:
            print(f"{'exit rule':<26}{'design':>9}{'valid':>9}{'oos':>9}"
                  f"{'all':>8}{'win%':>7}{'bars':>7}")
            print("-" * 76)
            for col, name in (("hold", "hold a fixed 20 bars"),
                              ("brk", "exit on structure break")):
                rec = {sp: q7.loc[q7.split == sp, col].mean() * 100 for sp in SPLITS}
                print(f"{name:<26}{rec['design']:>9.3f}{rec['validation']:>9.3f}"
                      f"{rec['oos']:>9.3f}{q7[col].mean() * 100:>8.3f}"
                      f"{100 * (q7[col] > 0).mean():>7.1f}{q7['bars'].mean():>7.1f}")
            print("")

        out = RESULTS / f"market_structure_{sym}_{tf}.csv"
        RESULTS.mkdir(parents=True, exist_ok=True)
        ep.to_csv(out, index=False)
        print(f"wrote {out}")

    print("")
    print("Read it this way: part 2 is the money question. If h20 is not positive in")
    print("all three splits, HH/HL is a description of where price has been, not a")
    print("signal about where it goes. Part 4 is the 'why': the values that separate")
    print("the periods that held from the ones that failed, if any survive the splits.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
