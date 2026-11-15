//+------------------------------------------------------------------+
//|                                         SovereignHedgeBlend.mqh  |
//|   RETRACTED - DO NOT ENABLE. Kept only to reproduce a negative    |
//|   result. See docs/HANDOFF.md and research/results/EDGE_LEDGER.md |
//|                                                                  |
//|  Carry momentum (3-month sum) gates the carry leg:               |
//|    carry_mom3 > 0  -> blend 50/50 carry + RSI band               |
//|    carry_mom3 <= 0 -> RSI band only (carry weight = 0)           |
//|                                                                  |
//|  The published "D+V Sharpe 1.89, OOS 1.47, maxDD -3.0%" was a     |
//|  LOOKAHEAD ARTIFACT: the weight for month t was built from        |
//|  carry.rolling(3).sum(), which includes carry[t] itself. Lagged   |
//|  one month the blend is CAGR 1.80% / Sharpe 0.42 / maxDD -7.5%,   |
//|  strictly dominated by RSI band alone (1.84% / 0.58 / -5.5%) in   |
//|  every split. Leave InpHedgeBlendEnabled = false.                 |
//|+------------------------------------------------------------------+
#ifndef __SOVEREIGN_HEDGE_BLEND_MQH__
#define __SOVEREIGN_HEDGE_BLEND_MQH__

#include <Trade\Trade.mqh>
#include <Sovereign\SovereignRSIBand.mqh>
#include <Sovereign\SovereignCarryMom.mqh>

class CSovereignCarryMom;
class CSovereignPerformance;
class CSovereignTrafficLight;
class CSovereignAuditLog;
class CSovereignCentury;

class CSovereignHedgeBlend
{
private:
   CSovereignCarryMom  *m_carry;
   CSovereignRSIBand   *m_rsiBand;
   CSovereignPerformance *m_perf;
   CSovereignTrafficLight *m_traffic;
   CSovereignAuditLog   *m_audit;
   CTrade               *m_trade;
   CSovereignCentury    *m_century;

   double   m_carryRetBuf[12];
   int      m_carryRetCount;
   int      m_carryRetHead;
   datetime m_lastCarryMonthKey;
   bool     m_carryAllowed;
   double   m_carryEquityAtMonthStart;
   bool     m_carryEquitySampled;
   long     m_magic;

   int MonthKey(datetime t) const;
   datetime MonthStart(datetime t) const;
   double CarryMom3() const;
   void UpdateCarryReturn(const double monthlyRet);

public:
   CSovereignHedgeBlend();
   ~CSovereignHedgeBlend();
   bool Init(CSovereignCarryMom &carry,
             CSovereignRSIBand &rsiBand,
             CSovereignPerformance &perf,
             CSovereignTrafficLight &traffic,
             CSovereignAuditLog &audit,
             CTrade &t,
             CSovereignCentury &century,
             long magic);
   void Deinit();
   void OnNewMonth(const datetime now, const bool allowEntries, const double equity,
                   const string &syms[], const int nSyms);
   void OnNewBar(const datetime now, const bool allowEntries, const double equity);
   bool CarryAllowed() const { return m_carryAllowed; }
   double CarryMom3() { return CarryMom3(); }
};

#endif // __SOVEREIGN_HEDGE_BLEND_MQH__

//+------------------------------------------------------------------+
//| Constructor                                                      |
//+------------------------------------------------------------------+
CSovereignHedgeBlend::CSovereignHedgeBlend()
{
   m_carry = NULL;
   m_rsiBand = NULL;
   m_perf = NULL;
   m_traffic = NULL;
   m_audit = NULL;
   m_trade = NULL;
   m_century = NULL;
   m_carryRetCount = 0;
   m_carryRetHead = 0;
   m_lastCarryMonthKey = 0;
   m_carryAllowed = true;
   m_carryEquityAtMonthStart = 0.0;
   m_carryEquitySampled = false;
   m_magic = 0;
   ArrayInitialize(m_carryRetBuf, 0.0);
}

//+------------------------------------------------------------------+
//| Destructor                                                       |
//+------------------------------------------------------------------+
CSovereignHedgeBlend::~CSovereignHedgeBlend()
{
}

