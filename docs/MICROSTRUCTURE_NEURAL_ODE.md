# Continuous microstructure AI (research)

## What you asked for vs what we have on disk

| Layer | Target (institutional) | Repo today |
|-------|----------------------|------------|
| L2 OFI | BBO + 3 levels | `microstructure_ofi.py` + `l2_loader.py` — **MT5 ticks** when `research/data/l2/{SYM}_l2_ticks.csv` exists; else M5 synthetic |
| Hawkes λ(t) | Tick event cascades | `hawkes_intensity.py` on bar burst events |
| Neural ODE | torchdiffeq | `neural_ode_euler.py` (NumPy Euler, IT-safe) + `neural_ode_torch_optional.py` |
| Alpha head | Huber on micro return | Euler head A + `huber_loss()` |
| Toxicity head | VPIN | `vpin_proxy()` + head B → `throttle_execution` flag |
| Data | Terabytes L2 | **Gap:** `research/data/l2/` empty — export from MT5 / vendor |

## Run (no pip.exe)

```powershell
.\scripts\run_l2_microstructure.ps1 -Symbol GBPUSD -Days 14
# or pipeline only:
.\.venv\Scripts\python.exe research\ml\microstructure_pipeline.py --symbol GBPUSD
```

Outputs:

- `research/results/microstructure_GBPUSD_M5.csv` or `*_ticks.csv` (tag reflects book source)
- `research/results/microstructure_GBPUSD_manifest.json`
- `backtest/mt5_files/Sovereign/microstructure_manifest.json` (EA-adjacent copy)

## Upgrade path

1. With MT5 running: `.\scripts\export_l2.ps1 -Symbol GBPUSD -Days 14` → `research/data/l2/GBPUSD_l2_ticks.csv`.
2. Re-run pipeline (auto-prefers ticks, 1s resample for ODE speed).
3. Optional: `-BookSnapshot` on export for a one-shot DOM CSV.
4. When IT allows: torch + torchdiffeq via **whitelisted python.exe** only (`ML_INSTALL_IT.md`).
5. Professor ingest merges manifests into `market_snapshot.json` → `microstructure` + gate `risk_mult` overlay.

## Trade-off matrix (your table)

Documented in manifest `data_gap` / `torch_status` — discrete M5 proxy is **not** sub-ms HFT; it is the research spine until L2 lands.
