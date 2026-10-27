//+------------------------------------------------------------------+
//|                                     SovereignGodsEyeEA.mq5       |
//|                               Copyright 2026, Quant Systems      |
//|             Ultimate Sovereign Institutional Execution Engine    |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026"
#property link      "https://www.mql5.com"
#property version   "10.00"
#property strict

#include <Trade\Trade.mqh>
#include <Sovereign\SovereignSignalClient.mqh>
#include <Sovereign\SovereignAuditLog.mqh>
#include <Sovereign\SovereignPerformance.mqh>
#include <Sovereign\SovereignMLModel.mqh>
#include <Sovereign\SovereignTrafficLight.mqh>
CTrade trade;

//--- Sovereign constants
#define SOVEREIGN_MAGIC 20261103
#define SOVEREIGN_ATR_PERIOD 14

//--- Telemetry and audit services
CSovereignSignalClient g_python;
CSovereignAuditLog     g_audit;

//--- Entry engine selection
enum ENUM_SOVEREIGN_ENTRY_MODE
{
   ENTRY_MEAN_REVERSION = 0,  // Fade outer range extremes
   ENTRY_BREAKOUT       = 1   // Trade closed-bar range breakouts
};

//--- Input Parameters ---
input group "=== Risk & Capital Protection ==="
input double InpBaseRiskPercent     = 1.0;       // Base risk per trade (% of equity)
input double InpMaxDailyDD          = 5.0;       // Max daily drawdown limit (%)
input int    InpMaxSpread           = 12;        // Max allowable spread in points
input int    InpMaxSlippage         = 3;         // Max allowed slippage in points

input group "=== Institutional Killzones (UTC Normalized) ==="
input int    InpKillzoneStart       = 7;         // London/NY Killzone Start Hour (UTC)
input int    InpKillzoneEnd         = 16;        // Killzone End Hour (UTC)

input group "=== Session Playbook (UTC) ==="
input bool   InpUseSessionPlaybook   = true;     // Enforce per-engine entry hours
input int    InpBreakoutSessionStart = 7;        // Breakout start hour (UTC)
input int    InpBreakoutSessionEnd   = 7;        // Breakout end hour (UTC)
input int    InpMeanRevSessionStart  = 7;        // Mean-reversion start hour (UTC)
input int    InpMeanRevSessionEnd    = 16;       // Mean-reversion end hour (UTC)
input int    InpServerGMTOffset      = 0;        // Broker server GMT offset (hours; 0=UTC)
input bool   InpAutoServerOffset     = true;     // Live: refresh offset from TimeGMT hourly

input group "=== Pre-Trade Traffic Light Board ==="
input bool   InpUseTrafficLight        = true;   // Evaluate the pre-trade board
input bool   InpTrafficLightLog        = true;   // Write traffic_light.csv
input int    InpTrafficLightGateMode   = 1;      // 0=off, 1=observe, 2=soft gate, 3=all green
input int    InpTrafficLightMinGreen   = 6;      // Minimum green lights for soft gate
input bool   InpTrafficLightRiskScale  = true;   // Scale risk by board quality
input bool   InpTrafficLightUseMacro   = true;   // Read DXYUSD/VIXUSD macro lights
input bool   InpTrafficLightUsePattern = true;   // Include candle-pattern light

input group "=== Structure & Multi-Timeframe Confluence ==="
input ENUM_SOVEREIGN_ENTRY_MODE InpEntryMode = ENTRY_MEAN_REVERSION; // Entry engine
input int    InpStructuralLookback  = 50;        // Lookback bars for dealing range high/low
input double InpExtremeThreshold    = 0.20;      // Outer 20% boundary (Premium/Discount)
input bool   InpUseHTFConfluence    = true;      // Require H1 Market Structure Alignment
input bool   InpUseMacroTrend       = true;      // Require H1 close to agree with H1 SMA200

input group "=== Advanced Execution Management ==="
input bool   InpEnablePartialClose  = true;      // Enable scaling out 50% at InpPartialCloseR
input double InpPartialCloseR       = 1.5;       // Partial-close trigger (R multiple)
input bool   InpEnableBreakEven     = true;      // Move stop to break-even after InpBreakEvenR
input double InpBreakEvenR          = 1.5;       // Break-even trigger (R multiple)
input double InpSLATRMultiple       = 1.5;       // Stop-loss distance (ATR multiple)
input double InpTPRMultiple         = 3.0;       // Take-profit distance (R multiple)
input double InpEntryBufferATR      = 0.35;      // Limit-order pullback (ATR fraction)
input int    InpPendingExpiryMinutes = 30;       // Cancel unfilled pending orders after N minutes
input int    InpNewsBufferMinutes   = 30;        // Minutes to pause before/after high-impact news

input group "=== Python Telemetry & Audit ==="
input bool   InpEnableTelemetry        = true;   // Connect to the Python TCP bridge
input string InpPythonHost             = "127.0.0.1";
input int    InpPythonPort             = 5556;   // Python TCP bridge port (ZMQ stays on 5555)
input bool   InpRequirePythonSignal    = false;  // Require Python agreement when link healthy
input double InpMinSignalConfidence    = 0.60;   // Minimum Python confidence to allow a trade
input int    InpHeartbeatTimeoutSec     = 3;     // Link considered stale after N seconds
input bool   InpEnableAuditLog         = true;   // Write CSV execution/slippage audit logs

input group "=== Adaptive Performance Governor ==="
input bool   InpEnablePerformanceGovernor = true;  // Monitor closed trades and adapt risk by bucket
input bool   InpPerformanceReset       = false; // Delete saved governor state at startup
input double InpGovernorDDStart        = 3.0;   // Start reducing risk at this total drawdown (%)
input double InpGovernorDDLimit        = 12.0;  // Stop new entries at this total drawdown (%)
input int    InpGovernorLossStreak     = 3;     // Consecutive losses before risk is halved

input group "=== Optional Neural Filter ==="
input bool   InpUseMLFilter            = false; // Veto setups below the exported MLP probability
input double InpMLThreshold            = 0.55; // Minimum neural probability to allow entry
input bool   InpMLFailOpen             = false; // Allow trade if ML features cannot be built

//--- Global Tracking ---
double startingEquity;
datetime lastResetDate;
int serverTimeOffsetHours = 0;
bool isZmqConnected = false;

//--- Cached indicator handles (created once in OnInit)
int h1Sma20Handle = INVALID_HANDLE;
int h1Sma50Handle = INVALID_HANDLE;
int h1Sma200Handle = INVALID_HANDLE;
int m5Sma240Handle = INVALID_HANDLE;
int m5Sma600Handle = INVALID_HANDLE;
int m5Sma2400Handle = INVALID_HANDLE;
int atrHandle     = INVALID_HANDLE;

//--- Neural-filter handles (only created when InpUseMLFilter is enabled)
int h1AdxHandle      = INVALID_HANDLE;
int h1AtrHandle      = INVALID_HANDLE;
int m5Ema20Handle    = INVALID_HANDLE;
int m5Rsi2Handle     = INVALID_HANDLE;
int d1Sma200Handle   = INVALID_HANDLE;
bool g_mlReady       = false;

