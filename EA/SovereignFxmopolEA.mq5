//+------------------------------------------------------------------+
//|                          SovereignFxmopolEA.mq5                  |
//| FXMOPOL — criminal candle + BabyPips S/R fib pivot EMA MTF       |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <Sovereign\SovereignFxmopol.mqh>
#include <Sovereign\SovereignPerformance.mqh>
#include <Sovereign\SovereignTrafficLight.mqh>
#include <Sovereign\SovereignAuditLog.mqh>
#include <Sovereign\SovereignFxmopolProfiles.mqh>
#include <Sovereign\SovereignMarketSnapshot.mqh>
#include <Sovereign\SovereignChartVisualizer.mqh>
#include <Sovereign\SovereignTemplate.mqh>
#include <Sovereign\SovereignMarketView.mqh>
#include <Sovereign\SovereignPatterns.mqh>
#include <Sovereign\SovereignChartSnapshot.mqh>

#define FXMOPOL_MAGIC 20261107

enum ENUM_FXMOPOL_PRESET
{
   PRESET_EUR_CHART_ONLY = 0,   // fxmopol.tpl — indicators only (EUR auto OOS negative)
   PRESET_USDJPY_STREAK = 1,    // only OOS+ intraday backtest
   PRESET_GBP_PLAYBOOK = 2      // only OOS+ playbook + proximity (research)
};

CTrade g_trade;
CSovereignFxmopol g_fxmopol;
CSovereignPerformance g_perf;
CSovereignTrafficLight g_tl;
CSovereignAuditLog g_audit;
CSovereignMarketSnapshot g_eco;
CSovereignChartVisualizer g_paint;
CSovereignTemplate     g_tpl;
CSovereignChartSnapshot g_snap;

input group "=== Best-win preset (backtest only) ==="
input bool   InpEnforceSymbolProfile = true;  // trade only symbols promoted in fxmopol_symbol_profiles.json
input ENUM_FXMOPOL_PRESET InpPreset = PRESET_EUR_CHART_ONLY;
input group "=== FXMOPOL chart (templates/fxmopol.tpl) ==="
input bool   InpVisualMobileSetup = true;
input int    InpEmaPeriod     = 21;
input int    InpSlowEma       = 50;
input int    InpSmmaPeriod    = 100;
input int    InpRsiPeriod     = 9;
input bool   InpUseFractals   = false;
input bool   InpUsePlaybook   = false;
input bool   InpStreakRide    = false;
input bool   InpRegimeEmaOnly = false;
input int    InpMinStreak     = 30;
input double InpManualLevel   = 0.0;       // e.g. 158.500 horizontal; 0 = ignore
input int    InpMaxPerDay     = 2;
input group "=== EMA distance filters (enter near EMA21) ==="
input bool   InpUseProxFilters = true;
input bool   InpStrictStayAway = true;
input bool   InpUseRegimeFilter = true;
input int    InpMaxLossesPerDay = 1;
input double InpMaxEntryEma21Atr = 0.40;
input double InpMinFanSpreadAtr  = 0.12;
input double InpExitExtEma21Atr  = 1.15;
input int    InpMinBarsExtExit   = 4;
input double InpMinProfitAtrExt  = 0.35;
input double InpSlAtr         = 1.0;
input double InpTpAtr         = 2.0;
input double InpRiskPct       = 0.35;
input int    InpMaxSpread     = 20;
input double InpMaxDailyDD    = 3.0;
input bool   InpGoodSessionsOnly = false;   // promote from fxmopol_learned_filters.json
input int    InpWeekdayMask = 0;           // 0 = all weekdays until research promotes a mask
input bool   InpMarketPulse = true;        // chart comment: regime + best move each new bar
input bool   InpEcosystemProfessor = true; // market_snapshot.json TA+fund+sentiment gates
input int    InpEcoReloadSec = 120;        // re-read professor snapshot
input bool   InpPlaybookSignalExitOnly = true; // playbook: wide SL/TP; exit on EMA21/RSI (matches research)
input bool   InpRequireD1Align = true;       // playbook H1: only trade with D1 stack + SMA200 side
input group "=== Price action (FXMOPOL_CHART_SNAPSHOT) ==="
input bool   InpUseFullPriceAction = true;   // wick reclaim+SMMA, stack regime, pullback/retest
input bool   InpUseLevelTp = true;           // exit at InpManualLevel when set
input int    InpStackRegimeBars = 5;         // bars full stack before playbook entry
input double InpRsiCoverLevel = 30.0;        // short cover zone (chart oversold exit)
input group "=== Legacy (disabled — not in best_win_settings) ==="
input int    InpH4EmaPeriod   = 200;
input int    InpSwingBars     = 20;
input bool   InpBreakoutMode  = false;
input bool   InpPinOnly       = false;

