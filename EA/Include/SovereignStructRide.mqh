//+------------------------------------------------------------------+
//|                                        SovereignStructRide.mqh   |
//|              H1 structure ride overlay (USDJPY certified only)   |
//|                                                                  |
//|  Ride the structure: hold while it makes higher highs/higher     |
//|  lows; exit on failed high / lower low; re-enter on new high.    |
//|  Mirrored for shorts. One position at a time, serial re-entry.   |
//|                                                                  |
//|  Swings are k-bar fractals confirmed k bars late (no lookahead). |
//|  Regime from EMA21 slope / ATR with deadband.                    |
//|  1.5 ATR stop, 2x ATR TP, max 12 bars per position.              |
//|+------------------------------------------------------------------+
#ifndef __SOVEREIGN_STRUCT_RIDE_MQH__
#define __SOVEREIGN_STRUCT_RIDE_MQH__

#include <Trade\Trade.mqh>
#include <Sovereign\SovereignError.mqh>

class CSovereignPerformance;
class CSovereignTrafficLight;
class CSovereignAuditLog;

class CSovereignStructRide
{
private:
   string   m_symbol;
   long     m_magic;
   int      m_k;           // fractal confirmation bars (k=2 confirmed 2 bars late)
   int      m_maxHoldBars; // max bars in a position
   double   m_stopAtr;     // stop distance in ATR units
   double   m_tpAtr;       // take profit distance in ATR units
   double   m_riskPct;     // risk per trade % of equity
   double   m_costPts;     // cost per cycle in points (audit only)
   bool     m_useStructureFilter; // retained for input compat; structure grade retracted

   int      m_hEma21;      // EMA21 indicator handle
   int      m_hAtr14;      // ATR14 indicator handle

   //--- position state
   bool     m_inPosition;
   int      m_positionSide;    // +1 long, -1 short
   double   m_entryPrice;
   double   m_stopPrice;
   double   m_tpPrice;
   double   m_entryAtr;
   int      m_barsHeld;

   //--- swing tracking (confirmed k bars late)
   double   m_lastHigh;        // last confirmed swing high price
   double   m_lastLow;         // last confirmed swing low price
   bool     m_pulledBack;      // pullback seen since last extreme in trade direction

   //--- regime state
   int      m_regime;          // current regime: +1 long, -1 short, 0 flat
   int      m_prevRegime;      // previous regime

   datetime m_lastBarTime;     // last processed bar time (new-bar detection)

   double   m_emaBuf[];
   double   m_atrBuf[];
   double   m_hiBuf[];
   double   m_loBuf[];
   double   m_clBuf[];

   bool IsNewBar();
   bool CopyBuffers();
   int  ComputeRegime();
   bool IsSwingHighAt(const int shift) const;
   bool IsSwingLowAt(const int shift) const;
   double NormalizeVolume(const double volume) const;
   double LotsForRisk(const double riskMoney, const double stopDist) const;
   void OpenPosition(const int side, const double price, const double atr,
                      CTrade &t, CSovereignPerformance &perf,
                      CSovereignTrafficLight &tl, CSovereignAuditLog &audit,
                      const string comment);
   void ClosePosition(CTrade &t, CSovereignAuditLog &audit,
                      const string reason);
   void LogOrderAttempt(const string action, const double price,
                        const double volume, const uint retcode,
                        const string comment, const int spread,
                        CSovereignAuditLog &audit);

public:
   CSovereignStructRide();
   ~CSovereignStructRide();
   bool Init(const string symbol, const long magic,
             const int k, const int maxHoldBars,
             const double stopAtr, const double tpAtr,
             const double riskPct, const double costPts,
             const bool useStructureFilter = true);
   void Deinit();
   void UpdateStructure(const datetime now, const double bid, const double atr);
   void OnNewBar(const datetime now, const bool allowEntries, const double equity,
                 CSovereignPerformance &perf, CSovereignTrafficLight &tl,
                 CSovereignAuditLog &audit, CTrade &t);
};
//+------------------------------------------------------------------+
//| Constructor                                                      |
//+------------------------------------------------------------------+
CSovereignStructRide::CSovereignStructRide()
{
   m_symbol = "";
   m_magic = 0;
   m_k = 2;
   m_maxHoldBars = 12;
   m_stopAtr = 1.5;
   m_tpAtr = 2.0;
   m_riskPct = 0.5;
   m_costPts = 12;
   m_useStructureFilter = true;
   m_hEma21 = INVALID_HANDLE;
   m_hAtr14 = INVALID_HANDLE;
   m_inPosition = false;
   m_positionSide = 0;
   m_entryPrice = 0.0;
   m_stopPrice = 0.0;
   m_tpPrice = 0.0;
   m_entryAtr = 0.0;
   m_barsHeld = 0;
   m_lastHigh = 0.0;
   m_lastLow = 0.0;
   m_pulledBack = false;
   m_regime = 0;
   m_prevRegime = 0;
   m_lastBarTime = 0;
}