//--- Adaptive governor and shared runtime state
CSovereignPerformance g_perf;
CSovereignTrafficLight g_traffic;
double g_atrRatio = 1.0;

//--- H1 trend: 1 = bullish, -1 = bearish, 0 = no clear trend
int g_htfTrend = 0;

//--- Per-ticket management flags (prevent repeated partial closes / SL edits)
ulong g_partialClosedTickets[];
ulong g_breakEvenTickets[];

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   startingEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   lastResetDate  = TimeCurrent();

   trade.SetExpertMagicNumber(SOVEREIGN_MAGIC);
   trade.SetDeviationInPoints(InpMaxSlippage);

   //--- Create indicator handles once; never create them per tick.
   h1Sma20Handle = iMA(_Symbol, PERIOD_H1, 20, 0, MODE_SMA, PRICE_CLOSE);
   h1Sma50Handle = iMA(_Symbol, PERIOD_H1, 50, 0, MODE_SMA, PRICE_CLOSE);
   h1Sma200Handle = iMA(_Symbol, PERIOD_H1, 200, 0, MODE_SMA, PRICE_CLOSE);
   //--- H1-equivalent trend fallback built from M5 bars.  The Strategy Tester
   //--- can hold a stale H1 cache; 240/600/2400 M5 bars cover the same windows.
   m5Sma240Handle  = iMA(_Symbol, PERIOD_CURRENT, 240, 0, MODE_SMA, PRICE_CLOSE);
   m5Sma600Handle  = iMA(_Symbol, PERIOD_CURRENT, 600, 0, MODE_SMA, PRICE_CLOSE);
   m5Sma2400Handle = iMA(_Symbol, PERIOD_CURRENT, 2400, 0, MODE_SMA, PRICE_CLOSE);
   atrHandle     = iATR(_Symbol, PERIOD_CURRENT, SOVEREIGN_ATR_PERIOD);

   if(h1Sma20Handle == INVALID_HANDLE || h1Sma50Handle == INVALID_HANDLE ||
      h1Sma200Handle == INVALID_HANDLE || m5Sma240Handle == INVALID_HANDLE ||
      m5Sma600Handle == INVALID_HANDLE || m5Sma2400Handle == INVALID_HANDLE ||
      atrHandle == INVALID_HANDLE)
   {
      Print("Sovereign EA init failed: unable to create indicator handles. Error: ", GetLastError());
      return(INIT_FAILED);
   }

   //--- The exported network was trained on M5 features. Keep it opt-in and
   //--- refuse to run it on a different execution timeframe.
   if(InpUseMLFilter)
   {
      if(Period() != PERIOD_M5)
      {
         Print("Sovereign ML: model requires M5 execution bars; neural filter disabled.");
         g_mlReady = false;
      }
      else
      {
         h1AdxHandle    = iADX(_Symbol, PERIOD_H1, 14);
         h1AtrHandle    = iATR(_Symbol, PERIOD_H1, 14);
         m5Ema20Handle  = iMA(_Symbol, PERIOD_M5, 20, 0, MODE_EMA, PRICE_CLOSE);
         m5Rsi2Handle   = iRSI(_Symbol, PERIOD_M5, 2, PRICE_CLOSE);
         d1Sma200Handle = iMA(_Symbol, PERIOD_D1, 200, 0, MODE_SMA, PRICE_CLOSE);
         g_mlReady = (h1AdxHandle != INVALID_HANDLE &&
                      h1AtrHandle != INVALID_HANDLE &&
                      m5Ema20Handle != INVALID_HANDLE &&
                      m5Rsi2Handle != INVALID_HANDLE &&
                      d1Sma200Handle != INVALID_HANDLE);
         if(!g_mlReady)
            Print("Sovereign ML: indicator initialization failed; neural filter disabled.");
      }
   }

   //--- Load the adaptive governor after the terminal account state is ready.
   g_perf.Init(InpEnablePerformanceGovernor, _Symbol, SOVEREIGN_MAGIC,
               InpGovernorDDStart, InpGovernorDDLimit, InpGovernorLossStreak);
   if(InpPerformanceReset) g_perf.Reset();

   if(InpEnableTelemetry)
      g_python.Init(InpPythonHost, InpPythonPort, 1000, 2);

   if(!g_audit.Init(InpEnableAuditLog))
      Print("Sovereign audit: CSV logging could not be fully initialized.");

   g_traffic.Init(InpUseTrafficLight, _Symbol, InpTrafficLightLog);
   g_traffic.Configure(InpTrafficLightGateMode, InpTrafficLightMinGreen,
                       InpTrafficLightRiskScale, InpTrafficLightUseMacro,
                       InpTrafficLightUsePattern, InpMaxSpread,
                       InpKillzoneStart, InpKillzoneEnd,
                       InpExtremeThreshold);

   PrintFormat(
      "Sovereign symbol: digits=%d point=%.6f tick=%.6f stops=%d freeze=%d spread=%d minLot=%.2f step=%.2f tickValue=%.5f",
      (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS),
      _Point,
      SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE),
      (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL),
      (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL),
      (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD),
      SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN),
      SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP),
      SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE));

   datetime gmtTime = TimeGMT();
   datetime serverTime = TimeCurrent();
   serverTimeOffsetHours = (int)((serverTime - gmtTime) / 3600);
   if(MQLInfoInteger(MQL_TESTER))
      serverTimeOffsetHours = InpServerGMTOffset;
   else if(!InpAutoServerOffset)
      serverTimeOffsetHours = InpServerGMTOffset;
   Print("Sovereign EA Initialized. Server GMT offset: ", serverTimeOffsetHours,
         " hrs (tester=", (MQLInfoInteger(MQL_TESTER) ? "yes" : "no"),
         "). Starting Equity: $", startingEquity);

   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   g_perf.Deinit();
   g_python.Deinit();
   g_audit.Deinit();
   g_traffic.Deinit();

   if(h1Sma20Handle != INVALID_HANDLE) IndicatorRelease(h1Sma20Handle);
   if(h1Sma50Handle != INVALID_HANDLE) IndicatorRelease(h1Sma50Handle);
   if(h1Sma200Handle != INVALID_HANDLE) IndicatorRelease(h1Sma200Handle);
   if(m5Sma240Handle != INVALID_HANDLE) IndicatorRelease(m5Sma240Handle);
   if(m5Sma600Handle != INVALID_HANDLE) IndicatorRelease(m5Sma600Handle);
   if(m5Sma2400Handle != INVALID_HANDLE) IndicatorRelease(m5Sma2400Handle);
   if(atrHandle     != INVALID_HANDLE) IndicatorRelease(atrHandle);
   if(h1AdxHandle   != INVALID_HANDLE) IndicatorRelease(h1AdxHandle);
   if(h1AtrHandle   != INVALID_HANDLE) IndicatorRelease(h1AtrHandle);
   if(m5Ema20Handle != INVALID_HANDLE) IndicatorRelease(m5Ema20Handle);
   if(m5Rsi2Handle  != INVALID_HANDLE) IndicatorRelease(m5Rsi2Handle);
   if(d1Sma200Handle!= INVALID_HANDLE) IndicatorRelease(d1Sma200Handle);
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   CheckDailyReset();
   g_perf.OnTick();
   ExpireStalePendingOrders();
   if (CheckDailyDrawdown()) return;

   // 0. Pull the latest Python telemetry (non-blocking).
   if(InpEnableTelemetry) g_python.Poll();

   // 1. ZMQ/TCP Heartbeat Watchdog
   CheckZmqWatchdog();

   // 2. Clock/session offset (handles broker DST in live trading)
   RefreshServerOffset();

   // 3. Institutional Filters
   if (!IsInInstitutionalKillzone()) return;
   if (IsHighImpactNewsWindow()) return;
   if (SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) > InpMaxSpread) return;

   // 4. Active Trade Management
   ManageOpenPositions();

   // 5. New Bar Execution Filter
   static datetime lastBarTime = 0;
   datetime currentBarTime = iTime(_Symbol, PERIOD_CURRENT, 0);
   if (currentBarTime == lastBarTime) return;
   lastBarTime = currentBarTime;

   // 6. Multi-Timeframe Confluence Check
   if (InpUseHTFConfluence && !CheckHigherTimeframeConfluence())
   {
      LogSignalSnapshot();
      return;
   }

   LogSignalSnapshot();

   if (CountTotalOrdersAndPositions() > 0) return;

   // 7. Execute Sovereign Engine
   if(InpEntryMode == ENTRY_BREAKOUT)
      ExecuteBreakoutEngine();
   else
      ExecuteSovereignEngine();
}

