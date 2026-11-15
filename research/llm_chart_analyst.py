#!/usr/bin/env python3
"""Put an LLM analyst on the chart - numbers first, vision optional, drawings back out.

You can hand a screenshot to Gemini and get a human-looking read. This does the same
thing properly, on a loop, with the LLM's output turned into something the chart can
actually draw.

HOW IT ANALYSES (and why this order matters)

  1. NUMBERS FIRST. The model gets an exact state bundle: price, EMA21/50 and slope
     in ATR, RSI9 and its state, ATR ratio, the confirmed look-left swing levels,
     the spread and whether it is hostile, thin-liquidity flags, and what the
     validated RSI band rule is currently doing. This is "an analyst with a
     terminal", not "a human squinting at pixels".
  2. VISION OPTIONAL (--image). Attach the rendered PNG so a multimodal model can
     also look. Useful for context; NOT trusted for levels.
  3. STRUCTURED OUTPUT. It must return strict JSON: a read, a confidence, a
     narrative, risks, and DRAW instructions (levels, zones, arrows) whose prices
     are checked against the supplied levels - anything further than 5 ATR away is
     rejected, so it cannot invent a level.

WHY IT IS ADVISOR ONLY

Every visual pattern this project measured was a persistence detector with zero
forward drift (curvature, candle colours, the S/R grade, ML direction). An LLM
looking at a chart will narrate edges that the data says are not there, and it will
sound just as confident when there is nothing to see. So it may VETO a setup and
explain it. It may never size or trigger a trade. The EA draws its output and
nothing else.

NO KEY? It still runs. With no OPENAI_API_KEY / ANTHROPIC_API_KEY it produces the
same JSON schema from deterministic rules, so the pipeline and the drawing are
testable without spending anything.

Usage
    .\\.venv\\Scripts\\python.exe research/llm_chart_analyst.py --symbol EURUSD --tf H1
    .\\.venv\\Scripts\\python.exe research/llm_chart_analyst.py --symbol GBPUSD --image
    .\\.venv\\Scripts\\python.exe research/llm_chart_analyst.py --symbol EURUSD --watch 60
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from sovereign_env import load_env  # noqa: E402

load_env()  # repo-root .env, no third-party dependency

from ema_cross_harvest import ATR_P, EMA, EMA_MID, RSI_P, ema, wilder_atr, wilder_rsi  # noqa: E402
from rsi_band_net import simulate  # noqa: E402
from structure_quality import swings  # noqa: E402

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"
CHARTS = RESULTS / "charts"

SYSTEM = """You are the risk reviewer on a systematic FX desk. You are NOT a signal
provider and you do not predict direction - the desk has already measured that visual
patterns carry no forward drift. Your job is to read the supplied state, say whether
the current setup is trustworthy, and name the risks.

Rules you must follow:
- Use ONLY the prices and levels supplied in the state. Never invent a level.
- If structure conflicts with momentum, or the spread is hostile, or the ATR is in a
  spike, prefer "wait" and say why.
- Be concrete and brief. No guarantees, no price targets you were not given.

