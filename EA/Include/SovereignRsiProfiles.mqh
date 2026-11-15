//+------------------------------------------------------------------+
//|                                          SovereignRsiProfiles.mqh |
//|                               Copyright 2026, Quant Systems      |
//|                                                                  |
//|  Per (symbol, timeframe) RSI band profiles.                       |
//|                                                                  |
//|  WHY THIS FILE EXISTS                                            |
//|  The EA used to auto-detect RSI band parameters by _Symbol only,  |
//|  so BTCUSD got the same mid=30 on M1, M5, M15, M30 and H1. That   |
//|  is wrong twice over:                                             |
//|                                                                  |
//|   1. The certified configs in the research are symbol AND         |
//|      timeframe specific - GBPUSD is certified on M30, USDJPY on   |
//|      M15, EURUSD on H1. The same band on another timeframe is a   |
//|      different rule with different bar-level noise.               |
//|   2. mid=30 on BTCUSD parked both entry triggers around RSI 30,   |
//|      i.e. a deep-dip rule that almost never fires. Measured on    |
//|      the broker tape (research/rsi_band_net.py, cost model,       |
//|      design/validation/OOS splits) BTCUSD H1 at mid=30 returns    |
//|      -76.85 / -26.06 / -7.43% net - it is not a config, it is a   |
//|      way to never trade. mid=50 is the shape that survives.       |
//|                                                                  |
//|  Resolution order (see SovResolveRsiProfile):                     |
//|    a. InpRsiBandOverrideProfile, if set - manual, this chart only |
//|    b. per (symbol, timeframe) table below                          |
//|    c. per symbol only (InpRsiBandPerTimeframe=false)              |
//|    d. the EURUSD-shape default band                               |
//|                                                                  |
//|  `certified` is TRUE only where the project actually certified    |
//|  that exact symbol+timeframe. Everything else is the rule being   |
//|  traded, not an edge that has been proven - and it says so in the |
//|  journal at init so nobody has to guess.                          |
//+------------------------------------------------------------------+
#property strict

#ifndef SOVEREIGN_RSI_PROFILES_MQH
#define SOVEREIGN_RSI_PROFILES_MQH

//--- one resolved profile
struct SovRsiProfile
{
   int    period;      // RSI period
   int    mid;         // RSI mid band (entry cross level)
   int    hi;          // RSI high exit band
   int    lo;          // RSI low exit band
   double deadband;    // EMA slope deadband
   double margin;      // RSI must cross the mid band by this much
   int    minHold;     // bars before the RSI-level exit may fire
   int    maxBars;     // bars held before the time exit (0=disabled)
   int    cooldown;    // bars to wait after an exit before re-entry (0=disabled)
   bool   certified;   // true only for a symbol+timeframe the research certified
   string source;      // where the numbers come from
};

//+------------------------------------------------------------------+
//| Timeframe short name (M1/M5/M15/M30/H1/H4/D1)                    |
//+------------------------------------------------------------------+
string SovTfName(const ENUM_TIMEFRAMES tf)
{
   switch(tf)
   {
      case PERIOD_M1:  return "M1";
      case PERIOD_M5:  return "M5";
      case PERIOD_M15: return "M15";
      case PERIOD_M30: return "M30";
      case PERIOD_H1:  return "H1";
      case PERIOD_H4:  return "H4";
      case PERIOD_D1:  return "D1";
      case PERIOD_W1:  return "W1";
      case PERIOD_MN1: return "MN1";
   }
   return EnumToString(tf);
}

//+------------------------------------------------------------------+
//| Symbol match (tolerates broker suffixes such as BTCUSD.a)        |
//+------------------------------------------------------------------+
bool SovSymIs(const string symbol, const string base)
{
   if(StringCompare(symbol, base, false) == 0)
      return true;
   return (StringFind(symbol, base, 0) == 0);
}

//+------------------------------------------------------------------+
//| Fill a profile                                                        |
//+------------------------------------------------------------------+
void SovSetProfile(SovRsiProfile &p, const int period, const int mid, const int hi,
                   const int lo, const double deadband, const double margin,
                   const int minHold, const int maxBars, const int cooldown,
                   const bool certified, const string source)
{
   p.period    = period;
   p.mid       = mid;
   p.hi        = hi;
   p.lo        = lo;
   p.deadband  = deadband;
   p.margin    = margin;
   p.minHold   = minHold;
   p.maxBars   = maxBars;
   p.cooldown  = cooldown;
   p.certified = certified;
   p.source    = source;
}

