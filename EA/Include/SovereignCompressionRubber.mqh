//+------------------------------------------------------------------+
//|                                              SovereignCompressionRubber.mqh |
//|                                  MA compression rubberband fade overlay   |
//|                                  H1 only. Fade extensions from MA bundle.   |
//+------------------------------------------------------------------+
#property strict

#ifndef __SOVEREIGN_COMPRESSION_RUBBER_MQH__
#define __SOVEREIGN_COMPRESSION_RUBBER_MQH__

#include <Trade\Trade.mqh>
#include <Sovereign\SovereignPerformance.mqh>
#include <Sovereign\SovereignTrafficLight.mqh>
#include <Sovereign\SovereignAuditLog.mqh>
#include <Sovereign\SovereignError.mqh>

//+------------------------------------------------------------------+
//| Class CSovereignCompressionRubber                                 |
//+------------------------------------------------------------------+
class CSovereignCompressionRubber
   {
protected:
   string          m_symbol;
   long            m_magic;

   int             m_emaFast;
   int             m_emaMid;
   int             m_emaSlow;
   double          m_compressAtr;   // MA compression threshold (x ATR)
   double          m_extAtr;        // extension threshold (x ATR)
   double          m_stopAtr;       // stop distance (x ATR)
   double          m_tpAtr;         // take profit (x ATR)
   double          m_riskPct;       // risk per trade %
   int             m_maxHoldBars;   // max bars in position

   int             m_hEmaFast;
   int             m_hEmaMid;
   int             m_hEmaSlow;
   int             m_hAtr14;

   double          m_emaFastBuf[];
   double          m_emaMidBuf[];
   double          m_emaSlowBuf[];
   double          m_atrBuf[];
   double          m_clBuf[];

   bool            m_inPosition;
   int             m_positionSide;
   double          m_entryPrice;
   double          m_stopPrice;
   double          m_tpPrice;
   double          m_entryAtr;
   int             m_barsHeld;
   datetime        m_lastBarTime;

public:
   CSovereignCompressionRubber();
   ~CSovereignCompressionRubber();

   bool Init(const string symbol, const long magic,
             const int emaFast, const int emaMid, const int emaSlow,
             const double compressAtr, const double extAtr,
             const double stopAtr, const double tpAtr,
             const double riskPct, const int maxHoldBars);
   void Deinit();

   void OnNewBar(const datetime now, const bool allowEntries, const double equity,
                 CSovereignPerformance &perf, CSovereignTrafficLight &tl,
                 CSovereignAuditLog &audit, CTrade &t);

   bool InPosition() const { return m_inPosition; }
   int  PositionSide() const { return m_positionSide; }
   double EntryPrice() const { return m_entryPrice; }

private:
   bool IsNewBar();
   bool CopyBuffers();
   void OpenPosition(const int side, const double price, const double atr,
                     CTrade &t, CSovereignPerformance &perf,
                     CSovereignTrafficLight &tl, CSovereignAuditLog &audit,
                     const string comment);
   void ClosePosition(CTrade &t, CSovereignAuditLog &audit,
                      const string reason);
   double LotsForRisk(const double riskMoney, const double stopDist) const;
   double NormalizeVolume(const double volume) const;
   };
#endif // __SOVEREIGN_COMPRESSION_RUBBER_MQH__

//+------------------------------------------------------------------+
//| Constructor                                                      |
//+------------------------------------------------------------------+
CSovereignCompressionRubber::CSovereignCompressionRubber()
   {
   m_symbol = "";
   m_magic = 0;
   m_hEmaFast = INVALID_HANDLE;
   m_hEmaMid = INVALID_HANDLE;
   m_hEmaSlow = INVALID_HANDLE;
   m_hAtr14 = INVALID_HANDLE;

   m_emaFast = 8;
   m_emaMid = 21;
   m_emaSlow = 50;
   m_compressAtr = 0.5;
   m_extAtr = 2.0;
   m_stopAtr = 1.5;
   m_tpAtr = 2.0;
   m_riskPct = 0.5;
   m_maxHoldBars = 24;

   m_inPosition = false;
   m_positionSide = 0;
   m_entryPrice = 0.0;
   m_stopPrice = 0.0;
   m_tpPrice = 0.0;
   m_entryAtr = 0.0;
   m_barsHeld = 0;
   m_lastBarTime = 0;
   }
