# Profitable sleeve — what to actually trade

**Problem we fixed in process:** chasing scalp on HistData while **no edge** survived broker + tester.  
**Problem we did not magically solve:** nobody has found a **high-return, low-DD** robot. The repo routes you to the **least bad honest sleeve**.

Formal bar: **`docs/STRATEGY_QUALITY.md`** + `configs/strategy_quality_bar.json`.

## The answer today

| Sleeve | 3-split proof | MT5 | Role |
|--------|---------------|-----|------|
| **`carry_mom_filter`** | Yes (`carry_promotion_verdict.json`) | `run_lane1_mt5_truth.ps1` | **Only default book** (~1–3% CAGR class, ~−26% DD) |
| Scalp M5 | No | `run_scalp_mt5_truth.ps1` | Research / kill ideas fast |
| H1 FXMOPOL | Overlay only | `run_tester_suite.ps1` | Satellite, not core |
| Discovery “OOS-only pass” | **No** (fails design/val) | Not wired | Queue only |

Regenerate routing:

```powershell
.\scripts\run_carry_book.ps1
.\scripts\run_profitable_sleeve.ps1
```

Output: `configs/profitable_sleeve.json`, `research/results/profitable_sleeve_verdict.json`

**Monthly broker proof** (after carry book tester): `research/carry_mt5_monthly.py` parses the tester HTML deal ledger into calendar months and compares to `carry_mom_filter_monthly.csv`:

- `research/results/carry_mt5_book_monthly.csv` — MT5 baseline + ECN profiles  
- `research/results/carry_mt5_python_mt5_monthly_compare.csv` — same-month Python vs broker  
- `research/results/carry_mt5_monthly_manifest.json` — window stats + correlation

## Trade in MT5 (demo / tester only — still FROZEN live)

1. Chart **D1** on any major in the basket  
2. EA **`SovereignGodsEyeEA`**, `InpStrategy=SOV_CARRY_MOM`  
3. Install rates: `.\scripts\install_ea.ps1`  
4. Prove on **your** broker: `.\scripts\run_lane1_mt5_truth.ps1 -NoVisual`

Scalp: **do not** attach London ORB for profit until `scalp_promotion_verdict.json` has `any_promoted: true` **and** tester parity.

## Finding the *next* strategy (MT5-first)

1. **D1:** `.\scripts\run_discovery.ps1` — only trust rows that pass **design + validation + OOS** (`profitable_sleeve.py` filters).  
2. **M5:** `research/mt5_m5_hypothesis_scan.py` on exported broker bars — if nothing beats PF 1.05, send survivors to **Strategy Tester optimization**, not more Python.  
3. Never promote from HistData when `require_mt5_tape_for_python` is on.

## Policy

`AGENTS.md` remains **FROZEN for live**. This doc is **which hypothesis to spend time on**, not permission to deploy.