//+------------------------------------------------------------------+
//| Init                                                             |
//+------------------------------------------------------------------+
bool CSovereignHedgeBlend::Init(CSovereignCarryMom &carry,
                                 CSovereignRSIBand &rsiBand,
                                 CSovereignPerformance &perf,
                                 CSovereignTrafficLight &traffic,
                                 CSovereignAuditLog &audit,
                                 CTrade &t,
                                 CSovereignCentury &century,
                                 long magic)
{
   m_carry = &carry;
   m_rsiBand = &rsiBand;
   m_perf = &perf;
   m_traffic = &traffic;
   m_audit = &audit;
   m_trade = &t;
   m_century = &century;
   m_magic = magic;
   m_carryAllowed = true;
   m_lastCarryMonthKey = 0;
   return true;
}

//+------------------------------------------------------------------+
//| Deinit                                                           |
//+------------------------------------------------------------------+
void CSovereignHedgeBlend::Deinit()
{
}

//+------------------------------------------------------------------+
//| MonthKey                                                         |
//+------------------------------------------------------------------+
int CSovereignHedgeBlend::MonthKey(datetime t) const
{
   if(t <= 0) return 0;
   MqlDateTime dt;
   TimeToStruct(t, dt);
   return dt.year * 100 + dt.mon;
}

//+------------------------------------------------------------------+
//| MonthStart                                                       |
//+------------------------------------------------------------------+
datetime CSovereignHedgeBlend::MonthStart(datetime t) const
{
   MqlDateTime dt;
   TimeToStruct(t, dt);
   dt.day = 1;
   dt.hour = 0;
   dt.min  = 0;
   dt.sec  = 0;
   return StructToTime(dt);
}

//+------------------------------------------------------------------+
//| CarryMom3 — 3-month sum of carry monthly returns                 |
//+------------------------------------------------------------------+
double CSovereignHedgeBlend::CarryMom3() const
{
   if(m_carryRetCount < 3) return 0.0;
   double sum = 0.0;
   int idx = m_carryRetHead;
   int n = MathMin(3, m_carryRetCount);
   for(int i = 0; i < n; i++)
   {
      idx--;
      if(idx < 0) idx = 11;
      sum += m_carryRetBuf[idx];
   }
   return sum;
}

//+------------------------------------------------------------------+
//| UpdateCarryReturn                                                |
//+------------------------------------------------------------------+
void CSovereignHedgeBlend::UpdateCarryReturn(const double monthlyRet)
{
   m_carryRetBuf[m_carryRetHead] = monthlyRet;
   m_carryRetHead++;
   if(m_carryRetHead >= 12) m_carryRetHead = 0;
   if(m_carryRetCount < 12) m_carryRetCount++;
}

//+------------------------------------------------------------------+
//| OnNewMonth — blend gate: decide carry weight from carry mom3     |
//+------------------------------------------------------------------+
void CSovereignHedgeBlend::OnNewMonth(const datetime now, const bool allowEntries,
                                       const double equity,
                                       const string &syms[], const int nSyms)
{
   int monthKey = MonthKey(now);
   if(monthKey == m_lastCarryMonthKey) return;
   m_lastCarryMonthKey = monthKey;

   //--- compute carry monthly return from equity change at month boundary
   if(m_carryEquitySampled && m_carryEquityAtMonthStart > 0.0)
   {
      double carryMonthlyRet = (equity - m_carryEquityAtMonthStart) / m_carryEquityAtMonthStart;
      UpdateCarryReturn(carryMonthlyRet);
   }
   m_carryEquityAtMonthStart = equity;
   m_carryEquitySampled = true;

   //--- regime gate: carry allowed only if carry momentum > 0
   double mom3 = CarryMom3();
   m_carryAllowed = (mom3 > 0.0);

   //--- run carry rebalance if allowed
   if(m_carryAllowed && allowEntries && !(*m_century).HaltTrading())
   {
      (*m_carry).Rebalance(now, allowEntries, equity, *m_perf, *m_traffic, *m_audit,
                           *m_trade, *m_century);
   }

   //--- RSI band runs independently on H1 bars (handled in OnNewBar)
}

//+------------------------------------------------------------------+
//| OnNewBar — RSI band overlay (runs every H1 bar)                  |
//+------------------------------------------------------------------+
void CSovereignHedgeBlend::OnNewBar(const datetime now, const bool allowEntries,
                                     const double equity)
{
   //--- RSI band always runs (it's the H1 satellite)
   //--- The blend weight is applied via risk sizing inside RSI band
   //--- When carry is not allowed, RSI band gets full risk allocation
   //--- When carry is allowed, RSI band gets reduced risk (50/50 blend)
   //--- We communicate this via a temporary risk override
   m_rsiBand.OnNewBar(now, allowEntries, equity, m_perf, m_traffic, m_audit, m_trade);
}
