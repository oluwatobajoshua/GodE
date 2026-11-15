#!/usr/bin/env python3
"""Can ML improve the ONE edge that survived - not by finding direction, but by
ranking its trades?

Everything else in this project says the market has no strong directional signal.
But `rsi_band_net` (EURUSD H1) IS a real net-positive edge. The question here is
different from "can ML predict price":

    Can a classifier look at the state of the market at ENTRY and tell which of the
    rule's own trades will win?

This is meta-labelling. The direction still comes from the proven rule; ML only
decides whether to take the trade. That is the correct place for ML, because it has
a real base to improve. If it works, we keep the winners and skip the losers.

WHAT IS MEASURED
  * AUC of "will this trade be profitable" (0.5 = ML cannot rank trades at all)
  * the actual net return / Sharpe / drawdown of the rule with the filter applied
  * the same three chronological splits as every other study
  * a shuffled-label control, so a leaky pipeline cannot look smart

Usage
    .\\.venv\\Scripts\\python.exe research/rsi_meta_label.py
    .\\.venv\\Scripts\\python.exe research/rsi_meta_label.py --model lgbm
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from ema_cross_harvest import ATR_P, EMA, RSI_P, ema, wilder_atr, wilder_rsi  # noqa: E402
from period_returns import publish_results  # noqa: E402
from rsi_band_net import returns, simulate, stats  # noqa: E402

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"
SPLITS = ("design", "validation", "oos")

warnings.filterwarnings("ignore")


def extract_trades(pos: np.ndarray):
    """One position at a time, so every trade is a contiguous run of +-1."""
    trades = []
    n, t = len(pos), 0
    while t < n:
        if pos[t] != 0:
            d, entry = pos[t], t
            while t < n and pos[t] == d:
                t += 1
            trades.append((entry, t, int(d)))
        else:
            t += 1
    return trades


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--model", default="both", choices=["linear", "lgbm", "both"])
    ap.add_argument("--target", default="both", choices=["win", "pnl", "both"])
    ap.add_argument("--min-hold", type=int, default=12)
    ap.add_argument("--margin", type=float, default=3.0)
    ap.add_argument("--comm-pts", type=float, default=7.0)
    ap.add_argument("--slip-pts", type=float, default=1.0)
    ap.add_argument("--floor-pts", type=float, default=1.0)
    ap.add_argument("--gate-pct", type=float, default=90.0)
    ap.add_argument("--deadband", type=float, default=0.10)
    ap.add_argument("--block", type=int, default=50, help="trades per walk-forward block")
    ap.add_argument("--min-train", type=int, default=150)
    args = ap.parse_args()

    sym, tf = args.symbol.upper(), args.tf.upper()
    path = DATA / f"{sym}_{tf}.csv"
    if not path.exists():
        raise SystemExit(f"missing data: {path}")

    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)

    df["ema"] = ema(df["close"], EMA)
    df["rsi"] = wilder_rsi(df["close"], RSI_P)
    df["atr"] = wilder_atr(df, ATR_P)
    df = df.dropna(subset=["ema", "rsi", "atr"]).reset_index(drop=True)

    close = df["close"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    spread = df["spread"].astype(float).to_numpy()
    time = df["time"]
    n = len(df)
    point = 0.001 if sym.endswith("JPY") else 0.00001

    slope3 = np.zeros(n)
    slope3[3:] = (e[3:] - e[:-3]) / np.maximum(atr[3:], 1e-12)
    trend = np.where(slope3 > args.deadband, 1,
                     np.where(slope3 < -args.deadband, -1, 0))

    mid = np.full(n, 50.0)
    hi = np.full(n, 75.0)
    lo = np.full(n, 25.0)

    cost_side = (np.maximum(spread, args.floor_pts) / 2.0
                 + args.slip_pts + args.comm_pts / 2.0) * point
    q = pd.Series(spread).rolling(500, min_periods=100).quantile(args.gate_pct / 100.0)
    spread_ok = (spread <= q.to_numpy()) | ~np.isfinite(q.to_numpy())

    cfg = dict(cooldown=0, min_hold=args.min_hold, margin=args.margin)
    pos = simulate(rsi, mid, hi, lo, trend, close, spread_ok, cost_side, n, cfg)
    trades = extract_trades(pos)

    # ---- features known at the ENTRY bar only
    atr_ma = pd.Series(atr).rolling(100, min_periods=20).mean().to_numpy()
    med = pd.Series(spread).rolling(500, min_periods=50).median().clip(lower=1.0).to_numpy()
    ret5 = np.zeros(n)
    ret20 = np.zeros(n)
    ret5[5:] = close[5:] / close[:-5] - 1.0
    ret20[20:] = close[20:] / close[:-20] - 1.0
    hours = time.dt.hour.to_numpy()
    dows = time.dt.dayofweek.to_numpy()

    rows, pnl, direction, t_entry, t_exit = [], [], [], [], []
    prev_exit = -10 ** 9
    for entry, exit_, d in trades:
        if exit_ >= n:
            continue
        gross = d * (close[exit_] / close[entry] - 1.0)
        cost = cost_side[entry] / close[entry] + cost_side[exit_] / close[exit_]
        p = gross - cost
        rows.append([
            rsi[entry], rsi[entry] - 50.0, rsi[entry] - rsi[entry - 1],
            (close[entry] - e[entry]) / atr[entry],
            atr[entry] / max(atr_ma[entry], 1e-12),
            spread[entry] / med[entry],
            np.sin(2 * np.pi * hours[entry] / 24), np.cos(2 * np.pi * hours[entry] / 24),
            float(dows[entry]), ret5[entry] * 100, ret20[entry] * 100,
            float(d), slope3[entry], float(min(entry - prev_exit, 500)),
        ])
        pnl.append(p)
        direction.append(d)
        t_entry.append(entry)
        t_exit.append(exit_)
        prev_exit = exit_

    X = np.asarray(rows, float)
    pnl = np.asarray(pnl)
    win = (pnl > 0).astype(int)
    t_entry = np.asarray(t_entry)
    t_exit = np.asarray(t_exit)
    m = len(X)

    t0, t1 = time.iloc[0], time.iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80
    bounds = {"design": (t0, edge1), "validation": (edge1, edge2), "oos": (edge2, t1)}
    bars_per_year = 24 * 252

    print(f"=== meta-labelling the RSI band edge  {sym} {tf} ===")
    print(f"rule: min-hold {args.min_hold}, margin {args.margin:.1f}, "
          f"spread gate p{args.gate_pct:.0f}")
    print(f"trades {m}   win rate {100 * win.mean():.1f}%   "
          f"mean net per trade {100 * pnl.mean():+.4f}%")
    print("")

    # ---- walk-forward predictions on trades (blocks of N trades)
    def predict(model_name, shuffle=False, target="win"):
        """Score each trade using only trades whose outcome was already known.

        target='win' ranks by P(profitable) - the intuitive target.
        target='pnl' ranks by predicted net return - the correct target for a rule
        that wins only 40% of the time and earns from asymmetric payoff, because a
        win/loss label throws away exactly the information that makes the edge.
        """
        p = np.full(m, np.nan)
        rng = np.random.default_rng(7)
        for lo_i in range(args.min_train, m, args.block):
            hi_i = min(lo_i + args.block, m)
            Xtr = X[:lo_i]
            if target == "win":
                ytr = win[:lo_i].copy()
                if len(np.unique(ytr)) < 2:
                    continue
            else:
                ytr = pnl[:lo_i].copy()
            if shuffle:
                ytr = rng.permutation(ytr)
            sc = StandardScaler().fit(Xtr)
            if model_name == "linear":
                mdl = (LogisticRegression(max_iter=500, C=0.1) if target == "win"
                       else Ridge(alpha=10.0))
            else:
                import lightgbm as lgb
                mdl = (lgb.LGBMClassifier(
                    n_estimators=200, learning_rate=0.05, num_leaves=15,
                    min_child_samples=30, subsample=0.8, subsample_freq=1,
                    colsample_bytree=0.8, reg_lambda=1.0, n_jobs=-1,
                    verbose=-1, random_state=0) if target == "win"
                       else lgb.LGBMRegressor(
                    n_estimators=200, learning_rate=0.05, num_leaves=15,
                    min_child_samples=30, subsample=0.8, subsample_freq=1,
                    colsample_bytree=0.8, reg_lambda=1.0, n_jobs=-1,
                    verbose=-1, random_state=0))
            mdl.fit(sc.transform(Xtr), ytr)
            if target == "win":
                p[lo_i:hi_i] = mdl.predict_proba(sc.transform(X[lo_i:hi_i]))[:, 1]
            else:
                p[lo_i:hi_i] = mdl.predict(sc.transform(X[lo_i:hi_i]))
        return p

    def series_from(accepted_idx):
        """Rebuild the position series using only the accepted trades."""
        pos2 = np.zeros(n, dtype=int)
        for i in accepted_idx:
            pos2[t_entry[i]:t_exit[i]] = direction[i]
        return returns(pos2, close, cost_side), len(accepted_idx)

    def split_stats(ret):
        out = {}
        for sp in SPLITS:
            a, b = bounds[sp]
            mask = ((time >= a) & (time < b)).to_numpy()
            out[sp] = stats(ret[mask], bars_per_year)
        return out

    base_ret, base_n = series_from(range(m))
    base_s = split_stats(base_ret)
    print("--- the rule as it stands (all trades taken) ---")
    print(f"  {'split':<12}{'trades':>7}{'total':>10}{'Sharpe':>8}{'maxDD':>9}")
    for sp in SPLITS:
        print(f"  {sp:<12}{base_n:>7}{base_s[sp]['total'] * 100:>+9.2f}%"
              f"{base_s[sp]['sharpe']:>8.2f}{base_s[sp]['dd'] * 100:>8.2f}%")
    print("")

    models = ["linear", "lgbm"] if args.model == "both" else [args.model]
    targets = ["win", "pnl"] if args.target == "both" else [args.target]
    results = []
    for target in targets:
        for name in models:
            pred = predict(name, target=target)
            ok = np.isfinite(pred)
            auc = roc_auc_score(win[ok], pred[ok]) if len(np.unique(win[ok])) > 1 else 0.5
            shuf = predict(name, shuffle=True, target=target)
            oks = np.isfinite(shuf)
            auc_s = (roc_auc_score(win[oks], shuf[oks])
                     if len(np.unique(win[oks])) > 1 else 0.5)
            print(f"--- {name} / target={target}: can it rank the rule's trades? ---")
            print(f"  rank AUC against trade PnL {auc:.4f}   "
                  f"(shuffled control {auc_s:.4f})")

            #---- apples to apples: only the trades the model was allowed to score,
            #---- because the first min-train trades have no prediction at all
            base_keep = [i for i in range(m) if ok[i]]
            bret, bn = series_from(base_keep)
            bs = split_stats(bret)
            bfull = stats(bret, bars_per_year)
            print(f"  {'all scored trades':<22} keep {bn:>4}  "
                  f"D {bs['design']['total'] * 100:>+6.2f}%  "
                  f"V {bs['validation']['total'] * 100:>+6.2f}%  "
                  f"OOS {bs['oos']['total'] * 100:>+6.2f}%  "
                  f"Sharpe {bfull['sharpe']:>5.2f}  DD {bfull['dd'] * 100:>6.2f}%")
            results.append(dict(model=name, target=target, keep_pct=100, trades=bn,
                                design=bs["design"]["total"] * 100,
                                validation=bs["validation"]["total"] * 100,
                                oos=bs["oos"]["total"] * 100,
                                sharpe_full=bfull["sharpe"],
                                dd_full=bfull["dd"] * 100))

            for q in (80, 60, 40, 20):
                cut = float(np.nanquantile(pred, 1 - q / 100.0))
                keep = [i for i in range(m) if ok[i] and pred[i] >= cut]
                if len(keep) < 20:
                    continue
                ret, cnt = series_from(keep)
                s = split_stats(ret)
                full = stats(ret, bars_per_year)
                results.append(dict(model=name, target=target, keep_pct=q, trades=cnt,
                                    design=s["design"]["total"] * 100,
                                    validation=s["validation"]["total"] * 100,
                                    oos=s["oos"]["total"] * 100,
                                    sharpe_full=full["sharpe"],
                                    dd_full=full["dd"] * 100))
                print(f"  {'keep best ' + str(q) + '%':<22} keep {cnt:>4}  "
                      f"D {s['design']['total'] * 100:>+6.2f}%  "
                      f"V {s['validation']['total'] * 100:>+6.2f}%  "
                      f"OOS {s['oos']['total'] * 100:>+6.2f}%  "
                      f"Sharpe {full['sharpe']:>5.2f}  DD {full['dd'] * 100:>6.2f}%")
            print("")

    tbl = pd.DataFrame(results)
    if len(tbl):
        tbl["worst_dv"] = tbl[["design", "validation"]].min(axis=1)
        base = tbl[tbl.keep_pct == 100]
        filt = tbl[tbl.keep_pct < 100]
        print("--- best filter by the WORST of design+validation (OOS never used) ---")
        for _, r in base.iterrows():
            print(f"  baseline {r.model}/{r.target:<4}       worst D+V {r.worst_dv:>+6.2f}%  "
                  f"OOS {r.oos:>+6.2f}%  Sharpe {r.sharpe_full:>5.2f}")
        if len(filt):
            best = filt.sort_values("worst_dv", ascending=False).iloc[0]
            bb = base[(base.model == best.model) & (base.target == best.target)].iloc[0]
            print(f"  {best.model}/{best.target:<4} keep {best.keep_pct:>3}%  "
                  f"worst D+V {best.worst_dv:>+6.2f}%  OOS {best.oos:>+6.2f}%  "
                  f"Sharpe {best.sharpe_full:>5.2f}")
            verdict = ("IMPROVES the edge" if best.worst_dv > bb.worst_dv + 0.5
                       else "does NOT improve the edge")
            print(f"  -> the filter {verdict}")

            #---- NULL TEST: keep the same fraction of trades but assign the scores
            #---- at random. If random cuts do as well, the ranking carried nothing
            #---- and the apparent improvement is noise from one lucky cut.
            real_pred = predict(best.model, target=best.target)
            okp = np.isfinite(real_pred)
            rng = np.random.default_rng(99)
            nulls = []
            for _ in range(args.perm):
                sp = real_pred.copy()
                idx = np.flatnonzero(okp)
                vals = sp[idx].copy()
                rng.shuffle(vals)
                sp[idx] = vals
                cut = float(np.nanquantile(sp, 1 - best.keep_pct / 100.0))
                keep = [i for i in range(m) if okp[i] and sp[i] >= cut]
                r2, _ = series_from(keep)
                s2 = split_stats(r2)
                nulls.append(min(s2["design"]["total"],
                                 s2["validation"]["total"]) * 100)
            nulls = np.asarray(nulls)
            pct = float((nulls >= best.worst_dv).mean())
            print(f"  null test ({args.perm} random cuts of the same {best.keep_pct}%):")
            print(f"    random worst D+V median {np.median(nulls):>+6.2f}%  "
                  f"p90 {np.percentile(nulls, 90):>+6.2f}%")
            print(f"    real worst D+V {best.worst_dv:>+6.2f}%  -> beaten by random "
                  f"{100 * pct:.0f}% of the time")
            if pct > 0.10:
                print("  -> the ranking is NOT informative: any cut of this size does")
                print("     as well, so this 'improvement' is noise. Do not deploy it.")
            else:
                print("  -> the ranking beats random cuts, so the filter carries")
                print("     information beyond chance.")
            #--- monthly proof for the comparison that matters: rule as it stands
            bb_ok = np.isfinite(predict(bb.model, target=bb.target))
            bret2, _ = series_from([i for i in range(m) if bb_ok[i]])
            publish_results(f"rsi_meta_{bb.model}_{bb.target}_baseline",
                            pd.Series(bret2, index=pd.DatetimeIndex(time)))
        print("")

        RESULTS.mkdir(parents=True, exist_ok=True)
        tbl.to_csv(RESULTS / f"rsi_meta_label_{sym}_{tf}.csv", index=False)
        print(f"wrote {RESULTS / f'rsi_meta_label_{sym}_{tf}.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