input group "=== Chart visuals (aligned to templates/fxmopol.tpl) ==="
input bool   InpShowVisuals        = true;   // paint the bot's read on the chart
input bool   InpVisualPanel        = true;   // state panel, top-left
input bool   InpVisualLevels       = true;   // zone / target / invalidation / pivot S1 R1
input bool   InpVisualEmaLines     = false;  // dotted EMA21/50/SMMA100 (template already draws them)
input bool   InpVisualPatterns     = true;   // zigzag + harmonic  (context only)
input int    InpVisualZigZagBars   = 160;
input double InpVisualZigZagAtr    = 0.80;
input bool   InpVisualPositions    = true;   // arrows + SL/TP of live positions
input bool   InpVisualSessionBox   = false;  // Asian box on H1 (off by default)
input int    InpVisualMaxArrows    = 400;    // ring buffer, keeps long runs light
input color  InpVisualFastColor    = clrDodgerBlue;
input color  InpVisualSlowColor    = clrOrange;
input color  InpVisualSmmaColor    = clrGray;
input color  InpVisualZoneColor    = clrTeal;
input color  InpVisualSlColor      = clrCrimson;
input color  InpVisualTpColor      = clrLimeGreen;
input color  InpVisualPatternColor = clrSlateBlue;
input bool   InpWriteChartSnapshot = true;   // proof file: what the EA saw this bar

double g_dayStart;
datetime g_dayKey;

void ApplyPresetInputs(const int preset,
                       bool &visual, bool &streak, bool &playbook, bool &regimeEma,
                       int &slowEma, int &minStreak, string &presetName)
{
   if(preset == PRESET_USDJPY_STREAK)
   {
      presetName = "USDJPY_STREAK";
      streak = true;
      playbook = false;
      regimeEma = true;
      slowEma = 100;
      minStreak = 30;
      return;
   }
   if(preset == PRESET_GBP_PLAYBOOK)
   {
      presetName = "GBP_PLAYBOOK_PROXIMITY";
      streak = false;
      playbook = true;
      regimeEma = false;
      slowEma = 50;
      minStreak = 5;
      return;
   }
   presetName = "EUR_CHART_ONLY";
   streak = false;
   playbook = false;
   regimeEma = false;
   slowEma = 50;
   minStreak = 5;
}

