//+------------------------------------------------------------------+
//|                                   SovereignEmaTrend.mqh          |
//|  Trade only with EMA slope + price side; pullbacks/continuations. |
//|  EXPERIMENTAL — intraday trend stacks failed OOS in this repo.     |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_EMA_TREND_MQH__
#define __SOVEREIGN_EMA_TREND_MQH__

#include <Trade/PositionInfo.mqh>
#include <Sovereign/SovereignTrafficLight.mqh>
#include <Sovereign/SovereignPerformance.mqh>

class CSovereignEmaTrend
{
private:
   long              m_magic;
   int               m_maxSpread;
   double            m_riskPct;
   int               m_emaPeriod;
   int               m_slopeBars;
   int               m_entryMode;
   double            m_pullbackAtr;
   double            m_minAdx;
   bool              m_useHtf;
   ENUM_TIMEFRAMES   m_htf;
   double            m_rsiMinLong;
   double            m_rsiMaxLong;
   double            m_rsiMinShort;
   double            m_rsiMaxShort;
   double            m_slAtr;
   double            m_tpAtr;
   int               m_cooldownBars;
   int               m_maxPositions;
   bool              m_closeOnEmaFlip;

   datetime          m_lastBar;
   datetime          m_lastEntryBar;

   bool NewBar(const string sym, const ENUM_TIMEFRAMES tf)
   {
      datetime t = iTime(sym, tf, 0);
      if(t == 0 || t == m_lastBar)
         return false;
      m_lastBar = t;
      return true;
   }

   double NormalizeVolume(const string sym, double vol) const
   {
      double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
      double vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
      double vmax = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
      if(step <= 0.0)
         step = 0.01;
      vol = MathFloor(vol / step) * step;
      if(vol < vmin)
         return 0.0;
      if(vol > vmax)
         vol = vmax;
      return vol;
   }

   int TrendRegime(const string sym, const ENUM_TIMEFRAMES tf) const
   {
      int need = m_slopeBars + 2;
      int hMa = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      if(hMa == INVALID_HANDLE)
         return 0;
      double ema[];
      ArraySetAsSeries(ema, true);
      if(CopyBuffer(hMa, 0, 1, need, ema) < need)
      {
         IndicatorRelease(hMa);
         return 0;
      }
      IndicatorRelease(hMa);

      double close[];
      ArraySetAsSeries(close, true);
      if(CopyClose(sym, tf, 1, 1, close) < 1)
         return 0;

      bool up = (close[0] > ema[0] && ema[0] > ema[m_slopeBars]);
      bool dn = (close[0] < ema[0] && ema[0] < ema[m_slopeBars]);
      if(up)
         return 1;
      if(dn)
         return -1;
      return 0;
   }

   bool AdxOk(const string sym, const ENUM_TIMEFRAMES tf) const
   {
      int h = iADX(sym, tf, 14);
      if(h == INVALID_HANDLE)
         return true;
      double buf[1];
      ArraySetAsSeries(buf, true);
      if(CopyBuffer(h, 0, 1, 1, buf) < 1)
      {
         IndicatorRelease(h);
         return true;
      }
      IndicatorRelease(h);
      return (buf[0] >= m_minAdx);
   }

   bool RsiOk(const string sym, const ENUM_TIMEFRAMES tf, const int side) const
   {
      int h = iRSI(sym, tf, 14, PRICE_CLOSE);
      if(h == INVALID_HANDLE)
         return true;
      double buf[1];
      ArraySetAsSeries(buf, true);
      if(CopyBuffer(h, 0, 1, 1, buf) < 1)
      {
         IndicatorRelease(h);
         return true;
      }
      IndicatorRelease(h);
      double r = buf[0];
      if(side > 0)
         return (r >= m_rsiMinLong && r <= m_rsiMaxLong);
      return (r >= m_rsiMinShort && r <= m_rsiMaxShort);
   }

   bool HtfAligned(const string sym, const ENUM_TIMEFRAMES chartTf, const int side) const
   {
      if(!m_useHtf)
         return true;
      ENUM_TIMEFRAMES htf = m_htf;
      if(PeriodSeconds(htf) <= PeriodSeconds(chartTf))
      {
         if(chartTf == PERIOD_M5 || chartTf == PERIOD_M15)
            htf = PERIOD_H1;
         else if(chartTf == PERIOD_H1)
            htf = PERIOD_H4;
         else
            htf = PERIOD_D1;
      }
      int reg = TrendRegime(sym, htf);
      return (reg == side);
   }

