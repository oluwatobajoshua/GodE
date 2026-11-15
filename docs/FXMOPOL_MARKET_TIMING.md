# FXMOPOL market timing (research + our backtests)

**Goal:** Fewer, higher-quality trades when liquidity and structure align — session/weekday gates + PA stack, not a forced one-trade-per-day rule.

## External evidence (FX microstructure)

| Finding | Source |
|--------|--------|
| Highest spot volume in **London–New York overlap**; GBP/EUR pairs especially | Oanda volume studies; NBER electronic broking paper |
| **Tuesday** often highest volume; **Friday** often lowest among weekdays | Currency futures day-of-week papers |
| Actionable overlap roughly **13:00–16:00 UTC** (winter); London open **07:00–11:00 UTC** | Session guides + MacroSetup hourly pip stats |

Weekday effects **vary by pair and decade** — any mask must be checked on **our** playbook trades, not copy-pasted from papers.

## Research workflow (preferred)

1. **Baseline** — `fxmopol_playbook.py` with **no** session/weekday gates (PA + D1 + proximity only).
2. **Learn** — `fxmopol_filter_learn.py` drops losing **design** buckets (hour + weekday); freezes mask/hours.
3. **Prove** — validation + OOS in `fxmopol_filter_learn_summary.csv`; only then copy to EA `InpWeekdayMask` / custom hours.

Optional literature prior (Tue–Thu, 07–11 & 13–16) is **not** the default in research anymore.

## Continuous improvement (every trade)

| Layer | What it does |
|-------|----------------|
| **SovereignPerformance** | Each fill updates buckets `SOV-FXMOPOL\|sym\|setup\|dow\|hour`; blocks losers, scales winners |
| **`python/fxmopol_adapt.py`** | Offline mirror from `trade_outcomes.csv` / `performance_buckets.csv` |
| **`research/fxmopol_filter_learn.py`** | Batch learn hour/dow from baseline replay (design only) |

Hook adapt into the century stack: run `fxmopol_adapt.py` after tester or from `century_daemon.py` when you extend it.

## What we implemented (EA + `fxmopol_playbook.py`)

| Filter | Default | Notes |
|--------|---------|------|
| `InpGoodSessionsOnly` | true | New entries **07–11** and **13–16** (server time) |
| `InpWeekdayMask` | **14** | Tue + Wed + Thu (`1<<1 \| 1<<2 \| 1<<3`, Mon=bit0) |
| `InpMaxPerDay` | **2** (GBP playbook default) | Cap overtrading; not “one trade only” |
| Stack | Full PA + D1 + proximity + `GOOD` stay-away | Quality, not frequency |

Exits still run outside the window; only **new entries** are blocked.

## Our data (playbook, good sessions already on) — honest

Run: `.\.venv\Scripts\python.exe research\fxmopol_session_calendar.py`  
Outputs: `research/results/fxmopol_session_calendar.json`, `*_calendar.csv`

With **small trade counts** per bucket:

- **GBPUSD OOS** inside prime hours: ~5 trades, **+8.9 bps/trade**, PF ~6 — encouraging but **not** statistically solid.
- Design split did **not** produce stable weekday picks at `min_trades=12` (too few signals).
- **Prime vs off-prime** is **mixed on design** — session filter is structurally right but not yet OOS-proven for every pair.

**Conclusion:** Timing filters reduce junk trades; they do **not** guarantee daily P&L. Certified slow premia remain **carry+momentum** (monthly), not H1 FXMOPOL.

## Promotion rule

Do not widen sessions, weekdays, or `MaxPerDay` for live use unless **validation + OOS** improve on **monthly** calendars (`period_returns.publish_results`).
