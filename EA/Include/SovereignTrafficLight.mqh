//+------------------------------------------------------------------+
//|                                   SovereignTrafficLight.mqh      |
//|          Pre-trade traffic-light board and risk scaling          |
//|                                                                  |
//| Every entry is checked against hard vetoes plus a board of soft   |
//| confirmations.  The board is always logged; blocking is optional  |
//| because research showed that requiring every light green selects  |
//| a very small, biased subset of trades.  Use it to observe, scale  |
//| risk, or enforce a minimum number of green lights.                |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_TRAFFIC_LIGHT_MQH__
#define __SOVEREIGN_TRAFFIC_LIGHT_MQH__

#include <Sovereign\SovereignError.mqh>

#define TL_RED    0
#define TL_YELLOW 1
#define TL_GREEN  2
#define TL_NA     3

struct TrafficLightResult
{
   bool   hardVeto;
   bool   allowed;
   int    green;
   int    yellow;
   int    red;
   int    na;
   int    total;
   double riskMultiplier;
   string reason;
   string summary;

   int    trend;
   int    structure;
   int    momentum;
   int    volatility;
   int    macro;
   int    sentiment;
   int    session;
   int    spread;
   int    room;
   int    opposing;
   int    pattern;
};

class CSovereignTrafficLight
{
private:
   bool   m_enabled;
   bool   m_log;
   string m_symbol;

   int    m_gateMode;          // 0 off, 1 observe/log, 2 soft gate, 3 all green
   int    m_minGreen;
   bool   m_riskScale;
   bool   m_useMacro;
   bool   m_usePattern;
   int    m_maxSpread;
   int    m_killStart;
   int    m_killEnd;
   double m_extremeThreshold;

   string m_lastVetoReason;
   datetime m_lastVetoTime;

   int    m_file;
   int    m_h1Adx;
   int    m_d1Sma200;
   int    m_m5Atr;
   int    m_m5Rsi2;

   string StateText(const int state) const
   {
      if(state == TL_GREEN)  return "GREEN";
      if(state == TL_YELLOW) return "YELLOW";
      if(state == TL_RED)    return "RED";
      return "NA";
   }

   string Csv(string value) const
   {
      StringReplace(value, ",", ";");
      StringReplace(value, "\r", " ");
      StringReplace(value, "\n", " ");
      return value;
   }

   void AddLight(TrafficLightResult &r, const int state) const
   {
      r.total++;
      if(state == TL_GREEN)       r.green++;
      else if(state == TL_YELLOW) r.yellow++;
      else if(state == TL_RED)    r.red++;
      else                        r.na++;
   }

   bool SymbolChange(const string symbol, const int shift, double &change) const
   {
      change = 0.0;
      if(!SymbolSelect(symbol, true)) return false;
      SovResetLastError();
      int bars = iBars(symbol, PERIOD_M5);
      if(bars < shift + 3)
      {
         SovLogWarn("SymbolChange: iBars(" + symbol + ")=" + IntegerToString(bars) + " < need " + IntegerToString(shift + 3));
         return false;
      }

      SovResetLastError();
      double newest = iClose(symbol, PERIOD_M5, 1);
      SovResetLastError();
      double oldest = iClose(symbol, PERIOD_M5, 1 + shift);
      if(newest <= 0.0 || oldest <= 0.0) return false;

      change = newest - oldest;
      return true;
   }

   int MacroLight(const int side, const int mode, const string symbol) const
   {
       double change = 0.0;
       string sym = symbol;
       if(!SymbolChange(sym, 6, change))
       {
          //--- Macro symbols (DXY/VIX) are unavailable on most brokers;
          //--- fall back to the main symbol's price change as a proxy.
          if(!SymbolChange(m_symbol, 6, change)) return TL_NA;
       }

      // For a long, a falling dollar / VIX is supportive.  For a short,
      // a rising dollar / VIX is supportive.  Mean reversion deliberately
      // wants the opposite (a short-term pullback against the trade).
      double alignment = -((double)side) * change;
      bool supportive = (mode == 1) ? (alignment > 0.0) : (alignment < 0.0);
      if(MathAbs(change) < 1e-12) return TL_YELLOW;
      return supportive ? TL_GREEN : TL_RED;
   }