//+------------------------------------------------------------------+
//| Destructor                                                       |
//+------------------------------------------------------------------+
CSovereignStructRide::~CSovereignStructRide()
{
   Deinit();
}

//+------------------------------------------------------------------+
//| Init                                                             |
//+------------------------------------------------------------------+
bool CSovereignStructRide::Init(const string symbol, const long magic,
                                const int k, const int maxHoldBars,
                                const double stopAtr, const double tpAtr,
                                const double riskPct, const double costPts,
                                const bool useStructureFilter)
{
   m_symbol = symbol;
   m_magic = magic;
   m_k = MathMax(1, k);
   m_maxHoldBars = MathMax(1, maxHoldBars);
   m_stopAtr = MathMax(0.1, stopAtr);
   m_tpAtr = MathMax(0.1, tpAtr);
   m_riskPct = MathMax(0.01, riskPct);
   m_costPts = MathMax(0.0, costPts);
   m_useStructureFilter = useStructureFilter;

    SovResetLastError();
    m_hEma21 = iMA(m_symbol, PERIOD_H1, 21, 0, MODE_EMA, PRICE_CLOSE);
    SovResetLastError();
    m_hAtr14 = iATR(m_symbol, PERIOD_H1, 14);

    if(m_hEma21 == INVALID_HANDLE || m_hAtr14 == INVALID_HANDLE)
    {
       SovLogError("StructRide: indicator handle failed for " + m_symbol);
       return false;
    }

   m_inPosition = false;
   m_positionSide = 0;
   m_entryPrice = 0.0;
   m_stopPrice = 0.0;
   m_tpPrice = 0.0;
   m_entryAtr = 0.0;
   m_barsHeld = 0;
   m_lastHigh = 0.0;
   m_lastLow = 0.0;
   m_pulledBack = false;
   m_regime = 0;
   m_prevRegime = 0;
   m_lastBarTime = 0;

   return true;
}

//+------------------------------------------------------------------+
//| Deinit                                                           |
//+------------------------------------------------------------------+
void CSovereignStructRide::Deinit()
{
   if(m_hEma21 != INVALID_HANDLE) { IndicatorRelease(m_hEma21); m_hEma21 = INVALID_HANDLE; }
   if(m_hAtr14 != INVALID_HANDLE) { IndicatorRelease(m_hAtr14); m_hAtr14 = INVALID_HANDLE; }
}

//+------------------------------------------------------------------+
//| UpdateStructure — called every tick to cache price/ATR           |
//+------------------------------------------------------------------+
void CSovereignStructRide::UpdateStructure(const datetime now, const double bid, const double atr)
{
   //--- cached for use in OnNewBar; ATR comes from the EA's iATR call
   //--- no per-tick state needed; all decisions happen on new bars
}

