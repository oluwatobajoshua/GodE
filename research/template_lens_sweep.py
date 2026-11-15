#!/usr/bin/env python3
r"""Dig the template's own vocabulary: EMA21, EMA50, SMMA100, fractals, RSI9 20/50/80.

The premise: the chart template is the lens, so the hypothesis space should be
exactly what the lens can see and nothing else. No new indicators are invented
here. Everything below is built from the five things `templates/fxmopol.tpl`
draws:

    EMA21     #00ffff             fast line
    EMA50     #ffd700             slow line
    SMMA100   #ffffff             the big line (smoothed MA, alpha = 1/100)
    fractals  arrows 217/218      5-bar fractal, confirmed 2 bars after the swing
    RSI9      levels 20 / 50 / 80

Two of those have never been tested in this project: the **SMMA100** as a regime
line, and the template's **20/80** bands (the certified RSI rule uses 25/75).

PRE-REGISTERED HYPOTHESIS SPACE (fixed before any result was seen)

  regime  R1  e21 > e50
          R2  e21 > e50 > s100            full stack, the template's "all up"
          R3  close > s100                above the big line
          R4  e21 > s100 and e21 rising    the user's rule: "EMA21 facing up"

  trigger T1  rsi crosses UP through 50
          T2  rsi crosses UP through 20   oversold recovery at the low line
          T3  rsi crosses UP through 80   breakout through the high line
          T4  close crosses UP through the last confirmed fractal high
          T5  close crosses UP through e21 while e21 rises

  exit    X1  rsi crosses DOWN through 50
          X2  rsi >= 80                   take profit at the template's high line
          X3  e21 crosses DOWN through e50
          X4  close < s100                lost the big line
          X5  close < last confirmed fractal low
          X6  24 bars maximum hold

  4 x 5 x 6 = 120 rules, each traded long-and-short symmetric.
  All are causal: a fractal is only used from its confirmation bar (swing + 2).

THE PART THAT MAKES THIS A TEST RATHER THAN A FISHING TRIP

  A grid of 120 rules will always produce some that look good. So every real rule
  is matched by random controls with the same trade count:
    * "random entry" - the entry bars are shuffled, the exit rule is real
    * "random exit"  - the entry is real, the exit bars are shuffled
  If the real grid's pass rate is not clearly above the controls', the lens has
  nothing in it. That is the same discipline that retired the MA compression
  sweep (2/48, below chance).

  The **ensemble** of all 120 rules is also reported: averaging the whole family
  removes the selection step, which is what killed the RSI knob search.

Run:
    .\\.venv\\Scripts\\python.exe research/template_lens_sweep.py
    .\\.venv\\Scripts\\python.exe research/template_lens_sweep.py --data-dir research/data/histdata
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from backtest_core import cost_per_side  # noqa: E402
from ema_cross_harvest import ema, wilder_rsi  # noqa: E402
from portfolio_combine import SPLITS  # noqa: E402

ROOT = HERE.parent
DATA = ROOT / "research" / "data" / "mt5"
BARS_PER_YEAR = 24 * 252
SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY")
N_NULL = 60


# --------------------------------------------------------------------------- #
# the template's vocabulary
# --------------------------------------------------------------------------- #
def smma(x, period: int) -> np.ndarray:
    """Smoothed moving average: the SMMA the template draws at period 100."""
    return pd.Series(np.asarray(x, float)).ewm(
        alpha=1.0 / period, adjust=False).mean().to_numpy()


def fractal_levels(high: np.ndarray, low: np.ndarray, k: int = 2):
    """5-bar fractal made causal: a swing at i is only usable from i + k."""
    n = len(high)
    fh = np.full(n, np.nan)
    fl = np.full(n, np.nan)
    for i in range(k, n - k):
        if (high[i] > high[i - k:i]).all() and (high[i] > high[i + 1:i + k + 1]).all():
            fh[i + k] = high[i]
        if (low[i] < low[i - k:i]).all() and (low[i] < low[i + 1:i + k + 1]).all():
            fl[i + k] = low[i]
    return (pd.Series(fh).ffill().to_numpy(),
            pd.Series(fl).ffill().to_numpy())


def _edges(level, n: int):
    """Level may be a scalar or a per-bar array; slice the array to the edges."""
    if np.isscalar(level):
        return level, level
    lv = np.asarray(level, float)
    if len(lv) != n:
        raise ValueError(f"level length {len(lv)} != series length {n}")
    return lv[:-1], lv[1:]


def cross_up(x: np.ndarray, level) -> np.ndarray:
    x = np.asarray(x, float)
    prev, cur = _edges(level, len(x))
    out = np.zeros(len(x), dtype=bool)
    out[1:] = (x[:-1] <= prev) & (x[1:] > cur)
    return out


def cross_dn(x: np.ndarray, level) -> np.ndarray:
    x = np.asarray(x, float)
    prev, cur = _edges(level, len(x))
    out = np.zeros(len(x), dtype=bool)
    out[1:] = (x[:-1] >= prev) & (x[1:] < cur)
    return out


# --------------------------------------------------------------------------- #
# position machine + the project's one return contract
# --------------------------------------------------------------------------- #
def build_pos(ent_l, ent_s, exi_l, exi_s) -> np.ndarray:
    """pos[t] = side entered at the close of bar t (held into bar t+1)."""
    n = len(ent_l)
    pos = np.zeros(n, dtype=np.int8)
    side = 0
    for t in range(n):
        if side == 0:
            if ent_l[t]:
                side = 1
            elif ent_s[t]:
                side = -1
        elif side > 0:
            if exi_l[t]:
                side = 0
        else:
            if exi_s[t]:
                side = 0
        pos[t] = side
    return pos


def build_maxhold(ent_l, ent_s, maxbars: int) -> np.ndarray:
    n = len(ent_l)
    pos = np.zeros(n, dtype=np.int8)
    side, left = 0, 0
    for t in range(n):
        if side == 0:
            if ent_l[t]:
                side, left = 1, maxbars
            elif ent_s[t]:
                side, left = -1, maxbars
        elif left <= 1:
            side, left = 0, 0
        else:
            left -= 1
        pos[t] = side
    return pos


def returns_from_pos(pos: np.ndarray, close: np.ndarray,
                     cost_side: np.ndarray) -> np.ndarray:
    """The project contract: per-bar mark-to-market, cost on every side change."""
    n = len(close)
    ret = np.zeros(n)
    ret[1:] = pos[:-1] * (close[1:] / close[:-1] - 1.0)
    turn = np.abs(np.diff(pos, prepend=np.int8(0))).astype(float)
    ret -= turn * (cost_side / close)
    return ret


def split_stats(ret: np.ndarray, time: pd.Series) -> dict:
    out = {}
    for sp, (a, b) in SPLITS.items():
        m = ((time >= pd.Timestamp(a)) & (time < pd.Timestamp(b))).to_numpy()
        x = ret[m]
        if len(x) < 100:
            out[sp] = dict(total=0.0, sharpe=0.0, dd=0.0, n=len(x))
            continue
        eq = np.cumprod(1.0 + x)
        sd = x.std(ddof=1)
        out[sp] = dict(
            total=float(eq[-1] - 1.0) * 100.0,
            sharpe=float(x.mean() / sd * np.sqrt(BARS_PER_YEAR)) if sd > 0 else 0.0,
            dd=float((eq / np.maximum.accumulate(eq) - 1.0).min()) * 100.0,
            n=int(m.sum()),
        )
    return out


def passes(st: dict) -> bool:
    return all(st[sp]["total"] > 0 for sp in SPLITS)


def rt_count(pos: np.ndarray) -> int:
    return int(np.abs(np.diff(pos, prepend=np.int8(0))).sum()) // 2


# --------------------------------------------------------------------------- #
# the grid
# --------------------------------------------------------------------------- #
def build_ctx(df: pd.DataFrame) -> dict:
    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    e21 = ema(df["close"], 21).to_numpy(float)
    e50 = ema(df["close"], 50).to_numpy(float)
    s100 = smma(close, 100)
    rsi = wilder_rsi(df["close"], 9).to_numpy(float)
    fh, fl = fractal_levels(high, low, 2)
    fh = np.where(np.isnan(fh), np.inf, fh)     # no swing yet -> unreachable
    fl = np.where(np.isnan(fl), -np.inf, fl)
    slope = np.zeros(len(close))
    slope[3:] = e21[3:] - e21[:-3]

    L = {
        "R1_e21>e50": e21 > e50,
        "R2_stack": (e21 > e50) & (e50 > s100),
        "R3_>s100": close > s100,
        "R4_e21up": (e21 > s100) & (slope > 0),
    }
    S = {   # exact mirrors
        "R1_e21>e50": e21 < e50,
        "R2_stack": (e21 < e50) & (e50 < s100),
        "R3_>s100": close < s100,
        "R4_e21up": (e21 < s100) & (slope < 0),
    }
    TL = {
        "T1_rsi50": cross_up(rsi, 50.0),
        "T2_rsi20": cross_up(rsi, 20.0),
        "T3_rsi80": cross_up(rsi, 80.0),
        "T4_fractal": cross_up(close, fh),
        "T5_close_e21": cross_up(close, e21) & (slope > 0),
    }
    TS = {
        "T1_rsi50": cross_dn(rsi, 50.0),
        "T2_rsi20": cross_dn(rsi, 80.0),
        "T3_rsi80": cross_dn(rsi, 20.0),
        "T4_fractal": cross_dn(close, fl),
        "T5_close_e21": cross_dn(close, e21) & (slope < 0),
    }
    XL = {
        "X1_rsi50": cross_dn(rsi, 50.0),
        "X2_rsi80": rsi >= 80.0,
        "X3_e21_e50": cross_dn(e21, e50),
        "X4_<s100": close < s100,
        "X5_fractal": cross_dn(close, fl),
    }
    XS = {
        "X1_rsi50": cross_up(rsi, 50.0),
        "X2_rsi80": rsi <= 20.0,
        "X3_e21_e50": cross_up(e21, e50),
        "X4_<s100": close > s100,
        "X5_fractal": cross_up(close, fh),
    }

    def entry(spec):
        rn, tn, _ = spec
        return L[rn] & TL[tn], S[rn] & TS[tn]

    def build(spec) -> np.ndarray:
        rn, tn, xn = spec
        ent_l, ent_s = L[rn] & TL[tn], S[rn] & TS[tn]
        if xn == "X6_24bars":
            return build_maxhold(ent_l, ent_s, 24)
        return build_pos(ent_l, ent_s, XL[xn], XS[xn])

    rules = {f"{rn}|{tn}|{xn}": (rn, tn, xn)
             for rn in L for tn in TL for xn in list(XL) + ["X6_24bars"]}
    return dict(close=close, e21=e21, e50=e50, s100=s100, rsi=rsi,
                fh=fh, fl=fl, L=L, S=S, TL=TL, TS=TS, XL=XL, XS=XS,
                rules=rules, build=build, entry=entry, n=len(close))


def load(sym: str, tf: str, data_dir: Path) -> pd.DataFrame:
    path = data_dir / f"{sym}_{tf}.csv"
    if not path.exists():
        raise SystemExit(f"missing data: {path}")
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if "spread" not in df.columns:
        df["spread"] = 0.0
    return df


def run_symbol(sym: str, tf: str, data_dir: Path) -> dict:
    df = load(sym, tf, data_dir)
    ctx = build_ctx(df)
    close = ctx["close"]
    time = df["time"]
    cost = cost_per_side(df, sym)

    results = []
    for name, spec in ctx["rules"].items():
        pos = ctx["build"](spec)
        ret = returns_from_pos(pos, close, cost)
        st = split_stats(ret, time)
        results.append(dict(sym=sym, rule=name, rt=rt_count(pos),
                            design=st["design"]["total"],
                            validation=st["validation"]["total"],
                            oos=st["OOS"]["total"],
                            sharpe=st["OOS"]["sharpe"], dd=st["OOS"]["dd"],
                            all3=passes(st), ret=ret))
    return dict(sym=sym, n=len(close), time=time, cost=cost, close=close,
                ctx=ctx, results=results)


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--nulls", type=int, default=N_NULL)
    ap.add_argument("--symbols", nargs="*", default=list(SYMBOLS))
    args = ap.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else DATA
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    from period_returns import publish_results  # noqa: PLC0415

    runs, rows, ens_series = {}, [], {}
    for sym in args.symbols:
        run = runs[sym] = run_symbol(sym, args.tf, data_dir)
        n_rules = len(run["results"])
        n_pass = sum(1 for r in run["results"] if r["all3"])
        print(f"\n=== {sym} {args.tf}  ({run['n']} bars, {n_rules} rules) ===")
        print(f"  positive in all three splits: {n_pass}/{n_rules} "
              f"({100 * n_pass / n_rules:.1f}%)")
        best = sorted(run["results"],
                      key=lambda r: min(r["design"], r["validation"]),
                      reverse=True)[:5]
        print(f"  {'rule':<34}{'RT':>6}{'design':>9}{'valid':>9}{'OOS':>9}{'SR':>7}")
        for r in best:
            print(f"  {r['rule']:<34}{r['rt']:>6}{r['design']:>+9.2f}"
                  f"{r['validation']:>+9.2f}{r['oos']:>+9.2f}{r['sharpe']:>7.2f}")

        mat = np.vstack([r["ret"] for r in run["results"]])
        ens = mat.mean(axis=0)
        st = split_stats(ens, run["time"])
        print(f"  ENSEMBLE of all {n_rules} rules:")
        for sp in ("design", "validation", "OOS"):
            print(f"    {sp:<11}{st[sp]['total']:>+8.2f}%  Sharpe "
                  f"{st[sp]['sharpe']:>5.2f}  maxDD {st[sp]['dd']:>6.2f}%")
        ens_series[sym] = (ens, run["time"])
        rows += [{k: v for k, v in r.items() if k != "ret"}
                 for r in run["results"]]

    # ---------------------------------------------------------------- controls
    print("\n" + "=" * 78)
    print("CHANCE CONTROLS: same trade counts, timing shuffled")
    print("=" * 78)
    rng = np.random.default_rng(11)
    null_pass, null_total = 0, 0
    for sym in args.symbols:
        run = runs[sym]
        ctx, cost, close = run["ctx"], run["cost"], run["close"]
        names = list(ctx["rules"])
        picks = rng.choice(len(names), size=min(args.nulls, len(names)),
                           replace=False)
        pe_n = px_n = pe_p = px_p = 0
        for i in picks:
            rn, tn, xn = ctx["rules"][names[i]]
            ent_l, ent_s = ctx["entry"]((rn, tn, xn))
            if xn == "X6_24bars":
                variants = [build_maxhold(rng.permutation(ent_l),
                                          rng.permutation(ent_s), 24)]
                labels = ["entry"]
            else:
                exi_l, exi_s = ctx["XL"][xn], ctx["XS"][xn]
                variants = [build_pos(rng.permutation(ent_l),
                                      rng.permutation(ent_s), exi_l, exi_s),
                            build_pos(ent_l, ent_s,
                                      rng.permutation(exi_l),
                                      rng.permutation(exi_s))]
                labels = ["entry", "exit"]
            for lab, pos in zip(labels, variants):
                st = split_stats(returns_from_pos(pos, close, cost), run["time"])
                ok = passes(st)
                if lab == "entry":
                    pe_n += 1
                    pe_p += ok
                else:
                    px_n += 1
                    px_p += ok
        null_pass += pe_p + px_p
        null_total += pe_n + px_n
        print(f"  {sym}: random-entry {pe_p}/{pe_n} pass all three, "
              f"random-exit {px_p}/{px_n} pass all three")

    real_n = len(rows)
    real_p = sum(1 for r in rows if r["all3"])
    print(f"\n  REAL grid      : {real_p}/{real_n} pass all three "
          f"({100 * real_p / real_n:.1f}%)")
    print(f"  RANDOM controls: {null_pass}/{null_total} pass all three "
          f"({100 * null_pass / null_total:.1f}%)")
    if null_total:
        z = (real_p / real_n - null_pass / null_total) / np.sqrt(
            (null_pass / null_total) * (1 - null_pass / null_total) / null_total)
        print(f"  difference {100 * (real_p / real_n - null_pass / null_total):+.1f}pp "
              f"(z = {z:+.2f} against the control)")

    out = ROOT / "research" / "results" / "template_lens_sweep.csv"
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"\nwrote {out}  ({len(rows)} rows)")

    for sym, (ens, time) in ens_series.items():
        publish_results(f"template_lens_ens_{sym}_{args.tf}",
                        pd.Series(ens, index=pd.DatetimeIndex(time)),
                        append_to_bundle=False)
        print(f"published template_lens_ens_{sym}_{args.tf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
