//+------------------------------------------------------------------+
//|                              SovereignFxmopolProfiles.mqh        |
//| Per-symbol OOS manifest: trade only symbols with promoted=true   |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_FXMOPOL_PROFILES_MQH__
#define __SOVEREIGN_FXMOPOL_PROFILES_MQH__

class CSovereignFxmopolProfiles
{
private:
   bool   m_loaded;
   bool   m_promoted;
   int    m_preset;
   string m_reason;

   bool JsonFlagAfter(const string body, const int start, const string key) const
   {
      int p = StringFind(body, "\"" + key + "\":", start);
      if(p < 0)
         return false;
      p = StringFind(body, "true", p);
      int p2 = StringFind(body, "}", p);
      if(p < 0 || (p2 >= 0 && p > p2))
         return false;
      return true;
   }

   int JsonIntAfter(const string body, const int start, const string key, const int defVal) const
   {
      int p = StringFind(body, "\"" + key + "\":", start);
      if(p < 0)
         return defVal;
      p += StringLen(key) + 3;
      string tail = StringSubstr(body, p, 32);
      return (int)StringToInteger(tail);
   }

public:
   CSovereignFxmopolProfiles()
   {
      m_loaded = false;
      m_promoted = false;
      m_preset = -1;
      m_reason = "no_profile";
   }

   bool Load(const string symbol)
   {
      m_loaded = false;
      m_promoted = false;
      m_preset = -1;
      m_reason = "symbol_not_in_manifest";

      int h = FileOpen("Sovereign\\fxmopol_symbol_profiles.json",
                       FILE_READ | FILE_TXT | FILE_COMMON);
      if(h == INVALID_HANDLE)
      {
         m_reason = "missing_fxmopol_symbol_profiles.json";
         return false;
      }
      string body = "";
      while(!FileIsEnding(h))
         body += FileReadString(h);
      FileClose(h);

      string needle = "\"" + symbol + "\"";
      int s = StringFind(body, needle);
      if(s < 0)
      {
         m_loaded = true;
         return true;
      }
      int blockEnd = StringFind(body, "}", s);
      if(blockEnd < 0)
         blockEnd = s + 800;

      m_promoted = JsonFlagAfter(body, s, "promoted");
      m_preset = JsonIntAfter(body, s, "preset", -1);
      int rp = StringFind(body, "\"reason\":", s);
      if(rp >= 0 && rp < blockEnd)
      {
         int q1 = StringFind(body, "\"", rp + 9);
         int q2 = StringFind(body, "\"", q1 + 1);
         if(q1 >= 0 && q2 > q1)
            m_reason = StringSubstr(body, q1 + 1, q2 - q1 - 1);
      }
      else if(m_promoted)
         m_reason = "oos_promoted";
      m_loaded = true;
      return true;
   }

   bool Loaded() const { return m_loaded; }
   bool Promoted() const { return m_promoted; }
   int Preset() const { return m_preset; }
   string Reason() const { return m_reason; }
};

#endif
