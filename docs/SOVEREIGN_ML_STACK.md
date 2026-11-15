# Sovereign ML stack (RL + TFT)

Research components requested for the **alive** ecosystem — not live-certified until OOS proof.

## IT-safe default (no pip.exe)

```powershell
.\scripts\run_ml_no_pip.ps1
```

- `fx_env_core.py` — same finance rules, NumPy only
- `rl_train_sklearn.py` — sklearn policy smoke
- `horizon_forecast_sklearn.py` — purged CV + HGBR → `prophet_forecasts.json`

See `docs/ML_INSTALL_IT.md`.

## Gymnasium FX env (PPO-ready, optional)

- `fx_gym_env.py` — only if gymnasium is allowed by IT
- Smoke: `.\scripts\run_rl_env_smoke.ps1`

## TFT quantile forecaster

- `research/ml/tft_forecast_pipeline.py` — purged split, encoder 60 → predict 5 horizons, quantiles 10/50/90
- `research/ml/purged_cv.py` — label-window purge
- Prep: `.\scripts\run_tft_pipeline.ps1 -PrepOnly`
- Train (GPU optional): `.\scripts\run_tft_pipeline.ps1 -Epochs 8`

Stop/TP from quantiles: `risk_bounds_from_quantiles()` in TFT module.

## Wire into Prophet / sleep stack (next)

Export TFT median/quantiles into `prophet_forecasts.json` after promotion gate passes.