   int VolatilityLight() const
   {
      if(m_m5Atr == INVALID_HANDLE) return TL_NA;
      double buffer[];
      SovResetLastError();
      int copied = CopyBuffer(m_m5Atr, 0, 1, 120, buffer);
      if(copied <= 0)
      {
         SovLogWarn("VolatilityLight: CopyBuffer ATR failed err=" + IntegerToString(GetLastError()));
         return TL_NA;
      }
      if(copied < 50) return TL_NA;

      double current = buffer[0];
      int below = 0;
      for(int i = 1; i < copied; i++)
         if(buffer[i] <= current) below++;

      double percentile = (double)below / (double)(copied - 1);
      if(percentile >= 0.20 && percentile <= 0.80) return TL_GREEN;
      if(percentile >= 0.10 && percentile <= 0.90) return TL_YELLOW;
      return TL_RED;
   }

   int TrendLight(const int side, const int htfTrend) const
   {
      bool h1Aligned = (htfTrend == side);
      bool d1Aligned = false;

      if(m_d1Sma200 != INVALID_HANDLE)
      {
         double sma[1];
         SovResetLastError();
         if(CopyBuffer(m_d1Sma200, 0, 0, 1, sma) >= 1)
         {
            SovResetLastError();
            double d1Close = iClose(m_symbol, PERIOD_D1, 0);
            if(d1Close > 0.0)
               d1Aligned = (side > 0) ? (d1Close > sma[0]) : (d1Close < sma[0]);
         }
      }

      if(h1Aligned && d1Aligned) return TL_GREEN;
      if(h1Aligned)              return TL_YELLOW;
      return TL_RED;
   }

   int StructureLight(const int side, const int mode,
                      const double rangePosition, const bool structureSignal) const
   {
      if(mode == 1) return structureSignal ? TL_GREEN : TL_RED;
      if(rangePosition < 0.0 || rangePosition > 1.0) return TL_NA;

      bool atEdge = (side > 0) ? (rangePosition <= m_extremeThreshold)
                               : (rangePosition >= 1.0 - m_extremeThreshold);
      if(atEdge) return TL_GREEN;

      bool nearEdge = (side > 0) ? (rangePosition <= m_extremeThreshold * 1.5)
                                 : (rangePosition >= 1.0 - m_extremeThreshold * 1.5);
      return nearEdge ? TL_YELLOW : TL_RED;
   }

   int MomentumLight(const int side, const int mode) const
   {
      if(m_h1Adx == INVALID_HANDLE) return TL_NA;

      double adx[1], plusDi[1], minusDi[1];
      bool adxReady = (SovCopyBuffer(m_h1Adx, 0, 1, 1, adx, "ADX0") &&
                       SovCopyBuffer(m_h1Adx, 1, 1, 1, plusDi, "ADX1") &&
                       SovCopyBuffer(m_h1Adx, 2, 1, 1, minusDi, "ADX2"));
      bool diAligned = adxReady &&
                       ((side > 0 && plusDi[0] > minusDi[0]) ||
                        (side < 0 && minusDi[0] > plusDi[0]));

      if(mode == 1)
      {
         double close1 = iClose(m_symbol, PERIOD_CURRENT, 1);
         double open1  = iOpen(m_symbol, PERIOD_CURRENT, 1);
         bool barAligned = (close1 > 0.0 && open1 > 0.0) &&
                           ((side > 0 && close1 > open1) ||
                            (side < 0 && close1 < open1));
         if(diAligned && barAligned && adxReady && adx[0] >= 18.0) return TL_GREEN;
         if(diAligned || barAligned) return TL_YELLOW;
         return TL_RED;
      }

      double rsi[1];
      bool rsiReady = (m_m5Rsi2 != INVALID_HANDLE &&
                       SovCopyBuffer(m_m5Rsi2, 0, 1, 1, rsi, "RSI2"));
      bool stretched = rsiReady &&
                       ((side > 0 && rsi[0] <= 15.0) ||
                        (side < 0 && rsi[0] >= 85.0));

      if(diAligned && stretched) return TL_GREEN;
      if(diAligned)              return TL_YELLOW;
      return TL_RED;
   }

