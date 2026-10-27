#!/usr/bin/env python3
"""Replay candidate entries with alternative exit policies.

The candidate file fixes entries and 1R risk.  This script holds the entry
constant and changes only the exit:

* fixed 3R target / 1R stop (baseline)
* time stop after N M5 bars
* conditional time stop (only if the trade has not made progress)
* trailing stop after +1R, 0.5R behind the best close
* combinations

Conservative assumptions: if stop and target are touched in the same bar, the
stop is assumed first; trailing stops are moved only on bar closes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cross_asset_analysis import add_directional  # noqa: E402

MODEL = HERE / "models"
DATA = HERE / "data"
OUT = HERE / "results"
POINT = 0.00001


def load_candidates() -> pd.DataFrame:
    cand = pd.read_csv(MODEL / "candidates_M5.csv", parse_dates=["signal_time", "entry_time"])
    ca = pd.read_csv(MODEL / "cross_asset_M5.csv", parse_dates=["time"]).sort_values("time")
    cand = cand.sort_values("signal_time")
    cand = pd.merge_asof(
        cand, ca, left_on="signal_time", right_on="time", direction="backward",
        tolerance=pd.Timedelta("6min"),
    ).dropna(subset=["dxy_ret6_atr", "vix_chg6_z", "flow_12"]).reset_index(drop=True)
    return add_directional(cand)


def replay(
    rows: pd.DataFrame,
    o: np.ndarray,
    h: np.ndarray,
    l: np.ndarray,
    c: np.ndarray,
    idx: dict[pd.Timestamp, int],
    *,
    horizon: int = 288,
    cost_pts: float = 11.0,
    tp_r: float = 3.0,
    time_bars: int | None = None,
    time_min_r: float | None = None,
    time_mfe_below: float | None = None,
    trail_start_r: float | None = None,
    trail_dist_r: float | None = None,
) -> pd.DataFrame:
    out = []
    for row in rows.itertuples(index=False):
        t = pd.Timestamp(row.entry_time)
        i0 = idx.get(t)
        if i0 is None:
            continue
        side = int(row.side)
        entry = float(row.entry)
        r_pts = float(row.sl_dist_pts)
        if not np.isfinite(r_pts) or r_pts <= 0:
            continue
        sl = entry - side * r_pts * POINT
        tp = entry + side * tp_r * r_pts * POINT
        mfe = 0.0
        mae = 0.0
        exit_r = None
        reason = "horizon"
        end = min(i0 + horizon, len(c) - 1)
        time_checked = False
        for k in range(i0, end + 1):
            hi, lo, cl = h[k], l[k], c[k]
            # Mark-to-market extremes.
            if side > 0:
                fav = (hi - entry) / POINT / r_pts
                adv = (entry - lo) / POINT / r_pts
            else:
                fav = (entry - lo) / POINT / r_pts
                adv = (hi - entry) / POINT / r_pts
            mfe = max(mfe, fav)
            mae = min(mae, adv)

            # Stop first, then target, both conservative.
            if side > 0:
                if lo <= sl:
                    exit_r = (sl - entry) / POINT / r_pts - cost_pts / r_pts
                    reason = "sl"
                    break
                if hi >= tp:
                    exit_r = (tp - entry) / POINT / r_pts - cost_pts / r_pts
                    reason = "tp"
                    break
            else:
                if hi >= sl:
                    exit_r = (entry - sl) / POINT / r_pts - cost_pts / r_pts
                    reason = "sl"
                    break
                if lo <= tp:
                    exit_r = (entry - tp) / POINT / r_pts - cost_pts / r_pts
                    reason = "tp"
                    break

            held = k - i0
            cur_r = (cl - entry) * side / POINT / r_pts
            # Conditional time stop is evaluated once, at the chosen bar.
            if time_bars is not None and not time_checked and held >= time_bars:
                time_checked = True
                if time_mfe_below is not None:
                    if mfe < time_mfe_below:
                        exit_r = cur_r - cost_pts / r_pts
                        reason = "time"
                        break
                elif time_min_r is not None:
                    if cur_r < time_min_r:
                        exit_r = cur_r - cost_pts / r_pts
                        reason = "time"
                        break
                else:
                    exit_r = cur_r - cost_pts / r_pts
                    reason = "time"
                    break

            # Trail on bar close: only after the trade has reached +start R.
            if trail_start_r is not None and trail_dist_r is not None:
                if cur_r >= trail_start_r:
                    if side > 0:
                        sl = max(sl, cl - trail_dist_r * r_pts * POINT)
                    else:
                        sl = min(sl, cl + trail_dist_r * r_pts * POINT)

        if exit_r is None:
            exit_r = (c[end] - entry) * side / POINT / r_pts - cost_pts / r_pts
            reason = "horizon"
        out.append(
            {
                "signal_time": row.signal_time,
                "side": side,
                "mode": int(row.mode),
                "filter_mode1_long_dxy": int(row.mode == 1 and side > 0 and row.dxy_align > 0),
                "filter_mode1_dxy": int(row.mode == 1 and row.dxy_align > 0),
                "filter_m0_short": int(row.mode == 0 and side < 0),
                "exit_r": exit_r,
                "reason": reason,
                "mfe": mfe,
                "mae": mae,
            }
        )
    return pd.DataFrame(out)


def metrics(d: pd.DataFrame) -> dict:
    if d.empty:
        return {"n": 0, "win": 0.0, "avg": 0.0, "total": 0.0, "pf": 0.0}
    w = d["exit_r"] > 0
    gp = float(d.loc[w, "exit_r"].sum())
    gl = float(-d.loc[~w, "exit_r"].sum())
    return {
        "n": int(len(d)),
        "win": round(float(w.mean() * 100), 1),
        "avg": round(float(d["exit_r"].mean()), 3),
        "total": round(float(d["exit_r"].sum()), 1),
        "pf": round(gp / gl, 2) if gl > 0 else 999.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(OUT / "candidate_exit_policies.csv"))
    args = parser.parse_args()

    cand = load_candidates()
    px = pd.read_csv(DATA / "EURUSD_M5.csv", parse_dates=["time"]).sort_values("time").reset_index(drop=True)
    o = px["open"].to_numpy()
    h = px["high"].to_numpy()
    l = px["low"].to_numpy()
    c = px["close"].to_numpy()
    idx = {ts: i for i, ts in enumerate(px["time"])}

    train_end = pd.Timestamp("2026-05-01", tz="UTC")
    val_end = pd.Timestamp("2026-07-01", tz="UTC")

    policies = {
        "base_tp3": dict(),
        "time_24": dict(time_bars=24),
        "time_36": dict(time_bars=36),
        "time_48": dict(time_bars=48),
        "time_72": dict(time_bars=72),
        "time_48_flat": dict(time_bars=48, time_min_r=0.5),
        "time_48_mfe1": dict(time_bars=48, time_mfe_below=1.0),
        "time_72_mfe1": dict(time_bars=72, time_mfe_below=1.0),
        "trail_1_05": dict(trail_start_r=1.0, trail_dist_r=0.5),
        "trail_15_075": dict(trail_start_r=1.5, trail_dist_r=0.75),
        "trail_1_05_time48": dict(trail_start_r=1.0, trail_dist_r=0.5, time_bars=48),
        "trail_1_05_time48_flat": dict(trail_start_r=1.0, trail_dist_r=0.5, time_bars=48, time_min_r=0.5),
        "trail_1_05_time48_mfe1": dict(trail_start_r=1.0, trail_dist_r=0.5, time_bars=48, time_mfe_below=1.0),
        "trail_15_075_time48": dict(trail_start_r=1.5, trail_dist_r=0.75, time_bars=48),
    }

    rows = []
    for name, kw in policies.items():
        res = replay(cand, o, h, l, c, idx, **kw)
        res["policy"] = name
        rows.append(res)
        print(f"replayed {name:<28} n={len(res)}")
    allres = pd.concat(rows, ignore_index=True)
    allres.to_csv(args.out, index=False)

    print("\n--- policy comparison by split (all filtered trades) ---")
    for name in policies:
        d = allres[allres.policy == name]
        tr = d[d.signal_time < train_end]
        va = d[(d.signal_time >= train_end) & (d.signal_time < val_end)]
        te = d[d.signal_time >= val_end]
        a, b, cc = metrics(tr), metrics(va), metrics(te)
        print(
            f"{name:<28} TR n={a['n']:4d} w={a['win']:5.1f}% aR={a['avg']:+.3f} tR={a['total']:+7.1f} "
            f"| VA n={b['n']:3d} w={b['win']:5.1f}% aR={b['avg']:+.3f} "
            f"| TE n={cc['n']:4d} w={cc['win']:5.1f}% aR={cc['avg']:+.3f} tR={cc['total']:+7.1f} PF={cc['pf']:.2f}"
        )

    print("\n--- mode1 long & dxy_align>0 ---")
    for name in policies:
        d = allres[(allres.policy == name) & (allres.filter_mode1_long_dxy == 1)]
        tr = d[d.signal_time < train_end]
        va = d[(d.signal_time >= train_end) & (d.signal_time < val_end)]
        te = d[d.signal_time >= val_end]
        a, b, cc = metrics(tr), metrics(va), metrics(te)
        print(
            f"{name:<28} TR n={a['n']:4d} w={a['win']:5.1f}% aR={a['avg']:+.3f} tR={a['total']:+7.1f} "
            f"| VA n={b['n']:3d} w={b['win']:5.1f}% aR={b['avg']:+.3f} "
            f"| TE n={cc['n']:4d} w={cc['win']:5.1f}% aR={cc['avg']:+.3f} tR={cc['total']:+7.1f} PF={cc['pf']:.2f}"
        )

    print("\n--- mode1 + dxy_align>0 ---")
    for name in policies:
        d = allres[(allres.policy == name) & (allres.filter_mode1_dxy == 1)]
        tr = d[d.signal_time < train_end]
        va = d[(d.signal_time >= train_end) & (d.signal_time < val_end)]
        te = d[d.signal_time >= val_end]
        a, b, cc = metrics(tr), metrics(va), metrics(te)
        print(
            f"{name:<28} TR n={a['n']:4d} w={a['win']:5.1f}% aR={a['avg']:+.3f} tR={a['total']:+7.1f} "
            f"| VA n={b['n']:3d} w={b['win']:5.1f}% aR={b['avg']:+.3f} "
            f"| TE n={cc['n']:4d} w={cc['win']:5.1f}% aR={cc['avg']:+.3f} tR={cc['total']:+7.1f} PF={cc['pf']:.2f}"
        )

    print(f"\nSaved: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
