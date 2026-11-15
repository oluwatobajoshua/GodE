//+------------------------------------------------------------------+
//|                                              SovereignEma21Harvest.mqh |
//|                          EMA21 cross candle harvesting strategy    |
//|                          Entry: N consecutive closes on cross side  |
//|                          Exit: EMA21 break / RSI extreme / max bars |
//+------------------------------------------------------------------+
#property strict

#ifndef __SOVEREIGN_EMA21_HARVEST_MQH__
#define __SOVEREIGN_EMA21_HARVEST_MQH__

#include <Trade\Trade.mqh>
#include <Sovereign\SovereignPerformance.mqh>
#include <Sovereign\SovereignTrafficLight.mqh>
#include <Sovereign\SovereignAuditLog.mqh>
#include <Sovereign\SovereignError.mqh>

//+------------------------------------------------------------------+
//| Class CSovereignEma21Harvest                                      |
//+------------------------------------------------------------------+
class CSovereignEma21Harvest
   {
protected:
   string          m_symbol;
   long            m_magic;
   ENUM_TIMEFRAMES m_timeframe;

   double          m_riskPct;      // risk per trade %
   int             m_minConsecutiveLong;   // min consecutive closes above EMA21 for long
   int             m_minConsecutiveShort;  // min consecutive closes below EMA21 for short
   bool            m_exitOnEmaBreak;       // exit when price closes beyond EMA21
   int             m_rsiExitLong;          // RSI level to exit long
   int             m_rsiExitShort;         // RSI level to exit short
   int             m_maxHold;              // max bars held before time exit
   double          m_trailAtr;             // ATR trailing stop multiplier (0=disabled)

   int             m_emaPeriod;      // EMA period for the harvest filter
   int             m_rsiEntryMax;    // only enter long if RSI < this (100=disabled)
   int             m_hEma21;
   int             m_hRsi;
   int             m_hAtr;

   double          m_ema21Buf[];
   double          m_rsiBuf[];
   double          m_atrBuf[];
   double          m_clBuf[];
   double          m_hiBuf[];
   double          m_loBuf[];

   bool            m_inPosition;
   int             m_positionSide;   // +1 long, -1 short
   double          m_entryPrice;
   double          m_entryAtr;
   int             m_entryBarIndex;
   int             m_barsHeld;
   double          m_highPx;
   double          m_lowPx;
   int             m_consecLong;
   int             m_consecShort;
   datetime        m_lastBarTime;

   string          m_lastBlockReason;
   datetime        m_lastBlockTime;

public:
   CSovereignEma21Harvest();
   ~CSovereignEma21Harvest();

   bool Init(const string symbol, const long magic, const ENUM_TIMEFRAMES timeframe,
             const int emaPeriod,
             const double riskPct,
             const int minConsecutiveLong = 1,
             const int minConsecutiveShort = 3,
             const bool exitOnEmaBreak = false,
             const int rsiExitLong = 80,
             const int rsiExitShort = 20,
             const int maxHold = 48,
             const double trailAtr = 0.0,
             const int rsiEntryMax = 100);
   void Deinit();

   void OnNewBar(const datetime now, const bool allowEntries, const double equity,
                 CSovereignPerformance &perf, CSovereignTrafficLight &tl,
                 CSovereignAuditLog &audit, CTrade &t);

   bool InPosition() const { return m_inPosition; }
   int  PositionSide() const { return m_positionSide; }
   double EntryPrice() const { return m_entryPrice; }
   int  BarsHeld() const { return m_barsHeld; }
   string LastBlockReason() const { return m_lastBlockReason; }
   datetime LastBlockTime() const { return m_lastBlockTime; }
   int  CurrentConsecutiveLong() const { return m_consecLong; }
   int  CurrentConsecutiveShort() const { return m_consecShort; }
   int  RsiEntryMax() const { return m_rsiEntryMax; }

private:
   bool IsNewBar();
   bool CopyBuffers();
   void OpenPosition(const int side, const double price, const double atr,
                     CTrade &t, CSovereignPerformance &perf,
                     CSovereignTrafficLight &tl, CSovereignAuditLog &audit,
                     const string source);
   void ClosePosition(CTrade &t, const string reason);
   double CalcLot(const int side, const double price, const double atr);
};

