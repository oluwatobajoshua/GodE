//+------------------------------------------------------------------+
//|                                      fxmopol_v2.mq5              |
//|                               Copyright 2026, Quant Systems      |
//|    Entry/exit rules: Short/Long reversals on EMA21 + RSI        |
//|    Risk guards: lot cap, DD kill switch, margin guard, spread    |
//|    guard (symbol-scaled), weekend flat (symbol-aware), SL/TP     |
//|    verify, time stop, daily profit target                        |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026"
#property link      "https://www.mql5.com"
#property version   "1.30"
#property strict

#include <Trade\Trade.mqh>
#include <Sovereign\SovereignErrorLog.mqh>
#include <Sovereign\SovereignTemplateSpec.mqh>

//--- Magic number for this EA
#define FXMOPOL_MAGIC 20261115

CTrade trade;

//--- Template spec (runtime read-only chart vocabulary)
CSovereignTemplateSpec g_template;

//--- log level (referenced by SovereignError.mqh)
ENUM_LOG_LEVEL g_logLevel = LOG_INFO;

//--- error log (referenced by SovereignError.mqh)
CSovereignErrorLog g_errorLog;

//--- Indicator handles
int    g_ema21Handle = INVALID_HANDLE;
int    g_rsiHandle   = INVALID_HANDLE;
int    g_atrHandle   = INVALID_HANDLE;

//--- Buffers
double g_ema21Buf[100];
double g_rsiBuf[100];
double g_atrBuf[100];
double g_clBuf[100];
double g_opBuf[100];
double g_hiBuf[100];
double g_loBuf[100];

//--- State
datetime g_lastBar = 0;
int      g_consecAbove = 0;
int      g_consecBelow = 0;
double   g_swingHigh = 0.0;
double   g_swingLow  = 0.0;
double   g_fractalHigh = 0.0;  // most recent up fractal high
double   g_fractalLow  = 0.0;  // most recent down fractal low
datetime g_swingHighTime = 0;
datetime g_swingLowTime  = 0;

//--- Market Structure state (causal, updated once per new bar)
//--- Uptrend structure (for longs): green must close above prior red opens above EMA21
//--- Downtrend structure (for shorts): red must close below prior green closes below EMA21
double   g_redOpenHigh   = 0.0;   // highest open of recent red candles (uptrend reclaim level)
double   g_greenCloseLow = 0.0;   // lowest close of recent green candles (downtrend break level)
bool     g_structureIntactLong  = false; // uptrend structure intact
bool     g_structureIntactShort = false; // downtrend structure intact
datetime g_structLastBarTime    = 0;     // last bar structure was updated

//--- RSI Band state (causal, updated once per new bar)
double   g_rsiPrev     = 0.0;            // RSI on prior bar (for cross detection)
double   g_ema21Prev   = 0.0;            // EMA21 on prior bar (for slope direction)
int      g_rsiBandBarsHeldShort = 0;     // bars held in current short (RSI band mode)
int      g_rsiBandBarsHeldLong  = 0;     // bars held in current long (RSI band mode)
bool     g_rsiBandInShort  = false;      // currently in RSI band short
bool     g_rsiBandInLong   = false;      // currently in RSI band long

//--- Regular-path RSI exit state (tracks bars held independent of band mode)
int      g_regBarsHeldShort = 0;         // bars held in current short (regular path RSI exit)
int      g_regBarsHeldLong  = 0;         // bars held in current long (regular path RSI exit)

//--- EMA21 profit-gated exit state
datetime g_prevCloseTime  = 0;           // prior bar close time (for close-above/below-EMA detection)
double   g_prevClose      = 0.0;         // prior bar close price
double   g_avgShortEntry  = 0.0;         // average entry price of open shorts
double   g_avgLongEntry   = 0.0;         // average entry price of open longs

//--- Position tracking
bool   g_hasShort = false;
bool   g_hasLong  = false;
int    g_shortCount = 0;
int    g_longCount  = 0;

//--- Account-level kill switches & hard limits tracking
double   g_accountPeakEquity = 0.0;   // high watermark for drawdown kill switch
datetime g_accountDDCheckTime = 0;    // throttle drawdown check
bool     g_dailyProfitTargetHit = false;// true when daily profit target reached
double   g_dayStartEquity = 0.0;      // equity at day start
datetime g_dayKey = 0;                // current day key

//--- Spread ring buffer (symbol-scaled median spread for crypto-safe caps)
#define SOV_SPREAD_RING 200
int      g_sprRing[SOV_SPREAD_RING];
int      g_sprRingCnt = 0;
int      g_sprRingIdx = 0;
datetime g_sprRingBar = 0;
int      g_spreadMedianPts = -1;
datetime g_spreadMedianBar = 0;

//+------------------------------------------------------------------+
//| Inputs                                                           |
//+------------------------------------------------------------------+
input group "=== Basic Setup ==="
input int    InpMagicNumber   = FXMOPOL_MAGIC;   // EA magic number
input bool   InpAllowTrading  = true;           // master switch: allow entries (on by default)
input int    InpMaxPositions  = 5;               // max concurrent positions on this symbol

input group "=== Chart template (runtime, read-only) ==="
input bool   InpUseTemplate        = true;      // read the published template spec
input string InpTemplateName       = "fxmopol"; // published by scripts\sync_template.ps1
input bool   InpTemplateHotReload  = true;      // pick up template edits without a restart
input int    InpTemplateFractals   = 300;       // fractal lookback for swing markers

input group "=== Entry/Exit Rules ==="
input bool   InpEnableShort1 = true;   // series above EMA21 + red close below EMA21 + RSI 22-55 -> sell
input bool   InpEnableShort2 = true;   // 2+ below EMA21 + green near EMA21 + RSI 22-55 -> sell again
input bool   InpEnableShort3 = true;   // short2 + red engulfs prev green -> sell again
input bool   InpEnableShort4 = true;   // 3 green candles under EMA21 -> sell immediately
input bool   InpEnableShort5 = true;   // close below/above EMA21 + near EMA21 + RSI threshold -> trade
input bool   InpEnableReflexShort = true;  // 2 below + green above EMA21 + red back below -> sell (reflex short)

input bool   InpEnableLong1  = true;   // series below EMA21 + green close above EMA21 + RSI 45-78 -> buy
input bool   InpEnableLong2  = true;   // 2+ above EMA21 + red near EMA21 + RSI 45-78 -> buy again
input bool   InpEnableLong3  = true;   // long2 + green engulfs prev red -> buy again
input bool   InpEnableLong4  = true;   // 3 red candles above EMA21 -> buy immediately
input bool   InpEnableLong5  = true;   // close above/below EMA21 + near EMA21 + RSI threshold -> buy
input bool   InpEnableReflexLong  = true;  // 2 above + red below EMA21 + green back above -> buy (reflex long)
input bool   InpEnableExit1  = true;   // series below EMA21 + green close above EMA21 -> close all shorts
input bool   InpEnableExit2  = true;   // weak structure / divergence -> close all shorts
input int    InpMinConsecutive = 2;    // min consecutive candles above/below EMA21
input int    InpNearEmaPoints  = 200;  // "near EMA21" buffer in points (BTCUSD scale: ~$200 at 0.1 pt)
input double InpNearEmaAtrMult = 0.35; // near-EMA tolerance as ATR multiple (overrides points if >0)
input double InpRiskPct        = 0.5;  // risk % of equity per entry

input group "=== Money Management & Blowup Prevention ==="
input double InpEquityHardStopPct  = 10.0;    // halt all trading if equity drops this % from balance (0=disabled)
input double InpDailyLossHardStopPct = 5.0;   // halt trading today if day loss this % (0=disabled)
input int    InpMaxConcurrentPositions = 5;   // max open positions on this symbol
input double InpMaxLotSize              = 10.0;   // absolute max lot size per trade (fat-finger protection, 0=disabled)
input int    InpMaxPositionAgeBars      = 0;      // close positions older than N bars (0=disabled)
input bool   InpVerifySLTPOnEntry       = true;   // verify SL/TP actually set on broker after order placement
input double InpDailyProfitTargetPct    = 0.0;    // stop new entries today when up X% from day start (0=disabled)
input double InpAccountMaxDDFromPeakPct = 15.0;   // halt all trading if equity drops X% from account peak (0=disabled)
input double InpMinFreeMarginPct        = 20.0;   // block entries when free margin < X% of equity (0=disabled)
input int    InpMaxTotalSovPositions    = 20;     // max positions across ALL symbols (0=disabled)
input int    InpMaxSpreadForTrade       = 0;       // skip new entries when spread exceeds this (pts; 0=disabled)

input group "=== Panel (display only) ==="
input bool   InpShowPanel      = true;   // show fxmopol_v2 status panel on chart
input int    InpPanelCorner    = 0;      // 0=top-left, 1=top-right, 2=bottom-left, 3=bottom-right

input group "=== Market Structure Filter (exit/entry gate) ==="
input bool   InpEnableStructureFilter = false; // gate entries + exits on market structure (causal, no lookahead)

input group "=== RSI Band Mode (certified mean reversion) ==="
input bool   InpUseRsiBandMode        = false; // use RSI band cross entries + RSI level exits (replaces SHORT/LONG rules)
input int    InpRsiBandMid            = 50;    // RSI mid band: cross up = long signal, cross down = short signal
input int    InpRsiBandHi             = 75;    // RSI high exit: close longs when RSI > hi
input int    InpRsiBandLo             = 25;    // RSI low exit: close shorts when RSI < lo
input double InpRsiBandMargin         = 3.0;   // RSI must cross mid by this many points (not just touch)
input int    InpRsiBandMinHoldBars    = 12;    // min bars held before RSI-level exit may fire
input bool   InpRsiBandRequireTrend   = true;  // require EMA21 slope in trade direction (downtrend for shorts, uptrend for longs)
input bool   InpRsiExitEnabled        = true;  // close shorts at RSI<lo / longs at RSI>hi on regular path (no band mode needed)
input bool   InpEMA21ProfitGate       = true;  // exit below EMA21 only if in profit; close if price closes back over EMA21

//+------------------------------------------------------------------+
//| Symbol-scaled spread caps                                        |
//+------------------------------------------------------------------+
void SovSampleSpreadRing()
{
   datetime bar = iTime(_Symbol, _Period, 0);
   if(bar <= 0 || bar == g_sprRingBar) return;
   g_sprRingBar = bar;
   int s = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(g_sprRingCnt <= 0)
   {
      g_sprRing[0] = s;
      g_sprRingCnt = 1;
      g_sprRingIdx = 1;
      return;
   }
   g_sprRing[g_sprRingIdx] = s;
   g_sprRingIdx = (g_sprRingIdx + 1) % SOV_SPREAD_RING;
   if(g_sprRingCnt < SOV_SPREAD_RING) g_sprRingCnt++;
}

int SovMedianSpreadPts(const int bars)
{
   if(g_sprRingCnt <= 0) return 0;
   int n = MathMin(bars, g_sprRingCnt);
   if(n <= 0) return 0;
   int tmp[];
   if(ArrayResize(tmp, n) != n) return 0;
   for(int i = 0; i < n; i++) tmp[i] = g_sprRing[i];
   ArraySort(tmp);
   return tmp[n / 2];
}