//+------------------------------------------------------------------+
//| Cancel pending orders that failed to fill within the configured  |
//| window. Without this, one unfilled limit blocks every later entry.|
//+------------------------------------------------------------------+
void ExpireStalePendingOrders()
{
   if(InpPendingExpiryMinutes <= 0) return;

   datetime cutoff = TimeCurrent() - (InpPendingExpiryMinutes * 60);
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if(OrderGetInteger(ORDER_MAGIC) != SOVEREIGN_MAGIC) continue;

      datetime setupTime = (datetime)OrderGetInteger(ORDER_TIME_SETUP);
      if(setupTime > cutoff) continue;

      if(trade.OrderDelete(ticket))
         PrintFormat("Sovereign: canceled stale pending order #%I64u after %d minutes.",
                     ticket, InpPendingExpiryMinutes);
   }
}

//+------------------------------------------------------------------+
//| Adaptive execution helpers                                       |
//+------------------------------------------------------------------+
double CalculateRiskMoney(const double lotSize, const double slPoints)
{
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickValue <= 0.0 || tickSize <= 0.0 || lotSize <= 0.0 || slPoints <= 0.0)
      return 0.0;
   double pointValue = tickValue * (_Point / tickSize);
   return lotSize * slPoints * pointValue;
}

string FeaturesToCsv(const double &f[])
{
   string out = "";
   for(int i = 0; i < ArraySize(f); i++)
   {
      if(i > 0) out += ";";
      out += DoubleToString(f[i], 6);
   }
   return out;
}

//+------------------------------------------------------------------+
//| Build the exact feature vector used by research/candidates.py.    |
//| The order is fixed and must match SovereignMLModel.mqh.           |
//+------------------------------------------------------------------+
bool BuildMLFeatures(const int side, const int mode, double &f[])
{
   if(!g_mlReady || side == 0) return false;
   double h1sma20[1], h1sma50[1], h1sma200[1];
   double h1adx[1], h1plus[1], h1minus[1], h1atr[1];
   double atr[1], ema20[1], rsi2[1], d1sma200[1];
   if(CopyBuffer(h1Sma20Handle, 0, 1, 1, h1sma20) < 1) return false;
   if(CopyBuffer(h1Sma50Handle, 0, 1, 1, h1sma50) < 1) return false;
   if(CopyBuffer(h1Sma200Handle, 0, 1, 1, h1sma200) < 1) return false;
   if(CopyBuffer(h1AdxHandle, 0, 1, 1, h1adx) < 1) return false;
   if(CopyBuffer(h1AdxHandle, 1, 1, 1, h1plus) < 1) return false;
   if(CopyBuffer(h1AdxHandle, 2, 1, 1, h1minus) < 1) return false;
   if(CopyBuffer(h1AtrHandle, 0, 1, 1, h1atr) < 1) return false;
   if(CopyBuffer(atrHandle, 0, 1, 1, atr) < 1) return false;
   if(CopyBuffer(m5Ema20Handle, 0, 1, 1, ema20) < 1) return false;
   if(CopyBuffer(m5Rsi2Handle, 0, 1, 1, rsi2) < 1) return false;
   if(CopyBuffer(d1Sma200Handle, 0, 1, 1, d1sma200) < 1) return false;

   double close1 = iClose(_Symbol, PERIOD_CURRENT, 1);
   double open1  = iOpen(_Symbol, PERIOD_CURRENT, 1);
   double close2 = iClose(_Symbol, PERIOD_CURRENT, 2);
   double close7 = iClose(_Symbol, PERIOD_CURRENT, 7);
   double h1close = iClose(_Symbol, PERIOD_H1, 1);
   double d1close = iClose(_Symbol, PERIOD_D1, 1);

   if(close1 <= 0.0 || open1 <= 0.0 || close2 <= 0.0 || close7 <= 0.0 ||
      h1close <= 0.0 || d1close <= 0.0 || atr[0] <= 0.0 || h1atr[0] <= 0.0)
      return false;

   int look = MathMax(10, InpStructuralLookback);
   double hh = -DBL_MAX;
   double ll = DBL_MAX;
   for(int i = 1; i <= look; i++)
   {
      double h = iHigh(_Symbol, PERIOD_CURRENT, i);
      double l = iLow(_Symbol, PERIOD_CURRENT, i);
      if(h > hh) hh = h;
      if(l < ll) ll = l;
   }
   double width = hh - ll;
   if(width <= 0.0) return false;

   double atrSeries[];
   ArraySetAsSeries(atrSeries, true);
   int atrCount = CopyBuffer(atrHandle, 0, 1, 200, atrSeries);
   if(atrCount < 50) return false;
   double atrAvg = 0.0;
   for(int i = 0; i < 50; i++) atrAvg += atrSeries[i];
   atrAvg /= 50.0;
   double atrRatio = (atrAvg > 0.0) ? (atr[0] / atrAvg) : 1.0;
   int below = 0;
   for(int i = 0; i < atrCount; i++)
      if(atrSeries[i] <= atr[0]) below++;
   double atrPct = (double)below / (double)atrCount;

   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int hour = GetUTCHour();
   int weekday = (dt.day_of_week + 6) % 7;   // 0 = Monday
   double twoPi = 6.283185307179586;
   double atrPts = atr[0] / _Point;
   double h1AtrPts = h1atr[0] / _Point;
   double spreadScaled = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) / 10.0;
   double d1Dist = (d1close - d1sma200[0]) / h1atr[0];

   ArrayResize(f, SOV_ML_FEATURES);
   f[0]  = (double)side;
   f[1]  = (double)mode;
   f[2]  = (double)g_htfTrend;
   f[3]  = h1adx[0] / 50.0;
   f[4]  = h1AtrPts / 100.0;
   f[5]  = (h1close - h1sma200[0]) / h1atr[0];
   f[6]  = (h1plus[0] - h1minus[0]) / 50.0;
   f[7]  = atrPts / 100.0;
   f[8]  = atrRatio;
   f[9]  = rsi2[0] / 100.0;
   f[10] = (close1 - ll) / width;
   f[11] = MathAbs(close1 - open1) / atr[0];
   f[12] = (close1 - close2) / atr[0];
   f[13] = (close1 - close7) / atr[0];
   f[14] = (close1 - ema20[0]) / atr[0];
   f[15] = (hh - close1) / atr[0];
   f[16] = (close1 - ll) / atr[0];
   f[17] = width / atr[0];
   f[18] = atrPct;
   f[19] = MathSin(twoPi * (double)hour / 24.0);
   f[20] = MathCos(twoPi * (double)hour / 24.0);
   f[21] = MathSin(twoPi * (double)weekday / 7.0);
   f[22] = MathCos(twoPi * (double)weekday / 7.0);
   f[23] = spreadScaled;
   f[24] = d1Dist;

   for(int i = 0; i < SOV_ML_FEATURES; i++)
      if(!MathIsValidNumber(f[i])) return false;
   return true;
}