Return STRICT JSON only, no markdown fence, with exactly these keys:
{
  "read": "long" | "short" | "wait",
  "confidence": 0.0-1.0,
  "narrative": "3-6 sentences a trader would actually say, referencing the numbers",
  "risks": ["short risk strings"],
  "veto": true | false,
  "veto_reason": "string or empty",
  "draw": {
    "levels": [{"price": <number from the supplied levels>, "kind": "support"|"resistance", "label": "short"}],
    "zones":  [{"price_low": <number>, "price_high": <number>, "label": "short"}],
    "arrows": [{"bar_offset": <negative int, bars back from last bar>, "direction": "up"|"down", "label": "short"}]
  }
}"""


def load(sym: str, tf: str) -> pd.DataFrame:
    p = DATA / f"{sym}_{tf}.csv"
    if not p.exists():
        raise SystemExit(f"missing data: {p}")
    df = pd.read_csv(p)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    return df.sort_values("time").reset_index(drop=True)


def build_state(df: pd.DataFrame, sym: str, tf: str, k: int = 2,
                margin: float = 3.0, min_hold: int = 12) -> dict:
    d = df.copy()
    d["ema"] = ema(d["close"], EMA)
    d["ema_mid"] = ema(d["close"], EMA_MID)
    d["rsi"] = wilder_rsi(d["close"], RSI_P)
    d["atr"] = wilder_atr(d, ATR_P)
    d = d.dropna(subset=["ema", "ema_mid", "rsi", "atr"]).reset_index(drop=True)

    c = d["close"].to_numpy(float)
    h = d["high"].to_numpy(float)
    l = d["low"].to_numpy(float)
    e1 = d["ema"].to_numpy(float)
    e2 = d["ema_mid"].to_numpy(float)
    rsi = d["rsi"].to_numpy(float)
    atr = d["atr"].to_numpy(float)
    spread = d["spread"].astype(float).to_numpy()
    time = d["time"]
    n = len(d)
    point = 0.001 if sym.endswith("JPY") else 0.00001

    slope3 = np.zeros(n)
    slope3[3:] = (e1[3:] - e1[:-3]) / np.maximum(atr[3:], 1e-12)
    trend = np.where(slope3 > 0.10, 1, np.where(slope3 < -0.10, -1, 0))
    cost_side = (np.maximum(spread, 1.0) / 2.0 + 1.0 + 3.5) * point
    q = pd.Series(spread).rolling(500, min_periods=100).quantile(0.90).to_numpy()
    spread_ok = (spread <= q) | ~np.isfinite(q)
    pos = simulate(rsi, np.full(n, 50.0), np.full(n, 75.0), np.full(n, 25.0),
                   trend, c, spread_ok, cost_side, n,
                   dict(cooldown=0, min_hold=min_hold, margin=margin))

    up_s, dn_s = swings(h, l, k)
    levels = []
    for i in range(max(0, n - 500), n):
        #--- only levels confirmed at or before the last closed bar
        if up_s[i] and i + k <= n - 1:
            levels.append(dict(price=round(float(h[i]), 5), kind="resistance",
                               age_bars=int(n - 1 - i)))
        if dn_s[i] and i + k <= n - 1:
            levels.append(dict(price=round(float(l[i]), 5), kind="support",
                               age_bars=int(n - 1 - i)))
    #--- keep the levels nearest to price, deduped
    last = float(c[-1])
    levels = [lv for lv in levels if abs(lv["price"] - last) <= 4.0 * atr[-1]]
    levels.sort(key=lambda lv: abs(lv["price"] - last))
    seen, keep = set(), []
    for lv in levels:
        key = (lv["kind"], round(lv["price"], 4))
        if key in seen:
            continue
        seen.add(key)
        keep.append(lv)
    levels = keep[:10]

    atr_ratio = float(atr[-1] / pd.Series(atr).rolling(100, min_periods=20).mean().to_numpy()[-1])
    hours = int(time.iloc[-1].hour)
    hi_idx = np.flatnonzero(up_s[:n])
    lo_idx = np.flatnonzero(dn_s[:n])
    if len(hi_idx) >= 2 and len(lo_idx) >= 2:
        hh = h[hi_idx[-1]] > h[hi_idx[-2]]
        hl = l[lo_idx[-1]] > l[lo_idx[-2]]
        struct = "uptrend (HH/HL)" if (hh and hl) else \
                 "downtrend (LH/LL)" if (not hh and not hl) else "mixed / range"
    else:
        struct = "unknown"

    recent_entries = []
    for i in range(max(1, n - 200), n):
        if pos[i] != 0 and pos[i - 1] == 0:
            recent_entries.append(dict(bar_offset=int(i - (n - 1)),
                                       direction="long" if pos[i] > 0 else "short"))

    return {
        "symbol": sym, "tf": tf,
        "asof": str(time.iloc[-1]),
        "bars": int(n),
        "price": {
            "last": round(last, 5),
            "change_1_bar_pct": round(float(c[-1] / c[-2] - 1) * 100, 4),
            "change_20_bar_pct": round(float(c[-1] / c[-21] - 1) * 100, 4),
        },
        "indicators": {
            "ema21": round(float(e1[-1]), 5),
            "ema50": round(float(e2[-1]), 5),
            #--- this is the slope the rule actually thresholds (3-bar, same as simulate)
            "ema21_slope_atr": round(float(slope3[-1]), 3),
            "ema21_slope5_atr": round(float((e1[-1] - e1[-6]) / atr[-1]), 3),
            "rsi9": round(float(rsi[-1]), 2),
            "atr": round(float(atr[-1]), 5),
            "atr_ratio": round(atr_ratio, 3),
        },
        "structure": {"state": struct},
        "levels": levels,
        "bad_zones": {
            "spread_pts": round(float(spread[-1]), 1),
            "spread_p90_pts": (round(float(q[-1]), 1) if np.isfinite(q[-1]) else None),
            "spread_hostile": bool(np.isfinite(q[-1]) and spread[-1] > q[-1]),
            "volatility_spike": bool(atr_ratio > 1.8),
            "thin_liquidity_hour": bool(hours >= 21 or hours <= 1),
        },
        "rule": {
            "strategy": "RSI band (min-hold %d, margin %.1f)" % (min_hold, margin),
            "position": int(pos[-1]),
            "trend_state": int(trend[-1]),
            "recent_entries": recent_entries[-6:],
        },
    }


def deterministic_read(state: dict) -> dict:
    """Same schema, no model. Used when there is no API key.

    Direction comes from the deadbanded trend state, not the raw slope sign - a
    slope of -0.01 ATR is flat, not short. Momentum that fights the structure is a
    conflict, and a conflict is a WAIT, because the rule only earns when a direction
    exists and nothing contradicts it.
    """
    ind, bz, st = state["indicators"], state["bad_zones"], state["structure"]
    last = state["price"]["last"]
    slope = ind["ema21_slope_atr"]
    rsi = ind["rsi9"]
    trend = state["rule"]["trend_state"]        # -1 / 0 / +1 after the 0.10 deadband
    struct = st["state"]

    risks, hard = [], False
    if bz["spread_hostile"]:
        risks.append("spread is above its trailing p90 - cost eats the edge")
        hard = True
    if bz["volatility_spike"]:
        risks.append("ATR is above 1.8x its mean - volatility spike")
        hard = True
    if bz["thin_liquidity_hour"]:
        risks.append("rollover / thin liquidity hour")
    if struct == "mixed / range":
        risks.append("structure is mixed - no clean HH/HL or LH/LL")

    conflict = ((trend > 0 and struct.startswith("downtrend")) or
                (trend < 0 and struct.startswith("uptrend")))
    if conflict:
        risks.append(f"momentum {'up' if trend > 0 else 'down'} fights a {struct}")

    if hard:
        read, conf = "wait", 0.15
        why = risks[0]
    elif trend == 0:
        read, conf = "wait", 0.20
        why = f"EMA21 slope {slope:+.2f} ATR is inside the flat band"
    elif conflict:
        read, conf = "wait", 0.20
        why = f"EMA21 points {'up' if trend > 0 else 'down'} but structure is {struct}"
    elif trend > 0 and rsi < 75:
        read, conf = "long", 0.55
        why = ""
    elif trend < 0 and rsi > 25:
        read, conf = "short", 0.55
        why = ""
    else:
        read, conf = "wait", 0.25
        why = f"RSI9 {rsi:.1f} is outside the tradeable band for this direction"

    if read == "long":
        narrative = (
            f"Price {last:.5f}. EMA21 slope is {slope:+.2f} ATR up and RSI9 is {rsi:.1f} "
            f"inside the 25-75 band. Structure reads {struct}, so nothing contradicts the "
            "direction. That is the regime the RSI band rule is built for: buy the pullback "
            "toward 50, leave above 75."
        )
    elif read == "short":
        narrative = (
            f"Price {last:.5f}. EMA21 slope is {slope:+.2f} ATR down and RSI9 is {rsi:.1f} "
            f"inside the 25-75 band. Structure reads {struct}, so nothing contradicts the "
            "direction. Mirror of the long case: sell the bounce toward 50, leave below 25."
        )
    else:
        narrative = (
            f"Price {last:.5f}. No trade: {why}. "
            f"RSI9 {rsi:.1f}, structure {struct}, spread {bz['spread_pts']:.1f} pts. "
            "The rule only earns when a direction exists and cost is low, so standing "
            "aside is the correct action, not a missed one."
        )

    draw_levels = [dict(price=lv["price"], kind=lv["kind"], label="S/R")
                   for lv in state["levels"][:6]]
    draw_arrows = [dict(bar_offset=e["bar_offset"],
                        direction="up" if e["direction"] == "long" else "down",
                        label="RSI band")
                   for e in state["rule"]["recent_entries"]]

    return {
        "read": read,
        "confidence": conf,
        "narrative": narrative,
        "risks": risks or ["no structural risk flagged"],
        "veto": bool(hard or conflict),
        "veto_reason": (risks[0] if (hard or conflict) and risks else ""),
        "draw": {"levels": draw_levels, "zones": [], "arrows": draw_arrows},
        "source": "deterministic",
    }


def _post_json(req: urllib.request.Request, provider: str,
               attempts: int = 3) -> dict:
    """POST with retry, and surface the provider's own error text.

    A 429 from OpenAI is either rate limiting or an empty credit balance, and the
    two need completely different fixes. The body says which; the status code does
    not. Transient socket timeouts (WinError 10060) are also retried with backoff,
    because they were previously reported as "the API is broken".
    """
    last_exc: Exception | None = None

    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode())

        except urllib.error.HTTPError as exc:
            raw = ""
            try:
                raw = exc.read().decode("utf-8", "replace")
            except Exception:
                pass
            code, msg = "", ""
            try:
                err = json.loads(raw).get("error", {})
                code = str(err.get("code", "") or "")
                msg = str(err.get("message", "") or "")
            except Exception:
                msg = raw[:300]
            detail = f"{code}: {msg}".strip(": ") or f"HTTP {exc.code}"
            low = detail.lower()

            #--- rate limiting is worth waiting out; an empty balance is not
            is_quota = ("quota" in low or "billing" in low
                        or "credit" in low or "exhausted" in low)
            if exc.code == 429 and not is_quota and attempt < attempts - 1:
                time.sleep(2 ** attempt)
                last_exc = exc
                continue

            if exc.code == 401:
                hint = "  -> key rejected. Check OPENAI_API_KEY in .env"
            elif exc.code == 403:
                hint = "  -> key lacks permission for this model/project"
            elif exc.code == 429 and is_quota:
                hint = ("  -> account has NO CREDIT. Add credits at "
                        "https://platform.openai.com/settings/organization/billing/")
            elif exc.code == 429:
                hint = "  -> rate limited. Wait, or raise the --watch interval"
            elif exc.code == 404:
                hint = ("  -> model not available to this key. Try "
                        "SOVEREIGN_LLM_MODEL=gpt-4o-mini")
            else:
                hint = ""
            raise RuntimeError(
                f"{provider} HTTP {exc.code}{hint}\n     {detail}") from None

        except urllib.error.URLError as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(
                f"{provider} network error after {attempts} attempts: "
                f"{exc.reason}") from None

    raise RuntimeError(f"{provider} request failed: {last_exc}") from None


def probe(provider: str = "auto") -> int:
    """Check the key without spending tokens (GET /v1/models)."""
    if provider == "auto":
        provider = ("openai" if os.environ.get("OPENAI_API_KEY")
                    else "anthropic" if os.environ.get("ANTHROPIC_API_KEY")
                    else "deterministic")

    if provider == "openai":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            print("openai: no OPENAI_API_KEY loaded (check .env)")
            return 1
        req = urllib.request.Request("https://api.openai.com/v1/models",
                                     headers={"Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode())
        except Exception as exc:
            print(f"openai: probe FAILED - {exc}")
            return 1
        ids = sorted(m.get("id", "") for m in data.get("data", []))
        want = os.environ.get("SOVEREIGN_LLM_MODEL", "gpt-4o-mini")
        status = "AVAILABLE" if want in ids else "NOT in this key's list"
        print(f"openai: key OK, {len(ids)} models visible")
        print(f"  wanted model: {want} -> {status}")
        if want not in ids and ids:
            near = [i for i in ids if want.split("-")[0] in i][:8]
            if near:
                print(f"  similar: {', '.join(near)}")
        return 0

    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            print("anthropic: no ANTHROPIC_API_KEY loaded (check .env)")
            return 1
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode())
        except Exception as exc:
            print(f"anthropic: probe FAILED - {exc}")
            return 1
        ids = [m.get("id", "") for m in data.get("data", [])]
        want = os.environ.get("SOVEREIGN_ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        status = "AVAILABLE" if want in ids else "NOT in this key's list"
        print(f"anthropic: key OK, {len(ids)} models visible")
        print(f"  wanted model: {want} -> {status}")
        return 0

    print("deterministic: no key set - analyst runs without an API call")
    return 0


def _openai(prompt: str, image_b64: str | None) -> str:
    key = os.environ["OPENAI_API_KEY"]
    model = os.environ.get("SOVEREIGN_LLM_MODEL", "gpt-4o-mini")
    content = [{"type": "text", "text": prompt}]
    if image_b64:
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_b64}"}})
    body = json.dumps({"model": model, "temperature": 0.2,
                       "messages": [{"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": content}]}).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST")
    payload = _post_json(req, "openai")
    return payload["choices"][0]["message"]["content"]


def _anthropic(prompt: str, image_b64: str | None) -> str:
    key = os.environ["ANTHROPIC_API_KEY"]
    model = os.environ.get("SOVEREIGN_ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    content = []
    if image_b64:
        content.append({"type": "image", "source": {"type": "base64",
                                                    "media_type": "image/png",
                                                    "data": image_b64}})
    content.append({"type": "text", "text": prompt})
    body = json.dumps({"model": model, "max_tokens": 1200, "temperature": 0.2,
                       "system": SYSTEM,
                       "messages": [{"role": "user", "content": content}]}).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "Content-Type": "application/json"}, method="POST")
    payload = _post_json(req, "anthropic")
    return "".join(p.get("text", "") for p in payload.get("content", [])
                   if p.get("type") == "text")


def parse_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.startswith("json"):
            t = t[4:]
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b < 0:
        raise ValueError("no JSON object in model output")
    return json.loads(t[a:b + 1])


def sanitize(note: dict, state: dict) -> dict:
    """Never let the model draw a level it was not given."""
    atr = state["indicators"]["atr"]
    last = state["price"]["last"]
    given = [lv["price"] for lv in state["levels"]]
    draw = note.get("draw") or {}
    out_levels = []
    for lv in draw.get("levels", [])[:12]:
        try:
            p = float(lv.get("price"))
        except Exception:
            continue
        if not given:
            continue
        near = min(given, key=lambda g: abs(g - p))
        if abs(near - p) <= 0.25 * atr:      # snap to a real level
            out_levels.append(dict(price=near,
                                   kind=str(lv.get("kind", "support")),
                                   label=str(lv.get("label", ""))[:24]))
    out_zones = []
    for z in draw.get("zones", [])[:6]:
        try:
            lo, hi = float(z["price_low"]), float(z["price_high"])
        except Exception:
            continue
        if lo > hi:
            lo, hi = hi, lo
        if abs(lo - last) <= 5 * atr and abs(hi - last) <= 5 * atr:
            out_zones.append(dict(price_low=lo, price_high=hi,
                                  label=str(z.get("label", ""))[:24]))
    out_arrows = []
    for ar in draw.get("arrows", [])[:12]:
        try:
            off = int(ar["bar_offset"])
        except Exception:
            continue
        if -500 < off < 0:
            out_arrows.append(dict(bar_offset=off,
                                   direction=str(ar.get("direction", "up")),
                                   label=str(ar.get("label", ""))[:24]))
    note["draw"] = dict(levels=out_levels, zones=out_zones, arrows=out_arrows)
    note["read"] = str(note.get("read", "wait")).lower()
    if note["read"] not in ("long", "short", "wait"):
        note["read"] = "wait"
    try:
        note["confidence"] = max(0.0, min(1.0, float(note.get("confidence", 0.3))))
    except Exception:
        note["confidence"] = 0.3
    return note


def run_once(sym: str, tf: str, bars: int, use_image: bool, provider: str,
             strict: bool = False) -> dict:
    df = load(sym, tf)
    state = build_state(df, sym, tf)

    png = None
    if use_image:
        subprocess.run([sys.executable, str(ROOT / "research" / "chart_annotate.py"),
                        "--symbol", sym, "--tf", tf, "--bars", str(bars)],
                       check=False, capture_output=True)
        p = CHARTS / f"{sym}_{tf}.png"
        if p.exists():
            png = base64.b64encode(p.read_bytes()).decode()

    if provider == "auto":
        provider = ("openai" if os.environ.get("OPENAI_API_KEY")
                    else "anthropic" if os.environ.get("ANTHROPIC_API_KEY")
                    else "deterministic")

    if provider == "deterministic":
        note = deterministic_read(state)
    else:
        prompt = ("State JSON:\n" + json.dumps(state, indent=2) +
                  ("\n\nAn annotated chart image is attached for context. Use the "
                   "supplied numbers for any level you draw." if png else ""))
        try:
            raw = (_openai(prompt, png) if provider == "openai"
                   else _anthropic(prompt, png))
            note = parse_json(raw)
            note["source"] = provider
            note = sanitize(note, state)
        except Exception as exc:
            print(f"  LLM unavailable:\n  {exc}")
            print("  -> deterministic analyst used (same schema, no API cost)")
            if strict:
                raise
            note = deterministic_read(state)

    note["_state"] = state
    return note


def _common_files() -> Path:
    """MQL5 Common\\Files\\Sovereign - the only place the EA can read from."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files" / "Sovereign"
    return ROOT / "backtest" / "mt5_files" / "Sovereign"