int OnInit()
{
   bool visual = InpVisualMobileSetup;
   bool streak = InpStreakRide;
   bool playbook = InpUsePlaybook;
   bool regimeEma = InpRegimeEmaOnly;
   int slowEma = InpSlowEma;
   int minStreak = InpMinStreak;
   string presetName = "EUR_CHART_ONLY";
   int maxPerDay = InpMaxPerDay;
   int presetUse = (int)InpPreset;

   ApplyPresetInputs(presetUse, visual, streak, playbook, regimeEma, slowEma, minStreak, presetName);

   CSovereignFxmopolProfiles symProf;
   if(InpEnforceSymbolProfile)
   {
      if(symProf.Load(_Symbol) && symProf.Loaded())
      {
         if(!symProf.Promoted())
         {
            maxPerDay = 0;
            streak = false;
            playbook = false;
            Print("FXMOPOL: ", _Symbol, " not OOS-promoted (", symProf.Reason(),
                  ") — chart only, no auto entries.");
         }
         else if(symProf.Preset() >= 0)
         {
            presetUse = symProf.Preset();
            ApplyPresetInputs(presetUse, visual, streak, playbook, regimeEma, slowEma, minStreak, presetName);
            Print("FXMOPOL: ", _Symbol, " profile preset=", presetUse, " reason=", symProf.Reason());
         }
      }
      else
         Print("FXMOPOL WARN: no symbol profile file; attach ", _Symbol,
               " only if you accept manual risk.");
   }

   g_trade.SetExpertMagicNumber(FXMOPOL_MAGIC);
   g_fxmopol.Init(FXMOPOL_MAGIC, InpEmaPeriod, InpH4EmaPeriod, InpSwingBars, maxPerDay,
                  InpSlAtr, InpTpAtr, InpMaxSpread, InpRiskPct,
                  InpBreakoutMode, InpGoodSessionsOnly, InpPinOnly,
                  visual, slowEma, InpSmmaPeriod, InpRsiPeriod,
                  false, InpManualLevel, streak, playbook,
                  regimeEma, minStreak,
                  InpUseProxFilters, InpStrictStayAway, InpUseRegimeFilter, InpMaxLossesPerDay,
                  InpMaxEntryEma21Atr, InpMinFanSpreadAtr,
                  InpExitExtEma21Atr, InpMinBarsExtExit, InpMinProfitAtrExt,
                  InpMarketPulse, InpPlaybookSignalExitOnly, InpRequireD1Align,
                  InpUseFullPriceAction, InpUseLevelTp, InpStackRegimeBars, InpRsiCoverLevel,
                  InpWeekdayMask);
   g_perf.Init(true, _Symbol, FXMOPOL_MAGIC, 2.0, 8.0, 3);
   g_tl.Init(true, _Symbol, true);
   g_audit.Init(true);
   g_eco.Init(InpEcosystemProfessor, InpEcoReloadSec);
   g_paint.Init(InpShowVisuals, "SOV_FXM_", InpVisualMaxArrows);
   g_snap.Init(_Symbol, _Period);
   g_tpl.Load();
   Print("FXMOPOL ", g_tpl.Status(InpEmaPeriod, slowEma, InpSmmaPeriod, InpRsiPeriod));
   Print("FXMOPOL ", g_tpl.ChartStatus(_Symbol, _Period));
   if(g_tpl.Loaded() && !g_tpl.Aligned(InpEmaPeriod, slowEma, InpSmmaPeriod, InpRsiPeriod))
      Print("FXMOPOL WARN: indicator inputs differ from the loaded template; ",
            "the panel shows MISMATCH until they agree.");
   if(InpPreset == PRESET_USDJPY_STREAK && _Symbol != "USDJPY")
      Print("WARN FXMOPOL: attach USDJPY H1 for USDJPY_STREAK preset.");
   if(InpPreset == PRESET_GBP_PLAYBOOK && _Symbol != "GBPUSD")
      Print("WARN FXMOPOL: attach GBPUSD H1 for GBP_PLAYBOOK preset.");
   Print("FXMOPOL preset=", presetName, " EMA", InpEmaPeriod, "/", slowEma, "/SMMA", InpSmmaPeriod,
         " streak=", streak, " playbook=", playbook,
         " good_sessions=", InpGoodSessionsOnly, " mask=", InpWeekdayMask);
   return INIT_SUCCEEDED;
}

void OnTick()
{
   g_perf.OnTick();
   g_eco.Reload();
   g_fxmopol.SetEcosystemGate(
      g_eco.CombinedMult(_Symbol),
      g_eco.BlockLong(_Symbol),
      g_eco.BlockShort(_Symbol),
      g_eco.ProfessorLine(_Symbol));
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   dt.hour = 0;
   dt.min = 0;
   dt.sec = 0;
   datetime dk = StructToTime(dt);
   if(dk != g_dayKey)
   {
      g_dayKey = dk;
      g_dayStart = AccountInfoDouble(ACCOUNT_EQUITY);
   }
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   double loss = (g_dayStart > 0 && eq < g_dayStart) ? (g_dayStart - eq) / g_dayStart * 100.0 : 0.0;
   g_fxmopol.OnTick(_Symbol, _Period, loss < InpMaxDailyDD, eq, g_perf, g_tl, g_trade);
   PaintChart(loss);
   WriteChartSnapshot();
}