//+------------------------------------------------------------------+
//| Destructor                                                       |
//+------------------------------------------------------------------+
CSovereignCompressionRubber::~CSovereignCompressionRubber()
   {
   Deinit();
   }
//+------------------------------------------------------------------+
//| Init                                                             |
//+------------------------------------------------------------------+
bool CSovereignCompressionRubber::Init(const string symbol, const long magic,
                                       const int emaFast, const int emaMid, const int emaSlow,
                                       const double compressAtr, const double extAtr,
                                       const double stopAtr, const double tpAtr,
                                       const double riskPct, const int maxHoldBars)
   {
   m_symbol = symbol;
   m_magic = magic;
   m_emaFast = MathMax(1, emaFast);
   m_emaMid = MathMax(1, emaMid);
   m_emaSlow = MathMax(1, emaSlow);
   m_compressAtr = MathMax(0.1, compressAtr);
   m_extAtr = MathMax(0.5, extAtr);
   m_stopAtr = MathMax(0.5, stopAtr);
   m_tpAtr = MathMax(0.5, tpAtr);
   m_riskPct = MathMax(0.01, riskPct);
   m_maxHoldBars = MathMax(1, maxHoldBars);

   SovResetLastError();
   m_hEmaFast = iMA(m_symbol, PERIOD_H1, m_emaFast, 0, MODE_EMA, PRICE_CLOSE);
   SovResetLastError();
   m_hEmaMid = iMA(m_symbol, PERIOD_H1, m_emaMid, 0, MODE_EMA, PRICE_CLOSE);
   SovResetLastError();
   m_hEmaSlow = iMA(m_symbol, PERIOD_H1, m_emaSlow, 0, MODE_EMA, PRICE_CLOSE);
   SovResetLastError();
   m_hAtr14 = iATR(m_symbol, PERIOD_H1, 14);

   if(m_hEmaFast == INVALID_HANDLE || m_hEmaMid == INVALID_HANDLE ||
      m_hEmaSlow == INVALID_HANDLE || m_hAtr14 == INVALID_HANDLE)
   {
      SovLogError("CompressionRubber: indicator handle failed for " + m_symbol);
      return false;
   }

   ArraySetAsSeries(m_emaFastBuf, true);
   ArraySetAsSeries(m_emaMidBuf, true);
   ArraySetAsSeries(m_emaSlowBuf, true);
   ArraySetAsSeries(m_atrBuf, true);
   ArraySetAsSeries(m_clBuf, true);

   return true;
   }
//+------------------------------------------------------------------+
//| Deinit                                                           |
//+------------------------------------------------------------------+
void CSovereignCompressionRubber::Deinit()
   {
   if(m_hEmaFast != INVALID_HANDLE) { IndicatorRelease(m_hEmaFast); m_hEmaFast = INVALID_HANDLE; }
   if(m_hEmaMid != INVALID_HANDLE)  { IndicatorRelease(m_hEmaMid);  m_hEmaMid = INVALID_HANDLE; }
   if(m_hEmaSlow != INVALID_HANDLE) { IndicatorRelease(m_hEmaSlow); m_hEmaSlow = INVALID_HANDLE; }
   if(m_hAtr14 != INVALID_HANDLE)   { IndicatorRelease(m_hAtr14);   m_hAtr14 = INVALID_HANDLE; }
   }
