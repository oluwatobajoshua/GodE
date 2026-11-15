//+------------------------------------------------------------------+
//|                        SovereignLondonOrbEA.mq5                   |
//|  EURUSD M5 London ORB — research only; see sovereign_trading_mode |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <Sovereign\SovereignLondonOrb.mqh>
#include <Sovereign\SovereignPerformance.mqh>
#include <Sovereign\SovereignAuditLog.mqh>
#include <Sovereign\SovereignChartVisualizer.mqh>

#define LONDON_ORB_MAGIC 20261109

CTrade                 g_trade;
CSovereignLondonOrb    g_engine;
CSovereignPerformance  g_perf;
CSovereignAuditLog     g_audit;
CSovereignChartVisualizer g_paint;

input group "=== London box (UTC hours) ==="
input int    InpBoxHour0           = 5;
input int    InpBoxHour1           = 6;
input int    InpTradeHourStart     = 7;
input int    InpTradeHourEnd       = 11;
input int    InpBrokerUtcOffset    = 0;

input group "=== ORB risk (match scalp_lane.json) ==="
input double InpBufAtr             = 0.15;
input double InpTpRMultiple        = 1.2;
input int    InpMaxHoldBars        = 24;
input int    InpMaxTradesPerDay    = 2;

input group "=== Risk ==="
input double InpRiskPct            = 0.25;
input int    InpMaxSpread          = 12;
input double InpMinBoxSpreadMult   = 4.0;
input int    InpMaxPositions       = 1;
input double InpMaxDailyDD         = 4.0;

input group "=== Governor ==="
input bool   InpEnablePerformanceGovernor = true;
input double InpGovernorDDStart    = 2.0;
input double InpGovernorDDLimit    = 8.0;

input group "=== Chart visuals (visual mode / tester) ==="
input bool   InpShowVisuals        = true;   // paint engine state on the chart
input bool   InpVisualSessionBox   = true;   // Asian pre-open box
input bool   InpVisualLevels       = true;   // box high / low / mid
input bool   InpVisualArrows       = true;   // entry arrow + SL/TP of last setup
input bool   InpVisualFractals     = false;  // 5-bar swing markers (context only)
input int    InpVisualFractalBars  = 120;
input int    InpVisualMaxArrows    = 400;    // ring buffer, keeps long runs light
input color  InpVisualBoxColor     = clrIndigo;
input color  InpVisualLevelColor   = clrMediumOrchid;
input color  InpVisualSlColor      = clrCrimson;
input color  InpVisualTpColor      = clrLimeGreen;

double   g_dayStart;
datetime g_dayKey;

int OnInit()
{
   g_trade.SetExpertMagicNumber(LONDON_ORB_MAGIC);
   g_dayStart = AccountInfoDouble(ACCOUNT_EQUITY);
   if(!g_engine.Init(LONDON_ORB_MAGIC, InpMaxSpread, InpRiskPct,
                    InpBoxHour0, InpBoxHour1, InpTradeHourStart, InpTradeHourEnd,
                    InpBrokerUtcOffset, InpBufAtr, InpTpRMultiple,
                    InpMaxHoldBars, InpMaxTradesPerDay, InpMaxPositions,
                    InpMinBoxSpreadMult))
      return INIT_FAILED;
   g_perf.Init(InpEnablePerformanceGovernor, _Symbol, LONDON_ORB_MAGIC,
               InpGovernorDDStart, InpGovernorDDLimit, 3);
   g_audit.Init(true);
   g_paint.Init(InpShowVisuals, "SOV_ORB_", InpVisualMaxArrows);
   if(_Period != PERIOD_M5)
      Print("[WARN] SovereignLondonOrbEA expects M5 chart (attach to M5)");
   PrintFormat("SOV-LONDON-ORB %s | box UTC %d-%d trade %d-%d UTC offset %d",
               _Symbol, InpBoxHour0, InpBoxHour1, InpTradeHourStart, InpTradeHourEnd, InpBrokerUtcOffset);
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
   g_engine.OnTick(_Symbol, _Period, allow, eq, g_perf, g_trade);
   PaintChart(allow, loss);
}

void OnDeinit(const int reason)
{
   g_paint.ClearAll();
   g_perf.Deinit();
}

