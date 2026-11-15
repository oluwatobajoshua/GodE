# H1 FXMOPOL promotion rule

Pre-registered in [research/h1_promotion.py](../research/h1_promotion.py):

| Criterion | Default |
|-----------|---------|
| OOS trades | ≥ 30 |
| OOS total return | > 0% |
| OOS profit factor | ≥ 1.05 |

Until all pass: **`role: overlay_only`** — size via professor/governor; core book is `carry_mom_filter`.

```powershell
.\.venv\Scripts\python.exe research\fxmopol_stack_pnl.py --symbol GBPUSD
.\.venv\Scripts\python.exe research\h1_promotion.py --mode professor_governor
```
