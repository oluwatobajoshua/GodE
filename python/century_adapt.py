#!/usr/bin/env python3
"""Automated long-horizon allocator for Project Sovereign.

Reads closed-trade history and optional research summaries, writes
``century_directives.json`` into the MT5 Files tree. The EA applies only
clamped fields; deterministic rules always run (LLM is optional overlay).

Run once:
    .\\.venv\\Scripts\\python.exe python\\century_adapt.py

Daemon (hourly):
    .\\.venv\\Scripts\\python.exe python\\century_daemon.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

#--- repo-root .env (stdlib only, real env wins)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
from sovereign_env import load_env  # noqa: E402

load_env()

REPO = Path(__file__).resolve().parents[1]
DEFAULT_FILES = Path(
    os.environ.get(
        "SOVEREIGN_MT5_FILES",
        REPO / "backtest" / "mt5_files" / "Sovereign",
    )
)
OUT_PATH = Path(os.environ.get("SOVEREIGN_DIRECTIVES", DEFAULT_FILES / "century_directives.json"))
HISTORY_PATH = Path(
    os.environ.get("SOVEREIGN_OUTCOMES", DEFAULT_FILES / "trade_outcomes.csv")
)
BUCKETS_PATH = Path(
    os.environ.get("SOVEREIGN_BUCKETS", DEFAULT_FILES / "performance_buckets.csv")
)
CARRY_SUMMARY = REPO / "research" / "results" / "carry_summary.csv"

SCHEMA_VERSION = 1
MIN_LEG_MULT = 0.0
MAX_LEG_MULT = 1.25
MIN_GLOBAL = 0.0
MAX_GLOBAL = 1.25


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def load_outcomes(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return df
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], errors="coerce")
        df = df.dropna(subset=["time"]).sort_values("time")
    for col in ("profit", "r_multiple", "risk_money"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_buckets(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def deterministic_directives(
    outcomes: pd.DataFrame,
    buckets: pd.DataFrame,
    carry_summary: pd.DataFrame | None,
) -> dict:
    now = _utcnow()
    expires = now + timedelta(hours=int(os.environ.get("SOVEREIGN_DIRECTIVE_HOURS", "24")))

    leg_mult: dict[str, float] = {}
    skip: list[str] = []
    global_scalar = 1.0
    halt = False
    reasons: list[str] = []

    if not outcomes.empty and "r_multiple" in outcomes.columns:
        recent = outcomes.tail(60)
        avg_r = float(recent["r_multiple"].mean())
        if avg_r <= -0.12:
            global_scalar = 0.5
            reasons.append(f"recent60 avg R {avg_r:.3f} -> global 0.5")
        elif avg_r <= -0.05:
            global_scalar = 0.75
            reasons.append(f"recent60 avg R {avg_r:.3f} -> global 0.75")
        elif avg_r >= 0.10:
            global_scalar = min(MAX_GLOBAL, 1.08)
            reasons.append(f"recent60 avg R {avg_r:.3f} -> global boost")

        if "bucket" in outcomes.columns:
            by_sym = recent.groupby("bucket")["r_multiple"].agg(["mean", "count"])
            for bucket, row in by_sym.iterrows():
                if row["count"] < 5:
                    continue
                sym = bucket.split("|")[-1] if "|" in str(bucket) else str(bucket)
                if row["mean"] <= -0.15:
                    skip.append(sym)
                    reasons.append(f"skip {sym}: avg R {row['mean']:.3f}")
                elif row["mean"] <= -0.05:
                    leg_mult[sym] = 0.6
                elif row["mean"] >= 0.12:
                    leg_mult[sym] = min(MAX_LEG_MULT, 1.1)

        eq = (1 + recent["r_multiple"].fillna(0) * 0.01).cumprod()
        dd = float((eq / eq.cummax() - 1).min()) if len(eq) else 0.0
        if dd <= -0.15:
            global_scalar = min(global_scalar, 0.35)
            reasons.append(f"rolling pseudo-DD {dd:.2%}")
        if dd <= -0.25:
            halt = True
            reasons.append("halt: pseudo-DD beyond -25%")

    if not buckets.empty and "sum_r_eff" in buckets.columns:
        for _, row in buckets.iterrows():
            key = str(row.get("key", ""))
            n = float(row.get("n_eff", 0) or 0)
            score = float(row.get("sum_r_eff", 0) or 0) / max(n, 1.0)
            if "|" not in key:
                continue
            sym = key.split("|")[-1]
            if n >= 10 and score < -0.08:
                leg_mult[sym] = min(leg_mult.get(sym, 1.0), 0.5)
                reasons.append(f"bucket {key} score {score:.3f}")

    top_n = 3
    if carry_summary is not None and not carry_summary.empty:
        mom = carry_summary[carry_summary["name"] == "carry_mom_filter"]
        if not mom.empty:
            oos = mom[mom["split"] == "OOS_2020_2026"]
            if not oos.empty and float(oos.iloc[0].get("sharpe", 0)) < 0:
                global_scalar = min(global_scalar, 0.7)
                reasons.append("research OOS sharpe negative -> cap risk")

    global_scalar = max(MIN_GLOBAL, min(MAX_GLOBAL, global_scalar))
    leg_mult = {k: max(MIN_LEG_MULT, min(MAX_LEG_MULT, v)) for k, v in leg_mult.items()}
    skip = sorted(set(skip))

    return {
        "version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "halt_trading": halt,
        "global_risk_scalar": round(global_scalar, 4),
        "top_n": top_n,
        "strategy": "carry_mom",
        "sleeve_weights": {"carry": 0.75, "novel_overlay": 0.25},
        "leg_multipliers": leg_mult,
        "skip_symbols": skip,
        "reason": "; ".join(reasons) if reasons else "deterministic default",
        "source": "deterministic",
    }


def try_llm_overlay(base: dict, outcomes: pd.DataFrame) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return base

    try:
        import urllib.request

        summary = {
            "n_trades": int(len(outcomes)),
            "avg_r_last_30": float(outcomes["r_multiple"].tail(30).mean())
            if not outcomes.empty and "r_multiple" in outcomes.columns
            else 0.0,
            "current_directive": base,
        }
        prompt = (
            "You are a risk allocator. Given trade stats JSON, return ONLY JSON with keys: "
            "global_risk_scalar (0-1.25), skip_symbols (list), leg_multipliers (dict), "
            "halt_trading (bool), note (string). Never increase risk above 1.25. "
            f"Stats: {json.dumps(summary)}"
        )
        if os.environ.get("OPENAI_API_KEY"):
            body = json.dumps(
                {
                    "model": os.environ.get("SOVEREIGN_LLM_MODEL", "gpt-4o-mini"),
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                }
            ).encode()
            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode())
            text = payload["choices"][0]["message"]["content"]
            llm = json.loads(text)
        else:
            return base

        merged = dict(base)
        merged["source"] = "deterministic+llm"
        g = float(llm.get("global_risk_scalar", merged["global_risk_scalar"]))
        merged["global_risk_scalar"] = min(
            merged["global_risk_scalar"], max(MIN_GLOBAL, min(MAX_GLOBAL, g))
        )
        if llm.get("halt_trading"):
            merged["halt_trading"] = True
        for sym, mult in (llm.get("leg_multipliers") or {}).items():
            m = float(mult)
            merged["leg_multipliers"][sym] = min(
                merged["leg_multipliers"].get(sym, 1.0),
                max(MIN_LEG_MULT, min(MAX_LEG_MULT, m)),
            )
        merged["skip_symbols"] = sorted(
            set(merged["skip_symbols"]) | set(llm.get("skip_symbols") or [])
        )
        merged["reason"] = f"{merged['reason']} | llm: {llm.get('note', '')}"
        return merged
    except Exception as exc:
        base["reason"] = f"{base['reason']} | llm_skipped: {exc}"
        return base


def write_directives(doc: dict) -> Path:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return OUT_PATH


def run_once() -> int:
    outcomes = load_outcomes(HISTORY_PATH)
    buckets = load_buckets(BUCKETS_PATH)
    carry = pd.read_csv(CARRY_SUMMARY) if CARRY_SUMMARY.exists() else None
    doc = deterministic_directives(outcomes, buckets, carry)
    if os.environ.get("SOVEREIGN_LLM_ENABLE", "1") == "1":
        doc = try_llm_overlay(doc, outcomes)
    path = write_directives(doc)
    print(json.dumps({"written": str(path), **{k: doc[k] for k in ("global_risk_scalar", "halt_trading", "source", "reason")}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_once())