void RegisterAdaptiveTrade(const ulong orderTicket, const ulong dealTicket,
                           const string bucket, const double riskMoney,
                           const int side, const int mode, const double mlProb,
                           const string featureCsv)
{
   if(!InpEnablePerformanceGovernor) return;
   ulong positionId = 0;
   if(dealTicket > 0 && HistoryDealSelect(dealTicket))
      positionId = (ulong)HistoryDealGetInteger(dealTicket, DEAL_POSITION_ID);

   if(positionId > 0)
      g_perf.RegisterPosition(positionId, bucket, riskMoney, side, mode, mlProb, featureCsv);
   else if(orderTicket > 0)
      g_perf.RegisterOrder(orderTicket, bucket, riskMoney, side, mode, mlProb, featureCsv);
}

//+------------------------------------------------------------------+
//| Donchian breakout engine (closed-bar confirmation)               |
//+------------------------------------------------------------------+
void ExecuteBreakoutEngine()
{
   if(!IsEngineSessionAllowed(1)) return;

   double highestHigh = 0.0;
   double lowestLow   = DBL_MAX;

   //--- Use completed bars only: bar 1 broke out of bars 2..N+1.
   for(int i = 2; i <= InpStructuralLookback + 1; i++)
   {
      double h = iHigh(_Symbol, PERIOD_CURRENT, i);
      double l = iLow(_Symbol, PERIOD_CURRENT, i);
      if(h > highestHigh) highestHigh = h;
      if(l < lowestLow)   lowestLow = l;
   }

   double close1 = iClose(_Symbol, PERIOD_CURRENT, 1);
   double atrVal = GetCurrentATR();
   if(close1 <= 0 || atrVal <= 0 || highestHigh <= 0 || lowestLow == DBL_MAX)
      return;

   double buffer = MathMax(2 * _Point, atrVal * 0.05);
   bool isLong  = (close1 > (highestHigh + buffer));
   bool isShort = (close1 < (lowestLow - buffer));
   if(isLong == isShort) return;

   if(InpUseHTFConfluence)
   {
      if(isLong && g_htfTrend != 1)  return;
      if(isShort && g_htfTrend != -1) return;
   }

   if(!PythonAllowsTrade(isLong)) return;

   double volMultiplier = GetVolatilityRegimeMultiplier();
   int side = isLong ? 1 : -1;
   int utcHour = GetUTCHour();
   string bucket = g_perf.BucketKey(1, side, g_atrRatio, utcHour);
   double perfMult = g_perf.CombinedMultiplier(bucket);
   if(perfMult <= 0.0)
   {
      g_audit.LogAdaptive("VETO", bucket, 1, side, -1.0, perfMult, 0.0, 0.0, "governor_blocked");
      return;
   }

   double mlProb = -1.0;
   double mlFeatures[];
   string featureCsv = "";
   if(InpUseMLFilter)
   {
      if(!BuildMLFeatures(side, 1, mlFeatures))
      {
         if(!InpMLFailOpen)
         {
            g_audit.LogAdaptive("VETO", bucket, 1, side, -1.0, perfMult, 0.0, 0.0, "ml_features_unavailable");
            return;
         }
      }
      else
      {
         mlProb = SovereignMLPredict(mlFeatures);
         featureCsv = FeaturesToCsv(mlFeatures);
         if(mlProb < InpMLThreshold)
         {
            g_audit.LogAdaptive("VETO", bucket, 1, side, mlProb, perfMult, 0.0, 0.0, "ml_below_threshold");
            return;
         }
      }
   }

   //--- Pre-trade traffic light board.  In observe mode it logs the board but
   //--- does not block; in gate modes it can veto and it scales the risk.
   g_traffic.SetSessionWindow(InpBreakoutSessionStart, InpBreakoutSessionEnd);
   TrafficLightResult tl;
   tl = g_traffic.Evaluate(side, 1, atrVal, GetRangePosition(), g_htfTrend,
                           perfMult > 0.0,
                           (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD),
                           utcHour, !IsHighImpactNewsWindow(), true);
   string tlNote = StringFormat("breakout bucket=%s", bucket);
   g_traffic.Log(tl, side, 1, tlNote);
   if(!tl.allowed)
   {
      g_audit.LogAdaptive("VETO", bucket, 1, side, mlProb,
                          perfMult * tl.riskMultiplier, 0.0, 0.0,
                          "traffic_light:" + tl.reason);
      return;
   }
   double riskMult = perfMult * tl.riskMultiplier;

   double slPoints = (atrVal * InpSLATRMultiple * volMultiplier) / _Point;
   double tpPoints = slPoints * InpTPRMultiple;

   double stopsLevelPoints = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   if(stopsLevelPoints < 0) stopsLevelPoints = 0;
   slPoints = MathMax(slPoints, stopsLevelPoints + 5.0);
   tpPoints = MathMax(tpPoints, stopsLevelPoints + 5.0);

   double lotSize = CalculateCapacityOptimizedLotSize(slPoints, volMultiplier, riskMult);
   if(lotSize <= 0) return;

   bool orderSuccess = false;
   double requestedPrice = 0.0;
   string orderType = "";
   string action = "";

   if(isLong)
   {
      requestedPrice = NormalizeDouble(SymbolInfoDouble(_Symbol, SYMBOL_ASK), _Digits);
      orderType = "BUY_BREAKOUT";
      action = "BUY";
      orderSuccess = trade.Buy(
         lotSize, _Symbol, requestedPrice,
         NormalizeDouble(requestedPrice - slPoints * _Point, _Digits),
         NormalizeDouble(requestedPrice + tpPoints * _Point, _Digits),
         "Sovereign Breakout Buy");
   }
   else
   {
      requestedPrice = NormalizeDouble(SymbolInfoDouble(_Symbol, SYMBOL_BID), _Digits);
      orderType = "SELL_BREAKOUT";
      action = "SELL";
      orderSuccess = trade.Sell(
         lotSize, _Symbol, requestedPrice,
         NormalizeDouble(requestedPrice + slPoints * _Point, _Digits),
         NormalizeDouble(requestedPrice - tpPoints * _Point, _Digits),
         "Sovereign Breakout Sell");
   }

   if(orderSuccess)
   {
      g_audit.RegisterAttempt((ulong)trade.ResultOrder(), requestedPrice, isLong ? 1 : -1);
      double riskMoney = CalculateRiskMoney(lotSize, slPoints);
      RegisterAdaptiveTrade((ulong)trade.ResultOrder(), (ulong)trade.ResultDeal(),
                            bucket, riskMoney, side, 1, mlProb, featureCsv);
      g_audit.LogAdaptive("ENTRY", bucket, 1, side, mlProb, riskMult, lotSize, riskMoney, orderType);
   }
   else
   {
      g_audit.LogAdaptive("FAILED", bucket, 1, side, mlProb, riskMult, lotSize, 0.0, orderType);
   }

   double resultVolume = trade.ResultVolume();
   g_audit.LogOrderAttempt(
      action,
      orderType,
      requestedPrice,
      trade.ResultPrice(),
      (resultVolume > 0 ? resultVolume : lotSize),
      (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD),
      (uint)trade.ResultRetcode(),
      trade.ResultComment(),
      g_python.IsHealthy(InpHeartbeatTimeoutSec),
      g_python.Action(),
      g_python.Confidence(),
      g_python.Poc());

   if(!orderSuccess)
      PrintFormat("Sovereign breakout order failed: retcode %d (%s)",
                  trade.ResultRetcode(), trade.ResultComment());
}