def _cell(value: object) -> str:
    """A field the MQL5 side can split on ',' with no escaping rules."""
    return str(value).replace(",", ";").replace("\r", " ").replace("\n", " ").strip()


def write_overlay_csv(note: dict, state: dict, sym: str, tf: str) -> Path:
    """Flat sidecar the EA draws from.

    The JSON is the analyst's record; this is the EA's. One record per line,
    comma separated, because MQL5 has StringSplit and nothing else. Every
    free-text field has commas stripped so a narrative can never shift the
    layout of a drawing record.
    """
    ind = state.get("indicators", {})
    bad = state.get("bad_zones", {})
    lines = [
        "#SOVEREIGN_AI_OVERLAY=1",
        f"SYMBOL,{_cell(sym)}",
        f"TF,{_cell(tf)}",
        f"ASOF,{_cell(state.get('asof', ''))}",
        f"READ,{_cell(note.get('read', 'wait'))},"
        f"{float(note.get('confidence', 0.0)):.2f},{1 if note.get('veto') else 0}",
        f"SOURCE,{_cell(note.get('source', 'deterministic'))}",
        f"NARR,{_cell(note.get('narrative', ''))}",
        f"VETO,{_cell(note.get('veto_reason', ''))}",
        f"PRICE,{float(state.get('price', {}).get('last', 0.0)):.5f}",
        f"EMA21,{float(ind.get('ema21', 0.0)):.5f}",
        f"EMA50,{float(ind.get('ema50', 0.0)):.5f}",
        f"RSI,{float(ind.get('rsi9', 0.0)):.1f}",
        f"ATR,{float(ind.get('atr', 0.0)):.5f}",
        f"SLOPE,{float(ind.get('ema21_slope_atr', 0.0)):+.3f}",
        f"STRUCT,{_cell(state.get('structure', {}).get('state', ''))}",
        f"SPREAD,{float(bad.get('spread_pts', 0.0)):.1f},"
        f"{1 if bad.get('spread_hostile') else 0}",
    ]
    for r in (note.get("risks") or [])[:6]:
        lines.append(f"RISK,{_cell(r)}")

    draw = note.get("draw", {}) or {}
    for lv in draw.get("levels", []):
        try:
            price = float(lv["price"])
        except (KeyError, TypeError, ValueError):
            continue
        lines.append(f"LEVEL,{price:.5f},{_cell(lv.get('kind', 'level'))},"
                     f"{_cell(lv.get('label', ''))}")
    for z in draw.get("zones", []):
        try:
            lo, hi = float(z["price_low"]), float(z["price_high"])
        except (KeyError, TypeError, ValueError):
            continue
        if lo > hi:
            lo, hi = hi, lo
        lines.append(f"ZONE,{lo:.5f},{hi:.5f},{_cell(z.get('label', ''))}")
    for a in draw.get("arrows", []):
        try:
            off = int(a["bar_offset"])
        except (KeyError, TypeError, ValueError):
            continue
        if off > 0:
            off = -off
        lines.append(f"ARROW,{off},{_cell(a.get('direction', 'up'))},"
                     f"{_cell(a.get('label', ''))}")

    body = "\n".join(lines) + "\n"
    written = RESULTS / f"ai_overlay_{sym}_{tf}.csv"
    for target in (written, _common_files() / f"ai_overlay_{sym}_{tf}.csv"):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="ascii", errors="replace")
        except OSError as exc:
            print(f"  could not write {target} ({exc})")
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--bars", type=int, default=320)
    ap.add_argument("--image", action="store_true", help="attach the chart PNG (vision)")
    ap.add_argument("--provider", choices=("auto", "openai", "anthropic"), default="auto")
    ap.add_argument("--watch", type=int, default=0, help="re-run every N seconds")
    ap.add_argument("--probe", action="store_true",
                    help="check the key with GET /v1/models (no token spend)")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero instead of falling back to deterministic")
    args = ap.parse_args()

    sym, tf = args.symbol.upper(), args.tf.upper()

    if args.probe:
        return probe(args.provider)

    while True:
        note = run_once(sym, tf, args.bars, args.image, args.provider,
                        strict=args.strict)
        state = note.pop("_state")
        RESULTS.mkdir(parents=True, exist_ok=True)
        out = RESULTS / f"ai_note_{sym}_{tf}.json"
        out.write_text(json.dumps({"note": note, "state": state}, indent=2),
                       encoding="utf-8")
        csv_out = write_overlay_csv(note, state, sym, tf)

        print(f"=== AI chart analyst  {sym} {tf}  ({note.get('source')}) ===")
        print(f"asof {state['asof']}   price {state['price']['last']:.5f}   "
              f"RSI9 {state['indicators']['rsi9']:.1f}   "
              f"slope {state['indicators']['ema21_slope_atr']:+.2f} ATR")
        print(f"structure {state['structure']['state']}   "
              f"spread {state['bad_zones']['spread_pts']:.1f} pts"
              f"{'  HOSTILE' if state['bad_zones']['spread_hostile'] else ''}")
        print("")
        print(f"READ: {note['read'].upper()}   confidence {note['confidence']:.2f}"
              + ("   VETO" if note.get("veto") else ""))
        print("")
        print(note["narrative"])
        if note.get("veto_reason"):
            print(f"\nveto: {note['veto_reason']}")
        if note.get("risks"):
            print("\nrisks:")
            for r in note["risks"][:6]:
                print(f"  - {r}")
        d = note.get("draw", {})
        print(f"\ndraw: {len(d.get('levels', []))} levels, "
              f"{len(d.get('zones', []))} zones, {len(d.get('arrows', []))} arrows")
        print(f"wrote {out}")
        print(f"wrote {csv_out}")
        print("")

        if args.watch <= 0:
            return 0
        print(f"watching - re-running every {args.watch}s (Ctrl+C to stop)\n")
        time.sleep(args.watch)


if __name__ == "__main__":
    raise SystemExit(main())
