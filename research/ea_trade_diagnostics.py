#!/usr/bin/env python3
"""Replay actual EA trades from the audit CSVs and compare exit policies.

The entry is taken from the EA's real fill.  The initial risk distance is
reconstructed from ``risk_money`` and the filled volume, so traffic-light risk
scaling cancels out.  Alternative exits are simulated on M5 bars with the same
conservative rules used in the research suite:

* if stop and target are touched in the same bar, the stop is assumed first;
* a trailing stop is moved only on the bar close;
* a fixed round-trip cost is charged to every simulated exit.

This is a diagnostic, not a replacement for an MT5 run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_M5 = HERE / "data" / "EURUSD_M5.csv"
DEFAULT_AUDIT = ROOT / "backtest" / "audit" / "apr_oct_base"
OUT = HERE / "results"
POINT = 0.00001
CONTRACT = 100000.0
COST_PTS = 11.0


def load_trades(audit_dir: Path) -> pd.DataFrame:
    ev = pd.read_csv(audit_dir / "trade_events.csv")
    oc = pd.read_csv(audit_dir / "trade_outcomes.csv")
    ev["dt"] = pd.to_datetime(ev["server_time"], utc=True)
    ins = ev[ev["entry"] == "IN"]
    outs = ev[ev["entry"] == "OUT"]
    rows = []
    for _, entry_row in ins.iterrows():
        pos = entry_row["position_id"]
        out_rows = outs[outs["position_id"] == pos]
        outcome_rows = oc[oc["position_id"] == pos]
        if out_rows.empty or outcome_rows.empty:
            continue
        out_row = out_rows.iloc[0]
        outcome = outcome_rows.iloc[0]
        side = 1 if entry_row["deal_type"] == "BUY" else -1
        volume = float(entry_row["volume"])
        risk_money = float(outcome["risk_money"])
        risk_pts = risk_money / (volume * CONTRACT) / POINT
        if not np.isfinite(risk_pts) or risk_pts <= 0:
            continue
        rows.append(
            {
                "position_id": int(pos),
                "side": side,
                "entry_time": entry_row["dt"],
                "exit_time": out_row["dt"],
                "entry": float(entry_row["price"]),
                "exit": float(out_row["price"]),
                "volume": volume,
                "risk_pts": risk_pts,
                "actual_r": float(outcome["r_multiple"]),
                "profit": float(outcome["profit"]),
                "comment": str(out_row["comment"]),
            }
        )
    return pd.DataFrame(rows)


def simulate(
    trades: pd.DataFrame,
    px: pd.DataFrame,
    *,
    tp_r: float = 3.0,
    time_bars: int | None = None,
    time_min_r: float | None = None,
    be_at_r: float | None = None,
    trail_start_r: float | None = None,
    trail_dist_r: float | None = None,
    partial_at_r: float | None = None,
    partial_fraction: float = 0.5,
    cost_pts: float = COST_PTS,
    horizon: int = 288,
) -> pd.DataFrame:
    idx = {ts: i for i, ts in enumerate(px["time"])}
    times = px["time"].to_numpy()
    highs = px["high"].to_numpy()
    lows = px["low"].to_numpy()
    closes = px["close"].to_numpy()

    rows = []
    for t in trades.itertuples(index=False):
        i0 = idx.get(t.entry_time)
        if i0 is None:
            # Nearest bar at or before the entry timestamp.
            pos = int(np.searchsorted(times, np.datetime64(t.entry_time), side="right") - 1)
            if pos < 0:
                continue
            i0 = pos
        side = int(t.side)
        entry = float(t.entry)
        r_pts = float(t.risk_pts)
        cost_r = cost_pts / r_pts
        sl = entry - side * r_pts * POINT
        tp = entry + side * tp_r * r_pts * POINT
        mfe = 0.0
        mae = 0.0
        exit_r = None
        reason = "horizon"
        partial_taken = False
        partial_r = 0.0
        time_checked = False
        end = min(i0 + horizon, len(closes) - 1)

        for k in range(i0, end + 1):
            hi, lo, cl = highs[k], lows[k], closes[k]
            if side > 0:
                fav = (hi - entry) / POINT / r_pts
                adv = (entry - lo) / POINT / r_pts
            else:
                fav = (entry - lo) / POINT / r_pts
                adv = (hi - entry) / POINT / r_pts
            mfe = max(mfe, fav)
            mae = min(mae, adv)

            # Stop first (conservative), then target.
            if side > 0:
                if lo <= sl:
                    exit_r = (sl - entry) / POINT / r_pts - cost_r
                    reason = "be" if abs(sl - entry) < POINT * 0.5 else "sl"
                    break
                if hi >= tp:
                    exit_r = (tp - entry) / POINT / r_pts - cost_r
                    reason = "tp"
                    break
            else:
                if hi >= sl:
                    exit_r = (entry - sl) / POINT / r_pts - cost_r
                    reason = "be" if abs(sl - entry) < POINT * 0.5 else "sl"
                    break
                if lo <= tp:
                    exit_r = (entry - tp) / POINT / r_pts - cost_r
                    reason = "tp"
                    break

            cur_r = (cl - entry) * side / POINT / r_pts

            # Partial close at a fixed R.  Do not assume the partial filled if
            # the same bar also hit the stop above.
            if partial_at_r is not None and not partial_taken and mfe >= partial_at_r:
                partial_taken = True
                partial_r = partial_fraction * (partial_at_r - cost_r)
                # Move the remaining stop to break-even.
                sl = entry
                if partial_fraction >= 1.0:
                    exit_r = partial_r
                    reason = "partial"
                    break

            if time_bars is not None and not time_checked and (k - i0) >= time_bars:
                time_checked = True
                if time_min_r is not None and cur_r < time_min_r:
                    exit_r = cur_r - cost_r
                    reason = "time"
                    break

            if be_at_r is not None and cur_r >= be_at_r:
                sl = entry

            if trail_start_r is not None and trail_dist_r is not None and cur_r >= trail_start_r:
                if side > 0:
                    sl = max(sl, cl - trail_dist_r * r_pts * POINT)
                else:
                    sl = min(sl, cl + trail_dist_r * r_pts * POINT)

        if exit_r is None:
            exit_r = (closes[end] - entry) * side / POINT / r_pts - cost_r
            reason = "horizon"

        if partial_taken and partial_fraction < 1.0:
            exit_r = partial_r + (1.0 - partial_fraction) * exit_r

        rows.append(
            {
                "position_id": t.position_id,
                "side": side,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "actual_r": t.actual_r,
                "exit_r": exit_r,
                "reason": reason,
                "mfe": mfe,
                "mae": mae,
                "risk_pts": r_pts,
                "cost_r": cost_r,
            }
        )
    return pd.DataFrame(rows)


def metrics(d: pd.DataFrame) -> dict:
    if d.empty:
        return {"n": 0, "win": 0.0, "avg": 0.0, "total": 0.0, "pf": 0.0}
    wins = d["exit_r"] > 0
    gp = float(d.loc[wins, "exit_r"].sum())
    gl = float(-d.loc[~wins, "exit_r"].sum())
    return {
        "n": int(len(d)),
        "win": round(float(wins.mean() * 100), 1),
        "avg": round(float(d["exit_r"].mean()), 3),
        "total": round(float(d["exit_r"].sum()), 2),
        "pf": round(gp / gl, 2) if gl > 0 else 999.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-dir", default=str(DEFAULT_AUDIT))
    parser.add_argument("--m5", default=str(DEFAULT_M5))
    parser.add_argument("--prefix", default="ea_exit_diagnostics")
    parser.add_argument(
        "--cost-pts",
        type=float,
        default=0.0,
        help="Extra round-trip cost in points. Actual EA fills already include spread, so 0 is the default.",
    )
    args = parser.parse_args()

    audit_dir = Path(args.audit_dir)
    trades = load_trades(audit_dir)
    px = pd.read_csv(args.m5, parse_dates=["time"]).sort_values("time").reset_index(drop=True)
    px["time"] = pd.to_datetime(px["time"], utc=True)

    policies = {
        "base_tp3": dict(),
        "be_1r": dict(be_at_r=1.0),
        "partial_1r_50": dict(partial_at_r=1.0, partial_fraction=0.5),
        "trail_1_05": dict(trail_start_r=1.0, trail_dist_r=0.5),
        "trail_15_075": dict(trail_start_r=1.5, trail_dist_r=0.75),
        "time_48": dict(time_bars=48),
        "time_48_mfe1": dict(time_bars=48, time_min_r=1.0),
        "trail_1_05_time48": dict(trail_start_r=1.0, trail_dist_r=0.5, time_bars=48),
        "trail_1_05_time48_mfe1": dict(
            trail_start_r=1.0, trail_dist_r=0.5, time_bars=48, time_min_r=1.0
        ),
    }

    rows = []
    summary = []
    for name, kwargs in policies.items():
        res = simulate(trades, px, cost_pts=args.cost_pts, **kwargs)
        res["policy"] = name
        rows.append(res)
        m = metrics(res)
        # The actual baseline is from the EA; the simulated baseline is the
        # no-trail control.  Report both.
        summary.append({"policy": name, **m})
        print(f"{name:<28} n={m['n']:>3} win={m['win']:>5.1f}% avgR={m['avg']:>7.3f} totalR={m['total']:>7.2f} PF={m['pf']:>5.2f}")

    allres = pd.concat(rows, ignore_index=True)
    OUT.mkdir(parents=True, exist_ok=True)
    allres.to_csv(OUT / f"{args.prefix}.csv", index=False)
    pd.DataFrame(summary).to_csv(OUT / f"{args.prefix}_summary.csv", index=False)

    base = allres[allres["policy"] == "base_tp3"].copy()
    base["giveback"] = base["mfe"] - base["actual_r"]
    print("\n--- actual EA baseline ---")
    print(
        f"n={len(trades)} win={(trades.actual_r > 0).mean() * 100:.1f}% "
        f"avgR={trades.actual_r.mean():.3f} totalR={trades.actual_r.sum():.2f}"
    )
    print(
        f"avg MFE={base.mfe.mean():.2f}R avg MAE={base.mae.mean():.2f}R "
        f"winners giveback={base.loc[base.actual_r > 0, 'giveback'].mean():.2f}R"
    )
    print(
        f"losers that reached +1R={int(((base.actual_r <= 0) & (base.mfe >= 1)).sum())}/"
        f"{int((base.actual_r <= 0).sum())}, +1.5R="
        f"{int(((base.actual_r <= 0) & (base.mfe >= 1.5)).sum())}, +2R="
        f"{int(((base.actual_r <= 0) & (base.mfe >= 2)).sum())}"
    )
    print("\n--- by side, actual ---")
    for side, label in [(1, "long"), (-1, "short")]:
        s = trades[trades.side == side]
        if not s.empty:
            print(
                f"{label:<5} n={len(s):>2} win={(s.actual_r > 0).mean() * 100:>5.1f}% "
                f"avgR={s.actual_r.mean():>7.3f} totalR={s.actual_r.sum():>7.2f}"
            )
    print(f"\nSaved: {OUT / (args.prefix + '.csv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
