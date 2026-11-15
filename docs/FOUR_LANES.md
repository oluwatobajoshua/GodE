# Four lanes (execution map)

Project stays **FROZEN** for live. These lanes close gaps between research, costs, risk appetite, and non-FX sleeves.

## Lane 1 — Truth on friction and MT5

| Step | Command | Output |
|------|---------|--------|
| Monthly ECN sweep | `research/ecn_carry_friction.py` | `ecn_carry_friction.csv` |
| MT5 long window | `.\scripts\run_lane1_mt5_truth.ps1 -NoVisual -From 2005.01.01` | `backtest/reports/*` |

Profiles: `gods_eye_carry_d1`, `gods_eye_carry_d1_ecn` (`InpMaxSpread=8`).

**Finding (2026-11-08):** Refined stack stays **3-split positive** from **0–20 pts** cost; at **8 pts** full CAGR **~2.58%**, max DD **~−24.8%** (Python monthly).

## Lane 2 — Outside G10 spot

`research/alt_sleeve_scan.py` → `alt_sleeve_manifest.json`

Simple **12-1 TSMOM** on BTC / XAU / DXY when CSVs exist. **Not promoted** unless `promoted_3split: true` in manifest — carry stack remains default.

## Lane 3 — Higher risk (documented DD floor)

`research/carry_high_risk.py` → `carry_high_risk_pick.json`

Vol-target scale on the **same** refined stack (`vol_target` 10–20%). Pick = max full CAGR with **3-split pass** and max DD ≥ **−35%**.

Tester profile: `gods_eye_carry_high_risk` (`InpRiskPerPosition=2`, `InpMaxTotalRisk=8`).

## Lane 4 — Balanced default (unchanged policy)

`stack_refine.py` + `apply_best_settings.py` → GodsEye **balanced** inputs (`mom12_n4_v16_28_m0.45_ddoff_neu1`).

## Run all Python lanes

```powershell
.\scripts\run_all_lanes.ps1
```

Merge: `research/results/lanes_summary.json`.
