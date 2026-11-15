//+------------------------------------------------------------------+
//|                                        SovereignRegimeFilter.mqh |
//|      Regime-filtered MA compression rubberband (H1 overlay)      |
//|                                                                  |
//|  Core idea: do not trade when MAs are compressed (chop/fake      |
//|  crosses). Only fade extensions during MA compression snap-back. |
//|                                                                  |
//|  Regime filters (skip bad moves):                                |
//|    - MA compression: EMA8/21/50 within compressAtr*ATR -> flat   |
//|    - Low vol: ATR pctile < volPctileMin -> flat                  |
//|    - Range: ADX < adxMin -> flat                                 |
//|    - Late session: hour >= sessionCutoff -> flat                 |
//|    - Wide spread: spread > maxSpread pts -> flat                 |
//|    - ATR squeeze: ATR < atrRatioMin * 20-bar avg ATR -> flat     |
//|                                                                  |
//|  Entry (only when NOT filtered):                                 |
//|    LONG : price < MA_center - extAtr*ATR  (during compression)   |
//|    SHORT: price > MA_center + extAtr*ATR  (during compression)   |
//|  Exit:   price crosses back through MA_center                    |
//|  Stop:   1.5 ATR   |  TP: 2.0 ATR   |  max 24 bars hold          |
//|  One position at a time. Risk % of equity per trade.             |
//|+------------------------------------------------------------------+
#ifndef __SOVEREIGN_REGIME_FILTER_MQH__
#define __SOVEREIGN_REGIME_FILTER_MQH__

#include <Trade\Trade.mqh>
#include <Sovereign\SovereignError.mqh>

class CSovereignPerformance;
class CSovereignTrafficLight;
class CSovereignAuditLog;

class CSovereignRegimeFilter
{
private:
   string   m_symbol;
   long     m_magic;
   int      m_hEma8;
   int      m_hEma21;
   int      m_hEma50;
   int      m_hAtr14;
   int      m_hAdx14;

   //--- regime filter inputs
   int      m_regimeMode;       // 0=none (trade all), 1=soft (size down), 2=hard (skip)
   double   m_compressAtr;    // MA compression threshold (x ATR)
   double   m_volPctileMin;   // min ATR pctile to trade (0-1)
   int      m_adxMin;         // min ADX to trade
   int      m_sessionCutoff;  // hour >= this -> reduce size
   int      m_maxSpread;      // max spread pts
   double   m_atrRatioMin;    // min ATR ratio vs 20-bar avg

   //--- trade inputs
   double   m_extAtr;         // extension threshold (x ATR from MA center)
   double   m_stopAtr;        // stop distance (x ATR)
   double   m_tpAtr;          // take profit distance (x ATR)
   double   m_riskPct;        // risk per trade % of equity
   int      m_maxHoldBars;    // max bars in position

   //--- buffers
   double   m_ema8Buf[];
   double   m_ema21Buf[];
   double   m_ema50Buf[];
   double   m_atrBuf[];
   double   m_adxBuf[];
   double   m_clBuf[];
   double   m_hiBuf[];
   double   m_loBuf[];

   //--- ATR history for ratio and pctile
   double   m_atrHist[200];
   int      m_atrCount;
   int      m_atrHead;

   //--- position state
   bool     m_inPosition;
   int      m_positionSide;    // +1 long, -1 short
   double   m_entryPrice;
   double   m_stopPrice;
   double   m_tpPrice;
   double   m_entryAtr;
   int      m_barsHeld;

   datetime m_lastBarTime;

