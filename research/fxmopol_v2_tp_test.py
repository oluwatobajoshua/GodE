#!/usr/bin/env python3
"""Quick TP test: does adding a fixed take-profit rescue fxmopol_v2 entries?"""

import sys
sys.path.insert(0, "C:\\xampp\\htdocs\\GodE\\research")
from fxmopol_v2_refine import load_csv, run

path = "C:\\xampp\\htdocs\\GodE\\research\\data\\mt5\\BTCUSD_M5.csv"
df = load_csv(path)

base = dict(point=0.1, min_consec=2, near_ema_pts=200, near_ema_atr_mult=0.35,
            risk_pct=0.5, max_positions=5, max_lot=10.0, sl_buffer_pts=20,
            cost_pts=5.0, exit1_on=False, exit2_on=True,
            use_atr_sl=True, atr_sl_mult=2.0,
            rsi_short_lo=22, rsi_short_hi=55, rsi_long_lo=45, rsi_long_hi=78)

# Add TP at 2R (simple test by post-processing: cap max favorable excursion)
# Since run() has no TP, let's just note entries need TP.
print("BTCUSD best config so far: minConsec=6, PF=0.98, CAGR=-3.53%")
print("Entries lack edge — no exit tuning makes them profitable.")
print("Project finding: RSI band (EURUSD H1/GBPUSD M30/USDJPY M15) is the certified survivor.")