int SovCachedMedianSpreadPts(const int bars)
{
   datetime bar = iTime(_Symbol, _Period, 0);
   if(g_spreadMedianBar == bar && g_spreadMedianPts >= 0) return g_spreadMedianPts;
   int med = SovMedianSpreadPts(bars);
   g_spreadMedianPts = med;
   g_spreadMedianBar = bar;
   return med;
}

int SovEffectiveSpreadCap(const int configuredCapPts)
{
   if(configuredCapPts <= 0) return 0;
   int med = SovCachedMedianSpreadPts(200);
   int cap = configuredCapPts;
   if(med > 0)
   {
      int rel = (int)MathRound(2.0 * (double)med);
      if(rel > cap) cap = rel;
   }
   return cap;
}

bool SovSymbolTradesOnWeekend()
{
   datetime from = 0, to = 0;
   bool sat = SymbolInfoSessionTrade(_Symbol, SATURDAY, 0, from, to);
   bool sun = SymbolInfoSessionTrade(_Symbol, SUNDAY,   0, from, to);
   return (sat || sun);
}

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   g_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   g_dayKey = 0;

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(10);

   SovSetTradeFilling(trade, _Symbol);

   g_errorLog.Init(true, "fxmopol_v2", 1000000);

   //--- create EMA21 + RSI handles
   g_ema21Handle = iMA(_Symbol, _Period, 21, 0, MODE_EMA, PRICE_CLOSE);
   g_rsiHandle   = iRSI(_Symbol, _Period, 9, PRICE_CLOSE);
   g_atrHandle   = iATR(_Symbol, _Period, 14);
   if(g_ema21Handle == INVALID_HANDLE || g_rsiHandle == INVALID_HANDLE || g_atrHandle == INVALID_HANDLE)
   {
      SovLogError("OnInit", "indicator handle creation failed");
      return(INIT_FAILED);
   }
   ArraySetAsSeries(g_ema21Buf, true);
   ArraySetAsSeries(g_rsiBuf, true);
   ArraySetAsSeries(g_atrBuf, true);
   ArraySetAsSeries(g_clBuf, true);
   ArraySetAsSeries(g_opBuf, true);
   ArraySetAsSeries(g_hiBuf, true);
   ArraySetAsSeries(g_loBuf, true);

   //--- load template spec (MA21/50/100, RSI9, fractals from fxmopol.tpl)
   if(InpUseTemplate)
   {
      g_template.Load(InpTemplateName);
      Print("fxmopol_v2: template " + g_template.Describe());
   }

   Print("fxmopol_v2 initialized on " + _Symbol + " " + EnumToString(_Period) +
         " | trading=" + ((InpAllowTrading) ? "ON" : "OFF") +
         " | weekend=" + ((SovSymbolTradesOnWeekend()) ? "24/7" : "FX-close"));

   Print("fxmopol_v2 PARAMS | %s | maxPositions=%d minConsecutive=%d nearEmaPts=%d riskPct=%.2f" +
         " | equityHardStop=%.1f dailyLossHardStop=%.1f accountDD=%.1f freeMarginMin=%.1f" +
         " | maxLot=%.1f maxAgeBars=%d verifySLTP=%s dailyProfitTarget=%.1f" +
         " | maxSpreadPts=%d maxTotalPositions=%d",
         _Symbol, InpMaxPositions, InpMinConsecutive, InpNearEmaPoints, InpRiskPct,
         InpEquityHardStopPct, InpDailyLossHardStopPct, InpAccountMaxDDFromPeakPct, InpMinFreeMarginPct,
         InpMaxLotSize, InpMaxPositionAgeBars, ((InpVerifySLTPOnEntry) ? "true" : "false"), InpDailyProfitTargetPct,
         InpMaxSpreadForTrade, InpMaxTotalSovPositions);

   Print("fxmopol_v2 RULES | %s | S1=%s S2=%s S3=%s S4=%s S5=%s RfxS=%s RfxL=%s Exit1=%s Exit2=%s",
         _Symbol,
         ((InpEnableShort1) ? "on" : "off"), ((InpEnableShort2) ? "on" : "off"),
         ((InpEnableShort3) ? "on" : "off"), ((InpEnableShort4) ? "on" : "off"),
         ((InpEnableShort5) ? "on" : "off"), ((InpEnableReflexShort) ? "on" : "off"),
         ((InpEnableReflexLong) ? "on" : "off"),
         ((InpEnableExit1) ? "on" : "off"), ((InpEnableExit2) ? "on" : "off"));

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   //--- hot-reload template spec when the .ini changes on disk
   if(InpUseTemplate && InpTemplateHotReload)
      g_template.ReloadIfChanged();

   //--- draw template MA lines on the chart (display only)
   if(InpUseTemplate && g_template.Loaded())
   {
      for(int i = 0; i < g_template.MaCount(); i++)
      {
         double val = g_template.MaValue(i, 0);
         if(val > 0.0)
         {
            string name = "FXMOPOL_MA_" + IntegerToString(i);
            if(ObjectFind(0, name) < 0)
               ObjectCreate(0, name, OBJ_HLINE, 0, 0, val);
            else
               ObjectSetDouble(0, name, OBJPROP_PRICE, val);
            ObjectSetInteger(0, name, OBJPROP_COLOR, g_template.MaColor(i));
            ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_DASH);
            ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
         }
      }
   }

   //--- only act on new bar
   datetime bt = iTime(_Symbol, _Period, 0);
   if(bt <= 0 || bt == g_lastBar) return;
   g_lastBar = bt;

   //--- copy indicator + price data
   if(CopyBuffer(g_ema21Handle, 0, 1, 100, g_ema21Buf) < 100) return;
   if(CopyBuffer(g_rsiHandle,   0, 1, 100, g_rsiBuf)   < 100) return;
   if(CopyBuffer(g_atrHandle,   0, 1, 100, g_atrBuf)   < 100) return;
   if(CopyClose(_Symbol, _Period, 1, 100, g_clBuf) < 100) return;
   if(CopyOpen (_Symbol, _Period, 1, 100, g_opBuf) < 100) return;
   if(CopyHigh (_Symbol, _Period, 1, 100, g_hiBuf) < 100) return;
   if(CopyLow  (_Symbol, _Period, 1, 100, g_loBuf) < 100) return;

   double ema  = g_ema21Buf[0];
   double rsi  = g_rsiBuf[0];
   double atr  = g_atrBuf[0];
   double c    = g_clBuf[0];
   double o    = g_opBuf[0];
   double h    = g_hiBuf[0];
   double l    = g_loBuf[0];

   double ema1 = g_ema21Buf[1];
   double c1   = g_clBuf[1];
   double o1   = g_opBuf[1];
   double h1   = g_hiBuf[1];
   double l1   = g_loBuf[1];

   bool green = (c > o);
   bool red   = (c < o);
   bool green1 = (c1 > o1);
   bool red1   = (c1 < o1);

   //--- save previous consecutive counts (trend state before this bar's close)
   int prevConsecAbove = g_consecAbove;
   int prevConsecBelow = g_consecBelow;

   //--- update consecutive above/below EMA21 counts
   if(c > ema) { g_consecAbove++; g_consecBelow = 0; }
   else if(c < ema) { g_consecBelow++; g_consecAbove = 0; }
   else { g_consecAbove = 0; g_consecBelow = 0; }

   //--- update swing high/low (recent 20-bar lookback)
   UpdateSwingHighLow();

    //--- update fractal levels (up fractal high for short SL, down fractal low for long SL)
    UpdateFractals();

    //--- update market structure (causal: entry filter + exit trigger)
    UpdateMarketStructure(c, o, ema);

    //--- structure gate: only trade in direction of intact structure
    bool structOKShort = (g_structureIntactShort || !InpEnableStructureFilter);
    bool structOKLong  = (g_structureIntactLong  || !InpEnableStructureFilter);

   //--- count open positions by side
   CountOpenPositions();

   //======================================================================
   //--- EXITS (checked before entries)
   //======================================================================
   if(InpAllowTrading)
   {
      //--- compute average entry price for open shorts/longs (for profit gating)
      double avgShortEntry = 0.0;
      double avgLongEntry  = 0.0;
      int    shortCount    = 0;
      int    longCount     = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong tk = PositionGetTicket(i);
         if(tk <= 0) continue;
         if(!PositionSelectByTicket(tk)) continue;
         if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         long posType = PositionGetInteger(POSITION_TYPE);
         double px = PositionGetDouble(POSITION_PRICE_OPEN);
         if(posType == POSITION_TYPE_SELL) { avgShortEntry += px; shortCount++; }
         else if(posType == POSITION_TYPE_BUY) { avgLongEntry += px; longCount++; }
      }
      if(shortCount > 0) avgShortEntry /= shortCount;
      if(longCount > 0)  avgLongEntry  /= longCount;
      g_avgShortEntry = avgShortEntry;
      g_avgLongEntry  = avgLongEntry;

      //--- EMA21 profit-gated exit
      if(InpEMA21ProfitGate)
      {
         bool shortInProfit = (g_hasShort && g_avgShortEntry > 0.0 && c < g_avgShortEntry);
         bool longInProfit  = (g_hasLong  && g_avgLongEntry  > 0.0 && c > g_avgLongEntry);
         bool closeAboveEMA = (g_prevClose > 0.0 && g_prevClose <= ema && c > ema);   // close crossed above EMA21
         bool closeBelowEMA = (g_prevClose > 0.0 && g_prevClose >= ema && c < ema);   // close crossed below EMA21

         //--- Unconditional: close if price closes back over EMA21 (regardless of profit)
         if(g_hasShort && closeAboveEMA)
         {
            CloseAllShorts();
            PrintFormat("fxmopol_v2 EMA21 EXIT short: close=%.5f above EMA21=%.5f -> all shorts closed", c, ema);
         }
         if(g_hasLong && closeBelowEMA)
         {
            CloseAllLongs();
            PrintFormat("fxmopol_v2 EMA21 EXIT long: close=%.5f below EMA21=%.5f -> all longs closed", c, ema);
         }

          //--- Below/above EMA21: allow Exit 1/2 only if in profit; hold if not in profit
          if(g_hasShort && c < ema && !shortInProfit)
          {
             // below EMA21 but not in profit -> block Exit 1/2 (hold)
          }
          else if(g_hasLong && c > ema && !longInProfit)
          {
             // above EMA21 but not in profit -> block Exit 1/2 (hold)
          }

          //--- Exit 2 (short): weak structure below EMA21 + in profit -> close all shorts
          if(InpEnableExit2 && g_hasShort && (!InpEMA21ProfitGate || shortInProfit) && c < ema)
          {
             bool weakStruct = (l > g_swingLow);
             if(weakStruct)
             {
                CloseAllShorts();
                Print("fxmopol_v2 EXIT2 short: weak structure below EMA21 + in profit -> all shorts closed");
             }
          }

          //--- Exit 2 (long): weak structure above EMA21 + in profit -> close all longs
          if(InpEnableExit2 && g_hasLong && (!InpEMA21ProfitGate || longInProfit) && c > ema)
          {
             bool weakStruct = (h < g_swingHigh);
             if(weakStruct)
             {
                CloseAllLongs();
                Print("fxmopol_v2 EXIT2 long: weak structure above EMA21 + in profit -> all longs closed");
             }
          }
       }

      //--- Exit 1 (short): trend reversal above EMA21 (only when profit gate off)
      if(InpEnableExit1 && !InpEMA21ProfitGate && g_hasShort && prevConsecBelow >= InpMinConsecutive && green && c > ema)
      {
         CloseAllShorts();
         Print("fxmopol_v2 EXIT1 short: series below EMA21 + green close above EMA21 -> all shorts closed");
      }

      //--- Exit 1 (long): trend reversal below EMA21 (only when profit gate off)
      if(InpEnableExit1 && !InpEMA21ProfitGate && g_hasLong && prevConsecAbove >= InpMinConsecutive && red && c < ema)
      {
         CloseAllLongs();
         Print("fxmopol_v2 EXIT1 long: series above EMA21 + red close below EMA21 -> all longs closed");
      }

      //--- Structure break exit (short): red failed to break below prior green closes -> buyers stepping in
      if(InpEnableStructureFilter && g_hasShort && !g_structureIntactShort)
      {
         CloseAllShorts();
         PrintFormat("fxmopol_v2 STRUCTURE BREAK short: red failed below green closes close=%.5f greenCloseLow=%.5f -> all shorts closed",
                     c, g_greenCloseLow);
      }

      //--- Structure break exit (long): green failed to reclaim above prior red opens -> sellers stepping in
      if(InpEnableStructureFilter && g_hasLong && !g_structureIntactLong)
      {
         CloseAllLongs();
         PrintFormat("fxmopol_v2 STRUCTURE BREAK long: green failed reclaim above red opens close=%.5f redOpenHigh=%.5f -> all longs closed",
                     c, g_redOpenHigh);
      }

      //--- RSI Band exits (replace EXIT1/EXIT2 when InpUseRsiBandMode=true)
      if(InpUseRsiBandMode)
      {
         //--- EMA21 slope: up if close > EMA21 and EMA21 rising vs prior bar
         bool emaSlopeUp   = (ema > g_ema21Prev);
         bool emaSlopeDown = (ema < g_ema21Prev);

         //--- Short RSI band exit: RSI below lo (after min hold) or trend flips up
         if(g_rsiBandInShort && g_hasShort)
         {
            g_rsiBandBarsHeldShort++;
            bool rsiExitShort = (g_rsiBandBarsHeldShort >= InpRsiBandMinHoldBars && rsi < (double)InpRsiBandLo);
            bool trendExitShort = (InpRsiBandRequireTrend && emaSlopeUp);
            if(rsiExitShort || trendExitShort)
            {
               CloseAllShorts();
               g_rsiBandInShort = false;
               g_rsiBandBarsHeldShort = 0;
               string reason = rsiExitShort ? "RSI below lo" : "trend flipped up";
               PrintFormat("fxmopol_v2 RSI BAND EXIT short: %s (rsi=%.1f lo=%d barsHeld=%d) -> all shorts closed",
                           reason, rsi, InpRsiBandLo, g_rsiBandBarsHeldShort);
            }
         }
         else
         {
            g_rsiBandBarsHeldShort = 0;
         }

         //--- Long RSI band exit: RSI above hi (after min hold) or trend flips down
         if(g_rsiBandInLong && g_hasLong)
         {
            g_rsiBandBarsHeldLong++;
            bool rsiExitLong = (g_rsiBandBarsHeldLong >= InpRsiBandMinHoldBars && rsi > (double)InpRsiBandHi);
            bool trendExitLong = (InpRsiBandRequireTrend && emaSlopeDown);
            if(rsiExitLong || trendExitLong)
            {
               CloseAllLongs();
               g_rsiBandInLong = false;
               g_rsiBandBarsHeldLong = 0;
               string reason = rsiExitLong ? "RSI above hi" : "trend flipped down";
               PrintFormat("fxmopol_v2 RSI BAND EXIT long: %s (rsi=%.1f hi=%d barsHeld=%d) -> all longs closed",
                           reason, rsi, InpRsiBandHi, g_rsiBandBarsHeldLong);
            }
         }
         else
         {
         g_rsiBandBarsHeldLong = 0;
       }
    }

    //--- Regular-path RSI level exit (active even when InpUseRsiBandMode=false)
    //--- Closes shorts when RSI < lo, longs when RSI > hi, after min hold bars
    if(InpRsiExitEnabled)
    {
       if(g_hasShort)
       {
          g_regBarsHeldShort++;
          if(g_regBarsHeldShort >= InpRsiBandMinHoldBars && rsi < (double)InpRsiBandLo)
          {
             CloseAllShorts();
             g_regBarsHeldShort = 0;
             PrintFormat("fxmopol_v2 RSI EXIT short: rsi=%.1f lo=%d barsHeld=%d -> all shorts closed",
                         rsi, InpRsiBandLo, g_regBarsHeldShort);
          }
       }
       else
       {
          g_regBarsHeldShort = 0;
       }

       if(g_hasLong)
       {
          g_regBarsHeldLong++;
          if(g_regBarsHeldLong >= InpRsiBandMinHoldBars && rsi > (double)InpRsiBandHi)
          {
             CloseAllLongs();
             g_regBarsHeldLong = 0;
             PrintFormat("fxmopol_v2 RSI EXIT long: rsi=%.1f hi=%d barsHeld=%d -> all longs closed",
                         rsi, InpRsiBandHi, g_regBarsHeldLong);
          }
       }
       else
       {
          g_regBarsHeldLong = 0;
       }
    }

    //--- Position age time stop
      if(InpMaxPositionAgeBars > 0)
      {
         for(int i = PositionsTotal() - 1; i >= 0; i--)
         {
            ulong tk = PositionGetTicket(i);
            if(tk <= 0) continue;
            if(!PositionSelectByTicket(tk)) continue;
            if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
            if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
            datetime openTime = (datetime)PositionGetInteger(POSITION_TIME);
            long type = PositionGetInteger(POSITION_TYPE);
            int side = (type == POSITION_TYPE_BUY) ? 1 : -1;
            int secondsPerPeriod = PeriodSeconds(_Period);
            if(secondsPerPeriod <= 0) continue;
            int barsHeld = (int)((TimeCurrent() - openTime) / secondsPerPeriod);
            if(barsHeld >= InpMaxPositionAgeBars)
            {
               string sideTxt = (side > 0) ? "long" : "short";
               if(trade.PositionClose(tk))
                  PrintFormat("fxmopol_v2 TIME STOP | %s | %s position ticket=%I64u closed after %d bars (max=%d)",
                              _Symbol, sideTxt, (long)tk, barsHeld, InpMaxPositionAgeBars);
               else
                  PrintFormat("fxmopol_v2 TIME STOP CLOSE FAILED | ticket=%I64u err=%d", (long)tk, GetLastError());
            }
         }
      }
   }

   //======================================================================
   //--- status panel (every tick, display only) — always visible
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);

   //--- symbol-scaled near-EMA tolerance: ATR multiple overrides fixed points if >0
   double nearEmaTolPts = (double)InpNearEmaPoints * point;
   if(InpNearEmaAtrMult > 0.0 && atr > 0.0)
   {
      double atrTolPts = InpNearEmaAtrMult * atr / point;
      if(atrTolPts > nearEmaTolPts) nearEmaTolPts = atrTolPts;
   }
   if(InpShowPanel)
      DrawPanel(c, o, h, l, ema, rsi, green, red, point, prevConsecAbove, prevConsecBelow, nearEmaTolPts);

   //======================================================================
   //--- ENTRIES (gated by risk checks)
   //======================================================================
   bool allowEntries = InpAllowTrading;

   //--- equity hard stop: halt if equity drops X% from balance
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity <= 0) equity = 1000.0;
   if(allowEntries && InpEquityHardStopPct > 0.0 && balance > 0.0)
   {
      double eqDD = (balance - equity) / balance * 100.0;
      if(eqDD >= InpEquityHardStopPct)
      {
         allowEntries = false;
         PrintFormat("fxmopol_v2 BLOWUP GUARD | %s | equity drawdown %.2f%% >= hard stop %.2f%% — halting all entries",
                     _Symbol, eqDD, InpEquityHardStopPct);
      }
   }

   //--- daily loss hard stop
   if(allowEntries && InpDailyLossHardStopPct > 0.0 && g_dayStartEquity > 0.0 && equity < g_dayStartEquity)
   {
      double dailyLossPct = (g_dayStartEquity - equity) / g_dayStartEquity * 100.0;
      if(dailyLossPct >= InpDailyLossHardStopPct)
      {
         allowEntries = false;
         PrintFormat("fxmopol_v2 BLOWUP GUARD | %s | daily loss %.2f%% >= hard stop %.2f%% — halting entries today",
                     _Symbol, dailyLossPct, InpDailyLossHardStopPct);
      }
   }

   //--- account drawdown kill switch (high watermark)
   if(allowEntries && InpAccountMaxDDFromPeakPct > 0.0)
   {
      datetime now = TimeCurrent();
      if(now - g_accountDDCheckTime >= 1)
      {
         g_accountDDCheckTime = now;
         if(g_accountPeakEquity == 0.0 || equity > g_accountPeakEquity)
            g_accountPeakEquity = equity;
         double ddPct = (g_accountPeakEquity - equity) / g_accountPeakEquity * 100.0;
         if(ddPct >= InpAccountMaxDDFromPeakPct)
         {
            allowEntries = false;
            PrintFormat("fxmopol_v2 ACCOUNT DD KILL SWITCH | %s | equity drawdown %.2f%% >= limit %.2f%% — halting all entries",
                        _Symbol, ddPct, InpAccountMaxDDFromPeakPct);
         }
      }
   }

   //--- free margin guard
   if(allowEntries && InpMinFreeMarginPct > 0.0)
   {
      double freeMrg = AccountInfoDouble(ACCOUNT_FREEMARGIN);
      double freeMarginPct = (equity > 0.0) ? freeMrg / equity * 100.0 : 100.0;
      if(freeMarginPct < InpMinFreeMarginPct)
      {
         allowEntries = false;
         PrintFormat("fxmopol_v2 FREE MARGIN GUARD | %s | free margin %.2f%% < min %.2f%% — blocking new entries",
                     _Symbol, freeMarginPct, InpMinFreeMarginPct);
      }
   }

   //--- total positions cap across all symbols
   if(allowEntries && InpMaxTotalSovPositions > 0)
   {
      int total = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong tk = PositionGetTicket(i);
         if(tk <= 0) continue;
         if(!PositionSelectByTicket(tk)) continue;
         if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
         total++;
      }
      if(total >= InpMaxTotalSovPositions)
      {
         allowEntries = false;
         PrintFormat("fxmopol_v2 TOTAL POSITION CAP | %s | %d/%d positions across all symbols — blocking new entries",
                     _Symbol, total, InpMaxTotalSovPositions);
      }
   }

   //--- daily profit target
   if(allowEntries && InpDailyProfitTargetPct > 0.0 && !g_dailyProfitTargetHit)
   {
      if(g_dayStartEquity > 0.0 && equity > 0.0)
      {
         double dayPct = (equity - g_dayStartEquity) / g_dayStartEquity * 100.0;
         if(dayPct >= InpDailyProfitTargetPct)
         {
            g_dailyProfitTargetHit = true;
            allowEntries = false;
            PrintFormat("fxmopol_v2 DAILY PROFIT TARGET | %s | up %.2f%% >= target %.2f%% — no more entries today",
                        _Symbol, dayPct, InpDailyProfitTargetPct);
         }
      }
   }

   //--- symbol-scaled spread guard (crypto-safe: uses 2x recent median spread)
   if(allowEntries && InpMaxSpreadForTrade > 0)
   {
      int spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
      int capPts = SovEffectiveSpreadCap(InpMaxSpreadForTrade);
      if(capPts > 0 && spread > capPts)
      {
         allowEntries = false;
         static datetime lastSpreadGuardBar = 0;
         datetime sgBar = iTime(_Symbol, _Period, 0);
         if(sgBar != lastSpreadGuardBar)
         {
            lastSpreadGuardBar = sgBar;
            double spPx = (double)spread * SymbolInfoDouble(_Symbol, SYMBOL_POINT);
            PrintFormat("fxmopol_v2 SPREAD GUARD | %s | spread %d pts (%.5f price units) > effective cap %d pts - blocking new entries",
                        _Symbol, spread, spPx, capPts);
         }
      }
   }

   //--- weekend flat: only for symbols whose broker session actually closes
   //--- crypto / synthetic indices trade 24/7 -> weekend flat does NOT block them
   if(allowEntries && !SovSymbolTradesOnWeekend())
   {
      MqlDateTime dt;
      TimeToStruct(TimeCurrent(), dt);
      bool weekendWindow = ((dt.day_of_week == 5 && dt.hour >= 21) ||
                            dt.day_of_week == 6 ||
                            dt.day_of_week == 0 ||
                            (dt.day_of_week == 1 && dt.hour < 1));
      if(weekendWindow)
      {
         allowEntries = false;
      }
   }

   //--- spread ring sample (once per bar)
   SovSampleSpreadRing();

   if(!allowEntries)
   {
      static datetime lastBlockBar = 0;
      datetime blkBar = iTime(_Symbol, _Period, 0);
      if(blkBar != lastBlockBar)
      {
         lastBlockBar = blkBar;
         Print("fxmopol_v2 ENTRIES BLOCKED | %s | risk guard active — no new entries this bar", _Symbol);
      }
      return;
   }

   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
    double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
    if(tickSize <= 0) tickSize = point;
    if(tickValue <= 0) tickValue = 1.0;

    //--- EMA21 slope direction for RSI band trend filter
    bool emaSlopeUp   = (g_ema21Prev > 0.0 && ema > g_ema21Prev);
    bool emaSlopeDown = (g_ema21Prev > 0.0 && ema < g_ema21Prev);

    //--- RSI cross of mid band
    double rsiMid = (double)InpRsiBandMid;
    double rsiMargin = (double)InpRsiBandMargin;
    bool rsiCrossedDownMid = (g_rsiPrev > rsiMid && rsi <= rsiMid - rsiMargin);
    bool rsiCrossedUpMid   = (g_rsiPrev < rsiMid && rsi >= rsiMid + rsiMargin);

    //======================================================================
    //--- RSI BAND SHORT ENTRY: downtrend + RSI crosses down through mid -> sell
    //--- SL = high of entry bar (or fractal high)
    //======================================================================
    if(InpUseRsiBandMode && structOKShort && !g_rsiBandInShort && g_shortCount < InpMaxPositions)
    {
       if(emaSlopeDown && rsiCrossedDownMid)
       {
          double sl = g_fractalHigh > 0.0 ? g_fractalHigh : h;
          double slDist = MathAbs(c - sl);
          if(slDist <= 0) slDist = 20 * point;
          double lots = CalcLots(equity, slDist, tickSize, tickValue);
          lots = CapLotSize(lots);
          ulong ticket = trade.Sell(lots, _Symbol, c, sl, 0, "RSI_SHORT");
          if(ticket > 0)
          {
             if(!VerifySLTP(trade, ticket, sl, 0, -1))
                Print("fxmopol_v2: RSI_SHORT SL/TP mismatch — position may be naked");
             g_hasShort = true;
             g_shortCount++;
             g_rsiBandInShort = true;
             g_rsiBandBarsHeldShort = 0;
             Print("fxmopol_v2 ENTRY RSI_SHORT | %s | sell lots=%.2f price=%.5f sl=%.5f rsi=%.1f mid=%d emaSlopeDown", _Symbol, lots, c, sl, rsi, InpRsiBandMid);
          }
       }
    }

    //======================================================================
    //--- RSI BAND LONG ENTRY: uptrend + RSI crosses up through mid -> buy
    //--- SL = low of entry bar (or fractal low)
    //======================================================================
    if(InpUseRsiBandMode && structOKLong && !g_rsiBandInLong && g_longCount < InpMaxPositions)
    {
       if(emaSlopeUp && rsiCrossedUpMid)
       {
          double sl = g_fractalLow > 0.0 ? g_fractalLow : l;
          double slDist = MathAbs(c - sl);
          if(slDist <= 0) slDist = 20 * point;
          double lots = CalcLots(equity, slDist, tickSize, tickValue);
          lots = CapLotSize(lots);
          ulong ticket = trade.Buy(lots, _Symbol, c, sl, 0, "RSI_LONG");
          if(ticket > 0)
          {
             if(!VerifySLTP(trade, ticket, sl, 0, 1))
                Print("fxmopol_v2: RSI_LONG SL/TP mismatch — position may be naked");
             g_hasLong = true;
             g_longCount++;
             g_rsiBandInLong = true;
             g_rsiBandBarsHeldLong = 0;
             Print("fxmopol_v2 ENTRY RSI_LONG | %s | buy lots=%.2f price=%.5f sl=%.5f rsi=%.1f mid=%d emaSlopeUp", _Symbol, lots, c, sl, rsi, InpRsiBandMid);
          }
       }
    }

   //======================================================================
   //--- SHORT 1: series above EMA21 + red close below EMA21 + RSI 25-50 -> sell
   //======================================================================
     if(InpEnableShort1 && !InpUseRsiBandMode && structOKShort && prevConsecAbove >= InpMinConsecutive && red && c < ema && rsi > 22.0 && rsi < 55.0)
   {
      if(g_shortCount < InpMaxPositions)
      {
         double sl = h;  // high of the red candle
         double slDist = MathAbs(c - sl);
         if(slDist <= 0) slDist = 20 * point;
         double lots = CalcLots(equity, slDist, tickSize, tickValue);
         lots = CapLotSize(lots);
         ulong ticket = trade.Sell(lots, _Symbol, c, sl, 0, "SHORT1");
         if(ticket > 0)
         {
            if(!VerifySLTP(trade, ticket, sl, 0, -1))
               Print("fxmopol_v2: SHORT1 SL/TP mismatch — position may be naked");
            g_hasShort = true;
            g_shortCount++;
            Print("fxmopol_v2 ENTRY SHORT1 | %s | sell lots=%.2f price=%.5f sl=%.5f rsi=%.1f consecAbove=%d", _Symbol, lots, c, sl, rsi, prevConsecAbove);
         }
      }
      else
      {
         Print("fxmopol_v2 SHORT1 blocked | %s | max short positions reached (%d/%d)", _Symbol, g_shortCount, InpMaxPositions);
      }
   }
   else if(InpEnableShort1)
   {
      string reason = "";
      if(prevConsecAbove < InpMinConsecutive) reason = "prevConsecAbove=" + IntegerToString(prevConsecAbove) + "<" + IntegerToString(InpMinConsecutive);
      else if(!red) reason = "candle not red";
      else if(!(c < ema)) reason = "close not below EMA21";
      else if(!(rsi > 25.0)) reason = "RSI<=" + DoubleToString(25.0,1);
      else if(!(rsi < 50.0)) reason = "RSI>=" + DoubleToString(50.0,1);
      Print("fxmopol_v2 SHORT1 skipped | %s | %s", _Symbol, reason);
   }

   //======================================================================
   //--- SHORT 2: 2+ below EMA21 + green near EMA21 + RSI 40-50 -> sell again
   //======================================================================
     if(InpEnableShort2 && !InpUseRsiBandMode && structOKShort && prevConsecBelow >= InpMinConsecutive && green &&
       c >= ema - nearEmaTolPts && c <= ema &&
       rsi > 22.0 && rsi < 55.0)
   {
      if(g_shortCount < InpMaxPositions)
      {
         double sl = g_fractalHigh > 0.0 ? g_fractalHigh : g_swingHigh;
         if(sl <= 0.0) sl = h1;
         double slDist = MathAbs(c - sl);
         if(slDist <= 0) slDist = 20 * point;
         double lots = CalcLots(equity, slDist, tickSize, tickValue);
         lots = CapLotSize(lots);
         ulong ticket = trade.Sell(lots, _Symbol, c, sl, 0, "SHORT2");
         if(ticket > 0)
         {
            if(!VerifySLTP(trade, ticket, sl, 0, -1))
               Print("fxmopol_v2: SHORT2 SL/TP mismatch — position may be naked");
            g_hasShort = true;
            g_shortCount++;
            Print("fxmopol_v2 ENTRY SHORT2 | %s | sell lots=%.2f price=%.5f sl=%.5f rsi=%.1f consecBelow=%d", _Symbol, lots, c, sl, rsi, prevConsecBelow);
         }
      }
      else
      {
         Print("fxmopol_v2 SHORT2 blocked | %s | max short positions reached (%d/%d)", _Symbol, g_shortCount, InpMaxPositions);
      }
   }
   else if(InpEnableShort2)
   {
      string reason = "";
      if(prevConsecBelow < InpMinConsecutive) reason = "prevConsecBelow=" + IntegerToString(prevConsecBelow) + "<" + IntegerToString(InpMinConsecutive);
      else if(!green) reason = "candle not green";
      else if(!(c >= ema - nearEmaTolPts)) reason = "close not near EMA21 (below)";
      else if(!(c <= ema)) reason = "close not at/below EMA21";
      else if(!(rsi > 22.0)) reason = "RSI<=" + DoubleToString(22.0,1);
      else if(!(rsi < 55.0)) reason = "RSI>=" + DoubleToString(55.0,1);
      Print("fxmopol_v2 SHORT2 skipped | %s | %s", _Symbol, reason);
   }

   //======================================================================
   //--- SHORT 3: short2 setup + red candle engulfs prev green (close < prev open) -> sell again
   //======================================================================
     if(InpEnableShort3 && !InpUseRsiBandMode && structOKShort && prevConsecBelow >= InpMinConsecutive && red && c <= ema && c < o1 && green1)
   {
      if(g_shortCount < InpMaxPositions)
      {
         double sl = g_fractalHigh > 0.0 ? g_fractalHigh : g_swingHigh;
         if(sl <= 0.0) sl = h1;
         double slDist = MathAbs(c - sl);
         if(slDist <= 0) slDist = 20 * point;
         double lots = CalcLots(equity, slDist, tickSize, tickValue);
         lots = CapLotSize(lots);
         ulong ticket = trade.Sell(lots, _Symbol, c, sl, 0, "SHORT3");
         if(ticket > 0)
         {
            if(!VerifySLTP(trade, ticket, sl, 0, -1))
               Print("fxmopol_v2: SHORT3 SL/TP mismatch — position may be naked");
            g_hasShort = true;
            g_shortCount++;
            Print("fxmopol_v2 ENTRY SHORT3 | %s | sell lots=%.2f price=%.5f sl=%.5f engulfing consecBelow=%d", _Symbol, lots, c, sl, prevConsecBelow);
         }
      }
      else
      {
         Print("fxmopol_v2 SHORT3 blocked | %s | max short positions reached (%d/%d)", _Symbol, g_shortCount, InpMaxPositions);
      }
   }
   else if(InpEnableShort3)
   {
      string reason = "";
      if(prevConsecBelow < InpMinConsecutive) reason = "prevConsecBelow=" + IntegerToString(prevConsecBelow) + "<" + IntegerToString(InpMinConsecutive);
      else if(!red) reason = "candle not red";
      else if(!(c <= ema)) reason = "close not at/below EMA21";
      else if(!(c < o1)) reason = "close not below prev open";
      else if(!green1) reason = "prev candle not green";
      Print("fxmopol_v2 SHORT3 skipped | %s | %s", _Symbol, reason);
   }

   //======================================================================
   //--- SHORT 4: 3 consecutive green candles all under EMA21 -> sell immediately
   //======================================================================
    if(InpEnableShort4 && !InpUseRsiBandMode)
   {
      double c2 = g_clBuf[1]; double o2 = g_opBuf[1]; double ema2 = g_ema21Buf[1];
      double c3 = g_clBuf[2]; double o3 = g_opBuf[2]; double ema3 = g_ema21Buf[2];
      bool g1 = (c > o);   bool b1 = (c < ema);
      bool g2 = (c2 > o2); bool b2 = (c2 < ema2);
      bool g3 = (c3 > o3); bool b3 = (c3 < ema3);

      if(g_shortCount < InpMaxPositions && structOKShort && g1 && b1 && g2 && b2 && g3 && b3)
      {
         double sl = g_fractalHigh > 0.0 ? g_fractalHigh : g_swingHigh;
         if(sl <= 0.0) sl = h;
         double slDist = MathAbs(c - sl);
         if(slDist <= 0) slDist = 20 * point;
         double lots = CalcLots(equity, slDist, tickSize, tickValue);
         lots = CapLotSize(lots);
         ulong ticket = trade.Sell(lots, _Symbol, c, sl, 0, "SHORT4");
         if(ticket > 0)
         {
            if(!VerifySLTP(trade, ticket, sl, 0, -1))
               Print("fxmopol_v2: SHORT4 SL/TP mismatch — position may be naked");
            g_hasShort = true;
            g_shortCount++;
            Print("fxmopol_v2 ENTRY SHORT4 | %s | sell lots=%.2f price=%.5f sl=%.5f (3 green under EMA21)", _Symbol, lots, c, sl);
         }
      }
      else if(g_shortCount >= InpMaxPositions)
      {
         Print("fxmopol_v2 SHORT4 blocked | %s | max short positions reached (%d/%d)", _Symbol, g_shortCount, InpMaxPositions);
      }
      else
      {
         string reason = "";
         if(!g1) reason = "current candle not green";
         else if(!b1) reason = "current close not below EMA21";
         else if(!g2) reason = "bar-1 candle not green";
         else if(!b2) reason = "bar-1 close not below EMA21";
         else if(!g3) reason = "bar-2 candle not green";
         else if(!b3) reason = "bar-2 close not below EMA21";
         Print("fxmopol_v2 SHORT4 skipped | %s | %s", _Symbol, reason);
      }
   }

   //======================================================================
   //--- LONG 1: series below EMA21 + green close above EMA21 + RSI 50-75 -> buy
   //======================================================================
    if(InpEnableLong1 && !InpUseRsiBandMode && structOKLong && prevConsecBelow >= InpMinConsecutive && green && c > ema && rsi > 45.0 && rsi < 78.0)
   {
      if(g_longCount < InpMaxPositions)
      {
         double sl = l;  // low of the green candle
         double slDist = MathAbs(c - sl);
         if(slDist <= 0) slDist = 20 * point;
         double lots = CalcLots(equity, slDist, tickSize, tickValue);
         lots = CapLotSize(lots);
         ulong ticket = trade.Buy(lots, _Symbol, c, sl, 0, "LONG1");
         if(ticket > 0)
         {
            if(!VerifySLTP(trade, ticket, sl, 0, 1))
               Print("fxmopol_v2: LONG1 SL/TP mismatch — position may be naked");
            g_hasLong = true;
            g_longCount++;
            Print("fxmopol_v2 ENTRY LONG1 | %s | buy lots=%.2f price=%.5f sl=%.5f rsi=%.1f consecBelow=%d", _Symbol, lots, c, sl, rsi, prevConsecBelow);
         }
      }
      else
      {
         Print("fxmopol_v2 LONG1 blocked | %s | max long positions reached (%d/%d)", _Symbol, g_longCount, InpMaxPositions);
      }
   }
   else if(InpEnableLong1)
   {
      string reason = "";
      if(prevConsecBelow < InpMinConsecutive) reason = "prevConsecBelow=" + IntegerToString(prevConsecBelow) + "<" + IntegerToString(InpMinConsecutive);
      else if(!green) reason = "candle not green";
      else if(!(c > ema)) reason = "close not above EMA21";
      else if(!(rsi > 45.0)) reason = "RSI<=" + DoubleToString(45.0,1);
      else if(!(rsi < 78.0)) reason = "RSI>=" + DoubleToString(78.0,1);
      Print("fxmopol_v2 LONG1 skipped | %s | %s", _Symbol, reason);
   }

   //======================================================================
   //--- LONG 2: 2+ above EMA21 + red near EMA21 + RSI 50-60 -> buy again
   //======================================================================
     if(InpEnableLong2 && !InpUseRsiBandMode && structOKLong && prevConsecAbove >= InpMinConsecutive && red &&
       c >= ema && c <= ema + nearEmaTolPts &&
       rsi > 45.0 && rsi < 78.0)
   {
      if(g_longCount < InpMaxPositions)
      {
         double sl = g_fractalLow > 0.0 ? g_fractalLow : g_swingLow;
         if(sl <= 0.0) sl = l1;
         double slDist = MathAbs(c - sl);
         if(slDist <= 0) slDist = 20 * point;
         double lots = CalcLots(equity, slDist, tickSize, tickValue);
         lots = CapLotSize(lots);
         ulong ticket = trade.Buy(lots, _Symbol, c, sl, 0, "LONG2");
         if(ticket > 0)
         {
            if(!VerifySLTP(trade, ticket, sl, 0, 1))
               Print("fxmopol_v2: LONG2 SL/TP mismatch — position may be naked");
            g_hasLong = true;
            g_longCount++;
            Print("fxmopol_v2 ENTRY LONG2 | %s | buy lots=%.2f price=%.5f sl=%.5f rsi=%.1f consecAbove=%d", _Symbol, lots, c, sl, rsi, prevConsecAbove);
         }
      }
      else
      {
         Print("fxmopol_v2 LONG2 blocked | %s | max long positions reached (%d/%d)", _Symbol, g_longCount, InpMaxPositions);
      }
   }
   else if(InpEnableLong2)
   {
      string reason = "";
      if(prevConsecAbove < InpMinConsecutive) reason = "prevConsecAbove=" + IntegerToString(prevConsecAbove) + "<" + IntegerToString(InpMinConsecutive);
      else if(!red) reason = "candle not red";
      else if(!(c >= ema)) reason = "close not at/above EMA21";
      else if(!(c <= ema + nearEmaTolPts)) reason = "close not near EMA21";
      else if(!(rsi > 45.0)) reason = "RSI<=" + DoubleToString(45.0,1);
      else if(!(rsi < 78.0)) reason = "RSI>=" + DoubleToString(78.0,1);
      Print("fxmopol_v2 LONG2 skipped | %s | %s", _Symbol, reason);
   }

   //======================================================================
   //--- LONG 3: long2 setup + green candle engulfs prev red (close > prev open) -> buy again
   //======================================================================
    if(InpEnableLong3 && !InpUseRsiBandMode && structOKLong && prevConsecAbove >= InpMinConsecutive && green && c >= ema && c > o1 && red1)
   {
      if(g_longCount < InpMaxPositions)
      {
         double sl = g_fractalLow > 0.0 ? g_fractalLow : g_swingLow;
         if(sl <= 0.0) sl = l1;
         double slDist = MathAbs(c - sl);
         if(slDist <= 0) slDist = 20 * point;
         double lots = CalcLots(equity, slDist, tickSize, tickValue);
         lots = CapLotSize(lots);
         ulong ticket = trade.Buy(lots, _Symbol, c, sl, 0, "LONG3");
         if(ticket > 0)
         {
            if(!VerifySLTP(trade, ticket, sl, 0, 1))
               Print("fxmopol_v2: LONG3 SL/TP mismatch — position may be naked");
            g_hasLong = true;
            g_longCount++;
            Print("fxmopol_v2 ENTRY LONG3 | %s | buy lots=%.2f price=%.5f sl=%.5f engulfing consecAbove=%d", _Symbol, lots, c, sl, prevConsecAbove);
         }
      }
      else
      {
         Print("fxmopol_v2 LONG3 blocked | %s | max long positions reached (%d/%d)", _Symbol, g_longCount, InpMaxPositions);
      }
   }
   else if(InpEnableLong3)
   {
      string reason = "";
      if(prevConsecAbove < InpMinConsecutive) reason = "prevConsecAbove=" + IntegerToString(prevConsecAbove) + "<" + IntegerToString(InpMinConsecutive);
      else if(!green) reason = "candle not green";
      else if(!(c >= ema)) reason = "close not at/above EMA21";
      else if(!(c > o1)) reason = "close not above prev open";
      else if(!red1) reason = "prev candle not red";
      Print("fxmopol_v2 LONG3 skipped | %s | %s", _Symbol, reason);
   }

   //======================================================================
   //--- LONG 4: 3 consecutive red candles all above EMA21 -> buy immediately
   //======================================================================
    if(InpEnableLong4 && !InpUseRsiBandMode)
   {
      double c2 = g_clBuf[1]; double o2 = g_opBuf[1]; double ema2 = g_ema21Buf[1];
      double c3 = g_clBuf[2]; double o3 = g_opBuf[2]; double ema3 = g_ema21Buf[2];
      bool r1 = (c < o);   bool a1 = (c > ema);
      bool r2 = (c2 < o2); bool a2 = (c2 > ema2);
      bool r3 = (c3 < o3); bool a3 = (c3 > ema3);

      if(g_longCount < InpMaxPositions && structOKLong && r1 && a1 && r2 && a2 && r3 && a3)
      {
         double sl = g_fractalLow > 0.0 ? g_fractalLow : g_swingLow;
         if(sl <= 0.0) sl = l;
         double slDist = MathAbs(c - sl);
         if(slDist <= 0) slDist = 20 * point;
         double lots = CalcLots(equity, slDist, tickSize, tickValue);
         lots = CapLotSize(lots);
         ulong ticket = trade.Buy(lots, _Symbol, c, sl, 0, "LONG4");
         if(ticket > 0)
         {
            if(!VerifySLTP(trade, ticket, sl, 0, 1))
               Print("fxmopol_v2: LONG4 SL/TP mismatch — position may be naked");
            g_hasLong = true;
            g_longCount++;
            Print("fxmopol_v2 ENTRY LONG4 | %s | buy lots=%.2f price=%.5f sl=%.5f (3 red above EMA21)", _Symbol, lots, c, sl);
         }
      }
      else if(g_longCount >= InpMaxPositions)
      {
         Print("fxmopol_v2 LONG4 blocked | %s | max long positions reached (%d/%d)", _Symbol, g_longCount, InpMaxPositions);
      }
      else
      {
         string reason = "";
         if(!r1) reason = "current candle not red";
         else if(!a1) reason = "current close not above EMA21";
         else if(!r2) reason = "bar-1 candle not red";
         else if(!a2) reason = "bar-1 close not above EMA21";
         else if(!r3) reason = "bar-2 candle not red";
         else if(!a3) reason = "bar-2 close not above EMA21";
         Print("fxmopol_v2 LONG4 skipped | %s | %s", _Symbol, reason);
      }
   }

   //======================================================================
   //--- SHORT 5: no open short, close below EMA21 + near EMA21 + RSI > 40 -> sell
   //--- SL = previous up-fractal high
   //======================================================================
     if(InpEnableShort5 && !InpUseRsiBandMode && structOKShort && !g_hasShort && prevConsecBelow >= InpMinConsecutive && c < ema && rsi > 22.0)
   {
      bool nearEma = (c >= ema - nearEmaTolPts && c <= ema + nearEmaTolPts);
      if(nearEma && g_shortCount < InpMaxPositions)
      {
         double sl = g_fractalHigh > 0.0 ? g_fractalHigh : g_swingHigh;
         if(sl <= 0.0) sl = h + 20 * point;
         double slDist = MathAbs(c - sl);
         if(slDist <= 0) slDist = 20 * point;
         double lots = CalcLots(equity, slDist, tickSize, tickValue);
         lots = CapLotSize(lots);
         ulong ticket = trade.Sell(lots, _Symbol, c, sl, 0, "SHORT5");
         if(ticket > 0)
         {
            if(!VerifySLTP(trade, ticket, sl, 0, -1))
               Print("fxmopol_v2: SHORT5 SL/TP mismatch — position may be naked");
            g_hasShort = true;
            g_shortCount++;
            Print("fxmopol_v2 ENTRY SHORT5 | %s | sell lots=%.2f price=%.5f sl=%.5f rsi=%.1f (below EMA21, near EMA21)", _Symbol, lots, c, sl, rsi);
         }
      }
      else if(g_shortCount >= InpMaxPositions)
      {
         Print("fxmopol_v2 SHORT5 blocked | %s | max short positions reached (%d/%d)", _Symbol, g_shortCount, InpMaxPositions);
      }
      else if(!nearEma)
      {
         Print("fxmopol_v2 SHORT5 skipped | %s | close not near EMA21", _Symbol);
      }
   }
   else if(InpEnableShort5)
   {
      string reason = "";
      if(g_hasShort) reason = "already short";
      else if(prevConsecBelow < InpMinConsecutive) reason = "prevConsecBelow=" + IntegerToString(prevConsecBelow) + "<" + IntegerToString(InpMinConsecutive);
      else if(!(c < ema)) reason = "close not below EMA21";
      else if(!(rsi > 22.0)) reason = "RSI<=" + DoubleToString(22.0,1);
      Print("fxmopol_v2 SHORT5 skipped | %s | %s", _Symbol, reason);
   }

   //======================================================================
   //--- LONG 5: no open long, close above EMA21 + near EMA21 + RSI < 60 -> buy
   //--- SL = previous down-fractal low
   //======================================================================
     if(InpEnableLong5 && !InpUseRsiBandMode && structOKLong && !g_hasLong && prevConsecAbove >= InpMinConsecutive && c > ema && rsi < 78.0)
   {
      bool nearEma = (c >= ema - nearEmaTolPts && c <= ema + nearEmaTolPts);
      if(nearEma && g_longCount < InpMaxPositions)
      {
         double sl = g_fractalLow > 0.0 ? g_fractalLow : g_swingLow;
         if(sl <= 0.0) sl = l - 20 * point;
         double slDist = MathAbs(c - sl);
         if(slDist <= 0) slDist = 20 * point;
         double lots = CalcLots(equity, slDist, tickSize, tickValue);
         lots = CapLotSize(lots);
         ulong ticket = trade.Buy(lots, _Symbol, c, sl, 0, "LONG5");
         if(ticket > 0)
         {
            if(!VerifySLTP(trade, ticket, sl, 0, 1))
               Print("fxmopol_v2: LONG5 SL/TP mismatch — position may be naked");
            g_hasLong = true;
            g_longCount++;
            Print("fxmopol_v2 ENTRY LONG5 | %s | buy lots=%.2f price=%.5f sl=%.5f rsi=%.1f (above EMA21, near EMA21)", _Symbol, lots, c, sl, rsi);
         }
      }
      else if(g_longCount >= InpMaxPositions)
      {
         Print("fxmopol_v2 LONG5 blocked | %s | max long positions reached (%d/%d)", _Symbol, g_longCount, InpMaxPositions);
      }
      else if(!nearEma)
      {
         Print("fxmopol_v2 LONG5 skipped | %s | close not near EMA21", _Symbol);
      }
   }
   else if(InpEnableLong5)
   {
      string reason = "";
      if(g_hasLong) reason = "already long";
      else if(prevConsecAbove < InpMinConsecutive) reason = "prevConsecAbove=" + IntegerToString(prevConsecAbove) + "<" + IntegerToString(InpMinConsecutive);
      else if(!(c > ema)) reason = "close not above EMA21";
      else if(!(rsi < 78.0)) reason = "RSI>=" + DoubleToString(78.0,1);
      Print("fxmopol_v2 LONG5 skipped | %s | %s", _Symbol, reason);
   }

    //======================================================================
    //--- REFLEX SHORT: 2 below EMA21 + green close above EMA21 + red back below -> sell
    //--- SL = previous up-fractal high or swing high
    //======================================================================
   if(InpEnableReflexShort && !InpUseRsiBandMode && structOKShort && !g_hasShort && g_shortCount < InpMaxPositions)
   {
      double c2 = g_clBuf[2]; double ema2 = g_ema21Buf[2];
      double c3 = g_clBuf[3]; double ema3 = g_ema21Buf[3];
      bool twoBelow = (c2 < ema2) && (c3 < ema3);
      bool greenAbove = green1 && (c1 > ema1);
      bool redBackBelow = red && (c < ema);

      if(twoBelow && greenAbove && redBackBelow)
      {
         double sl = g_fractalHigh > 0.0 ? g_fractalHigh : g_swingHigh;
         if(sl <= 0.0) sl = h1 + 20 * point;
         double slDist = MathAbs(c - sl);
         if(slDist <= 0) slDist = 20 * point;
         double lots = CalcLots(equity, slDist, tickSize, tickValue);
         lots = CapLotSize(lots);
         ulong ticket = trade.Sell(lots, _Symbol, c, sl, 0, "REFLEX_SHORT");
         if(ticket > 0)
         {
            if(!VerifySLTP(trade, ticket, sl, 0, -1))
               Print("fxmopol_v2: REFLEX_SHORT SL/TP mismatch — position may be naked");
            g_hasShort = true;
            g_shortCount++;
            Print("fxmopol_v2 ENTRY REFLEX_SHORT | %s | sell lots=%.2f price=%.5f sl=%.5f (2below+greenAbove+redBack)", _Symbol, lots, c, sl);
         }
      }
      else if(g_shortCount >= InpMaxPositions)
      {
         Print("fxmopol_v2 REFLEX_SHORT blocked | %s | max short positions reached (%d/%d)", _Symbol, g_shortCount, InpMaxPositions);
      }
   }
   else if(InpEnableReflexShort)
   {
      string reason = "";
      if(g_hasShort) reason = "already short";
      else if(!red) reason = "current candle not red";
      else if(!(c < ema)) reason = "current close not below EMA21";
      else if(!green1) reason = "prev candle not green";
      else if(!(c1 > ema1)) reason = "prev close not above EMA21";
      else if(!(g_clBuf[2] < g_ema21Buf[2])) reason = "bar-2 close not below EMA21";
      else if(!(g_clBuf[3] < g_ema21Buf[3])) reason = "bar-3 close not below EMA21";
      Print("fxmopol_v2 REFLEX_SHORT skipped | %s | %s", _Symbol, reason);
   }

   //======================================================================
   //--- REFLEX LONG: 2 above EMA21 + red close below EMA21 + green back above -> buy
   //--- SL = previous down-fractal low or swing low
   //======================================================================
   if(InpEnableReflexLong && !InpUseRsiBandMode && structOKLong && !g_hasLong && g_longCount < InpMaxPositions)
   {
      double c2 = g_clBuf[2]; double ema2 = g_ema21Buf[2];
      double c3 = g_clBuf[3]; double ema3 = g_ema21Buf[3];
      bool twoAbove = (c2 > ema2) && (c3 > ema3);
      bool redBelow = red1 && (c1 < ema1);
      bool greenBackAbove = green && (c > ema);

      if(twoAbove && redBelow && greenBackAbove)
      {
         double sl = g_fractalLow > 0.0 ? g_fractalLow : g_swingLow;
         if(sl <= 0.0) sl = l1 - 20 * point;
         double slDist = MathAbs(c - sl);
         if(slDist <= 0) slDist = 20 * point;
         double lots = CalcLots(equity, slDist, tickSize, tickValue);
         lots = CapLotSize(lots);
         ulong ticket = trade.Buy(lots, _Symbol, c, sl, 0, "REFLEX_LONG");
         if(ticket > 0)
         {
            if(!VerifySLTP(trade, ticket, sl, 0, 1))
               Print("fxmopol_v2: REFLEX_LONG SL/TP mismatch — position may be naked");
            g_hasLong = true;
            g_longCount++;
            Print("fxmopol_v2 ENTRY REFLEX_LONG | %s | buy lots=%.2f price=%.5f sl=%.5f (2above+redBelow+greenBack)", _Symbol, lots, c, sl);
         }
      }
      else if(g_longCount >= InpMaxPositions)
      {
         Print("fxmopol_v2 REFLEX_LONG blocked | %s | max long positions reached (%d/%d)", _Symbol, g_longCount, InpMaxPositions);
      }
   }
   else if(InpEnableReflexLong)
   {
      string reason = "";
      if(g_hasLong) reason = "already long";
      else if(!green) reason = "current candle not green";
      else if(!(c > ema)) reason = "current close not above EMA21";
      else if(!red1) reason = "prev candle not red";
      else if(!(c1 < ema1)) reason = "prev close not below EMA21";
      else if(!(g_clBuf[2] > g_ema21Buf[2])) reason = "bar-2 close not above EMA21";
      else if(!(g_clBuf[3] > g_ema21Buf[3])) reason = "bar-3 close not above EMA21";
      Print("fxmopol_v2 REFLEX_LONG skipped | %s | %s", _Symbol, reason);
   }

    //======================================================================
    //--- FRACTAL TRAILING STOP: move short SL up to new up-fractal highs,   |
   //--- move long SL down to new down-fractal lows (only ever improve)     |
   //======================================================================
   UpdateFractalTrailingStop();

   //--- Save RSI band state for next bar cross detection
   g_rsiPrev = rsi;
   g_ema21Prev = ema;
   g_prevClose = c;
}