    bool IsNewBar();
    bool CopyBuffers();
     int  ComputeRegime() const;            // +1 long-bias, -1 short-bias, 0 no setup
    double ComputeRegimeMultiplier();       // 1.0=full size, 0.25=min size (soft mode)
    double PctileATR();                     // trailing ATR percentile of current bar
    double AtrRatio() const;                // current ATR / 20-bar avg ATR
   int    VolatilityRegime();        // 0=low, 1=normal, 2=high
   int    TrendRegime();             // 0=ranging, 1=trending
   double RegimeSizeMultiplier();    // combined sizing factor 0.25-1.0
    double NormalizeVolume(const double volume) const;
    double LotsForRisk(const double riskMoney, const double stopDist) const;
    void OpenPosition(const int side, const double price, const double atr,
                       CTrade &t, CSovereignPerformance &perf,
                       CSovereignTrafficLight &tl, CSovereignAuditLog &audit,
                       const string comment, const double sizeMult);
    void ClosePosition(CTrade &t, CSovereignAuditLog &audit,
                       const string reason);
    void LogOrderAttempt(const string action, const double price,
                         const double volume, const uint retcode,
                         const string comment, const int spread,
                         CSovereignAuditLog &audit);

public:
   CSovereignRegimeFilter();
   ~CSovereignRegimeFilter();
   bool Init(const string symbol, const long magic,
             const int regimeMode,            // 0=none, 1=soft, 2=hard
             const double compressAtr, const double volPctileMin,
             const int adxMin, const int sessionCutoff,
             const int maxSpread, const double atrRatioMin,
             const double extAtr, const double stopAtr,
             const double tpAtr, const double riskPct,
             const int maxHoldBars);
   void Deinit();
   void OnNewBar(const datetime now, const bool allowEntries, const double equity,
                 CSovereignPerformance &perf, CSovereignTrafficLight &tl,
                 CSovereignAuditLog &audit, CTrade &t);
   bool InPosition() const { return m_inPosition; }
   int  PositionSide() const { return m_positionSide; }
   double EntryPrice() const { return m_entryPrice; }
   int  Regime() const { return ComputeRegime(); }
};
#endif // __SOVEREIGN_REGIME_FILTER_MQH__