//+------------------------------------------------------------------+
//| ZMQ Watchdog & Failover Protocol                                 |
//+------------------------------------------------------------------+
void CheckZmqWatchdog()
{
   bool healthy = g_python.IsHealthy(InpHeartbeatTimeoutSec);

   if(healthy != isZmqConnected)
   {
      if(healthy)
         PrintFormat("Sovereign telemetry ONLINE (heartbeat age %ds).", g_python.HeartbeatAgeSeconds());
      else
         Print("WARNING: Python telemetry lost/stale. Operating in Pure-MQL5 Autonomous Fallback Mode.");
      isZmqConnected = healthy;
   }
}

//+------------------------------------------------------------------+
//| Append one signal snapshot per new bar for post-run reconciliation|
//+------------------------------------------------------------------+
void LogSignalSnapshot()
{
   if(!g_audit.Enabled()) return;

   string localBias = "NONE";
   if(g_htfTrend > 0)      localBias = "BULL";
   else if(g_htfTrend < 0) localBias = "BEAR";

   g_audit.LogSignal(
      localBias,
      SymbolInfoDouble(_Symbol, SYMBOL_BID),
      g_python.IsHealthy(InpHeartbeatTimeoutSec),
      g_python.Action(),
      g_python.Confidence(),
      g_python.Poc(),
      g_python.RangePosition(),
      g_python.VolatilityRatio(),
      (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD),
      g_python.HeartbeatAgeSeconds());
}

//+------------------------------------------------------------------+
//| Python signal gate (only applied while the link is healthy)      |
//+------------------------------------------------------------------+
bool PythonAllowsTrade(const bool isDiscountZone)
{
   if(!InpEnableTelemetry || !InpRequirePythonSignal) return true;
   if(!g_python.IsHealthy(InpHeartbeatTimeoutSec))    return true; // autonomous fallback

   if(g_python.Confidence() < InpMinSignalConfidence)
   {
      PrintFormat("Sovereign: Python confidence %.2f below threshold %.2f. Trade vetoed.",
                  g_python.Confidence(), InpMinSignalConfidence);
      return false;
   }

   if(isDiscountZone)
      return (g_python.Action() == "BUY");

   return (g_python.Action() == "SELL");
}

//+------------------------------------------------------------------+
//| Higher Timeframe (H1) Confluence Check                           |
//+------------------------------------------------------------------+
bool CheckHigherTimeframeConfluence()
{
   g_htfTrend = 0;

   double sma20[1], sma50[1], sma200[1];
   double h1Close = 0.0;

   //--- Prefer real H1 data.  If the H1 series is stale (seen in the Strategy
   //--- Tester when its H1 cache ends before the test end), use the equivalent
   //--- M5 SMA windows (240/600/2400 bars) so the trend filter keeps working.
   datetime h1Bar = iTime(_Symbol, PERIOD_H1, 0);
   bool h1Fresh = (h1Bar > 0 && h1Bar + 7200 >= TimeCurrent());

   if(h1Fresh)
   {
      if(CopyBuffer(h1Sma20Handle, 0, 1, 1, sma20) < 1) return false;
      if(CopyBuffer(h1Sma50Handle, 0, 1, 1, sma50) < 1) return false;
      if(InpUseMacroTrend && CopyBuffer(h1Sma200Handle, 0, 1, 1, sma200) < 1) return false;
      h1Close = iClose(_Symbol, PERIOD_H1, 1);
   }
   else
   {
      if(CopyBuffer(m5Sma240Handle, 0, 1, 1, sma20) < 1) return false;
      if(CopyBuffer(m5Sma600Handle, 0, 1, 1, sma50) < 1) return false;
      if(InpUseMacroTrend && CopyBuffer(m5Sma2400Handle, 0, 1, 1, sma200) < 1) return false;
      h1Close = iClose(_Symbol, PERIOD_CURRENT, 1);
   }

   if(h1Close <= 0) return false;

   //--- Require price, fast MA and slow MA to agree on direction. The optional
   //--- H1 SMA200 filter blocks counter-trend entries during larger swings.
   bool macroBull = (!InpUseMacroTrend || h1Close > sma200[0]);
   bool macroBear = (!InpUseMacroTrend || h1Close < sma200[0]);

   if(h1Close > sma20[0] && sma20[0] > sma50[0] && macroBull)      g_htfTrend = 1;
   else if(h1Close < sma20[0] && sma20[0] < sma50[0] && macroBear) g_htfTrend = -1;

   return (g_htfTrend != 0);
}

