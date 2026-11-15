//+------------------------------------------------------------------+
//|                                     SovereignFxmopol.mqh         |
//| FXMOPOL — criminal candle chase + BabyPips MTF / S/R / fib / pivot |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_FXMOPOL_MQH__
#define __SOVEREIGN_FXMOPOL_MQH__

#include <Trade/PositionInfo.mqh>
#include <Sovereign/SovereignPerformance.mqh>
#include <Sovereign/SovereignTrafficLight.mqh>

class CSovereignFxmopol
{
private:
   long     m_magic;
   int      m_emaPeriod;
   int      m_h4EmaPeriod;
   int      m_swingBars;
   int      m_maxPerDay;
   double   m_slAtr;
   double   m_tpAtr;
   int      m_maxSpread;
   double   m_riskPct;
   bool     m_breakout;
   bool     m_goodSessionsOnly;
   int      m_weekdayMask;
   bool     m_pinOnly;
   bool     m_visualSetup;
   int      m_slowEmaPeriod;
   int      m_smmaPeriod;
   int      m_rsiPeriod;
   bool     m_useFractals;
   double   m_manualLevel;
   bool     m_streakRide;
   bool     m_playbook;
   bool     m_regimeEmaOnly;
   int      m_minStreak;
   bool     m_useProxFilters;
   bool     m_strictStayAway;
   bool     m_useRegimeFilter;
   int      m_maxLossesPerDay;
   int      m_dayLossCount;
   double   m_maxEntryEma21Atr;
   double   m_minFanSpreadAtr;
   double   m_exitExtEma21Atr;
   int      m_minBarsExtExit;
   double   m_minProfitAtrExt;
   datetime m_lastBar;
   int      m_dayKey;
   int      m_dayCount;
   bool     m_showMarketPulse;
   bool     m_playbookSignalExit;
   bool     m_useD1Gate;
   bool     m_useFullPriceAction;
   bool     m_useLevelTp;
   int      m_stackRegimeBars;
   double   m_rsiCoverLevel;
   double   m_ecoRiskMult;
   bool     m_ecoBlockLong;
   bool     m_ecoBlockShort;
   string   m_ecoProfessorLine;

   //--- read-only pulse cache, consumed by the chart renderer only.
   //    Storing these changes no decision; it lets the panel show the
   //    exact numbers this tick's analysis produced.
   int      m_pulseReg;
   int      m_pulseCtxL;
   int      m_pulseCtxS;
   double   m_pulseRsi;
   double   m_pulseAtr;
   string   m_pulseAction;
   string   m_pulseQ1;
   string   m_pulseQ2;
   string   m_pulseIssues;
   string   m_pulseD1;
   string   m_pulseProfessor;
   int      m_pulseTlL;
   int      m_pulseTlS;
   bool     m_pulseValid;

   int DayKey(datetime t) const
   {
      MqlDateTime dt;
      TimeToStruct(t, dt);
      return dt.year * 10000 + dt.mon * 100 + dt.day;
   }

   bool SessionAllowsNewEntry(const int hour) const
   {
      if(!m_goodSessionsOnly)
         return true;
      return (hour >= 7 && hour <= 11) || (hour >= 13 && hour <= 16);
   }

   bool WeekdayAllowsNewEntry(const int dayOfWeek) const
   {
      if(m_weekdayMask == 0)
         return true;
      if(dayOfWeek < 1 || dayOfWeek > 5)
         return false;
      const int bit = 1 << (dayOfWeek - 1);
      return (m_weekdayMask & bit) != 0;
   }

   bool NewBar(const string sym, const ENUM_TIMEFRAMES tf)
   {
      datetime t = iTime(sym, tf, 0);
      if(t == 0 || t == m_lastBar)
         return false;
      m_lastBar = t;
      int dk = DayKey(t);
      if(dk != m_dayKey)
      {
         m_dayKey = dk;
         m_dayCount = 0;
         m_dayLossCount = 0;
      }
      return true;
   }