   int RoomLight(const int side, const double atr) const
   {
      if(atr <= 0.0) return TL_NA;
      SovResetLastError();
      double price = (side > 0) ? SovSymbolDouble(m_symbol, SYMBOL_ASK, 0.0, "RoomLight price")
                                : SovSymbolDouble(m_symbol, SYMBOL_BID, 0.0, "RoomLight price");
      if(price <= 0.0) return TL_NA;

      double nearest = 0.0;
      for(int i = 1; i <= 100; i++)
      {
         if(side > 0)
         {
            double high = iHigh(m_symbol, PERIOD_CURRENT, i);
            if(high > price)
            {
               double distance = high - price;
               if(nearest == 0.0 || distance < nearest) nearest = distance;
            }
         }
         else
         {
            double low = iLow(m_symbol, PERIOD_CURRENT, i);
            if(low > 0.0 && low < price)
            {
               double distance = price - low;
               if(nearest == 0.0 || distance < nearest) nearest = distance;
            }
         }
      }

      if(nearest == 0.0) return TL_GREEN;

      double roomAtr = nearest / atr;
      if(roomAtr >= 1.5) return TL_GREEN;
      if(roomAtr >= 0.75) return TL_YELLOW;
      return TL_RED;
   }

   int OpposingLight(const int side, const double atr) const
   {
      if(atr <= 0.0) return TL_NA;
      SovResetLastError();
      double price = (side > 0) ? SovSymbolDouble(m_symbol, SYMBOL_ASK, 0.0, "OpposingLight price")
                                : SovSymbolDouble(m_symbol, SYMBOL_BID, 0.0, "OpposingLight price");
      if(price <= 0.0) return TL_NA;

      double level = 0.0;
      for(int i = 1; i <= 120; i++)
      {
         if(side > 0)
         {
            double high = iHigh(m_symbol, PERIOD_CURRENT, i);
            if(high > price)
            {
               double distance = high - price;
               if(level == 0.0 || distance < level)
                  level = distance;
            }
         }
         else
         {
            double low = iLow(m_symbol, PERIOD_CURRENT, i);
            if(low > 0.0 && low < price)
            {
               double distance = price - low;
               if(level == 0.0 || distance < level)
                  level = distance;
            }
         }
      }

      if(level == 0.0) return TL_GREEN;

      double target = (side > 0) ? (price + level) : (price - level);
      int touches = 0;
      double tolerance = 0.15 * atr;
      for(int i = 1; i <= 120; i++)
      {
         double extreme = (side > 0) ? iHigh(m_symbol, PERIOD_CURRENT, i)
                                     : iLow(m_symbol, PERIOD_CURRENT, i);
         if(extreme > 0.0 && MathAbs(extreme - target) <= tolerance)
            touches++;
      }

      if(touches >= 3) return TL_RED;
      if(touches == 2) return TL_YELLOW;
      return TL_GREEN;
   }

