# MT5 data plane — one tape for “seeing the market”

**Problem we fixed:** Research (HistData CSV), the ecosystem professor (`market_snapshot.json`), and the Strategy Tester (broker bars) were three different views of price. That guarantees nonsense H1 curves and `parity_ok: false` even when logic is fine.

**Rule:** For any symbol you trade in MT5, **professor + playbook + parity checks** must read the **same H1 series** as the tester: `research/data/mt5/{SYM}_H1.csv` exported from your terminal.

Project remains **FROZEN for live** per `AGENTS.md`. This pipeline is alignment and honesty, not a profitability claim.

---

## What MT5 supplies vs what we use now

| Data | MT5 API | Sovereign usage after data plane |
|------|---------|----------------------------------|
| OHLC + spread history | `copy_rates_range` | **Canonical** `research/data/mt5/` |
| Live bid/ask/spread | `SymbolInfo*` | EA entry filters (unchanged) |
| Swap | `SYMBOL_SWAP_*` | Carry EA (unchanged) |
| Ticks / DOM | `copy_ticks_range`, `MarketBookGet` | Optional `export_l2.ps1` → microstructure |
| Calendar | `CalendarValueHistory` | Still CSV `fxmacrodata/` (future: terminal hook) |

The old TCP brain (`institutional_quant_master.py`) stays **synthetic** and is **not** the data plane.

---

## Commands (run in order)

```powershell
# 1) Export broker history (terminal installed; can be closed)
.\scripts\sync_mt5_data_plane.ps1 -Export

# 2) Refresh professor + audit + optional install to MQL5/Files
.\scripts\sync_mt5_data_plane.ps1 -Install

# 3) Only audit (no export/ingest)
.\scripts\sync_mt5_data_plane.ps1 -AuditOnly
```

Manual export:

```powershell
.\.venv\Scripts\python.exe scripts\export_mt5_bars.py --symbols GBPUSD EURUSD USDJPY
```

---

## Config

`configs/mt5_data_plane.json`:

- `canonical_bar_source`: `"mt5"` (default)
- `audit.overlap_from` / `overlap_to` — window to compare mt5 vs histdata
- `audit.close_pips_p99_max` — fail if broker vs archive closes diverge (default 3 pips)
- `audit.require_mt5_export_for_go` — audit exit 1 if no mt5 export

Override tape: `$env:SOVEREIGN_BAR_SOURCE = "histdata"` (discouraged for tester parity).

---

## Code map

| Piece | Role |
|-------|------|
| `research/bar_store.py` | Single `load_h1()` for playbook + professor |
| `scripts/export_mt5_bars.py` | MT5 → `research/data/mt5/` |
| `research/mt5_data_plane_audit.py` | Spread, sessions, tape diff, chart_state mismatch |
| `scripts/sync_mt5_data_plane.ps1` | Export → ingest → install → audit |
| `python/ecosystem_professor.py` | Snapshot includes `data_plane` + per-symbol `bar_source` |

Artifacts:

- `research/data/mt5/mt5_export_manifest.json`
- `research/results/mt5_data_plane_audit.json`
- `research/results/ea_stack_parity_report.json` (after tester trades exist)

---

## Strategy Tester workflow (H1)

1. Run `sync_mt5_data_plane.ps1 -Export -Install` so `FILE_COMMON` snapshot matches canonical bars.
2. Run GBP H1 tester on **the same broker** you exported from.
3. `research\ea_stack_parity_check.py` — Python OOS vs tester trades.
4. If audit shows `close_diff_pips_p99 > 3` vs HistData, **ignore** HistData-only research for that symbol.

**Carry (D1)** is less sensitive to H1 tape issues; keep proving carry on D1 separately (`run_lane1_mt5_truth.ps1`).

---

## Scalp lane (M5) — tester is the engine

Do **not** treat HistData + guessed costs as profitability. Use MT5 end-to-end:

| Step | MT5 native | Sovereign wrapper |
|------|------------|---------------------|
| OHLC + spread | `copy_rates_range` | `scripts/export_mt5_bars.py` → `research/data/mt5/{SYM}_M5.csv` |
| Fast research replay | Same CSV as tester | `research/scalp_lane_m5.py` (`bars.research_source: mt5_only`) |
| Profit proof | **Strategy Tester** | `scripts/run_london_orb_promote.ps1` |
| One command | — | `scripts/run_scalp_mt5_truth.ps1` |

Python OOS split **`tester_oos`** matches the tester window in `configs/scalp_lane.json` (default 2024-07 → 2026-01). Promotion requires **`require_mt5_tape_for_python`** and **`require_mt5_parity`**.

HistData M5 (`SOVEREIGN_M5_SOURCE=histdata_blend`) is **exploration only** — never certifies an EA.

---

## Interpreting audit

| Signal | Meaning |
|--------|---------|
| `resolved_source: mt5` | Professor/playbook see broker tape |
| `missing_mt5_export` | Not “seeing” broker market in research |
| `close_diff_p99_exceeded` | HistData and broker disagree — do not mix |
| `chart_state_mismatch_pct` high | EA indicators vs Python professor diverge on same timestamps |
| `spread.available: false` on histdata-only | Cost model in research is blind to real spread |

---

## Profit expectations (honest)

Aligned data **does not create edge**. It stops you from optimizing and testing on **fiction**. The only promoted slow sleeve remains **monthly carry + momentum** (`carry_promotion_verdict.json`). H1 stays **overlay_only** until OOS promotion passes **after** data plane is green.