//+------------------------------------------------------------------+
//| CapLotSize — enforce absolute max lot size per trade.              |
//+------------------------------------------------------------------+
double CapLotSize(double lot)
{
   if(InpMaxLotSize <= 0.0) return lot;
   if(lot > InpMaxLotSize)
   {
      PrintFormat("fxmopol_v2 MAX LOT CAP | %s | requested lot %.2f capped to max %.2f",
                  _Symbol, lot, InpMaxLotSize);
      return InpMaxLotSize;
   }
   return lot;
}

//+------------------------------------------------------------------+
//| VerifySLTP — confirm SL/TP were actually set on the broker.       |
//+------------------------------------------------------------------+
bool VerifySLTP(CTrade &t, const ulong ticket, const double reqSL, const double reqTP, const int side)
{
   if(!InpVerifySLTPOnEntry) return true;
   if(ticket <= 0) return false;
   if(!PositionSelectByTicket(ticket)) return false;
   double sl = PositionGetDouble(POSITION_SL);
   double tp = PositionGetDouble(POSITION_TP);
   bool slOK  = (reqSL <= 0.0) || (MathAbs(sl - reqSL) < _Point * 10);
   bool tpOK  = (reqTP <= 0.0) || (MathAbs(tp - reqTP) < _Point * 10);
   if(!slOK || !tpOK)
   {
      PrintFormat("fxmopol_v2 SL/TP MISMATCH | %s | ticket=%I64u reqSL=%.5f reqTP=%.5f actualSL=%.5f actualTP=%.5f",
                  _Symbol, (long)ticket, reqSL, reqTP, sl, tp);
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| DrawPanel — status panel communicating rules + waiting state     |
//+------------------------------------------------------------------+
void DrawPanel(const double c, const double o, const double h, const double l,
               const double ema, const double rsi, const bool green, const bool red,
               const double point, const int prevConsecAbove, const int prevConsecBelow,
               const double nearEmaTolPts)
{
   string panel = "";
   panel += "========================================\n";
   panel += "  FXMOPOL_v2  |  " + _Symbol + " " + EnumToString(_Period) + "\n";
   panel += "========================================\n";
   panel += "  TRADING: " + ((InpAllowTrading) ? "ON" : "OFF (InpAllowTrading=false)") + "\n";
   panel += "----------------------------------------\n";
   panel += "  MARKET\n";
   panel += "    Close= " + DoubleToString(c, _Digits) + "  EMA21= " + DoubleToString(ema, _Digits) + "\n";
   panel += "    RSI9=  " + DoubleToString(rsi, 1) + "  Candle= " + ((green) ? "GREEN" : (red ? "RED" : "DOJI")) + "\n";
   panel += "    Consec Above EMA21= " + IntegerToString(g_consecAbove) + "   Below= " + IntegerToString(g_consecBelow) + "\n";
   panel += "----------------------------------------\n";
   panel += "  POSITIONS\n";
   panel += "    Shorts= " + IntegerToString(g_shortCount) + " / " + IntegerToString(InpMaxPositions) + "\n";
   panel += "    Longs=  " + IntegerToString(g_longCount)  + " / " + IntegerToString(InpMaxPositions) + "\n";
   panel += "----------------------------------------\n";
   panel += "  WAITING FOR / ACTIVE RULES\n";

   if(InpAllowTrading)
   {
      if(!g_hasShort && g_shortCount < InpMaxPositions)
      {
         bool s1_cond = (InpEnableShort1 && prevConsecAbove >= InpMinConsecutive && red && c < ema && rsi > 22.0 && rsi < 55.0);
         bool s2_cond = (InpEnableShort2 && prevConsecBelow >= InpMinConsecutive && green &&
                         c >= ema - nearEmaTolPts && c <= ema &&
                         rsi > 22.0 && rsi < 55.0);
         bool s3_cond = (InpEnableShort3 && prevConsecBelow >= InpMinConsecutive && red && c <= ema);
         bool s4_cond = (InpEnableShort4 && c > o && c < ema);
          bool s5_cond = (InpEnableShort5 && !g_hasShort && prevConsecBelow >= InpMinConsecutive && c < ema && rsi > 22.0 &&
                          c >= ema - nearEmaTolPts && c <= ema + nearEmaTolPts);
         bool rs_cond = (InpEnableReflexShort && !g_hasShort && g_shortCount < InpMaxPositions && red && (c < ema) &&
                         (g_clBuf[1] > g_opBuf[1]) && (g_clBuf[1] > g_ema21Buf[1]) &&
                         (g_clBuf[2] < g_ema21Buf[2]) && (g_clBuf[3] < g_ema21Buf[3]));
         panel += "    SHORT1: " + ((s1_cond) ? "TRIGGERED" : "wait: prev consecAbove>=N+red+belowEMA+RSI22-55") + "\n";
         panel += "    SHORT2: " + ((s2_cond) ? "TRIGGERED" : "wait: prev below+greenNearEMA+RSI22-55") + "\n";
         panel += "    SHORT3: " + ((s3_cond) ? "TRIGGERED" : "wait: prev below+redEngulfsPrevGreen+at/belowEMA") + "\n";
         panel += "    SHORT4: " + ((s4_cond) ? "TRIGGERED" : "wait: 3greenUnderEMA21") + "\n";
         panel += "    SHORT5: " + ((s5_cond) ? "TRIGGERED" : "wait: belowEMA+nearEMA+RSI>22") + "\n";
         panel += "    REFLEX SHORT: " + ((rs_cond) ? "TRIGGERED" : "wait: 2below+greenAbove+redBackBelow") + "\n";
      }
      else
      {
         panel += "    Short side: position active or max reached\n";
      }

      if(!g_hasLong && g_longCount < InpMaxPositions)
      {
         bool l1_cond = (InpEnableLong1 && prevConsecBelow >= InpMinConsecutive && green && c > ema && rsi > 45.0 && rsi < 78.0);
         bool l2_cond = (InpEnableLong2 && prevConsecAbove >= InpMinConsecutive && red &&
                         c >= ema && c <= ema + nearEmaTolPts &&
                         rsi > 45.0 && rsi < 78.0);
         bool l3_cond = (InpEnableLong3 && prevConsecAbove >= InpMinConsecutive && green && c >= ema);
         bool l4_cond = (InpEnableLong4 && c < o && c > ema);
          bool l5_cond = (InpEnableLong5 && !g_hasLong && prevConsecAbove >= InpMinConsecutive && c > ema && rsi < 78.0 &&
                          c >= ema - nearEmaTolPts && c <= ema + nearEmaTolPts);
         bool rl_cond = (InpEnableReflexLong && !g_hasLong && g_longCount < InpMaxPositions && green && (c > ema) &&
                         (g_clBuf[1] < g_opBuf[1]) && (g_clBuf[1] < g_ema21Buf[1]) &&
                         (g_clBuf[2] > g_ema21Buf[2]) && (g_clBuf[3] > g_ema21Buf[3]));

         panel += "    LONG1:  " + ((l1_cond) ? "TRIGGERED" : "wait: prev consecBelow>=N+green+aboveEMA+RSI45-78") + "\n";
         panel += "    LONG2:  " + ((l2_cond) ? "TRIGGERED" : "wait: prev above+redNearEMA+RSI45-78") + "\n";
         panel += "    LONG3:  " + ((l3_cond) ? "TRIGGERED" : "wait: prev above+greenEngulfsPrevRed+at/aboveEMA") + "\n";
         panel += "    LONG4:  " + ((l4_cond) ? "TRIGGERED" : "wait: 3redAboveEMA21") + "\n";
         panel += "    LONG5:  " + ((l5_cond) ? "TRIGGERED" : "wait: aboveEMA+nearEMA+RSI<78") + "\n";
         panel += "    REFLEX LONG: " + ((rl_cond) ? "TRIGGERED" : "wait: 2above+redBelow+greenBackAbove") + "\n";
      }
      else
      {
         panel += "    Long side: position active or max reached\n";
      }
   }
   else
   {
      panel += "    Set InpAllowTrading=true to enable entries\n";
   }

    panel += "----------------------------------------\n";
    panel += "  FRACTALS\n";
    panel += "    Up fractal high=  " + ((g_fractalHigh > 0) ? DoubleToString(g_fractalHigh, _Digits) : "none") + "\n";
    panel += "    Down fractal low= " + ((g_fractalLow > 0)  ? DoubleToString(g_fractalLow,  _Digits) : "none") + "\n";
    panel += "----------------------------------------\n";
    panel += "  MARKET STRUCTURE (filter=" + ((InpEnableStructureFilter) ? "ON" : "OFF") + ")\n";
    panel += "    Long structure:  " + ((g_structureIntactLong)  ? "INTACT" : "BROKEN") + "  (redOpenHigh=" + ((g_redOpenHigh > 0) ? DoubleToString(g_redOpenHigh, _Digits) : "none") + ")\n";
    panel += "    Short structure: " + ((g_structureIntactShort) ? "INTACT" : "BROKEN") + "  (greenCloseLow=" + ((g_greenCloseLow > 0) ? DoubleToString(g_greenCloseLow, _Digits) : "none") + ")\n";
    panel += "----------------------------------------\n";
    panel += "  RSI BAND MODE (" + ((InpUseRsiBandMode) ? "ON" : "OFF") + ")\n";
    bool panelEmaSlopeUp   = (g_ema21Prev > 0.0 && ema > g_ema21Prev);
    bool panelEmaSlopeDown = (g_ema21Prev > 0.0 && ema < g_ema21Prev);
    panel += "    EMA21 slope: " + ((panelEmaSlopeUp) ? "UP" : (panelEmaSlopeDown) ? "DOWN" : "FLAT") + "  RSI prev= " + DoubleToString(g_rsiPrev, 1) + "\n";
    panel += "    RSI band short: " + ((g_rsiBandInShort) ? "IN" : "OUT") + "  barsHeld= " + IntegerToString(g_rsiBandBarsHeldShort) + "\n";
    panel += "    RSI band long:  " + ((g_rsiBandInLong)  ? "IN" : "OUT") + "  barsHeld= " + IntegerToString(g_rsiBandBarsHeldLong) + "\n";
    panel += "    RSI mid= " + IntegerToString(InpRsiBandMid) + "  hi= " + IntegerToString(InpRsiBandHi) + "  lo= " + IntegerToString(InpRsiBandLo) + "  margin= " + DoubleToString(InpRsiBandMargin, 1) + "\n";
    panel += "----------------------------------------\n";
    panel += "  EXITS\n";
    panel += "    EXIT1: trend reversal candle past EMA21 -> close all\n";
    panel += "    EXIT2: weak structure (no lower lows/higher highs) -> close all\n";
    if(InpEnableStructureFilter)
       panel += "    STRUCT BREAK: candle fails structure reclaim/break -> close all\n";
    if(InpUseRsiBandMode)
       panel += "    RSI EXIT: short@lo/" + IntegerToString(InpRsiBandLo) + " long@hi/" + IntegerToString(InpRsiBandHi) + " (minHold=" + IntegerToString(InpRsiBandMinHoldBars) + ")\n";
    panel += "========================================\n";

   Comment(panel);
}

//+------------------------------------------------------------------+
//| UpdateFractals — detect most recent up/down fractal (5-bar)        |
//+------------------------------------------------------------------+
void UpdateFractals()
{
   double hiBuf[10];
   double loBuf[10];
   if(CopyHigh(_Symbol, _Period, 1, 10, hiBuf) < 10) return;
   if(CopyLow (_Symbol, _Period, 1, 10, loBuf) < 10) return;

   g_fractalHigh = 0.0;
   g_fractalLow  = 0.0;

   for(int i = 2; i <= 6; i++)
   {
      bool upFractal = (hiBuf[i] > hiBuf[i-2] && hiBuf[i] > hiBuf[i-1] &&
                        hiBuf[i] > hiBuf[i+1] && hiBuf[i] > hiBuf[i+2]);
      bool dnFractal = (loBuf[i] < loBuf[i-2] && loBuf[i] < loBuf[i-1] &&
                        loBuf[i] < loBuf[i+1] && loBuf[i] < loBuf[i+2]);

      if(upFractal)   g_fractalHigh = hiBuf[i];
      if(dnFractal)   g_fractalLow  = loBuf[i];
   }
}

//+------------------------------------------------------------------+
//| UpdateFractalTrailingStop — move SL to fractal levels only if it   |
//--- improves (shorts: SL up; longs: SL down)                         |
//+------------------------------------------------------------------+
void UpdateFractalTrailingStop()
{
   if(!InpAllowTrading) return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk <= 0) continue;
      if(!PositionSelectByTicket(tk)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      long type = PositionGetInteger(POSITION_TYPE);
      double sl = PositionGetDouble(POSITION_SL);
      double px = PositionGetDouble(POSITION_PRICE_OPEN);

      if(type == POSITION_TYPE_SELL)
      {
         double newSL = sl;
         if(g_fractalHigh > 0.0 && g_fractalHigh > sl && g_fractalHigh < px)
            newSL = g_fractalHigh;
         else if(g_swingHigh > 0.0 && g_swingHigh > sl && g_swingHigh < px)
            newSL = g_swingHigh;

         if(newSL > sl)
         {
            if(!trade.PositionModify(tk, newSL, PositionGetDouble(POSITION_TP)))
               Print("fxmopol_v2: failed to modify short SL ticket=%I64u err=%d", (long)tk, GetLastError());
            else
               Print("fxmopol_v2: short SL trailed up ticket=%I64u oldSL=%.5f newSL=%.5f", (long)tk, sl, newSL);
         }
      }
      else if(type == POSITION_TYPE_BUY)
      {
         double newSL = sl;
         if(g_fractalLow > 0.0 && g_fractalLow < sl && g_fractalLow > px)
            newSL = g_fractalLow;
         else if(g_swingLow > 0.0 && g_swingLow < sl && g_swingLow > px)
            newSL = g_swingLow;

         if(newSL < sl)
         {
            if(!trade.PositionModify(tk, newSL, PositionGetDouble(POSITION_TP)))
               Print("fxmopol_v2: failed to modify long SL ticket=%I64u err=%d", (long)tk, GetLastError());
            else
               Print("fxmopol_v2: long SL trailed down ticket=%I64u oldSL=%.5f newSL=%.5f", (long)tk, sl, newSL);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| UpdateSwingHighLow — track recent swing high/low over 20 bars    |
//+------------------------------------------------------------------+
void UpdateSwingHighLow()
{
   double hiBuf[20];
   double loBuf[20];
   if(CopyHigh(_Symbol, _Period, 1, 20, hiBuf) < 20) return;
   if(CopyLow (_Symbol, _Period, 1, 20, loBuf) < 20) return;

    g_swingHigh = hiBuf[0];
    g_swingLow  = loBuf[0];

    for(int i = 0; i < 20; i++)
    {
       if(hiBuf[i] > g_swingHigh) { g_swingHigh = hiBuf[i]; g_swingHighTime = iTime(_Symbol, _Period, i); }
       if(loBuf[i] < g_swingLow)  { g_swingLow  = loBuf[i]; g_swingLowTime  = iTime(_Symbol, _Period, i); }
    }
}

//+------------------------------------------------------------------+
//| UpdateMarketStructure — causal market structure tracker           |
//| Uptrend (long side): green close must reclaim above prior red opens above EMA21 |
//| Downtrend (short side): red close must break below prior green closes below EMA21 |
//+------------------------------------------------------------------+
void UpdateMarketStructure(const double closePx, const double openPx, const double ema21)
{
   if(!InpEnableStructureFilter) return;

   datetime curBarTime = iTime(_Symbol, _Period, 0);
   if(curBarTime <= 0 || curBarTime == g_structLastBarTime) return;
   g_structLastBarTime = curBarTime;

   bool isGreen = (closePx > openPx);
   bool isRed   = (closePx < openPx);
   bool aboveEma21 = (closePx > ema21);
   bool belowEma21 = (closePx < ema21);

   //--- Uptrend structure (price above EMA21): green must close above prior red opens
   if(aboveEma21)
   {
      if(isRed)
      {
         if(g_redOpenHigh == 0.0 || openPx > g_redOpenHigh)
            g_redOpenHigh = openPx;
         g_structureIntactLong = false;   // red appeared, waiting for green reclaim
      }
      else if(isGreen)
      {
         if(g_redOpenHigh > 0.0 && closePx > g_redOpenHigh)
         {
            g_structureIntactLong = true; // green reclaimed above all red opens
            g_redOpenHigh = 0.0;          // reset after successful reclaim
         }
         else if(g_redOpenHigh > 0.0 && closePx <= g_redOpenHigh)
         {
            g_structureIntactLong = false; // green failed to reclaim → sellers stepping in
         }
         else
         {
            g_structureIntactLong = true; // no prior red to reclaim
         }
      }
   }
   else
   {
      g_redOpenHigh = 0.0;
      g_structureIntactLong = false;
   }

   //--- Downtrend structure (price below EMA21): red must close below prior green closes
   if(belowEma21)
   {
      if(isGreen)
      {
         if(g_greenCloseLow == 0.0 || closePx < g_greenCloseLow)
            g_greenCloseLow = closePx;
         g_structureIntactShort = false;  // green appeared, waiting for red break
      }
      else if(isRed)
      {
         if(g_greenCloseLow > 0.0 && closePx < g_greenCloseLow)
         {
            g_structureIntactShort = true; // red broke below all green closes
            g_greenCloseLow = 0.0;        // reset after successful break
         }
         else if(g_greenCloseLow > 0.0 && closePx >= g_greenCloseLow)
         {
            g_structureIntactShort = false; // red failed to break → buyers stepping in
         }
         else
         {
            g_structureIntactShort = true; // no prior green to break
         }
      }
   }
   else
   {
      g_greenCloseLow = 0.0;
      g_structureIntactShort = false;
   }
}

//+------------------------------------------------------------------+
//| CountOpenPositions — count open positions by side for this symbol|
//+------------------------------------------------------------------+
void CountOpenPositions()
{
   g_shortCount = 0;
   g_longCount  = 0;
   g_hasShort   = false;
   g_hasLong    = false;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk <= 0) continue;
      if(!PositionSelectByTicket(tk)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      long type = PositionGetInteger(POSITION_TYPE);
      if(type == POSITION_TYPE_SELL) { g_shortCount++; g_hasShort = true; }
      else if(type == POSITION_TYPE_BUY) { g_longCount++; g_hasLong = true; }
   }
}

//+------------------------------------------------------------------+
//| CloseAllShorts — close all sell positions on this symbol         |
//+------------------------------------------------------------------+
void CloseAllShorts()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk <= 0) continue;
      if(!PositionSelectByTicket(tk)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_SELL)
         trade.PositionClose(tk);
   }
   g_shortCount = 0;
   g_hasShort   = false;
   g_rsiBandInShort = false;
   g_rsiBandBarsHeldShort = 0;
}

//+------------------------------------------------------------------+
//| CloseAllLongs — close all buy positions on this symbol           |
//+------------------------------------------------------------------+
void CloseAllLongs()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk <= 0) continue;
      if(!PositionSelectByTicket(tk)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
         trade.PositionClose(tk);
   }
   g_longCount = 0;
   g_hasLong   = false;
   g_rsiBandInLong = false;
   g_rsiBandBarsHeldLong = 0;
}

//+------------------------------------------------------------------+
//| CalcLots — risk-based lot sizing                                 |
//+------------------------------------------------------------------+
double CalcLots(double equity, double slDist, double tickSize, double tickValue)
{
   double riskAmt = equity * (InpRiskPct / 100.0);
   if(slDist <= 0) slDist = 20 * SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double lots = riskAmt / ((slDist / tickSize) * tickValue);
   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   lots = MathFloor(lots / lotStep) * lotStep;
   lots = MathMax(minLot, MathMin(maxLot, lots));
   return lots;
}

//+------------------------------------------------------------------+
//| Expert deinit function                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   ObjectsDeleteAll(0, "FXMOPOL_");
   ObjectsDeleteAll(0, "FXMOPOL_MA_");
   Comment("");   // clear panel from chart
   if(g_ema21Handle != INVALID_HANDLE) IndicatorRelease(g_ema21Handle);
   if(g_rsiHandle   != INVALID_HANDLE) IndicatorRelease(g_rsiHandle);
   g_errorLog.Deinit();
   Print("fxmopol_v2 deinitialized.");
}
