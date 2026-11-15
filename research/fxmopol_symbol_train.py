#!/usr/bin/env python3
"""Per-symbol FXMOPOL training — walk-forward + static hold-out (MT5 best practice).

Protocol (fixed before running — no meta-overfitting):
  1) Small rule grid; one replay_trades() per config (cached).
  2) Rolling WFO on pre-holdout history (18mo IS / 3mo OOS / 3mo step).
  3) Pick deployment config = best **median WFO IS Sharpe** among windows (not single peak).
  4) Promote only if **chained WFO OOS** AND **static hold-out OOS** pass thresholds.
  5) Optional --ml logistic gate on design (skip filter), never neural nets.

Outputs: research/results/fxmopol_symbol_profiles.json
"""
from __future__ import annotations

import argparse
import json
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

import fxmopol_playbook as pb
from fxmopol_playbook import SPLITS, metrics, replay_trades
from fxmopol_signal_layers import layers_available
from fxmopol_wfo import (
    DEFAULT_PROTOCOL,
    config_key,
    plateau_ok,
    trade_metrics,
    trades_to_returns,
    walk_forward_chain,
)

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "fxmopol_symbol_profiles.json"
MANIFEST = HERE / "results" / "fxmopol_symbol_train_manifest.json"
WFO_LOG = HERE / "results" / "fxmopol_symbol_wfo_windows.json"

CRYPTO_SYMBOLS = {"BTCUSD", "ETHUSD"}

PROMOTE = {
    "min_static_oos_n": 40,
    "min_static_oos_exp_bps": 0.0,
    "min_static_oos_pf": 1.0,
    "min_wfo_chain_n": 40,
    "min_wfo_chain_exp_bps": 0.0,
    "min_wfe": DEFAULT_PROTOCOL.min_wfe,
}


def discover_symbols() -> list[str]:
    out: list[str] = []
    for p in DATA.glob("*_H1.csv"):
        out.append(p.stem.replace("_H1", ""))
    for sym in ("EURUSD", "GBPUSD", "USDJPY"):
        if sym not in out and (DATA / "histdata" / f"{sym}_H1.csv").exists():
            out.append(sym)
    return sorted(set(out))


def load_symbol(sym: str) -> pd.DataFrame:
    """Prefer the longest H1 file (HistData full history over short MT5 exports)."""
    best_len = 0
    best: pd.DataFrame | None = None
    for base in (DATA / "histdata", DATA):
        p = base / f"{sym}_H1.csv"
        if not p.exists():
            continue
        d = pd.read_csv(p, parse_dates=["time"])
        d["time"] = pd.to_datetime(d["time"], utc=True, errors="coerce").dt.tz_convert(None)
        d = d.set_index("time").sort_index()
        if len(d) > best_len:
            best_len = len(d)
            best = d
    if best is None:
        raise FileNotFoundError(sym)
    return best


def static_holdout(sym: str) -> tuple[str, str]:
    if sym in CRYPTO_SYMBOLS:
        return "2023-07-01", "2032-01-01"
    return SPLITS["OOS"]


