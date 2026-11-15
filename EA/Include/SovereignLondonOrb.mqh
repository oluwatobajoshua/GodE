//+------------------------------------------------------------------+
//|                               SovereignLondonOrb.mqh              |
//| London pre-open box breakout — matches research/scalp_lane_m5.py   |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_LONDON_ORB_MQH__
#define __SOVEREIGN_LONDON_ORB_MQH__

#include <Trade/Trade.mqh>
#include <Trade/PositionInfo.mqh>
#include <Sovereign/SovereignPerformance.mqh>

class CSovereignLondonOrb
{
private:
   long   m_magic;
   int    m_maxSpread;
   double m_riskPct;
   int    m_boxH0;
   int    m_boxH1;
   int    m_tradeH0;
   int    m_tradeH1;
   int    m_utcOffset;
   double m_bufAtr;
   double m_tpR;
   int    m_maxHoldBars;
   int    m_maxTradesDay;
   int    m_maxPositions;
   double m_minBoxSpreadMult;

   datetime m_lastBar;
   datetime m_boxDay;
   double   m_boxHi;
   double   m_boxLo;
   bool     m_boxReady;
   int      m_dayTrades;
   datetime m_tradeDay;
   datetime m_entryBarTime;
   int      m_holdBars;

   //--- last evaluated breakout, exposed read-only for chart painting
   int      m_lastSide;
   double   m_lastEntry;
   double   m_lastSl;
   double   m_lastTp;
   datetime m_lastSignalTime;
   bool     m_haveSignal;

   int UtcHour(const datetime t) const
   {
      MqlDateTime dt;
      TimeToStruct(t, dt);
      int h = dt.hour - m_utcOffset;
      while(h < 0) h += 24;
      while(h > 23) h -= 24;
      return h;
   }

   datetime UtcDayStart(const datetime t) const
   {
      MqlDateTime dt;
      TimeToStruct(t, dt);
      dt.hour = 0;
      dt.min = 0;
      dt.sec = 0;
      return StructToTime(dt);
   }

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
      if(step <= 0.0) step = 0.01;
      vol = MathFloor(vol / step) * step;
      if(vol < vmin) return 0.0;
      if(vol > vmax) vol = vmax;
      return vol;
   }

   int OpenCount(const string sym) const
   {
      int n = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0 || !PositionSelectByTicket(ticket)) continue;
         if(PositionGetString(POSITION_SYMBOL) != sym) continue;
         if((long)PositionGetInteger(POSITION_MAGIC) != m_magic) continue;
         n++;
      }
      return n;
   }

   void ResetDay(const datetime barTime)
   {
      datetime d = UtcDayStart(barTime);
      if(d != m_tradeDay)
      {
         m_tradeDay = d;
         m_dayTrades = 0;
         m_boxDay = 0;
         m_boxReady = false;
      }
   }

   bool BuildBox(const string sym, const ENUM_TIMEFRAMES tf, const datetime dayStart)
   {
      m_boxHi = -DBL_MAX;
      m_boxLo = DBL_MAX;
      m_boxReady = false;
      int n = Bars(sym, tf);
      if(n < 50) return false;
      int got = 0;
      for(int s = 1; s < n && s < 3000; s++)
      {
         datetime bt = iTime(sym, tf, s);
         if(bt < dayStart)
            break;
         int uh = UtcHour(bt);
         if(uh != m_boxH0 && uh != m_boxH1)
            continue;
         double hi[], lo[];
         ArraySetAsSeries(hi, true);
         ArraySetAsSeries(lo, true);
         if(CopyHigh(sym, tf, s, 1, hi) < 1) continue;
         if(CopyLow(sym, tf, s, 1, lo) < 1) continue;
         if(hi[0] > m_boxHi) m_boxHi = hi[0];
         if(lo[0] < m_boxLo) m_boxLo = lo[0];
         got++;
      }
      if(got < 4 || m_boxHi <= m_boxLo)
         return false;
      m_boxDay = dayStart;
      m_boxReady = true;
      return true;
   }

   double MedianAtr(const string sym, const ENUM_TIMEFRAMES tf) const
   {
      int h = iATR(sym, tf, 14);
      if(h == INVALID_HANDLE) return 0.0;
      double buf[];
      ArraySetAsSeries(buf, true);
      int c = CopyBuffer(h, 0, 1, 48, buf);
      IndicatorRelease(h);
      if(c < 5) return 0.0;
      double sum = 0.0;
      for(int i = 0; i < c; i++) sum += buf[i];
      return sum / c;
   }