   bool HasOpenPosition(const string sym) const
   {
      CPositionInfo p;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!p.SelectByIndex(i))
            continue;
         if(p.Magic() == m_magic && p.Symbol() == sym)
            return true;
      }
      return false;
   }

   bool Criminal(const double o, const double h, const double l, const double c, const int side) const
   {
      double body = MathAbs(c - o);
      double rng = h - l;
      if(rng <= 0 || body / rng < 0.55)
         return false;
      if(side > 0 && (c <= o || (h - c) / rng > 0.25))
         return false;
      if(side < 0 && (c >= o || (c - l) / rng > 0.25))
         return false;
      return true;
   }

   bool Engulf(const double o, const double c, const double po, const double pc, const int side) const
   {
      if(side > 0)
         return (c > o && pc < po && c > po && o < pc);
      return (c < o && pc > po && c < po && o > pc);
   }

   bool Pin(const double o, const double h, const double l, const double c, const int side) const
   {
      double body = MathAbs(c - o);
      double rng = h - l;
      if(rng <= 0 || body <= 0)
         return false;
      double lower = MathMin(o, c) - l;
      double upper = h - MathMax(o, c);
      if(side > 0)
         return (lower >= 2.0 * body && upper <= body);
      return (upper >= 2.0 * body && lower <= body);
   }

   bool D1StackAligns(const string sym, const int side) const
   {
      if(!m_useD1Gate || !m_playbook)
         return true;
      double c[];
      ArraySetAsSeries(c, true);
      if(CopyClose(sym, PERIOD_D1, 1, 1, c) < 1)
         return false;
      if(!FullStackBarFromSeries(sym, PERIOD_D1, c[0], side))
         return false;
      int hMa = iMA(sym, PERIOD_D1, m_h4EmaPeriod, 0, MODE_SMA, PRICE_CLOSE);
      if(hMa == INVALID_HANDLE)
         return true;
      double ma[];
      ArraySetAsSeries(ma, true);
      bool ok = (CopyBuffer(hMa, 0, 1, 1, ma) >= 1);
      IndicatorRelease(hMa);
      if(!ok)
         return true;
      if(side > 0)
         return (c[0] > ma[0]);
      return (c[0] < ma[0]);
   }

   bool H4TrendOk(const string sym, const int side) const
   {
      int hMa = iMA(sym, PERIOD_H4, m_h4EmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      if(hMa == INVALID_HANDLE)
         return false;
      double ema[], close[];
      ArraySetAsSeries(ema, true);
      ArraySetAsSeries(close, true);
      bool ok = (CopyBuffer(hMa, 0, 1, 2, ema) >= 2 && CopyClose(sym, PERIOD_H4, 1, 2, close) >= 2);
      IndicatorRelease(hMa);
      if(!ok)
         return false;
      if(side > 0)
         return (close[0] > ema[0]);
      return (close[0] < ema[0]);
   }

   bool DownFractal(const double &lo[], const int i) const
   {
      return (lo[i] < lo[i + 1] && lo[i] < lo[i + 2] && lo[i] < lo[i - 1] && lo[i] < lo[i - 2]);
   }

   bool UpFractal(const double &hi[], const int i) const
   {
      return (hi[i] > hi[i + 1] && hi[i] > hi[i + 2] && hi[i] > hi[i - 1] && hi[i] > hi[i - 2]);
   }

   bool NearFractal(const double &h[], const double &l[], const int side, const int lookback = 8) const
   {
      for(int k = 2; k <= lookback + 1; k++)
      {
         if(k + 2 >= ArraySize(l))
            break;
         if(side > 0 && DownFractal(l, k) && l[0] <= l[k] * 1.002)
            return true;
         if(side < 0 && UpFractal(h, k) && h[0] >= h[k] * 0.998)
            return true;
      }
      return false;
   }

   bool VisualTrendStack(const string sym, const ENUM_TIMEFRAMES tf, const int side,
                         const double c0, const double c1) const
   {
      int hFast = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSlow = iMA(sym, tf, m_slowEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSmma = iMA(sym, tf, m_smmaPeriod, 0, MODE_SMMA, PRICE_CLOSE);
      if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE || hSmma == INVALID_HANDLE)
      {
         if(hFast != INVALID_HANDLE) IndicatorRelease(hFast);
         if(hSlow != INVALID_HANDLE) IndicatorRelease(hSlow);
         if(hSmma != INVALID_HANDLE) IndicatorRelease(hSmma);
         return false;
      }
      double e21[], e100[], s100[];
      ArraySetAsSeries(e21, true);
      ArraySetAsSeries(e100, true);
      ArraySetAsSeries(s100, true);
      bool ok = (CopyBuffer(hFast, 0, 1, 3, e21) >= 3
                 && CopyBuffer(hSlow, 0, 1, 2, e100) >= 2
                 && CopyBuffer(hSmma, 0, 1, 2, s100) >= 2);
      IndicatorRelease(hFast);
      IndicatorRelease(hSlow);
      IndicatorRelease(hSmma);
      if(!ok)
         return false;
      if(side > 0)
         return (c0 > e21[0] && e21[0] > e100[0] && c0 > s100[0] && e21[0] > e21[2]);
      return (c0 < e21[0] && e21[0] < e100[0] && c0 < s100[0] && e21[0] < e21[2]);
   }

   int CountStreak(const string sym, const ENUM_TIMEFRAMES tf, const bool wantLong) const
   {
      int need = MathMax(m_minStreak + 2, 30);
      double c[];
      ArraySetAsSeries(c, true);
      if(CopyClose(sym, tf, 1, need, c) < need)
         return 0;
      int hMa = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSlow = iMA(sym, tf, m_slowEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSmma = iMA(sym, tf, m_smmaPeriod, 0, MODE_SMMA, PRICE_CLOSE);
      if(hMa == INVALID_HANDLE)
         return 0;
      double e21[], e100[], s100[];
      ArraySetAsSeries(e21, true);
      ArraySetAsSeries(e100, true);
      ArraySetAsSeries(s100, true);
      if(CopyBuffer(hMa, 0, 1, need, e21) < need)
      {
         IndicatorRelease(hMa);
         return 0;
      }
      bool haveStack = (hSlow != INVALID_HANDLE && hSmma != INVALID_HANDLE
                        && CopyBuffer(hSlow, 0, 1, need, e100) >= need
                        && CopyBuffer(hSmma, 0, 1, need, s100) >= need);
      IndicatorRelease(hMa);
      if(hSlow != INVALID_HANDLE) IndicatorRelease(hSlow);
      if(hSmma != INVALID_HANDLE) IndicatorRelease(hSmma);
      int streak = 0;
      for(int i = 0; i < need; i++)
      {
         bool barLong = (c[i] > e21[i]);
         if(haveStack && !m_regimeEmaOnly)
            barLong = barLong && (c[i] > e100[i]) && (e21[i] > e100[i]) && (c[i] > s100[i]);
         bool barShort = (c[i] < e21[i]);
         if(haveStack && !m_regimeEmaOnly)
            barShort = barShort && (c[i] < e100[i]) && (e21[i] < e100[i]) && (c[i] < s100[i]);
         if(wantLong && barLong)
            streak++;
         else if(!wantLong && barShort)
            streak++;
         else
            break;
      }
      return streak;
   }

   bool WickHuntReclaim(const double o, const double h, const double l, const double c, const int side) const
   {
      double body = MathAbs(c - o);
      double rng = h - l;
      if(rng <= 0 || body <= 0)
         return false;
      double lower = MathMin(o, c) - l;
      double upper = h - MathMax(o, c);
      if(side > 0)
         return (lower >= 2.0 * body && c > o);
      return (upper >= 2.0 * body && c < o);
   }

   bool WickHuntReclaimStack(const string sym, const ENUM_TIMEFRAMES tf,
                             const double o, const double h, const double l, const double c,
                             const int side) const
   {
      if(!WickHuntReclaim(o, h, l, c, side))
         return false;
      int hFast = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSmma = iMA(sym, tf, m_smmaPeriod, 0, MODE_SMMA, PRICE_CLOSE);
      if(hFast == INVALID_HANDLE || hSmma == INVALID_HANDLE)
      {
         if(hFast != INVALID_HANDLE) IndicatorRelease(hFast);
         if(hSmma != INVALID_HANDLE) IndicatorRelease(hSmma);
         return true;
      }
      double e21[], s100[];
      ArraySetAsSeries(e21, true);
      ArraySetAsSeries(s100, true);
      bool ok = (CopyBuffer(hFast, 0, 1, 1, e21) >= 1 && CopyBuffer(hSmma, 0, 1, 1, s100) >= 1);
      IndicatorRelease(hFast);
      IndicatorRelease(hSmma);
      if(!ok)
         return true;
      if(side > 0)
         return (c > e21[0] && c > s100[0]);
      return (c < e21[0] && c < s100[0]);
   }

   bool FractalLowAt(const double &l[], const int shift = 2) const
   {
      if(ArraySize(l) < shift + 3)
         return false;
      double v = l[shift];
      return (v < l[shift + 1] && v < l[shift + 2] && v < l[shift - 1] && v < l[shift - 2]);
   }

   bool FractalHighAt(const double &h[], const int shift = 2) const
   {
      if(ArraySize(h) < shift + 3)
         return false;
      double v = h[shift];
      return (v > h[shift + 1] && v > h[shift + 2] && v > h[shift - 1] && v > h[shift - 2]);
   }

   bool PlaybookChartEntry(const string sym, const ENUM_TIMEFRAMES tf, const int side,
                           const double o0, const double h0, const double l0, const double c0,
                           const double atr0, const double &h[], const double &l[]) const
   {
      int rb = PlaybookRegimeBars();
      if(!StackRegimeOk(sym, tf, side, rb))
         return false;
      int hMa = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      double e21[];
      ArraySetAsSeries(e21, true);
      if(hMa == INVALID_HANDLE || CopyBuffer(hMa, 0, 1, 1, e21) < 1)
      {
         if(hMa != INVALID_HANDLE) IndicatorRelease(hMa);
         return false;
      }
      IndicatorRelease(hMa);
      if(side > 0)
      {
         bool pull = PlaybookPullbackLong(l0, c0, e21[0], atr0);
         bool hunt = WickHuntReclaimStack(sym, tf, o0, h0, l0, c0, 1);
         if(m_useFractals)
            hunt = hunt && FractalLowAt(l, 2);
         return pull || hunt;
      }
      bool ret = PlaybookRetestShort(h0, c0, e21[0], atr0);
      bool hunt = WickHuntReclaimStack(sym, tf, o0, h0, l0, c0, -1);
      if(m_useFractals)
         hunt = hunt && FractalHighAt(h, 2);
      return ret || hunt;
   }

   bool LevelTpHit(const string sym, const int side, const double atr) const
   {
      if(!m_useLevelTp || m_manualLevel <= 0.0 || atr <= 0)
         return false;
      double tol = MathMax(atr * 0.15, SymbolInfoDouble(sym, SYMBOL_POINT) * 5);
      if(side > 0)
      {
         double hi[];
         ArraySetAsSeries(hi, true);
         if(CopyHigh(sym, PERIOD_CURRENT, 1, 1, hi) < 1)
            return false;
         return (hi[0] >= m_manualLevel - tol);
      }
      double lo[];
      ArraySetAsSeries(lo, true);
      if(CopyLow(sym, PERIOD_CURRENT, 1, 1, lo) < 1)
         return false;
      return (lo[0] <= m_manualLevel + tol);
   }

   bool PlaybookPullbackLong(const double l0, const double c0, const double e21, const double atr) const
   {
      if(e21 <= 0 || atr <= 0)
         return false;
      return (l0 <= e21 + atr * 0.25 && c0 > e21);
   }

   bool PlaybookRetestShort(const double h0, const double c0, const double e21, const double atr) const
   {
      if(e21 <= 0 || atr <= 0)
         return false;
      return (h0 >= e21 - atr * 0.25 && c0 < e21);
   }

   bool Ema21CloseCrossLong(const double c0, const double c1, const double e0, const double e1) const
   {
      return (c0 > e0 && c1 <= e1);
   }

   bool Ema21CloseCrossShort(const double c0, const double c1, const double e0, const double e1) const
   {
      return (c0 < e0 && c1 >= e1);
   }

   int Ema21CloseFlips(const string sym, const ENUM_TIMEFRAMES tf, const int lookback) const
   {
      int need = lookback + 2;
      double c[];
      ArraySetAsSeries(c, true);
      if(CopyClose(sym, tf, 1, need, c) < need)
         return 99;
      int hFast = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      if(hFast == INVALID_HANDLE)
         return 99;
      double e21[];
      ArraySetAsSeries(e21, true);
      if(CopyBuffer(hFast, 0, 1, need, e21) < need)
      {
         IndicatorRelease(hFast);
         return 99;
      }
      IndicatorRelease(hFast);
      int flips = 0;
      for(int j = 0; j < lookback; j++)
      {
         bool aboveJ = (c[j] > e21[j]);
         bool abovePrev = (c[j + 1] > e21[j + 1]);
         if(aboveJ != abovePrev)
            flips++;
      }
      return flips;
   }

   bool Ema21WhipsawChop(const string sym, const ENUM_TIMEFRAMES tf) const
   {
      return (Ema21CloseFlips(sym, tf, 4) >= 2);
   }

   // +1 uptrend, -1 downtrend, 0 sideway (wide-way range / chop — stay flat).
   int MarketRegime(const string sym, const ENUM_TIMEFRAMES tf) const
   {
      if(Ema21WhipsawChop(sym, tf))
         return 0;
      int atrH = iATR(sym, tf, 14);
      double atr[];
      ArraySetAsSeries(atr, true);
      if(atrH == INVALID_HANDLE || CopyBuffer(atrH, 0, 1, 1, atr) < 1 || atr[0] <= 0)
      {
         if(atrH != INVALID_HANDLE)
            IndicatorRelease(atrH);
         return 0;
      }
      IndicatorRelease(atrH);
      double c[];
      ArraySetAsSeries(c, true);
      if(CopyClose(sym, tf, 1, 3, c) < 3)
         return 0;
      int hFast = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSlow = iMA(sym, tf, m_slowEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSmma = iMA(sym, tf, m_smmaPeriod, 0, MODE_SMMA, PRICE_CLOSE);
      double e21[], eMid[], s100[];
      ArraySetAsSeries(e21, true);
      ArraySetAsSeries(eMid, true);
      ArraySetAsSeries(s100, true);
      if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE || hSmma == INVALID_HANDLE)
      {
         if(hFast != INVALID_HANDLE) IndicatorRelease(hFast);
         if(hSlow != INVALID_HANDLE) IndicatorRelease(hSlow);
         if(hSmma != INVALID_HANDLE) IndicatorRelease(hSmma);
         return 0;
      }
      bool ok = (CopyBuffer(hFast, 0, 1, 3, e21) >= 3
                 && CopyBuffer(hSlow, 0, 1, 2, eMid) >= 2
                 && CopyBuffer(hSmma, 0, 1, 1, s100) >= 1);
      IndicatorRelease(hFast);
      IndicatorRelease(hSlow);
      IndicatorRelease(hSmma);
      if(!ok)
         return 0;
      if(ChopNearSmma(c[0], s100[0], atr[0])
         && !FullStackBar(c[0], e21[0], eMid[0], s100[0], 1)
         && !FullStackBar(c[0], e21[0], eMid[0], s100[0], -1))
         return 0;
      if(!EmaFanOk(e21[0], eMid[0], s100[0], atr[0], 1) && !EmaFanOk(e21[0], eMid[0], s100[0], atr[0], -1))
         return 0;
      double slope = (e21[0] - e21[2]) / atr[0];
      if(e21[0] > eMid[0] && c[0] > e21[0] && slope > 0.01)
         return 1;
      if(e21[0] < eMid[0] && c[0] < e21[0] && slope < -0.01)
         return -1;
      return 0;
   }

   // 0 CHOPPY, 1 SIDEWAY, 2 BAD/weak trend, 3 GOOD trend (press it).
   int MarketContext(const string sym, const ENUM_TIMEFRAMES tf, const int side) const
   {
      if(side == 0)
         return 1;
      int flips = Ema21CloseFlips(sym, tf, 4);
      if(flips >= 3)
         return 0;
      int reg = MarketRegime(sym, tf);
      if(reg == 0)
         return (flips >= 2 ? 0 : 1);
      if(reg != side)
         return 0;
      int atrH = iATR(sym, tf, 14);
      double atr[];
      ArraySetAsSeries(atr, true);
      if(atrH == INVALID_HANDLE || CopyBuffer(atrH, 0, 1, 1, atr) < 1 || atr[0] <= 0)
      {
         if(atrH != INVALID_HANDLE)
            IndicatorRelease(atrH);
         return 2;
      }
      IndicatorRelease(atrH);
      double c[];
      ArraySetAsSeries(c, true);
      if(CopyClose(sym, tf, 1, 1, c) < 1)
         return 2;
      if(!RsiVisualOk(sym, tf, side))
         return 2;
      int hFast = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSlow = iMA(sym, tf, m_slowEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSmma = iMA(sym, tf, m_smmaPeriod, 0, MODE_SMMA, PRICE_CLOSE);
      double e21[], eMid[], s100[];
      ArraySetAsSeries(e21, true);
      ArraySetAsSeries(eMid, true);
      ArraySetAsSeries(s100, true);
      if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE || hSmma == INVALID_HANDLE)
      {
         if(hFast != INVALID_HANDLE) IndicatorRelease(hFast);
         if(hSlow != INVALID_HANDLE) IndicatorRelease(hSlow);
         if(hSmma != INVALID_HANDLE) IndicatorRelease(hSmma);
         return 2;
      }
      bool ok = (CopyBuffer(hFast, 0, 1, 1, e21) >= 1
                 && CopyBuffer(hSlow, 0, 1, 1, eMid) >= 1
                 && CopyBuffer(hSmma, 0, 1, 1, s100) >= 1);
      IndicatorRelease(hFast);
      IndicatorRelease(hSlow);
      IndicatorRelease(hSmma);
      if(!ok)
         return 2;
      bool stacked = FullStackBar(c[0], e21[0], eMid[0], s100[0], side);
      bool chop = ChopNearSmma(c[0], s100[0], atr[0]) && !stacked;
      bool fan = EmaFanOk(e21[0], eMid[0], s100[0], atr[0], side);
      if(fan && !chop && (stacked || RsiGradeOkFromSymbol(sym, tf, side)))
         return 3;
      return 2;
   }

   string MarketDoingNow(const int reg, const double rsi, const int ctxL, const int ctxS) const
   {
      if(reg == 0)
         return "Chop/sideway — MAs tangled; no clean trend";
      if(reg > 0)
         return StringFormat("Uptrend press | RSI %.1f | long readiness %d/3", rsi, ctxL);
      return StringFormat("Downtrend press | RSI %.1f | short readiness %d/3", rsi, ctxS);
   }

   string QuickInOutPlan(const string action, const int openSide, const double atrV) const
   {
      string stopHint = (atrV > 0 ? StringFormat("stop ~%.5f (1xATR)", atrV) : "stop 1xATR");
      if(openSide > 0)
         return "OUT: close below EMA21, RSI>=80, extension exit; avoid add unless ctx=3";
      if(openSide < 0)
         return "OUT: close above EMA21, RSI<=20, extension exit; avoid add unless ctx=3";
      if(StringFind(action, "ENTER_LONG") >= 0)
         return StringFormat("IN: buy next bar if TrafficLight OK | %s | OUT: EMA21/RSI80", stopHint);
      if(StringFind(action, "ENTER_SHORT") >= 0)
         return StringFormat("IN: sell next bar if TrafficLight OK | %s | OUT: EMA21/RSI20", stopHint);
      if(StringFind(action, "WATCH_LONG") >= 0)
         return "IN: pullback to EMA21 or hunt reclaim | OUT: EMA21 break";
      if(StringFind(action, "WATCH_SHORT") >= 0)
         return "IN: EMA21 retest reject | OUT: EMA21 break";
      if(StringFind(action, "WAIT") >= 0 || StringFind(action, "CHOP") >= 0)
         return "IN: none (issues: chop/no trigger) | OUT: n/a";
      return "IN: wait playbook trigger | OUT: EMA21 + RSI extremes";
   }

   string PulseActionText(const int ctxLong, const int ctxShort, const int reg, const int openSide) const
   {
      if(openSide > 0)
      {
         if(ctxLong == 0)
            return "EXIT_OR_FLAT (chop vs long)";
         if(ctxLong >= 3)
            return "HOLD_LONG";
         return "TIGHTEN_LONG";
      }
      if(openSide < 0)
      {
         if(ctxShort == 0)
            return "EXIT_OR_FLAT (chop vs short)";
         if(ctxShort >= 3)
            return "HOLD_SHORT";
         return "TIGHTEN_SHORT";
      }
      if(reg == 0)
         return "WAIT_FLAT (chop/sideway)";
      if(ctxLong >= 3 && ctxShort < 3)
         return "ENTER_LONG_NOW";
      if(ctxShort >= 3 && ctxLong < 3)
         return "ENTER_SHORT_NOW";
      if(reg > 0)
         return (ctxLong >= 2 ? "WATCH_LONG" : "WAIT_LONG");
      if(reg < 0)
         return (ctxShort >= 2 ? "WATCH_SHORT" : "WAIT_SHORT");
      return "WAIT_FLAT";
   }

   void PublishMarketPulse(const string sym, const ENUM_TIMEFRAMES tf,
                           CSovereignTrafficLight &tl, const int openSide)
   {
      int reg = MarketRegime(sym, tf);
      int ctxL = MarketContext(sym, tf, 1);
      int ctxS = MarketContext(sym, tf, -1);
      string action = PulseActionText(ctxL, ctxS, reg, openSide);
      int hRsi = iRSI(sym, tf, m_rsiPeriod, PRICE_CLOSE);
      double rsiV = 0.0;
      if(hRsi != INVALID_HANDLE)
      {
         double rsi[];
         ArraySetAsSeries(rsi, true);
         if(CopyBuffer(hRsi, 0, 1, 1, rsi) >= 1)
            rsiV = rsi[0];
         IndicatorRelease(hRsi);
      }
      int atrH = iATR(sym, tf, 14);
      double atrV = 0.0;
      if(atrH != INVALID_HANDLE)
      {
         double atr[];
         ArraySetAsSeries(atr, true);
         if(CopyBuffer(atrH, 0, 1, 1, atr) >= 1)
            atrV = atr[0];
         IndicatorRelease(atrH);
      }
      MqlDateTime barDt;
      TimeToStruct(iTime(sym, tf, 1), barDt);
      int spread = (int)SymbolInfoInteger(sym, SYMBOL_SPREAD);
      TrafficLightResult tlLong = tl.Evaluate(1, 2, atrV, 0.5, 0, true, spread, barDt.hour, true, true);
      TrafficLightResult tlShort = tl.Evaluate(-1, 2, atrV, 0.5, 0, true, spread, barDt.hour, true, true);
      string q1 = MarketDoingNow(reg, rsiV, ctxL, ctxS);
      string q2 = QuickInOutPlan(action, openSide, atrV);
      string issues = "none";
      if(spread > m_maxSpread)
         issues = "spread too wide";
      else if(StringFind(action, "ENTER_LONG") >= 0 && !tlLong.allowed)
         issues = "TrafficLight blocks long";
      else if(StringFind(action, "ENTER_SHORT") >= 0 && !tlShort.allowed)
         issues = "TrafficLight blocks short";
      else if(reg == 0 && openSide == 0)
         issues = "chop — skip entries";
      if(m_maxPerDay <= 0 && openSide == 0 && StringFind(action, "ENTER") >= 0)
         issues = issues + (issues == "none" ? "" : "; ") + "auto OFF — manual only";
      string d1Tag = "D1 off";
      if(m_useD1Gate && m_playbook)
      {
         if(D1StackAligns(sym, 1))
            d1Tag = "D1 long OK";
         else if(D1StackAligns(sym, -1))
            d1Tag = "D1 short OK";
         else
            d1Tag = "D1 flat — wait";
      }
      string prof = m_ecoProfessorLine;
      if(prof == "")
         prof = "Professor: (no snapshot)";
      string block = StringFormat(
         "FXMOPOL %s H1\nQ1 NOW: %s\nQ2 IN/OUT: %s\n%s\nMove: %s | TL %dG/%dG | Issues: %s\n%s",
         sym, q1, q2, d1Tag, action, tlLong.green, tlShort.green, issues, prof);
      Comment(block);
      Print("PULSE Q1=", q1, " | Q2=", q2, " | ", action, " | issues=", issues);

      //--- cache for the chart panel (read-only mirror of the above)
      m_pulseReg       = reg;
      m_pulseCtxL      = ctxL;
      m_pulseCtxS      = ctxS;
      m_pulseRsi       = rsiV;
      m_pulseAtr       = atrV;
      m_pulseAction    = action;
      m_pulseQ1        = q1;
      m_pulseQ2        = q2;
      m_pulseIssues    = issues;
      m_pulseD1        = d1Tag;
      m_pulseProfessor = prof;
      m_pulseTlL       = tlLong.green;
      m_pulseTlS       = tlShort.green;
      m_pulseValid     = true;
   }

   bool RsiGradeOkFromSymbol(const string sym, const ENUM_TIMEFRAMES tf, const int side) const
   {
      int hRsi = iRSI(sym, tf, m_rsiPeriod, PRICE_CLOSE);
      if(hRsi == INVALID_HANDLE)
         return false;
      double rsi[];
      ArraySetAsSeries(rsi, true);
      bool ok = (CopyBuffer(hRsi, 0, 1, 1, rsi) >= 1);
      IndicatorRelease(hRsi);
      if(!ok)
         return false;
      return RsiGradeOk(rsi[0], side);
   }

   bool Ema21OneBarFakeoutLong(const double c0, const double c1, const double c2,
                               const double e0, const double e1, const double e2) const
   {
      return (c2 <= e2 && c1 > e1 && c0 > e0);
   }

   bool Ema21OneBarFakeoutShort(const double c0, const double c1, const double c2,
                                const double e0, const double e1, const double e2) const
   {
      return (c2 >= e2 && c1 < e1 && c0 < e0);
   }

   bool Ema21CrossBiasLong(const string sym, const ENUM_TIMEFRAMES tf, const double c0) const
   {
      if(FullStackBarFromSeries(sym, tf, c0, 1))
         return true;
      int hFast = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSlow = iMA(sym, tf, m_slowEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSmma = iMA(sym, tf, m_smmaPeriod, 0, MODE_SMMA, PRICE_CLOSE);
      double e21[], eMid[], s100[];
      ArraySetAsSeries(e21, true);
      ArraySetAsSeries(eMid, true);
      ArraySetAsSeries(s100, true);
      if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE || hSmma == INVALID_HANDLE)
      {
         if(hFast != INVALID_HANDLE) IndicatorRelease(hFast);
         if(hSlow != INVALID_HANDLE) IndicatorRelease(hSlow);
         if(hSmma != INVALID_HANDLE) IndicatorRelease(hSmma);
         return false;
      }
      bool ok = (CopyBuffer(hFast, 0, 1, 1, e21) >= 1
                 && CopyBuffer(hSlow, 0, 1, 1, eMid) >= 1
                 && CopyBuffer(hSmma, 0, 1, 1, s100) >= 1);
      IndicatorRelease(hFast);
      IndicatorRelease(hSlow);
      IndicatorRelease(hSmma);
      if(!ok)
         return false;
      return (c0 > s100[0] && e21[0] > eMid[0]);
   }

   bool Ema21CrossBiasShort(const string sym, const ENUM_TIMEFRAMES tf, const double c0) const
   {
      if(FullStackBarFromSeries(sym, tf, c0, -1))
         return true;
      int hFast = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSlow = iMA(sym, tf, m_slowEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSmma = iMA(sym, tf, m_smmaPeriod, 0, MODE_SMMA, PRICE_CLOSE);
      double e21[], eMid[], s100[];
      ArraySetAsSeries(e21, true);
      ArraySetAsSeries(eMid, true);
      ArraySetAsSeries(s100, true);
      if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE || hSmma == INVALID_HANDLE)
      {
         if(hFast != INVALID_HANDLE) IndicatorRelease(hFast);
         if(hSlow != INVALID_HANDLE) IndicatorRelease(hSlow);
         if(hSmma != INVALID_HANDLE) IndicatorRelease(hSmma);
         return false;
      }
      bool ok = (CopyBuffer(hFast, 0, 1, 1, e21) >= 1
                 && CopyBuffer(hSlow, 0, 1, 1, eMid) >= 1
                 && CopyBuffer(hSmma, 0, 1, 1, s100) >= 1);
      IndicatorRelease(hFast);
      IndicatorRelease(hSlow);
      IndicatorRelease(hSmma);
      if(!ok)
         return false;
      return (c0 < s100[0] && e21[0] < eMid[0]);
   }

   bool FullStackBarFromSeries(const string sym, const ENUM_TIMEFRAMES tf, const double c0, const int side) const
   {
      int hFast = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSlow = iMA(sym, tf, m_slowEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSmma = iMA(sym, tf, m_smmaPeriod, 0, MODE_SMMA, PRICE_CLOSE);
      double e21[], eMid[], s100[];
      ArraySetAsSeries(e21, true);
      ArraySetAsSeries(eMid, true);
      ArraySetAsSeries(s100, true);
      if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE || hSmma == INVALID_HANDLE)
      {
         if(hFast != INVALID_HANDLE) IndicatorRelease(hFast);
         if(hSlow != INVALID_HANDLE) IndicatorRelease(hSlow);
         if(hSmma != INVALID_HANDLE) IndicatorRelease(hSmma);
         return false;
      }
      bool ok = (CopyBuffer(hFast, 0, 1, 1, e21) >= 1
                 && CopyBuffer(hSlow, 0, 1, 1, eMid) >= 1
                 && CopyBuffer(hSmma, 0, 1, 1, s100) >= 1);
      IndicatorRelease(hFast);
      IndicatorRelease(hSlow);
      IndicatorRelease(hSmma);
      if(!ok)
         return false;
      return FullStackBar(c0, e21[0], eMid[0], s100[0], side);
   }

   double DistAtr(const double price, const double level, const double atr) const
   {
      if(atr <= 0)
         return 999.0;
      return MathAbs(price - level) / atr;
   }

   bool EmaFanOk(const double e21, const double eMid, const double s100, const double atr, const int side) const
   {
      if(atr <= 0 || e21 <= 0 || eMid <= 0 || s100 <= 0)
         return false;
      double s1, s2;
      if(side > 0)
      {
         s1 = (e21 - eMid) / atr;
         s2 = (eMid - s100) / atr;
      }
      else
      {
         s1 = (eMid - e21) / atr;
         s2 = (s100 - eMid) / atr;
      }
      if(s1 <= 0 || s2 <= 0)
         return false;
      double mn = MathMin(s1, s2);
      if(mn < m_minFanSpreadAtr)
         return false;
      if((s1 + s2) > m_minFanSpreadAtr * 40.0)
         return false;
      return true;
   }

   bool EntryProximityOk(const double c0, const double h0, const double l0,
                         const double e21, const double atr, const int side) const
   {
      if(!m_useProxFilters)
         return true;
      double closeD = DistAtr(c0, e21, atr);
      if(side > 0)
      {
         double touchD = DistAtr(l0, e21, atr);
         return (closeD <= m_maxEntryEma21Atr || touchD <= m_maxEntryEma21Atr * 1.35);
      }
      double touchD = DistAtr(h0, e21, atr);
      return (closeD <= m_maxEntryEma21Atr || touchD <= m_maxEntryEma21Atr * 1.35);
   }

   bool FullStackBar(const double c0, const double e21, const double eMid, const double s100, const int side) const
   {
      if(side > 0)
         return (c0 > e21 && e21 > eMid && c0 > s100);
      return (c0 < e21 && e21 < eMid && c0 < s100);
   }

   bool ChopNearSmma(const double c0, const double s100, const double atr) const
   {
      if(atr <= 0)
         return false;
      return (MathAbs(c0 - s100) <= atr * 0.35);
   }

   bool RsiGradeOk(const double rsi, const int side) const
   {
      if(side > 0)
         return (rsi > 45.0 && rsi < 78.0);
      return (rsi > 22.0 && rsi < 55.0);
   }

   bool StackRegimeOk(const string sym, const ENUM_TIMEFRAMES tf, const int side, const int needBars) const
   {
      int need = MathMax(needBars + 2, 8);
      double c[];
      ArraySetAsSeries(c, true);
      if(CopyClose(sym, tf, 1, need, c) < need)
         return false;
      int hFast = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSlow = iMA(sym, tf, m_slowEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSmma = iMA(sym, tf, m_smmaPeriod, 0, MODE_SMMA, PRICE_CLOSE);
      if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE || hSmma == INVALID_HANDLE)
      {
         if(hFast != INVALID_HANDLE) IndicatorRelease(hFast);
         if(hSlow != INVALID_HANDLE) IndicatorRelease(hSlow);
         if(hSmma != INVALID_HANDLE) IndicatorRelease(hSmma);
         return false;
      }
      double e21[], eMid[], s100[];
      ArraySetAsSeries(e21, true);
      ArraySetAsSeries(eMid, true);
      ArraySetAsSeries(s100, true);
      if(CopyBuffer(hFast, 0, 1, need, e21) < need
         || CopyBuffer(hSlow, 0, 1, need, eMid) < need
         || CopyBuffer(hSmma, 0, 1, need, s100) < need)
      {
         IndicatorRelease(hFast);
         IndicatorRelease(hSlow);
         IndicatorRelease(hSmma);
         return false;
      }
      IndicatorRelease(hFast);
      IndicatorRelease(hSlow);
      IndicatorRelease(hSmma);
      for(int i = 0; i < needBars; i++)
      {
         if(!FullStackBar(c[i], e21[i], eMid[i], s100[i], side))
            return false;
      }
      return true;
   }

   int PlaybookRegimeBars() const
   {
      return (m_playbook ? m_stackRegimeBars : m_minStreak);
   }

   bool StayAwayOk(const string sym, const ENUM_TIMEFRAMES tf, const int side,
                   const double c0, const double h0, const double l0, const double atr,
                   const bool earlyCross = false, const bool goodTrend = false) const
   {
      if(!m_strictStayAway)
         return true;
      int regimeBars = PlaybookRegimeBars();
      if(goodTrend && earlyCross)
      {
         if(side > 0 && !Ema21CrossBiasLong(sym, tf, c0))
            return false;
         if(side < 0 && !Ema21CrossBiasShort(sym, tf, c0))
            return false;
         int hFast = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
         int hSlow = iMA(sym, tf, m_slowEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
         int hSmma = iMA(sym, tf, m_smmaPeriod, 0, MODE_SMMA, PRICE_CLOSE);
         double e21[], eMid[], s100[];
         ArraySetAsSeries(e21, true);
         ArraySetAsSeries(eMid, true);
         ArraySetAsSeries(s100, true);
         if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE || hSmma == INVALID_HANDLE)
         {
            if(hFast != INVALID_HANDLE) IndicatorRelease(hFast);
            if(hSlow != INVALID_HANDLE) IndicatorRelease(hSlow);
            if(hSmma != INVALID_HANDLE) IndicatorRelease(hSmma);
            return false;
         }
         bool ok = (CopyBuffer(hFast, 0, 1, 1, e21) >= 1
                    && CopyBuffer(hSlow, 0, 1, 1, eMid) >= 1
                    && CopyBuffer(hSmma, 0, 1, 1, s100) >= 1);
         IndicatorRelease(hFast);
         IndicatorRelease(hSlow);
         IndicatorRelease(hSmma);
         if(!ok)
            return false;
         if(ChopNearSmma(c0, s100[0], atr) && !FullStackBar(c0, e21[0], eMid[0], s100[0], side))
            return false;
         if(!RsiGradeOkFromSymbol(sym, tf, side))
            return false;
         return ProximityStackOk(sym, tf, side, c0, h0, l0, atr);
      }
      if(!earlyCross && !StackRegimeOk(sym, tf, side, regimeBars))
         return false;
      int hFast = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSlow = iMA(sym, tf, m_slowEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSmma = iMA(sym, tf, m_smmaPeriod, 0, MODE_SMMA, PRICE_CLOSE);
      int hRsi = iRSI(sym, tf, m_rsiPeriod, PRICE_CLOSE);
      double e21[], eMid[], s100[], rsi[];
      ArraySetAsSeries(e21, true);
      ArraySetAsSeries(eMid, true);
      ArraySetAsSeries(s100, true);
      ArraySetAsSeries(rsi, true);
      if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE || hSmma == INVALID_HANDLE || hRsi == INVALID_HANDLE)
      {
         if(hFast != INVALID_HANDLE) IndicatorRelease(hFast);
         if(hSlow != INVALID_HANDLE) IndicatorRelease(hSlow);
         if(hSmma != INVALID_HANDLE) IndicatorRelease(hSmma);
         if(hRsi != INVALID_HANDLE) IndicatorRelease(hRsi);
         return false;
      }
      bool ok = (CopyBuffer(hFast, 0, 1, 1, e21) >= 1
                 && CopyBuffer(hSlow, 0, 1, 1, eMid) >= 1
                 && CopyBuffer(hSmma, 0, 1, 1, s100) >= 1
                 && CopyBuffer(hRsi, 0, 1, 1, rsi) >= 1);
      IndicatorRelease(hFast);
      IndicatorRelease(hSlow);
      IndicatorRelease(hSmma);
      IndicatorRelease(hRsi);
      if(!ok)
         return false;
      if(earlyCross)
      {
         if(side > 0 && !Ema21CrossBiasLong(sym, tf, c0))
            return false;
         if(side < 0 && !Ema21CrossBiasShort(sym, tf, c0))
            return false;
      }
      else if(!FullStackBar(c0, e21[0], eMid[0], s100[0], side))
         return false;
      // Chop only blocks when not in a clean stack (matches chart_state_at / playbook GOOD).
      if(ChopNearSmma(c0, s100[0], atr) && !FullStackBar(c0, e21[0], eMid[0], s100[0], side))
         return false;
      if(!RsiGradeOk(rsi[0], side))
         return false;
      return ProximityStackOk(sym, tf, side, c0, h0, l0, atr);
   }

   bool ProximityStackOk(const string sym, const ENUM_TIMEFRAMES tf, const int side,
                         const double c0, const double h0, const double l0, const double atr) const
   {
      if(!m_useProxFilters)
         return true;
      int hFast = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSlow = iMA(sym, tf, m_slowEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hSmma = iMA(sym, tf, m_smmaPeriod, 0, MODE_SMMA, PRICE_CLOSE);
      if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE || hSmma == INVALID_HANDLE)
      {
         if(hFast != INVALID_HANDLE) IndicatorRelease(hFast);
         if(hSlow != INVALID_HANDLE) IndicatorRelease(hSlow);
         if(hSmma != INVALID_HANDLE) IndicatorRelease(hSmma);
         return false;
      }
      double e21[], eMid[], s100[];
      ArraySetAsSeries(e21, true);
      ArraySetAsSeries(eMid, true);
      ArraySetAsSeries(s100, true);
      bool ok = (CopyBuffer(hFast, 0, 1, 4, e21) >= 4
                 && CopyBuffer(hSlow, 0, 1, 2, eMid) >= 2
                 && CopyBuffer(hSmma, 0, 1, 2, s100) >= 2);
      IndicatorRelease(hFast);
      IndicatorRelease(hSlow);
      IndicatorRelease(hSmma);
      if(!ok)
         return false;
      if(!EmaFanOk(e21[0], eMid[0], s100[0], atr, side))
         return false;
      if(!EntryProximityOk(c0, h0, l0, e21[0], atr, side))
         return false;
      double slowChase = (side > 0) ? (c0 - s100[0]) / atr : (s100[0] - c0) / atr;
      if(slowChase < 0 || slowChase > 2.8)
         return false;
      double slope = (e21[0] - e21[2]) / atr;
      if(side > 0 && slope < 0.02)
         return false;
      if(side < 0 && slope > -0.02)
         return false;
      if(MathAbs(c0 - s100[0]) <= atr * 0.35)
      {
         if(!EmaFanOk(e21[0], eMid[0], s100[0], atr, side))
            return false;
      }
      return true;
   }

   bool ExtensionExitOk(const string sym, const ENUM_TIMEFRAMES tf, const int side, const ulong ticket,
                        const double c0, const double e21, const double atr) const
   {
      if(!m_useProxFilters || atr <= 0)
         return false;
      if(!PositionSelectByTicket(ticket))
         return false;
      datetime tOpen = (datetime)PositionGetInteger(POSITION_TIME);
      int barsHeld = iBarShift(sym, tf, tOpen, true);
      if(barsHeld < m_minBarsExtExit)
         return false;
      double entryPx = PositionGetDouble(POSITION_PRICE_OPEN);
      double profitAtr = (side > 0) ? (c0 - entryPx) / atr : (entryPx - c0) / atr;
      if(profitAtr < m_minProfitAtrExt)
         return false;
      double ext = DistAtr(c0, e21, atr);
      if(ext < m_exitExtEma21Atr)
         return false;
      if(side > 0 && c0 < e21)
         return false;
      if(side < 0 && c0 > e21)
         return false;
      return true;
   }

   bool RsiVisualOk(const string sym, const ENUM_TIMEFRAMES tf, const int side) const
   {
      int hRsi = iRSI(sym, tf, m_rsiPeriod, PRICE_CLOSE);
      if(hRsi == INVALID_HANDLE)
         return false;
      double rsi[];
      ArraySetAsSeries(rsi, true);
      bool ok = (CopyBuffer(hRsi, 0, 1, 1, rsi) >= 1);
      IndicatorRelease(hRsi);
      if(!ok)
         return false;
      if(side > 0)
         return (rsi[0] > 50.0 && rsi[0] < 80.0);
      return (rsi[0] > 20.0 && rsi[0] < 50.0);
   }

   bool PivotLevels(const string sym, double &s1, double &r1) const
   {
      s1 = r1 = 0.0;
      double h[], l[], c[];
      ArraySetAsSeries(h, true);
      ArraySetAsSeries(l, true);
      ArraySetAsSeries(c, true);
      if(CopyHigh(sym, PERIOD_D1, 1, 1, h) < 1)
         return false;
      if(CopyLow(sym, PERIOD_D1, 1, 1, l) < 1)
         return false;
      if(CopyClose(sym, PERIOD_D1, 1, 1, c) < 1)
         return false;
      double p = (h[0] + l[0] + c[0]) / 3.0;
      s1 = 2.0 * p - h[0];
      r1 = 2.0 * p - l[0];
      return true;
   }

   double NormalizeVolume(const string sym, double vol) const
   {
      double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
      double vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
      double vmax = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
      if(step <= 0)
         step = 0.01;
      vol = MathFloor(vol / step) * step;
      if(vol < vmin)
         return 0.0;
      if(vol > vmax)
         vol = vmax;
      return vol;
   }

public:
   CSovereignFxmopol()
   {
      m_magic = 0;
      m_emaPeriod = 21;
      m_h4EmaPeriod = 50;
      m_swingBars = 20;
      m_maxPerDay = 3;
      m_slAtr = 1.0;
      m_tpAtr = 2.0;
      m_maxSpread = 20;
      m_riskPct = 0.35;
      m_breakout = false;
      m_goodSessionsOnly = true;
      m_pinOnly = false;
      m_visualSetup = true;
      m_slowEmaPeriod = 50;
      m_smmaPeriod = 100;
      m_rsiPeriod = 9;
      m_useFractals = true;
      m_manualLevel = 0.0;
      m_streakRide = false;
      m_playbook = false;
      m_regimeEmaOnly = true;
      m_minStreak = 30;
      m_useProxFilters = true;
      m_strictStayAway = true;
      m_useRegimeFilter = true;
      m_maxLossesPerDay = 1;
      m_dayLossCount = 0;
      m_maxEntryEma21Atr = 0.40;
      m_minFanSpreadAtr = 0.12;
      m_exitExtEma21Atr = 1.15;
      m_minBarsExtExit = 4;
      m_minProfitAtrExt = 0.35;
      m_lastBar = 0;
      m_dayKey = 0;
      m_dayCount = 0;
      m_showMarketPulse = true;
      m_playbookSignalExit = false;
      m_ecoRiskMult = 1.0;
      m_ecoBlockLong = false;
      m_ecoBlockShort = false;
      m_ecoProfessorLine = "";

      m_pulseReg = 0;
      m_pulseCtxL = 0;
      m_pulseCtxS = 0;
      m_pulseRsi = 0.0;
      m_pulseAtr = 0.0;
      m_pulseAction = "";
      m_pulseQ1 = "";
      m_pulseQ2 = "";
      m_pulseIssues = "";
      m_pulseD1 = "";
      m_pulseProfessor = "";
      m_pulseTlL = 0;
      m_pulseTlS = 0;
      m_pulseValid = false;
   }

   //--- read-only accessors for the chart panel ---------------------
   bool   PulseValid()      const { return m_pulseValid; }
   int    PulseRegime()     const { return m_pulseReg; }
   int    PulseCtxLong()    const { return m_pulseCtxL; }
   int    PulseCtxShort()   const { return m_pulseCtxS; }
   double PulseRsi()        const { return m_pulseRsi; }
   double PulseAtr()        const { return m_pulseAtr; }
   string PulseAction()     const { return m_pulseAction; }
   string PulseQ1()         const { return m_pulseQ1; }
   string PulseQ2()         const { return m_pulseQ2; }
   string PulseIssues()     const { return m_pulseIssues; }
   string PulseD1()         const { return m_pulseD1; }
   string PulseProfessor()  const { return m_pulseProfessor; }
   int    PulseTlLong()     const { return m_pulseTlL; }
   int    PulseTlShort()    const { return m_pulseTlS; }
   int    EmaPeriod()       const { return m_emaPeriod; }
   int    SlowEmaPeriod()   const { return m_slowEmaPeriod; }
   int    SmmaPeriod()      const { return m_smmaPeriod; }
   int    RsiPeriod()       const { return m_rsiPeriod; }
   int    MaxPerDay()       const { return m_maxPerDay; }
   double SlAtr()           const { return m_slAtr; }
   double TpAtr()           const { return m_tpAtr; }

   void SetEcosystemGate(const double riskMult, const bool blockLong, const bool blockShort,
                         const string professorLine)
   {
      m_ecoRiskMult = riskMult;
      if(m_ecoRiskMult < 0.0)
         m_ecoRiskMult = 0.0;
      if(m_ecoRiskMult > 1.25)
         m_ecoRiskMult = 1.25;
      m_ecoBlockLong = blockLong;
      m_ecoBlockShort = blockShort;
      m_ecoProfessorLine = professorLine;
   }

   bool PlaybookEntryHardGate(const string sym, const ENUM_TIMEFRAMES tf, const int side,
                              const bool earlyCross, const double c0, const double h0,
                              const double l0, const double atr) const
   {
      if(!m_playbook)
         return true;
      if(!D1StackAligns(sym, side))
         return false;
      if(Ema21WhipsawChop(sym, tf))
         return false;
      if(!StayAwayOk(sym, tf, side, c0, h0, l0, atr, earlyCross, (MarketContext(sym, tf, side) >= 3)))
         return false;
      return true;
   }

   bool Init(const long magic, const int ema, const int h4Ema, const int swing, const int maxPerDay,
             const double slAtr, const double tpAtr, const int maxSpread, const double riskPct,
             const bool breakout, const bool goodSessionsOnly, const bool pinOnly,
             const bool visualSetup, const int slowEma, const int smmaPeriod, const int rsiPeriod,
             const bool useFractals, const double manualLevel,
             const bool streakRide, const bool playbook, const bool regimeEmaOnly, const int minStreak,
             const bool useProxFilters, const bool strictStayAway, const bool useRegimeFilter,
             const int maxLossesPerDay,
             const double maxEntryEma21Atr, const double minFanSpreadAtr,
             const double exitExtEma21Atr, const int minBarsExtExit, const double minProfitAtrExt,
             const bool showMarketPulse = true,
             const bool playbookSignalExit = false,
             const bool useD1Gate = true,
             const bool useFullPriceAction = true,
             const bool useLevelTp = true,
             const int stackRegimeBars = 5,
             const double rsiCoverLevel = 30.0,
             const int weekdayMask = 14)
   {
      m_magic = magic;
      m_emaPeriod = MathMax(5, ema);
      m_h4EmaPeriod = MathMax(10, h4Ema);
      m_swingBars = MathMax(5, swing);
      m_maxPerDay = MathMax(0, maxPerDay);
      m_slAtr = slAtr;
      m_tpAtr = tpAtr;
      m_maxSpread = maxSpread;
      m_riskPct = riskPct;
      m_breakout = breakout;
      m_goodSessionsOnly = goodSessionsOnly;
      m_pinOnly = pinOnly;
      m_visualSetup = visualSetup;
      m_slowEmaPeriod = MathMax(20, slowEma);
      m_smmaPeriod = MathMax(20, smmaPeriod);
      m_rsiPeriod = MathMax(2, rsiPeriod);
      m_useFractals = useFractals;
      m_manualLevel = manualLevel;
      m_streakRide = streakRide;
      m_playbook = playbook;
      m_regimeEmaOnly = regimeEmaOnly;
      m_minStreak = MathMax(3, minStreak);
      m_useProxFilters = useProxFilters;
      m_strictStayAway = strictStayAway;
      m_useRegimeFilter = useRegimeFilter;
      m_maxLossesPerDay = MathMax(0, maxLossesPerDay);
      m_maxEntryEma21Atr = MathMax(0.05, maxEntryEma21Atr);
      m_minFanSpreadAtr = MathMax(0.02, minFanSpreadAtr);
      m_exitExtEma21Atr = MathMax(0.3, exitExtEma21Atr);
      m_minBarsExtExit = MathMax(1, minBarsExtExit);
      m_minProfitAtrExt = MathMax(0.0, minProfitAtrExt);
      m_showMarketPulse = showMarketPulse;
      m_playbookSignalExit = (playbookSignalExit || playbook);
      m_useD1Gate = useD1Gate;
      m_useFullPriceAction = useFullPriceAction;
      m_useLevelTp = useLevelTp;
      m_stackRegimeBars = MathMax(3, stackRegimeBars);
      m_rsiCoverLevel = MathMax(5.0, MathMin(rsiCoverLevel, 45.0));
      m_weekdayMask = MathMax(0, weekdayMask);
      return true;
   }

   void OnTradeClosed(const MqlTradeTransaction &trans)
   {
      if(trans.type != TRADE_TRANSACTION_DEAL_ADD)
         return;
      if(!HistoryDealSelect(trans.deal))
         return;
      if((long)HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != m_magic)
         return;
      if((long)HistoryDealGetInteger(trans.deal, DEAL_ENTRY) != DEAL_ENTRY_OUT)
         return;
      double profit = HistoryDealGetDouble(trans.deal, DEAL_PROFIT);
      if(profit < 0)
         m_dayLossCount++;
   }

   void OnTick(const string sym, const ENUM_TIMEFRAMES tf, const bool allow, const double equity,
               CSovereignPerformance &perf, CSovereignTrafficLight &tl, CTrade &trade)
   {
      if(!allow)
         return;
      bool newBar = NewBar(sym, tf);
      int openSidePulse = 0;
      if(HasOpenPosition(sym))
      {
         CPositionInfo pp;
         for(int pi = PositionsTotal() - 1; pi >= 0; pi--)
         {
            if(pp.SelectByIndex(pi) && pp.Magic() == m_magic && pp.Symbol() == sym)
            {
               openSidePulse = (pp.PositionType() == POSITION_TYPE_BUY) ? 1 : -1;
               break;
            }
         }
      }
      if(m_showMarketPulse && newBar)
         PublishMarketPulse(sym, tf, tl, openSidePulse);
      if(m_maxPerDay <= 0)
         return;
      if(!newBar)
         return;
      MqlDateTime barDt;
      TimeToStruct(m_lastBar, barDt);
      int openSide = 0;
      if(HasOpenPosition(sym))
      {
         CPositionInfo pos;
         for(int pi = PositionsTotal() - 1; pi >= 0; pi--)
         {
            if(!pos.SelectByIndex(pi))
               continue;
            if(pos.Magic() == m_magic && pos.Symbol() == sym)
            {
               openSide = (pos.PositionType() == POSITION_TYPE_BUY) ? 1 : -1;
               break;
            }
         }
      }

      if((m_streakRide || m_playbook) && openSide != 0)
      {
         int hMaX = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
         int hRsiX = iRSI(sym, tf, m_rsiPeriod, PRICE_CLOSE);
         double e21x[], cx[], rsiX[];
         ArraySetAsSeries(e21x, true);
         ArraySetAsSeries(cx, true);
         ArraySetAsSeries(rsiX, true);
         bool okMa = (hMaX != INVALID_HANDLE && CopyBuffer(hMaX, 0, 1, 2, e21x) >= 2 && CopyClose(sym, tf, 1, 2, cx) >= 2);
         bool okRsi = (hRsiX != INVALID_HANDLE && CopyBuffer(hRsiX, 0, 1, 2, rsiX) >= 2);
         int atrHx = iATR(sym, tf, 14);
         double atrX[];
         ArraySetAsSeries(atrX, true);
         bool okAtr = (atrHx != INVALID_HANDLE && CopyBuffer(atrHx, 0, 1, 1, atrX) >= 1);
         if(atrHx != INVALID_HANDLE)
            IndicatorRelease(atrHx);
         CPositionInfo posX;
         ulong ticketX = 0;
         for(int pix = PositionsTotal() - 1; pix >= 0; pix--)
         {
            if(posX.SelectByIndex(pix) && posX.Magic() == m_magic && posX.Symbol() == sym)
            {
               ticketX = posX.Ticket();
               break;
            }
         }
         if(okMa)
         {
            bool extExit = (okAtr && ticketX > 0
                            && ExtensionExitOk(sym, tf, openSide, ticketX, cx[0], e21x[0], atrX[0]));
            // Playbook signal exits (EMA/RSI) match research — do not flatten on brief regime chop.
            if(m_playbook && m_useRegimeFilter && !m_playbookSignalExit)
            {
               int ctxOpen = MarketContext(sym, tf, openSide);
               int reg = MarketRegime(sym, tf);
               if(ctxOpen == 0 || (reg != 0 && reg != openSide))
               {
                  trade.PositionClose(sym);
                  if(hMaX != INVALID_HANDLE)
                     IndicatorRelease(hMaX);
                  if(hRsiX != INVALID_HANDLE)
                     IndicatorRelease(hRsiX);
                  return;
               }
            }
            if(m_playbook && okMa)
            {
               bool longBreak = (cx[0] < e21x[0] && cx[1] < e21x[1]);
               bool shortBreak = (cx[0] > e21x[0] && cx[1] > e21x[1]);
               bool rsiRollLong = (okRsi && rsiX[1] >= 80.0 && rsiX[0] < 75.0);
               bool rsiRollShort = (okRsi && rsiX[1] <= 20.0 && rsiX[0] > 25.0);
               bool levelHit = LevelTpHit(sym, openSide, okAtr ? atrX[0] : 0.0);
               if(openSide > 0 && (longBreak || (okRsi && rsiX[0] >= 80.0) || rsiRollLong || levelHit || extExit))
                  trade.PositionClose(sym);
               if(openSide < 0 && (shortBreak || (okRsi && rsiX[0] <= m_rsiCoverLevel)
                                   || (okRsi && rsiX[0] <= 20.0) || rsiRollShort || levelHit || extExit))
                  trade.PositionClose(sym);
            }
            else
            {
               if(openSide > 0 && (cx[0] < e21x[0] || (okRsi && rsiX[0] >= 80.0) || extExit))
                  trade.PositionClose(sym);
               if(openSide < 0 && (cx[0] > e21x[0] || (okRsi && rsiX[0] <= 20.0) || extExit))
                  trade.PositionClose(sym);
            }
         }
         if(hMaX != INVALID_HANDLE)
            IndicatorRelease(hMaX);
         if(hRsiX != INVALID_HANDLE)
            IndicatorRelease(hRsiX);
         return;
      }

      if(m_dayCount >= m_maxPerDay || HasOpenPosition(sym))
         return;
      if(m_maxLossesPerDay > 0 && m_dayLossCount >= m_maxLossesPerDay)
         return;
      if(!SessionAllowsNewEntry(barDt.hour) || !WeekdayAllowsNewEntry(barDt.day_of_week))
         return;

      if(m_visualSetup && !m_streakRide && !m_playbook)
         return;

      int need = m_swingBars + 5;
      double c[], h[], l[], o[];
      ArraySetAsSeries(c, true);
      ArraySetAsSeries(h, true);
      ArraySetAsSeries(l, true);
      ArraySetAsSeries(o, true);
      if(CopyClose(sym, tf, 1, need, c) < need)
         return;
      if(CopyHigh(sym, tf, 1, need, h) < need)
         return;
      if(CopyLow(sym, tf, 1, need, l) < need)
         return;
      if(CopyOpen(sym, tf, 1, need, o) < need)
         return;

      int atrH0 = iATR(sym, tf, 14);
      double atr[];
      ArraySetAsSeries(atr, true);
      if(CopyBuffer(atrH0, 0, 1, 1, atr) < 1 || atr[0] <= 0)
      {
         if(atrH0 != INVALID_HANDLE)
            IndicatorRelease(atrH0);
         return;
      }
      if(atrH0 != INVALID_HANDLE)
         IndicatorRelease(atrH0);

      int hMa = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      double ema[];
      ArraySetAsSeries(ema, true);
      if(CopyBuffer(hMa, 0, 1, 4, ema) < 4)
      {
         IndicatorRelease(hMa);
         return;
      }
      IndicatorRelease(hMa);

      int side = 0;
      bool earlyCross = false;
      if(m_playbook && m_visualSetup)
      {
         int hMaX = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
         double e21x[];
         ArraySetAsSeries(e21x, true);
         if(hMaX != INVALID_HANDLE && CopyBuffer(hMaX, 0, 1, 3, e21x) >= 3)
         {
            if(Ema21CloseCrossLong(c[0], c[1], e21x[0], e21x[1])
               && !Ema21WhipsawChop(sym, tf)
               && !Ema21OneBarFakeoutLong(c[0], c[1], c[2], e21x[0], e21x[1], e21x[2])
               && Ema21CrossBiasLong(sym, tf, c[0])
               && RsiVisualOk(sym, tf, 1))
            {
               side = 1;
               earlyCross = true;
            }
            else if(Ema21CloseCrossShort(c[0], c[1], e21x[0], e21x[1])
                     && !Ema21WhipsawChop(sym, tf)
                     && !Ema21OneBarFakeoutShort(c[0], c[1], c[2], e21x[0], e21x[1], e21x[2])
                     && Ema21CrossBiasShort(sym, tf, c[0])
                     && RsiVisualOk(sym, tf, -1))
            {
               side = -1;
               earlyCross = true;
            }
         }
         if(hMaX != INVALID_HANDLE)
            IndicatorRelease(hMaX);
      }
      if(m_visualSetup)
      {
         if(m_playbook)
         {
            if(side == 0)
            {
               int hMaP = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
               double e21p[];
               ArraySetAsSeries(e21p, true);
               bool haveE21 = (hMaP != INVALID_HANDLE && CopyBuffer(hMaP, 0, 1, 1, e21p) >= 1);
               if(hMaP != INVALID_HANDLE)
                  IndicatorRelease(hMaP);
               int rb = PlaybookRegimeBars();
               if(haveE21 && StackRegimeOk(sym, tf, 1, rb) && RsiVisualOk(sym, tf, 1))
               {
                  if(m_useFullPriceAction)
                  {
                     if(PlaybookChartEntry(sym, tf, 1, o[0], h[0], l[0], c[0], atr[0], h, l))
                        side = 1;
                  }
                  else if(PlaybookPullbackLong(l[0], c[0], e21p[0], atr[0])
                          || WickHuntReclaim(o[0], h[0], l[0], c[0], 1))
                     side = 1;
               }
               if(side == 0 && haveE21 && StackRegimeOk(sym, tf, -1, rb) && RsiVisualOk(sym, tf, -1))
               {
                  if(m_useFullPriceAction)
                  {
                     if(PlaybookChartEntry(sym, tf, -1, o[0], h[0], l[0], c[0], atr[0], h, l))
                        side = -1;
                  }
                  else if(PlaybookRetestShort(h[0], c[0], e21p[0], atr[0])
                          || WickHuntReclaim(o[0], h[0], l[0], c[0], -1))
                     side = -1;
               }
            }
            if(side == 0)
               return;
         }
         else
         {
            if(side == 0 && VisualTrendStack(sym, tf, 1, c[0], c[1]))
               side = 1;
            else if(side == 0 && VisualTrendStack(sym, tf, -1, c[0], c[1]))
               side = -1;
            if(side == 0)
               return;
            if(!earlyCross && !RsiVisualOk(sym, tf, side))
               return;
         }
      }
      else
      {
         bool up = (c[0] > ema[0] && ema[0] > ema[2]);
         bool dn = (c[0] < ema[0] && ema[0] < ema[2]);
         if(!up && !dn)
            return;
         side = up ? 1 : -1;
         if(!H4TrendOk(sym, side))
            return;
      }

      double swingHi = h[1], swingLo = l[1];
      for(int k = 2; k < m_swingBars; k++)
      {
         swingHi = MathMax(swingHi, h[k]);
         swingLo = MathMin(swingLo, l[k]);
      }
      double fib50 = swingLo + (swingHi - swingLo) * 0.5;
      double fib618 = swingLo + (swingHi - swingLo) * 0.618;

      double s1, r1;
      if(!PivotLevels(sym, s1, r1))
         return;

      int mktCtx = 3;
      if(m_playbook && m_useRegimeFilter)
      {
         mktCtx = MarketContext(sym, tf, side);
         if(mktCtx <= 1)
            return;
         if(mktCtx < 3 && !earlyCross)
            return;
         if(mktCtx == 2 && earlyCross && m_dayCount >= 1)
            return;
         if(HasOpenPosition(sym) && mktCtx < 3)
            return;
      }
      bool goodTrend = (mktCtx >= 3);
      if(!PlaybookEntryHardGate(sym, tf, side, earlyCross, c[0], h[0], l[0], atr[0]))
         return;
      if(m_visualSetup && !m_playbook && !ProximityStackOk(sym, tf, side, c[0], h[0], l[0], atr[0]))
         return;

      if(m_visualSetup)
      {
         if(m_playbook && !earlyCross && !RsiVisualOk(sym, tf, side))
            return;
         else if(m_streakRide)
         {
            int st = CountStreak(sym, tf, side > 0);
            if(st != m_minStreak)
               return;
         }
         else if(m_useFullPriceAction && m_visualSetup)
         {
            int rb = PlaybookRegimeBars();
            if(!StackRegimeOk(sym, tf, side, rb) || !RsiVisualOk(sym, tf, side))
               return;
            if(!PlaybookChartEntry(sym, tf, side, o[0], h[0], l[0], c[0], atr[0], h, l))
               return;
         }
         else
         {
            if(m_useFractals && !NearFractal(h, l, side))
               return;
            if(m_manualLevel > 0.0 && MathAbs(c[0] - m_manualLevel) > atr[0] * 0.6)
               return;
            if(!Criminal(o[0], h[0], l[0], c[0], side))
               return;
         }
      }
      else
      {
         if(m_breakout)
         {
            if(side > 0 && c[0] <= swingHi)
               return;
            if(side < 0 && c[0] >= swingLo)
               return;
         }
         else
         {
            bool atZone = false;
            if(side > 0)
               atZone = (l[0] >= fib618 && l[0] <= swingHi * 1.001) || (MathAbs(l[0] - s1) < atr[0] * 0.35);
            else
               atZone = (h[0] <= fib50 && h[0] >= swingLo * 0.999) || (MathAbs(h[0] - r1) < atr[0] * 0.35);
            if(!atZone)
               return;
         }

         bool pat = false;
         if(m_pinOnly)
            pat = Pin(o[0], h[0], l[0], c[0], side);
         else
            pat = Criminal(o[0], h[0], l[0], c[0], side)
                  || Engulf(o[0], c[0], o[1], c[1], side)
                  || Pin(o[0], h[0], l[0], c[0], side);
         if(!pat)
            return;

         int hRsi = iRSI(sym, tf, m_rsiPeriod, PRICE_CLOSE);
         double rsi[];
         ArraySetAsSeries(rsi, true);
         if(CopyBuffer(hRsi, 0, 1, 1, rsi) < 1)
         {
            IndicatorRelease(hRsi);
            return;
         }
         IndicatorRelease(hRsi);
         if(side > 0 && (rsi[0] < 35 || rsi[0] > 58))
            return;
         if(side < 0 && (rsi[0] < 42 || rsi[0] > 65))
            return;
      }

      int spread = (int)SymbolInfoInteger(sym, SYMBOL_SPREAD);
      if(spread > m_maxSpread)
         return;

      if(side > 0 && m_ecoBlockLong)
         return;
      if(side < 0 && m_ecoBlockShort)
         return;
      if(m_ecoRiskMult <= 0.0)
         return;

      string entryTag = m_playbook ? (earlyCross ? "cross" : "playbook") : (m_streakRide ? "streak" : "visual");
      string legBucket = CSovereignPerformance::LegBucket("SOV-FXMOPOL", sym);
      string featBucket = CSovereignPerformance::FeatureBucket(
         "SOV-FXMOPOL", sym, entryTag, barDt.day_of_week, barDt.hour);
      if(!perf.AllowNewRisk(legBucket))
         return;
      double mult = perf.CombinedMultiplier(legBucket);
      if(perf.AllowNewRisk(featBucket))
         mult = MathMin(mult, perf.CombinedMultiplier(featBucket));
      if(mult <= 0)
         return;

      double stopDist = atr[0] * (m_playbookSignalExit ? MathMax(m_slAtr, 3.5) : m_slAtr);
      double tickSize = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
      double tickVal = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
      if(tickSize <= 0 || tickVal <= 0)
         return;
      double riskMoney = equity * m_riskPct / 100.0 * mult * m_ecoRiskMult;
      double lossPerLot = (stopDist / tickSize) * tickVal;
      if(lossPerLot <= 0)
         return;
      double lot = NormalizeVolume(sym, riskMoney / lossPerLot);
      if(lot <= 0)
         return;

      TrafficLightResult tlRes = tl.Evaluate(side, 2, atr[0], 0.5, 0, true, spread, barDt.hour, true, true);
      if(!tlRes.allowed)
         return;
      lot = NormalizeVolume(sym, lot * tlRes.riskMultiplier);
      if(lot <= 0)
         return;

      double px = (side > 0) ? SymbolInfoDouble(sym, SYMBOL_ASK) : SymbolInfoDouble(sym, SYMBOL_BID);
      double sl = (side > 0) ? px - stopDist : px + stopDist;
      double tp = (side > 0) ? px + atr[0] * m_tpAtr : px - atr[0] * m_tpAtr;
      if(m_playbookSignalExit)
         tp = (side > 0) ? px + atr[0] * 24.0 : px - atr[0] * 24.0;
      string comment = "SOV-FXMOPOL";
      bool ok = (side > 0) ? trade.Buy(lot, sym, px, sl, tp, comment) : trade.Sell(lot, sym, px, sl, tp, comment);
      if(ok)
      {
         m_dayCount++;
         string feats = StringFormat("setup=%s;dow=%d;hour=%d", entryTag, barDt.day_of_week, barDt.hour);
         perf.RegisterOrder(trade.ResultOrder(), featBucket, riskMoney, side, 0, 0.0, feats);
      }
   }
};

#endif
