#!/usr/bin/env python3
"""Automated parameter search for the Sovereign research engine.

Usage:
  python research/sweep.py --stage random --samples 300 --timeframes M5,M15,M30

Results are written incrementally, so an interrupted run still leaves a usable
CSV. The score rewards configurations that are positive in the first three
months, the next three months, *and* the final four months, then penalises
drawdown and rewards monthly consistency.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import core  # noqa: E402
import engine  # noqa: E402

RESULTS_DIR = HERE / "results"

TRAIN = ("2026-01-01", "2026-03-31 23:59:59")
VAL = ("2026-04-01", "2026-06-30 23:59:59")
TEST = ("2026-07-01", "2026-10-31 23:59:59")


def _cfg_dict(cfg: engine.Config) -> dict:
    d = asdict(cfg)
    return d


def _split_metrics(trades: pd.DataFrame, result: engine.Result) -> dict:
    out = {}
    for name, (start, end) in (("train", TRAIN), ("val", VAL), ("test", TEST)):
        part = engine.slice_trades(trades, start, end)
        m = engine.metrics(result, part)
        for key, value in m.items():
            out[f"{name}_{key}"] = value
    full = engine.metrics(result, trades)
    for key, value in full.items():
        out[f"full_{key}"] = value
    return out


def _score(row: dict) -> float:
    train_net = row["train_net"]
    val_net = row["val_net"]
    test_net = row["test_net"]
    if min(train_net, val_net, test_net) <= 0:
        return -abs(min(train_net, val_net, test_net)) / 100.0
    if min(row["train_trades"], row["val_trades"], row["test_trades"]) < 5:
        return -1.0
    min_pf = min(row["train_pf"], row["val_pf"], row["test_pf"])
    consistency = row["full_positive_months"] / max(row["full_months"], 1)
    min_net = min(train_net, val_net, test_net)
    dd = max(row["full_max_dd"], 1.0)
    return min_pf * (0.25 + consistency) * (1.0 + min_net / dd)


def _random_config(rng: random.Random, timeframe: str) -> engine.Config:
    strategy = rng.choice(
        ["adaptive", "trend_pullback", "trend_breakout", "range_fade", "momentum", "mr_limit", "session_breakout"]
    )
    cfg = engine.Config(
        strategy=strategy,
        timeframe=timeframe,
        risk=0.5,
        spread=10.0,
        slippage=1.0,
        sl_atr=rng.choice([1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]),
        tp_r=rng.choice([0.0, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0]),
        be_r=rng.choice([0.0, 0.75, 1.0, 1.5]),
        partial_r=rng.choice([0.0, 0.75, 1.0, 1.5, 2.0]),
        partial_frac=rng.choice([0.4, 0.5, 0.6]),
        trail_atr=rng.choice([0.0, 0.0, 0.75, 1.0, 1.5, 2.0]),
        trail_start_r=rng.choice([0.5, 1.0, 1.5, 2.0]),
        time_exit_bars=rng.choice([0, 0, 12, 24, 48, 96]),
        adx_trend=rng.choice([18.0, 20.0, 22.0, 24.0, 26.0, 30.0]),
        adx_range=rng.choice([14.0, 16.0, 18.0, 20.0, 22.0]),
        extreme=rng.choice([0.05, 0.1, 0.15, 0.2, 0.25]),
        lookback=rng.choice([20, 30, 50, 80]),
        expiry_bars=rng.choice([3, 6, 12]),
        session_start=rng.choice([0, 6, 7, 8]),
        session_end=rng.choice([12, 16, 18, 20]),
        min_sl_points=rng.choice([15.0, 25.0, 40.0]),
        min_atr_points=rng.choice([10.0, 15.0, 20.0, 30.0]),
        max_spread=20.0,
        daily_dd_limit=rng.choice([0.0, 0.0, 2.0, 5.0]),
        max_trades_day=rng.choice([0, 0, 2, 3, 5]),
        cooldown_bars=rng.choice([1, 2, 3, 6]),
        require_stack=rng.choice([True, True, False]),
        require_di=rng.choice([True, True, False]),
        max_h1_dist_atr=rng.choice([0.0, 0.0, 3.0, 5.0]),
        entry_buffer_atr=rng.choice([0.0, 0.05, 0.1, 0.2]),
        momentum_atr=rng.choice([0.5, 0.8, 1.0, 1.5]),
        rsi_long=rng.choice([5.0, 10.0, 15.0, 20.0]),
        rsi_short=rng.choice([80.0, 85.0, 90.0, 95.0]),
        trend_engine=rng.choice(["pullback", "breakout", "both"]),
        mr_limit_require_trend=rng.choice([True, False]),
        session_breakout_expiry=rng.choice([24, 36, 60, 96]),
        session_breakout_tp_r=rng.choice([1.0, 1.5, 2.0, 3.0]),
        asian_start=0,
    )
    # A session-breakout model uses its own target multiple.
    if strategy == "session_breakout":
        cfg.tp_r = cfg.session_breakout_tp_r
    return cfg


def _seed_configs(timeframe: str) -> list[engine.Config]:
    seeds = []
    common = dict(
        timeframe=timeframe,
        risk=0.5,
        spread=10.0,
        slippage=1.0,
        sl_atr=2.0,
        be_r=0.0,
        partial_r=0.0,
        trail_atr=0.0,
        time_exit_bars=0,
        adx_trend=24.0,
        adx_range=18.0,
        extreme=0.15,
        lookback=50,
        session_start=7,
        session_end=16,
        min_sl_points=25.0,
        min_atr_points=15.0,
        cooldown_bars=1,
        require_stack=True,
        require_di=True,
    )
    for strategy in ["trend_breakout", "trend_pullback", "adaptive", "range_fade", "momentum"]:
        for tp in [1.5, 2.0, 3.0]:
            seeds.append(engine.Config(strategy=strategy, tp_r=tp, **common))
    # Exit-management variants on the breakout model.
    for trail in [1.0, 1.5, 2.0]:
        seeds.append(
            engine.Config(
                **{
                    **common,
                    "strategy": "trend_breakout",
                    "tp_r": 0.0,
                    "trail_atr": trail,
                    "trail_start_r": 1.0,
                }
            )
        )
    for be, partial in [(1.0, 0.0), (1.5, 1.5), (1.0, 1.5)]:
        seeds.append(
            engine.Config(
                **{
                    **common,
                    "strategy": "trend_breakout",
                    "tp_r": 4.0,
                    "be_r": be,
                    "partial_r": partial,
                }
            )
        )
    return seeds


def main() -> int:
    parser = argparse.ArgumentParser(description="Sovereign parameter sweep")
    parser.add_argument("--stage", default="random", choices=["random", "exit", "all"])
    parser.add_argument("--timeframes", default="M5,M15,M30")
    parser.add_argument("--samples", type=int, default=250)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default=None)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--max-runs", type=int, default=0)
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.out) if args.out else RESULTS_DIR / f"sweep_{stamp}.csv"
    timeframes = [x.strip() for x in args.timeframes.split(",") if x.strip()]

    rng = random.Random(args.seed)
    total_runs = 0
    rows: list[dict] = []

    for timeframe in timeframes:
        print(f"\n=== {timeframe} ===")
        df = core.load_data(timeframe)
        print(f"bars={len(df):,}  {df['time'].iloc[0]} -> {df['time'].iloc[-1]}")

        configs: list[engine.Config] = []
        if args.stage in ("random", "all"):
            configs.extend(_seed_configs(timeframe))
            configs.extend(_random_config(rng, timeframe) for _ in range(args.samples))
        if args.stage == "exit":
            # Exit stage is driven from the best existing sweep CSV.
            latest = sorted(RESULTS_DIR.glob("sweep_*.csv"))
            if not latest:
                print("No prior sweep found for --stage exit")
                return 1
            prior = pd.read_csv(latest[-1])
            prior = prior[prior["timeframe"] == timeframe].sort_values("score", ascending=False).head(20)
            for _, row in prior.iterrows():
                cfg = engine.Config(
                    strategy=row["strategy"],
                    timeframe=timeframe,
                    risk=0.5,
                    spread=10.0,
                    slippage=1.0,
                    sl_atr=float(row["sl_atr"]),
                    tp_r=float(row["tp_r"]),
                    adx_trend=float(row["adx_trend"]),
                    adx_range=float(row["adx_range"]),
                    extreme=float(row["extreme"]),
                    lookback=int(row["lookback"]),
                    session_start=int(row["session_start"]),
                    session_end=int(row["session_end"]),
                    min_sl_points=float(row["min_sl_points"]),
                    min_atr_points=float(row["min_atr_points"]),
                    cooldown_bars=int(row["cooldown_bars"]),
                    require_stack=bool(row["require_stack"]),
                    require_di=bool(row["require_di"]),
                    max_h1_dist_atr=float(row["max_h1_dist_atr"]),
                    entry_buffer_atr=float(row["entry_buffer_atr"]),
                    trend_engine=str(row["trend_engine"]),
                    mr_limit_require_trend=bool(row["mr_limit_require_trend"]),
                )
                for be in [0.0, 1.0, 1.5]:
                    for partial in [0.0, 1.0, 1.5]:
                        for trail in [0.0, 1.0, 1.5, 2.0]:
                            for time_exit in [0, 24, 48]:
                                x = engine.Config(**{**asdict(cfg), "be_r": be, "partial_r": partial,
                                                     "trail_atr": trail, "time_exit_bars": time_exit})
                                configs.append(x)

        seen = set()
        unique_configs = []
        for cfg in configs:
            key = json.dumps(_cfg_dict(cfg), sort_keys=True)
            if key not in seen:
                seen.add(key)
                unique_configs.append(cfg)

        if args.max_runs:
            unique_configs = unique_configs[: args.max_runs]
        print(f"configs={len(unique_configs)}")

        for idx, cfg in enumerate(unique_configs, 1):
            result = engine.run(df, cfg)
            trades = pd.DataFrame([t.__dict__ for t in result.trades])
            if trades.empty:
                continue
            m = _split_metrics(trades, result)
            row = {
                "timeframe": timeframe,
                **_cfg_dict(cfg),
                **m,
            }
            row["score"] = _score(row)
            rows.append(row)
            total_runs += 1

            if idx % 25 == 0 or idx == len(unique_configs):
                print(f"  {idx}/{len(unique_configs)} runs, best score={max(r['score'] for r in rows):.3f}")
                pd.DataFrame(rows).to_csv(out_path, index=False)

    if not rows:
        print("No trades produced by any configuration.")
        return 1
    table = pd.DataFrame(rows)
    table.to_csv(out_path, index=False)
    print(f"\nSaved {len(table)} rows to {out_path}")

    cols = [
        "score", "timeframe", "strategy", "sl_atr", "tp_r", "be_r", "partial_r",
        "trail_atr", "time_exit_bars", "adx_trend", "adx_range", "extreme",
        "session_start", "session_end", "trend_engine",
        "train_net", "val_net", "test_net", "full_net", "full_pf",
        "full_max_dd_pct", "full_positive_months", "full_months", "full_trades",
    ]
    shown = table.sort_values("score", ascending=False).head(args.top)
    print("\nTop candidates:")
    print(shown[cols].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
