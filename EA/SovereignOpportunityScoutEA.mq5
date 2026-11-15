//+------------------------------------------------------------------+
//|                         SovereignOpportunityScoutEA.mq5          |
//| EXPERIMENTAL — short-horizon scout. Attach to ANY chart TF.      |
//| Project research: M5/H1 price scalps failed OOS historically.   |
//| Use tiny risk, tester first, do NOT expect "magic" win streaks.  |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <Sovereign\SovereignPerformance.mqh>
#include <Sovereign\SovereignTrafficLight.mqh>
#include <Sovereign\SovereignAuditLog.mqh>
#include <Sovereign\SovereignOpportunityScout.mqh>

#define SCOUT_MAGIC 20261104

CTrade g_trade;
CSovereignOpportunityScout g_scout;
CSovereignPerformance g_perf;
CSovereignTrafficLight g_tl;
CSovereignAuditLog g_audit;

input group "=== EXPERIMENTAL Scout ==="
input double InpRiskPct       = 0.25;
input int    InpMaxHoldBars = 3;
input double InpTpAtr        = 0.45;
input double InpSlAtr        = 0.55;
input int    InpMaxSpread    = 20;
input double InpMaxDailyDD   = 3.0;

input group "=== Governor ==="
input bool   InpEnablePerformanceGovernor = true;
input double InpGovernorDDStart = 2.0;
input double InpGovernorDDLimit = 8.0;

double g_dayStart;
datetime g_dayKey;

int OnInit()
{
   g_trade.SetExpertMagicNumber(SCOUT_MAGIC);
   g_dayStart = AccountInfoDouble(ACCOUNT_EQUITY);
   if(!g_scout.Init(SCOUT_MAGIC, InpMaxSpread, InpRiskPct, InpMaxHoldBars,
                    InpTpAtr, InpSlAtr))
      return INIT_FAILED;
   g_perf.Init(InpEnablePerformanceGovernor, _Symbol, SCOUT_MAGIC,
               InpGovernorDDStart, InpGovernorDDLimit, 3);
   g_tl.Init(true, _Symbol, false);
   g_audit.Init(true);
   Print("SOV-SCOUT: experimental micro-opportunity EA on ", EnumToString(_Period));
   return INIT_SUCCEEDED;
}

void OnTick()
{
   g_perf.OnTick();
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   dt.hour = 0; dt.min = 0; dt.sec = 0;
   datetime dk = StructToTime(dt);
   if(dk != g_dayKey)
   {
      g_dayKey = dk;
      g_dayStart = AccountInfoDouble(ACCOUNT_EQUITY);
   }
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   double loss = (g_dayStart > 0 && eq < g_dayStart)
                 ? (g_dayStart - eq) / g_dayStart * 100.0 : 0.0;
   bool allow = (loss < InpMaxDailyDD);
   g_scout.OnTick(_Symbol, _Period, allow, eq, g_perf, g_tl, g_trade);
}

void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &req,
                        const MqlTradeResult &res)
{
   g_audit.LogDeal(trans, res.retcode);
   g_perf.OnTradeTransaction(trans);
}