//+------------------------------------------------------------------+
//| EURUSD                                                           |
//|   H1 certified 9/50/75/25 - the only FX pair+TF where the rule    |
//|   passes all three splits on the broker tape (61/100 configs).    |
//+------------------------------------------------------------------+
void SovProfileEurUsd(SovRsiProfile &p, const ENUM_TIMEFRAMES tf, const bool perTf)
{
   if(!perTf)
   {
      SovSetProfile(p, 9, 50, 75, 25, 0.10, 3.0, 12, 24, 0, true,
                    "EURUSD H1 certified 9/50/75/25 (per-symbol mode: same band on every TF)");
      return;
   }

   if(tf == PERIOD_H1)
   {
      SovSetProfile(p, 9, 50, 75, 25, 0.10, 3.0, 12, 24, 0, true,
                    "EURUSD H1 certified: 61/100 configs positive in all 3 splits, net +4.83/+4.72/+2.02%");
      return;
   }
   if(tf == PERIOD_M30 || tf == PERIOD_M15)
   {
      SovSetProfile(p, 9, 50, 75, 25, 0.10, 3.0, 24, 24, 0, false,
                    "EURUSD M15/M30: tested on the broker tape and does NOT pass all 3 splits - rule traded, edge not certified");
      return;
   }
   SovSetProfile(p, 9, 50, 75, 25, 0.10, 3.0, 24, 24, 0, false,
                 "EURUSD " + SovTfName(tf) + ": no per-timeframe evidence (M1 was rejected as spread-dominated)");
}

//+------------------------------------------------------------------+
//| GBPUSD - certified on M30 with its own band 45/65/35             |
//+------------------------------------------------------------------+
void SovProfileGbpUsd(SovRsiProfile &p, const ENUM_TIMEFRAMES tf, const bool perTf)
{
   if(!perTf)
   {
      SovSetProfile(p, 9, 45, 65, 35, 0.20, 3.0, 24, 24, 0, true,
                    "GBPUSD M30 certified 9/45/65/35 (per-symbol mode: same band on every TF)");
      return;
   }

   if(tf == PERIOD_M30)
   {
      SovSetProfile(p, 9, 45, 65, 35, 0.20, 3.0, 24, 24, 0, true,
                    "GBPUSD M30 certified: net +3.37/+1.81/+0.05% (all 3 splits)");
      return;
   }
   if(tf == PERIOD_H1)
   {
      SovSetProfile(p, 9, 45, 65, 35, 0.20, 3.0, 12, 24, 0, false,
                    "GBPUSD H1: +3.13% OOS on broker tape, but FAILS the HistData 2005-2025 tape - not certified");
      return;
   }
   SovSetProfile(p, 9, 45, 65, 35, 0.20, 3.0, 24, 24, 0, false,
                 "GBPUSD " + SovTfName(tf) + ": no per-timeframe evidence - rule traded, edge not certified");
}

//+------------------------------------------------------------------+
//| USDJPY - certified on M15 with its own band 55/75/25             |
//+------------------------------------------------------------------+
void SovProfileUsdJpy(SovRsiProfile &p, const ENUM_TIMEFRAMES tf, const bool perTf)
{
   if(!perTf)
   {
      SovSetProfile(p, 14, 55, 75, 25, 0.10, 3.0, 24, 24, 0, true,
                    "USDJPY M15 certified 14/55/75/25 (per-symbol mode: same band on every TF)");
      return;
   }

   if(tf == PERIOD_M15)
   {
      SovSetProfile(p, 14, 55, 75, 25, 0.10, 3.0, 24, 24, 0, true,
                    "USDJPY M15 certified: net +13.62/+10.27/+2.46% (all 3 splits)");
      return;
   }
   if(tf == PERIOD_H1)
   {
      SovSetProfile(p, 14, 55, 75, 25, 0.10, 3.0, 12, 24, 0, false,
                    "USDJPY H1: RSI band not certified on H1 (the certified H1 overlay is SOV_STRUCT_RIDE)");
      return;
   }
   SovSetProfile(p, 14, 55, 75, 25, 0.10, 3.0, 24, 24, 0, false,
                 "USDJPY " + SovTfName(tf) + ": no per-timeframe evidence - rule traded, edge not certified");
}