//+------------------------------------------------------------------+
//| Proof file: one JSON per bar with every value the analysis used. |
//| scripts/compare_chart_snapshot.py diffs it against a Python       |
//| recomputation from the same broker bars.                          |
//+------------------------------------------------------------------+
void WriteChartSnapshot()
{
   if(!InpWriteChartSnapshot)
      return;
   if(!g_snap.Due())
      return;

   int fastP = g_fxmopol.EmaPeriod();
   int slowP = g_fxmopol.SlowEmaPeriod();
   int smmaP = g_fxmopol.SmmaPeriod();
   int rsiP  = g_fxmopol.RsiPeriod();

   SovMarketView v;
   if(!CSovereignMarketView::Build(_Symbol, _Period, fastP, slowP, smmaP, rsiP, 40, v))
      return;

   SovPivot piv[];
   CSovereignPatterns::BuildZigZag(_Symbol, _Period, InpVisualZigZagBars,
                                   InpVisualZigZagAtr, piv);
   SovPattern pat;
   string pattern = "none";
   if(CSovereignPatterns::MatchHarmonic(piv, pat) && pat.valid)
      pattern = pat.text;

   bool chartMatch = (g_tpl.Symbol() == _Symbol
                      && g_tpl.Period() == CSovereignTemplate::TfName(_Period));

   if(g_snap.Write(v,
                   g_fxmopol.PulseRegime(), g_fxmopol.PulseCtxLong(),
                   g_fxmopol.PulseCtxShort(), g_fxmopol.PulseAction(),
                   g_fxmopol.PulseIssues(), g_fxmopol.PulseD1(), pattern,
                   g_tpl.Loaded(), g_tpl.EmaFast(), g_tpl.EmaSlow(),
                   g_tpl.Smma(), g_tpl.Rsi(),
                   g_tpl.Aligned(fastP, slowP, smmaP, rsiP), chartMatch))
      Print("SNAPSHOT ", g_snap.Path(), " bar ", TimeToString(v.barTime, TIME_DATE | TIME_MINUTES));
}

void OnDeinit(const int reason)
{
   g_paint.ClearAll();
   Comment("");
}