//+------------------------------------------------------------------+
//| Paint what the engine actually used this session.                |
//| Every level below is read from CSovereignLondonOrb, so the chart |
//| cannot drift away from the logic that placed the order.          |
//+------------------------------------------------------------------+
void PaintChart(const bool allow, const double dayLossPct)
{
   if(!InpShowVisuals)
      return;

   string sym = _Symbol;
   ENUM_TIMEFRAMES tf = _Period;
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   if(digits <= 0)
      digits = 5;

   //--- Asian pre-open box, rebuilt from real bars
   int h0 = CSovereignChartVisualizer::NormHour(InpBoxHour0 + InpBrokerUtcOffset);
   int h1 = CSovereignChartVisualizer::NormHour(InpBoxHour1 + InpBrokerUtcOffset);
   if(InpVisualSessionBox)
      g_paint.DrawSessionBoxFromBars("AsiaBox", sym, tf, 0, h0, h1, InpVisualBoxColor);

   //--- the box the engine measured
   double hi = g_engine.BoxHigh();
   double lo = g_engine.BoxLow();
   if(InpVisualLevels && g_engine.BoxReady() && hi > lo)
   {
      double mid = (hi + lo) * 0.5;
      g_paint.DrawLevel("BoxHigh", hi, InpVisualLevelColor, STYLE_SOLID, 1, "London box high");
      g_paint.DrawLevel("BoxLow",  lo, InpVisualLevelColor, STYLE_SOLID, 1, "London box low");
      g_paint.DrawLevel("BoxMid",  mid, InpVisualLevelColor, STYLE_DOT, 1, "London box mid");
      datetime tl = iTime(sym, tf, 1);
      if(tl > 0)
      {
         g_paint.DrawText("BoxHiTxt", tl, hi, "BOX HI " + DoubleToString(hi, digits),
                          InpVisualLevelColor, ANCHOR_LEFT, 8);
         g_paint.DrawText("BoxLoTxt", tl, lo, "BOX LO " + DoubleToString(lo, digits),
                          InpVisualLevelColor, ANCHOR_LEFT, 8);
      }
   }
   else if(!g_engine.BoxReady())
   {
      g_paint.DrawLevel("BoxHigh", 0.0, InpVisualLevelColor, STYLE_SOLID, 1);
      g_paint.DrawLevel("BoxLow",  0.0, InpVisualLevelColor, STYLE_SOLID, 1);
      g_paint.DrawLevel("BoxMid",  0.0, InpVisualLevelColor, STYLE_SOLID, 1);
   }

   //--- last setup the engine produced (entry, stop, target)
   if(InpVisualArrows && g_engine.HaveSignal())
   {
      datetime ts = g_engine.LastSignalTime();
      int    side = g_engine.LastSide();
      double ent  = g_engine.LastEntry();
      double sl   = g_engine.LastSl();
      double tp   = g_engine.LastTp();
      string tag  = IntegerToString((long)ts);
      if(ts > 0 && ent > 0.0)
      {
         if(side > 0)
            g_paint.DrawArrow("Entry_" + tag, ts, ent, SOV_ARROW_UP, clrLime,
                              "ORB long entry");
         else if(side < 0)
            g_paint.DrawArrow("Entry_" + tag, ts, ent, SOV_ARROW_DOWN, clrTomato,
                              "ORB short entry");
         g_paint.DrawText("EntryTxt_" + tag, ts, ent,
                          StringFormat("%s @ %s", (side > 0 ? "LONG" : "SHORT"),
                                       DoubleToString(ent, digits)),
                          (side > 0 ? clrLime : clrTomato),
                          (side > 0 ? ANCHOR_LEFT : ANCHOR_LEFT), 8);
      }
      if(sl > 0.0)
         g_paint.DrawLevel("LastSL", sl, InpVisualSlColor, STYLE_DASH, 1,
                           "Engine stop loss");
      if(tp > 0.0)
         g_paint.DrawLevel("LastTP", tp, InpVisualTpColor, STYLE_DASH, 1,
                           "Engine take profit");
   }

   //--- 5-bar swings: structure context only, never an entry trigger
   if(InpVisualFractals)
      g_paint.DrawFractals(sym, tf, InpVisualFractalBars, clrTomato, clrDodgerBlue);

   //--- panel
   string lines[10];
   color  cols[10];
   int n = 0;

   lines[n] = StringFormat("SOV LONDON ORB   %s %s", sym,
                           OrbTfText(tf));
   cols[n]  = clrGold; n++;

   lines[n] = StringFormat("box %02d-%02d UTC   trade %02d-%02d UTC   offset %+d",
                           h0, h1,
                           CSovereignChartVisualizer::NormHour(InpTradeHourStart + InpBrokerUtcOffset),
                           CSovereignChartVisualizer::NormHour(InpTradeHourEnd + InpBrokerUtcOffset),
                           InpBrokerUtcOffset);
   cols[n]  = clrSilver; n++;

   if(g_engine.BoxReady() && hi > lo)
   {
      lines[n] = StringFormat("BOX READY  hi %s  lo %s  width %d pts",
                              DoubleToString(hi, digits), DoubleToString(lo, digits),
                              (int)MathRound((hi - lo) / _Point));
      cols[n]  = clrLime;
   }
   else
   {
      lines[n] = "BOX PENDING  waiting for the session bars";
      cols[n]  = clrOrange;
   }
   n++;

   if(g_engine.HaveSignal())
   {
      lines[n] = StringFormat("LAST %s  entry %s  sl %s  tp %s",
                              (g_engine.LastSide() > 0 ? "LONG" : "SHORT"),
                              DoubleToString(g_engine.LastEntry(), digits),
                              DoubleToString(g_engine.LastSl(), digits),
                              DoubleToString(g_engine.LastTp(), digits));
      cols[n]  = (g_engine.LastSide() > 0 ? clrLime : clrTomato);
   }
   else
   {
      lines[n] = "LAST  no setup yet this box";
      cols[n]  = clrGray;
   }
   n++;

   lines[n] = StringFormat("risk %.2f%%   max %d/day   hold %d bars   tp %.2fR",
                           InpRiskPct, InpMaxTradesPerDay, InpMaxHoldBars,
                           InpTpRMultiple);
   cols[n]  = clrSilver; n++;

   lines[n] = StringFormat("day P&L guard  %.2f%% / %.2f%%   %s",
                           dayLossPct, InpMaxDailyDD,
                           (allow ? "TRADING ALLOWED" : "HALTED"));
   cols[n]  = (allow ? clrLime : clrRed); n++;

   lines[n] = g_perf.StatusLine();
   cols[n]  = clrSilver; n++;

   g_paint.DrawPanel("ORB_PANEL", lines, cols, 12, 18, 14, 8,
                     (color)C'18,18,22', clrDimGray);
   g_paint.Flush();
}

//+------------------------------------------------------------------+
string OrbTfText(const ENUM_TIMEFRAMES tf)
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

void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &req,
                        const MqlTradeResult &res)
{
   g_audit.LogDeal(trans, res.retcode);
   g_perf.OnTradeTransaction(trans);
}
