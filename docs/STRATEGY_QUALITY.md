# Good strategy — implemented bar

The definition is **code**, not prose: `configs/strategy_quality_bar.json` + `research/strategy_certification.py`.

## Tiers

| Tier | Meaning |
|------|---------|
| **research_good** | Mandatory gates for sleeve family (splits, PF, tape, MT5 parity if M5). |
| **desk_good** | research_good + Sharpe/CAGR/DD desk floor (carry) or activity (scalp). |
| **live_good** | desk_good + `research/results/forward_demo_manifest.json` forward pass (optional; policy still FROZEN). |

## Commands

```powershell
.\scripts\run_strategy_certification.ps1
```

Artifacts:

- `research/results/strategy_certification_verdict.json` — per-sleeve gates and reasons  
- `configs/good_strategies.json` — router lists (`research_good`, `desk_good`, `live_good`)

## Sleeve families

| Family | Config block | Evidence files |
|--------|--------------|----------------|
| D1 carry | `gates.carry_monthly` | `carry_promotion_verdict.json` |
| M5 scalp | `gates.scalp_intraday` | `scalp_promotion_verdict.json`, broker `bar_meta.chosen=mt5` |
| H1 satellite | `gates.h1_overlay` | `h1_promotion_verdict.json` |
| Discovery | `gates.discovery_daily` | `discovery_proof.csv` (all 3 splits) |

## Router

`profitable_sleeve.py` and `run_profitable_sleeve.ps1` read certification output. **Only `research_good` sleeves** appear as MT5 trade candidates.

See `docs/PROFITABLE_SLEEVE.md` for what to attach in the terminal today.