//+------------------------------------------------------------------+
//| IsNewBar — detect a new H1 bar                                   |
//+------------------------------------------------------------------+
bool CSovereignCompressionRubber::IsNewBar()
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
   bool CSovereignCompressionRubber::CopyBuffers()
   {
   int need = 60;
   ArraySetAsSeries(m_emaFastBuf, true);
   SovResetLastError();
   if(!SovCheckHandle(m_hEmaFast, "iMA EMAFast")) return false;
   if(!SovCopyBuffer(m_hEmaFast, 0, 1, need, m_emaFastBuf, "EMAFast")) return false;
   ArraySetAsSeries(m_emaMidBuf, true);
   SovResetLastError();
   if(!SovCheckHandle(m_hEmaMid, "iMA EMAMid")) return false;
   if(!SovCopyBuffer(m_hEmaMid, 0, 1, need, m_emaMidBuf, "EMAMid")) return false;
   ArraySetAsSeries(m_emaSlowBuf, true);
   SovResetLastError();
   if(!SovCheckHandle(m_hEmaSlow, "iMA EMASlow")) return false;
   if(!SovCopyBuffer(m_hEmaSlow, 0, 1, need, m_emaSlowBuf, "EMASlow")) return false;
   ArraySetAsSeries(m_atrBuf, true);
   SovResetLastError();
   if(!SovCheckHandle(m_hAtr14, "iATR14")) return false;
   if(!SovCopyBuffer(m_hAtr14, 0, 1, need, m_atrBuf, "ATR14")) return false;
   if(CopyClose(m_symbol, PERIOD_H1, 1, need, m_clBuf) < need) return false;
   return true;
   }
//+------------------------------------------------------------------+
//| LotsForRisk                                                      |
//+------------------------------------------------------------------+
double CSovereignCompressionRubber::LotsForRisk(const double riskMoney, const double stopDist) const
   {
   if(stopDist <= 0.0) return 0.0;
   double tick = SovSymbolDouble(m_symbol, SYMBOL_TRADE_TICK_SIZE, 0.0, "LotsForRisk");
   double tickValue = SovSymbolDouble(m_symbol, SYMBOL_TRADE_TICK_VALUE, 0.0, "LotsForRisk");
   if(tick <= 0 || tickValue <= 0) return 0.0;
   double lots = riskMoney / (stopDist / tick * tickValue);
   return NormalizeVolume(lots);
   }
//+------------------------------------------------------------------+
//| NormalizeVolume                                                  |
//+------------------------------------------------------------------+
double CSovereignCompressionRubber::NormalizeVolume(const double volume) const
   {
   double minLot = SovSymbolDouble(m_symbol, SYMBOL_VOLUME_MIN, 0.01, "NormalizeVolume");
   double maxLot = SovSymbolDouble(m_symbol, SYMBOL_VOLUME_MAX, 100.0, "NormalizeVolume");
   double stepLot = SovSymbolDouble(m_symbol, SYMBOL_VOLUME_STEP, 0.01, "NormalizeVolume");
   if(stepLot <= 0) stepLot = 0.01;
   double v = MathFloor(volume / stepLot) * stepLot;
   if(v < minLot) v = minLot;
   if(v > maxLot) v = maxLot;
   return v;
   }
//+------------------------------------------------------------------+
//| OpenPosition                                                     |
//+------------------------------------------------------------------+
void CSovereignCompressionRubber::OpenPosition(const int side, const double price,
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

   string legBucket = CSovereignPerformance::LegBucket("SOV|COMP", m_symbol);
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

      m_inPosition = true;
      m_positionSide = side;
      m_entryPrice = entryPx;
      m_stopPrice = newSL;
      m_tpPrice = newTP;
      m_entryAtr = atr;
      m_barsHeld = 0;
   }
   else
   {
      SovLogTradeRetcode(t, "CompressionRubber OpenPosition " + m_symbol + " side=" + IntegerToString(side), m_symbol);
   }
}
//+------------------------------------------------------------------+
//| ClosePosition                                                    |
//+------------------------------------------------------------------+
void CSovereignCompressionRubber::ClosePosition(CTrade &t, CSovereignAuditLog &audit,
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

   if(targetTicket == 0) { m_inPosition = false; m_positionSide = 0; return; }

   SovResetLastError();
   if(!t.PositionClose(targetTicket))
      SovLogTradeRetcode(t, "CompressionRubber ClosePosition " + m_symbol + " reason=" + reason, m_symbol);

   m_inPosition = false;
   m_positionSide = 0;
   m_entryPrice = 0.0;
   m_stopPrice = 0.0;
   m_tpPrice = 0.0;
   m_entryAtr = 0.0;
   m_barsHeld = 0;
   }