   int OpenCount(const string sym) const
   {
      int n = 0;
      CPositionInfo p;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!p.SelectByIndex(i))
            continue;
         if(p.Magic() != m_magic || p.Symbol() != sym)
            continue;
         n++;
      }
      return n;
   }

   bool PullbackSignal(const string sym, const ENUM_TIMEFRAMES tf, const int regime,
                       const double ema1, const double atr) const
   {
      double open[], high[], low[], close[];
      ArraySetAsSeries(open, true);
      ArraySetAsSeries(high, true);
      ArraySetAsSeries(low, true);
      ArraySetAsSeries(close, true);
      if(CopyOpen(sym, tf, 1, 1, open) < 1)
         return false;
      if(CopyHigh(sym, tf, 1, 1, high) < 1)
         return false;
      if(CopyLow(sym, tf, 1, 1, low) < 1)
         return false;
      if(CopyClose(sym, tf, 1, 1, close) < 1)
         return false;

      double touchBand = atr * m_pullbackAtr;
      if(regime > 0)
      {
         bool touched = (low[0] <= ema1 + touchBand);
         bool bullish = (close[0] > open[0] && close[0] > ema1);
         return (touched && bullish);
      }
      if(regime < 0)
      {
         bool touched = (high[0] >= ema1 - touchBand);
         bool bearish = (close[0] < open[0] && close[0] < ema1);
         return (touched && bearish);
      }
      return false;
   }

   bool BreakSignal(const string sym, const ENUM_TIMEFRAMES tf, const int regime,
                    const double ema1) const
   {
      double high[], low[], close[];
      ArraySetAsSeries(high, true);
      ArraySetAsSeries(low, true);
      ArraySetAsSeries(close, true);
      if(CopyHigh(sym, tf, 1, 2, high) < 2)
         return false;
      if(CopyLow(sym, tf, 1, 2, low) < 2)
         return false;
      if(CopyClose(sym, tf, 1, 1, close) < 1)
         return false;

      if(regime > 0)
         return (close[0] > high[1] && close[0] > ema1);
      if(regime < 0)
         return (close[0] < low[1] && close[0] < ema1);
      return false;
   }

   void ManageExits(const string sym, const ENUM_TIMEFRAMES tf, CTrade &trade)
   {
      if(!m_closeOnEmaFlip)
         return;
      int regime = TrendRegime(sym, tf);
      CPositionInfo p;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!p.SelectByIndex(i))
            continue;
         if(p.Magic() != m_magic || p.Symbol() != sym)
            continue;
         if(p.PositionType() == POSITION_TYPE_BUY && regime <= 0)
            trade.PositionClose(p.Ticket());
         else if(p.PositionType() == POSITION_TYPE_SELL && regime >= 0)
            trade.PositionClose(p.Ticket());
      }
   }