public:
   CSovereignLondonOrb() { Clear(); }

   void Clear()
   {
      m_magic = 0;
      m_maxSpread = 30;
      m_riskPct = 0.25;
      m_boxH0 = 5;
      m_boxH1 = 6;
      m_tradeH0 = 7;
      m_tradeH1 = 11;
      m_utcOffset = 0;
      m_bufAtr = 0.15;
      m_tpR = 1.2;
      m_maxHoldBars = 24;
      m_maxTradesDay = 2;
      m_maxPositions = 1;
      m_minBoxSpreadMult = 3.0;
      m_lastBar = 0;
      m_boxDay = 0;
      m_boxHi = 0;
      m_boxLo = 0;
      m_boxReady = false;
      m_dayTrades = 0;
      m_tradeDay = 0;
      m_entryBarTime = 0;
      m_holdBars = 0;
      m_lastSide = 0;
      m_lastEntry = 0.0;
      m_lastSl = 0.0;
      m_lastTp = 0.0;
      m_lastSignalTime = 0;
      m_haveSignal = false;
   }

   bool Init(const long magic, const int maxSpread, const double riskPct,
             const int boxH0, const int boxH1, const int tradeH0, const int tradeH1,
             const int utcOffset, const double bufAtr, const double tpR,
             const int maxHold, const int maxDayTrades, const int maxPos,
             const double minBoxSpreadMult = 3.0)
   {
      m_magic = magic;
      m_maxSpread = maxSpread;
      m_riskPct = riskPct;
      m_boxH0 = boxH0;
      m_boxH1 = boxH1;
      m_tradeH0 = tradeH0;
      m_tradeH1 = tradeH1;
      m_utcOffset = utcOffset;
      m_bufAtr = bufAtr;
      m_tpR = tpR;
      m_maxHoldBars = MathMax(6, maxHold);
      m_maxTradesDay = MathMax(1, maxDayTrades);
      m_maxPositions = MathMax(1, maxPos);
      m_minBoxSpreadMult = MathMax(1.0, minBoxSpreadMult);
      return true;
   }

   //--- read-only state for the chart renderer ----------------------
   double   BoxHigh() const        { return m_boxHi; }
   double   BoxLow() const         { return m_boxLo; }
   bool     BoxReady() const       { return m_boxReady; }
   datetime BoxDay() const         { return m_boxDay; }
   bool     HaveSignal() const     { return m_haveSignal; }
   int      LastSide() const       { return m_lastSide; }
   double   LastEntry() const      { return m_lastEntry; }
   double   LastSl() const         { return m_lastSl; }
   double   LastTp() const         { return m_lastTp; }
   datetime LastSignalTime() const { return m_lastSignalTime; }

   void OnTick(const string sym, const ENUM_TIMEFRAMES tf, const bool allow,
               const double equity, CSovereignPerformance &perf, CTrade &trade)
   {
      const bool nb = NewBar(sym, tf);
      if(nb && OpenCount(sym) > 0)
      {
         m_holdBars++;
         if(m_holdBars >= m_maxHoldBars)
         {
            for(int i = PositionsTotal() - 1; i >= 0; i--)
            {
               ulong ticket = PositionGetTicket(i);
               if(!PositionSelectByTicket(ticket)) continue;
               if(PositionGetString(POSITION_SYMBOL) != sym) continue;
               if((long)PositionGetInteger(POSITION_MAGIC) != m_magic) continue;
               trade.PositionClose(ticket);
            }
            m_holdBars = 0;
         }
      }
      if(!allow || !nb) return;

      datetime bar1 = iTime(sym, tf, 1);
      ResetDay(bar1);
      int uh = UtcHour(bar1);
      if(uh < m_tradeH0 || uh > m_tradeH1)
         return;
      if(OpenCount(sym) >= m_maxPositions)
         return;
      if(m_dayTrades >= m_maxTradesDay)
         return;

      datetime dayStart = UtcDayStart(bar1);
      if(!m_boxReady || m_boxDay != dayStart)
      {
         if(!BuildBox(sym, tf, dayStart))
            return;
      }

      double c[], h[], l[];
      ArraySetAsSeries(c, true);
      ArraySetAsSeries(h, true);
      ArraySetAsSeries(l, true);
      if(CopyClose(sym, tf, 1, 1, c) < 1) return;
      if(CopyHigh(sym, tf, 1, 1, h) < 1) return;
      if(CopyLow(sym, tf, 1, 1, l) < 1) return;

      int side = 0;
      if(h[0] > m_boxHi && c[0] > m_boxHi)
         side = 1;
      else if(l[0] < m_boxLo && c[0] < m_boxLo)
         side = -1;
      if(side == 0) return;

      int spread = (int)SymbolInfoInteger(sym, SYMBOL_SPREAD);
      if(spread > m_maxSpread) return;
      double pt = SymbolInfoDouble(sym, SYMBOL_POINT);
      double boxW = m_boxHi - m_boxLo;
      if(pt > 0.0 && boxW < spread * pt * m_minBoxSpreadMult) return;

      string bucket = CSovereignPerformance::LegBucket("SOV-ORB", sym);
      if(!perf.AllowNewRisk(bucket))
         return;
      double mult = perf.CombinedMultiplier(bucket);
      if(mult <= 0.0) return;

      double atr = MedianAtr(sym, tf);
      if(atr <= 0.0) return;

      double px = (side > 0) ? SymbolInfoDouble(sym, SYMBOL_ASK) : SymbolInfoDouble(sym, SYMBOL_BID);
      if(px <= 0.0) return;
      double sl = (side > 0) ? (m_boxLo - m_bufAtr * atr) : (m_boxHi + m_bufAtr * atr);
      double risk = MathAbs(px - sl);
      if(risk <= 0.0) return;
      double tp = (side > 0) ? (px + risk * m_tpR) : (px - risk * m_tpR);

      //--- publish the evaluated setup for the chart renderer
      m_lastSide       = side;
      m_lastEntry      = px;
      m_lastSl         = sl;
      m_lastTp         = tp;
      m_lastSignalTime = iTime(sym, tf, 1);
      m_haveSignal     = true;

      double tickSize = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
      double tickVal = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
      if(tickSize <= 0.0 || tickVal <= 0.0) return;
      double riskMoney = equity * m_riskPct / 100.0 * mult;
      double lossPerLot = (risk / tickSize) * tickVal;
      if(lossPerLot <= 0.0) return;
      double lot = NormalizeVolume(sym, riskMoney / lossPerLot);
      if(lot <= 0.0) return;

      string comment = StringFormat("SOV-ORB %s", side > 0 ? "L" : "S");
      bool ok = false;
      if(side > 0)
         ok = trade.Buy(lot, sym, px, sl, tp, comment);
      else
         ok = trade.Sell(lot, sym, px, sl, tp, comment);
      if(ok)
      {
         m_dayTrades++;
         m_holdBars = 0;
         m_entryBarTime = iTime(sym, tf, 0);
         perf.RegisterOrder(trade.ResultOrder(), bucket, riskMoney, side, 0, 0.0, "london_orb");
      }
   }
};

#endif
