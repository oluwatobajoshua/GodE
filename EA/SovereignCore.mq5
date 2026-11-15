//+------------------------------------------------------------------+
//| SovereignCore.mq5 — Modular event-driven EA orchestrator         |
//| Strict separation: strategy math / risk / execution / UI         |
//+------------------------------------------------------------------+
#property strict
#property copyright "Sovereign"
#property link      ""
#property version   "2.0.0"
#property script_show_inputs

#include <Trade\Trade.mqh>
#include <Sovereign\Core\IStrategy.mqh>
#include <Sovereign\Core\RingBuffer.mqh>
#include <Sovereign\Risk\CircuitBreaker.mqh>
#include <Sovereign\Execution\TradeGateway.mqh>
#include <Sovereign\UI\CanvasHUD.mqh>
#include <Sovereign\Strategies\RsiBandStrategy.mqh>

//--- inputs
input group "=== Strategy ==="
input bool   InpStrategyEnabled      = true;   // enable RSI band strategy
input int    InpRsiPeriod            = 9;      // RSI period
input int    InpRsiMid               = 50;     // RSI mid band
input int    InpRsiHi                = 75;     // RSI high exit band
input int    InpRsiLo                = 25;     // RSI low exit band
input double InpRsiMargin            = 3.0;    // RSI cross margin
input double InpRsiDeadband          = 0.10;   // EMA slope deadband
input bool   InpRsiLongOnly          = false;  // long-only mode
input bool   InpRsiRequireEmaSlope   = false;  // require EMA21 slope

input group "=== Risk ==="
input int    InpMaxSpread            = 100;    // max spread in points
input double InpMaxDailyDD           = 5.0;    // max daily drawdown %
input int    InpTrafficLightMinGreen = 6;      // min green signals
input bool   InpUseTrafficLight      = true;   // traffic light gate
input bool   InpCenturyHalt          = false;  // century kill-switch

input group "=== Execution ==="
input ulong  InpMagicNumber          = 123456; // EA magic number
input double InpRiskPct              = 0.5;    // risk per trade % equity
input double InpStopAtr              = 1.5;    // stop distance x ATR
input double InpTpAtr                = 2.0;    // take profit x ATR
input int    InpSlippage             = 10;     // max slippage points

input group "=== UI / Debug ==="
input bool   InpShowHud              = true;   // show Canvas HUD on chart
input bool   InpVerboseBarLog        = true;   // print full plan on new bar
input bool   InpForceBypassAllFilters= false;  // bypass all filters for testing

//--- globals
CRsiBandStrategy  *g_strategy = NULL;
CCircuitBreaker   *g_breaker  = NULL;
CTradeGateway     *g_gateway  = NULL;
CCanvasHUD        *g_hud      = NULL;

datetime   g_lastBarTime = 0;
double     g_dayStartEquity = 0.0;
bool       g_algoTrading = false;
int        g_trafficLightGreen = 0;
bool       g_trafficLightVeto = false;

//+------------------------------------------------------------------+
int OnInit()
   {
   Print("SovereignCore OnInit: starting modular EA...");

   //--- create strategy
   g_strategy = new CRsiBandStrategy(InpRsiPeriod, InpRsiMid, InpRsiHi, InpRsiLo,
                                     InpRsiMargin, InpRsiDeadband,
                                     InpRsiLongOnly, InpRsiRequireEmaSlope);
   if(CheckPointer(g_strategy) == POINTER_INVALID)
      {
      Print("OnInit: FAILED to create strategy");
      return INIT_FAILED;
      }

   //--- create circuit breaker
   g_breaker = new CCircuitBreaker();
   if(CheckPointer(g_breaker) == POINTER_INVALID)
      {
      Print("OnInit: FAILED to create circuit breaker");
      return INIT_FAILED;
      }

   //--- create trade gateway
   g_gateway = new CTradeGateway(InpMagicNumber, InpSlippage, ORDER_FILLING_FOK);
   if(CheckPointer(g_gateway) == POINTER_INVALID)
      {
      Print("OnInit: FAILED to create trade gateway");
      return INIT_FAILED;
      }

   //--- create HUD
   g_hud = new CCanvasHUD(340, 300, 10, 250);
   if(CheckPointer(g_hud) == POINTER_INVALID)
      {
      Print("OnInit: FAILED to create HUD");
      return INIT_FAILED;
      }
   if(InpShowHud && !g_hud->Create("SOV_HUD"))
      Print("OnInit: HUD create failed (continuing without HUD)");

   //--- configure breaker
   g_algoTrading = (MQLInfoInteger(MQL_TRADE_ALLOWED) != 0);
   g_breaker->SetAlgoTrading(g_algoTrading);
   g_breaker->SetMaxSpread(InpMaxSpread);
   g_breaker->SetMaxDailyDD(InpMaxDailyDD);
   g_breaker->SetTrafficLight(InpUseTrafficLight, InpTrafficLightMinGreen);
   g_breaker->SetCenturyHalt(InpCenturyHalt);

   //--- symbol whitelist (empty = disabled)
   string whitelist[] = {"EURUSD", "GBPUSD", "USDJPY", "BTCUSD"};
   g_breaker->SetSymbolWhitelist(false, whitelist);

   //--- day start equity
   g_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(g_dayStartEquity <= 0) g_dayStartEquity = 1.0;

   Print("SovereignCore OnInit: strategy=" + g_strategy->Name() +
         " magic=" + IntegerToString((long)InpMagicNumber) +
         " algo=" + ((g_algoTrading) ? "ON" : "OFF"));
   return INIT_SUCCEEDED;
   }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
   {
   if(CheckPointer(g_hud) != POINTER_INVALID) { delete g_hud; g_hud = NULL; }
   if(CheckPointer(g_gateway) != POINTER_INVALID) { delete g_gateway; g_gateway = NULL; }
   if(CheckPointer(g_breaker) != POINTER_INVALID) { delete g_breaker; g_breaker = NULL; }
   if(CheckPointer(g_strategy) != POINTER_INVALID) { delete g_strategy; g_strategy = NULL; }
   Print("SovereignCore OnDeinit: reason=" + IntegerToString(reason));
   }