//+------------------------------------------------------------------+
//| IsNewBar — detect a new H1 bar                                   |
//+------------------------------------------------------------------+
bool CSovereignStructRide::IsNewBar()
{
   datetime barTime = iTime(m_symbol, PERIOD_H1, 0);
   if(barTime <= 0) return false;
   if(barTime != m_lastBarTime)
   {
      m_lastBarTime = barTime;
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| CopyBuffers — copy completed-bar data (skip forming bar 0)       |
//+------------------------------------------------------------------+
   bool CSovereignStructRide::CopyBuffers()
   {
      int need = MathMax(2 * m_k + 5, 10);

      ArraySetAsSeries(m_emaBuf, true);
      SovResetLastError();
      if(!SovCheckHandle(m_hEma21, "iMA EMA21"))
         return false;
      if(!SovCopyBuffer(m_hEma21, 0, 1, need, m_emaBuf, "EMA21"))
         return false;

      ArraySetAsSeries(m_atrBuf, true);
      SovResetLastError();
      if(!SovCheckHandle(m_hAtr14, "iATR14"))
         return false;
      if(!SovCopyBuffer(m_hAtr14, 0, 1, need, m_atrBuf, "ATR14"))
         return false;

      ArraySetAsSeries(m_hiBuf, true);
      ArraySetAsSeries(m_loBuf, true);
      ArraySetAsSeries(m_clBuf, true);
      if(CopyHigh(m_symbol, PERIOD_H1, 1, need, m_hiBuf) < need)
         return false;
      if(CopyLow(m_symbol, PERIOD_H1, 1, need, m_loBuf) < need)
         return false;
      if(CopyClose(m_symbol, PERIOD_H1, 1, need, m_clBuf) < need)
         return false;

      return true;
   }

//+------------------------------------------------------------------+
//| ComputeRegime — EMA21 slope over 3 bars / ATR, deadbanded        |
//+------------------------------------------------------------------+
int CSovereignStructRide::ComputeRegime()
{
   //--- buffer index 0 = bar 1 (most recent completed), index 3 = bar 4
   double ema1 = m_emaBuf[0];
   double ema4 = m_emaBuf[3];
   double atr1 = m_atrBuf[0];

   if(ema1 <= 0.0 || ema4 <= 0.0 || atr1 <= 0.0)
      return 0;

   double slope3 = (ema1 - ema4) / atr1;
   const double deadband = 0.10;

   if(slope3 > deadband)  return 1;
   if(slope3 < -deadband) return -1;
   return 0;
}

//+------------------------------------------------------------------+
//| IsSwingHighAt — check if bar at 'shift' is a confirmed swing high|
//|  Window: buffer indices 0..2*shift, center at shift.             |
//|  Center must be >= max of window and strictly > max of older     |
//|  bars (indices shift+1..2*shift). Confirmed at buffer index 0.   |
//+------------------------------------------------------------------+
bool CSovereignStructRide::IsSwingHighAt(const int shift) const
{
   int n = ArraySize(m_hiBuf);
   if(shift < 0 || 2 * shift >= n) return false;

   double center = m_hiBuf[shift];

   //--- center >= max of full window (indices 0..2*shift)
   for(int i = 0; i <= 2 * shift; i++)
      if(m_hiBuf[i] > center) return false;

   //--- center strictly > max of older bars (indices shift+1..2*shift)
   for(int i = shift + 1; i <= 2 * shift; i++)
      if(m_hiBuf[i] >= center) return false;

   return true;
}

//+------------------------------------------------------------------+
//| IsSwingLowAt — check if bar at 'shift' is a confirmed swing low  |
//+------------------------------------------------------------------+
bool CSovereignStructRide::IsSwingLowAt(const int shift) const
{
   int n = ArraySize(m_loBuf);
   if(shift < 0 || 2 * shift >= n) return false;

   double center = m_loBuf[shift];

   //--- center <= min of full window
   for(int i = 0; i <= 2 * shift; i++)
      if(m_loBuf[i] < center) return false;

   //--- center strictly < min of older bars
   for(int i = shift + 1; i <= 2 * shift; i++)
      if(m_loBuf[i] <= center) return false;

   return true;
}

//+------------------------------------------------------------------+
//| NormalizeVolume                                                  |
//+------------------------------------------------------------------+
double CSovereignStructRide::NormalizeVolume(const double volume) const
{
   double step = SovSymbolDouble(m_symbol, SYMBOL_VOLUME_STEP, 0.01, "NormalizeVolume");
   double vmin = SovSymbolDouble(m_symbol, SYMBOL_VOLUME_MIN, 0.01, "NormalizeVolume");
   double vmax = SovSymbolDouble(m_symbol, SYMBOL_VOLUME_MAX, 100.0, "NormalizeVolume");
   if(step <= 0) step = 0.01;
   double v = MathFloor(volume / step) * step;
   if(v < vmin) return 0.0;
   if(v > vmax) v = vmax;
   return v;
}

//+------------------------------------------------------------------+
//| LotsForRisk                                                      |
//+------------------------------------------------------------------+
double CSovereignStructRide::LotsForRisk(const double riskMoney,
                                         const double stopDist) const
{
   if(riskMoney <= 0.0 || stopDist <= 0.0) return 0.0;

   double tickSize  = SovSymbolDouble(m_symbol, SYMBOL_TRADE_TICK_SIZE, 0.0, "LotsForRisk");
   double tickValue = SovSymbolDouble(m_symbol, SYMBOL_TRADE_TICK_VALUE, 0.0, "LotsForRisk");
   if(tickSize <= 0.0 || tickValue <= 0.0) return 0.0;

   double lossPerLot = (stopDist / tickSize) * tickValue;
   if(lossPerLot <= 0.0) return 0.0;

   return NormalizeVolume(riskMoney / lossPerLot);
}

//+------------------------------------------------------------------+
//| OpenPosition                                                     |
//+------------------------------------------------------------------+
void CSovereignStructRide::OpenPosition(const int side, const double price,
                                         const double atr,
                                         CTrade &t,
                                         CSovereignPerformance &perf,
                                         CSovereignTrafficLight &tl,
                                         CSovereignAuditLog &audit,
                                         const string comment)
{
   if(atr <= 0.0) return;

   double stopDist = atr * m_stopAtr;
   double sl = (side > 0) ? (price - stopDist) : (price + stopDist);
   double tp = (side > 0) ? (price + stopDist * m_tpAtr) : (price - stopDist * m_tpAtr);

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double riskMoney = equity * m_riskPct / 100.0;
   double lot = LotsForRisk(riskMoney, stopDist);
   if(lot <= 0.0) return;

   SovResetLastError();
   int spread = (int)SovSymbolInteger(m_symbol, SYMBOL_SPREAD, 0, "OpenPosition spread");
   MqlDateTime barDt;
   TimeToStruct(iTime(m_symbol, PERIOD_H1, 1), barDt);

   TrafficLightResult tlRes;
   tlRes = tl.Evaluate(side, 1, atr, 0.5, side, true, spread,
                       barDt.hour, true, true);
   if(!tlRes.allowed) return;

   string legBucket = CSovereignPerformance::LegBucket("SOV|STRUCT", m_symbol);
   if(!perf.AllowNewRisk(legBucket)) return;
   double govMult = perf.CombinedMultiplier(legBucket);
   if(govMult <= 0.0) return;

   riskMoney = equity * m_riskPct / 100.0 * govMult;
   lot = LotsForRisk(riskMoney, stopDist);
   if(lot <= 0.0) return;
   lot = NormalizeVolume(lot * tlRes.riskMultiplier);
   if(lot <= 0.0) return;

   SovResetLastError();
   double entryPx = (side > 0) ? SovSymbolDouble(m_symbol, SYMBOL_ASK, 0.0, "OpenPosition price")
                               : SovSymbolDouble(m_symbol, SYMBOL_BID, 0.0, "OpenPosition price");
   if(entryPx <= 0.0) return;

   double newSL = (side > 0) ? entryPx - stopDist : entryPx + stopDist;
   double newTP = (side > 0) ? entryPx + stopDist * m_tpAtr : entryPx - stopDist * m_tpAtr;

   SovResetLastError();
   bool ok = false;
   if(side > 0)
      ok = t.Buy(lot, m_symbol, entryPx, newSL, newTP, comment);
   else
      ok = t.Sell(lot, m_symbol, entryPx, newSL, newTP, comment);

   if(ok)
   {
      perf.RegisterOrder(t.ResultOrder(), legBucket, riskMoney,
                         side, 1, 0.0, "");
      LogOrderAttempt((side > 0) ? "BUY" : "SELL", entryPx, lot,
                      t.ResultRetcode(), comment, spread, audit);

      m_inPosition = true;
      m_positionSide = side;
      m_entryPrice = entryPx;
      m_stopPrice = newSL;
      m_tpPrice = newTP;
      m_entryAtr = atr;
      m_barsHeld = 0;

      //--- reset swing tracking for new leg
      m_lastHigh = 0.0;
      m_lastLow = 0.0;
      m_pulledBack = false;
   }
   else
   {
      SovLogTradeRetcode(t, "StructRide OpenPosition " + m_symbol + " side=" + IntegerToString(side), m_symbol);
    }
}

//+------------------------------------------------------------------+
//| ClosePosition                                                    |
//+------------------------------------------------------------------+
void CSovereignStructRide::ClosePosition(CTrade &t,
                                          CSovereignAuditLog &audit,
                                          const string reason)
{
   CPositionInfo p;
   ulong targetTicket = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!p.SelectByIndex(i)) continue;
      if(p.Magic() != m_magic || p.Symbol() != m_symbol) continue;

      targetTicket = p.Ticket();
      break;
   }

   if(targetTicket == 0) return;

   SovResetLastError();
   if(!t.PositionClose(targetTicket))
      SovLogTradeRetcode(t, "StructRide ClosePosition " + m_symbol + " reason=" + reason, m_symbol);

   m_inPosition = false;
   m_positionSide = 0;
   m_entryPrice = 0.0;
   m_stopPrice = 0.0;
   m_tpPrice = 0.0;
   m_entryAtr = 0.0;
   m_barsHeld = 0;
}

