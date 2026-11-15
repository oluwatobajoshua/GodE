//+------------------------------------------------------------------+
//|                                    SovereignTemplate.mqh          |
//|  Reads the exported chart template (Sovereign/chart_template.json) |
//|  so the EA can align its analysis with the indicators the human    |
//|  actually sees on the chart.                                      |
//|                                                                  |
//|  Written by scripts/export_chart_template.py and copied by         |
//|  scripts/install_ea.ps1 to MQL5/Files/Sovereign/ (+ Common).       |
//|                                                                  |
//|  Read-only: this class never changes trading behaviour. It only    |
//|  reports alignment so a mismatch is visible instead of silent.     |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_TEMPLATE_MQH__
#define __SOVEREIGN_TEMPLATE_MQH__

class CSovereignTemplate
{
private:
   bool   m_loaded;
   string m_source;
   string m_symbol;
   string m_period;
   int    m_emaFast;
   int    m_emaSlow;
   int    m_smma;
   int    m_rsi;
   bool   m_fractals;

   static int IntField(const string body, const string key, const int def)
   {
      string needle = "\"" + key + "\"";
      int p = StringFind(body, needle);
      if(p < 0)
         return def;
      int c = StringFind(body, ":", p);
      if(c < 0)
         return def;
      string rest = StringSubstr(body, c + 1);
      int q = StringFind(rest, ",");
      if(q < 0)
         q = StringFind(rest, "}");
      if(q < 0)
         q = StringLen(rest);
      string num = StringSubstr(rest, 0, q);
      StringTrimLeft(num);
      StringTrimRight(num);
      if(StringLen(num) == 0)
         return def;
      return (int)StringToInteger(num);
   }

   static bool BoolField(const string body, const string key, const bool def)
   {
      string needle = "\"" + key + "\"";
      int p = StringFind(body, needle);
      if(p < 0)
         return def;
      int c = StringFind(body, ":", p);
      if(c < 0)
         return def;
      string rest = StringSubstr(body, c + 1, 8);
      if(StringFind(rest, "true") >= 0)
         return true;
      if(StringFind(rest, "false") >= 0)
         return false;
      return def;
   }

   static string StrField(const string body, const string key)
   {
      string needle = "\"" + key + "\"";
      int p = StringFind(body, needle);
      if(p < 0)
         return "";
      int c = StringFind(body, ":", p);
      if(c < 0)
         return "";
      int q1 = StringFind(body, "\"", c);
      if(q1 < 0)
         return "";
      int q2 = StringFind(body, "\"", q1 + 1);
      if(q2 < 0)
         return "";
      return StringSubstr(body, q1 + 1, q2 - q1 - 1);
   }

public:
   CSovereignTemplate()
   {
      m_loaded   = false;
      m_source   = "";
      m_symbol   = "";
      m_period   = "";
      m_emaFast  = 0;
      m_emaSlow  = 0;
      m_smma     = 0;
      m_rsi      = 0;
      m_fractals = false;
   }

   bool Load()
   {
      m_loaded = false;
      int h = FileOpen("Sovereign\\chart_template.json",
                       FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
      if(h == INVALID_HANDLE)
         return false;

      string body = "";
      while(!FileIsEnding(h))
         body += FileReadString(h) + "\n";
      FileClose(h);
      if(StringLen(body) < 10)
         return false;

      m_source   = StrField(body, "source");
      m_symbol   = StrField(body, "symbol");
      m_period   = StrField(body, "period");
      m_emaFast  = IntField(body, "ema_fast", 0);
      m_emaSlow  = IntField(body, "ema_slow", 0);
      m_smma     = IntField(body, "smma", 0);
      m_rsi      = IntField(body, "rsi", 0);
      m_fractals = BoolField(body, "fractals", false);
      m_loaded   = (m_emaFast > 0 && m_rsi > 0);
      return m_loaded;
   }

   bool   Loaded()   const { return m_loaded; }
   string Source()   const { return m_source; }
   string Symbol()   const { return m_symbol; }
   string Period()   const { return m_period; }
   int    EmaFast()  const { return m_emaFast; }
   int    EmaSlow()  const { return m_emaSlow; }
   int    Smma()     const { return m_smma; }
   int    Rsi()      const { return m_rsi; }
   bool   Fractals() const { return m_fractals; }

   bool Aligned(const int eaFast, const int eaSlow, const int eaSmma, const int eaRsi) const
   {
      if(!m_loaded)
         return false;
      return (eaFast == m_emaFast && eaSlow == m_emaSlow
              && eaSmma == m_smma && eaRsi == m_rsi);
   }

   //--- one line for the panel / log
   string Status(const int eaFast, const int eaSlow, const int eaSmma, const int eaRsi) const
   {
      if(!m_loaded)
         return "TEMPLATE: missing (run scripts\\install_ea.ps1)";
      if(Aligned(eaFast, eaSlow, eaSmma, eaRsi))
         return StringFormat("TEMPLATE: %s %s EMA%d/%d SMMA%d RSI%d  ALIGNED",
                             m_symbol, m_period, m_emaFast, m_emaSlow, m_smma, m_rsi);
      return StringFormat("TEMPLATE: EMA%d/%d SMMA%d RSI%d | EA: EMA%d/%d SMMA%d RSI%d  MISMATCH",
                          m_emaFast, m_emaSlow, m_smma, m_rsi,
                          eaFast, eaSlow, eaSmma, eaRsi);
   }

   //--- separate symbol/period check (template pins EURUSD H1)
   string ChartStatus(const string chartSymbol, const ENUM_TIMEFRAMES chartTf) const
   {
      if(!m_loaded)
         return "";
      string tf = TfName(chartTf);
      if(m_symbol == "" || m_period == "")
         return StringFormat("CHART: %s %s", chartSymbol, tf);
      if(chartSymbol == m_symbol && tf == m_period)
         return StringFormat("CHART: %s %s  MATCHES template", chartSymbol, tf);
      return StringFormat("CHART: %s %s | template %s %s  MISMATCH",
                          chartSymbol, tf, m_symbol, m_period);
   }

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
};

#endif
