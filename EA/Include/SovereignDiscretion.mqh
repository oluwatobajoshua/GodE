//+------------------------------------------------------------------+
//|                                         SovereignDiscretion.mqh   |
//|  Human discretion / veto layer for the Sovereign EA:             |
//|  - File-based overrides: MQL5/Files/Sovereign/discretion.csv     |
//|    SYMBOL,ACTION,REASON,EXPIRY                                   |
//|    ACTION ∈ {VETO, APPROVE, FLATTEN, SIZE_CAP}                   |
//|  - Emergency flat-all: close all Sovereign positions instantly   |
//|  - Size cap override: force max lot per symbol                   |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_DISCRETION_MQH__
#define __SOVEREIGN_DISCRETION_MQH__

#include <Trade\Trade.mqh>
#include <Sovereign\SovereignError.mqh>

#define SOV_DISC_MAX_RULES 32

enum SovDiscAction
{
   SOV_DISC_VETO,      // block new entries for symbol
   SOV_DISC_APPROVE,   // force-allow entries for symbol
   SOV_DISC_FLATTEN,   // close all positions for symbol
   SOV_DISC_SIZE_CAP   // max lot size for symbol
};

struct SovDiscRule
{
   string   symbol;      // "" = all symbols
   int      action;
   string   reason;
   datetime expiry;      // 0 = permanent
   double   sizeCap;     // max lots (for SIZE_CAP)
   bool     active;
};

class CSovereignDiscretion
{
private:
   string   m_folder;
   bool     m_enabled;
   SovDiscRule m_rules[SOV_DISC_MAX_RULES];
   int      m_nRules;
   datetime m_lastReload;
   bool     m_flatAllPending;

   //--- helpers
   int FindRule(const string symbol, const int action) const;
   int EnsureRule(const string symbol, const int action, const string reason,
                  const datetime expiry, const double sizeCap);
   void LoadRules();
   void SaveRules();

public:
   CSovereignDiscretion();
   ~CSovereignDiscretion();
   bool Init(const string folder);
   void Deinit();
   void OnTick();

   //--- entry veto check: returns false if entry blocked, sets blockReason
   bool EntryAllowed(const string symbol, string &blockReason) const;

   //--- size cap: returns max allowed lots (0 = no cap / unlimited)
   double SizeCap(const string symbol) const;

   //--- flatten check: returns true if flat-all requested
   bool FlatAllRequested();

   //--- trigger emergency flat-all (called from EA OnTick)
   void RequestFlatAll() { m_flatAllPending = true; }

   //--- status
   string StatusLine() const;
};

//+------------------------------------------------------------------+
//| Constructor                                                      |
//+------------------------------------------------------------------+
CSovereignDiscretion::CSovereignDiscretion()
{
   m_folder = "Sovereign";
   m_enabled = false;
   m_nRules = 0;
   m_lastReload = 0;
   m_flatAllPending = false;
}
//+------------------------------------------------------------------+
//| Destructor                                                       |
//+------------------------------------------------------------------+
CSovereignDiscretion::~CSovereignDiscretion()
{
   Deinit();
}
//+------------------------------------------------------------------+
//| Init                                                             |
//+------------------------------------------------------------------+
bool CSovereignDiscretion::Init(const string folder)
{
   m_folder = folder;
   m_enabled = true;
   m_nRules = 0;
   m_lastReload = 0;
   m_flatAllPending = false;
   SovResetLastError();
   if(!FolderCreate(m_folder))
   {
      SovLogWarn("Discretion: FolderCreate(" + m_folder + ") failed err=" + IntegerToString(GetLastError()));
      return false;
   }
   LoadRules();
   return true;
}
//+------------------------------------------------------------------+
//| Deinit                                                           |
//+------------------------------------------------------------------+
void CSovereignDiscretion::Deinit()
{
   m_enabled = false;
   m_nRules = 0;
}
//+------------------------------------------------------------------+
//| OnTick — reload rules periodically, check flat-all               |
//+------------------------------------------------------------------+
void CSovereignDiscretion::OnTick()
{
   if(!m_enabled) return;
   //--- reload rules every 60 seconds
   if(TimeCurrent() - m_lastReload >= 60)
   {
      m_lastReload = TimeCurrent();
      LoadRules();
   }
}
//+------------------------------------------------------------------+
//| EntryAllowed                                                     |
//+------------------------------------------------------------------+
bool CSovereignDiscretion::EntryAllowed(const string symbol, string &blockReason) const
{
   if(!m_enabled) return true;
   blockReason = "";
   for(int i = 0; i < m_nRules; i++)
   {
      if(!m_rules[i].active) continue;
      if(m_rules[i].expiry > 0 && TimeCurrent() > m_rules[i].expiry) continue;
      bool symMatch = (m_rules[i].symbol == "" || m_rules[i].symbol == symbol);
      if(!symMatch) continue;
      if(m_rules[i].action == SOV_DISC_VETO)
      {
         blockReason = "discretion VETO (" + m_rules[i].reason + ")";
         return false;
      }
      if(m_rules[i].action == SOV_DISC_APPROVE)
      {
         //--- approve overrides any veto for this symbol
         return true;
      }
   }
   return true;
}
//+------------------------------------------------------------------+
//| SizeCap                                                          |
//+------------------------------------------------------------------+
double CSovereignDiscretion::SizeCap(const string symbol) const
{
   if(!m_enabled) return 0.0;
   double minCap = 0.0;
   for(int i = 0; i < m_nRules; i++)
   {
      if(!m_rules[i].active) continue;
      if(m_rules[i].expiry > 0 && TimeCurrent() > m_rules[i].expiry) continue;
      bool symMatch = (m_rules[i].symbol == "" || m_rules[i].symbol == symbol);
      if(!symMatch) continue;
      if(m_rules[i].action == SOV_DISC_SIZE_CAP)
      {
         if(minCap == 0.0 || m_rules[i].sizeCap < minCap)
            minCap = m_rules[i].sizeCap;
      }
   }
   return minCap;
}
//+------------------------------------------------------------------+
//| FlatAllRequested                                                 |
//+------------------------------------------------------------------+
bool CSovereignDiscretion::FlatAllRequested()
{
   if(!m_enabled) return false;
   //--- also check file for FLATTEN action
   for(int i = 0; i < m_nRules; i++)
   {
      if(!m_rules[i].active) continue;
      if(m_rules[i].expiry > 0 && TimeCurrent() > m_rules[i].expiry) continue;
      if(m_rules[i].action == SOV_DISC_FLATTEN)
         return true;
   }
   return m_flatAllPending;
}
//+------------------------------------------------------------------+
//| StatusLine                                                       |
//+------------------------------------------------------------------+
string CSovereignDiscretion::StatusLine() const
{
   if(!m_enabled) return "discretion=off";
   int active = 0;
   for(int i = 0; i < m_nRules; i++)
      if(m_rules[i].active) active++;
   return StringFormat("discretion rules=%d active=%d flatAll=%d",
                       m_nRules, active, (int)m_flatAllPending);
}