//+------------------------------------------------------------------+
//| Sovereign Execution Engine                                       |
//+------------------------------------------------------------------+
void ExecuteSovereignEngine()
{
   if(!IsEngineSessionAllowed(0)) return;

   double currentPrice;
   bool isDiscount;

   if (!IsOutsideNoMansLand(currentPrice, isDiscount)) return;

   //--- H1 confluence must agree with the M5 structural direction.
   if (InpUseHTFConfluence)
   {
      if (isDiscount && g_htfTrend != 1)  return;
      if (!isDiscount && g_htfTrend != -1) return;
   }

   //--- Optional Python veto while the telemetry link is healthy.
   if (!PythonAllowsTrade(isDiscount)) return;

   double atrVal = GetCurrentATR();
   double volMultiplier = GetVolatilityRegimeMultiplier();
   int side = isDiscount ? 1 : -1;
   int utcHour = GetUTCHour();
   string bucket = g_perf.BucketKey(0, side, g_atrRatio, utcHour);
   double perfMult = g_perf.CombinedMultiplier(bucket);
   if(perfMult <= 0.0)
   {
      g_audit.LogAdaptive("VETO", bucket, 0, side, -1.0, perfMult, 0.0, 0.0, "governor_blocked");
      return;
   }

   double mlProb = -1.0;
   double mlFeatures[];
   string featureCsv = "";
   if(InpUseMLFilter)
   {
      if(!BuildMLFeatures(side, 0, mlFeatures))
      {
         if(!InpMLFailOpen)
         {
            g_audit.LogAdaptive("VETO", bucket, 0, side, -1.0, perfMult, 0.0, 0.0, "ml_features_unavailable");
            return;
         }
      }
      else
      {
         mlProb = SovereignMLPredict(mlFeatures);
         featureCsv = FeaturesToCsv(mlFeatures);
         if(mlProb < InpMLThreshold)
         {
            g_audit.LogAdaptive("VETO", bucket, 0, side, mlProb, perfMult, 0.0, 0.0, "ml_below_threshold");
            return;
         }
      }
   }

   //--- Pre-trade traffic light board.  Strategy-specific session window and
   //--- strategy-aware macro/pattern lights are handled inside the board.
   g_traffic.SetSessionWindow(InpMeanRevSessionStart, InpMeanRevSessionEnd);
   TrafficLightResult tl;
   tl = g_traffic.Evaluate(side, 0, atrVal, GetRangePosition(), g_htfTrend,
                           perfMult > 0.0,
                           (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD),
                           utcHour, !IsHighImpactNewsWindow(), true);
   string tlNote = StringFormat("meanrev bucket=%s", bucket);
   g_traffic.Log(tl, side, 0, tlNote);
   if(!tl.allowed)
   {
      g_audit.LogAdaptive("VETO", bucket, 0, side, mlProb,
                          perfMult * tl.riskMultiplier, 0.0, 0.0,
                          "traffic_light:" + tl.reason);
      return;
   }
   double riskMult = perfMult * tl.riskMultiplier;

   double slPoints = (atrVal * InpSLATRMultiple * volMultiplier) / _Point;
   double tpPoints = slPoints * InpTPRMultiple;

   //--- Respect broker minimum stop distance and leave room for price drift
   //--- between reading the quote and the server validating the order.
   double stopsLevelPoints = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   if(stopsLevelPoints < 0) stopsLevelPoints = 0;
   slPoints = MathMax(slPoints, stopsLevelPoints + 5.0);
   tpPoints = MathMax(tpPoints, stopsLevelPoints + 5.0);

   //--- A 3-point pullback is inside normal M5 bar noise and repeatedly
   //--- produced retcode 10015 (invalid price) at bar open. Use a
   //--- volatility-aware buffer instead.
   double entryBufferPoints = MathMax(12.0, (atrVal / _Point) * InpEntryBufferATR);
   entryBufferPoints = MathMax(entryBufferPoints, stopsLevelPoints + 3.0);

   // Continuous Capacity Lot Sizing (Handles $10 accounts to $10,000+ seamlessly)
   double lotSize = CalculateCapacityOptimizedLotSize(slPoints, volMultiplier, riskMult);
   if(lotSize <= 0) return; // Blocked due to margin limits

   // Automatic Microstructure Router (Limit vs Market)
   bool useMarketOrder = EvaluateMicrostructureRejection(isDiscount);
   string action = isDiscount ? "BUY" : "SELL";

   bool orderSuccess = false;
   int attempts = 0;
   
   while(!orderSuccess && attempts < 3)
   {
      attempts++;

      double requestedPrice = 0.0;
      string orderType = "";

      if (!useMarketOrder) // Patient Pending Limit Order
      {
         if (isDiscount)
         {
            double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
            double limitPrice = NormalizeDouble(bid - (entryBufferPoints * _Point), _Digits);
            double stopLoss = NormalizeDouble(limitPrice - (slPoints * _Point), _Digits);
            double takeProfit = NormalizeDouble(limitPrice + (tpPoints * _Point), _Digits);
            requestedPrice = limitPrice;
            orderType = "BUY_LIMIT";
            if(trade.BuyLimit(lotSize, limitPrice, _Symbol, stopLoss, takeProfit, ORDER_TIME_GTC, 0, "Sovereign Limit Buy"))
               orderSuccess = true;
         }
         else
         {
            double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
            double limitPrice = NormalizeDouble(ask + (entryBufferPoints * _Point), _Digits);
            double stopLoss = NormalizeDouble(limitPrice + (slPoints * _Point), _Digits);
            double takeProfit = NormalizeDouble(limitPrice - (tpPoints * _Point), _Digits);
            requestedPrice = limitPrice;
            orderType = "SELL_LIMIT";
            if(trade.SellLimit(lotSize, limitPrice, _Symbol, stopLoss, takeProfit, ORDER_TIME_GTC, 0, "Sovereign Limit Sell"))
               orderSuccess = true;
         }
      }
      else // Aggressive Market Order (Sweep / Rejection Wick)
      {
         if (isDiscount)
         {
            double ask = NormalizeDouble(SymbolInfoDouble(_Symbol, SYMBOL_ASK), _Digits);
            requestedPrice = ask;
            orderType = "BUY_MARKET";
            if(trade.Buy(lotSize, _Symbol, ask, NormalizeDouble(ask - (slPoints * _Point), _Digits), NormalizeDouble(ask + (tpPoints * _Point), _Digits), "Sovereign Market Buy"))
               orderSuccess = true;
         }
         else
         {
            double bid = NormalizeDouble(SymbolInfoDouble(_Symbol, SYMBOL_BID), _Digits);
            requestedPrice = bid;
            orderType = "SELL_MARKET";
            if(trade.Sell(lotSize, _Symbol, bid, NormalizeDouble(bid + (slPoints * _Point), _Digits), NormalizeDouble(bid - (tpPoints * _Point), _Digits), "Sovereign Market Sell"))
               orderSuccess = true;
         }
      }

      if(orderSuccess)
      {
         g_audit.RegisterAttempt((ulong)trade.ResultOrder(), requestedPrice, side);
         double riskMoney = CalculateRiskMoney(lotSize, slPoints);
         RegisterAdaptiveTrade((ulong)trade.ResultOrder(), (ulong)trade.ResultDeal(),
                               bucket, riskMoney, side, 0, mlProb, featureCsv);
         g_audit.LogAdaptive("ENTRY", bucket, 0, side, mlProb, riskMult, lotSize, riskMoney, orderType);
      }
      else
      {
         g_audit.LogAdaptive("FAILED", bucket, 0, side, mlProb, riskMult, lotSize, 0.0, orderType);
      }

      double resultVolume = trade.ResultVolume();
      g_audit.LogOrderAttempt(
         action,
         orderType,
         requestedPrice,
         trade.ResultPrice(),
         (resultVolume > 0 ? resultVolume : lotSize),
         (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD),
         (uint)trade.ResultRetcode(),
         trade.ResultComment(),
         g_python.IsHealthy(InpHeartbeatTimeoutSec),
         g_python.Action(),
         g_python.Confidence(),
         g_python.Poc());

      if(!orderSuccess)
      {
         Print("Execution Warning: Sovereign order failed with retcode ", trade.ResultRetcode(), ". Retrying...");
         Sleep(250);
      }
   }
}

