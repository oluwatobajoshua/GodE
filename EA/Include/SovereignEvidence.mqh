//+------------------------------------------------------------------+
//|                                      SovereignEvidence.mqh       |
//| Reads MQL5/Files/Sovereign/evidence_directives.json from research |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_EVIDENCE_MQH__
#define __SOVEREIGN_EVIDENCE_MQH__

#include <Sovereign\SovereignError.mqh>

class CSovereignEvidence
{
private:
   string m_active;
   bool   m_useStackV1;

public:
   CSovereignEvidence()
   {
      m_active = "";
      m_useStackV1 = false;
   }

   void Reload()
   {
      m_active = "";
      m_useStackV1 = false;
      SovResetLastError();
      int h = FileOpen("Sovereign\\evidence_directives.json", FILE_READ | FILE_TXT | FILE_COMMON);
      if(h == INVALID_HANDLE)
      {
         SovLogWarn("Evidence: cannot open evidence_directives.json err=" + IntegerToString(GetLastError()));
         return;
      }
      string body = "";
      while(!FileIsEnding(h))
         body += FileReadString(h);
      SovResetLastError();
      FileClose(h);
      if(GetLastError() != 0)
         SovLogWarn("Evidence: FileClose failed err=" + IntegerToString(GetLastError()));
      if(StringFind(body, "carry_mom_filter") >= 0 || StringFind(body, "sov_stack_v1") >= 0)
      {
         if(StringFind(body, "\"active_strategy\": \"carry_mom_filter\"") >= 0)
            m_active = "carry_mom_filter";
         else if(StringFind(body, "\"active_strategy\": \"sov_stack_v1\"") >= 0)
            m_active = "sov_stack_v1";
         else if(StringFind(body, "carry_mom_filter") >= 0)
            m_active = "carry_mom_filter";
      }
      m_useStackV1 = (StringFind(body, "\"use_sovereign_stack_v1\": true") >= 0);
   }

   string ActiveStrategy() const { return m_active; }
   bool PreferStackV1() const { return m_useStackV1; }
};

#endif