//+------------------------------------------------------------------+
//| Constructor                                                      |
//+------------------------------------------------------------------+
CSovereignRegimeFilter::CSovereignRegimeFilter()
{
   m_symbol = "";
   m_magic = 0;
   m_hEma8 = INVALID_HANDLE;
   m_hEma21 = INVALID_HANDLE;
   m_hEma50 = INVALID_HANDLE;
   m_hAtr14 = INVALID_HANDLE;
   m_hAdx14 = INVALID_HANDLE;

   m_compressAtr = 0.5;
   m_volPctileMin = 0.33;
   m_adxMin = 20;
   m_sessionCutoff = 21;
   m_maxSpread = 12;
   m_atrRatioMin = 0.70;
   m_regimeMode = 1;   // default: soft sizing

   m_extAtr = 2.0;
   m_stopAtr = 1.5;
   m_tpAtr = 2.0;
   m_riskPct = 0.5;
   m_maxHoldBars = 24;

   m_atrCount = 0;
   m_atrHead = 0;
   ArrayInitialize(m_atrHist, 0.0);

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
CSovereignRegimeFilter::~CSovereignRegimeFilter()
{
   Deinit();
}
//+------------------------------------------------------------------+
//| Init                                                             |
//+------------------------------------------------------------------+
bool CSovereignRegimeFilter::Init(const string symbol, const long magic,
                                  const int regimeMode,            // 0=none, 1=soft, 2=hard
                                  const double compressAtr, const double volPctileMin,
                                  const int adxMin, const int sessionCutoff,
                                  const int maxSpread, const double atrRatioMin,
                                  const double extAtr, const double stopAtr,
                                  const double tpAtr, const double riskPct,
                                  const int maxHoldBars)
{
   m_symbol = symbol;
   m_magic = magic;
   m_regimeMode = regimeMode;
   m_compressAtr = MathMax(0.1, compressAtr);
   m_volPctileMin = MathMin(1.0, MathMax(0.0, volPctileMin));
   m_adxMin = MathMax(1, adxMin);
   m_sessionCutoff = MathMin(23, MathMax(0, sessionCutoff));
   m_maxSpread = MathMax(0, maxSpread);
   m_atrRatioMin = MathMax(0.1, atrRatioMin);
   m_extAtr = MathMax(0.5, extAtr);
   m_stopAtr = MathMax(0.5, stopAtr);
   m_tpAtr = MathMax(0.5, tpAtr);
   m_riskPct = MathMax(0.01, riskPct);
   m_maxHoldBars = MathMax(1, maxHoldBars);

   SovResetLastError();
   m_hEma8 = iMA(m_symbol, PERIOD_H1, 8, 0, MODE_EMA, PRICE_CLOSE);
   SovResetLastError();
   m_hEma21 = iMA(m_symbol, PERIOD_H1, 21, 0, MODE_EMA, PRICE_CLOSE);
   SovResetLastError();
   m_hEma50 = iMA(m_symbol, PERIOD_H1, 50, 0, MODE_EMA, PRICE_CLOSE);
   SovResetLastError();
   m_hAtr14 = iATR(m_symbol, PERIOD_H1, 14);
   SovResetLastError();
   m_hAdx14 = iADX(m_symbol, PERIOD_H1, 14);

   if(m_hEma8 == INVALID_HANDLE || m_hEma21 == INVALID_HANDLE ||
      m_hEma50 == INVALID_HANDLE || m_hAtr14 == INVALID_HANDLE ||
      m_hAdx14 == INVALID_HANDLE)
   {
      SovLogError("RegimeFilter: indicator handle failed for " + m_symbol);
      return false;
   }

   m_inPosition = false;
   m_positionSide = 0;
   m_entryPrice = 0.0;
   m_stopPrice = 0.0;
   m_tpPrice = 0.0;
   m_entryAtr = 0.0;
   m_barsHeld = 0;
   m_lastBarTime = 0;

   return true;
}
//+------------------------------------------------------------------+
//| Deinit                                                           |
//+------------------------------------------------------------------+
void CSovereignRegimeFilter::Deinit()
{
   if(m_hEma8 != INVALID_HANDLE) { IndicatorRelease(m_hEma8); m_hEma8 = INVALID_HANDLE; }
   if(m_hEma21 != INVALID_HANDLE) { IndicatorRelease(m_hEma21); m_hEma21 = INVALID_HANDLE; }
   if(m_hEma50 != INVALID_HANDLE) { IndicatorRelease(m_hEma50); m_hEma50 = INVALID_HANDLE; }
   if(m_hAtr14 != INVALID_HANDLE) { IndicatorRelease(m_hAtr14); m_hAtr14 = INVALID_HANDLE; }
   if(m_hAdx14 != INVALID_HANDLE) { IndicatorRelease(m_hAdx14); m_hAdx14 = INVALID_HANDLE; }
}
//+------------------------------------------------------------------+
//| IsNewBar — detect a new H1 bar                                   |
//+------------------------------------------------------------------+
bool CSovereignRegimeFilter::IsNewBar()
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
   bool CSovereignRegimeFilter::CopyBuffers()
   {
      int need = 60;

      ArraySetAsSeries(m_ema8Buf, true);
      SovResetLastError();
      if(!SovCheckHandle(m_hEma8, "iMA EMA8")) return false;
      if(!SovCopyBuffer(m_hEma8, 0, 1, need, m_ema8Buf, "EMA8")) return false;

      ArraySetAsSeries(m_ema21Buf, true);
      SovResetLastError();
      if(!SovCheckHandle(m_hEma21, "iMA EMA21")) return false;
      if(!SovCopyBuffer(m_hEma21, 0, 1, need, m_ema21Buf, "EMA21")) return false;

      ArraySetAsSeries(m_ema50Buf, true);
      SovResetLastError();
      if(!SovCheckHandle(m_hEma50, "iMA EMA50")) return false;
      if(!SovCopyBuffer(m_hEma50, 0, 1, need, m_ema50Buf, "EMA50")) return false;

      ArraySetAsSeries(m_atrBuf, true);
      SovResetLastError();
      if(!SovCheckHandle(m_hAtr14, "iATR14")) return false;
      if(!SovCopyBuffer(m_hAtr14, 0, 1, need, m_atrBuf, "ATR14")) return false;

      ArraySetAsSeries(m_adxBuf, true);
      SovResetLastError();
      if(!SovCheckHandle(m_hAdx14, "iADX14")) return false;
      if(!SovCopyBuffer(m_hAdx14, 0, 1, need, m_adxBuf, "ADX14")) return false;

      if(CopyClose(m_symbol, PERIOD_H1, 1, need, m_clBuf) < need) return false;
      if(CopyHigh(m_symbol, PERIOD_H1, 1, need, m_hiBuf) < need) return false;
      if(CopyLow(m_symbol, PERIOD_H1, 1, need, m_loBuf) < need) return false;

      return true;
   }
//+------------------------------------------------------------------+
//| ComputeRegimeMultiplier — position size multiplier by regime     |
//|   mode 0=none (always 1.0), 1=soft (gradual reduction),          |
//|   2=hard (0.0 = skip trade if any bad regime)                    |
//+------------------------------------------------------------------+
double CSovereignRegimeFilter::ComputeRegimeMultiplier()
{
   double ema8 = m_ema8Buf[0];
   double ema21 = m_ema21Buf[0];
   double ema50 = m_ema50Buf[0];
   double atr = m_atrBuf[0];
   double adx = m_adxBuf[0];
   double close = m_clBuf[0];

   if(ema8 <= 0 || ema21 <= 0 || ema50 <= 0 || atr <= 0 || close <= 0)
      return 1.0;

   //--- count bad regime conditions
   int badCount = 0;

   // 1. MA compression
   double maSpread = MathMax(MathAbs(ema8 - ema21),
                             MathMax(MathAbs(ema21 - ema50), MathAbs(ema8 - ema50)));
   bool compressed = (maSpread < m_compressAtr * atr);
   if(compressed) badCount++;

   // 2. Low volatility
   double pct = PctileATR();
   bool lowVol = (pct >= 0 && pct < m_volPctileMin);
   if(lowVol) badCount++;

   // 3. Range (low ADX)
   bool rangeMkt = (adx > 0 && adx < m_adxMin);
   if(rangeMkt) badCount++;

   // 4. Late session
   MqlDateTime dt;
   TimeToStruct(iTime(m_symbol, PERIOD_H1, 0), dt);
   bool lateSession = (dt.hour >= m_sessionCutoff);
   if(lateSession) badCount++;

   // 5. Wide spread
   int spread = (int)SymbolInfoInteger(m_symbol, SYMBOL_SPREAD);
   bool wideSpread = (spread > m_maxSpread);
   if(wideSpread) badCount++;

   // 6. ATR squeeze
   double ratio = AtrRatio();
   bool squeeze = (ratio > 0 && ratio < m_atrRatioMin);
   if(squeeze) badCount++;

   //--- apply mode
   if(m_regimeMode == 0)   // none — trade every signal at full size
      return 1.0;

   if(m_regimeMode == 2)   // hard — skip if any bad regime
      return (badCount > 0) ? 0.0 : 1.0;

   // mode 1 = soft — gradual reduction, floor at 25%
   double mult = 1.0 - (badCount * 0.20);
   if(mult < 0.25) mult = 0.25;
   return mult;
}
//+------------------------------------------------------------------+
//| VolatilityRegime — 0=low, 1=normal, 2=high                        |
//+------------------------------------------------------------------+
int CSovereignRegimeFilter::VolatilityRegime()
{
   double pct = PctileATR();
   if(pct < 0.0) return 1;
   if(pct < 0.33) return 0;   // low vol
   if(pct > 0.66) return 2;   // high vol
   return 1;                  // normal
}
//+------------------------------------------------------------------+
//| TrendRegime — 0=ranging, 1=trending                               |
//+------------------------------------------------------------------+
int CSovereignRegimeFilter::TrendRegime()
{
   double ema8 = m_ema8Buf[0];
   double ema21 = m_ema21Buf[0];
   double ema50 = m_ema50Buf[0];
   double adx = m_adxBuf[0];
   double close = m_clBuf[0];

   if(ema8 <= 0 || ema21 <= 0 || ema50 <= 0 || close <= 0) return 0;

   //--- trending if ADX >= 25 and EMA8/EMA21/EMA50 aligned
   bool aligned = (ema8 > ema21 && ema21 > ema50) ||
                  (ema8 < ema21 && ema21 < ema50);
   bool strongTrend = (adx > 0 && adx >= 25.0);
   return (aligned && strongTrend) ? 1 : 0;
}
//+------------------------------------------------------------------+
//| RegimeSizeMultiplier — combined sizing factor 0.25-1.0            |
//+------------------------------------------------------------------+
double CSovereignRegimeFilter::RegimeSizeMultiplier()
{
   int volRegime = VolatilityRegime();
   int trendRegime = TrendRegime();

   double mult = 1.0;
   if(volRegime == 2) mult *= 0.50;   // high vol: halve size
   if(volRegime == 0) mult *= 0.75;   // low vol: reduce slightly
   if(trendRegime == 0) mult *= 0.75; // ranging: reduce for mean-rev strategies
   if(mult < 0.25) mult = 0.25;
   return mult;
}
//+------------------------------------------------------------------+
//| ComputeRegime — trade bias (+1 long, -1 short, 0 no setup)       |
//+------------------------------------------------------------------+
int CSovereignRegimeFilter::ComputeRegime() const
{
   double ema8 = m_ema8Buf[0];
   double ema21 = m_ema21Buf[0];
   double ema50 = m_ema50Buf[0];
   double atr = m_atrBuf[0];
   double close = m_clBuf[0];

   if(ema8 <= 0 || ema21 <= 0 || ema50 <= 0 || atr <= 0 || close <= 0)
      return 0;

   double maCenter = (ema8 + ema21 + ema50) / 3.0;

   //--- rubberband: price extended beyond extAtr*ATR from MA center
   double extDist = m_extAtr * atr;
   if(close < maCenter - extDist)
      return 1;   // long fade setup
   if(close > maCenter + extDist)
      return -1;  // short fade setup

   return 0;   // not extended enough
}
//+------------------------------------------------------------------+
//| PctileATR — trailing percentile of current ATR over last 200     |
//+------------------------------------------------------------------+
double CSovereignRegimeFilter::PctileATR()
{
   double atr = m_atrBuf[0];
   if(atr <= 0) return -1;

   // record in rolling history
   m_atrHist[m_atrHead] = atr;
   m_atrHead++;
   if(m_atrHead >= 200) m_atrHead = 0;
   if(m_atrCount < 200) m_atrCount++;

   int n = m_atrCount;
   if(n < 20) return 1.0;   // not enough history, allow

   double sorted[200];
   for(int i = 0; i < n; i++) sorted[i] = 0.0;
   int idx = m_atrHead;
   int filled = 0;
   for(int i = 0; i < n; i++)
   {
      sorted[filled] = m_atrHist[idx];
      idx++;
      if(idx >= 200) idx = 0;
      filled++;
   }
   ArraySort(sorted);

   // rank of current atr
   int rank = 0;
   for(int i = 0; i < n; i++)
   {
      if(sorted[i] <= atr) rank++;
   }
   return (double)rank / (double)n;
}
//+------------------------------------------------------------------+
//| AtrRatio — current ATR / 20-bar average ATR                      |
//+------------------------------------------------------------------+
double CSovereignRegimeFilter::AtrRatio() const
{
   double atr = m_atrBuf[0];
   if(atr <= 0) return 0;

   // sum last 20 ATR values from history (approximate rolling)
   double sum = 0;
   int cnt = 0;
   int idx = m_atrHead;
   int n = m_atrCount;
   int take = MathMin(20, n);
   for(int i = 0; i < take; i++)
   {
      idx--;
      if(idx < 0) idx = 199;
      double v = m_atrHist[idx];
      if(v > 0) { sum += v; cnt++; }
   }
   if(cnt == 0) return 1.0;
   double avg = sum / (double)cnt;
   if(avg <= 0) return 1.0;
   return atr / avg;
}
//+------------------------------------------------------------------+
//| NormalizeVolume                                                  |
//+------------------------------------------------------------------+
double CSovereignRegimeFilter::NormalizeVolume(const double volume) const
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
double CSovereignRegimeFilter::LotsForRisk(const double riskMoney,
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
void CSovereignRegimeFilter::OpenPosition(const int side, const double price,
                                           const double atr,
                                           CTrade &t,
                                           CSovereignPerformance &perf,
                                           CSovereignTrafficLight &tl,
                                           CSovereignAuditLog &audit,
                                           const string comment,
                                           const double sizeMult)
{
   if(atr <= 0.0) return;
   if(sizeMult <= 0.0) return;

   double stopDist = atr * m_stopAtr;   // 1.5 ATR stop
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

   string legBucket = CSovereignPerformance::LegBucket("SOV|REGIME", m_symbol);
   if(!perf.AllowNewRisk(legBucket)) return;
   double govMult = perf.CombinedMultiplier(legBucket);
   if(govMult <= 0.0) return;

   riskMoney = equity * m_riskPct / 100.0 * govMult * sizeMult;
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
   }
   else
   {
      SovLogTradeRetcode(t, "RegimeFilter OpenPosition " + m_symbol + " side=" + IntegerToString(side), m_symbol);
   }
}
//+------------------------------------------------------------------+
//| ClosePosition                                                    |
//+------------------------------------------------------------------+
void CSovereignRegimeFilter::ClosePosition(CTrade &t, CSovereignAuditLog &audit,
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
      SovLogTradeRetcode(t, "RegimeFilter ClosePosition " + m_symbol + " reason=" + reason, m_symbol);

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
void CSovereignRegimeFilter::LogOrderAttempt(const string action, const double price,
                                              const double volume, const uint retcode,
                                              const string comment, const int spread,
                                              CSovereignAuditLog &audit)
{
   audit.LogOrderAttempt(action, "MARKET", price, price, volume,
                         spread, retcode, comment,
                         false, "", 0.0, 0.0);
}
//+------------------------------------------------------------------+
//| OnNewBar — acts only on new H1 bars                              |
//+------------------------------------------------------------------+
void CSovereignRegimeFilter::OnNewBar(const datetime now, const bool allowEntries,
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
   int regime = ComputeRegime();

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
      bool hit = false;
      if(m_positionSide > 0)  // long
      {
         //--- exit: price back to MA center (rubberband snap-back)
         double ema8 = m_ema8Buf[0];
         double ema21 = m_ema21Buf[0];
         double ema50 = m_ema50Buf[0];
         double maCenter = (ema8 + ema21 + ema50) / 3.0;
         if(closePx >= maCenter) { ClosePosition(t, audit, "snap-back"); return; }
         //--- hard stop / TP
         if(closePx <= m_stopPrice) { ClosePosition(t, audit, "stop/tp"); return; }
         if(closePx >= m_tpPrice)   { ClosePosition(t, audit, "stop/tp"); return; }
         //--- max hold
         if(m_barsHeld >= m_maxHoldBars) { ClosePosition(t, audit, "max-hold"); return; }
      }
      else  // short
      {
         double ema8 = m_ema8Buf[0];
         double ema21 = m_ema21Buf[0];
         double ema50 = m_ema50Buf[0];
         double maCenter = (ema8 + ema21 + ema50) / 3.0;
         if(closePx <= maCenter) { ClosePosition(t, audit, "snap-back"); return; }
         //--- hard stop / TP
         if(closePx >= m_stopPrice) { ClosePosition(t, audit, "stop/tp"); return; }
         if(closePx <= m_tpPrice)   { ClosePosition(t, audit, "stop/tp"); return; }
         //--- max hold
         if(m_barsHeld >= m_maxHoldBars) { ClosePosition(t, audit, "max-hold"); return; }
      }
   }

   //==================================================================
   //--- FLAT: look for entry (soft size multiplier by regime)       |
   //==================================================================
   if(!m_inPosition && allowEntries && regime != 0)
   {
      int spread = (int)SymbolInfoInteger(m_symbol, SYMBOL_SPREAD);
      double sizeMult = ComputeRegimeMultiplier();
      if(sizeMult <= 0.0) return;   // hard mode: skip
      OpenPosition(regime, closePx, m_atrBuf[0], trade, perf, tl, audit, "SOV-REGIME", sizeMult);
   }
}
//+------------------------------------------------------------------+
