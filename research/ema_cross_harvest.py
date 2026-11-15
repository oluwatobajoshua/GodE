"""Harvest EMA21 rides without hindsight.

The chart says: a real move is a stretch where price stays above (or below)
EMA21 and keeps going - maybe only five candles, maybe forty. Some crosses are
real, some are fakes that snap straight back.

This script answers three questions, in order:

  1. BASE RATE
     Out of every EMA21 cross, how many turn into a run of at least N candles
     that stays clean (price does not dip back through EMA21 too often)?

  2. ENTRY OFFSET
     If we enter at the close of candle 1, 2 or 3 after the cross, which one
     pays? (Entering later is safer but gives up part of the move.)

  3. FAKE vs REAL
     Using only information visible at entry, which filters separate real
     rides from fakes? Tested on unseen data, never on the data that chose them.

No ride is selected using future bars. Every trade is simulated forward from
the entry bar until stop / target / EMA21 break.

Chronological split: design 60% | validation 20% | oos 20%.

Usage
    .\\.venv\\Scripts\\python.exe research\\ema_cross_harvest.py --symbol EURUSD --tf H1
    .\\.venv\\Scripts\\python.exe research\\ema_cross_harvest.py --symbol GBPUSD --tf H1 --min-bars 5
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


# ------------------------------------------------------------------ events
def build_events(df: pd.DataFrame, min_bars: int, clean_max: float) -> pd.DataFrame:
    """Every EMA21 cross, with the run it started and entry-time features."""
    close = df["close"].to_numpy(float)
    openp = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    e50 = df["ema50"].to_numpy(float)
    s100 = df["smma"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    time = df["time"].to_numpy()
    n = len(df)

    side = np.where(close > e, 1, np.where(close < e, -1, 0))
    for i in range(1, n):
        if side[i] == 0:
            side[i] = side[i - 1]

    rows = []
    prev_start = 0
    i = 1
    while i < n:
        if side[i] == side[i - 1] or side[i] == 0:
            i += 1
            continue
        start = i
        j = i
        while j + 1 < n and side[j + 1] == side[i]:
            j += 1
        run_bars = j - start + 1

        viol = 0
        for k in range(start, j + 1):
            if side[i] > 0 and low[k] < e[k]:
                viol += 1
            elif side[i] < 0 and high[k] > e[k]:
                viol += 1
        ratio = viol / run_bars
        real = 1 if (run_bars >= min_bars and ratio <= clean_max) else 0

        rows.append(
            dict(
                i=start, side=int(side[i]), run_bars=run_bars, viol=viol,
                viol_ratio=ratio, real=real, prev_bars=start - prev_start,
                time=pd.Timestamp(time[start]),
            )
        )
        prev_start = start
        i = j + 1

    ev = pd.DataFrame(rows)
    if ev.empty:
        return ev

    # features at a given entry bar (offset 1..3), all visible at that close
    for off in (1, 2, 3):
        cols = {c: [] for c in (
            "e_i", "e_time", "e_ok", "slope3", "stack", "dist21", "dist50",
            "dist100", "body", "rsi_adj", "atr_ratio", "prev_bars_f", "hour", "dow",
            "entry_px", "avail_r",
        )}
        for _, r in ev.iterrows():
            i0 = int(r["i"])
            s = int(r["side"])
            k = i0 + off - 1
            ok = (k <= n - 1) and (side[k] == s) and (atr[k] > 0)
            if not ok:
                cols["e_i"].append(k)
                cols["e_time"].append(pd.NaT)
                cols["e_ok"].append(0)
                for c in ("slope3", "stack", "dist21", "dist50", "dist100",
                          "body", "rsi_adj", "atr_ratio", "prev_bars_f",
                          "hour", "dow", "entry_px", "avail_r"):
                    cols[c].append(np.nan)
                continue
            a = atr[k]
            cols["e_i"].append(k)
            cols["e_time"].append(pd.Timestamp(time[k]))
            cols["e_ok"].append(1)
            cols["slope3"].append((e[k] - e[k - 3]) * s / a)
            stack = 0
            if (close[k] - e[k]) * s > 0:
                stack += 1
            if (e[k] - e50[k]) * s > 0:
                stack += 1
            if (e50[k] - s100[k]) * s > 0:
                stack += 1
            cols["stack"].append(stack)
            cols["dist21"].append((close[k] - e[k]) * s / a)
            cols["dist50"].append((close[k] - e50[k]) * s / a)
            cols["dist100"].append((close[k] - s100[k]) * s / a)
            cols["body"].append((close[k] - openp[k]) * s / a)
            cols["rsi_adj"].append((rsi[k] - 50.0) * s)
            cols["atr_ratio"].append(a / atr[k - 20] if k >= 20 and atr[k - 20] > 0 else np.nan)
            cols["prev_bars_f"].append(min(int(r["prev_bars"]), 100))
            t = pd.Timestamp(time[k])
            cols["hour"].append(t.hour)
            cols["dow"].append(t.dayofweek)
            cols["entry_px"].append(float(close[k]))
            # move still available inside this run, from this entry bar
            jj = i0 + int(r["run_bars"]) - 1
            cols["avail_r"].append((float(close[jj]) - float(close[k])) * s / a)

        for c, vals in cols.items():
            ev[f"o{off}_{c}"] = vals

    return ev


# ------------------------------------------------------------------ trade sim
def simulate(df: pd.DataFrame, entry_i: int, side: int,
             stop_atr: float, mode: str, tp_atr: float,
             cost_atr: float, max_bars: int = 400) -> tuple[float, int]:
    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    n = len(df)

    entry = float(close[entry_i])
    a = float(atr[entry_i])
    stop = entry - side * stop_atr * a
    tp = entry + side * tp_atr * a if tp_atr > 0 else None
    be = False
    last = min(entry_i + max_bars, n - 1)

    for k in range(entry_i + 1, last + 1):
        stop_hit = (low[k] <= stop) if side > 0 else (high[k] >= stop)
        if stop_hit:
            return (stop - entry) * side / a - cost_atr, k
        if tp is not None:
            tp_hit = (high[k] >= tp) if side > 0 else (low[k] <= tp)
            if tp_hit:
                return (tp - entry) * side / a - cost_atr, k
        if mode == "ema_be" and not be and (close[k] - entry) * side >= a:
            stop = entry
            be = True
        if mode in ("ema", "ema_be"):
            broken = (close[k] < e[k]) if side > 0 else (close[k] > e[k])
            if broken:
                return (close[k] - entry) * side / a - cost_atr, k

    return (close[last] - entry) * side / a - cost_atr, last


# ------------------------------------------------------------------ metrics
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


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0,
                 iters: int = 1500, lr: float = 0.5) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mu = X.mean(0)
    sd = X.std(0) + 1e-9
    Z = (X - mu) / sd
    Z = np.hstack([np.ones((len(Z), 1)), Z])
    w = np.zeros(Z.shape[1])
    for _ in range(iters):
        z = np.clip(Z @ w, -30, 30)
        p = 1.0 / (1.0 + np.exp(-z))
        g = Z.T @ (p - y) / len(y)
        g[1:] += l2 * w[1:] / len(y)
        w -= lr * g
    return w, mu, sd


def predict(w: np.ndarray, mu: np.ndarray, sd: np.ndarray, X: np.ndarray) -> np.ndarray:
    Z = (X - mu) / sd
    Z = np.hstack([np.ones((len(Z), 1)), Z])
    return 1.0 / (1.0 + np.exp(-np.clip(Z @ w, -30, 30)))


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--min-bars", type=int, default=5)
    ap.add_argument("--clean-max", type=float, default=0.25)
    ap.add_argument("--cost-pts", type=float, default=12.0)
    ap.add_argument("--stop-atr", type=float, default=1.0)
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

    ev = build_events(df, args.min_bars, args.clean_max)
    if ev.empty:
        print("no cross events found")
        return 1

    t0, t1 = df["time"].iloc[0], df["time"].iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80

    def split_of(ts):
        if pd.isna(ts):
            return "none"
        if ts < edge1:
            return "design"
        if ts < edge2:
            return "validation"
        return "oos"

    ev["split"] = ev["o1_e_time"].apply(split_of)

    print(f"=== EMA{EMA} cross harvest  {sym} {tf} ===")
    print(f"bars {len(df)}  {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}")
    print(f"cost {args.cost_pts:.0f} pts = {cost_atr:.3f} ATR   stop {args.stop_atr:.2f} ATR")
    print("")
    print(f"--- 1. base rate: crosses that become a clean run >= {args.min_bars} bars ---")
    for sp in SPLITS:
        sub = ev[ev.split == sp]
        if sub.empty:
            continue
        print(f"  {sp:<11} crosses {len(sub):>5}   real {int(sub.real.sum()):>4} "
              f"({sub.real.mean() * 100:5.1f}%)   median run {sub.run_bars.median():4.1f} bars")

    # ---------------------------------------------------------- simulate once
    for off in (1, 2, 3):
        vals = np.full(len(ev), np.nan)
        for idx, r in ev.iterrows():
            if int(r[f"o{off}_e_ok"]) != 1:
                continue
            p, _ = simulate(df, int(r[f"o{off}_e_i"]), int(r["side"]),
                            args.stop_atr, "ema", 0.0, cost_atr)
            vals[idx] = p
        ev[f"o{off}_pnl"] = vals

    def pnl_of(off: int, sp: str, mask=None) -> list[float]:
        m = (ev["split"] == sp) & ev[f"o{off}_pnl"].notna()
        if mask is not None:
            m &= mask
        return ev.loc[m, f"o{off}_pnl"].tolist()

    # ---------------------------------------------------------- 2. offset
    print("")
    print(f"--- 2. entry offset (stop {args.stop_atr:.1f} ATR, exit on EMA21 break) ---")
    hdr = f"{'offset':<8}{'split':<12}{'n':>5}{'win%':>7}{'avgR':>8}{'totalR':>9}{'PF':>7}{'capture':>9}"
    print(hdr)
    print("-" * len(hdr))

    chosen: dict[int, dict] = {}
    for off in (1, 2, 3):
        for sp in SPLITS:
            pnl = pnl_of(off, sp)
            m = metrics(pnl)
            real_mask = (ev["real"] == 1) & (ev[f"o{off}_e_ok"] == 1)
            avail = float(ev.loc[real_mask & (ev["split"] == sp), f"o{off}_avail_r"].clip(lower=0).sum())
            cap = 100.0 * m["total"] / avail if avail > 0 else 0.0
            print(f"{off:<8}{sp:<12}{m['n']:>5}{m['win']:>7.1f}{m['avg']:>8.2f}"
                  f"{m['total']:>9.1f}{m['pf']:>7.2f}{cap:>8.1f}%")
            chosen.setdefault(off, {})[sp] = dict(metrics=m, cap=cap)
        print("")

    # ---------------------------------------------------------- 3. filters
    print("--- 3. fake vs real: filters using only entry-time information ---")
    print("    (offset 2 entry; same simulation; design chooses, val/oos judge)")
    print("")
    ok2 = ev["o2_e_ok"] == 1
    filters = {
        "all": ok2,
        "stack3": ok2 & (ev["o2_stack"] >= 3),
        "stack2+": ok2 & (ev["o2_stack"] >= 2),
        "slope_up": ok2 & (ev["o2_slope3"] > 0),
        "rsi_50": ok2 & (ev["o2_rsi_adj"] > 0),
        "stack3+slope": ok2 & (ev["o2_stack"] >= 3) & (ev["o2_slope3"] > 0),
        "stack3+rsi": ok2 & (ev["o2_stack"] >= 3) & (ev["o2_rsi_adj"] > 0),
        "core": ok2 & (ev["o2_stack"] >= 2) & (ev["o2_slope3"] > 0) & (ev["o2_rsi_adj"] > 0),
        "not_stretched": ok2 & (ev["o2_dist21"] <= 1.5),
    }
    hdr = f"{'filter':<18}{'split':<12}{'n':>5}{'win%':>7}{'avgR':>8}{'totalR':>9}{'PF':>7}{'real%':>7}"
    print(hdr)
    print("-" * len(hdr))
    for name, mask in filters.items():
        for sp in SPLITS:
            m = metrics(pnl_of(2, sp, mask))
            sel = ev[mask & (ev["split"] == sp)]
            rate = 100.0 * sel["real"].mean() if len(sel) else 0.0
            print(f"{name:<18}{sp:<12}{m['n']:>5}{m['win']:>7.1f}{m['avg']:>8.2f}"
                  f"{m['total']:>9.1f}{m['pf']:>7.2f}{rate:>6.1f}%")
        print("")

    # ---------------------------------------------------------- 4. logistic
    print("--- 4. logistic model: P(real ride | entry-time features), offset 2 ---")
    feats = ["o2_slope3", "o2_stack", "o2_dist21", "o2_dist50", "o2_dist100",
             "o2_body", "o2_rsi_adj", "o2_atr_ratio", "o2_prev_bars_f",
             "o2_hour", "o2_dow"]
    train = ev[(ev.split == "design") & (ev.o2_e_ok == 1)].dropna(subset=feats)
    if len(train) > 200:
        Xtr = train[feats].to_numpy(float)
        ytr = train["real"].to_numpy(int)
        w, mu, sd = fit_logistic(Xtr, ytr)
        for sp in SPLITS:
            sub = ev[(ev.split == sp) & (ev.o2_e_ok == 1)].dropna(subset=feats)
            if sub.empty:
                continue
            p = predict(w, mu, sd, sub[feats].to_numpy(float))
            print(f"  {sp:<11} n {len(sub):>5}  AUC {auc(sub['real'].to_numpy(int), p):.3f}  "
                  f"base {sub['real'].mean() * 100:5.1f}%  "
                  f"top-quartile precision {sub['real'].to_numpy(int)[p >= np.quantile(p, 0.75)].mean() * 100:5.1f}%")

        # take only the model's most confident half, chosen on design
        sub_d = ev[(ev.split == "design") & (ev.o2_e_ok == 1)].dropna(subset=feats)
        p_d = predict(w, mu, sd, sub_d[feats].to_numpy(float))
        thr = float(np.quantile(p_d, 0.5))
        print(f"  threshold (design median) {thr:.3f}")
        for sp in SPLITS:
            sub = ev[(ev.split == sp) & (ev.o2_e_ok == 1)].dropna(subset=feats)
            if sub.empty:
                continue
            p = predict(w, mu, sd, sub[feats].to_numpy(float))
            pnl = []
            for (_, r), pi in zip(sub.iterrows(), p):
                if pi < thr:
                    continue
                v, _ = simulate(df, int(r["o2_e_i"]), int(r["side"]),
                                args.stop_atr, "ema", 0.0, cost_atr)
                pnl.append(v)
            m = metrics(pnl)
            print(f"  {sp:<11} model-approved n {m['n']:>4}  totalR {m['total']:>7.1f}  "
                  f"PF {m['pf']:>5.2f}  avgR {m['avg']:>5.2f}")
    else:
        print("  not enough design events to fit")

    # ---------------------------------------------------------- publish
    print("")
    best_off = max(chosen, key=lambda o: chosen[o]["design"]["metrics"]["total"])
    sel = ev[ev[f"o{best_off}_pnl"].notna()].sort_values(f"o{best_off}_e_time")
    all_pnl = sel[f"o{best_off}_pnl"].tolist()
    all_t = sel[f"o{best_off}_e_time"].tolist()
    print(f"--- offset {best_off} chosen on design only; monthly/daily proof follows ---")
    if all_pnl and len(all_t) == len(all_pnl):
        s = pd.Series(np.asarray(all_pnl) * 0.01,
                      index=pd.DatetimeIndex(all_t)).sort_index()
        publish_results(f"ema_harvest_o{best_off}_{sym}_{tf}", s,
                        last_months=12, last_days=21)

    out = RESULTS / f"ema_cross_harvest_{sym}_{tf}.csv"
    keep = ["time", "split", "side", "run_bars", "viol_ratio", "real", "prev_bars",
            "o1_e_ok", "o2_e_ok", "o3_e_ok", "o2_stack", "o2_slope3", "o2_rsi_adj",
            "o2_dist21", "o2_avail_r"]
    ev[[c for c in keep if c in ev.columns]].to_csv(out, index=False)
    print("")
    print(f"wrote {out}")
    print("")
    print("Read it this way: a rule only counts if design, validation AND oos are")
    print("all positive after cost. Anything else stays a research note.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
