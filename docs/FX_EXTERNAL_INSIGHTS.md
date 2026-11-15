# External research → Sovereign actions

Synthesis from literature and public audits (2024–2026). **Not** live advice.

## London breakout / M5 scalp

| Source | Finding | Repo action |
|--------|---------|-------------|
| [Holdout Labs LB-1](https://holdoutlabs.io/audits/london-breakout/) | **Gross** negative on GBP; no cost level profitable | `london_orb_gross_net.py`; ORB **not** primary |
| [forexsignals.io 10y test](https://forexsignals.io/blog/london-breakout-strategy-tested) | Small **gross** edge **&lt; spread** on GBP | Cost-first gates in `m5_execution_cost.py` |
| Riyadex / execution notes | Round-trip cost eats tiny M5 targets | `require_mt5_tape_for_python`, tester certify |

## Carry + momentum (slow book)

| Source | Finding | Repo action |
|--------|---------|-------------|
| JIMF 2024 carry OOS | Profits **unstable**; luck/snooping; 1998–2005 cluster | Keep **3-split** bar; no live policy |
| EUR thesis carry vs mom | Mix **low correlation**; mom weakened post-2011 | Default EA: `carry_mom_filter` |
| Filippou et al. 2024 costs | Turnover + impact erodes carry; need **cost-aware** allocation | `ecn_carry_friction`, `carry_train_grid`, ECN tester profile |

## What profitable **systems** do differently

1. **Slow premia** (carry, trend on weeks/months) — matches our only `research_good` sleeve.  
2. **Cost inside the signal** — gross vs net before optimization (`london_orb_gross_net.py`).  
3. **Hold longer** — H1 swing 1–7 days (academic TA swing); `swing_h1_lane.py` on broker H1.  
4. **MT5 / broker as truth** — not a second Python market.

## Active repo mode

See `configs/sovereign_trading_mode.json`:

- **Trade:** D1 carry (`SovereignGodsEyeEA`)  
- **Discontinued primary:** M5 London ORB until `london_orb_gross_net.json` → `gross_viable`  
- **Next research:** `swing_h1_lane.py` + discovery strict 3-split