   int PatternLight(const int side, const int mode) const
   {
      if(!m_usePattern) return TL_NA;

      double open1  = iOpen(m_symbol, PERIOD_CURRENT, 1);
      double close1 = iClose(m_symbol, PERIOD_CURRENT, 1);
      double high1  = iHigh(m_symbol, PERIOD_CURRENT, 1);
      double low1   = iLow(m_symbol, PERIOD_CURRENT, 1);
      double open2  = iOpen(m_symbol, PERIOD_CURRENT, 2);
      double close2 = iClose(m_symbol, PERIOD_CURRENT, 2);
      if(open1 <= 0.0 || close1 <= 0.0 || high1 <= 0.0 || low1 <= 0.0 ||
         open2 <= 0.0 || close2 <= 0.0) return TL_NA;

      double range = high1 - low1;
      if(range <= 0.0) return TL_NA;

      double body  = MathAbs(close1 - open1);
      double upper = high1 - MathMax(open1, close1);
      double lower = MathMin(open1, close1) - low1;

      bool bullEngulf = (close1 > open1) && (open1 <= close2) &&
                        (close1 >= open2) && (close2 < open2);
      bool bearEngulf = (close1 < open1) && (open1 >= close2) &&
                        (close1 <= open2) && (close2 > open2);
      bool bullPin = (lower > 2.0 * body) && (close1 > open1);
      bool bearPin = (upper > 2.0 * body) && (close1 < open1);
      bool bullBar = (close1 > open1) && (body / range > 0.50);
      bool bearBar = (close1 < open1) && (body / range > 0.50);

      if(mode == 1)
      {
         if(side > 0 && (bullEngulf || bullBar)) return TL_GREEN;
         if(side < 0 && (bearEngulf || bearBar)) return TL_GREEN;
         return TL_YELLOW;
      }

      if(side > 0 && (bullEngulf || bullPin)) return TL_GREEN;
      if(side < 0 && (bearEngulf || bearPin)) return TL_GREEN;
      if(side > 0 && bearEngulf) return TL_RED;
      if(side < 0 && bullEngulf) return TL_RED;
      return TL_YELLOW;
   }

public:
   CSovereignTrafficLight()
   {
      m_enabled = false;
      m_log = false;
      m_symbol = "";
      m_gateMode = 1;
      m_minGreen = 6;
      m_riskScale = true;
      m_useMacro = true;
      m_usePattern = true;
      m_maxSpread = 10;
      m_killStart = 7;
      m_killEnd = 16;
      m_extremeThreshold = 0.20;
      m_file = INVALID_HANDLE;
      m_h1Adx = INVALID_HANDLE;
      m_d1Sma200 = INVALID_HANDLE;
      m_m5Atr = INVALID_HANDLE;
      m_m5Rsi2 = INVALID_HANDLE;
      m_lastVetoReason = "";
      m_lastVetoTime = 0;
   }

    bool Init(const bool enabled, const string symbol, const bool logEnabled)
   {
      m_enabled = enabled;
      m_log = logEnabled;
      m_symbol = symbol;
      m_file = INVALID_HANDLE;

      if(!enabled) return true;

      SovResetLastError();
      m_h1Adx    = iADX(symbol, PERIOD_H1, 14);
      SovResetLastError();
      m_d1Sma200 = iMA(symbol, PERIOD_D1, 200, 0, MODE_SMA, PRICE_CLOSE);
      SovResetLastError();
      m_m5Atr    = iATR(symbol, PERIOD_CURRENT, 14);
      SovResetLastError();
      m_m5Rsi2   = iRSI(symbol, PERIOD_CURRENT, 2, PRICE_CLOSE);

      if(m_h1Adx == INVALID_HANDLE || m_d1Sma200 == INVALID_HANDLE ||
         m_m5Atr == INVALID_HANDLE || m_m5Rsi2 == INVALID_HANDLE)
      {
         SovLogWarn("TrafficLight: one or more indicator handles failed; affected lights will report NA.");
      }

      if(logEnabled)
      {
         SovResetLastError();
         if(!FolderCreate("Sovereign"))
            SovLogWarn("TrafficLight: FolderCreate(Sovereign) failed err=" + IntegerToString(GetLastError()));
         string path = "Sovereign\\traffic_light.csv";
         bool existed = FileIsExist(path);
         SovResetLastError();
         m_file = FileOpen(path,
                           FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI |
                           FILE_SHARE_READ | FILE_SHARE_WRITE);
         if(m_file == INVALID_HANDLE)
         {
            SovLogWarn("TrafficLight: cannot open " + path + " err=" + IntegerToString(GetLastError()));
         }
         else
         {
            FileSeek(m_file, 0, SEEK_END);
            if(!existed || FileSize(m_file) == 0)
            {
               SovResetLastError();
               if(FileWriteString(m_file,
                  "server_time,symbol,mode,side,decision,hard_veto,green,yellow,red,na,"
                  "total,risk_mult,trend,structure,momentum,volatility,macro,sentiment,"
                  "session,spread,room,opposing,pattern,note\r\n") <= 0)
               {
                  SovLogWarn("TrafficLight: write header failed err=" + IntegerToString(GetLastError()));
               }
            }
            SovResetLastError();
            FileFlush(m_file);
            if(GetLastError() != 0)
               SovLogWarn("TrafficLight: FileFlush failed err=" + IntegerToString(GetLastError()));
         }
      }

      return true;
   }