//+------------------------------------------------------------------+
//| OnNewBar — acts only on new H1 bars                              |
//+------------------------------------------------------------------+
void CSovereignCompressionRubber::OnNewBar(const datetime now, const bool allowEntries,
                                            const double equity,
                                            CSovereignPerformance &perf,
                                            CSovereignTrafficLight &tl,
                                            CSovereignAuditLog &audit,
                                            CTrade &t)
   {
   if(!IsNewBar()) return;
   if(!CopyBuffers()) return;

   m_barsHeld++;

   double closePx = m_clBuf[0];

   //--- check if position still exists externally (SL/TP hit)
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

   //==================================================================
   //--- IN POSITION: manage exits                                    |
   //==================================================================
   if(m_inPosition)
      {
      double emaFast = m_emaFastBuf[0];
      double emaMid = m_emaMidBuf[0];
      double emaSlow = m_emaSlowBuf[0];
      double maCenter = (emaFast + emaMid + emaSlow) / 3.0;

      if(m_positionSide > 0)  // long
         {
         //--- exit: price back to MA center (rubberband snap-back)
         if(closePx >= maCenter) { ClosePosition(t, audit, "snap-back"); return; }
         //--- hard stop / TP
         if(closePx <= m_stopPrice) { ClosePosition(t, audit, "stop/tp"); return; }
         if(closePx >= m_tpPrice)   { ClosePosition(t, audit, "stop/tp"); return; }
         //--- max hold
         if(m_barsHeld >= m_maxHoldBars) { ClosePosition(t, audit, "max-hold"); return; }
         }
      else  // short
         {
         if(closePx <= maCenter) { ClosePosition(t, audit, "snap-back"); return; }
         if(closePx >= m_stopPrice) { ClosePosition(t, audit, "stop/tp"); return; }
         if(closePx <= m_tpPrice)   { ClosePosition(t, audit, "stop/tp"); return; }
         if(m_barsHeld >= m_maxHoldBars) { ClosePosition(t, audit, "max-hold"); return; }
         }
      }

   //==================================================================
   //--- FLAT: look for entry                                         |
   //==================================================================
   if(!m_inPosition && allowEntries)
      {
      double emaFast = m_emaFastBuf[0];
      double emaMid = m_emaMidBuf[0];
      double emaSlow = m_emaSlowBuf[0];
      double atr = m_atrBuf[0];

      if(emaFast <= 0 || emaMid <= 0 || emaSlow <= 0 || atr <= 0 || closePx <= 0) return;

      double maSpread = MathMax(MathAbs(emaFast - emaMid),
                                MathMax(MathAbs(emaMid - emaSlow), MathAbs(emaFast - emaSlow)));
      bool compressed = (maSpread < m_compressAtr * atr);

      double maCenter = (emaFast + emaMid + emaSlow) / 3.0;
      double band = m_extAtr * atr;

      if(compressed)
         {
         if(closePx < maCenter - band)
            OpenPosition(1, closePx, atr, t, perf, tl, audit, "SOV-COMP-LONG");
         else if(closePx > maCenter + band)
            OpenPosition(-1, closePx, atr, t, perf, tl, audit, "SOV-COMP-SHORT");
         }
      }
   }
//+------------------------------------------------------------------+
