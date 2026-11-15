#!/usr/bin/env python3
"""Load a repo-root .env into os.environ, with no third-party dependency.

Why not python-dotenv: this repo must never invoke pip launchers (ASR policy,
see AGENTS.md), and a 40-line stdlib parser removes the install question entirely.

Precedence is deliberate: a value already present in the real environment WINS
over the file. That means CI, a shell session, or a one-off
``$env:OPENAI_API_KEY="..."`` still overrides the file, which is what you want
when you are testing a different key.

Usage
    from sovereign_env import load_env
    load_env()            # idempotent, safe to call from any entry point

    # or, from a script that is not on the repo path:
    sys.path.insert(0, str(ROOT / "research")); from sovereign_env import load_env

Recognised keys (all optional; with none set the analyst runs deterministically)
    OPENAI_API_KEY
    ANTHROPIC_API_KEY
    SOVEREIGN_LLM_MODEL          default gpt-4o-mini
    SOVEREIGN_ANTHROPIC_MODEL    default claude-sonnet-4-20250514
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = ROOT / ".env"

_LOADED = False


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines. Tolerates comments, `export `, quotes, CRLF, BOM."""
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")

    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        #--- strip one layer of matching quotes
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        #--- drop an inline comment only when it is preceded by whitespace
        if " #" in val:
            val = val.split(" #", 1)[0].rstrip()
        if key:
            out[key] = val
    return out


def load_env(path: Path | str | None = None, override: bool = False) -> dict[str, str]:
    """Load the .env file into os.environ. Returns the keys it applied."""
    global _LOADED
    p = Path(path) if path is not None else DEFAULT_ENV
    values = parse_env_file(p)
    applied: dict[str, str] = {}
    for key, val in values.items():
        if override or key not in os.environ:
            os.environ[key] = val
            applied[key] = val
    _LOADED = True
    return applied


def llm_status() -> str:
    """One line describing which provider, if any, is configured."""
    if os.environ.get("OPENAI_API_KEY"):
        return f"openai ({os.environ.get('SOVEREIGN_LLM_MODEL', 'gpt-4o-mini')})"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ("anthropic ("
                f"{os.environ.get('SOVEREIGN_ANTHROPIC_MODEL', 'claude-sonnet-4-20250514')})")
    return "none - deterministic analyst"


if __name__ == "__main__":
    applied = load_env()
    print(f".env: {DEFAULT_ENV} ({'found' if DEFAULT_ENV.exists() else 'missing'})")
    if applied:
        for k in applied:
            v = os.environ.get(k, "")
            shown = (v[:6] + "...") if k.endswith("_KEY") and len(v) > 6 else v
            print(f"  {k} = {shown}")
    else:
        print("  no new keys applied (file missing, or real env already set them)")
    print(f"llm: {llm_status()}")