   void Configure(const int gateMode, const int minGreen, const bool riskScale,
                  const bool useMacro, const bool usePattern,
                  const int maxSpread, const int killStart, const int killEnd,
                  const double extremeThreshold)
   {
      m_gateMode = gateMode;
      m_minGreen = minGreen;
      m_riskScale = riskScale;
      m_useMacro = useMacro;
      m_usePattern = usePattern;
      m_maxSpread = maxSpread;
      m_killStart = killStart;
      m_killEnd = killEnd;
      m_extremeThreshold = extremeThreshold;
   }

   // Each engine can have its own entry window.  The research showed that the
   // London-open breakout and mean reversion do not share the same edge.
   void SetSessionWindow(const int startHour, const int endHour)
   {
      m_killStart = startHour;
      m_killEnd = endHour;
   }

    void Deinit()
   {
      if(m_file != INVALID_HANDLE) { SovResetLastError(); FileClose(m_file); m_file = INVALID_HANDLE; }
      if(m_h1Adx != INVALID_HANDLE)    { SovResetLastError(); IndicatorRelease(m_h1Adx);    m_h1Adx = INVALID_HANDLE; }
      if(m_d1Sma200 != INVALID_HANDLE) { SovResetLastError(); IndicatorRelease(m_d1Sma200); m_d1Sma200 = INVALID_HANDLE; }
      if(m_m5Atr != INVALID_HANDLE)    { SovResetLastError(); IndicatorRelease(m_m5Atr);    m_m5Atr = INVALID_HANDLE; }
      if(m_m5Rsi2 != INVALID_HANDLE)   { SovResetLastError(); IndicatorRelease(m_m5Rsi2);   m_m5Rsi2 = INVALID_HANDLE; }
   }

   bool Enabled() const { return m_enabled; }

   //--- Spread veto threshold. The EA sets this per bar to a symbol-scaled cap
   //--- (max(configured, 2 x recent median spread)) - a fixed 10-point cap is
   //--- FX-calibrated and hard-vetoes every crypto/vol-index tick forever.
   void SetMaxSpread(const int pts) { if(pts > 0) m_maxSpread = pts; }
   int  MaxSpread() const { return m_maxSpread; }