//+------------------------------------------------------------------+
//| Render the bot's actual read of the tape.                        |
//|                                                                  |
//| Sources, in order of trust:                                      |
//|   1. g_fxmopol.Pulse*()  - the exact numbers the gates used      |
//|   2. g_tpl               - the indicators the human has loaded   |
//|   3. open positions      - real fills, real SL/TP                |
//|   4. zigzag/harmonic     - drawing only, labelled as such        |
//+------------------------------------------------------------------+
void PaintChart(const double dayLossPct)
{
   if(!InpShowVisuals)
      return;

   string sym = _Symbol;
   ENUM_TIMEFRAMES tf = _Period;
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   if(digits <= 0)
      digits = 5;

   int fastP = g_fxmopol.EmaPeriod();
   int slowP = g_fxmopol.SlowEmaPeriod();
   int smmaP = g_fxmopol.SmmaPeriod();
   int rsiP  = g_fxmopol.RsiPeriod();

   SovMarketView mv;
   bool haveView = CSovereignMarketView::Build(sym, tf, fastP, slowP, smmaP, rsiP,
                                               40, mv);

   //--- the template's own indicator levels -------------------------
   if(InpVisualEmaLines && haveView)
   {
      g_paint.DrawLevel("EMA_Fast", mv.emaFast, InpVisualFastColor, STYLE_DOT, 1, "EMA fast");
      g_paint.DrawLevel("EMA_Slow", mv.emaSlow, InpVisualSlowColor, STYLE_DOT, 1, "EMA slow");
      g_paint.DrawLevel("SMMA",     mv.smma,    InpVisualSmmaColor, STYLE_DOT, 1, "SMMA");
   }

   //--- zone / target / invalidation from the view -------------------
   if(InpVisualLevels && haveView)
   {
      datetime t1 = iTime(sym, tf, 12);
      datetime t2 = iTime(sym, tf, 0);
      if(t2 > 0)
         t2 = t2 + PeriodSeconds(tf) * 8;
      if(t1 > 0 && t2 > t1)
         g_paint.DrawBox("Zone", t1, mv.zoneHi, t2, mv.zoneLo, InpVisualZoneColor);

      g_paint.DrawLevel("Target",       mv.target,       InpVisualTpColor, STYLE_DASH, 1,
                        "Advisory target");
      g_paint.DrawLevel("Invalidation", mv.invalidation, InpVisualSlColor, STYLE_DASH, 1,
                        "Advisory invalidation");

      g_paint.DrawLevel("PivotR1", mv.r1, clrDimGray, STYLE_DOT, 1, "D1 pivot R1");
      g_paint.DrawLevel("PivotS1", mv.s1, clrDimGray, STYLE_DOT, 1, "D1 pivot S1");

      if(InpManualLevel > 0.0)
         g_paint.DrawLevel("Manual", InpManualLevel, clrGold, STYLE_SOLID, 2,
                           "InpManualLevel");

      if(mv.target > 0.0)
         g_paint.DrawText("TargetTxt", t2, mv.target,
                          "T " + DoubleToString(mv.target, digits),
                          InpVisualTpColor, ANCHOR_RIGHT, 8);
      if(mv.invalidation > 0.0)
         g_paint.DrawText("InvalidTxt", t2, mv.invalidation,
                          "X " + DoubleToString(mv.invalidation, digits),
                          InpVisualSlColor, ANCHOR_RIGHT, 8);
   }
   else if(!haveView)
   {
      g_paint.DrawLevel("Target", 0.0, InpVisualTpColor, STYLE_DASH, 1);
      g_paint.DrawLevel("Invalidation", 0.0, InpVisualSlColor, STYLE_DASH, 1);
   }

   //--- swing structure + harmonic shape (context only) --------------
   string patternLine = "pattern: none";
   if(InpVisualPatterns)
   {
      SovPivot piv[];
      int np = CSovereignPatterns::BuildZigZag(sym, tf, InpVisualZigZagBars,
                                               InpVisualZigZagAtr, piv);
      for(int i = 1; i < np; i++)
      {
         if(piv[i - 1].time <= 0 || piv[i].time <= 0)
            continue;
         g_paint.DrawTrend("Zig_" + IntegerToString((long)piv[i].time),
                           piv[i - 1].time, piv[i - 1].price,
                           piv[i].time, piv[i].price,
                           InpVisualPatternColor, STYLE_SOLID, 1, false);
      }
      SovPattern pat;
      if(CSovereignPatterns::MatchHarmonic(piv, pat) && pat.valid)
      {
         patternLine = "pattern: " + pat.text;
         if(np > 0 && piv[np - 1].time > 0)
            g_paint.DrawText("PatternTxt", piv[np - 1].time, piv[np - 1].price,
                             pat.name, InpVisualPatternColor, ANCHOR_LEFT, 8);
      }
   }

   //--- live positions: the only arrows that mean money ---------------
   int openCount = 0;
   if(InpVisualPositions)
   {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong ticket = PositionGetTicket(i);
         if(!PositionSelectByTicket(ticket))
            continue;
         if(PositionGetString(POSITION_SYMBOL) != sym)
            continue;
         if(PositionGetInteger(POSITION_MAGIC) != FXMOPOL_MAGIC)
            continue;

         long   type  = PositionGetInteger(POSITION_TYPE);
         double ent   = PositionGetDouble(POSITION_PRICE_OPEN);
         double sl    = PositionGetDouble(POSITION_SL);
         double tp    = PositionGetDouble(POSITION_TP);
         datetime ot  = (datetime)PositionGetInteger(POSITION_TIME);
         string tag   = IntegerToString((long)ticket);
         openCount++;

         if(ot > 0 && ent > 0.0)
         {
            if(type == POSITION_TYPE_BUY)
               g_paint.DrawArrow("Pos_" + tag, ot, ent, SOV_ARROW_UP, clrLime,
                                 "Open long #" + tag);
            else
               g_paint.DrawArrow("Pos_" + tag, ot, ent, SOV_ARROW_DOWN, clrTomato,
                                 "Open short #" + tag);
         }
         if(sl > 0.0)
            g_paint.DrawLevel("PosSL_" + tag, sl, InpVisualSlColor, STYLE_DASH, 1,
                              "Position stop #" + tag);
         if(tp > 0.0)
            g_paint.DrawLevel("PosTP_" + tag, tp, InpVisualTpColor, STYLE_DASH, 1,
                              "Position target #" + tag);
      }
   }

   if(!InpVisualPanel)
   {
      g_paint.Flush();
      return;
   }

   //--- panel -------------------------------------------------------
   string lines[22];
   color  cols[22];
   int n = 0;

   lines[n] = StringFormat("SOV FXMOPOL   %s %s", sym,
                           CSovereignTemplate::TfName(tf));
   cols[n]  = clrGold; n++;

   lines[n] = g_tpl.Status(fastP, slowP, smmaP, rsiP);
   cols[n]  = (g_tpl.Aligned(fastP, slowP, smmaP, rsiP) ? clrLime : clrOrange); n++;

   lines[n] = g_tpl.ChartStatus(sym, tf);
   cols[n]  = ((g_tpl.Symbol() == sym && g_tpl.Period() == CSovereignTemplate::TfName(tf))
               ? clrLime : clrOrange); n++;

   if(!g_fxmopol.PulseValid())
   {
      lines[n] = "analysis: waiting for the first completed bar";
      cols[n]  = clrGray; n++;
   }
   else
   {
      int reg = g_fxmopol.PulseRegime();
      lines[n] = StringFormat("REGIME %s   ctxL %d  ctxS %d   RSI %.1f   ATR %s",
                              (reg > 0 ? "UP" : (reg < 0 ? "DOWN" : "CHOP")),
                              g_fxmopol.PulseCtxLong(), g_fxmopol.PulseCtxShort(),
                              g_fxmopol.PulseRsi(),
                              DoubleToString(g_fxmopol.PulseAtr(), digits));
      cols[n]  = (reg > 0 ? clrLime : (reg < 0 ? clrTomato : clrOrange)); n++;

      lines[n] = "MOVE  " + g_fxmopol.PulseAction() +
                 StringFormat("   TL %dG/%dG", g_fxmopol.PulseTlLong(),
                              g_fxmopol.PulseTlShort());
      cols[n]  = (StringFind(g_fxmopol.PulseAction(), "ENTER") >= 0 ? clrAqua : clrSilver); n++;

      lines[n] = "Q1 " + g_fxmopol.PulseQ1();
      cols[n]  = clrSilver; n++;

      lines[n] = "Q2 " + g_fxmopol.PulseQ2();
      cols[n]  = clrSilver; n++;

      lines[n] = "D1 " + g_fxmopol.PulseD1();
      cols[n]  = clrSilver; n++;

      lines[n] = "ISSUES " + g_fxmopol.PulseIssues();
      cols[n]  = (g_fxmopol.PulseIssues() == "none" ? clrLime : clrOrange); n++;

      lines[n] = g_fxmopol.PulseProfessor();
      cols[n]  = clrSilver; n++;
   }

   if(haveView)
   {
      lines[n] = StringFormat("PRICE %s   EMA%d %s   EMA%d %s   SMMA%d %s",
                              DoubleToString(mv.price, digits),
                              fastP, DoubleToString(mv.emaFast, digits),
                              slowP, DoubleToString(mv.emaSlow, digits),
                              smmaP, DoubleToString(mv.smma, digits));
      cols[n]  = clrSilver; n++;

      lines[n] = mv.state;
      cols[n]  = (mv.trend > 0 ? clrLime : (mv.trend < 0 ? clrTomato : clrOrange)); n++;

      lines[n] = "PLAN  " + mv.plan;
      cols[n]  = (mv.bias > 0 ? clrLime : (mv.bias < 0 ? clrTomato : clrGray)); n++;

      lines[n] = StringFormat("%s   %s   %s", mv.zoneText, mv.targetText, mv.invalidText);
      cols[n]  = clrSilver; n++;

      lines[n] = StringFormat("swing hi %s  lo %s   fib50 %s  fib61.8 %s   S1 %s  R1 %s",
                              DoubleToString(mv.swingHi, digits),
                              DoubleToString(mv.swingLo, digits),
                              DoubleToString(mv.fib50, digits),
                              DoubleToString(mv.fib618, digits),
                              DoubleToString(mv.s1, digits),
                              DoubleToString(mv.r1, digits));
      cols[n]  = clrGray; n++;
   }

   lines[n] = patternLine;
   cols[n]  = clrSlateBlue; n++;

   lines[n] = StringFormat("positions %d   max/day %d   sl %.2fATR  tp %.2fATR   day guard %.2f/%.2f%%",
                           openCount, g_fxmopol.MaxPerDay(),
                           g_fxmopol.SlAtr(), g_fxmopol.TpAtr(),
                           dayLossPct, InpMaxDailyDD);
   cols[n]  = clrSilver; n++;

   lines[n] = g_perf.StatusLine();
   cols[n]  = clrGray; n++;

   g_paint.DrawPanel("FXM_PANEL", lines, cols, 12, 18, 13, 8,
                     (color)C'16,18,24', clrDimGray);
   g_paint.Flush();
}

void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &req, const MqlTradeResult &res)
{
   g_audit.LogDeal(trans, res.retcode);
   g_perf.OnTradeTransaction(trans);
   g_fxmopol.OnTradeClosed(trans);
}
