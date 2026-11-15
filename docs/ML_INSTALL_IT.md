# ML and Python on locked-down Windows (ASR blocks pip.exe)

**Repo policy:** Agents and `scripts\` must never call `pip.exe` / `pip3.exe`. See
`.cursor/rules/asr-safe-execution.mdc` and `AGENTS.md` § ASR.

Your policy blocked: `VENV\Scripts\pip.exe` (Attack Surface Reduction — low prevalence).

## Safe path (no pip.exe)

Use **only** `\.venv\Scripts\python.exe` on scripts. Core stack already works:

```powershell
.\scripts\run_ml_no_pip.ps1
```

This runs:

1. `fx_env_core.py` — RL environment (NumPy only)
2. `rl_train_sklearn.py` — policy research (scikit-learn, already in requirements.txt)
3. `horizon_forecast_sklearn.py` — multi-horizon forecast → `prophet_forecasts.json`

Then refresh professor brain **without pip**:

```powershell
.\.venv\Scripts\python.exe python\market_ingest.py
```

Sleep stack:

```powershell
.\scripts\run_sleep_gbp_h1.ps1 -Prepare
```

## Optional Gymnasium / PyTorch later (IT path)

Ask admin to **allowlist** (not pip.exe):

- `C:\xampp\htdocs\GodE\.venv\Scripts\python.exe`

Or install wheels on a build machine and copy `site-packages` via approved software deployment — **do not** run pip.exe from the venv in scripts.

## What we stopped doing

- No `pip install` in PowerShell scripts
- No subprocess calling `pip.exe`
- TFT/PPO wrappers remain in repo but optional until IT allows extra packages