   TrafficLightResult Evaluate(const int side, const int mode, const double atr,
                               const double rangePosition, const int htfTrend,
                               const bool governorOk, const int spreadPoints,
                               const int utcHour, const bool newsOk,
                               const bool structureSignal)
   {
      TrafficLightResult r;
      r.hardVeto = false;
      r.allowed = true;
      r.green = 0;
      r.yellow = 0;
      r.red = 0;
      r.na = 0;
      r.total = 0;
      r.riskMultiplier = 1.0;
      r.reason = "";
      r.summary = "";

      if(!m_enabled)
      {
         r.summary = "BOARD OFF";
         return r;
      }

      if(!governorOk) { r.hardVeto = true; r.reason = "governor"; }
      if(!newsOk)     { r.hardVeto = true; r.reason = "news"; }
      if(atr <= 0.0)  { r.hardVeto = true; r.reason = "atr"; }
      if(spreadPoints > m_maxSpread)
      {
         r.hardVeto = true;
         if(r.reason == "") r.reason = "spread";
      }
      if(mode == 1 && !structureSignal)
      {
         r.hardVeto = true;
         if(r.reason == "") r.reason = "structure";
      }

      r.trend      = TrendLight(side, htfTrend);
      r.structure  = StructureLight(side, mode, rangePosition, structureSignal);
      r.momentum   = MomentumLight(side, mode);
      r.volatility = VolatilityLight();
      r.macro      = m_useMacro ? MacroLight(side, mode, "DXYUSD") : TL_NA;
      r.sentiment  = m_useMacro ? MacroLight(side, mode, "VIXUSD") : TL_NA;
      r.session    = (utcHour >= m_killStart && utcHour <= m_killEnd) ? TL_GREEN : TL_RED;
      r.spread     = (spreadPoints <= m_maxSpread) ? TL_GREEN
                     : ((spreadPoints <= m_maxSpread + 5) ? TL_YELLOW : TL_RED);
      r.room       = RoomLight(side, atr);
      r.opposing   = OpposingLight(side, atr);
      r.pattern    = PatternLight(side, mode);

      AddLight(r, r.trend);
      AddLight(r, r.structure);
      AddLight(r, r.momentum);
      AddLight(r, r.volatility);
      AddLight(r, r.macro);
      AddLight(r, r.sentiment);
      AddLight(r, r.session);
      AddLight(r, r.spread);
      AddLight(r, r.room);
      AddLight(r, r.opposing);
      AddLight(r, r.pattern);

      int softTotal = r.total - r.na;
      if(m_riskScale && softTotal > 0)
      {
         r.riskMultiplier = 0.50 + 0.50 * ((double)r.green / (double)softTotal);
         r.riskMultiplier -= 0.05 * (double)r.red;
      }
      if(r.riskMultiplier < 0.35) r.riskMultiplier = 0.35;
      if(r.riskMultiplier > 1.00) r.riskMultiplier = 1.00;

      if(m_gateMode == 0)
      {
         r.allowed = true;
         r.riskMultiplier = 1.0;
      }
      else if(m_gateMode == 1)
      {
         // Observe and log only.  The EA's existing hard filters still run.
         r.allowed = true;
      }
      else if(m_gateMode == 2)
      {
         if(r.hardVeto || r.green < m_minGreen)
         {
            r.allowed = false;
            if(r.reason == "") r.reason = "min_green";
         }
      }
      else if(m_gateMode == 3)
      {
         if(r.hardVeto || r.green < softTotal)
         {
            r.allowed = false;
            if(r.reason == "") r.reason = "all_green";
         }
      }

      r.summary = StringFormat("G%d/Y%d/R%d/NA%d risk=%.2f",
                               r.green, r.yellow, r.red, r.na, r.riskMultiplier);

      //--- persist last veto reason for the status panel
      if(r.reason != "" && (r.hardVeto || (m_gateMode > 1 && !r.allowed)))
      {
         m_lastVetoReason = r.reason;
         m_lastVetoTime = TimeCurrent();
      }

      return r;
   }

   void Log(const TrafficLightResult &r, const int side, const int mode,
            const string note)
   {
      if(!m_enabled || !m_log || m_file == INVALID_HANDLE) return;

      string line = StringFormat(
         "%s,%s,%d,%d,%s,%d,%d,%d,%d,%d,%d,%.2f,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s",
         TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
         Csv(m_symbol),
         mode,
         side,
         r.allowed ? "ALLOW" : "BLOCK",
         r.hardVeto ? 1 : 0,
         r.green,
         r.yellow,
         r.red,
         r.na,
         r.total,
         r.riskMultiplier,
         StateText(r.trend),
         StateText(r.structure),
         StateText(r.momentum),
         StateText(r.volatility),
         StateText(r.macro),
         StateText(r.sentiment),
         StateText(r.session),
         StateText(r.spread),
         StateText(r.room),
         StateText(r.opposing),
         StateText(r.pattern),
         Csv(note));

      SovResetLastError();
      if(FileWriteString(m_file, line + "\r\n") <= 0)
         SovLogWarn("TrafficLight: FileWriteString failed err=" + IntegerToString(GetLastError()));
      SovResetLastError();
      FileFlush(m_file);
      if(GetLastError() != 0)
         SovLogWarn("TrafficLight: FileFlush failed err=" + IntegerToString(GetLastError()));
   }

   string LastVetoReason() const { return m_lastVetoReason; }
   datetime LastVetoTime() const { return m_lastVetoTime; }
};

#endif // __SOVEREIGN_TRAFFIC_LIGHT_MQH__
