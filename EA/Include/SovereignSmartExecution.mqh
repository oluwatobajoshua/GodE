//+------------------------------------------------------------------+
//|                                         SovereignSmartExecution.mqh|
//|  Smart execution engine for the Sovereign EA:                    |
//|  - TWAP slicing (split large orders into child orders over time) |
//|  - Slippage model (spread + volatility-adjusted limit prices)    |
//|  - Iceberg (display only a fraction of order size)               |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_SMART_EXECUTION_MQH__
#define __SOVEREIGN_SMART_EXECUTION_MQH__

#include <Trade\Trade.mqh>
#include <Sovereign\SovereignError.mqh>

#define SOV_EXEC_MAX_CHILDREN 16
#define SOV_EXEC_MAX_ORDERS   64

enum SovExecMode
{
   SOV_EXEC_IMMEDIATE = 0,   // single market order (default)
   SOV_EXEC_TWAP        = 1,   // time-weighted average price slicing
   SOV_EXEC_ICEBERG     = 2,   // show only peak size, replenish
   SOV_EXEC_ADAPTIVE    = 3    // TWAP + slow down when spread/vol spikes
};

struct SovExecChild
{
   ulong    ticket;
   double   lots;
   double   price;
   int      side;
   bool     filled;
   datetime submitTime;
   string   symbol;
};

struct SovExecSlice
{
   ulong    parentTicket;
   string   symbol;
   int      side;
   double   totalLots;
   double   filledLots;
   double   remainingLots;
   int      mode;
   int      nChildren;
   int      childIdx;
   double   sliceIntervalSec;
   datetime nextSliceTime;
   double   slippagePts;
   double   avgFillPrice;
   int      fills;
   bool     active;
   bool     done;
   SovExecChild children[SOV_EXEC_MAX_CHILDREN];
};

class CSovereignSmartExecution
{
private:
   string   m_folder;
   bool     m_enabled;
   SovExecSlice m_slices[SOV_EXEC_MAX_ORDERS];
   int      m_nSlices;

   int      m_defaultChildren;
   int      m_sliceIntervalSec;
   double   m_slippageMult;
   bool     m_iceberg;
   double   m_icebergShowPct;
   double   m_adaptiveSlowdown;

   int FindSlice(const ulong parentTicket) const;
   int EnsureSlice(const string symbol, const int side, const double totalLots,
                   const SovExecMode mode);
   double EstimateSlippage(const string symbol, const double lots,
                           const double atr) const;
   double SlippageAdjustedPrice(const string symbol, const int side,
                                const double refPrice, const double slippagePts) const;
   double NormalizeVolume(const string symbol, const double volume) const;

public:
   CSovereignSmartExecution();
   ~CSovereignSmartExecution();
   bool Init(const string folder,
             const int defaultChildren, const int sliceIntervalSec,
             const double slippageMult, const bool iceberg,
             const double icebergShowPct, const double adaptiveSlowdown);
   void Deinit();
   void OnTick();

   ulong Submit(const string symbol, const int side, const double lots,
                const double price, const SovExecMode mode,
                CTrade &t, CSovereignAuditLog &audit);

   bool SliceActive(const ulong parentTicket) const;
   double SliceFilledLots(const ulong parentTicket) const;
   double SliceAvgPrice(const ulong parentTicket) const;
   bool SliceDone(const ulong parentTicket) const;

   bool CancelSlice(const ulong parentTicket, CTrade &t);

   string StatusLine() const;
};