//+------------------------------------------------------------------+
//| Constructor                                                      |
//+------------------------------------------------------------------+
CSovereignEma21Harvest::CSovereignEma21Harvest()
   {
    m_symbol = "";
    m_magic = 0;
    m_timeframe = PERIOD_CURRENT;
    m_emaPeriod = 21;
    m_rsiEntryMax = 100;
    m_riskPct = 1.0;
    m_minConsecutiveLong = 1;
    m_minConsecutiveShort = 3;
    m_exitOnEmaBreak = false;
    m_rsiExitLong = 80;
    m_rsiExitShort = 20;
    m_maxHold = 48;
    m_trailAtr = 0.0;

    m_hEma21 = INVALID_HANDLE;
    m_hRsi = INVALID_HANDLE;
    m_hAtr = INVALID_HANDLE;

    ArraySetAsSeries(m_ema21Buf, true);
    ArraySetAsSeries(m_rsiBuf, true);
    ArraySetAsSeries(m_atrBuf, true);
    ArraySetAsSeries(m_clBuf, true);
    ArraySetAsSeries(m_hiBuf, true);
    ArraySetAsSeries(m_loBuf, true);

    m_inPosition = false;
    m_positionSide = 0;
    m_entryPrice = 0.0;
    m_entryAtr = 0.0;
    m_entryBarIndex = 0;
    m_barsHeld = 0;
    m_highPx = 0.0;
    m_lowPx = 0.0;
    m_consecLong = 0;
    m_consecShort = 0;
    m_lastBarTime = 0;
    m_lastBlockReason = "";
    m_lastBlockTime = 0;
   }

//+------------------------------------------------------------------+
//| Destructor                                                       |
//+------------------------------------------------------------------+
CSovereignEma21Harvest::~CSovereignEma21Harvest()
   {
    Deinit();
   }

//+------------------------------------------------------------------+
//| Init                                                             |
//+------------------------------------------------------------------+
bool CSovereignEma21Harvest::Init(const string symbol, const long magic, const ENUM_TIMEFRAMES timeframe,
                                  const int emaPeriod,
                                  const double riskPct,
                                  const int minConsecutiveLong,
                                  const int minConsecutiveShort,
                                  const bool exitOnEmaBreak,
                                  const int rsiExitLong,
                                   const int rsiExitShort,
                                   const int maxHold,
                                   const double trailAtr,
                                   const int rsiEntryMax = 100)
   {
    m_symbol = symbol;
    m_magic = magic;
    m_timeframe = timeframe;
    m_emaPeriod = emaPeriod;
    m_rsiEntryMax = rsiEntryMax;
    m_riskPct = riskPct;
    m_minConsecutiveLong = MathMax(0, minConsecutiveLong);
    m_minConsecutiveShort = MathMax(0, minConsecutiveShort);
    m_exitOnEmaBreak = exitOnEmaBreak;
    m_rsiExitLong = rsiExitLong;
    m_rsiExitShort = rsiExitShort;
    m_maxHold = maxHold;
    m_trailAtr = trailAtr;
    m_rsiEntryMax = rsiEntryMax;

    m_hEma21 = iMA(_Symbol, m_timeframe, m_emaPeriod, 0, MODE_EMA, PRICE_CLOSE);
    m_hRsi = iRSI(_Symbol, m_timeframe, 9, PRICE_CLOSE);
    m_hAtr = iATR(_Symbol, m_timeframe, 14);

    if(m_hEma21 == INVALID_HANDLE || m_hRsi == INVALID_HANDLE || m_hAtr == INVALID_HANDLE)
       {
        SovLogError("Init", "Indicator handle creation failed (ema21=" + IntegerToString((long)m_hEma21) +
                    " rsi=" + IntegerToString((long)m_hRsi) + " atr=" + IntegerToString((long)m_hAtr) + ")");
        return false;
       }

    ArrayResize(m_ema21Buf, 100);
    ArrayResize(m_rsiBuf, 100);
    ArrayResize(m_atrBuf, 100);
    ArrayResize(m_clBuf, 100);
    ArrayResize(m_hiBuf, 100);
    ArrayResize(m_loBuf, 100);

    m_inPosition = false;
    m_positionSide = 0;
    m_entryPrice = 0.0;
    m_entryAtr = 0.0;
    m_entryBarIndex = 0;
    m_barsHeld = 0;
    m_highPx = 0.0;
    m_lowPx = 0.0;
    m_consecLong = 0;
    m_consecShort = 0;
    m_lastBlockReason = "";
    m_lastBlockTime = 0;

     SovLogInfo("Init: EMA harvest " + m_symbol + " tf=" + IntegerToString((long)m_timeframe) +
               " emaPeriod=" + IntegerToString(m_emaPeriod) +
               " minConsecLong=" + IntegerToString(m_minConsecutiveLong) +
               " minConsecShort=" + IntegerToString(m_minConsecutiveShort) +
               " exitOnEma=" + ((m_exitOnEmaBreak) ? "true" : "false") +
               " rsiExitLong=" + IntegerToString(m_rsiExitLong) +
               " rsiExitShort=" + IntegerToString(m_rsiExitShort) +
               " maxHold=" + IntegerToString(m_maxHold) +
               " trailAtr=" + DoubleToString(m_trailAtr, 1) +
               " rsiEntryMax=" + IntegerToString(m_rsiEntryMax));
    return true;
   }

