//+------------------------------------------------------------------+
//|                                  SovereignChartSnapshot.mqh      |
//|                                                                  |
//|  Writes a machine-readable record of what the EA actually saw on  |
//|  the last completed bar: every indicator value, the regime, the   |
//|  context grades, the action text, the plan, the pattern match.    |
//|                                                                  |
//|  Purpose: proof, not decoration.                                    |
//|  research/chart_snapshot.py recomputes the same fields from the    |
//|  same broker bars, and scripts/compare_chart_snapshot.py diffs     |
//|  the two files. If they agree within one point, the bot's read of  |
//|  the chart is the same read a human has with the template loaded.  |
//|                                                                  |
//|  Written once per new bar to                                       |
//|    MQL5/Files/Sovereign/chart_read_<SYMBOL>_<TF>.json  (FILE_COMMON)|
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_CHART_SNAPSHOT_MQH__
#define __SOVEREIGN_CHART_SNAPSHOT_MQH__

#include <Sovereign\SovereignMarketView.mqh>

class CSovereignChartSnapshot
{
private:
   string          m_symbol;
   ENUM_TIMEFRAMES m_tf;
   datetime        m_lastBar;
   int             m_writes;

   static string TfName(const ENUM_TIMEFRAMES tf)
   {
      switch(tf)
      {
         case PERIOD_M1:  return "M1";
         case PERIOD_M5:  return "M5";
         case PERIOD_M15: return "M15";
         case PERIOD_M30: return "M30";
         case PERIOD_H1:  return "H1";
         case PERIOD_H4:  return "H4";
         case PERIOD_D1:  return "D1";
         case PERIOD_W1:  return "W1";
         case PERIOD_MN1: return "MN1";
      }
      return "TF" + IntegerToString((int)tf);
   }

   static string Esc(string s)
   {
      StringReplace(s, "\\", "/");
      StringReplace(s, "\"", "'");
      StringReplace(s, "\n", " ");
      StringReplace(s, "\r", " ");
      return s;
   }

   static string Num(const double v, const int digits)
   {
      if(v == 0.0)
         return "0";
      return DoubleToString(v, digits);
   }

public:
   CSovereignChartSnapshot()
   {
      m_symbol  = "";
      m_tf      = PERIOD_H1;
      m_lastBar = 0;
      m_writes  = 0;
   }

   void Init(const string sym, const ENUM_TIMEFRAMES tf)
   {
      m_symbol  = sym;
      m_tf      = tf;
      m_lastBar = 0;
      m_writes  = 0;
   }

   string Path() const
   {
      return "Sovereign\\chart_read_" + m_symbol + "_" + TfName(m_tf) + ".json";
   }

   int  Writes() const { return m_writes; }

   //--- true when a new completed bar is available
   bool Due() const
   {
      datetime b = iTime(m_symbol, m_tf, 1);
      return (b > 0 && b != m_lastBar);
   }