//+------------------------------------------------------------------+
//| Continuous Capacity & Micro-Account ($10 ECN) Lot Calculator     |
//+------------------------------------------------------------------+
double CalculateCapacityOptimizedLotSize(double slPoints, double volMultiplier, double governorMultiplier = 1.0)
{
   double equity      = AccountInfoDouble(ACCOUNT_EQUITY);
   double freeMargin  = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   
   if(governorMultiplier < 0.0) governorMultiplier = 0.0;
   if(governorMultiplier > 1.25) governorMultiplier = 1.25;
   double adjustedRisk = (InpBaseRiskPercent / volMultiplier) * governorMultiplier;
   double riskAmount   = equity * (adjustedRisk / 100.0);
   
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double lotStep   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   
   if(slPoints <= 0 || tickValue <= 0 || tickSize <= 0 || lotStep <= 0) return minLot;

   //--- Value of one point per lot (handles symbols whose tick size != point).
   double pointValue = tickValue * (_Point / tickSize);
   double rawLots    = riskAmount / (slPoints * pointValue);
   
   // Micro-Account Floor Safeguard ($10 ECN Mode Support)
   if(rawLots < minLot)
   {
      rawLots = minLot; 
      Print("Capacity Notice: Equity ($", equity, ") triggered Micro-Account Floor. Forcing minimum lot (", minLot, ").");
   }
   
   // Margin Capacity Safety Check
   double marginRequired = 0;
   double currentAsk = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   
   if(OrderCalcMargin(ORDER_TYPE_BUY, _Symbol, rawLots, currentAsk, marginRequired))
   {
      if(marginRequired > freeMargin)
      {
         Print("CRITICAL CAPACITY BLOCK: Insufficient free margin ($", freeMargin, ") to support required margin ($", marginRequired, "). Trade skipped.");
         return 0;
      }
   }
   
   double normLots = MathFloor(rawLots / lotStep + 0.0000001) * lotStep;
   normLots = MathMax(minLot, MathMin(maxLot, normLots));
   int volDigits = (int)MathMax(0, MathCeil(-MathLog10(lotStep)));
   return NormalizeDouble(normLots, volDigits);
}

//+------------------------------------------------------------------+
//| Microstructure Inspector for Auto-Routing                        |
//+------------------------------------------------------------------+
bool EvaluateMicrostructureRejection(bool isDiscount)
{
   double open  = iOpen(_Symbol, PERIOD_CURRENT, 1);
   double close = iClose(_Symbol, PERIOD_CURRENT, 1);
   double high  = iHigh(_Symbol, PERIOD_CURRENT, 1);
   double low   = iLow(_Symbol, PERIOD_CURRENT, 1);
   
   double range = high - low;
   if(range <= 0) return false;

   double upperWick = high - MathMax(open, close);
   double lowerWick = MathMin(open, close) - low;

   if(isDiscount && (lowerWick / range) > 0.50) return true;
   if(!isDiscount && (upperWick / range) > 0.50) return true;

   return false;
}

//+------------------------------------------------------------------+
//| Volatility Regime Multiplier                                     |
//+------------------------------------------------------------------+
double GetVolatilityRegimeMultiplier()
{
   double atr[];
   ArraySetAsSeries(atr, true);
   if(CopyBuffer(atrHandle, 0, 0, 20, atr) < 20) return 1.0;

   double shortAtr = atr[0];
   double longAtr = 0;
   for(int i=0; i<20; i++) longAtr += atr[i];
   longAtr /= 20.0;

   double ratio = (longAtr > 0) ? (shortAtr / longAtr) : 1.0;
   g_atrRatio = ratio;
   
   if(ratio > 1.3) return 1.25; 
   if(ratio < 0.7) return 0.85; 
   return 1.0;
}

int GetUTCHour()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int hour = dt.hour - serverTimeOffsetHours;
   if(hour < 0) hour += 24;
   if(hour >= 24) hour -= 24;
   return hour;
}

void RefreshServerOffset()
{
   if(MQLInfoInteger(MQL_TESTER))
   {
      serverTimeOffsetHours = InpServerGMTOffset;
      return;
   }

   if(!InpAutoServerOffset)
   {
      serverTimeOffsetHours = InpServerGMTOffset;
      return;
   }

   static datetime lastRefresh = 0;
   datetime now = TimeCurrent();
   if(lastRefresh > 0 && now - lastRefresh < 3600) return;
   lastRefresh = now;

   int diff = (int)MathRound((double)(TimeCurrent() - TimeGMT()) / 3600.0);
   if(diff >= -14 && diff <= 14)
      serverTimeOffsetHours = diff;
}

//--- Standard Helper Filters & Management ---
double GetRangePosition()
{
   double highestHigh = 0.0;
   double lowestLow   = DBL_MAX;

   for(int i = 1; i <= InpStructuralLookback; i++)
   {
      double h = iHigh(_Symbol, PERIOD_CURRENT, i);
      double l = iLow(_Symbol, PERIOD_CURRENT, i);
      if(h > highestHigh) highestHigh = h;
      if(l < lowestLow)   lowestLow = l;
   }

   if(highestHigh <= 0.0 || lowestLow == DBL_MAX) return -1.0;
   double range = highestHigh - lowestLow;
   if(range <= 0.0) return -1.0;

   double price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(price <= 0.0) return -1.0;
   return (price - lowestLow) / range;
}

bool IsEngineSessionAllowed(const int mode)
{
   if(!InpUseSessionPlaybook) return true;

   int start = (mode == 1) ? InpBreakoutSessionStart : InpMeanRevSessionStart;
   int end   = (mode == 1) ? InpBreakoutSessionEnd   : InpMeanRevSessionEnd;
   if(start < 0) start = 0;
   if(start > 23) start = 23;
   if(end < 0) end = 0;
   if(end > 23) end = 23;

   int hour = GetUTCHour();
   if(start <= end) return (hour >= start && hour <= end);
   return (hour >= start || hour <= end);
}

