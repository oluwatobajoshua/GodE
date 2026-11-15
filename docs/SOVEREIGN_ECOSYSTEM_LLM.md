# Sovereign Ecosystem LLM (Professor layer)

**Status:** Research / FROZEN for live. The professor ingests real research data and writes
`market_snapshot.json`; the EA scales or blocks new risk — it does not replace OOS proof.

## What it is

A **manifest-driven market brain** with an LLM-like lifecycle (ingest → snapshot → deploy → learn):

| Pillar | Hedge-fund analogue | Source on disk | Professor output |
|--------|---------------------|----------------|------------------|
| **Technical** | Systematic / desk TA | `*_H1.csv` | Stack, regime, RSI, D1, playbook pulse |
| **Fundamental** | Rates / carry desks | `rates/*.csv` (FRED) | Carry, 3m rate momentum |
| **Sentiment** | Risk premia | `VIX.csv`, `fxmacrodata/risk_sentiment.csv` | VIX, composite risk z |
| **Positioning** | COT / flow monitors | `cot/fut_fin_*.csv` | Lev-money % OI, z-score, crowding |
| **Events** | Macro calendar | `fxmacrodata/calendar_USD.csv` | Hours to NFP/CPI/FOMC, event window |
| **Intermarket** | Cross-asset | `DXYUSD_M5.csv`, `XAUUSD_M5.csv` | USD regime, gold safe-haven bias |
| **Cross-section** | RV / momentum pods | G10 `*_H1.csv` | 5d mom rank, EUR correlation |
| **Vol / liquidity** | Micro / execution | H1 `spread`, volume | Realized vol, ATR%, spread pctile |
| **Session** | Session alpha | Bar UTC time | London, overlap |
| **Policy** | Risk limits | learned JSON + century | Halt / risk scalar |

Snapshot includes `knowledge_coverage` — % of hedge-fund-style pillars present on disk
and explicit **gaps** (options skew, EM credit, bank flows, NLP — not yet wired).

## Commands

```powershell
.\scripts\run_market_ingest.ps1
.\scripts\run_market_ingest.ps1 -Symbols "GBPUSD,EURUSD,USDJPY"
.\.venv\Scripts\python.exe python\test_ecosystem_snapshot.py
.\scripts\install_ea.ps1   # refreshes snapshot + copies to MQL5/Files + Common\Files
```

`century_daemon.py` calls `market_ingest.run_once()` each interval (with adapt).

## EA wiring

- `python/market_ingest.py` → `MQL5/Files/Sovereign/market_snapshot.json` (and Common mirror on install)
- `EA/Include/SovereignMarketSnapshot.mqh` — reads `symbol_gates` + `global`
- `SovereignFxmopolEA.mq5` — `InpEcosystemProfessor` applies risk mult and long/short blocks; pulse comment shows professor line

## Data you must have

1. **H1 bars** per symbol — `research/data/{SYMBOL}_H1.csv` (or `histdata/`)
2. **FRED rates** — `research/data/rates/{US,GB,EZ,JP,...}.csv` + `VIX.csv` (copied by `install_ea.ps1`)
3. **Optional:** `DXYUSD_M5.csv` for intermarket USD regime
4. **Optional:** MT5 `trade_outcomes.csv` / learned JSON under `backtest/mt5_files/Sovereign/`

Missing files degrade gracefully (pillar marked unavailable; gates default to permissive except chop/fund blocks).

## Not included (by design)

- No scraping social/news in the EA tick loop
- No claim of profitability; negative OOS remains preserved in `AGENTS.md`
- Python `institutional_quant_master.py` synthetic brain is **not** the professor source of truth
