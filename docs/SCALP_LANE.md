# Scalp lane (short patience, high frequency)

**Status:** Research only. Project stays **FROZEN for live**.

## MT5-first (do not reinvent the wheel)

| Wrong | Right |
|-------|--------|
| Long HistData M5 + “spread calibration” | **Broker** `copy_rates` → `research/data/mt5/` |
| Python-only OOS promotion | **Strategy Tester** report JSON + promotion gate |
| Manual HTML copy | `run_backtest.py` + `london_orb_tester_collect.py` |

**Single command for truth:**

```powershell
.\scripts\run_scalp_mt5_truth.ps1          # export M5 + Python on broker tape + tester certify
.\scripts\run_scalp_mt5_truth.ps1 -ExportOnly   # no tester (MT5 can stay open)
```

Underlying pieces (same as H1 data plane):

```powershell
.\scripts\sync_mt5_data_plane.ps1 -Export -ScalpM5 -SkipIngest
.\scripts\run_london_orb_promote.ps1        # UTC sweep + MT5 OOS + promotion
```

Config: `configs/scalp_lane.json`

- `bars.research_source`: **`mt5_only`** (default)
- `bars.tester_oos`: same window as tester
- `promotion.require_mt5_tape_for_python` + `require_mt5_parity`

Exploration on long HistData (not for promotion):

```powershell
$env:SOVEREIGN_M5_SOURCE = "histdata_blend"
.\.venv\Scripts\python.exe research\scalp_lane_m5.py
```

## Strategies under test

1. **`london_orb`** — `SovereignLondonOrbEA.mq5`, profile `london_orb_eurusd_m5`
2. **`ema21_pullback`** — `SovereignEmaTrendEA.mq5` (tester path TBD)

Costs: per-bar **`spread`** column from MT5 export + `m5_execution_cost.py` (not flat 15 points).

## Promotion rule

- Python OOS on **`tester_oos`**, broker M5 only
- OOS trades **≥ 150**, PF **≥ 1.05**, total **> 0**
- **MT5 tester**: net **> 0**, PF **≥ 1.05** (same window)

Until `any_promoted: true`, **do not** size live.

### MT5 OOS reference (EURUSD London ORB, 2024.07–2026.01)

UTC sweep **0–3**: best offset **0** → net **−302**, PF **0.94**, 742 trades. **`any_promoted: false`**.

## Outputs

- `research/data/mt5/*_M5.csv`, `mt5_export_manifest.json`
- `research/results/scalp_lane_manifest.json` (`m5_policy: mt5_only`)
- `research/results/scalp_promotion_verdict.json`
- `research/results/london_orb_tester_manifest.json`

## Related

- `docs/MT5_DATA_PLANE.md`
- `scripts/run_lane1_mt5_truth.ps1` — D1 carry tester path
- `AGENTS.md` — frozen for live