//+------------------------------------------------------------------+
//| Deinit                                                           |
//+------------------------------------------------------------------+
void CSovereignEma21Harvest::Deinit()
   {
    if(m_hEma21 != INVALID_HANDLE) { IndicatorRelease(m_hEma21); m_hEma21 = INVALID_HANDLE; }
    if(m_hRsi != INVALID_HANDLE)   { IndicatorRelease(m_hRsi);   m_hRsi = INVALID_HANDLE; }
    if(m_hAtr != INVALID_HANDLE)   { IndicatorRelease(m_hAtr);   m_hAtr = INVALID_HANDLE; }
   }

//+------------------------------------------------------------------+
//| IsNewBar                                                         |
//+------------------------------------------------------------------+
bool CSovereignEma21Harvest::IsNewBar()
   {
    datetime bt = iTime(m_symbol, m_timeframe, 0);
    if(bt <= 0) return false;
    if(m_lastBarTime == 0) { m_lastBarTime = bt; return false; }
    if(bt != m_lastBarTime) { m_lastBarTime = bt; return true; }
    return false;
   }

//+------------------------------------------------------------------+
//| CopyBuffers                                                      |
//+------------------------------------------------------------------+
bool CSovereignEma21Harvest::CopyBuffers()
   {
    const int need = 100;
    if(!SovCopyBuffer(m_hEma21, 0, 1, need, m_ema21Buf, "EMA21")) return false;
    if(!SovCopyBuffer(m_hRsi, 0, 1, need, m_rsiBuf, "RSI9")) return false;
    if(!SovCopyBuffer(m_hAtr, 0, 1, need, m_atrBuf, "ATR14")) return false;
    if(CopyClose(m_symbol, m_timeframe, 1, need, m_clBuf) < need) return false;
    if(CopyHigh(m_symbol, m_timeframe, 1, need, m_hiBuf) < need) return false;
    if(CopyLow(m_symbol, m_timeframe, 1, need, m_loBuf) < need) return false;
    ArraySetAsSeries(m_clBuf, true);
    ArraySetAsSeries(m_hiBuf, true);
    ArraySetAsSeries(m_loBuf, true);
    return true;
   }