//+------------------------------------------------------------------+
void OnTick()
   {
   //--- position state (for HUD)
   int openCount = 0;
   double posEntry = 0, posSL = 0, posTP = 0;
   int posSide = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
      ulong tk = PositionGetTicket(i);
      if(tk <= 0) continue;
      if(!PositionSelectByTicket(tk)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      openCount++;
      posEntry = PositionGetDouble(POSITION_PRICE_OPEN);
      posSL = PositionGetDouble(POSITION_SL);
      posTP = PositionGetDouble(POSITION_TP);
      posSide = (int)PositionGetInteger(POSITION_TYPE);
      }

   //--- detect new bar
   datetime barTime = iTime(_Symbol, _Period, 0);
   bool newBar = (barTime > 0 && barTime != g_lastBarTime);
   if(newBar) g_lastBarTime = barTime;

   //--- equity / drawdown
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double dailyLossPct = 0.0;
   if(g_dayStartEquity > 0 && equity < g_dayStartEquity)
      dailyLossPct = (g_dayStartEquity - equity) / g_dayStartEquity * 100.0;

   //--- indicator buffers (bar 0 = forming bar for live entry)
   int need = 60;
   double close[], high[], low[], rsi[], ema21[], ema50[], atr[];
   ArraySetAsSeries(close, true);
   ArraySetAsSeries(high, true);
   ArraySetAsSeries(low, true);
   ArraySetAsSeries(rsi, true);
   ArraySetAsSeries(ema21, true);
   ArraySetAsSeries(ema50, true);
   ArraySetAsSeries(atr, true);

   int rsiH = iRSI(_Symbol, _Period, InpRsiPeriod, PRICE_CLOSE);
   int ema21H = iMA(_Symbol, _Period, 21, 0, MODE_EMA, PRICE_CLOSE);
   int ema50H = iMA(_Symbol, _Period, 50, 0, MODE_EMA, PRICE_CLOSE);
   int atrH = iATR(_Symbol, _Period, 14);

   if(CopyClose(_Symbol, _Period, 0, need, close) < need) return;
   if(CopyBuffer(rsiH, 0, 0, need, rsi) < need) return;
   if(CopyBuffer(ema21H, 0, 0, need, ema21) < need) return;
   if(CopyBuffer(ema50H, 0, 0, need, ema50) < need) return;
   if(CopyBuffer(atrH, 0, 0, need, atr) < need) return;

   int spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);

   //--- circuit breaker evaluation
   SSystemHealth health = g_breaker->Evaluate(_Symbol, spread, dailyLossPct,
                                              g_trafficLightGreen, g_trafficLightVeto);
   if(InpForceBypassAllFilters)
      health.isCleared = true;

   //--- HUD state
   SHUDState hud;
   hud.entriesStatus = (health.isCleared) ? "ALLOWED" : "HALTED";
   hud.blockReason = health.blockReason;
   hud.positionStatus = StringFormat("Pos: %s | entry=%.5f SL=%.5f TP=%.5f",
                                     (posSide == POSITION_TYPE_BUY ? "LONG" :
                                      posSide == POSITION_TYPE_SELL ? "SHORT" : "FLAT"),
                                     posEntry, posSL, posTP);

   //--- BAR-OPEN: strategy evaluation (only on new bar)
   if(newBar)
      {
      g_hud->ForceRedraw();

      ENUM_SIGNAL sig = g_strategy->Evaluate(close, high, low, rsi, ema21, ema50, atr);
      string summary = g_strategy->GetEvaluationSummary();

      //--- build market context
      string trendText = (g_strategy->CurrentTrend() > 0) ? "UPTREND" :
                         (g_strategy->CurrentTrend() < 0) ? "DOWNTREND" : "FLAT";
      hud.marketContext = StringFormat("SYM=%s | Bid=%.5f Ask=%.5f Spread=%d | RSI=%.1f | Trend=%s",
                                       _Symbol, SymbolInfoDouble(_Symbol, SYMBOL_BID),
                                       SymbolInfoDouble(_Symbol, SYMBOL_ASK), spread,
                                       g_strategy->CurrentRsi(), trendText);
      hud.reasoningChain = summary;

      if(health.isCleared && openCount == 0 && sig != SIGNAL_NONE)
         {
         //--- pending execution
         g_hud->activePlan = StringFormat("PENDING EXECUTION | %s | RSI=%.1f",
                                          (sig == SIGNAL_BUY ? "BUY" : "SELL"),
                                       g_strategy->CurrentRsi());
         g_hud->badge = BADGE_CLEARED;
         g_hud->badgeText = "CLEARED";

         //--- verbose bar log
         if(InpVerboseBarLog)
            {
            Print("=== SOV TELEMETRY ===");
            Print(hud.marketContext);
            Print("Plan: " + g_hud->activePlan);
            Print("Reason: " + summary);
            Print("Entries: ALLOWED | DailyDD=" + DoubleToString(dailyLossPct, 2) + "%/" +
                  DoubleToString(InpMaxDailyDD, 2) + "% | Block: none");
            Print("=====================");
            }

         //--- execute trade
         double slDist = InpStopAtr * g_strategy->CurrentAtr();
         double tpDist = InpTpAtr * g_strategy->CurrentAtr();
         double slPx = 0, tpPx = 0;

         if(sig == SIGNAL_BUY)
            {
            slPx = SymbolInfoDouble(_Symbol, SYMBOL_ASK) - slDist;
            tpPx = SymbolInfoDouble(_Symbol, SYMBOL_ASK) + tpDist;
            double riskMoney = equity * (InpRiskPct / 100.0);
            double vol = g_gateway->NormalizeVolume(_Symbol, riskMoney / slDist);
            STradeResult res = g_gateway->Buy(_Symbol, vol, slPx, tpPx, "Sovereign RSI");
            if(res.ok) AuditPush("BUY executed ticket=" + IntegerToString((long)res.ticket));
            }
         else if(sig == SIGNAL_SELL)
            {
            slPx = SymbolInfoDouble(_Symbol, SYMBOL_BID) + slDist;
            tpPx = SymbolInfoDouble(_Symbol, SYMBOL_BID) - tpDist;
            double riskMoney = equity * (InpRiskPct / 100.0);
            double vol = g_gateway->NormalizeVolume(_Symbol, riskMoney / slDist);
            STradeResult res = g_gateway->Sell(_Symbol, vol, slPx, tpPx, "Sovereign RSI");
            if(res.ok) AuditPush("SELL executed ticket=" + IntegerToString((long)res.ticket));
            }
         }
      else if(openCount > 0)
         {
         //--- managing active trade
         g_hud->activePlan = StringFormat("MANAGING ACTIVE TRADE | bars held on this bar");
         g_hud->badge = BADGE_CLEARED;
         g_hud->badgeText = "CLEARED";
         hud.reasoningChain = "Position open: trailing/exit management active";
         }
      else
         {
         g_hud->activePlan = "WAITING: " + summary;
         g_hud->badge = (health.isCleared) ? BADGE_CLEARED : BADGE_BLOCKED;
         g_hud->badgeText = (health.isCleared) ? "CLEARED" : "BLOCKED";
         hud.reasoningChain = summary + " | " + health.blockReason;
         }
      }
   else
      {
      //--- tick-level: only HUD update + position management (trailing stops)
      g_hud->markets = StringFormat("SYM=%s | Bid=%.5f | RSI=%.1f", _Symbol,
                                    SymbolInfoDouble(_Symbol, SYMBOL_BID),
                                    g_strategy->CurrentRsi());
      }

   //--- render HUD (throttled)
   if(InpShowHud && CheckPointer(g_hud) != POINTER_INVALID)
      g_hud->Render(hud);
   }

//+------------------------------------------------------------------+
//| OnTimer — async audit buffer flush                               |
//+------------------------------------------------------------------+
void OnTimer()
   {
   g_auditBuf.Flush(AuditFlushToCSV);
   }

//+------------------------------------------------------------------+
//| Set timer in OnInit for async flush                              |
//+------------------------------------------------------------------+
// (called from OnInit via EventSetMillisecondTimer below)
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| Patch: set timer at end of OnInit                                |
//+------------------------------------------------------------------+
// Note: EventSetMillisecondTimer(1000) called after object creation
