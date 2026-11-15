//+------------------------------------------------------------------+
//|                                         SovereignCentury.mqh     |
//|  Automated long-horizon risk layer (reads century_directives)   |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_CENTURY_MQH__
#define __SOVEREIGN_CENTURY_MQH__

#include <Sovereign\SovereignError.mqh>

class CSovereignCentury
{
private:
   bool     m_enabled;
   datetime m_lastLoad;
   int      m_reloadSec;
   bool     m_halt;
   double   m_global;
   int      m_topN;
   string   m_json;

   int FindValueStart(const string json, const string key) const
   {
      string needle = "\"" + key + "\"";
      int pos = StringFind(json, needle);
      if(pos < 0) return -1;
      pos += StringLen(needle);
      int len = StringLen(json);
      while(pos < len)
      {
         ushort c = StringGetCharacter(json, pos);
         if(c == ':' || c == ' ' || c == '\t')
            pos++;
         else
            break;
      }
      return pos;
   }

   double JsonNumber(const string json, const string key) const
   {
      int pos = FindValueStart(json, key);
      if(pos < 0) return 0.0;
      int start = pos;
      int len = StringLen(json);
      while(pos < len)
      {
         ushort c = StringGetCharacter(json, pos);
         bool ok = (c >= '0' && c <= '9') || c == '-' || c == '+' || c == '.';
         if(!ok) break;
         pos++;
      }
      if(pos == start) return 0.0;
      return StringToDouble(StringSubstr(json, start, pos - start));
   }

   bool JsonBool(const string json, const string key) const
   {
      int pos = FindValueStart(json, key);
      if(pos < 0) return false;
      return (StringSubstr(json, pos, 4) == "true");
   }

   string JsonString(const string json, const string key) const
   {
      int pos = FindValueStart(json, key);
      if(pos < 0) return "";
      if(StringGetCharacter(json, pos) != '"') return "";
      int start = pos + 1;
      int end = StringFind(json, "\"", start);
      if(end < 0) return "";
      return StringSubstr(json, start, end - start);
   }

   bool IsExpired() const
   {
      string exp = JsonString(m_json, "expires_at");
      if(exp == "") return false;
      datetime t = StringToTime(exp);
      if(t <= 0) return false;
      return (TimeCurrent() > t);
   }

   bool ParseSkip(const string sym) const
   {
      int pos = StringFind(m_json, "\"skip_symbols\"");
      if(pos < 0) return false;
      string tail = StringSubstr(m_json, pos);
      if(StringFind(tail, "\"" + sym + "\"") >= 0)
         return true;
      return false;
   }

   double ParseLegMult(const string sym) const
   {
      string key = "\"" + sym + "\"";
      int pos = StringFind(m_json, key);
      if(pos < 0) return 1.0;
      string tail = StringSubstr(m_json, pos + StringLen(key));
      int colon = StringFind(tail, ":");
      if(colon < 0) return 1.0;
      return StringToDouble(StringSubstr(tail, colon + 1, 12));
   }

public:
   CSovereignCentury()
   {
      m_enabled = true;
      m_lastLoad = 0;
      m_reloadSec = 300;
      m_halt = false;
      m_global = 1.0;
      m_topN = 3;
   }

   bool Init(const bool enabled, const int reloadSec = 300)
   {
      m_enabled = enabled;
      m_reloadSec = MathMax(60, reloadSec);
      Reload(true);
      return true;
   }

   void Reload(const bool force = false)
   {
      if(!m_enabled) return;
      if(!force && TimeCurrent() - m_lastLoad < m_reloadSec)
         return;

      m_lastLoad = TimeCurrent();
      SovResetLastError();
      int h = FileOpen("Sovereign\\century_directives.json", FILE_READ | FILE_TXT |
                       FILE_ANSI | FILE_COMMON);
      if(h == INVALID_HANDLE)
      {
         SovLogWarn("Century: cannot open century_directives.json err=" + IntegerToString(GetLastError()));
         m_halt = false;
         m_global = 1.0;
         m_topN = 3;
         m_json = "";
         return;
      }

      m_json = "";
      while(!FileIsEnding(h))
         m_json += FileReadString(h);
      SovResetLastError();
      FileClose(h);
      if(GetLastError() != 0)
         SovLogWarn("Century: FileClose failed err=" + IntegerToString(GetLastError()));

      m_halt = JsonBool(m_json, "halt_trading") || IsExpired();
      m_global = JsonNumber(m_json, "global_risk_scalar");
      if(m_global <= 0.0) m_global = 1.0;
      if(m_global > 1.25) m_global = 1.25;
      int tn = (int)JsonNumber(m_json, "top_n");
      if(tn >= 1 && tn <= 6) m_topN = tn;
   }

   bool Enabled() const { return m_enabled; }
   bool HaltTrading() const { return m_enabled && m_halt; }
   double GlobalScalar() const { return m_enabled ? m_global : 1.0; }
   int TopN() const { return m_enabled ? m_topN : 3; }

   double SleeveWeight(const string sleeve) const
   {
      if(!m_enabled || m_json == "")
         return (sleeve == "carry") ? 1.0 : 0.0;
      string key = "\"" + sleeve + "\"";
      int p = StringFind(m_json, "\"sleeve_weights\"");
      if(p < 0)
         return (sleeve == "carry") ? 1.0 : 0.0;
      string tail = StringSubstr(m_json, p);
      int k = StringFind(tail, key);
      if(k < 0)
         return (sleeve == "carry") ? 1.0 : 0.0;
      string frag = StringSubstr(tail, k);
      int colon = StringFind(frag, ":");
      if(colon < 0)
         return 1.0;
      double w = StringToDouble(StringSubstr(frag, colon + 1, 8));
      if(w < 0.0) w = 0.0;
      if(w > 1.0) w = 1.0;
      return w;
   }

   bool SkipSymbol(const string sym) const
   {
      if(!m_enabled) return false;
      return ParseSkip(sym);
   }

   double LegScalar(const string sym) const
   {
      if(!m_enabled) return 1.0;
      double m = ParseLegMult(sym);
      if(m <= 0.0) m = 1.0;
      if(m > 1.25) m = 1.25;
      return m;
   }

   double CombinedLegScalar(const string sym) const
   {
      if(SkipSymbol(sym)) return 0.0;
      return GlobalScalar() * LegScalar(sym);
   }

   string Reason() const
   {
      return JsonString(m_json, "reason");
   }
};

#endif