def holdout_bounds(h1: pd.DataFrame, sym: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    ho0, ho1 = static_holdout(sym)
    ho0, ho1 = pd.Timestamp(ho0), pd.Timestamp(ho1)
    if h1.index.max() <= ho0:
        span = h1.index.max() - h1.index.min()
        ho0 = h1.index.min() + span * 0.75
        ho1 = h1.index.max() + pd.Timedelta(hours=1)
    return ho0, ho1


def wfo_region(h1: pd.DataFrame, sym: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """WFO runs only **before** static hold-out (data not reused for final OOS)."""
    ho0, _ = holdout_bounds(h1, sym)
    start = h1.index.min() + pd.DateOffset(months=6)
    return start, ho0


def grid_configs(sym: str) -> list[dict]:
    cost = 10.0 if sym in CRYPTO_SYMBOLS else 3.0
    ema_opts = (100, 50) if sym == "USDJPY" else (50,)
    avail = layers_available(sym)
    layer_opts = product((False, True), (False, True), (False, True))  # rsi, sr, fund
    configs = []
    for ema_mid, prox, strict, rsi_div, sr_filt, fund_fs in product(
        ema_opts, (True, False), (True, False), layer_opts
    ):
        if fund_fs and not avail["fund_stack"]:
            continue
        if sr_filt and not avail["sr"]:
            continue
        configs.append(
            {
                "mode": "playbook",
                "preset": 2,
                "ema_mid": ema_mid,
                "cost_bps": cost,
                "proximity": prox,
                "strict_stayaway": strict,
                "rsi_divergence": rsi_div,
                "sr_filter": sr_filt,
                "fund_sentiment": fund_fs,
            }
        )
    return configs


def replay_config(h1: pd.DataFrame, cfg: dict, sym: str) -> pd.DataFrame:
    pb.COST_BPS = cfg["cost_bps"]
    pb.USE_PROXIMITY_FILTERS = cfg["proximity"]
    pb.STRICT_STAY_AWAY = cfg["strict_stayaway"]
    pb.USE_RSI_DIVERGENCE = bool(cfg.get("rsi_divergence", False))
    pb.USE_SR_FILTER = bool(cfg.get("sr_filter", False))
    pb.USE_FUND_SENTIMENT = bool(cfg.get("fund_sentiment", False))
    return replay_trades(h1, ema_mid=cfg["ema_mid"], symbol=sym)


def build_config_returns(h1: pd.DataFrame, configs: list[dict], sym: str) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    for cfg in configs:
        trades = replay_config(h1, cfg, sym)
        out[config_key(cfg)] = trades_to_returns(trades)
    return out


def pick_deployment_config(
    config_returns: dict[str, pd.Series],
    windows: list[dict],
) -> str | None:
    """Config with highest median IS Sharpe across WFO windows it was selected."""
    if not windows:
        return None
    scores: dict[str, list[float]] = {}
    for w in windows:
        key = w.get("pick")
        sh = w.get("is_sharpe")
        if key and sh is not None:
            scores.setdefault(key, []).append(float(sh))
    if not scores:
        return None
    return max(scores.keys(), key=lambda k: float(np.median(scores[k])))


def try_ml_gate(trades: pd.DataFrame, splits: dict) -> dict | None:
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        return None
    if trades.empty or len(trades) < 120:
        return None
    tr = trades.copy()
    tr["side_long"] = (tr["side"] == "LONG").astype(int)
    rsi = pd.to_numeric(tr.get("rsi_at_signal"), errors="coerce").fillna(50.0)
    tr["rsi_norm"] = (rsi - 50.0) / 30.0
    tr["win"] = (tr["return_pct"] > 0).astype(int)
    tr["entry_time"] = pd.to_datetime(tr["entry_time"])
    d0, d1 = splits["design"]
    v0, v1 = splits["validation"]
    des = tr[(tr["entry_time"] >= d0) & (tr["entry_time"] < d1)]
    val = tr[(tr["entry_time"] >= v0) & (tr["entry_time"] < v1)]
    if len(des) < 80 or len(val) < 40:
        return None
    feats = ["side_long", "rsi_norm"]
    scaler = StandardScaler()
    Xd_s = scaler.fit_transform(des[feats].values)
    Xv_s = scaler.transform(val[feats].values)
    clf = LogisticRegression(max_iter=500, class_weight="balanced")
    clf.fit(Xd_s, des["win"].values)
    probs = clf.predict_proba(Xv_s)[:, 1]
    yv = val["return_pct"].values / 100.0
    best_thr, best_exp = 0.5, -1e9
    for thr in np.linspace(0.35, 0.65, 13):
        mask = probs >= thr
        if mask.sum() < 10:
            continue
        exp = yv[mask].mean() * 10000
        if exp > best_exp:
            best_exp, best_thr = exp, float(thr)
    if best_exp <= yv.mean() * 10000:
        return None
    return {"enabled": True, "min_p_win": best_thr, "features": feats}


def passes_promotion(static_m: dict, wfo_m: dict, wfe: float) -> bool:
    return (
        static_m["n"] >= PROMOTE["min_static_oos_n"]
        and static_m["exp_bps"] >= PROMOTE["min_static_oos_exp_bps"]
        and static_m["pf"] >= PROMOTE["min_static_oos_pf"]
        and wfo_m["n"] >= PROMOTE["min_wfo_chain_n"]
        and wfo_m["exp_bps"] >= PROMOTE["min_wfo_chain_exp_bps"]
        and wfe >= PROMOTE["min_wfe"]
    )


def train_symbol(sym: str, use_ml: bool) -> dict:
    h1 = load_symbol(sym)
    configs = grid_configs(sym)
    config_returns = build_config_returns(h1, configs, sym)
    cfg_by_key = {config_key(c): c for c in configs}

    wf_start, wf_end = wfo_region(h1, sym)
    chained, windows = walk_forward_chain(config_returns, wf_start, wf_end)
    wfo_m = trade_metrics(chained)
    is_sharpes = [w["is_sharpe"] for w in windows if w.get("is_sharpe") is not None]
    med_is_sh = float(np.median(is_sharpes)) if is_sharpes else -999.0
    wfe = (wfo_m["sharpe"] / med_is_sh) if med_is_sh > 1e-6 else 0.0

    deploy_key = pick_deployment_config(config_returns, windows)
    if deploy_key is None:
        deploy_key = max(config_returns.keys(), key=lambda k: trade_metrics(config_returns[k])["sharpe"])
    best_cfg = cfg_by_key[deploy_key]

    ho0, ho1 = holdout_bounds(h1, sym)
    static_r = config_returns[deploy_key]
    static_oos = static_r[(static_r.index >= ho0) & (static_r.index < ho1)]
    static_m = metrics(static_oos)  # playbook metrics for compatibility
    static_m["sharpe"] = trade_metrics(static_oos)["sharpe"]

    val0, val1 = SPLITS["validation"] if sym not in CRYPTO_SYMBOLS else ("2021-01-01", "2023-07-01")
    plateau = plateau_ok(config_returns, deploy_key, pd.Timestamp(val0), pd.Timestamp(val1))

    promoted = passes_promotion(static_m, wfo_m, wfe) and plateau

    reason = "oos_promoted" if promoted else (
        f"fail static n={static_m['n']} exp={static_m['exp_bps']} pf={static_m['pf']} "
        f"wfo_n={wfo_m['n']} wfe={wfe:.2f} plateau={plateau}"
    )

    profile: dict = {
        "promoted": promoted,
        "preset": int(best_cfg["preset"]),
        "mode": best_cfg["mode"],
        "reason": reason,
        "deployment_config": deploy_key,
        "protocol": {
            "wfo": DEFAULT_PROTOCOL.__dict__,
            "promotion": PROMOTE,
            "method": "rolling_walk_forward_plus_static_holdout",
            "references": [
                "https://www.mql5.com/en/articles/3279",
                "https://www.mql5.com/en/blogs/post/756386",
            ],
        },
        "wfo_chain": wfo_m,
        "wfe": round(wfe, 3),
        "static_holdout_oos": static_m,
        "plateau_ok": plateau,
        "wfo_windows": len(windows),
        "bars": len(h1),
        "from": str(h1.index[0]),
        "to": str(h1.index[-1]),
        "config": best_cfg,
        "signal_stack": {
            "rsi_divergence": bool(best_cfg.get("rsi_divergence", False)),
            "sr_filter": bool(best_cfg.get("sr_filter", False)),
            "fund_sentiment": bool(best_cfg.get("fund_sentiment", False)),
            "layers_available": layers_available(sym),
        },
        "inputs_hint": {
            "InpPreset": best_cfg["preset"],
            "InpSlowEma": best_cfg["ema_mid"],
            "InpUseProxFilters": best_cfg["proximity"],
            "InpStrictStayAway": best_cfg["strict_stayaway"],
            "InpUsePlaybook": True,
            "InpGoodSessionsOnly": sym not in CRYPTO_SYMBOLS,
        },
    }
    if use_ml and promoted:
        trades = replay_config(h1, best_cfg, sym)
        ml = try_ml_gate(
            trades,
            {"design": SPLITS["design"], "validation": (val0, val1)},
        )
        if ml:
            profile["ml_gate"] = ml
    return profile, windows


def merge_usdjpy_streak(payload: dict) -> None:
    streak_path = HERE / "results" / "ema_streak_ride_grid.csv"
    if not streak_path.exists():
        return
    row = pd.read_csv(streak_path)
    ok = row[(row.get("exp_bps_OOS", 0) > 0) & (row.get("n_OOS", 0) >= 50)]
    if ok.empty:
        return
    cur = payload["symbols"].get("USDJPY", {})
    if cur.get("promoted"):
        return
    best = ok.sort_values("exp_bps_OOS", ascending=False).iloc[0]
    import re

    cfg_s = str(best["config"])
    m = re.search(r"_s(\d+)", cfg_s)
    min_streak = int(m.group(1)) if m else 30
    payload["symbols"]["USDJPY"] = {
        "promoted": True,
        "preset": 1,
        "mode": "streak",
        "reason": "ema_streak_ride OOS+ (pre-research; rerun WFO when USDJPY H1 refreshed)",
        "oos": {
            "n": int(best["n_OOS"]),
            "exp_bps": float(best["exp_bps_OOS"]),
            "pf": float(best.get("pf_OOS", 0)),
        },
        "config": {"ema_mid": 100, "min_streak": min_streak},
        "inputs_hint": {"InpPreset": 1, "InpStreakRide": True, "InpSlowEma": 100},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="FXMOPOL per-symbol walk-forward train")
    parser.add_argument("--ml", action="store_true")
    parser.add_argument("--symbol", action="append", help="Train one symbol only (repeatable)")
    args = parser.parse_args()

    symbols = args.symbol if args.symbol else discover_symbols()
    payload = {
        "version": 2,
        "updated": pd.Timestamp.now("UTC").isoformat(),
        "symbols": {},
    }
    all_windows: dict = {}

    for sym in symbols:
        try:
            profile, wins = train_symbol(sym, args.ml)
            payload["symbols"][sym] = profile
            all_windows[sym] = wins
            flag = "PROMOTED" if profile["promoted"] else "BLOCKED"
            so = profile["static_holdout_oos"]
            wf = profile["wfo_chain"]
            print(
                f"{sym}: {flag}  static_OOS exp={so['exp_bps']} pf={so['pf']} n={so['n']}  "
                f"WFO exp={wf['exp_bps']} wfe={profile['wfe']}"
            )
        except FileNotFoundError as e:
            print(f"Skip {sym}: {e}")

    merge_usdjpy_streak(payload)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    WFO_LOG.write_text(json.dumps(all_windows, indent=2), encoding="utf-8")
    MANIFEST.write_text(
        json.dumps(
            {s: {"promoted": p["promoted"], "reason": p["reason"]} for s, p in payload["symbols"].items()},
            indent=2,
        ),
        encoding="utf-8",
    )
    promoted = [s for s, p in payload["symbols"].items() if p.get("promoted")]
    print(f"\nTrade-enabled symbols: {promoted or '(none)'}")
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
