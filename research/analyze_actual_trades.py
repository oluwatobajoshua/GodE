#!/usr/bin/env python3
"""Reconstruct actual tester trades and explain winners vs losers.

Usage:
  python research/analyze_actual_trades.py --trades "<path to trade_events.csv>"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import core  # noqa: E402

POINT = core.POINT
OUT = HERE / "results"


def safe_float(v, default=np.nan):
    try:
        return float(v)
    except Exception:
        return default


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", required=True)
    parser.add_argument("--out", default=str(OUT / "actual_trades_analysis.csv"))
    args = parser.parse_args()

    d = pd.read_csv(args.trades, encoding="utf-8-sig")
    d["t"] = pd.to_datetime(d["server_time"])
    d = d.sort_values("t")

    rows = []
    for pid, g in d.groupby("position_id"):
        ins = g[g["entry"] == "IN"]
        outs = g[g["entry"] == "OUT"]
        if ins.empty or outs.empty:
            continue
        i0 = ins.iloc[0]
        rows.append(
            {
                "position_id": int(pid),
                "side": "LONG" if i0["deal_type"] == "BUY" else "SHORT",
                "entry_time": ins["t"].min(),
                "exit_time": outs["t"].max(),
                "entry": float(i0["price"]),
                "exit": float(outs.iloc[-1]["price"]),
                "volume": float(ins["volume"].sum()),
                "profit": float(outs["profit"].sum()),
                "n_out": int(len(outs)),
                "final_comment": str(outs.iloc[-1]["comment"]),
                "comments": "|".join(str(x) for x in outs["comment"].fillna("")),
            }
        )
    t = pd.DataFrame(rows).sort_values("entry_time").reset_index(drop=True)
    if t.empty:
        print("No closed positions reconstructed.")
        return 1

    # Feature frame. The exported CSV is server-clock time, same clock as the tester.
    features = core.load_data("M5")
    features = features.sort_values("time").reset_index(drop=True)
    t["entry_time"] = pd.to_datetime(t["entry_time"]).dt.tz_localize("UTC", ambiguous="NaT", nonexistent="NaT")
    t["exit_time"] = pd.to_datetime(t["exit_time"]).dt.tz_localize("UTC", ambiguous="NaT", nonexistent="NaT")

    # Pull the last completed feature row at or before entry.
    fcols = [
        "time", "open", "high", "low", "close", "atr", "atr_ratio", "atr_pct",
        "rsi2", "range_high", "range_low", "range_width", "range_pos",
        "body_atr", "ret1_atr", "mom6_atr", "ema20", "spread",
        "h1_close", "h1_sma20", "h1_sma50", "h1_sma200", "h1_adx",
        "h1_atr", "h1_plus_di", "h1_minus_di", "d1_close", "d1_sma200",
        "hour", "weekday",
    ]
    f = features[fcols].copy()
    t = pd.merge_asof(
        t.sort_values("entry_time"),
        f.sort_values("time").rename(columns={"time": "feature_time"}),
        left_on="entry_time",
        right_on="feature_time",
        direction="backward",
        tolerance=pd.Timedelta("2h"),
    )
    t = t.dropna(subset=["atr"]).reset_index(drop=True)
    t["features"] = t.apply(
        lambda r: {
            "h1_trend": 1 if (r.h1_close > r.h1_sma20 > r.h1_sma50 and r.h1_close > r.h1_sma200)
            else (-1 if (r.h1_close < r.h1_sma20 < r.h1_sma50 and r.h1_close < r.h1_sma200) else 0),
        },
        axis=1,
    )
    t["h1_trend"] = t["features"].apply(lambda x: x["h1_trend"])
    t = t.drop(columns=["features"])

    # Approximate the EA's initial risk from the exported ATR and volatility rule.
    t["vol_mult"] = np.where(t["atr_ratio"] > 1.3, 1.25, np.where(t["atr_ratio"] < 0.7, 0.85, 1.0))
    t["sl_points"] = (t["atr"] / POINT) * 1.5 * t["vol_mult"]
    t["sl_points"] = np.maximum(t["sl_points"], 25.0)
    t["risk_money"] = t["volume"] * t["sl_points"]
    t["r"] = t["profit"] / t["risk_money"]
    t["side_sign"] = np.where(t["side"] == "LONG", 1, -1)
    t["cost_points"] = 10.0
    t["cost_to_risk"] = t["cost_points"] / t["sl_points"]
    t["range_width_atr"] = t["range_width"] / t["atr"]
    t["mr_pos"] = (t["close"] - t["range_low"]) / t["range_width"]
    t["room_up_atr"] = (t["range_high"] - t["close"]) / t["atr"]
    t["room_down_atr"] = (t["close"] - t["range_low"]) / t["atr"]
    t["room_to_opposite_atr"] = np.where(t["side_sign"] > 0, t["room_up_atr"], t["room_down_atr"])
    t["h1_dist_sma200_atr"] = (t["h1_close"] - t["h1_sma200"]) / t["h1_atr"]
    t["h1_di_diff"] = t["h1_plus_di"] - t["h1_minus_di"]
    t["d1_dist_atr"] = (t["d1_close"] - t["d1_sma200"]) / t["h1_atr"]
    t["momentum_align"] = t["side_sign"] * t["ret1_atr"]
    t["rsi_extreme"] = np.where(t["side_sign"] > 0, 100.0 - t["rsi2"], t["rsi2"])
    t["entry_hour"] = t["entry_time"].dt.hour
    t["entry_weekday"] = t["entry_time"].dt.weekday
    t["session"] = np.where(
        t["entry_hour"] < 7, "ASIA",
        np.where(t["entry_hour"] < 12, "LONDON", np.where(t["entry_hour"] < 17, "NY", "LATE")),
    )
    t["bars_held"] = 0
    t["mfe_r"] = 0.0
    t["mae_r"] = 0.0

    idx = features.set_index("time").sort_index()
    for i, row in t.iterrows():
        try:
            window = idx.loc[row.entry_time : row.exit_time]
        except Exception:
            window = idx.iloc[0:0]
        if window.empty:
            continue
        entry = row.entry
        sl_dist = row.sl_points * POINT
        if row.side == "LONG":
            mfe = (window["high"].max() - entry) / sl_dist
            mae = (entry - window["low"].min()) / sl_dist
        else:
            mfe = (entry - window["low"].min()) / sl_dist
            mae = (window["high"].max() - entry) / sl_dist
        t.at[i, "mfe_r"] = float(mfe)
        t.at[i, "mae_r"] = float(mae)
        t.at[i, "bars_held"] = int(len(window))

    t["win"] = t["profit"] > 0
    t["tp_exit"] = t["comments"].str.contains("tp ", regex=False)
    t["be_exit"] = (t["n_out"] > 1) & (t["final_comment"].str.startswith("sl")) & ((t["exit"] - t["entry"]).abs() / POINT < 6)
    t["sl_exit"] = t["final_comment"].str.startswith("sl") & ~t["be_exit"]
    t["partial"] = t["n_out"] > 1
    t["reached_1r"] = t["mfe_r"] >= 1.0
    t["reached_15r"] = t["mfe_r"] >= 1.5
    t["reached_2r"] = t["mfe_r"] >= 2.0

    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(args.out, index=False)

    print("=" * 78)
    print(f"ACTUAL TRADES: {len(t)}   NET={t.profit.sum():+.2f}   WIN={t.win.mean()*100:.1f}%   AVG_R={t.r.mean():+.3f}")
    print("=" * 78)
    print("\n--- exit type ---")
    print(
        t.groupby(["tp_exit", "be_exit", "sl_exit"])
        .agg(n=("profit", "size"), net=("profit", "sum"), avg_r=("r", "mean"), avg_mfe=("mfe_r", "mean"), avg_mae=("mae_r", "mean"))
        .to_string()
    )
    print("\n--- partial vs single exit ---")
    print(t.groupby("partial").agg(n=("profit", "size"), net=("profit", "sum"), win=("win", "mean"), avg_r=("r", "mean"), avg_mfe=("mfe_r", "mean"), avg_mae=("mae_r", "mean")).to_string())
    print("\n--- side / session / hour ---")
    print(t.groupby("side").agg(n=("profit", "size"), net=("profit", "sum"), win=("win", "mean"), avg_r=("r", "mean"), mfe=("mfe_r", "mean"), mae=("mae_r", "mean")).to_string())
    print(t.groupby("session").agg(n=("profit", "size"), net=("profit", "sum"), win=("win", "mean"), avg_r=("r", "mean"), mfe=("mfe_r", "mean"), mae=("mae_r", "mean")).to_string())
    print(t.groupby("entry_hour").agg(n=("profit", "size"), net=("profit", "sum"), win=("win", "mean"), avg_r=("r", "mean")).to_string())

    print("\n--- winners vs losers (means) ---")
    cols = [
        "atr", "atr_ratio", "atr_pct", "range_width_atr", "mr_pos", "room_to_opposite_atr",
        "rsi2", "rsi_extreme", "body_atr", "ret1_atr", "mom6_atr", "momentum_align",
        "h1_adx", "h1_dist_sma200_atr", "h1_di_diff", "d1_dist_atr", "cost_to_risk",
        "mfe_r", "mae_r", "bars_held", "sl_points",
    ]
    rows = []
    for c in cols:
        w = t.loc[t.win, c].dropna()
        l = t.loc[~t.win, c].dropna()
        p = stats.mannwhitneyu(w, l, alternative="two-sided").pvalue if len(w) > 2 and len(l) > 2 else np.nan
        rows.append(
            {
                "feature": c,
                "winner_mean": w.mean(),
                "loser_mean": l.mean(),
                "winner_median": w.median(),
                "loser_median": l.median(),
                "p_value": p,
            }
        )
    cmp = pd.DataFrame(rows).sort_values("p_value")
    print(cmp.to_string(index=False, float_format=lambda x: f"{x:8.3f}"))

    print("\n--- MFE/MAE diagnostics ---")
    print(f"losers that reached +1.0R first: {(t.loc[~t.win,'reached_1r'].sum())}/{len(t.loc[~t.win])}")
    print(f"losers that reached +1.5R first: {(t.loc[~t.win,'reached_15r'].sum())}/{len(t.loc[~t.win])}")
    print(f"losers that reached +2.0R first: {(t.loc[~t.win,'reached_2r'].sum())}/{len(t.loc[~t.win])}")
    print(f"winners that first went -0.5R: {(t.loc[t.win,'mae_r'] >= 0.5).sum()}/{len(t.loc[t.win])}")
    print(f"winners that first went -1.0R: {(t.loc[t.win,'mae_r'] >= 1.0).sum()}/{len(t.loc[t.win])}")

    print("\n--- strongest feature/R correlations (Spearman) ---")
    corrs = []
    for c in cols:
        s = t[[c, "r"]].dropna()
        if len(s) > 5:
            corrs.append((c, s[c].corr(s["r"], method="spearman")))
    print(pd.DataFrame(corrs, columns=["feature", "spearman_r"]).sort_values("spearman_r").to_string(index=False))

    print("\n--- quantile buckets ---")
    for c in ["atr_pct", "atr", "range_width_atr", "cost_to_risk", "h1_adx", "h1_dist_sma200_atr", "momentum_align", "rsi_extreme", "room_to_opposite_atr", "mfe_r", "mae_r"]:
        s = t[c].dropna()
        if s.nunique() < 3:
            continue
        q = pd.qcut(t[c], 4, duplicates="drop")
        g = t.groupby(q, observed=True).agg(n=("r", "size"), win=("win", "mean"), avg_r=("r", "mean"), total_r=("r", "sum"))
        print(f"\n{c}:")
        print(g.to_string())

    print("\n--- top losers ---")
    print(t.sort_values("r").head(12)[["entry_time", "side", "atr", "atr_pct", "range_width_atr", "mr_pos", "ret1_atr", "h1_adx", "mfe_r", "mae_r", "r", "final_comment"]].to_string(index=False))
    print("\n--- top winners ---")
    print(t.sort_values("r", ascending=False).head(12)[["entry_time", "side", "atr", "atr_pct", "range_width_atr", "mr_pos", "ret1_atr", "h1_adx", "mfe_r", "mae_r", "r", "final_comment"]].to_string(index=False))
    print(f"\nSaved: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