//+------------------------------------------------------------------+
//| LogOrderAttempt                                                  |
//+------------------------------------------------------------------+
void CSovereignStructRide::LogOrderAttempt(const string action, const double price,
                                            const double volume, const uint retcode,
                                            const string comment, const int spread,
                                            CSovereignAuditLog &audit)
{
   audit.LogOrderAttempt(action, "MARKET", price, price, volume,
                         spread, retcode, comment,
                         false, "", 0.0, 0.0);
}

//+------------------------------------------------------------------+
//| OnNewBar — called every tick; acts only on new H1 bars           |
//+------------------------------------------------------------------+
void CSovereignStructRide::OnNewBar(const datetime now, const bool allowEntries,
                                     const double equity,
                                     CSovereignPerformance &perf,
                                     CSovereignTrafficLight &tl,
                                     CSovereignAuditLog &audit,
                                     CTrade &t)
{
   if(!IsNewBar()) return;
   if(!CopyBuffers()) return;

   //--- compute regime (carry-forward last non-zero when in deadband)
   int rawRegime = ComputeRegime();
   int regime = (rawRegime != 0) ? rawRegime : m_prevRegime;

   //--- detect regime change (end of leg) vs new leg start
   bool regimeChanged = (m_prevRegime != 0 && regime != 0 && regime != m_prevRegime);
   bool newRegime = (regime != 0 && regime != m_prevRegime);

   if(regime != 0)
      m_prevRegime = regime;
   m_regime = regime;

   double closePx = m_clBuf[0];  // close of most recent completed bar (bar 1)

   //--- if we still think we're in a position but it's gone (SL/TP hit externally)
   if(m_inPosition)
   {
      bool stillThere = false;
      CPositionInfo p;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!p.SelectByIndex(i)) continue;
         if(p.Magic() == m_magic && p.Symbol() == m_symbol)
         {
            stillThere = true;
            break;
         }
      }
      if(!stillThere)
      {
         m_inPosition = false;
         m_positionSide = 0;
         m_entryPrice = 0.0;
         m_stopPrice = 0.0;
         m_tpPrice = 0.0;
         m_entryAtr = 0.0;
         m_barsHeld = 0;
      }
   }

   //--- process confirmed swing at buffer index m_k (center), confirmed now (index 0)
   //--- shift = m_k means the swing center is m_k bars ago, confirmed at the current bar
   bool swingHigh = false, swingLow = false;
   double swingHighPx = 0.0, swingLowPx = 0.0;

   if(m_k > 0 && 2 * m_k < ArraySize(m_hiBuf))
   {
      if(IsSwingHighAt(m_k))
      {
         swingHigh = true;
         swingHighPx = m_hiBuf[m_k];
      }
      if(IsSwingLowAt(m_k))
      {
         swingLow = true;
         swingLowPx = m_loBuf[m_k];
      }
   }

   //==================================================================
   //--- IN POSITION: manage exits                                    |
   //==================================================================
   if(m_inPosition)
   {
      //--- regime flip: end of leg — close everything
      if(regimeChanged)
      {
         ClosePosition(t, audit, "regime flip");
         return;
      }

      m_barsHeld++;

      //--- process swing events for exits
      if(swingHigh || swingLow)
      {
         if(m_positionSide > 0)  // long
         {
            if(swingHigh)
            {
               //--- failed high: pullback then lower high
               if(m_lastHigh > 0.0 && swingHighPx < m_lastHigh && m_pulledBack)
               {
                  ClosePosition(t, audit, "failed high");
                  return;
               }
               m_lastHigh = swingHighPx;
               m_pulledBack = false;
            }
            if(swingLow)
            {
               //--- lower low: structure breakdown
               if(m_lastLow > 0.0 && swingLowPx < m_lastLow)
               {
                  ClosePosition(t, audit, "lower low");
                  return;
               }
               m_lastLow = swingLowPx;
               m_pulledBack = true;
            }
         }
         else  // short
         {
            if(swingLow)
            {
               //--- failed low: pullback then higher low
               if(m_lastLow > 0.0 && swingLowPx > m_lastLow && m_pulledBack)
               {
                  ClosePosition(t, audit, "failed low");
                  return;
               }
               m_lastLow = swingLowPx;
               m_pulledBack = false;
            }
            if(swingHigh)
            {
               //--- higher high: structure breakdown against short
               if(m_lastHigh > 0.0 && swingHighPx > m_lastHigh)
               {
                  ClosePosition(t, audit, "higher high");
                  return;
               }
               m_lastHigh = swingHighPx;
               m_pulledBack = true;
            }
         }
      }

      //--- hard stop / take profit
      bool hit = false;
      double exitPx = 0.0;
      if(m_positionSide > 0)
      {
         if(closePx <= m_stopPrice) { hit = true; exitPx = m_stopPrice; }
         else if(closePx >= m_tpPrice) { hit = true; exitPx = m_tpPrice; }
      }
      else
      {
         if(closePx >= m_stopPrice) { hit = true; exitPx = m_stopPrice; }
         else if(closePx <= m_tpPrice) { hit = true; exitPx = m_tpPrice; }
      }

      if(hit)
      {
         ClosePosition(t, audit, "stop/tp");
         return;
      }

      //--- max hold bars
      if(m_barsHeld >= m_maxHoldBars)
      {
         ClosePosition(t, audit, "max hold");
         return;
      }
   }

   //==================================================================
   //--- FLAT: look for entry                                         |
   //==================================================================
   if(!m_inPosition && allowEntries && regime != 0)
   {
      bool enter = false;

      if(newRegime)
      {
         //--- start of a new leg: enter in regime direction
         enter = true;
      }
      else
      {
         //--- leg is ongoing: check for re-entry signal from swings
         if(m_positionSide > 0 || m_positionSide < 0)
         {
            //--- We were in this leg direction before but got exited
            //--- by a signal before the leg ended. Wait for re-entry.
         }

         if(swingHigh)
         {
            if(regime > 0)
            {
               //--- long: re-enter on new high above last confirmed high
               if(m_lastHigh > 0.0 && swingHighPx > m_lastHigh)
                  enter = true;
               m_lastHigh = swingHighPx;
               m_pulledBack = false;
            }
            else
            {
               //--- short: update last_high on swing high (no re-entry on highs)
               m_lastHigh = swingHighPx;
               m_pulledBack = true;  // pullback against short = higher high
            }
         }
         if(swingLow)
         {
            if(regime < 0)
            {
               //--- short: re-enter on new low below last confirmed low
               if(m_lastLow > 0.0 && swingLowPx < m_lastLow)
                  enter = true;
               m_lastLow = swingLowPx;
               m_pulledBack = false;
            }
            else
            {
               //--- long: update last_low on swing low (no re-entry on lows)
               m_lastLow = swingLowPx;
               m_pulledBack = true;  // pullback against long = lower low
            }
         }
      }

      if(enter)
      {
         OpenPosition(regime, closePx, m_atrBuf[0], t, perf, tl, audit, "SOV-STRUCT");
      }
   }
}

#endif // __SOVEREIGN_STRUCT_RIDE_MQH__
