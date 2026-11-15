# FXMOPOL market pulse (instant condition readout)

## What this is — and what it is not

| Claim | Reality |
|-------|---------|
| “Analyze the market 100%” | **100% of the checklist** we programmed (EMA stack, RSI, chop, PA triggers, TrafficLight). |
| “Know the best thing in split seconds” | **One recommended action** on each **new closed bar** (H1), computed in one pass on the chart. |
| “Always win” | **No.** OOS research still shows many symbols fail promotion. Pulse is decision support, not prophecy. |

No system can know every future tick. Pulse always answers **two questions**:

1. **What is the market doing right now?** — plain-language regime (`q1_market_now` / chart `Q1 NOW`).
2. **How can I get in and out quickly without trouble?** — entry trigger, stop, exit rules, and an **issues** list (chop, spread, TrafficLight veto, profile auto-off).

Profitable quick in/out is the **goal**; the issues line is what helps you avoid bad fills. Research still shows many symbols are not OOS-promoted — respect `issues`.

## Layers rolled into one action

1. **Regime** — up / down / chop (`MarketRegime`, `chart_state`)
2. **Price action** — pullback, hunt, EMA cross, RSI div (Python pulse; playbook in EA entries)
3. **Support / resistance** — optional in research (`USE_SR_FILTER`)
4. **Technical** — EMA21/50/SMMA100, RSI bands, proximity fan
5. **Fundamental + sentiment** — TrafficLight macro/sentiment in EA; FRED+VIX optional in research

## Live chart (split-second on new bar)

1. Attach `SovereignFxmopolEA` on **H1** with `InpMarketPulse = true` (default).
2. Each new bar: top-left **Comment** + Experts log line, e.g.  
   `BEST MOVE: WATCH_LONG | ctx L2/S0` and TrafficLight green counts.
3. Works even when `InpEnforceSymbolProfile` blocks auto-trades (`maxPerDay=0`) — advisor only.

## Offline / mobile prep

```powershell
.\.venv\Scripts\python.exe research\fxmopol_market_pulse.py --symbol GBPUSD
.\.venv\Scripts\python.exe research\fxmopol_market_pulse.py --symbol EURUSD --json
```

Uses longest H1 CSV in `research/data/`.

## Action codes (EA)

| Action | Meaning |
|--------|---------|
| `WAIT_FLAT` | Chop or no edge — stay out |
| `WATCH_LONG` / `WATCH_SHORT` | Trend side OK, wait for trigger |
| `ENTER_LONG_NOW` / `ENTER_SHORT_NOW` | Context score 3 — playbook-quality |
| `HOLD_*` / `TIGHTEN_*` / `EXIT_OR_FLAT` | Position management hint |

Python adds finer strings (`entry_pullback_ema21`, etc.) from `fxmopol_market_pulse.py`.

## LLM (GPT / Claude) with almost no extra code

| Approach | Code | Safe for live? |
|----------|------|----------------|
| **Paste pulse into ChatGPT/Claude** | Zero — run `.\scripts\fxmopol_market_now.ps1`, paste output | Yes (human decides) |
| **`fxmopol_llm_brief.ps1`** | ~90 lines; pulse JSON → API narrative | Yes — **no orders**, text only |
| **`century_adapt.py`** | Already in repo | Yes — LLM only **tightens** risk JSON the EA already clamps |
| **LLM picks entries** | Not recommended | No — overfits; not wired in FXMOPOL |

```powershell
$env:OPENAI_API_KEY = "sk-..."   # or ANTHROPIC_API_KEY
.\scripts\fxmopol_llm_brief.ps1 -Symbol GBPUSD
# Optional: -Provider anthropic
```

Optional model env: `SOVEREIGN_LLM_MODEL`, `SOVEREIGN_ANTHROPIC_MODEL`.

**Do not** let the LLM replace the pulse math, symbol profiles, or TrafficLight — use it to **explain** Q1/Q2 in plain English.

## Train the rules, not omniscience

Walk-forward training (`fxmopol_symbol_train.py`) picks **which filters** help per symbol on hold-out data. Pulse uses those rules live; it does not replace promotion gates or risk caps.

See [FXMOPOL_SIGNAL_STACK.md](FXMOPOL_SIGNAL_STACK.md), [FXMOPOL_DECISION_RULES.md](FXMOPOL_DECISION_RULES.md).