//+------------------------------------------------------------------+
//| OnNewBar                                                         |
//+------------------------------------------------------------------+
void CSovereignEma21Harvest::OnNewBar(const datetime now, const bool allowEntries, const double equity,
                                      CSovereignPerformance &perf, CSovereignTrafficLight &tl,
                                      CSovereignAuditLog &audit, CTrade &t)
   {
    if(!IsNewBar()) return;
    if(!CopyBuffers()) return;

    int n = ArraySize(m_clBuf);
    if(n < 30) return;

    double c_i = m_clBuf[0];
    double c_prev = m_clBuf[1];
    double e21_i = m_ema21Buf[0];
    double e21_prev = m_ema21Buf[1];
    double rsi_i = m_rsiBuf[0];
    double atr_i = m_atrBuf[0];

    //--- track consecutive closes on each side of EMA21
    if(c_i > e21_i)
       {
        m_consecLong++;
        m_consecShort = 0;
       }
    else if(c_i < e21_i)
       {
        m_consecShort++;
        m_consecLong = 0;
       }
    else
       {
        m_consecLong = 0;
        m_consecShort = 0;
       }

    //==================================================================
    //--- IN POSITION: manage exits                                    |
    //==================================================================
    if(m_inPosition)
       {
        m_barsHeld++;

        bool exitNow = false;
        string exitReason = "";

        //--- EMA21 break exit
        if(m_exitOnEmaBreak)
           {
            if(m_positionSide > 0 && c_i < e21_i)
               { exitNow = true; exitReason = "ema_break"; }
            if(m_positionSide < 0 && c_i > e21_i)
               { exitNow = true; exitReason = "ema_break"; }
           }

        //--- RSI extreme exit
        if(!exitNow)
           {
            if(m_positionSide > 0 && rsi_i >= (double)m_rsiExitLong)
               { exitNow = true; exitReason = "rsi_extreme"; }
            if(m_positionSide < 0 && rsi_i <= (double)m_rsiExitShort)
               { exitNow = true; exitReason = "rsi_extreme"; }
           }

        //--- ATR trailing stop
        if(!exitNow && m_trailAtr > 0.0 && m_barsHeld > 0)
           {
            double a = m_entryAtr;
            if(a <= 0.0) a = atr_i;
            if(m_positionSide > 0)
               {
                m_highPx = MathMax(m_highPx, m_hiBuf[0]);
                double trailPx = m_highPx - m_trailAtr * a;
                if(c_i <= trailPx)
                   { exitNow = true; exitReason = "trail_stop"; }
               }
            else
               {
                m_lowPx = MathMin(m_lowPx, m_loBuf[0]);
                double trailPx = m_lowPx + m_trailAtr * a;
                if(c_i >= trailPx)
                   { exitNow = true; exitReason = "trail_stop"; }
               }
           }

        //--- Time stop
        if(!exitNow && m_maxHold > 0 && m_barsHeld >= m_maxHold)
           { exitNow = true; exitReason = "time_stop"; }

        if(exitNow)
           {
            PrintFormat("Sovereign EMA harvest %s: EXIT %s (side=%d barsHeld=%d c=%.5f ema=%.5f rsi=%.1f)",
                        m_symbol, exitReason, m_positionSide, m_barsHeld, c_i, e21_i, rsi_i);
            ClosePosition(t, exitReason);
            return;
           }
       }

    //==================================================================
    //--- FLAT: look for entry                                         |
    //==================================================================
    if(!m_inPosition)
       {
        if(!allowEntries)
           {
            m_lastBlockReason = "entries blocked (daily DD / century halt)";
            m_lastBlockTime = now;
            return;
           }

        //--- traffic light gate
        TrafficLightResult tlr = tl.Evaluate(1, 1, atr_i, 0.5, 0, true, 10, 12, true, true);
        if(tlr.hardVeto || !tlr.allowed)
           {
            m_lastBlockReason = "traffic light red";
            m_lastBlockTime = now;
            return;
           }

        //--- Entry: long cross confirmed by min_consec_long closes above EMA21
        //      (previous close was at/below EMA21 = cross just happened)
        if(m_consecLong >= m_minConsecutiveLong && c_prev <= e21_prev)
           {
            if(!CanPyramidInProfit((ulong)m_magic, m_symbol))
               {
               m_lastBlockReason = "pyramiding guard (position not in profit)";
               m_lastBlockTime = now;
               return;
               }
            OpenPosition(1, c_i, atr_i, t, perf, tl, audit, "SOV-EMA21-HARVEST");
            return;
           }

        //--- Entry: short cross confirmed by min_consec_short closes below EMA21
        if(m_consecShort >= m_minConsecutiveShort && c_prev >= e21_prev)
           {
            if(!CanPyramidInProfit((ulong)m_magic, m_symbol))
               {
               m_lastBlockReason = "pyramiding guard (position not in profit)";
               m_lastBlockTime = now;
               return;
               }
            OpenPosition(-1, c_i, atr_i, t, perf, tl, audit, "SOV-EMA21-HARVEST");
            return;
           }
       }
   }

