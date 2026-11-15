//+------------------------------------------------------------------+
//|                               SovereignMarketSnapshot.mqh        |
//| Ecosystem Professor — reads market_snapshot.json (TA/fund/sent)  |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_MARKET_SNAPSHOT_MQH__
#define __SOVEREIGN_MARKET_SNAPSHOT_MQH__

class CSovereignMarketSnapshot
{
private:
   bool     m_enabled;
   datetime m_lastLoad;
   int      m_reloadSec;
   string   m_json;
   bool     m_halt;
   double   m_global;
   string   m_headline;

   int FindValueStart(const string json, const string key, const int from = 0) const
   {
      string needle = "\"" + key + "\"";
      int pos = StringFind(json, needle, from);
      if(pos < 0) return -1;
      pos += StringLen(needle);
      int len = StringLen(json);
      while(pos < len)
      {
         ushort c = StringGetCharacter(json, pos);
         if(c == ':' || c == ' ' || c == '\t' || c == '\n' || c == '\r')
            pos++;
         else
            break;
      }
      return pos;
   }

   double JsonNumberAt(const string json, const string key, const int from) const
   {
      int pos = FindValueStart(json, key, from);
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

   bool JsonBoolAt(const string json, const string key, const int from) const
   {
      int pos = FindValueStart(json, key, from);
      if(pos < 0) return false;
      return (StringSubstr(json, pos, 4) == "true");
   }

   string JsonStringAt(const string json, const string key, const int from) const
   {
      int pos = FindValueStart(json, key, from);
      if(pos < 0) return "";
      if(StringGetCharacter(json, pos) != '"') return "";
      int start = pos + 1;
      int end = StringFind(json, "\"", start);
      if(end < 0) return "";
      return StringSubstr(json, start, end - start);
   }

   int SymbolBlockStart(const string sym) const
   {
      int pos = StringFind(m_json, "\"symbol_gates\"");
      if(pos < 0) return -1;
      string needle = "\"" + sym + "\"";
      return StringFind(m_json, needle, pos);
   }

   bool IsExpired() const
   {
      int pos = FindValueStart(m_json, "expires_at");
      if(pos < 0) return false;
      string exp = "";
      if(StringGetCharacter(m_json, pos) == '"')
      {
         int start = pos + 1;
         int end = StringFind(m_json, "\"", start);
         if(end > start)
            exp = StringSubstr(m_json, start, end - start);
      }
      if(exp == "") return false;
      StringReplace(exp, "T", " ");
      int dot = StringFind(exp, ".");
      if(dot > 0) exp = StringSubstr(exp, 0, dot);
      datetime t = StringToTime(exp);
      if(t <= 0) return false;
      return (TimeCurrent() > t);
   }

   void ParseGlobal()
   {
      m_halt = false;
      m_global = 1.0;
      m_headline = "";
      int gpos = StringFind(m_json, "\"global\"");
      if(gpos < 0) return;
      m_halt = JsonBoolAt(m_json, "halt_new_risk", gpos);
      m_global = JsonNumberAt(m_json, "global_risk_mult", gpos);
      if(m_global <= 0.0) m_global = 1.0;
      if(m_global > 1.25) m_global = 1.25;
      m_headline = JsonStringAt(m_json, "professor_headline", gpos);
   }

public:
   CSovereignMarketSnapshot()
   {
      m_enabled = true;
      m_lastLoad = 0;
      m_reloadSec = 120;
      m_json = "";
      m_halt = false;
      m_global = 1.0;
      m_headline = "";
   }

   bool Init(const bool enabled, const int reloadSec = 120)
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

      int h = FileOpen("Sovereign\\market_snapshot.json", FILE_READ | FILE_TXT |
                       FILE_ANSI | FILE_COMMON);
      if(h == INVALID_HANDLE)
      {
         m_json = "";
         m_halt = false;
         m_global = 1.0;
         m_headline = "";
         return;
      }
      m_json = "";
      while(!FileIsEnding(h))
         m_json += FileReadString(h);
      FileClose(h);

      if(IsExpired())
      {
         m_json = "";
         m_halt = false;
         m_global = 1.0;
         m_headline = "snapshot expired";
         return;
      }
      ParseGlobal();
   }

   bool Enabled() const { return m_enabled; }
   bool Loaded() const { return (m_json != ""); }
   bool HaltNewRisk() const { return m_enabled && (m_halt || IsExpired()); }
   double GlobalMult() const { return m_enabled ? m_global : 1.0; }
   string Headline() const { return m_headline; }

   double SymbolRiskMult(const string sym) const
   {
      if(!m_enabled || m_json == "") return 1.0;
      int pos = SymbolBlockStart(sym);
      if(pos < 0) return 1.0;
      double m = JsonNumberAt(m_json, "risk_mult", pos);
      if(m <= 0.0) m = 1.0;
      if(m > 1.25) m = 1.25;
      return m;
   }

   bool BlockLong(const string sym) const
   {
      if(!m_enabled || HaltNewRisk()) return true;
      int pos = SymbolBlockStart(sym);
      if(pos < 0) return false;
      return JsonBoolAt(m_json, "block_long", pos);
   }

   bool BlockShort(const string sym) const
   {
      if(!m_enabled || HaltNewRisk()) return true;
      int pos = SymbolBlockStart(sym);
      if(pos < 0) return false;
      return JsonBoolAt(m_json, "block_short", pos);
   }

   string ProfessorLine(const string sym) const
   {
      if(!m_enabled || m_json == "") return "";
      int pos = SymbolBlockStart(sym);
      if(pos < 0) return m_headline;
      string line = JsonStringAt(m_json, "professor_line", pos);
      if(line == "") return m_headline;
      return line;
   }

   double CombinedMult(const string sym) const
   {
      if(HaltNewRisk()) return 0.0;
      return GlobalMult() * SymbolRiskMult(sym);
   }
};

#endif