bool IsInInstitutionalKillzone()
{
   datetime serverTime = TimeCurrent();
   MqlDateTime dt;
   TimeToStruct(serverTime, dt);
   if(dt.day_of_week == 0 || dt.day_of_week == 6) return false;

   int utcHour = dt.hour - serverTimeOffsetHours;
   if(utcHour < 0) utcHour += 24;
   if(utcHour >= 24) utcHour -= 24;

   if(utcHour < InpKillzoneStart || utcHour > InpKillzoneEnd) return false;
   return true;
}

bool IsHighImpactNewsWindow()
{
   datetime currentTime = TimeCurrent();
   datetime startTime   = currentTime - (InpNewsBufferMinutes * 60);
   datetime endTime     = currentTime + (InpNewsBufferMinutes * 60);

   MqlCalendarValue values[];
   if(CalendarValueHistory(values, startTime, endTime))
   {
      for(int i = 0; i < ArraySize(values); i++)
      {
         MqlCalendarEvent event;
         if(CalendarEventById(values[i].event_id, event) &&
            event.importance >= CALENDAR_IMPORTANCE_HIGH)
            return true;
      }
   }
   return false;
}

bool IsOutsideNoMansLand(double &currentPrice, bool &isDiscountZone)
{
   double highestHigh = 0;
   double lowestLow = 999999;

   for(int i = 1; i <= InpStructuralLookback; i++)
   {
      double h = iHigh(_Symbol, PERIOD_CURRENT, i);
      double l = iLow(_Symbol, PERIOD_CURRENT, i);
      if(h > highestHigh) highestHigh = h;
      if(l < lowestLow)   lowestLow = l;
   }

   double totalRange = highestHigh - lowestLow;
   if(totalRange <= 0) return false;

   currentPrice = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double premiumThreshold  = highestHigh - (totalRange * InpExtremeThreshold);
   double discountThreshold = lowestLow   + (totalRange * InpExtremeThreshold);

   if(currentPrice > discountThreshold && currentPrice < premiumThreshold) return false; 

   isDiscountZone = (currentPrice <= discountThreshold);
   return true;
}

//+------------------------------------------------------------------+
//| Position management helpers                                      |
//+------------------------------------------------------------------+
bool IsTicketTracked(ulong &tickets[], ulong ticket)
{
   for(int i = 0; i < ArraySize(tickets); i++)
      if(tickets[i] == ticket) return true;
   return false;
}

void TrackTicket(ulong &tickets[], ulong ticket)
{
   if(IsTicketTracked(tickets, ticket)) return;
   int n = ArraySize(tickets);
   ArrayResize(tickets, n + 1);
   tickets[n] = ticket;
}

double NormalizeVolume(double volume)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minV = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxV = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0) step = 0.01;

   double v = MathFloor(volume / step + 0.0000001) * step;
   v = MathMax(minV, MathMin(maxV, v));
   int volDigits = (int)MathMax(0, MathCeil(-MathLog10(step)));
   return NormalizeDouble(v, volDigits);
}

void ManageOpenPositions()
{
   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(PositionGetSymbol(i) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != SOVEREIGN_MAGIC) continue;

      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;

      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double volume    = PositionGetDouble(POSITION_VOLUME);
      double sl        = PositionGetDouble(POSITION_SL);
      double tp        = PositionGetDouble(POSITION_TP);
      long   type      = PositionGetInteger(POSITION_TYPE);

      double currentBid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double currentAsk = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

      if(type == POSITION_TYPE_BUY)
      {
         double riskDistance = openPrice - sl;
         if(riskDistance <= 0) continue;

         double profitDistance = currentBid - openPrice;

         if(InpEnablePartialClose && InpPartialCloseR > 0 &&
            profitDistance >= (riskDistance * InpPartialCloseR) &&
            !IsTicketTracked(g_partialClosedTickets, ticket))
         {
            double halfLots = NormalizeVolume(volume / 2.0);
            if(halfLots >= minLot && (volume - halfLots) >= minLot)
            {
               if(trade.PositionClosePartial(ticket, halfLots))
                  TrackTicket(g_partialClosedTickets, ticket);
            }
         }
         if(InpEnableBreakEven && InpBreakEvenR > 0 &&
            profitDistance >= (riskDistance * InpBreakEvenR) &&
            !IsTicketTracked(g_breakEvenTickets, ticket) && sl < openPrice)
         {
            if(trade.PositionModify(ticket, openPrice + (2 * _Point), tp))
               TrackTicket(g_breakEvenTickets, ticket);
         }
      }
      else if(type == POSITION_TYPE_SELL)
      {
         double riskDistance = sl - openPrice;
         if(riskDistance <= 0) continue;

         double profitDistance = openPrice - currentAsk;

         if(InpEnablePartialClose && InpPartialCloseR > 0 &&
            profitDistance >= (riskDistance * InpPartialCloseR) &&
            !IsTicketTracked(g_partialClosedTickets, ticket))
         {
            double halfLots = NormalizeVolume(volume / 2.0);
            if(halfLots >= minLot && (volume - halfLots) >= minLot)
            {
               if(trade.PositionClosePartial(ticket, halfLots))
                  TrackTicket(g_partialClosedTickets, ticket);
            }
         }
         if(InpEnableBreakEven && InpBreakEvenR > 0 &&
            profitDistance >= (riskDistance * InpBreakEvenR) &&
            !IsTicketTracked(g_breakEvenTickets, ticket) && sl > openPrice)
         {
            if(trade.PositionModify(ticket, openPrice - (2 * _Point), tp))
               TrackTicket(g_breakEvenTickets, ticket);
         }
      }
   }
}

double GetCurrentATR()
{
   double atr[1];
   if(CopyBuffer(atrHandle, 0, 0, 1, atr) < 1) return 0.0010;
   return atr[0];
}

int CountTotalOrdersAndPositions()
{
   int count = 0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == SOVEREIGN_MAGIC)
         count++;
   }

   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) == _Symbol &&
         OrderGetInteger(ORDER_MAGIC) == SOVEREIGN_MAGIC)
         count++;
   }

   return count;
}

void CheckDailyReset()
{
   MqlDateTime dt, lastDt;
   TimeToStruct(TimeCurrent(), dt);
   TimeToStruct(lastResetDate, lastDt);
   if(dt.year != lastDt.year || dt.day_of_year != lastDt.day_of_year)
   {
      startingEquity = AccountInfoDouble(ACCOUNT_EQUITY);
      lastResetDate  = TimeCurrent();
      ArrayResize(g_partialClosedTickets, 0);
      ArrayResize(g_breakEvenTickets, 0);
   }
}

bool CheckDailyDrawdown()
{
   if(startingEquity <= 0) return false;

   double currentEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   double currentDD     = ((startingEquity - currentEquity) / startingEquity) * 100.0;
   if (currentDD >= InpMaxDailyDD) { Print("Sovereign Circuit Breaker: Max daily drawdown reached."); return true; }
   return false;
}

//+------------------------------------------------------------------+
//| Trade transaction audit hook                                     |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   g_audit.LogDeal(trans, result.retcode);
   g_perf.OnTradeTransaction(trans);
}