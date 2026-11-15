#!/usr/bin/env python3
"""Turn FXMOPOL market pulse into a short GPT/Claude brief (advisor only — no auto trades).

Requires: OPENAI_API_KEY and/or ANTHROPIC_API_KEY in the environment.
Uses existing deterministic pulse; LLM only narrates Q1/Q2 and highlights issues.

  .\\.venv\\Scripts\\python.exe research\\fxmopol_llm_brief.py --symbol GBPUSD
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request

from fxmopol_market_pulse import analyze_market_pulse
from fxmopol_playbook import load_h1
from fxmopol_symbol_train import load_symbol
from sovereign_env import load_env

load_env()  # repo-root .env, no third-party dependency

SYSTEM = (
    "You are FXMOPOL trading coach. The user gets a JSON market pulse from rules, not live ticks. "
    "Answer in under 12 lines: (1) what the market is doing now, (2) whether to enter/exit/wait, "
    "(3) the main risks from the issues list. Never invent prices or guarantee profit. "
    "If action is WAIT_FLAT or issues mention chop, strongly prefer no trade."
)


def _openai(prompt: str) -> str:
    key = os.environ["OPENAI_API_KEY"]
    model = os.environ.get("SOVEREIGN_LLM_MODEL", "gpt-4o-mini")
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
        }
    ).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        payload = json.loads(resp.read().decode())
    return payload["choices"][0]["message"]["content"].strip()


def _anthropic(prompt: str) -> str:
    key = os.environ["ANTHROPIC_API_KEY"]
    model = os.environ.get("SOVEREIGN_ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    body = json.dumps(
        {
            "model": model,
            "max_tokens": 800,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        payload = json.loads(resp.read().decode())
    parts = payload.get("content") or []
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="GBPUSD")
    p.add_argument("--provider", choices=("auto", "openai", "anthropic"), default="auto")
    args = p.parse_args()
    sym = args.symbol.upper()
    try:
        h1 = load_symbol(sym)
    except FileNotFoundError:
        h1 = load_h1(sym)
    pulse = analyze_market_pulse(h1, symbol=sym)
    prompt = f"Symbol {sym}. Pulse JSON:\n{json.dumps(pulse, indent=2)}"

    provider = args.provider
    if provider == "auto":
        if os.environ.get("OPENAI_API_KEY"):
            provider = "openai"
        elif os.environ.get("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        else:
            print("Set OPENAI_API_KEY or ANTHROPIC_API_KEY (no LLM call made).")
            print("Deterministic pulse:\n", json.dumps(pulse, indent=2))
            return 1

    try:
        text = _openai(prompt) if provider == "openai" else _anthropic(prompt)
    except Exception as exc:
        print(f"LLM failed: {exc}")
        print(json.dumps(pulse, indent=2))
        return 1

    print(f"=== LLM brief ({provider}) {sym} ===\n{text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