CSovereignSmartExecution::CSovereignSmartExecution()
{
   m_folder = "Sovereign";
   m_enabled = false;
   m_nSlices = 0;
   m_defaultChildren = 1;
   m_sliceIntervalSec = 60;
   m_slippageMult = 0.5;
   m_iceberg = false;
   m_icebergShowPct = 0.25;
   m_adaptiveSlowdown = 2.0;
}
CSovereignSmartExecution::~CSovereignSmartExecution()
{
   Deinit();
}
bool CSovereignSmartExecution::Init(const string folder,
                                    const int defaultChildren,
                                    const int sliceIntervalSec,
                                    const double slippageMult,
                                    const bool iceberg,
                                    const double icebergShowPct,
                                    const double adaptiveSlowdown)
{
   m_folder = folder;
   m_enabled = true;
   m_defaultChildren = MathMax(1, defaultChildren);
   m_sliceIntervalSec = MathMax(10, sliceIntervalSec);
   m_slippageMult = MathMax(0.0, slippageMult);
   m_iceberg = iceberg;
   m_icebergShowPct = MathMax(0.01, MathMin(1.0, icebergShowPct));
   m_adaptiveSlowdown = MathMax(1.0, adaptiveSlowdown);
   m_nSlices = 0;
   return true;
}
void CSovereignSmartExecution::Deinit()
{
   m_enabled = false;
   m_nSlices = 0;
}
void CSovereignSmartExecution::OnTick()
{
   if(!m_enabled) return;

   for(int i = 0; i < m_nSlices; i++)
   {
      if(!m_slices[i].active || m_slices[i].done) continue;

      double filled = 0.0;
      double fillSum = 0.0;
      int nFill = 0;
      for(int c = 0; c < m_slices[i].nChildren; c++)
      {
         if(!m_slices[i].children[c].filled)
         {
            ulong tk = m_slices[i].children[c].ticket;
            if(tk > 0)
            {
               if(OrderSelect(tk))
               {
                  long state = (long)OrderGetInteger(ORDER_STATE);
                  if((state & ORDER_STATE_FILLED) != 0)
                  {
                     m_slices[i].children[c].filled = true;
                     double fillVol = (double)OrderGetDouble(ORDER_VOLUME_INITIAL);
                     double fillPx = (double)OrderGetDouble(ORDER_PRICE_OPEN);
                     filled += fillVol;
                     fillSum += fillVol * fillPx;
                     nFill++;
                  }
               }
               else if(PositionSelectByTicket(tk))
               {
                  m_slices[i].children[c].filled = true;
                  double fillVol = (double)PositionGetDouble(POSITION_VOLUME);
                  double fillPx = (double)PositionGetDouble(POSITION_PRICE_OPEN);
                  filled += fillVol;
                  fillSum += fillVol * fillPx;
                  nFill++;
               }
            }
         }
      }
      m_slices[i].filledLots += filled;
      if(nFill > 0)
      {
         m_slices[i].avgFillPrice = (m_slices[i].avgFillPrice * m_slices[i].fills + fillSum) /
                                    (m_slices[i].fills + filled);
         m_slices[i].fills += nFill;
      }
      m_slices[i].remainingLots = m_slices[i].totalLots - m_slices[i].filledLots;

      if(m_slices[i].mode == SOV_EXEC_TWAP || m_slices[i].mode == SOV_EXEC_ADAPTIVE)
      {
         double interval = m_slices[i].sliceIntervalSec;
         if(m_slices[i].mode == SOV_EXEC_ADAPTIVE)
         {
            int spread = (int)SymbolInfoInteger(m_slices[i].symbol, SYMBOL_SPREAD);
            if(spread > 20) interval *= m_adaptiveSlowdown;
         }
         if(TimeCurrent() >= m_slices[i].nextSliceTime && m_slices[i].childIdx < m_slices[i].nChildren)
         {
            if(!m_slices[i].children[m_slices[i].childIdx].filled &&
               m_slices[i].children[m_slices[i].childIdx].ticket == 0)
            {
               double childLots = m_slices[i].totalLots / (double)m_slices[i].nChildren;
               if(m_iceberg)
                  childLots *= m_icebergShowPct;
               childLots = NormalizeVolume(m_slices[i].symbol, childLots);
               if(childLots > 0.0)
               {
                  double refPx = (m_slices[i].side > 0) ?
                                 SymbolInfoDouble(m_slices[i].symbol, SYMBOL_ASK) :
                                 SymbolInfoDouble(m_slices[i].symbol, SYMBOL_BID);
                  double slip = EstimateSlippage(m_slices[i].symbol, childLots, 0.0);
                  double px = SlippageAdjustedPrice(m_slices[i].symbol, m_slices[i].side,
                                                    refPx, slip);
                  CTrade childTrade;
                  childTrade.SetDeviationInPoints(3);
                  ulong childTicket = 0;
                  if(m_slices[i].side > 0)
                     childTicket = (ulong)childTrade.BuyLimit(childLots, m_slices[i].symbol, px,
                                                              0, 0, ORDER_TIME_GTC, 0, "SOV-TWAP");
                  else
                     childTicket = (ulong)childTrade.SellLimit(childLots, m_slices[i].symbol, px,
                                                               0, 0, ORDER_TIME_GTC, 0, "SOV-TWAP");
                  if(childTicket > 0)
                  {
                     m_slices[i].children[m_slices[i].childIdx].ticket = childTicket;
                     m_slices[i].children[m_slices[i].childIdx].lots = childLots;
                     m_slices[i].children[m_slices[i].childIdx].price = px;
                     m_slices[i].children[m_slices[i].childIdx].side = m_slices[i].side;
                     m_slices[i].children[m_slices[i].childIdx].symbol = m_slices[i].symbol;
                     m_slices[i].children[m_slices[i].childIdx].submitTime = TimeCurrent();
                  }
               }
               m_slices[i].childIdx++;
            }
            m_slices[i].nextSliceTime = TimeCurrent() + (long)interval;
         }
      }

      if(m_slices[i].filledLots >= m_slices[i].totalLots ||
         (m_slices[i].childIdx >= m_slices[i].nChildren && m_slices[i].remainingLots <= 0.0))
      {
         m_slices[i].done = true;
         m_slices[i].active = false;
      }
   }
   int write = 0;
   for(int i = 0; i < m_nSlices; i++)
   {
      if(!m_slices[i].done || m_slices[i].active)
         m_slices[write++] = m_slices[i];
   }
   m_nSlices = write;
}
ulong CSovereignSmartExecution::Submit(const string symbol, const int side,
                                        const double lots, const double price,
                                        const SovExecMode mode,
                                        CTrade &t, CSovereignAuditLog &audit)
{
   if(!m_enabled) return 0;
   if(mode == SOV_EXEC_IMMEDIATE || m_defaultChildren <= 1)
   {
      t.SetDeviationInPoints(3);
      bool ok = (side > 0) ? t.Buy(lots, symbol, price, 0, 0, "SOV-IMM")
                           : t.Sell(lots, symbol, price, 0, 0, "SOV-IMM");
      if(ok)
      {
         int spread = (int)SymbolInfoInteger(symbol, SYMBOL_SPREAD);
         audit.LogOrderAttempt((side > 0) ? "BUY" : "SELL", "MARKET",
                               price, price, lots, spread,
                               (uint)t.ResultRetcode(), "SOV-IMM",
                               false, "", 0.0, 0.0);
         return (ulong)t.ResultOrder();
      }
      return 0;
   }
   int idx = EnsureSlice(symbol, side, lots, mode);
   if(idx < 0) return 0;
   m_slices[idx].active = true;
   m_slices[idx].done = false;
   m_slices[idx].parentTicket = (ulong)(TimeCurrent() * 1000 + idx);
   m_slices[idx].nextSliceTime = TimeCurrent();

   double childLots = m_slices[idx].totalLots / (double)m_slices[idx].nChildren;
   if(m_iceberg)
      childLots *= m_icebergShowPct;
   childLots = NormalizeVolume(symbol, childLots);
   if(childLots > 0.0)
   {
      double slip = EstimateSlippage(symbol, childLots, 0.0);
      double px = SlippageAdjustedPrice(symbol, side, price, slip);
      CTrade childTrade;
      childTrade.SetDeviationInPoints(3);
      ulong childTicket = 0;
      if(side > 0)
         childTicket = (ulong)childTrade.BuyLimit(childLots, symbol, px,
                                                  0, 0, ORDER_TIME_GTC, 0, "SOV-TWAP");
      else
         childTicket = (ulong)childTrade.SellLimit(childLots, symbol, px,
                                                   0, 0, ORDER_TIME_GTC, 0, "SOV-TWAP");
      if(childTicket > 0)
      {
         m_slices[idx].children[0].ticket = childTicket;
         m_slices[idx].children[0].lots = childLots;
         m_slices[idx].children[0].price = px;
         m_slices[idx].children[0].side = side;
         m_slices[idx].children[0].symbol = symbol;
         m_slices[idx].children[0].submitTime = TimeCurrent();
         m_slices[idx].childIdx = 1;
      }
   }
   return m_slices[idx].parentTicket;
}
bool CSovereignSmartExecution::SliceActive(const ulong parentTicket) const
{
   int idx = FindSlice(parentTicket);
   return (idx >= 0 && m_slices[idx].active && !m_slices[idx].done);
}
double CSovereignSmartExecution::SliceFilledLots(const ulong parentTicket) const
{
   int idx = FindSlice(parentTicket);
   return (idx >= 0) ? m_slices[idx].filledLots : 0.0;
}
double CSovereignSmartExecution::SliceAvgPrice(const ulong parentTicket) const
{
   int idx = FindSlice(parentTicket);
   return (idx >= 0) ? m_slices[idx].avgFillPrice : 0.0;
}
bool CSovereignSmartExecution::SliceDone(const ulong parentTicket) const
{
   int idx = FindSlice(parentTicket);
   return (idx >= 0 && m_slices[idx].done);
}
bool CSovereignSmartExecution::CancelSlice(const ulong parentTicket, CTrade &t)
{
   int idx = FindSlice(parentTicket);
   if(idx < 0) return false;
   bool any = false;
   for(int c = 0; c < m_slices[idx].nChildren; c++)
   {
      if(!m_slices[idx].children[c].filled && m_slices[idx].children[c].ticket > 0)
      {
         if(t.OrderDelete(m_slices[idx].children[c].ticket)) any = true;
      }
   }
   m_slices[idx].active = false;
   m_slices[idx].done = true;
   return any;
}
string CSovereignSmartExecution::StatusLine() const
{
   if(!m_enabled) return "exec=off";
   int active = 0;
   for(int i = 0; i < m_nSlices; i++)
      if(m_slices[i].active && !m_slices[i].done) active++;
   return StringFormat("exec slices=%d active=%d", m_nSlices, active);
}
int CSovereignSmartExecution::FindSlice(const ulong parentTicket) const
{
   for(int i = 0; i < m_nSlices; i++)
      if(m_slices[i].parentTicket == parentTicket) return i;
   return -1;
}
int CSovereignSmartExecution::EnsureSlice(const string symbol, const int side,
                                           const double totalLots,
                                           const SovExecMode mode)
{
   if(m_nSlices >= SOV_EXEC_MAX_ORDERS) return -1;
   int idx = m_nSlices++;
   m_slices[idx].parentTicket = 0;
   m_slices[idx].symbol = symbol;
   m_slices[idx].side = side;
   m_slices[idx].totalLots = totalLots;
   m_slices[idx].filledLots = 0.0;
   m_slices[idx].remainingLots = totalLots;
   m_slices[idx].mode = mode;
   m_slices[idx].nChildren = m_defaultChildren;
   m_slices[idx].childIdx = 0;
   m_slices[idx].sliceIntervalSec = m_sliceIntervalSec;
   m_slices[idx].nextSliceTime = 0;
   m_slices[idx].slippagePts = 0.0;
   m_slices[idx].avgFillPrice = 0.0;
   m_slices[idx].fills = 0;
   m_slices[idx].active = false;
   m_slices[idx].done = false;
   for(int c = 0; c < SOV_EXEC_MAX_CHILDREN; c++)
   {
      m_slices[idx].children[c].ticket = 0;
      m_slices[idx].children[c].lots = 0.0;
      m_slices[idx].children[c].price = 0.0;
      m_slices[idx].children[c].side = 0;
      m_slices[idx].children[c].filled = false;
      m_slices[idx].children[c].submitTime = 0;
      m_slices[idx].children[c].symbol = "";
   }
   return idx;
}
double CSovereignSmartExecution::EstimateSlippage(const string symbol,
                                                   const double lots,
                                                   const double atr) const
{
   int spread = (int)SymbolInfoInteger(symbol, SYMBOL_SPREAD);
   double slip = (double)spread * 0.5;
   if(atr > 0.0) slip += atr * m_slippageMult;
   return slip;
}
double CSovereignSmartExecution::SlippageAdjustedPrice(const string symbol,
                                                        const int side,
                                                        const double refPrice,
                                                        const double slippagePts) const
{
   SovResetLastError();
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   if(point <= 0.0) point = 0.00001;
   double adj = slippagePts * point;
   if(side > 0)
      return refPrice + adj;
   else
      return refPrice - adj;
}
double CSovereignSmartExecution::NormalizeVolume(const string symbol,
                                                  const double volume) const
{
   SovResetLastError();
   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0.0) step = 0.01;
   double v = MathFloor(volume / step) * step;
   if(v < vmin) return 0.0;
   if(v > vmax) v = vmax;
   return v;
}
#endif // __SOVEREIGN_SMART_EXECUTION_MQH__