public:
   CSovereignEmaTrend()
   {
      m_magic = 0;
      m_maxSpread = 20;
      m_riskPct = 0.35;
      m_emaPeriod = 50;
      m_slopeBars = 3;
      m_entryMode = 2;
      m_pullbackAtr = 0.35;
      m_minAdx = 18.0;
      m_useHtf = true;
      m_htf = PERIOD_H1;
      m_rsiMinLong = 40.0;
      m_rsiMaxLong = 72.0;
      m_rsiMinShort = 28.0;
      m_rsiMaxShort = 60.0;
      m_slAtr = 1.25;
      m_tpAtr = 1.75;
      m_cooldownBars = 0;
      m_maxPositions = 1;
      m_closeOnEmaFlip = true;
      m_lastBar = 0;
      m_lastEntryBar = 0;
   }

   bool Init(const long magic, const int maxSpread, const double riskPct,
             const int emaPeriod, const int slopeBars, const int entryMode,
             const double pullbackAtr, const double minAdx, const bool useHtf,
             const ENUM_TIMEFRAMES htf, const double slAtr, const double tpAtr,
             const int cooldownBars, const int maxPositions, const bool closeOnFlip)
   {
      m_magic = magic;
      m_maxSpread = maxSpread;
      m_riskPct = riskPct;
      m_emaPeriod = MathMax(5, emaPeriod);
      m_slopeBars = MathMax(1, slopeBars);
      m_entryMode = entryMode;
      m_pullbackAtr = MathMax(0.05, pullbackAtr);
      m_minAdx = minAdx;
      m_useHtf = useHtf;
      m_htf = htf;
      m_slAtr = slAtr;
      m_tpAtr = tpAtr;
      m_cooldownBars = MathMax(0, cooldownBars);
      m_maxPositions = MathMax(1, maxPositions);
      m_closeOnEmaFlip = closeOnFlip;
      return true;
   }

   void OnTick(const string sym, const ENUM_TIMEFRAMES tf, const bool allow,
               const double equity, CSovereignPerformance &perf,
               CSovereignTrafficLight &tl, CTrade &trade)
   {
      ManageExits(sym, tf, trade);
      if(!allow)
         return;
      if(!NewBar(sym, tf))
         return;
      if(OpenCount(sym) >= m_maxPositions)
         return;

      int regime = TrendRegime(sym, tf);
      if(regime == 0)
         return;
      if(!AdxOk(sym, tf))
         return;
      if(!HtfAligned(sym, tf, regime))
         return;
      if(!RsiOk(sym, tf, regime))
         return;

      int hMa = iMA(sym, tf, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      if(hMa == INVALID_HANDLE)
         return;
      double emaBuf[1];
      ArraySetAsSeries(emaBuf, true);
      if(CopyBuffer(hMa, 0, 1, 1, emaBuf) < 1)
      {
         IndicatorRelease(hMa);
         return;
      }
      double ema1 = emaBuf[0];
      IndicatorRelease(hMa);

      int atrHandle = iATR(sym, tf, 14);
      if(atrHandle == INVALID_HANDLE)
         return;
      double atrBuf[1];
      ArraySetAsSeries(atrBuf, true);
      if(CopyBuffer(atrHandle, 0, 1, 1, atrBuf) < 1)
      {
         IndicatorRelease(atrHandle);
         return;
      }
      double atr = atrBuf[0];
      IndicatorRelease(atrHandle);
      if(atr <= 0.0)
         return;

      bool fire = false;
      if(m_entryMode == 0)
         fire = PullbackSignal(sym, tf, regime, ema1, atr);
      else if(m_entryMode == 1)
         fire = BreakSignal(sym, tf, regime, ema1);
      else
         fire = PullbackSignal(sym, tf, regime, ema1, atr) || BreakSignal(sym, tf, regime, ema1);

      if(!fire)
         return;

      datetime bar1 = iTime(sym, tf, 1);
      if(m_cooldownBars > 0 && m_lastEntryBar > 0)
      {
         int shiftLast = iBarShift(sym, tf, m_lastEntryBar, true);
         int shiftNow = iBarShift(sym, tf, bar1, true);
         if(shiftLast >= 0 && shiftNow >= 0 && (shiftLast - shiftNow) < m_cooldownBars)
            return;
      }

      int spread = (int)SymbolInfoInteger(sym, SYMBOL_SPREAD);
      if(spread > m_maxSpread)
         return;

      string bucket = CSovereignPerformance::LegBucket("SOV-EMA", sym);
      if(!perf.AllowNewRisk(bucket))
         return;
      double mult = perf.CombinedMultiplier(bucket);
      if(mult <= 0.0)
         return;

      double stopDist = atr * m_slAtr;
      double tickSize = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
      double tickVal = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
      if(tickSize <= 0.0 || tickVal <= 0.0)
         return;
      double riskMoney = equity * m_riskPct / 100.0 * mult;
      double lossPerLot = (stopDist / tickSize) * tickVal;
      if(lossPerLot <= 0.0)
         return;
      double lot = NormalizeVolume(sym, riskMoney / lossPerLot);
      if(lot <= 0.0)
         return;

      MqlDateTime utc;
      TimeToStruct(TimeCurrent(), utc);
      TrafficLightResult tlRes = tl.Evaluate(regime, 2, atr, 0.5, 0,
                                            true, spread, utc.hour, true, true);
      if(!tlRes.allowed)
         return;
      lot = NormalizeVolume(sym, lot * tlRes.riskMultiplier);
      if(lot <= 0.0)
         return;

      double px = (regime > 0) ? SymbolInfoDouble(sym, SYMBOL_ASK) : SymbolInfoDouble(sym, SYMBOL_BID);
      if(px <= 0.0)
         return;
      double sl = (regime > 0) ? px - stopDist : px + stopDist;
      double tp = (regime > 0) ? px + atr * m_tpAtr : px - atr * m_tpAtr;
      string comment = StringFormat("SOV-EMA reg=%d", regime);
      bool ok = false;
      if(regime > 0)
         ok = trade.Buy(lot, sym, px, sl, tp, comment);
      else
         ok = trade.Sell(lot, sym, px, sl, tp, comment);
      if(ok)
      {
         m_lastEntryBar = bar1;
         perf.RegisterOrder(trade.ResultOrder(), bucket, riskMoney, regime, 0, 0.0, "");
      }
   }
};

#endif
