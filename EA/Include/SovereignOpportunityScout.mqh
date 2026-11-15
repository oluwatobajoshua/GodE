//+------------------------------------------------------------------+
//|                              SovereignOpportunityScout.mqh       |
//|  EXPERIMENTAL: short-horizon opportunistic entries on new bar.   |
//|  NOT validated OOS. Does NOT predict "next few seconds" — uses   |
//|  spread, vol, and micro stretch with hard SL/TP/time exit.       |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_OPPORTUNITY_SCOUT_MQH__
#define __SOVEREIGN_OPPORTUNITY_SCOUT_MQH__

#include <Trade/PositionInfo.mqh>

class CSovereignOpportunityScout
{
private:
   long   m_magic;
   int    m_maxSpread;
   double m_riskPct;
   int    m_maxHoldBars;
   double m_tpAtr;
   double m_slAtr;
   int    m_rsiPeriod;
   double m_rsiBuy;
   double m_rsiSell;
   int    m_zBars;
   double m_zEntry;

   datetime m_lastBar;

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
      if(step <= 0) step = 0.01;
      vol = MathFloor(vol / step) * step;
      if(vol < vmin) return 0.0;
      if(vol > vmax) vol = vmax;
      return vol;
   }

   bool MicroSignal(const string sym, const ENUM_TIMEFRAMES tf, int &side, double &atr) const
   {
      side = 0;
      atr = 0.0;
      int rsiHandle = iRSI(sym, tf, m_rsiPeriod, PRICE_CLOSE);
      if(rsiHandle == INVALID_HANDLE) return false;
      double rsiBuf[1];
      ArraySetAsSeries(rsiBuf, true);
      if(CopyBuffer(rsiHandle, 0, 1, 1, rsiBuf) < 1)
      {
         IndicatorRelease(rsiHandle);
         return false;
      }
      double rsi = rsiBuf[0];
      IndicatorRelease(rsiHandle);

      double close[];
      ArraySetAsSeries(close, true);
      if(CopyClose(sym, tf, 1, m_zBars, close) < m_zBars) return false;
      double sum = 0.0;
      for(int i = 0; i < m_zBars; i++) sum += close[i];
      double sma = sum / (double)m_zBars;
      double sq = 0.0;
      for(int i = 0; i < m_zBars; i++)
      {
         double d = close[i] - sma;
         sq += d * d;
      }
      double sd = MathSqrt(sq / MathMax(1.0, m_zBars - 1));
      double z = (sd > 0.0) ? (close[0] - sma) / sd : 0.0;

      int atrHandle = iATR(sym, tf, 14);
      if(atrHandle == INVALID_HANDLE) return false;
      double atrBuf[1];
      ArraySetAsSeries(atrBuf, true);
      if(CopyBuffer(atrHandle, 0, 1, 1, atrBuf) < 1)
      {
         IndicatorRelease(atrHandle);
         return false;
      }
      atr = atrBuf[0];
      IndicatorRelease(atrHandle);
      if(atr <= 0.0) return false;

      bool stretchDown = (rsi <= m_rsiBuy && z <= -m_zEntry);
      bool stretchUp   = (rsi >= m_rsiSell && z >= m_zEntry);
      if(stretchDown) side = 1;
      else if(stretchUp) side = -1;
      return (side != 0);
   }

   int OpenCount() const
   {
      int n = 0;
      CPositionInfo p;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!p.SelectByIndex(i)) continue;
         if(p.Magic() == m_magic) n++;
      }
      return n;
   }

   void ManageExits(const string sym, const ENUM_TIMEFRAMES tf, CTrade &trade)
   {
      CPositionInfo p;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!p.SelectByIndex(i)) continue;
         if(p.Magic() != m_magic || p.Symbol() != sym) continue;
         datetime opened = (datetime)p.Time();
         int shiftOpen = iBarShift(sym, tf, opened, true);
         int shiftNow = iBarShift(sym, tf, TimeCurrent(), true);
         if(shiftOpen < 0 || shiftNow < 0) continue;
         if(shiftOpen - shiftNow >= m_maxHoldBars)
            trade.PositionClose(p.Ticket());
      }
   }

public:
   CSovereignOpportunityScout()
   {
      m_magic = 0;
      m_maxSpread = 20;
      m_riskPct = 0.25;
      m_maxHoldBars = 3;
      m_tpAtr = 0.45;
      m_slAtr = 0.55;
      m_rsiPeriod = 2;
      m_rsiBuy = 15.0;
      m_rsiSell = 85.0;
      m_zBars = 8;
      m_zEntry = 1.2;
      m_lastBar = 0;
   }

   bool Init(const long magic, const int maxSpread, const double riskPct,
             const int maxHoldBars, const double tpAtr, const double slAtr)
   {
      m_magic = magic;
      m_maxSpread = maxSpread;
      m_riskPct = riskPct;
      m_maxHoldBars = MathMax(1, maxHoldBars);
      m_tpAtr = tpAtr;
      m_slAtr = slAtr;
      return true;
   }

   void OnTick(const string sym, const ENUM_TIMEFRAMES tf, const bool allow,
               const double equity, CSovereignPerformance &perf,
               CSovereignTrafficLight &tl, CTrade &trade)
   {
      ManageExits(sym, tf, trade);
      if(!allow) return;
      if(!NewBar(sym, tf)) return;
      if(OpenCount() > 0) return;

      int spread = (int)SymbolInfoInteger(sym, SYMBOL_SPREAD);
      if(spread > m_maxSpread) return;

      int side = 0;
      double atr = 0.0;
      if(!MicroSignal(sym, tf, side, atr)) return;

      string bucket = CSovereignPerformance::LegBucket("SOV-SCOUT", sym);
      double mult = perf.CombinedMultiplier(bucket);
      if(mult <= 0.0) return;

      double tickSize = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
      double tickVal = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
      if(tickSize <= 0 || tickVal <= 0) return;

      double riskMoney = equity * m_riskPct / 100.0 * mult;
      double stopDist = atr * m_slAtr;
      double lossPerLot = (stopDist / tickSize) * tickVal;
      if(lossPerLot <= 0) return;
      double lot = NormalizeVolume(sym, riskMoney / lossPerLot);
      if(lot <= 0) return;

      TrafficLightResult tlRes = tl.Evaluate(side, 2, atr, 0.5, 0,
                                            true, spread, 12, true, true);
      if(!tlRes.allowed) return;
      lot = NormalizeVolume(sym, lot * tlRes.riskMultiplier);
      if(lot <= 0) return;

      double px = (side > 0) ? SymbolInfoDouble(sym, SYMBOL_ASK)
                             : SymbolInfoDouble(sym, SYMBOL_BID);
      if(px <= 0) return;
      double sl = (side > 0) ? px - stopDist : px + stopDist;
      double tp = (side > 0) ? px + atr * m_tpAtr : px - atr * m_tpAtr;

      string cmt = StringFormat("SOV-SCOUT tf=%d side=%d", (int)tf, side);
      bool ok = (side > 0) ? trade.Buy(lot, sym, px, sl, tp, cmt)
                           : trade.Sell(lot, sym, px, sl, tp, cmt);
      if(ok)
         perf.RegisterOrder(trade.ResultOrder(), bucket, riskMoney, side, 2, 0.0, "");
   }
};

#endif
