//+------------------------------------------------------------------+
//|                          SovereignEmaTrendEA.mq5                   |
//|  EMA 21 trend system — trade only with EMA slope, never against.    |
//|  research/ema21_system.py: no config passed val+OOS on EURUSD yet.  |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <Sovereign\SovereignEmaTrend.mqh>
#include <Sovereign\SovereignPerformance.mqh>
#include <Sovereign\SovereignTrafficLight.mqh>
#include <Sovereign\SovereignAuditLog.mqh>

#define EMA_TREND_MAGIC 20261105

CTrade                 g_trade;
CSovereignEmaTrend     g_engine;
CSovereignPerformance  g_perf;
CSovereignTrafficLight g_tl;
CSovereignAuditLog     g_audit;

input group "=== EMA trend (match chart EMA period) ==="
input int    InpEmaPeriod          = 21;
input int    InpEmaSlopeBars       = 2;
input int    InpEntryMode          = 0;   // EMA21 preset: pullback only
input double InpPullbackAtr        = 0.30;

input group "=== Quality filters ==="
input double InpMinAdx             = 22.0;
input bool   InpUseHtfFilter       = true;
input ENUM_TIMEFRAMES InpHtfTrend  = PERIOD_H4;
input double InpRsiMinLong         = 40.0;
input double InpRsiMaxLong         = 72.0;
input double InpRsiMinShort        = 28.0;
input double InpRsiMaxShort        = 60.0;
input bool   InpCloseOnEmaFlip     = true;

input group "=== Risk ==="
input double InpRiskPct            = 0.35;
input double InpSlAtr              = 1.25;
input double InpTpAtr              = 2.50;
input int    InpMaxSpread          = 20;
input int    InpCooldownBars       = 0;
input int    InpMaxPositions       = 1;
input double InpMaxDailyDD         = 3.0;

input group "=== Traffic light & governor ==="
input bool   InpUseTrafficLight    = true;
input bool   InpEnablePerformanceGovernor = true;
input double InpGovernorDDStart    = 2.0;
input double InpGovernorDDLimit    = 8.0;

double   g_dayStart;
datetime g_dayKey;

int OnInit()
{
   g_trade.SetExpertMagicNumber(EMA_TREND_MAGIC);
   g_dayStart = AccountInfoDouble(ACCOUNT_EQUITY);
   if(!g_engine.Init(EMA_TREND_MAGIC, InpMaxSpread, InpRiskPct, InpEmaPeriod,
                     InpEmaSlopeBars, InpEntryMode, InpPullbackAtr, InpMinAdx,
                     InpUseHtfFilter, InpHtfTrend, InpSlAtr, InpTpAtr,
                     InpCooldownBars, InpMaxPositions, InpCloseOnEmaFlip))
      return INIT_FAILED;
   g_perf.Init(InpEnablePerformanceGovernor, _Symbol, EMA_TREND_MAGIC,
               InpGovernorDDStart, InpGovernorDDLimit, 3);
   g_tl.Init(InpUseTrafficLight, _Symbol, true);
   g_audit.Init(true);
   PrintFormat("SOV-EMA-TREND on %s %s | EMA=%d (trade with slope, never against)",
               _Symbol, EnumToString(_Period), InpEmaPeriod);
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
   double loss = (g_dayStart > 0.0 && eq < g_dayStart)
                 ? (g_dayStart - eq) / g_dayStart * 100.0 : 0.0;
   bool allow = (loss < InpMaxDailyDD);
   g_engine.OnTick(_Symbol, _Period, allow, eq, g_perf, g_tl, g_trade);
}

void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &req,
                        const MqlTradeResult &res)
{
   g_audit.LogDeal(trans, res.retcode);
   g_perf.OnTradeTransaction(trans);
}