//+------------------------------------------------------------------+
//| BTCUSD - swept on broker tape, per timeframe                      |
//|                                                                  |
//|   Measured with research/rsi_band_net.py on research/data/mt5:    |
//|     H1  2015-2026   mid 50 : 1/100 configs positive in all 3      |
//|                              (cooldown 40, min-hold 24, margin 0) |
//|                              design +5.28 / valid +53.05 / OOS +6.66
//|     M30 2021-2026   mid 50 : 0/100                               |
//|     M15 2024-2026   mid 50 : 0/100                               |
//|     M5  2025-2026   mid 50 : 0/100                               |
//|   mid 30 (the old guess) on H1: -76.85 / -26.06 / -7.43% net.     |
//|                                                                  |
//|   So: mid=50 everywhere (that is the shape that fires at all),    |
//|   the H1 cooldown only where it was measured, and certified=false |
//|   on every timeframe - one knife-edge config is not a plateau.    |
//+------------------------------------------------------------------+
void SovProfileBtcUsd(SovRsiProfile &p, const ENUM_TIMEFRAMES tf, const bool perTf)
{
   if(perTf && tf == PERIOD_H1)
   {
      SovSetProfile(p, 9, 50, 75, 25, 0.10, 0.0, 24, 24, 40, false,
                    "BTCUSD H1: 1/100 configs positive in all 3 splits (knife-edge, cooldown 40/min-hold 24/margin 0)");
      return;
   }
   if(perTf)
   {
      SovSetProfile(p, 9, 50, 75, 25, 0.10, 0.0, 24, 24, 0, false,
                    "BTCUSD " + SovTfName(tf) + ": 0/100 configs positive in all 3 splits - rule traded, edge NOT certified");
      return;
   }
   SovSetProfile(p, 9, 50, 75, 25, 0.10, 0.0, 24, 24, 0, false,
                 "BTCUSD mid=50 (per-symbol mode: same band on every TF) - uncertified");
}

//+------------------------------------------------------------------+
//| Default band - the EURUSD H1 shape, for symbols with no research  |
//+------------------------------------------------------------------+
void SovProfileDefault(SovRsiProfile &p, const string symbol, const ENUM_TIMEFRAMES tf)
{
   SovSetProfile(p, 9, 50, 75, 25, 0.10, 3.0, 24, 24, 0, false,
                 "default EURUSD-shape band: no research for " + symbol + " " + SovTfName(tf));
}

//+------------------------------------------------------------------+
//| Resolve the profile for a symbol AND timeframe                    |
//+------------------------------------------------------------------+
SovRsiProfile SovResolveRsiProfile(const string symbol, const ENUM_TIMEFRAMES tf,
                                   const bool perTimeframe)
{
   SovRsiProfile p;
   SovProfileDefault(p, symbol, tf);

   if(SovSymIs(symbol, "EURUSD"))
      SovProfileEurUsd(p, tf, perTimeframe);
   else if(SovSymIs(symbol, "GBPUSD"))
      SovProfileGbpUsd(p, tf, perTimeframe);
   else if(SovSymIs(symbol, "USDJPY"))
      SovProfileUsdJpy(p, tf, perTimeframe);
   else if(SovSymIs(symbol, "BTCUSD"))
      SovProfileBtcUsd(p, tf, perTimeframe);

   return p;
}

//+------------------------------------------------------------------+
//| Manual override for THIS chart:                                   |
//|   "period,mid,hi,lo,deadband,margin,minHold,maxBars[,cooldown]"    |
//| Returns false when the string is empty or malformed.               |
//+------------------------------------------------------------------+
bool SovParseRsiOverride(const string spec, SovRsiProfile &p)
{
   string s = spec;
   StringTrimLeft(s);
   StringTrimRight(s);
   if(StringLen(s) == 0)
      return false;

   string parts[];
   int n = StringSplit(s, ',', parts);
   if(n < 8)
      return false;

   for(int i = 0; i < 8; i++)
   {
      StringTrimLeft(parts[i]);
      StringTrimRight(parts[i]);
   }

   int    period  = (int)StringToInteger(parts[0]);
   int    mid     = (int)StringToInteger(parts[1]);
   int    hi      = (int)StringToInteger(parts[2]);
   int    lo      = (int)StringToInteger(parts[3]);
   double db      = StringToDouble(parts[4]);
   double margin  = StringToDouble(parts[5]);
   int    minHold = (int)StringToInteger(parts[6]);
   int    maxBars = (int)StringToInteger(parts[7]);
   int    cooldown = 0;
   if(n >= 9)
   {
      StringTrimLeft(parts[8]);
      StringTrimRight(parts[8]);
      cooldown = (int)StringToInteger(parts[8]);
   }

   //--- sanity: a band that cannot be right is a typo, not a strategy
   if(period < 2 || period > 100 || mid <= 0 || mid >= 100 ||
      hi <= 0 || hi > 100 || lo < 0 || lo >= 100 || hi <= lo)
      return false;

   SovSetProfile(p, period, mid, hi, lo, db, margin,
                 MathMax(1, minHold), MathMax(0, maxBars), MathMax(0, cooldown),
                 false, "MANUAL override from input: " + s);
   return true;
}

#endif // SOVEREIGN_RSI_PROFILES_MQH
//+------------------------------------------------------------------+