//+------------------------------------------------------------------+
//| OpenPosition                                                     |
//+------------------------------------------------------------------+
void CSovereignEma21Harvest::OpenPosition(const int side, const double price, const double atr,
                                          CTrade &t, CSovereignPerformance &perf,
                                          CSovereignTrafficLight &tl, CSovereignAuditLog &audit,
                                          const string source)
   {
    double sl = 0.0, tp = 0.0;
    double lot = CalcLot(side, price, atr);

    if(side > 0)
       {
        sl = price - 1.5 * atr;
        tp = price + 3.0 * atr;
       }
    else
       {
        sl = price + 1.5 * atr;
        tp = price - 3.0 * atr;
       }

    ulong ticket = 0;
    if(side > 0)
       ticket = t.Buy(lot, m_symbol, price, sl, tp, source);
    else
       ticket = t.Sell(lot, m_symbol, price, sl, tp, source);

    if(ticket > 0)
       {
        m_inPosition = true;
        m_positionSide = side;
        m_entryPrice = price;
        m_entryAtr = atr;
        m_entryBarIndex = 0;
        m_barsHeld = 0;
        m_highPx = price;
        m_lowPx = price;

        SovLogInfo("Sovereign EMA harvest " + m_symbol + ": ORDER PLACED side=" + IntegerToString(side) +
                   " lot=" + DoubleToString(lot,2) + " entry=" + DoubleToString(price, _Digits) +
                   " sl=" + DoubleToString(sl, _Digits) + " tp=" + DoubleToString(tp, _Digits) +
                   " riskPct=" + DoubleToString(m_riskPct,2) + "% order=" + IntegerToString((long)ticket));
       }
    else
       {
        SovLogError("ORDER FAILED side=" + IntegerToString(side) + " lot=" + DoubleToString(lot,2) +
                    " price=" + DoubleToString(price, _Digits) + " sl=" + DoubleToString(sl, _Digits) +
                    " tp=" + DoubleToString(tp, _Digits) + " retcode=" + IntegerToString((int)t.ResultRetcode()) +
                    " (" + t.ResultRetcodeDescription() + ") err=" + IntegerToString(GetLastError()), m_symbol);
       }
   }

//+------------------------------------------------------------------+
//| ClosePosition                                                    |
//+------------------------------------------------------------------+
void CSovereignEma21Harvest::ClosePosition(CTrade &t, const string reason)
   {
    if(!m_inPosition) return;

    CPositionInfo p;
    bool found = false;
    ulong targetTicket = 0;

    for(int i = PositionsTotal() - 1; i >= 0; i--)
       {
        if(!p.SelectByIndex(i))
           {
            SovLogError("PositionSelectByIndex", m_symbol);
            continue;
           }
        if(p.Magic() != m_magic || p.Symbol() != m_symbol)
           continue;
        found = true;
        targetTicket = p.Ticket();
        break;
       }

    if(found)
       {
         if(!t.PositionClose(targetTicket))
            SovLogError("ClosePosition", "t.PositionClose failed retcode=" + IntegerToString((long)t.ResultRetcode()) +
                        " desc=" + t.ResultRetcodeDescription() + " err=" + IntegerToString(GetLastError()));
       }

    SovLogInfo("ClosePosition: " + reason + " " + m_symbol + " side=" + IntegerToString(m_positionSide) +
               " barsHeld=" + IntegerToString(m_barsHeld));
    m_inPosition = false;
    m_positionSide = 0;
    m_entryPrice = 0.0;
    m_entryAtr = 0.0;
    m_barsHeld = 0;
    m_highPx = 0.0;
    m_lowPx = 0.0;
   }

//+------------------------------------------------------------------+
//| CalcLot                                                          |
//+------------------------------------------------------------------+
double CSovereignEma21Harvest::CalcLot(const int side, const double price, const double atr)
   {
    double equity = AccountInfoDouble(ACCOUNT_EQUITY);
    if(equity <= 0) equity = 1000.0;
    double riskAmt = equity * (m_riskPct / 100.0);
    double tickSize = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_TICK_SIZE);
    double tickValue = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_TICK_VALUE);
    if(tickSize <= 0) tickSize = _Point;
    if(tickValue <= 0) tickValue = 1.0;
    double slDist = 1.5 * atr;
    if(slDist <= 0) slDist = 10 * _Point;
    double lots = riskAmt / (slDist / tickSize * tickValue);
    double minLot = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MIN);
    double maxLot = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MAX);
    double lotStep = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_STEP);
    lots = MathFloor(lots / lotStep) * lotStep;
    lots = MathMax(minLot, MathMin(maxLot, lots));
    return lots;
   }

#endif