//+------------------------------------------------------------------+
//| Private helpers                                                  |
//+------------------------------------------------------------------+
int CSovereignDiscretion::FindRule(const string symbol, const int action) const
{
   for(int i = 0; i < m_nRules; i++)
      if(m_rules[i].symbol == symbol && m_rules[i].action == action) return i;
   return -1;
}
int CSovereignDiscretion::EnsureRule(const string symbol, const int action,
                                      const string reason, const datetime expiry,
                                      const double sizeCap)
{
   int idx = FindRule(symbol, action);
   if(idx >= 0)
   {
      m_rules[idx].reason = reason;
      m_rules[idx].expiry = expiry;
      m_rules[idx].sizeCap = sizeCap;
      m_rules[idx].active = true;
      return idx;
   }
   if(m_nRules >= SOV_DISC_MAX_RULES) return -1;
   idx = m_nRules++;
   m_rules[idx].symbol = symbol;
   m_rules[idx].action = action;
   m_rules[idx].reason = reason;
   m_rules[idx].expiry = expiry;
   m_rules[idx].sizeCap = sizeCap;
   m_rules[idx].active = true;
   return idx;
}
void CSovereignDiscretion::LoadRules()
{
   string path = m_folder + "\\discretion.csv";
   if(!FileIsExist(path)) return;
   SovResetLastError();
   int h = FileOpen(path, FILE_READ | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE) return;
   FileReadString(h); // header
   m_nRules = 0;
   while(!FileIsEnding(h))
   {
      string line = FileReadString(h);
      if(StringLen(line) == 0) continue;
      string p[];
      if(StringSplit(line, ',', p) < 4) continue;
      string sym = p[0];
      int action = (int)StringToInteger(p[1]);
      string reason = p[2];
      datetime expiry = (datetime)StringToInteger(p[3]);
      double sizeCap = (ArraySize(p) > 4) ? StringToDouble(p[4]) : 0.0;
      EnsureRule(sym, action, reason, expiry, sizeCap);
   }
   SovResetLastError();
   FileClose(h);
}
void CSovereignDiscretion::SaveRules()
{
   string path = m_folder + "\\discretion.csv";
   SovResetLastError();
   int h = FileOpen(path, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE) return;
   FileWriteString(h, "symbol,action,reason,expiry,sizeCap\r\n");
   for(int i = 0; i < m_nRules; i++)
   {
      string line = StringFormat("%s,%d,%s,%I64d,%.2f\r\n",
                    m_rules[i].symbol, m_rules[i].action, m_rules[i].reason,
                    (long)m_rules[i].expiry, m_rules[i].sizeCap);
      FileWriteString(h, line);
   }
   SovResetLastError();
   FileClose(h);
}

#endif // __SOVEREIGN_DISCRETION_MQH__