   bool Write(const SovMarketView &v,
              const int regime, const int ctxL, const int ctxS,
              const string action, const string issues,
              const string d1Tag, const string pattern,
              const bool tplLoaded, const int tplFast, const int tplSlow,
              const int tplSmma, const int tplRsi,
              const bool tplAligned, const bool chartMatches)
   {
      if(m_symbol == "" || !v.ok || v.barTime <= 0)
         return false;

      int digits = (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS);
      if(digits <= 0)
         digits = 5;
      int pdigits = digits + 1;   // ATR / zone widths need one more

      int h = FileOpen(Path(), FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
      if(h == INVALID_HANDLE)
      {
         Print("SNAPSHOT: cannot open ", Path(), " err=", GetLastError());
         return false;
      }

      FileWriteString(h, "{\n");
      FileWriteString(h, "  \"schema_version\": 1,\n");
      FileWriteString(h, "  \"source\": \"ea\",\n");
      FileWriteString(h, "  \"symbol\": \"" + Esc(m_symbol) + "\",\n");
      FileWriteString(h, "  \"period\": \"" + TfName(m_tf) + "\",\n");
      FileWriteString(h, "  \"bar_time\": \"" +
                        TimeToString(v.barTime, TIME_DATE | TIME_MINUTES) + "\",\n");
      FileWriteString(h, "  \"bar_time_epoch\": " + IntegerToString((long)v.barTime) + ",\n");

      FileWriteString(h, "  \"price\": " + Num(v.price, digits) + ",\n");
      FileWriteString(h, "  \"ema_fast\": " + Num(v.emaFast, digits) + ",\n");
      FileWriteString(h, "  \"ema_slow\": " + Num(v.emaSlow, digits) + ",\n");
      FileWriteString(h, "  \"smma\": " + Num(v.smma, digits) + ",\n");
      FileWriteString(h, "  \"rsi\": " + Num(v.rsi, 2) + ",\n");
      FileWriteString(h, "  \"atr\": " + Num(v.atr, pdigits) + ",\n");

      FileWriteString(h, "  \"swing_hi\": " + Num(v.swingHi, digits) + ",\n");
      FileWriteString(h, "  \"swing_lo\": " + Num(v.swingLo, digits) + ",\n");
      FileWriteString(h, "  \"fib50\": " + Num(v.fib50, digits) + ",\n");
      FileWriteString(h, "  \"fib618\": " + Num(v.fib618, digits) + ",\n");
      FileWriteString(h, "  \"s1\": " + Num(v.s1, digits) + ",\n");
      FileWriteString(h, "  \"r1\": " + Num(v.r1, digits) + ",\n");

      FileWriteString(h, "  \"trend\": " + IntegerToString(v.trend) + ",\n");
      FileWriteString(h, "  \"bias\": " + IntegerToString(v.bias) + ",\n");
      FileWriteString(h, "  \"state\": \"" + Esc(v.state) + "\",\n");
      FileWriteString(h, "  \"plan\": \"" + Esc(v.plan) + "\",\n");
      FileWriteString(h, "  \"zone_lo\": " + Num(v.zoneLo, digits) + ",\n");
      FileWriteString(h, "  \"zone_hi\": " + Num(v.zoneHi, digits) + ",\n");
      FileWriteString(h, "  \"target\": " + Num(v.target, digits) + ",\n");
      FileWriteString(h, "  \"invalidation\": " + Num(v.invalidation, digits) + ",\n");

      FileWriteString(h, "  \"regime\": " + IntegerToString(regime) + ",\n");
      FileWriteString(h, "  \"ctx_long\": " + IntegerToString(ctxL) + ",\n");
      FileWriteString(h, "  \"ctx_short\": " + IntegerToString(ctxS) + ",\n");
      FileWriteString(h, "  \"action\": \"" + Esc(action) + "\",\n");
      FileWriteString(h, "  \"issues\": \"" + Esc(issues) + "\",\n");
      FileWriteString(h, "  \"d1_tag\": \"" + Esc(d1Tag) + "\",\n");
      FileWriteString(h, "  \"pattern\": \"" + Esc(pattern) + "\",\n");

      FileWriteString(h, "  \"template\": {\n");
      FileWriteString(h, "    \"loaded\": " + (tplLoaded ? "true" : "false") + ",\n");
      FileWriteString(h, "    \"ema_fast\": " + IntegerToString(tplFast) + ",\n");
      FileWriteString(h, "    \"ema_slow\": " + IntegerToString(tplSlow) + ",\n");
      FileWriteString(h, "    \"smma\": " + IntegerToString(tplSmma) + ",\n");
      FileWriteString(h, "    \"rsi\": " + IntegerToString(tplRsi) + ",\n");
      FileWriteString(h, "    \"aligned\": " + (tplAligned ? "true" : "false") + ",\n");
      FileWriteString(h, "    \"chart_matches\": " + (chartMatches ? "true" : "false") + "\n");
      FileWriteString(h, "  }\n");
      FileWriteString(h, "}\n");

      FileClose(h);
      m_lastBar = v.barTime;
      m_writes++;
      return true;
   }
};

#endif
