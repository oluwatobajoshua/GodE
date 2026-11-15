//+------------------------------------------------------------------+
//|                                     SovereignGodsEyeEA.mq5       |
//|                               Copyright 2026, Quant Systems      |
//|    Monthly carry + 12m momentum (default) or D1 xsec mean-rev   |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026"
#property link      "https://www.mql5.com"
#property version   "14.00"
#property strict

#include <Trade\Trade.mqh>
#include <Sovereign\SovereignErrorLog.mqh>
#include <Sovereign\SovereignError.mqh>
#include <Sovereign\SovereignSignalClient.mqh>
#include <Sovereign\SovereignTelemetryUplink.mqh>
#include <Sovereign\SovereignAuditLog.mqh>
#include <Sovereign\SovereignPerformance.mqh>
#include <Sovereign\SovereignTrafficLight.mqh>
#include <Sovereign\SovereignPortfolio.mqh>
#include <Sovereign\SovereignCarryMom.mqh>
#include <Sovereign\SovereignCentury.mqh>
#include <Sovereign\SovereignEvidence.mqh>
#include <Sovereign\SovereignChartVisualizer.mqh>
#include <Sovereign\SovereignTemplateSpec.mqh>
#include <Sovereign\SovereignAiOverlay.mqh>
#include <Sovereign\SovereignStructRide.mqh>
#include <Sovereign\SovereignRSIBand.mqh>
#include "Include\SovereignRsiProfiles.mqh"
#include <Sovereign\SovereignDivergence.mqh>
#include <Sovereign\SovereignEma21Harvest.mqh>
#include <Sovereign\SovereignHedgeBlend.mqh>
#include <Sovereign\SovereignRegimeFilter.mqh>
#include <Sovereign\SovereignCompressionRubber.mqh>
#include <Sovereign\SovereignDynamicPlan.mqh>
#include <Sovereign\SovereignPortfolioRisk.mqh>
#include <Sovereign\SovereignSmartExecution.mqh>
#include <Sovereign\SovereignDiscretion.mqh>

enum SovereignStrategyMode
{
   SOV_CARRY_MOM   = 0,
   SOV_XSEC_MR     = 1,
   SOV_STRUCT_RIDE = 2,
   SOV_RSI_BAND    = 3,
   SOV_HEDGE_BLEND = 4,
   SOV_REGIME_FILTER = 5,
   SOV_COMPRESSION_RUBBERBAND = 6,
   SOV_EMA_HARVEST = 7
};

CTrade trade;

//--- Always-open error log (MQL5\Files\Sovereign\error_log.csv)
CSovereignErrorLog g_errorLog;

//--- Sovereign constants
#define SOVEREIGN_MAGIC 20261103

//--- Portfolio configuration
input group "=== Portfolio Configuration ==="
input SovereignStrategyMode InpStrategy = SOV_RSI_BAND;
input string InpSymbols           = "EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD,USDCAD,NZDUSD,EURJPY,GBPJPY,AUDJPY";
input int    InpTopN              = 4;   // best_win_settings: mom12_n4 stack tune OOS
input int    InpZWindow           = 20;
input double InpEntryZ            = 1.5;
input int    InpMaxHoldBars       = 5;
input double InpRiskPerPosition   = 1.0;
input double InpMaxTotalRisk      = 4.0;
input double InpCarryTilt         = 0.5;

input group "=== Risk & Capital Protection ==="
input double InpMaxDailyDD        = 5.0;
input int    InpMaxSpread          = 0;
input int    InpMaxSlippage        = 3;

input group "=== Money Management & Blowup Prevention ==="
input double InpRiskPerTradePct    = 0.5;     // risk % of account BALANCE per trade (RSI band)
input int    InpMaxConcurrentPositions = 5;   // max open Sovereign positions across all symbols
input double InpEquityHardStopPct  = 10.0;    // halt all trading if equity drops this % from day start (0=disabled)
input double InpDailyLossHardStopPct = 5.0;   // halt trading today if day loss this % (0=disabled)
input bool   InpWeekendFlat        = true;    // close all positions before weekend (Fri 21:00 server)
input int    InpMaxSpreadForTrade  = 0;       // skip new entries when spread exceeds this (pts; 0=disabled)
input double InpTrailMinStepPts    = 0.0;     // min price improvement (pts) to modify trailing SL (0=disabled)
input double InpBreakevenAtR       = 1.0;     // move SL to breakeven when price reaches +N x stop distance (0=disabled)
input double InpBreakevenBufferPts = 0.0;     // extra buffer beyond entry price at breakeven (pts)

input group "=== Portfolio-Wide Correlation & Exposure Heat ==="
input double InpMaxPortfolioRiskPct = 8.0;    // aggregate % of equity at risk across all Sovereign positions (0=disabled)
input double InpMaxNetExposurePerCCY = 0.0;   // max net exposure per currency in lots (0=disabled)

input group "=== Economic Calendar News Blackout (MT5 native) ==="
input bool   InpNewsBlackoutEnabled   = true;   // block entries around high-impact news (uses MT5 Calendar API)
input int    InpNewsBlackoutImpact    = 3;      // min impact: 1=LOW,2=MEDIUM,3=HIGH (default HIGH only)
input int    InpNewsBlackoutMinsBefore= 15;     // block new entries N minutes before event
input int    InpNewsBlackoutMinsAfter = 15;     // block new entries N minutes after event
input string InpNewsBlackoutCurrencies= "";     // currencies to watch (e.g. "USD,EUR"). Empty = chart symbol's currencies

input group "=== Account-Level Kill Switches & Hard Limits ==="
input double InpAccountMaxDDFromPeakPct = 15.0;   // halt all trading if equity drops X% from peak (0=disabled)
input double InpMinFreeMarginPct        = 20.0;   // block entries when free margin < X% of equity (0=disabled)
input double InpMaxLotSize              = 10.0;   // absolute max lot size per trade (fat-finger protection, 0=disabled)
input int    InpMaxPositionAgeBars      = 0;      // close positions older than N bars (0=disabled)
input bool   InpVerifySLTPOnEntry       = true;   // verify SL/TP actually set on broker after order placement
input double InpDailyProfitTargetPct    = 0.0;    // stop new entries today when up X% from day start (0=disabled)
input int    InpMaxTotalSovPositions    = 20;     // max Sovereign positions across ALL symbols (0=disabled)

input group "=== Pre-Trade Traffic Light Board ==="
input bool   InpUseTrafficLight        = true;
input bool   InpTrafficLightLog        = true;
input int    InpTrafficLightGateMode   = 1;
input int    InpTrafficLightMinGreen   = 6;
input bool   InpTrafficLightRiskScale  = true;

input group "=== Python Telemetry & Audit ==="
input bool   InpEnableTelemetry        = true;
input string InpPythonHost             = "127.0.0.1";
input int    InpPythonPort             = 5556;
input bool   InpRequirePythonSignal    = false;
input double InpMinSignalConfidence    = 0.60;
input int    InpHeartbeatTimeoutSec     = 3;
input bool   InpEnableAuditLog         = true;
input ENUM_LOG_LEVEL InpLogLevel       = LOG_INFO;   // LOG_DEBUG=verbose, LOG_INFO=standard, LOG_ERROR=errors-only

input group "=== Adaptive Performance Governor ==="
input bool   InpEnablePerformanceGovernor = true;
input bool   InpPerformanceReset       = false;
input bool   InpGovernorBootstrapHistory = true;
input int    InpGovernorHistoryDays    = 365;
input bool   InpGovernorLogDaily       = true;
input double InpGovernorDDStart        = 3.0;
input double InpGovernorDDLimit        = 12.0;
input int    InpGovernorLossStreak     = 3;

input group "=== Century Survivor (automated adapt) ==="
input bool   InpEnableCenturyAdapt     = true;
input int    InpCenturyReloadSec       = 300;

input group "=== Evidence-backed automation ==="
input bool   InpFollowEvidenceDirectives = true;

input group "=== Sovereign Stack v1 (carry risk overlays) ==="
input bool   InpUseSovereignStackV1    = true;

input group "=== Structure Ride (H1 overlay, USDJPY certified) ==="
input bool   InpStructRideEnabled      = false;
input string InpStructRideSymbols      = "USDJPY"; // only trade struct ride on these symbols
input int    InpStructRideFractalK     = 2;      // swing confirmation bars
input int    InpStructRideMaxHoldBars  = 12;     // max bars in a leg
input double InpStructRideStopAtr      = 1.5;    // stop distance
input double InpStructRideTpAtr        = 2.0;    // take profit distance
input double InpStructRideRiskPct      = 0.5;    // risk per trade %
input double InpStructRideCostPts      = 12;     // cost per cycle
input bool   InpStructRideUseStructureFilter = false; // structure grade retracted — keep off
input bool   InpStackRequireNeutral    = true;
input double InpStackVixLo             = 16.0;
input double InpStackVixHi             = 28.0;
input double InpStackVixMidScale       = 0.45;
input bool   InpStackDdHaltEnable      = false;
input double InpStackDdHaltPct         = 20.0;

//--- Carry vol targeting (matches research/carry.py: 10% annual target,
//--- rolling 12-month vol, max 2x scalar).  Set InpUseCarryStopLoss=false
//--- to hold to month-end with no stop (research default).
input bool   InpUseCarryVolTarget        = true;
input double InpCarryVolTargetPct        = 10.0;
input int    InpCarryVolLookbackMonths   = 12;
input double InpCarryVolMaxScalar        = 2.0;
input bool   InpUseCarryStopLoss         = false;

input group "=== RSI Band (overlay, template params: EMA21/50, RSI9, levels 20/50/80) ==="
input bool   InpRsiBandEnabled         = true;
input string InpRsiBandSymbols         = "";   // symbols to trade RSI band on (empty = any attached symbol)
input double InpRsiBandRiskPct         = 0.5;      // risk per trade %
input double InpRsiBandStopAtr          = 1.5;      // initial stop distance (x ATR)
input int    InpRsiBandMinHoldBars     = 24;       // min bars before RSI-level exit (24 tested on M15/M30)
input double InpRsiBandMargin          = 3.0;      // RSI must cross by this many points
input int    InpRsiBandSpreadGatePct   = 90;       // spread p90 gate for entry
input int    InpRsiBandRsiPeriod       = 9;        // RSI period (template=9)
input int    InpRsiBandMid             = 50;       // RSI mid band (template=50)
input int    InpRsiBandHi              = 75;       // RSI high exit band (template=75, EURUSD validated)
input int    InpRsiBandLo              = 25;       // RSI low exit band (template=25, EURUSD validated)
input double InpRsiBandDeadband        = 0.20;     // EMA slope deadband (template=0.10)
input int    InpRsiBandMaxBars         = 24;       // max bars held before time exit (0=disabled, 24 tested — cuts DD ~50%)
input bool   InpRsiBandAutoDetect      = true;     // auto-set RSI params by symbol (overrides manual above)
input bool   InpRsiBandVerboseLog      = false;    // log every bar to Journal (false = events only)
input double InpRsiBandAtrPctMax       = 0.80;     // skip entry when ATR > this percentile of last 500 bars (0.80 = p80, reduces BTC whipsaw DD)
input int    InpRsiBandMaxMACross      = 5;         // max MA21 crossovers in lookback window (clean trend filter)
input int    InpRsiBandMACrossWindow   = 50;        // lookback bars for MA crossover count
input int    InpRsiBandMinConsecutiveLong = 0;      // min consecutive closes above EMA21 for long entry (0=disabled)
input int    InpRsiBandMinConsecutiveShort = 0;     // min consecutive closes below EMA21 for short entry (0=disabled)
input bool   InpRsiBandRequireEma21Slope  = false;  // require EMA21 slope in trade direction
input bool   InpRsiBandExitOnEmaBreak     = true;    // exit when price closes beyond EMA21
input double InpRsiBandEmaBreakBufferAtr  = 0.5;     // EMA break buffer in ATR multiples (0=exit on touch, 0.5=need 0.5xATR beyond EMA)
input bool   InpRsiBandLongOnly           = false;   // long-only (skip short entries)
input bool   InpRsiBandUseMacroFilter     = false;    // Rule 1: block shorts above / longs below EMA200 (disabled by default for trading)
input bool   InpRsiBandOverlayWarn        = true;    // show a WARN line when unvalidated overlays are active
input bool   InpRsiBandIntraBar           = false;   // enter on forming bar (intra-bar) instead of waiting for bar close
input bool   InpRsiBandPerTimeframe       = true;     // resolve RSI params per symbol+timeframe (per-timeframe profiles)
input string InpRsiBandOverrideProfile    = "";       // manual RSI profile override spec (empty = use table)
input int    InpRsiBandCooldownBars       = 0;        // min bars between entries on same symbol (0=disabled)

//=== RSI band: bearish engulfing + RSI overbought short (downtrend continuation) ===
input bool   InpRsiBandEngulfingEnabled   = false;   // enable engulfing short entry mode
input int    InpRsiBandEngulfingRsiLevel  = 70;      // RSI overbought threshold for engulfing short
input double InpRsiBandEngulfingStopBufferPts = 10;  // buffer above engulfing candle high for stop (pts)
input bool   InpRsiBandEngulfingAllRedExit  = true;  // exit engulfing short on any green candle
input bool   InpRsiBandEngulfingNoKissEma21 = true;  // exit if price touches/kisses EMA21 (bounce risk)
//=== RSI band: green-red entry (fade EMA21 bounce, wait for confirming candle) ===
input bool   InpRsiBandGreenRedEnabled      = false;  // enable green-red short/long entry mode
input int    InpRsiBandGreenRedRsiLevel     = 40;     // RSI threshold for short (short: RSI > 40), long: RSI between 50 and (100-level)
input bool   InpRsiBandGreenRedWaitForRed   = true;   // wait for next candle close to confirm (red→sell, green→buy)
input int    InpRsiBandGreenRedMaxCandles   = 3;      // max consecutive candles before arming
input double InpRsiBandGreenRedEmaTol       = 0.002;  // EMA21 proximity tolerance (0.002 = 0.2%)
//=== RSI band: reflex entry (streak below/above EMA21, cross, then reversal back) ===
input int    InpRsiBandReflexMinStreak   = 3;    // min consecutive candles below/above EMA21 before reflex triggers
//=== RSI band: early trend-change (price closes above/below EMA21 and holds, before EMA21/EMA50 cross) ===
input bool   InpRsiBandEarlyTrendEnabled   = true;   // enable early trend-change entries before EMA confirmation
input int    InpRsiBandEarlyTrendHoldBars  = 2;      // consecutive closes above/below EMA21 to confirm early flip
input double InpRsiBandEarlyTrendSizeMult  = 0.5;    // lot size multiplier for early entries (0.5 = half size)
input int    InpRsiBandEarlyTrendRsiMax    = 50;     // early long: RSI must be below this (recovering from oversold)
input int    InpRsiBandEarlyTrendRsiMin    = 50;     // early short: RSI must be above this (recovering from overbought)
//=== RSI band: market structure filter (green close above red opens / red close below green closes) ===
input bool   InpRsiBandStructureFilterEnabled = false; // enable market structure candle filter
//=== RSI band: EMA21 trend entry (sell on green RSI>level in downtrend, buy on red RSI<level in uptrend) ===
input bool   InpRsiBandEmaTrendEnabled      = false;  // enable EMA21 trend entry mode
input int    InpRsiBandEmaTrendRsiLevel     = 45;     // RSI threshold: short RSI>level, long RSI<(100-level)
input int    InpRsiBandEmaTrendSlopeBars    = 5;      // EMA21 slope lookback bars
input double InpRsiBandEmaTrendSlopeMin     = 0.0001; // min EMA21 slope for trend strength
input double InpRsiBandEmaTrendBodyMinAtr   = 0.5;    // min candle body as fraction of ATR
input int    InpRsiBandEmaTrendConsecutive  = 2;      // consecutive rejections before entry
input bool   InpRsiBandEmaTrendHTFFilter    = false;  // require HTF trend alignment
input int    InpRsiBandEmaTrendHTF          = 16412;  // HTF timeframe (PERIOD_H4=16412)
input int    InpRsiBandEmaTrendStopMode     = 0;      // 0=wick,1=ATR mult,2=EMA21 buffer
input double InpRsiBandEmaTrendStopAtrMult  = 1.5;    // ATR multiplier for stop
input double InpRsiBandEmaTrendEmaDistAtr   = 1.5;    // max distance from EMA21 in ATR for smart exit
input group "=== RSI divergence (display only - never sizes or triggers a trade) ==="
input bool   InpDivEnabled                  = true;   // detect + draw RSI divergence on this chart
input int    InpDivRsiPeriod                = 14;     // RSI period used for divergence
input int    InpDivPivot                    = 3;      // swing pivot: bars required each side
input int    InpDivMinBars                  = 5;      // min bars between the two pivots
input int    InpDivMaxBars                  = 60;     // max bars between the two pivots
input double InpDivMinRsiDelta              = 1.0;    // min RSI improvement at the new extreme
input double InpDivBullMaxRsi               = 50.0;   // bullish: RSI at the new low must be <= this
input double InpDivBearMinRsi               = 50.0;   // bearish: RSI at the new high must be >= this
input int    InpDivLookback                 = 500;    // bars scanned on every new bar
input int    InpDivMaxDraw                  = 12;     // most recent divergences drawn
input bool   InpDivLogOnNew                 = true;   // journal the moment a divergence confirms
input bool   InpDebugMode                   = false;  // DEBUG: bypass symbol whitelist, traffic light, drawdown blocks (sandbox testing)
input bool   InpForceBypassAllFilters     = false;   // FORCE: bypass ALL entry filters (whitelist, traffic light, RSI gates) to test core order placement
input bool   InpVerboseBarLog             = true;    // print full plan summary to Journal on each new bar

input group "=== Institutional Risk Modules (portfolio VaR, Kelly, smart exec, discretion) ==="
input bool   InpPortfolioRiskEnabled      = true;    // enable portfolio-level risk engine
input double InpPortVarLimitPct           = 0.0;     // max portfolio VaR % of equity (0=disabled)
input double InpPortCorrLimit             = 0.0;     // max pairwise correlation (0=disabled)
input double InpPortMaxCcyExposurePct     = 0.0;     // max notional per currency % equity (0=disabled)
input double InpPortKellyFraction         = 0.25;    // fraction of full Kelly (0.0-1.0)
input double InpPortDailyLossLimitPct     = 0.0;     // daily loss circuit breaker % (0=disabled)
input double InpPortWeeklyLossLimitPct    = 0.0;     // weekly loss circuit breaker % (0=disabled)
input double InpPortMaxDDFromPeakPct      = 0.0;     // max DD from peak % (0=disabled)

input bool   InpSmartExecutionEnabled     = false;   // enable TWAP/iceberg execution
input int    InpExecDefaultChildren       = 1;       // TWAP child count (1=immediate)
input int    InpExecSliceIntervalSec      = 60;      // seconds between TWAP children
input double InpExecSlippageMult          = 0.5;     // slippage multiplier (pts per ATR)
input bool   InpExecIceberg               = false;   // iceberg: show partial size
input double InpExecIcebergShowPct        = 0.25;    // % of size to display
input double InpExecAdaptiveSlowdown      = 2.0;     // slowdown when spread wide

input bool   InpDiscretionEnabled         = false;   // enable human discretion/veto layer

input group "=== EMA Harvest (candle harvesting, BTCUSD H1 EMA=2 best) ==="
input bool   InpEmaHarvestEnabled         = false;    // enable EMA harvest strategy
input string InpEmaHarvestSymbols         = "BTCUSD"; // symbols to trade EMA harvest on (research: BTCUSD H1)
input int    InpEmaHarvestPeriod          = 2;        // EMA period for harvest filter (research: 2 best on BTCUSD H1)
input int    InpEmaHarvestMinConsecLong   = 1;        // min consecutive closes above EMA for long entry
input int    InpEmaHarvestMinConsecShort  = 3;        // min consecutive closes below EMA for short entry
input bool   InpEmaHarvestExitOnEmaBreak  = false;    // exit when price closes beyond EMA
input int    InpEmaHarvestRsiExitLong     = 70;       // RSI level to exit long (research: 70 best for profitable trades)
input int    InpEmaHarvestRsiExitShort    = 20;       // RSI level to exit short
input int    InpEmaHarvestMaxHold         = 24;       // max bars held before time exit (research: 24 best)
input double InpEmaHarvestTrailAtr        = 2.0;      // ATR trailing stop multiplier (research: 2.0 best for profitable trades)
input double InpEmaHarvestRiskPct         = 1.0;      // risk per trade %
input int    InpEmaHarvestRsiEntryMax     = 100;      // only enter long if RSI < this (100=disabled)

input group "=== EMA21 Rubber Band (enter NEAR EMA21, exit FAR from EMA21) ==="
input bool   InpEmaRbEnabled         = true;    // enable EMA21 rubber band overlay
input string InpEmaRbSymbols         = "";      // symbols to trade on (empty = any attached symbol)
input int    InpEmaRbPeriod          = 21;      // EMA period
input double InpEmaRbEntryAtr        = 0.3;     // entry within N x ATR of EMA21
input double InpEmaRbExitAtr         = 2.0;     // exit when price extends N x ATR from EMA21
input int    InpEmaRbRsiPeriod       = 14;      // RSI period
input int    InpEmaRbRsiMax          = 70;      // long entry: RSI below this
input int    InpEmaRbRsiMin          = 30;      // short entry: RSI above this
input bool   InpEmaRbRequireGreen    = true;    // long entry needs green candle, short needs red
input bool   InpEmaRbRequireSlope    = false;   // require EMA21 slope in trade direction
input double InpEmaRbStopAtr         = 1.5;     // stop distance x ATR
input double InpEmaRbTpAtr           = 2.0;     // take profit distance x ATR
input double InpEmaRbRiskPct         = 0.5;     // risk per trade %

input group "=== Hedge Blend (RETRACTED - do not enable) ==="
input bool   InpHedgeBlendEnabled      = false;    // RETRACTED: lookahead artifact, leave false
input int    InpHedgeBlendCarryWeight  = 50;       // carry weight % when mom3 > 0
input int    InpHedgeBlendMomLookback  = 3;        // carry momentum lookback months

input group "=== Regime Filter (H1 overlay, compression rubberband) ==="
input bool   InpRegimeFilterEnabled    = false;
input int    InpRegimeFilterMode       = 1;     // 0=none, 1=soft size-down, 2=hard skip
input string InpRegimeFilterSymbols    = "EURUSD,GBPUSD,USDJPY";
input double InpRegimeFilterCompressAtr= 0.5;      // MA compression threshold (x ATR)
input double InpRegimeFilterVolPctile  = 0.33;     // min ATR percentile to trade
input int    InpRegimeFilterAdxMin     = 20;       // min ADX to trade
input int    InpRegimeFilterSessionCut = 21;       // skip late session (hour >=)
input int    InpRegimeFilterMaxSpread  = 12;       // max spread pts
input double InpRegimeFilterAtrRatio   = 0.70;     // min ATR vs 20-bar avg
input double InpRegimeFilterExtAtr     = 2.0;      // extension threshold (x ATR)
input double InpRegimeFilterStopAtr    = 1.5;      // stop distance (x ATR)
input double InpRegimeFilterTpAtr      = 2.0;      // take profit (x ATR)
input double InpRegimeFilterRiskPct    = 0.5;      // risk per trade %
input int    InpRegimeFilterMaxHoldBars= 24;       // max bars in position

input group "=== Compression Rubberband (H1, MA compression fade) ==="
input bool   InpCompressionEnabled     = false;    // SOV_COMPRESSION_RUBBERBAND mode
input string InpCompressionSymbols     = "EURUSD,GBPUSD,USDJPY";
input int    InpCompressionEmaFast     = 8;        // fast EMA
input int    InpCompressionEmaMid      = 21;       // mid EMA
input int    InpCompressionEmaSlow     = 50;       // slow EMA
input double InpCompressionCompressAtr = 0.5;      // MA compression threshold (x ATR)
input double InpCompressionExtAtr      = 2.0;      // extension threshold (x ATR)
input double InpCompressionStopAtr     = 1.5;      // stop distance (x ATR)
input double InpCompressionTpAtr       = 2.0;      // take profit (x ATR)
input double InpCompressionRiskPct     = 0.5;      // risk per trade %
input int    InpCompressionMaxHoldBars = 24;       // max bars in position

input group "=== Chart visuals (display only) ==="
input bool   InpShowVisuals        = true;   // paint state and real positions
input bool   InpVisualPanel        = true;   // state panel, top-left
input bool   InpVisualPositions    = true;   // entry arrows + SL/TP of live positions
input bool   InpVisualSessionBox   = false;  // intraday session box (off on D1)
input int    InpVisualMaxArrows    = 400;
input color  InpVisualSlColor      = clrCrimson;
input color  InpVisualTpColor      = clrLimeGreen;
input bool   InpShowTelemetryHud   = true;   // real-time reasoning HUD on chart + Journal broadcast

input group "=== Visual Simulation / Trade Plan Overlay (LEGACY hardcoded levels) ==="
input bool   InpVisualSimulation      = true;   // draw LEGACY simulated plan (display only)
input int    InpVisualSimMaxArrows    = 100;    // max sim arrows on chart
input int    InpMacroMaPeriod         = 200;    // macro trend filter MA period (EMA 200)
input int    InpMacroMaMethod         = 1;      // 0=SMA, 1=EMA, 2=SMMA, 3=LWMA
input int    InpMaxPyramidLayers      = 3;      // max concurrent pyramid layers (display only)
input double InpInitialEntryPrice     = 77500.0;// DEPRECATED - legacy BTCUSD level, not used by dynamic engine
input double InpScaleIn1Price         = 74800.0;// DEPRECATED - legacy BTCUSD level, not used by dynamic engine
input double InpScaleIn2Price         = 70000.0;// DEPRECATED - legacy BTCUSD level, not used by dynamic engine
input double InpStopLossZonePrice     = 81000.0;// DEPRECATED - legacy BTCUSD level, not used by dynamic engine
input int    InpRsiExhaustionLevel    = 30;     // RSI exhaustion exit for shorts (RSI < 30)
input double InpTrailAtrMultiplier    = 1.5;    // trailing stop ATR multiplier
input bool   InpLegacySimPlan         = false;  // draw the LEGACY hardcoded sim plan (off: dynamic analyst used)

input group "=== Dynamic Market Analyst (analysis + visualisation, display only) ==="
input bool   InpAnalystEnabled       = true;   // analyse market like an LLM analyst and draw the read
input int    InpAnalystSlowMaPeriod  = 100;    // macro / regime MA (slow; template=100)
input int    InpAnalystSlowMaMethod  = 1;      // 0=SMA, 1=EMA, 2=SMMA, 3=LWMA
input int    InpAnalystFastMaPeriod  = 21;     // fast MA (dynamic resistance band upper)
input int    InpAnalystMidMaPeriod   = 50;     // mid MA (dynamic resistance band lower)
input int    InpAnalystAtrPeriod     = 14;     // ATR period for volatility + stops
input int    InpAnalystRsiPeriod     = 9;      // RSI period for momentum
input double InpAnalystStopAtr       = 1.5;    // stop distance (x ATR) for the drawn plan
input double InpAnalystTpAtr         = 2.0;    // take-profit distance (x ATR)
input double InpAnalystScaleStepAtr  = 0.75;   // scale-in checkpoint spacing (x ATR)
input int    InpAnalystPathBars      = 60;     // bars of MA path to draw (0 = no path)
input bool   InpAnalystShowNarrative = true;   // show the plain-English narrative line

input group "=== Pyramiding Execution (OPT-IN, UNVALIDATED) ==="
input bool   InpPyramidExecEnabled    = true;   // open additional legs in profit
input double InpPyramidScaleStepAtr   = 0.75;   // ATR step between scale-in legs
input bool   InpPyramidAllowLoss      = true;   // allow pyramid legs even if existing legs are underwater

input group "=== Chart template (runtime, read-only) ==="
input bool   InpUseTemplate        = true;      // read the published template spec
input string InpTemplateName       = "fxmopol"; // published by scripts\sync_template.ps1
input bool   InpTemplateHotReload  = true;      // pick up template edits without a restart
input int    InpTemplateFractals   = 300;       // fractal lookback for swing markers

input group "=== AI analyst overlay (display only) ==="
input bool   InpAiOverlay          = true;      // draw the analyst's read on the chart
input int    InpAiOverlayMaxAgeSec = 7200;      // mark the read STALE after N seconds

//--- Global Tracking ---
double g_dayStartEquity;
datetime g_dayKey;
bool   g_pythonPaused = false;
bool   g_flatAllRequested = false;
double g_pythonRiskScale = 1.0;
string g_lastBlockReason = "";
datetime g_lastBlockTime = 0;

//--- Macro trend filter (EMA 200) + pyramiding state
int    g_macroMaHandle = INVALID_HANDLE;
int    g_structAtrHandle = INVALID_HANDLE;
int    g_divRsiHandle  = INVALID_HANDLE;   // RSI handle for divergence drawing
double g_macroMaBuf[];
double g_divRsiBuf[];
datetime g_macroMaBar = 0;
datetime g_divBar = 0;                     // last bar the divergence overlay was redrawn on
int    g_pyramidLegs = 0;
double g_pyramidNetProfit = 0.0;
bool   g_pyramidArmed = true;

//--- Portfolio-wide correlation & exposure heat tracking
double g_portfolioRiskPct = 0.0;      // aggregate % of equity at risk across all Sovereign positions
bool   g_portfolioHeatBlocked = false;// true when portfolio heat cap breached
string g_portfolioHeatDetail = "";    // human-readable reason

//--- Economic calendar news blackout tracking (MT5 native Calendar API)
datetime g_newsLastCheck    = 0;      // last time we refreshed calendar events
int      g_newsCacheSeconds = 60;     // cache calendar events N seconds
bool     g_newsBlocked      = false;  // currently inside a news blackout window
string   g_newsBlockDetail  = "";     // which event is blocking

//--- Account-level kill switches & hard limits tracking
double   g_accountPeakEquity = 0.0;   // high watermark for drawdown kill switch
datetime g_accountDDCheckTime = 0;    // throttle drawdown check
bool     g_dailyProfitTargetHit = false;// true when daily profit target reached

ENUM_LOG_LEVEL g_logLevel = LOG_INFO;
datetime g_lastDynBar = 0;   // last bar the analyst markup was drawn on

//--- Telemetry / state broadcast (real-time oversight of EA reasoning)
enum ENUM_SOV_PHASE
{
   SOV_PHASE_IDLE_WAITING_BAR,
   SOV_PHASE_CHECKING_SPREAD,
   SOV_PHASE_CHECKING_TRAFFIC_LIGHT,
   SOV_PHASE_EVALUATING_RSI_BAND,
   SOV_PHASE_PENDING_EXECUTION,
   SOV_PHASE_MANAGING_ACTIVE_TRADE
};
ENUM_SOV_PHASE g_sovPhase = SOV_PHASE_IDLE_WAITING_BAR;
string         g_sovMarketSituation = "Initializing...";
string         g_sovActivePlan = "Waiting for bar data...";
string         g_sovReasoningChain = "";
datetime       g_sovLastBroadcastBar = 0;
ENUM_SOV_PHASE g_sovLastPhase        = SOV_PHASE_IDLE_WAITING_BAR;  // Journal broadcast throttle
bool           g_sovInPosition = false;
int            g_sovPositionSide = 0;
double         g_sovEntryPrice = 0.0;
double         g_sovStopPrice = 0.0;
double         g_sovTpPrice = 0.0;

//--- Portfolio state
CSovereignPortfolio g_portfolio;
CSovereignCarryMom    g_carry;
CSovereignCentury     g_century;
CSovereignEvidence    g_evidence;
CSovereignAuditLog   g_audit;
CSovereignTrafficLight g_traffic;
CSovereignPerformance g_perf;
CSovereignSignalClient g_python;
CSovereignTelemetryUplink g_telemetry;
CSovereignChartVisualizer g_paint;
CSovereignStructRide  g_structRide;
CSovereignRSIBand     g_rsiBand;
CSovereignDivergence  g_div;
CSovereignEma21Harvest g_emaHarvest;
CSovereignHedgeBlend  g_hedgeBlend;
CSovereignRegimeFilter g_regimeFilter;
CSovereignCompressionRubber g_compressionRubber;
CSovereignTemplateSpec g_template;
CSovereignAiOverlay   g_ai;
CSovereignDynamicPlan g_plan;
CSovereignPortfolioRisk g_portRisk;
CSovereignSmartExecution g_exec;
CSovereignDiscretion g_disc;

//--- EMA21 Rubber Band overlay state (enter NEAR EMA21, exit FAR from EMA21)
int     g_emaRbHema = INVALID_HANDLE;
int     g_emaRbHrsi = INVALID_HANDLE;
int     g_emaRbHatr = INVALID_HANDLE;
double  g_emaRbBufEma[];
double  g_emaRbBufRsi[];
double  g_emaRbBufAtr[];
double  g_emaRbBufCl[];
double  g_emaRbBufOp[];
double  g_emaRbBufHi[];
double  g_emaRbBufLo[];
bool    g_emaRbInPosition = false;
int     g_emaRbSide = 0;          // +1 long, -1 short
double  g_emaRbEntryPrice = 0.0;
double  g_emaRbEntryAtr = 0.0;
int     g_emaRbBarsHeld = 0;
string  g_emaRbLastBlockReason = "";
datetime g_emaRbLastBlockTime = 0;

//--- Dynamic plan snapshot + telemetry throttle
SovereignPlanView   g_planView;
datetime            g_lastPlanBuildTime = 0;
datetime            g_lastJournalPrint = 0;
int                 g_lastPrintMinute = -1;
int                 g_lastPlanSide  = 0;   // last side printed in SOV PLAN (log throttle)

//+------------------------------------------------------------------+
//| Symbol-scaled spread caps                                        |
//|                                                                  |
//| A FIXED points cap is FX-calibrated and silently freezes every    |
//| non-FX symbol: 12 pts is 1.2 pips on EURUSD but only 0.012 USD on |
//| BTCUSD, where the broker's NORMAL spread is ~18,000 pts. That made|
//| the traffic light's spread hard-veto fire on 100% of BTCUSD ticks |
//| and the EA never opened a trade.                                  |
//|                                                                  |
//| The cap is therefore: max(configured floor, 2 x the symbol's own  |
//| recent median spread). FX behaviour is unchanged (2x median is    |
//| far below the configured floor); crypto/vol indices get a cap     |
//| that means "unusually wide for THIS symbol", which is the same    |
//| relative idea as the p90 spread gate in the validated research.   |
//+------------------------------------------------------------------+
int      g_spreadMedianPts = -1;   // cached median spread (points), -1 = not computed
datetime g_spreadMedianBar = 0;    // bar the median cache was built on

//--- ring buffer of LIVE spreads, one sample per bar.
//--- iSpread() (the spread stored in bar history) does NOT match SYMBOL_SPREAD on
//--- this broker: BTCUSD history says ~2,400 pts while every live tick quotes
//--- ~19,000 pts. Judging the live quote against history would block forever, so
//--- the reference is built from what the EA itself observes.
#define SOV_SPREAD_RING 200
int      g_sprRing[SOV_SPREAD_RING];
int      g_sprRingCnt = 0;
int      g_sprRingIdx = 0;
datetime g_sprRingBar = 0;

void SovSampleSpreadRing()
{
   datetime bar = iTime(_Symbol, _Period, 0);
   if(bar <= 0 || bar == g_sprRingBar) return;         // once per bar
   g_sprRingBar = bar;
   int s = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(g_sprRingCnt <= 0)
   {
      //--- first sample after attach: judge the current quote against itself so a
      //--- normal spread is not treated as a spike, then adapt bar by bar.
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
   for(int i = 0; i < n; i++)
      tmp[i] = g_sprRing[i];
   ArraySort(tmp);
   return tmp[n / 2];
}

//--- median cached once per bar (the cap is asked for several times per tick)
int SovCachedMedianSpreadPts(const int bars)
{
   datetime bar = iTime(_Symbol, _Period, 0);
   if(g_spreadMedianBar == bar && g_spreadMedianPts >= 0)
      return g_spreadMedianPts;
   int med = SovMedianSpreadPts(bars);
   g_spreadMedianPts = med;
   g_spreadMedianBar = bar;
   return med;
}

int SovEffectiveSpreadCap(const int configuredCapPts)
{
   if(configuredCapPts <= 0) return 0;                 // 0 = disabled, stay disabled

   int med = SovCachedMedianSpreadPts(200);
   int cap = configuredCapPts;
   if(med > 0)
   {
      int rel = (int)MathRound(2.0 * (double)med);
      if(rel > cap) cap = rel;
   }
   return cap;
}

//--- does this symbol have a broker trading session on Sat or Sun?
//--- FX: no (so the Friday-21:00 weekend flat applies). Crypto / synthetic
//--- indices: yes (they trade 24/7, and blocking Sunday froze them).
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

   g_errorLog.Init(InpEnableAuditLog, "Sovereign", 1000000);
   g_logLevel = InpLogLevel;
   SovLogInfo("Sovereign EA OnInit start | log_level=" + EnumToString(g_logLevel));

   SovResetLastError();
   PrintFormat("Sovereign OnInit: checking trade allowed... MQL_TRADE_ALLOWED=%d ACCOUNT_TRADE_EXPERT=%d",
               (bool)MQLInfoInteger(MQL_TRADE_ALLOWED), AccountInfoInteger(ACCOUNT_TRADE_EXPERT));
   if(!SovCheckTradeAllowed())
   {
      //--- NON-FATAL: Algo Trading / account permission is a runtime condition.
      //--- Failing OnInit here removes the EA from the chart and forces a reattach
      //--- after the user enables Algo Trading. Attach and observe instead.
      SovLogWarn("OnInit: terminal/account trading is OFF - EA will initialise in OBSERVE mode "
                 "and start trading when Algo Trading is enabled.");
    }
    else PrintFormat("Sovereign OnInit: trade allowed OK");
    PrintFormat("Sovereign OnInit: checking symbol tradable... _Symbol=%s SYMBOL_TRADE_MODE=%d", _Symbol, SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE));
    if(!SovCheckSymbolTradable(_Symbol))
    {
       //--- NON-FATAL: symbol may still be loading, or valid for display but not
       //--- order entry. Strategies re-check tradability before every order.
       SovLogWarn("OnInit: symbol " + _Symbol + " is not in full trade mode - EA will initialise " +
                  "but cannot open orders on this symbol until it becomes tradable.");
    }
    else PrintFormat("Sovereign OnInit: symbol tradable OK");
    if(!SovSetTradeFilling(trade, _Symbol))
    {
       SovLogWarn("OnInit: could not select a supported filling mode for " + _Symbol);
    }
    else PrintFormat("Sovereign OnInit: trade filling OK");

   trade.SetExpertMagicNumber(SOVEREIGN_MAGIC);
   trade.SetDeviationInPoints(InpMaxSlippage);

   //--- dynamic array: StringSplit resizes it to fit InpSymbols (a fixed
   //--- string syms[10] truncates / fails once the universe exceeds 10 symbols)
   string syms[];
   int n = StringSplit(InpSymbols, ',', syms);
   if(n <= 0)
   {
      SovLogError("OnInit", "no symbols parsed from InpSymbols");
      return(INIT_FAILED);
   }

    if(InpStrategy == SOV_XSEC_MR)
    {
       if(!g_portfolio.Init(syms, n, InpTopN, InpZWindow, InpEntryZ,
                            InpMaxHoldBars, InpRiskPerPosition, InpMaxTotalRisk,
                            InpCarryTilt, SOVEREIGN_MAGIC))
       {
          SovLogError("OnInit", "portfolio init failed");
          return(INIT_FAILED);
       }
    }
    else if(InpStrategy == SOV_CARRY_MOM || InpStrategy == SOV_HEDGE_BLEND)
    {
       if(!g_carry.InitUniverse(syms, n, SOVEREIGN_MAGIC, InpTopN, InpRiskPerPosition,
                                InpMaxTotalRisk, 252, InpMaxSpread))
       {
          SovLogError("OnInit", "carry+momentum universe empty");
          return(INIT_FAILED);
       }
    }
   bool useStack = InpUseSovereignStackV1;
   g_evidence.Reload();
   if(InpFollowEvidenceDirectives && g_evidence.ActiveStrategy() != "")
      PrintFormat("Evidence automation: active_strategy=%s", g_evidence.ActiveStrategy());
   if(InpFollowEvidenceDirectives && g_evidence.PreferStackV1())
      useStack = true;
   if(InpStrategy == SOV_CARRY_MOM)
    {
       g_carry.ConfigureStackV1(useStack, InpStackRequireNeutral,
                                InpStackVixLo, InpStackVixHi, InpStackVixMidScale,
                                InpStackDdHaltEnable, InpStackDdHaltPct);
       g_carry.ConfigureVolTargeting(InpUseCarryVolTarget,
                                     InpCarryVolTargetPct / 100.0,
                                     InpCarryVolLookbackMonths,
                                     InpCarryVolMaxScalar);
       g_carry.ConfigureStopLoss(InpUseCarryStopLoss);
    }

   g_century.Init(InpEnableCenturyAdapt, InpCenturyReloadSec);

   g_perf.Init(InpEnablePerformanceGovernor, _Symbol, SOVEREIGN_MAGIC,
               InpGovernorDDStart, InpGovernorDDLimit, InpGovernorLossStreak);
   if(InpPerformanceReset)
      g_perf.Reset();
   else if(InpGovernorBootstrapHistory)
      g_perf.BootstrapFromHistory(SOVEREIGN_MAGIC, InpGovernorHistoryDays,
                                  InpRiskPerPosition);

   g_traffic.Init(InpUseTrafficLight, _Symbol, InpTrafficLightLog);
   SovLogInfo("OnInit: traffic light init " + ((InpUseTrafficLight) ? "ENABLED" : "disabled"));
   SovLogInfo("OnInit: weekend session " + (SovSymbolTradesOnWeekend()
              ? "OPEN (24/7 symbol - weekend flat will NOT block entries)"
              : "closed (Friday 21:00 weekend flat applies)"));

   g_audit.Init(InpEnableAuditLog);
   SovLogInfo("OnInit: audit log init " + ((InpEnableAuditLog) ? "ENABLED" : "disabled"));

    if(InpEnableTelemetry)
       g_python.Init(InpPythonHost, InpPythonPort, InpHeartbeatTimeoutSec);

    g_telemetry.Init(InpPythonHost, 5557, InpHeartbeatTimeoutSec);

   g_paint.Init(InpShowVisuals, "SOV_GE_", InpVisualMaxArrows);
   SovLogInfo("OnInit: chart visualizer init " + ((InpShowVisuals) ? "ENABLED" : "disabled"));
   //--- The macro MA is used by BOTH the display plan and the (optional) RSI band
   //--- directional lock. Create it whenever either consumer is active so that
   //--- turning the DISPLAY off can never silently change trade behaviour.
   bool needMacroMa = (InpVisualSimulation && InpShowVisuals) ||
                      (InpRsiBandEnabled && InpRsiBandUseMacroFilter);
   if(needMacroMa)
   {
      g_macroMaHandle = iMA(_Symbol, _Period, InpMacroMaPeriod, 0,
                            (ENUM_MA_METHOD)InpMacroMaMethod, PRICE_CLOSE);
      if(g_macroMaHandle == INVALID_HANDLE)
      {
         SovLogError("OnInit", "iMA macro MA handle failed err=" + IntegerToString(GetLastError()));
         g_macroMaHandle = INVALID_HANDLE;
      }
      ArraySetAsSeries(g_macroMaBuf, true);
    }

    //--- RSI handle for divergence detection/drawing (display only)
    g_divRsiHandle = iRSI(_Symbol, _Period, (InpDivRsiPeriod > 0 ? InpDivRsiPeriod : 14), PRICE_CLOSE);
    if(g_divRsiHandle == INVALID_HANDLE)
    {
       SovLogError("OnInit", "iRSI divergence handle failed err=" + IntegerToString(GetLastError()));
       g_divRsiHandle = INVALID_HANDLE;
    }
    else
    {
       ArraySetAsSeries(g_divRsiBuf, true);
       g_div.Configure(g_divRsiHandle, InpDivRsiPeriod, InpDivPivot,
                       InpDivMinBars, InpDivMaxBars, InpDivMinRsiDelta,
                       InpDivBullMaxRsi, InpDivBearMinRsi,
                       InpDivLookback, InpDivMaxDraw);
       PrintFormat("Divergence: RSI%d pivot=%d bars=%d..%d minDelta=%.1f zone<=%.0f/>=%.0f lookback=%d draw=%d %s",
                   InpDivRsiPeriod, InpDivPivot, InpDivMinBars, InpDivMaxBars,
                   InpDivMinRsiDelta, InpDivBullMaxRsi, InpDivBearMinRsi,
                   InpDivLookback, InpDivMaxDraw,
                   (InpDivEnabled ? "ENABLED (display only)" : "disabled"));
    }
    g_divBar = 0;

    //--- template first: the analyst aligns its MA/RSI periods with fxmopol.tpl
   if(InpUseTemplate)
   {
      g_template.Load(InpTemplateName);
      PrintFormat("Template: %s", g_template.Describe());

      //--- NOTE: ChartApplyTemplate is intentionally NOT called. It applies the
      //--- full .tpl (symbol=EURUSD, period=M1) which changes the chart and
      //--- forces the EA to deinit. Apply fxmopol.tpl manually before attaching
      //--- the EA: right-click chart -> Template -> Load Template -> fxmopol.tpl.
      //--- EA drawings (panel, sim plan, dynamic plan, analyst overlay) use
      //--- colors from g_template.* read from the .ini spec.
      if(g_template.Loaded())
      {
         PrintFormat("Template colors loaded: candleUp=%d candleDown=%d bg=%d fg=%d grid=%d",
                     (int)g_template.CandleUp(), (int)g_template.CandleDown(),
                     (int)g_template.Bg(), (int)g_template.Fg(), (int)g_template.Grid());
      }
   }

   //--- dynamic market analyst: analysis + visualisation only (never trades)
   if(InpAnalystEnabled)
   {
      //--- resolve MA/RSI periods: the template wins when it carries 3 MAs + RSI
      int planFast       = InpAnalystFastMaPeriod;
      int planMid        = InpAnalystMidMaPeriod;
      int planSlow       = InpAnalystSlowMaPeriod;
      int planRsi        = InpAnalystRsiPeriod;
      int planFastMethod = 1;                        // template MA21  = EMA
      int planMidMethod  = 1;                        // template MA50  = EMA
      int planSlowMethod = InpAnalystSlowMaMethod;   // template MA100 = SMMA(2)
      if(InpUseTemplate && g_template.Loaded() && g_template.MaCount() >= 3)
      {
         int p[3], mm[3];
         p[0] = g_template.MaPeriod(0);  mm[0] = g_template.MaMethod(0);
         p[1] = g_template.MaPeriod(1);  mm[1] = g_template.MaMethod(1);
         p[2] = g_template.MaPeriod(2);  mm[2] = g_template.MaMethod(2);
         //--- sort ascending by period: smallest = fast, middle = mid, largest = slow
         for(int a = 0; a < 2; a++)
            for(int b = a + 1; b < 3; b++)
               if(p[b] < p[a])
               {
                  int tmpP = p[a]; p[a] = p[b]; p[b] = tmpP;
                  int tmpM = mm[a]; mm[a] = mm[b]; mm[b] = tmpM;
               }
         if(p[0] > 0 && p[1] > 0 && p[2] > 0)
         {
            planFast = p[0];  planFastMethod = mm[0];
            planMid  = p[1];  planMidMethod  = mm[1];
            planSlow = p[2];  planSlowMethod = mm[2];
         }
         if(g_template.RsiPeriod() > 0)
            planRsi = g_template.RsiPeriod();
         PrintFormat("Analyst: aligned to template MA %d(m%d)/%d(m%d)/%d(m%d) RSI %d (fractals on)",
                     planFast, planFastMethod, planMid, planMidMethod,
                     planSlow, planSlowMethod, planRsi);
      }

      if(!g_plan.Init(_Symbol, _Period,
                      planSlow, planSlowMethod,
                      planFast, planMid,
                      InpAnalystAtrPeriod, planRsi,
                      InpAnalystStopAtr, InpAnalystTpAtr, InpAnalystScaleStepAtr,
                      planFastMethod, planMidMethod))
         SovLogWarn("OnInit: dynamic analyst init failed - analysis/visuals disabled, trading unaffected");
   }
   if(InpAiOverlay)
   {
      g_ai.Load(_Symbol, SovTfText(_Period));
      PrintFormat("AI overlay: %s", g_ai.Describe());
   }
   if(InpShowVisuals && _Period < PERIOD_D1)
      Print("SOV WARN: this strategy rebalances monthly/daily - attach it to a D1 chart ",
            "or the panel will look idle.");

    if(InpStructRideEnabled && _Period == PERIOD_H1)
    {
      g_structAtrHandle = iATR(_Symbol, PERIOD_H1, 14);
      if(g_structAtrHandle == INVALID_HANDLE)
      {
         SovLogError("OnInit", "struct ride ATR handle failed");
         return(INIT_FAILED);
      }

      if(!g_structRide.Init(_Symbol, SOVEREIGN_MAGIC,
                            InpStructRideFractalK, InpStructRideMaxHoldBars,
                            InpStructRideStopAtr, InpStructRideTpAtr,
                            InpStructRideRiskPct, InpStructRideCostPts,
                            InpStructRideUseStructureFilter))
      {
         SovLogError("OnInit", "struct ride init failed");
         return(INIT_FAILED);
      }
    }

   //--- RSI band init: standalone mode uses chart timeframe; hedge blend (D1) uses H1 satellite
   bool rsiBandInitNeeded = InpRsiBandEnabled || (InpHedgeBlendEnabled && _Period == PERIOD_D1);
   ENUM_TIMEFRAMES rsiBandTf = (InpRsiBandEnabled) ? _Period : PERIOD_H1;

   if(rsiBandInitNeeded)
   {
      //--- Parameters are resolved per SYMBOL *AND* TIMEFRAME (see
      //--- SovereignRsiProfiles.mqh). The old code keyed on _Symbol only, so
      //--- BTCUSD carried mid=30 onto M1/M5/M15/M30/H1 alike and parked both
      //--- entry triggers around RSI 30 - a rule that almost never fires.
      //--- Measured on the broker tape, BTCUSD H1 at mid=30 is -76.85 / -26.06
      //--- / -7.43% net; mid=50 is the shape that survives.
      SovRsiProfile prof = SovResolveRsiProfile(_Symbol, rsiBandTf, InpRsiBandPerTimeframe);

      //--- manual override for THIS chart only (empty = use the table)
      SovRsiProfile manual;
      SovProfileDefault(manual, _Symbol, rsiBandTf);
      if(SovParseRsiOverride(InpRsiBandOverrideProfile, manual))
      {
         prof = manual;
      }
      else if(!InpRsiBandAutoDetect)
      {
         //--- auto-detect off: honour the dialog inputs verbatim
         SovSetProfile(prof, InpRsiBandRsiPeriod, InpRsiBandMid, InpRsiBandHi, InpRsiBandLo,
                       InpRsiBandDeadband, InpRsiBandMargin, InpRsiBandMinHoldBars,
                       InpRsiBandMaxBars, InpRsiBandCooldownBars, false,
                       "manual dialog inputs (auto-detect off)");
      }

      int    rsiPeriod   = prof.period;
      int    rsiMid      = prof.mid;
      int    rsiHi       = prof.hi;
      int    rsiLo       = prof.lo;
      double rsiDb       = prof.deadband;
      int    rsiMaxBars  = prof.maxBars;
      double rsiMargin   = prof.margin;
      int    rsiCooldown = (prof.cooldown > 0) ? prof.cooldown : InpRsiBandCooldownBars;

      PrintFormat("Sovereign RSI band profile | %s %s | RSI%d mid=%d hi=%d lo=%d db=%.2f margin=%.1f minHold=%d maxBars=%d cooldown=%d | certified=%s | %s",
                  _Symbol, SovTfName(rsiBandTf), rsiPeriod, rsiMid, rsiHi, rsiLo,
                  rsiDb, rsiMargin, prof.minHold, rsiMaxBars, rsiCooldown,
                  (prof.certified ? "YES" : "NO"), prof.source);

      if(!prof.certified)
         PrintFormat("Sovereign RSI band %s %s: profile is UNCERTIFIED - the EA trades the rule, but this symbol+timeframe has no validated edge. Source: %s",
                     _Symbol, SovTfName(rsiBandTf), prof.source);


       if(!g_rsiBand.Init(_Symbol, SOVEREIGN_MAGIC,
                          rsiBandTf,
                          InpRsiBandRiskPct, InpRsiBandMinHoldBars,
                          rsiMargin, InpRsiBandSpreadGatePct,
                          rsiPeriod, rsiMid, rsiHi, rsiLo, rsiDb,
                          rsiMaxBars, InpRsiBandAtrPctMax, InpRsiBandVerboseLog,
                          InpRsiBandMaxMACross, InpRsiBandMACrossWindow,
                           InpRsiBandMinConsecutiveLong, InpRsiBandMinConsecutiveShort,
                            InpRsiBandRequireEma21Slope, InpRsiBandExitOnEmaBreak, InpRsiBandEmaBreakBufferAtr, InpRsiBandLongOnly))
        {
           SovLogError("OnInit", "RSI band init failed");
           return(INIT_FAILED);
        }

         //--- static strategy parameters (also refreshed each tick in OnTick)
         g_rsiBand.SetIntraBar(InpRsiBandIntraBar);
         g_rsiBand.SetStopAtr(InpRsiBandStopAtr);
        g_rsiBand.SetTrailAtrMult(InpTrailAtrMultiplier);
        g_rsiBand.SetRsiExhaustionLevel(InpRsiExhaustionLevel);
        g_rsiBand.SetMaxPyramidLayers(InpMaxPyramidLayers);
         g_rsiBand.SetPyramidExec(InpPyramidExecEnabled, InpMaxPyramidLayers, InpPyramidScaleStepAtr);
         g_rsiBand.SetPyramidAllowLoss(InpPyramidAllowLoss);
         g_rsiBand.SetTrailMinStep(InpTrailMinStepPts);
         g_rsiBand.SetBreakevenAtR(InpBreakevenAtR);
         g_rsiBand.SetBreakevenBufferPts(InpBreakevenBufferPts);
          g_rsiBand.SetMaxLotSize(InpMaxLotSize);
          g_rsiBand.SetVerifySLTP(InpVerifySLTPOnEntry);
          g_rsiBand.SetMaxPositionAgeBars(InpMaxPositionAgeBars);
          g_rsiBand.SetEmaBreakBufferAtr(InpRsiBandEmaBreakBufferAtr);

         //--- RSI band: bearish engulfing + RSI overbought short
         g_rsiBand.SetEngulfingEnabled(InpRsiBandEngulfingEnabled);
         g_rsiBand.SetEngulfingRsiLevel(InpRsiBandEngulfingRsiLevel);
         g_rsiBand.SetEngulfingStopBufferPts(InpRsiBandEngulfingStopBufferPts);
         g_rsiBand.SetEngulfingAllRedExit(InpRsiBandEngulfingAllRedExit);
         g_rsiBand.SetEngulfingNoKissEma21(InpRsiBandEngulfingNoKissEma21);

         //--- RSI band: green-red entry (fade EMA21 bounce, wait for confirming candle)
         g_rsiBand.SetGreenRedEnabled(InpRsiBandGreenRedEnabled);
         g_rsiBand.SetGreenRedRsiLevel(InpRsiBandGreenRedRsiLevel);
         g_rsiBand.SetGreenRedWaitForRed(InpRsiBandGreenRedWaitForRed);
         g_rsiBand.SetGreenRedMaxGreenCandles(InpRsiBandGreenRedMaxCandles);
         g_rsiBand.SetGreenRedEmaTolerance(InpRsiBandGreenRedEmaTol);

         //--- EMA21 trend entry mode
         g_rsiBand.SetEmaTrendEnabled(InpRsiBandEmaTrendEnabled);
         g_rsiBand.SetEmaTrendRsiLevel(InpRsiBandEmaTrendRsiLevel);
         g_rsiBand.SetEmaTrendSlopeBars(InpRsiBandEmaTrendSlopeBars);
         g_rsiBand.SetEmaTrendSlopeMin(InpRsiBandEmaTrendSlopeMin);
         g_rsiBand.SetEmaTrendBodyMinAtr(InpRsiBandEmaTrendBodyMinAtr);
         g_rsiBand.SetEmaTrendConsecutive(InpRsiBandEmaTrendConsecutive);
         g_rsiBand.SetEmaTrendHTFFilter(InpRsiBandEmaTrendHTFFilter);
         g_rsiBand.SetEmaTrendHTF((ENUM_TIMEFRAMES)InpRsiBandEmaTrendHTF);
         g_rsiBand.SetEmaTrendStopMode(InpRsiBandEmaTrendStopMode);
         g_rsiBand.SetEmaTrendStopAtrMult(InpRsiBandEmaTrendStopAtrMult);
          g_rsiBand.SetEmaTrendEmaDistAtr(InpRsiBandEmaTrendEmaDistAtr);
           g_rsiBand.SetReflexMinStreak(InpRsiBandReflexMinStreak);

          //--- Early trend-change setters
          g_rsiBand.SetEarlyTrendEnabled(InpRsiBandEarlyTrendEnabled);
          g_rsiBand.SetEarlyTrendHoldBars(InpRsiBandEarlyTrendHoldBars);
          g_rsiBand.SetEarlyTrendSizeMult(InpRsiBandEarlyTrendSizeMult);
          g_rsiBand.SetEarlyTrendRsiMax(InpRsiBandEarlyTrendRsiMax);
          g_rsiBand.SetEarlyTrendRsiMin(InpRsiBandEarlyTrendRsiMin);
          g_rsiBand.SetStructureFilterEnabled(InpRsiBandStructureFilterEnabled);

          //--- State recovery: re-adopt any live Sovereign positions so trailing /
         //--- RSI-exit management keeps working after a terminal restart or
         //--- timeframe change (positions are never orphaned).
         g_rsiBand.RecoverState(trade);

         //--- Wire institutional risk modules into RSI band
         g_rsiBand.SetPortfolioRisk(&g_portRisk);
         g_rsiBand.SetSmartExecution(&g_exec, InpSmartExecutionEnabled);
     }

    //--- Institutional risk modules
    if(InpPortfolioRiskEnabled)
    {
       if(!g_portRisk.Init("Sovereign", SOVEREIGN_MAGIC,
                           InpPortVarLimitPct, InpPortCorrLimit,
                           InpPortMaxCcyExposurePct, InpPortKellyFraction,
                           InpPortDailyLossLimitPct, InpPortWeeklyLossLimitPct,
                           InpPortMaxDDFromPeakPct))
       {
          SovLogError("OnInit", "portfolio risk init failed");
          return(INIT_FAILED);
       }
       SovLogInfo("OnInit: portfolio risk engine ENABLED");
    }
    if(InpSmartExecutionEnabled)
    {
       if(!g_exec.Init("Sovereign",
                       InpExecDefaultChildren, InpExecSliceIntervalSec,
                       InpExecSlippageMult, InpExecIceberg,
                       InpExecIcebergShowPct, InpExecAdaptiveSlowdown))
       {
          SovLogError("OnInit", "smart execution init failed");
          return(INIT_FAILED);
       }
       SovLogInfo("OnInit: smart execution ENABLED");
    }
    if(InpDiscretionEnabled)
    {
       if(!g_disc.Init("Sovereign"))
       {
          SovLogError("OnInit", "discretion init failed");
          return(INIT_FAILED);
       }
       SovLogInfo("OnInit: discretion/veto layer ENABLED");
    }

    if(InpEmaHarvestEnabled)
    {
       if(!g_emaHarvest.Init(_Symbol, SOVEREIGN_MAGIC, _Period,
                             InpEmaHarvestPeriod, InpEmaHarvestRiskPct,
                             InpEmaHarvestMinConsecLong, InpEmaHarvestMinConsecShort,
                             InpEmaHarvestExitOnEmaBreak,
                             InpEmaHarvestRsiExitLong, InpEmaHarvestRsiExitShort,
                             InpEmaHarvestMaxHold, InpEmaHarvestTrailAtr,
                             InpEmaHarvestRsiEntryMax))
       {
        SovLogError("OnInit", "EMA harvest init failed");
           return(INIT_FAILED);
        }
     }

    if(InpEmaRbEnabled)
    {
       g_emaRbHema = iMA(_Symbol, _Period, InpEmaRbPeriod, 0, MODE_EMA, PRICE_CLOSE);
       g_emaRbHrsi = iRSI(_Symbol, _Period, InpEmaRbRsiPeriod, PRICE_CLOSE);
       g_emaRbHatr = iATR(_Symbol, _Period, 14);
       if(g_emaRbHema == INVALID_HANDLE || g_emaRbHrsi == INVALID_HANDLE || g_emaRbHatr == INVALID_HANDLE)
       {
          SovLogError("OnInit", "EMA21 rubber band indicator handle creation failed");
          return(INIT_FAILED);
       }
       ArraySetAsSeries(g_emaRbBufEma, true);
       ArraySetAsSeries(g_emaRbBufRsi, true);
       ArraySetAsSeries(g_emaRbBufAtr, true);
       ArraySetAsSeries(g_emaRbBufCl, true);
       ArraySetAsSeries(g_emaRbBufOp, true);
       ArraySetAsSeries(g_emaRbBufHi, true);
       ArraySetAsSeries(g_emaRbBufLo, true);
       ArrayResize(g_emaRbBufEma, 100);
       ArrayResize(g_emaRbBufRsi, 100);
       ArrayResize(g_emaRbBufAtr, 100);
       ArrayResize(g_emaRbBufCl, 100);
       ArrayResize(g_emaRbBufOp, 100);
       ArrayResize(g_emaRbBufHi, 100);
       ArrayResize(g_emaRbBufLo, 100);
       SovLogInfo("OnInit: EMA21 rubber band ENABLED | ema=" + IntegerToString(InpEmaRbPeriod) +
                  " entryAtr=" + DoubleToString(InpEmaRbEntryAtr,1) + " exitAtr=" + DoubleToString(InpEmaRbExitAtr,1) +
                  " rsiMax=" + IntegerToString(InpEmaRbRsiMax) + " rsiMin=" + IntegerToString(InpEmaRbRsiMin) +
                  " stopAtr=" + DoubleToString(InpEmaRbStopAtr,1) + " tpAtr=" + DoubleToString(InpEmaRbTpAtr,1));
    }

    if(InpHedgeBlendEnabled && _Period == PERIOD_D1)
    {
       if(!g_hedgeBlend.Init(g_carry, g_rsiBand, g_perf, g_traffic,
                             g_audit, trade, g_century, SOVEREIGN_MAGIC))
       {
          SovLogError("OnInit", "hedge blend init failed");
          return(INIT_FAILED);
       }
    }

   if(InpRegimeFilterEnabled && _Period == PERIOD_H1)
   {
      string symList[];
      int nSyms = StringSplit(InpRegimeFilterSymbols, ',', symList);
      bool symAllowed = false;
      for(int si = 0; si < nSyms; si++)
      {
         string s = symList[si];
         StringTrimLeft(s);
          StringTrimRight(s);
         if(s == _Symbol)
         {
            symAllowed = true;
            break;
         }
      }
      if(symAllowed)
      {
           if(!g_regimeFilter.Init(_Symbol, SOVEREIGN_MAGIC,
                                   InpRegimeFilterMode,
                                   InpRegimeFilterCompressAtr, InpRegimeFilterVolPctile,
                                   InpRegimeFilterAdxMin, InpRegimeFilterSessionCut,
                                   InpRegimeFilterMaxSpread, InpRegimeFilterAtrRatio,
                                   InpRegimeFilterExtAtr, InpRegimeFilterStopAtr,
                                   InpRegimeFilterTpAtr, InpRegimeFilterRiskPct,
                                   InpRegimeFilterMaxHoldBars))
          {
             SovLogError("OnInit", "regime filter init failed");
             return(INIT_FAILED);
          }
      }
   }

   if(InpCompressionEnabled && _Period == PERIOD_H1)
   {
      string symList[];
      int nSyms = StringSplit(InpCompressionSymbols, ',', symList);
      bool symAllowed = false;
      for(int si = 0; si < nSyms; si++)
      {
         string s = symList[si];
         StringTrimLeft(s);
          StringTrimRight(s);
         if(s == _Symbol)
         {
            symAllowed = true;
            break;
         }
      }
      if(symAllowed)
      {
          if(!g_compressionRubber.Init(_Symbol, SOVEREIGN_MAGIC,
                                       InpCompressionEmaFast, InpCompressionEmaMid, InpCompressionEmaSlow,
                                       InpCompressionCompressAtr, InpCompressionExtAtr,
                                       InpCompressionStopAtr, InpCompressionTpAtr,
                                       InpCompressionRiskPct, InpCompressionMaxHoldBars))
          {
             SovLogError("OnInit", "compression rubberband init failed");
             return(INIT_FAILED);
          }
      }
   }

   if(InpStrategy == SOV_CARRY_MOM)
      Print(InpUseSovereignStackV1
            ? "Sovereign EA initialized: Stack v1 carry+momentum (neutral + VIX scale)"
            : "Sovereign EA initialized: monthly carry + 12m momentum (legacy filter)");
   else if(InpStrategy == SOV_XSEC_MR)
      Print("Sovereign EA initialized: daily cross-sectional mean-reversion + carry*");
   else if(InpStrategy == SOV_RSI_BAND)
      Print("Sovereign EA initialized: RSI band per-bar (any TF)");
   else if(InpStructRideEnabled)
      Print("Sovereign EA initialized: H1 structure ride overlay (USDJPY certified)");
    PrintFormat("Sovereign EA: InpStrategy=%d (0=CARRY_MOM, 3=RSI_BAND), InpRsiBandEnabled=%d, InpStructRideEnabled=%d, InpEmaHarvestEnabled=%d",
                (int)InpStrategy, (int)InpRsiBandEnabled, (int)InpStructRideEnabled, (int)InpEmaHarvestEnabled);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
bool DayRolled(datetime now)
{
   MqlDateTime dt;
   TimeToStruct(now, dt);
   dt.hour = 0;
   dt.min  = 0;
   dt.sec  = 0;
   datetime key = StructToTime(dt);
   if(key != g_dayKey)
   {
      g_dayKey = key;
      g_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| UpdatePyramidState — count Sovereign legs on this symbol and sum |
//| their net profit (PROFIT + SWAP + COMMISSION).                   |
//|                                                                  |
//| g_pyramidArmed is the profit gate used by the pyramiding guard:  |
//|   no legs  -> armed (an initial entry is always allowed)         |
//|   all legs net positive -> armed (a scale-in is allowed)         |
//|   any leg net <= 0      -> blocked                               |
//+------------------------------------------------------------------+
void UpdatePyramidState()
{
   g_pyramidLegs = 0;
   g_pyramidNetProfit = 0.0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk <= 0)
         continue;
      if(!PositionSelectByTicket(tk))
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != SOVEREIGN_MAGIC)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      g_pyramidLegs++;
      g_pyramidNetProfit += SovPositionNetProfit(tk);
   }

   g_pyramidArmed = (g_pyramidLegs == 0) || (g_pyramidNetProfit > 0.0);
}

//+------------------------------------------------------------------+
//| CheckPortfolioHeat — portfolio-wide correlation & exposure guard   |
//| Computes aggregate % of equity at risk across all Sovereign        |
//| positions and net directional exposure per currency. Returns true  |
//| when within caps (new entries allowed).                            |
//+------------------------------------------------------------------+
bool CheckPortfolioHeat()
{
   g_portfolioRiskPct = 0.0;
   g_portfolioHeatBlocked = false;
   g_portfolioHeatDetail = "";

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity <= 0.0) return true;

   //--- per-currency net exposure accumulators
   string ccy[];
   double ccyExp[];
   int nCcy = 0;

   double totalRiskMoney = 0.0;
   int sovPos = 0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk <= 0) continue;
      if(!PositionSelectByTicket(tk)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != SOVEREIGN_MAGIC) continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      double vol = PositionGetDouble(POSITION_VOLUME);
      double px  = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl  = PositionGetDouble(POSITION_SL);
      long type  = PositionGetInteger(POSITION_TYPE);
      int side = (type == POSITION_TYPE_BUY) ? 1 : -1;
      sovPos++;

      //--- risk money for this position (stop distance × tick value)
      double tickSize = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
      double tickVal  = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
      double stopDist = MathAbs(px - sl);
      double riskMoney = 0.0;
      if(stopDist > 0.0 && tickSize > 0.0 && tickVal > 0.0)
         riskMoney = vol * (stopDist / tickSize) * tickVal;
      else
         riskMoney = vol * px * 0.01;   // no SL: assume 1% adverse move
      totalRiskMoney += riskMoney;

      //--- per-currency exposure (only for 6-char symbols like EURUSD, BTCUSD)
      if(StringLen(sym) >= 6)
      {
         string base = StringSubstr(sym, 0, 3);
         string quote = StringSubstr(sym, 3, 3);
         AddCcyExposure(ccy, ccyExp, nCcy, base, (double)side * vol);
         AddCcyExposure(ccy, ccyExp, nCcy, quote, (double)side * vol);
      }
   }

   g_portfolioRiskPct = (equity > 0.0) ? totalRiskMoney / equity * 100.0 : 0.0;

   //--- aggregate portfolio risk cap
   if(InpMaxPortfolioRiskPct > 0.0 && g_portfolioRiskPct >= InpMaxPortfolioRiskPct)
   {
      g_portfolioHeatBlocked = true;
      g_portfolioHeatDetail = "portfolio risk " + DoubleToString(g_portfolioRiskPct,2) +
                              "% >= cap " + DoubleToString(InpMaxPortfolioRiskPct,2) + "%";
      return false;
   }

   //--- per-currency net exposure cap
   if(InpMaxNetExposurePerCCY > 0.0)
   {
      for(int k = 0; k < nCcy; k++)
      {
         if(MathAbs(ccyExp[k]) >= InpMaxNetExposurePerCCY)
         {
            g_portfolioHeatBlocked = true;
            g_portfolioHeatDetail = "net exposure " + ccy[k] + " = " +
                                    DoubleToString(ccyExp[k],2) +
                                    " lots >= cap " + DoubleToString(InpMaxNetExposurePerCCY,2);
            return false;
         }
      }
   }

   return true;
}

//+------------------------------------------------------------------+
//| AddCcyExposure — accumulate net exposure for a currency          |
//+------------------------------------------------------------------+
void AddCcyExposure(string &ccy[], double &exp[], int &n, const string c, double v)
{
   for(int k = 0; k < n; k++)
   {
      if(ccy[k] == c)
      {
         exp[k] += v;
         return;
      }
   }
   //--- new currency
   int sz = ArraySize(ccy);
   ArrayResize(ccy, sz + 1);
   ArrayResize(exp, sz + 1);
   ccy[sz] = c;
   exp[sz] = v;
   n++;
}

//+------------------------------------------------------------------+
//| CheckAccountDrawdown — high-watermark kill switch.                 |
//| Halts all new entries if equity drops X% from account peak.        |
//+------------------------------------------------------------------+
bool CheckAccountDrawdown()
{
   if(InpAccountMaxDDFromPeakPct <= 0.0) return true;

   //--- throttle to once per second
   datetime now = TimeCurrent();
   if(now - g_accountDDCheckTime < 1) return !g_dailyProfitTargetHit;
   g_accountDDCheckTime = now;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity <= 0.0) return true;

   //--- update peak equity (high watermark)
   if(g_accountPeakEquity == 0.0 || equity > g_accountPeakEquity)
      g_accountPeakEquity = equity;

   double ddPct = (g_accountPeakEquity - equity) / g_accountPeakEquity * 100.0;
   if(ddPct >= InpAccountMaxDDFromPeakPct)
   {
      g_lastBlockReason = "account drawdown (" + DoubleToString(ddPct,2) + "% >= " + DoubleToString(InpAccountMaxDDFromPeakPct,2) + "%)";
      g_lastBlockTime   = now;
      PrintFormat("Sovereign ACCOUNT DD KILL SWITCH | %s | equity drawdown %.2f%% >= limit %.2f%% — halting all entries",
                  _Symbol, ddPct, InpAccountMaxDDFromPeakPct);
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| CheckFreeMargin — block entries when free margin too low.          |
//+------------------------------------------------------------------+
bool CheckFreeMargin()
{
   if(InpMinFreeMarginPct <= 0.0) return true;

   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double freeMrg = AccountInfoDouble(ACCOUNT_FREEMARGIN);

   if(equity <= 0.0) return true;

   double freeMarginPct = (equity > 0.0) ? freeMrg / equity * 100.0 : 100.0;
   if(freeMarginPct < InpMinFreeMarginPct)
   {
      g_lastBlockReason = "free margin (" + DoubleToString(freeMarginPct,2) + "% < " + DoubleToString(InpMinFreeMarginPct,2) + "%)";
      g_lastBlockTime   = TimeCurrent();
      PrintFormat("Sovereign FREE MARGIN GUARD | %s | free margin %.2f%% < min %.2f%% — blocking new entries",
                  _Symbol, freeMarginPct, InpMinFreeMarginPct);
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| CapLotSize — enforce absolute max lot size per trade.              |
//+------------------------------------------------------------------+
double CapLotSize(double lot)
{
   if(InpMaxLotSize <= 0.0) return lot;
   if(lot > InpMaxLotSize)
   {
      PrintFormat("Sovereign MAX LOT CAP | %s | requested lot %.2f capped to max %.2f",
                  _Symbol, lot, InpMaxLotSize);
      return InpMaxLotSize;
   }
   return lot;
}

//+------------------------------------------------------------------+
//| CountTotalSovPositions — count Sovereign positions across ALL symbols.|
//+------------------------------------------------------------------+
int CountTotalSovPositions()
{
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk <= 0) continue;
      if(!PositionSelectByTicket(tk)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != SOVEREIGN_MAGIC) continue;
      n++;
   }
   return n;
}

//+------------------------------------------------------------------+
//| CheckTotalPositionsCap — block entries when account-wide cap reached.|
//+------------------------------------------------------------------+
bool CheckTotalPositionsCap()
{
   if(InpMaxTotalSovPositions <= 0) return true;

   int total = CountTotalSovPositions();
   if(total >= InpMaxTotalSovPositions)
   {
      g_lastBlockReason = "total positions (" + IntegerToString(total) + "/" + IntegerToString(InpMaxTotalSovPositions) + ")";
      g_lastBlockTime   = TimeCurrent();
      PrintFormat("Sovereign TOTAL POSITION CAP | %s | %d/%d positions across all symbols — blocking new entries",
                  _Symbol, total, InpMaxTotalSovPositions);
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| CheckDailyProfitTarget — stop new entries when day profit target hit.|
//+------------------------------------------------------------------+
bool CheckDailyProfitTarget()
{
   if(InpDailyProfitTargetPct <= 0.0) return true;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity <= 0.0 || g_dayStartEquity <= 0.0) return true;

   double dayPct = (equity - g_dayStartEquity) / g_dayStartEquity * 100.0;
   if(dayPct >= InpDailyProfitTargetPct && !g_dailyProfitTargetHit)
   {
      g_dailyProfitTargetHit = true;
      g_lastBlockReason = "daily profit target (" + DoubleToString(dayPct,2) + "% >= " + DoubleToString(InpDailyProfitTargetPct,2) + "%)";
      g_lastBlockTime   = TimeCurrent();
      PrintFormat("Sovereign DAILY PROFIT TARGET | %s | up %.2f%% >= target %.2f%% — no more entries today",
                  _Symbol, dayPct, InpDailyProfitTargetPct);
   }
   return !g_dailyProfitTargetHit;
}

//+------------------------------------------------------------------+
//| CheckPositionAge — close positions older than max age bars.        |
//+ Called from OnNewBar position management for each open position. |
//+------------------------------------------------------------------+
void CheckPositionAge(CTrade &t, const ulong ticket, const datetime openTime, const int side)
{
   if(InpMaxPositionAgeBars <= 0) return;

   datetime now = TimeCurrent();
   int secondsPerPeriod = PeriodSeconds(_Period);
   if(secondsPerPeriod <= 0) return;

   int barsHeld = (int)((now - openTime) / secondsPerPeriod);
   if(barsHeld >= InpMaxPositionAgeBars)
   {
      string sideTxt = (side > 0) ? "long" : "short";
      if(t.PositionClose(ticket))
      {
         PrintFormat("Sovereign TIME STOP | %s | %s position ticket=%I64u closed after %d bars (max=%d)",
                     _Symbol, sideTxt, (long)ticket, barsHeld, InpMaxPositionAgeBars);
      }
      else
      {
         PrintFormat("Sovereign TIME STOP CLOSE FAILED | %s | ticket=%I64u retcode=%d err=%d",
                     _Symbol, (long)ticket, (int)t.ResultRetcode(), GetLastError());
      }
   }
}

//+------------------------------------------------------------------+
//| VerifySLTP — confirm SL/TP were actually set on the broker.        |
//+ Called right after order placement in OpenPosition.               |
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
      PrintFormat("Sovereign SL/TP MISMATCH | %s | ticket=%I64u reqSL=%.5f reqTP=%.5f actualSL=%.5f actualTP=%.5f",
                  _Symbol, (long)ticket, reqSL, reqTP, sl, tp);
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| CheckNewsBlackout — block new entries around high-impact calendar  |
//| events using MT5's native CalendarValueHistory API.               |
//| Caches results for g_newsCacheSeconds to avoid repeated API calls. |
//| Returns true when entries are allowed (no blocking event).         |
//+------------------------------------------------------------------+
bool CheckNewsBlackout()
{
   g_newsBlocked   = false;
   g_newsBlockDetail = "";

   if(!InpNewsBlackoutEnabled) return true;

   //--- throttle calendar refresh
   datetime now = TimeCurrent();
   static datetime g_lastCalendarRefresh = 0;
   static MqlCalendarValue g_calEvents[];
   static int   g_calCount = 0;

   bool refresh = (g_lastCalendarRefresh == 0) || (now - g_lastCalendarRefresh >= g_newsCacheSeconds);
   if(refresh)
   {
      datetime from = now - 86400 * 2;   // look back 2 days
      datetime to   = now + 86400 * 2;   // look ahead 2 days
      g_calCount = CalendarValueHistory(g_calEvents, from, to);
      if(g_calCount < 0)
      {
         g_calCount = 0;
         SovLogWarn("CheckNewsBlackout: CalendarValueHistory failed err=" + IntegerToString(GetLastError()));
         return true;   // fail open — don't block on API error
      }
      g_lastCalendarRefresh = now;
   }

   //--- determine which currencies to watch
   string watchCcy[];
   int nWatch = 0;

   if(StringLen(InpNewsBlackoutCurrencies) > 0)
   {
      string parts[];
      int np = StringSplit(InpNewsBlackoutCurrencies, ',', parts);
      for(int k = 0; k < np; k++)
      {
         string c = parts[k];
         StringTrimLeft(c); StringTrimRight(c);
         if(StringLen(c) > 0)
         {
            int sz = ArraySize(watchCcy);
            ArrayResize(watchCcy, sz + 1);
            watchCcy[sz] = c;
            nWatch++;
         }
      }
   }
   else
   {
      //--- derive from chart symbol: EURUSD -> EUR, USD
      string sym = _Symbol;
      if(StringLen(sym) >= 6)
      {
         int sz = ArraySize(watchCcy);
         ArrayResize(watchCcy, sz + 1);
         watchCcy[sz] = StringSubstr(sym, 0, 3);
         nWatch++;
         sz = ArraySize(watchCcy);
         ArrayResize(watchCcy, sz + 1);
         watchCcy[sz] = StringSubstr(sym, 3, 3);
         nWatch++;
      }
   }

   //--- scan calendar events for blocking ones (time-based only;
   //   any calendar event within the pre/post window blocks entries)
   long beforeSec = (long)InpNewsBlackoutMinsBefore * 60;
   long afterSec  = (long)InpNewsBlackoutMinsAfter  * 60;

   for(int k = 0; k < g_calCount; k++)
   {
      //--- time window filter: [event - before, event + after]
      datetime evTime = (datetime)g_calEvents[k].time;
      if(evTime < now - beforeSec) continue;   // already passed the pre-window
      if(evTime > now + afterSec)  continue;   // too far in future

      g_newsBlocked   = true;
      g_newsBlockDetail = "calendar event at " + TimeToString(evTime, TIME_DATE|TIME_MINUTES);
      break;
   }

   return !g_newsBlocked;
}

void OnTick()
{
   if(SovCheckStopped("OnTick")) return;

   SovResetLastError();
    SovLogDebug(StringFormat("Sovereign EA [%s]: OnTick heartbeat Bid=%.5f Ask=%.5f Bars=%d",
                _Symbol, SymbolInfoDouble(_Symbol, SYMBOL_BID), SymbolInfoDouble(_Symbol, SYMBOL_ASK), Bars(_Symbol, _Period)));
   g_python.Poll();
   g_telemetry.Poll();

   //--- position state must be current before any strategy entry check
   UpdatePyramidState();

   //--- refresh the symbol-scaled spread caps once per bar (traffic light + guard).
   //--- A fixed FX-calibrated cap hard-vetoes every crypto/vol-index entry, which
   //--- is why BTCUSD never traded.
   {
      static datetime lastSpreadCapBar = 0;
      datetime capBar = iTime(_Symbol, _Period, 0);
      if(capBar != lastSpreadCapBar)
      {
         lastSpreadCapBar = capBar;
         SovSampleSpreadRing();                       // one live sample per bar
         int tlCap  = SovEffectiveSpreadCap(InpMaxSpread);
         int eaCap  = SovEffectiveSpreadCap(InpMaxSpreadForTrade);
         if(tlCap > 0) g_traffic.SetMaxSpread(tlCap);
         if(tlCap != InpMaxSpread || (eaCap > 0 && eaCap != InpMaxSpreadForTrade))
         {
            PrintFormat("Sovereign SPREAD CAP | %s | median=%d pts | traffic-light cap %d (input %d) | entry cap %d (input %d) | current spread %d pts",
                        _Symbol, SovCachedMedianSpreadPts(200), tlCap, InpMaxSpread, eaCap, InpMaxSpreadForTrade,
                        (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD));
         }
      }
   }

    //--- process Python->EA commands
   if(g_python.HasCommand())
   {
      string cmd = g_python.Command();
      string cmdSym = g_python.CommandSymbol();
      double val = g_python.CommandValue();

      //--- per-symbol targeting: command applies if symbol matches or is wildcard/all
      bool cmdApplies = false;
      if(cmdSym == "" || cmdSym == "*")
         cmdApplies = true;
      else if(StringCompare(cmdSym, _Symbol, 0) == 0)
         cmdApplies = true;

      if(cmdApplies)
      {
         PrintFormat("Sovereign telemetry: command %s applied to %s (symbol filter=%s)",
                     cmd, _Symbol, (cmdSym == "" ? "<all>" : cmdSym));
         if(cmd == "PAUSE")
            g_pythonPaused = true;
         else if(cmd == "RESUME")
            g_pythonPaused = false;
         else if(cmd == "FLAT_ALL")
            g_flatAllRequested = true;
         else if(cmd == "RISK_SCALE")
            g_pythonRiskScale = MathMax(0.0, MathMin(10.0, val));
         else if(cmd == "RESET")
         {
            g_pythonPaused = false;
            g_flatAllRequested = false;
            g_pythonRiskScale = 1.0;
         }
      }
      else
      {
         PrintFormat("Sovereign telemetry: command %s ignored (symbol filter=%s, chart=%s)",
                     cmd, cmdSym, _Symbol);
      }
      g_python.ClearCommand();
   }

    //--- Python risk scale scales the effective risk (applied inside strategy sizing)
    //--- stored in g_pythonRiskScale, read by strategies via InpRiskPerPosition * g_pythonRiskScale

    g_perf.OnTick();
    g_portRisk.OnTick();
    g_exec.OnTick();
    g_disc.OnTick();
   g_century.Reload(false);
   if(InpUseTemplate && InpTemplateHotReload)
      g_template.ReloadIfChanged();
   if(InpAiOverlay)
      g_ai.ReloadIfChanged();

    datetime now = TimeCurrent();
    string syms[];
    int n = StringSplit(InpSymbols, ',', syms);
    if(DayRolled(now) && InpGovernorLogDaily && InpEnablePerformanceGovernor)
       Print(g_perf.StatusLine());
    double equity = AccountInfoDouble(ACCOUNT_EQUITY);
    if(equity <= 0.0 && GetLastError() != 0)
    {
       SovLogError("OnTick", "AccountInfoDouble(ACCOUNT_EQUITY) failed");
       return;
    }
     double dailyLossPct = 0.0;
     if(g_dayStartEquity > 0.0 && equity < g_dayStartEquity)
        dailyLossPct = (g_dayStartEquity - equity) / g_dayStartEquity * 100.0;
     bool terminalOk   = SovTerminalTradingEnabled();
     bool allowEntries = (dailyLossPct < InpMaxDailyDD) && terminalOk;
     if(g_century.Enabled() && g_century.HaltTrading())
        allowEntries = false;
      // Python can request a flat-all or pause via telemetry for oversight, but it never blocks the EA's own trading.
      // The EA remains self-sufficient: Python is enhancement/telemetry only, not a gate.
      if(InpDebugMode || InpForceBypassAllFilters)
      {
         allowEntries = terminalOk; // bypass daily DD, century halt, python pause
         if(InpDebugMode)
            PrintFormat("Sovereign EA [%s]: DEBUG MODE active - bypassing drawdown/century/python blocks", _Symbol);
         if(InpForceBypassAllFilters)
            PrintFormat("Sovereign EA [%s]: FORCE BYPASS ALL FILTERS - bypassing drawdown/century/python blocks", _Symbol);
      }

      //=== Money management & blowup prevention (always active; not bypassed by debug) ===
      //--- equity hard stop: halt if equity drops X% from account balance
      double balance = AccountInfoDouble(ACCOUNT_BALANCE);
      if(InpEquityHardStopPct > 0.0 && balance > 0.0)
      {
         double eqDD = (balance - equity) / balance * 100.0;
         if(eqDD >= InpEquityHardStopPct)
         {
            allowEntries = false;
            g_lastBlockReason = "equity hard stop (" + DoubleToString(eqDD,2) + "% >= " + DoubleToString(InpEquityHardStopPct,2) + "%)";
            PrintFormat("Sovereign BLOWUP GUARD | %s | equity drawdown %.2f%% >= hard stop %.2f%% — halting all entries", _Symbol, eqDD, InpEquityHardStopPct);
         }
      }

      //--- daily loss hard stop: halt today if day loss exceeds hard % (separate from soft InpMaxDailyDD)
      if(InpDailyLossHardStopPct > 0.0 && dailyLossPct >= InpDailyLossHardStopPct)
      {
         allowEntries = false;
         g_lastBlockReason = "daily loss hard stop (" + DoubleToString(dailyLossPct,2) + "% >= " + DoubleToString(InpDailyLossHardStopPct,2) + "%)";
         PrintFormat("Sovereign BLOWUP GUARD | %s | daily loss %.2f%% >= hard stop %.2f%% — halting entries today", _Symbol, dailyLossPct, InpDailyLossHardStopPct);
      }

      //--- max concurrent positions: block new entries when at cap
      int sovPosCount = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong tk = PositionGetTicket(i);
         if(tk <= 0) continue;
         if(!PositionSelectByTicket(tk)) continue;
         if(PositionGetInteger(POSITION_MAGIC) != SOVEREIGN_MAGIC) continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         sovPosCount++;
      }
      if(InpMaxConcurrentPositions > 0 && sovPosCount >= InpMaxConcurrentPositions)
      {
         allowEntries = false;
         g_lastBlockReason = "max concurrent positions (" + IntegerToString(sovPosCount) + "/" + IntegerToString(InpMaxConcurrentPositions) + ")";
         PrintFormat("Sovereign POSITION CAP | %s | %d/%d positions — blocking new entries", _Symbol, sovPosCount, InpMaxConcurrentPositions);
      }

      //--- spread-aware pause: skip new entries when spread too wide (crypto/vol indices).
      //--- The cap is symbol-scaled: max(InpMaxSpreadForTrade, 2 x recent median spread),
      //--- because a fixed points cap calibrated on FX hard-blocks crypto/vol indices
      //--- whose normal spread is thousands of points.
      if(InpMaxSpreadForTrade > 0)
      {
         int spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
         int capPts = SovEffectiveSpreadCap(InpMaxSpreadForTrade);
         if(capPts > 0 && spread > capPts)
         {
            allowEntries = false;
            g_lastBlockReason = "spread too wide (" + IntegerToString(spread) + " > " + IntegerToString(capPts) + " pts)";
            //--- log once per bar, not once per tick
            static datetime lastSpreadGuardBar = 0;
            datetime sgBar = iTime(_Symbol, _Period, 0);
            if(sgBar != lastSpreadGuardBar)
            {
               lastSpreadGuardBar = sgBar;
               double spPx = (double)spread * SymbolInfoDouble(_Symbol, SYMBOL_POINT);
               PrintFormat("Sovereign SPREAD GUARD | %s | spread %d pts (%.5f price units) > effective cap %d pts (configured %d, 2x median %d) - blocking new entries",
                           _Symbol, spread, spPx, capPts, InpMaxSpreadForTrade, SovCachedMedianSpreadPts(200));
            }
         }
      }

      //--- weekend flat: close all before the weekend and block entries until Monday.
      //--- ONLY for symbols whose broker session actually closes: crypto and
      //--- synthetic indices trade 24/7, and blocking Sunday there is an FX
      //--- convention that silently froze BTCUSD all weekend.
      if(InpWeekendFlat && !SovSymbolTradesOnWeekend())
      {
         static datetime g_lastWeekendFlat = 0;
         MqlDateTime dt;
         TimeToStruct(TimeCurrent(), dt);
         bool weekendWindow = ((dt.day_of_week == 5 && dt.hour >= 21) ||
                               dt.day_of_week == 6 ||
                               dt.day_of_week == 0 ||
                               (dt.day_of_week == 1 && dt.hour < 1));
         if(weekendWindow)
         {
            //--- block entries for the WHOLE window (this used to fire once per
            //--- minute and leave entries enabled on every other tick)
            allowEntries = false;
            g_lastBlockReason = "weekend flat (market closed until Monday)";

            if(TimeCurrent() - g_lastWeekendFlat > 60)
            {
               g_lastWeekendFlat = TimeCurrent();
               int flatClosed = 0;
               for(int i = PositionsTotal() - 1; i >= 0; i--)
               {
                  ulong tk = PositionGetTicket(i);
                  if(tk <= 0) continue;
                  if(!PositionSelectByTicket(tk)) continue;
                  if(PositionGetInteger(POSITION_MAGIC) != SOVEREIGN_MAGIC) continue;
                  if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
                  if(trade.PositionClose(tk)) flatClosed++;
               }
               PrintFormat("Sovereign WEEKEND FLAT | %s | %d positions flattened — no entries until Monday", _Symbol, flatClosed);
            }
         }
      }

      //--- Portfolio-wide correlation & exposure heat (always active; not bypassed by debug)
      if(!CheckPortfolioHeat())
      {
         allowEntries = false;
         g_lastBlockReason = g_portfolioHeatDetail;
         g_lastBlockTime = TimeCurrent();
         PrintFormat("Sovereign PORTFOLIO HEAT | %s | %s — blocking new entries", _Symbol, g_portfolioHeatDetail);
      }

      //--- Economic calendar news blackout (MT5 native Calendar API)
      if(!CheckNewsBlackout())
      {
         allowEntries = false;
         g_lastBlockReason = "news blackout (" + g_newsBlockDetail + ")";
         g_lastBlockTime = TimeCurrent();
         PrintFormat("Sovereign NEWS BLACKOUT | %s | %s — blocking new entries", _Symbol, g_newsBlockDetail);
      }

      //--- Portfolio-level risk engine (VaR, correlation, Kelly, circuit breakers)
      if(allowEntries && InpPortfolioRiskEnabled)
      {
         if(!g_portRisk.CheckVarLimit(equity))
         {
            allowEntries = false;
            g_lastBlockReason = "portfolio VaR limit";
            PrintFormat("Sovereign PORTFOLIO VAR | %s | VaR limit breached — blocking new entries", _Symbol);
         }
         if(allowEntries && !g_portRisk.CheckCorrelation(_Symbol))
         {
            allowEntries = false;
            g_lastBlockReason = "portfolio correlation";
            PrintFormat("Sovereign PORTFOLIO CORR | %s | too correlated with open positions — blocking new entries", _Symbol);
         }
         if(allowEntries && !g_portRisk.CheckConcentration(_Symbol, 0.0, 0.0))
         {
            allowEntries = false;
            g_lastBlockReason = "portfolio concentration";
            PrintFormat("Sovereign PORTFOLIO CONC | %s | currency exposure cap — blocking new entries", _Symbol);
         }
         if(allowEntries && !g_portRisk.CheckCircuitBreakers(equity))
         {
            allowEntries = false;
            g_lastBlockReason = g_portRisk.HaltReason();
            PrintFormat("Sovereign PORTFOLIO CB | %s | %s — halting all entries", _Symbol, g_portRisk.HaltReason());
         }
      }

      //--- Human discretion / veto layer
      if(allowEntries && InpDiscretionEnabled)
      {
         string discReason = "";
         if(!g_disc.EntryAllowed(_Symbol, discReason))
         {
            allowEntries = false;
            g_lastBlockReason = discReason;
            PrintFormat("Sovereign DISCRETION | %s | %s — blocking new entries", _Symbol, discReason);
         }
      }

      //--- Account-level kill switches & hard limits (always active; not bypassed by debug)
      if(!CheckAccountDrawdown())
      {
         allowEntries = false;
         PrintFormat("Sovereign ACCOUNT DD GUARD | %s | %s — blocking new entries", _Symbol, g_lastBlockReason);
      }
      if(allowEntries && !CheckFreeMargin())
      {
         allowEntries = false;
         PrintFormat("Sovereign FREE MARGIN GUARD | %s | %s — blocking new entries", _Symbol, g_lastBlockReason);
      }
      if(allowEntries && !CheckTotalPositionsCap())
      {
         allowEntries = false;
         PrintFormat("Sovereign TOTAL POSITION CAP | %s | %s — blocking new entries", _Symbol, g_lastBlockReason);
      }
      if(allowEntries && !CheckDailyProfitTarget())
      {
         allowEntries = false;
         PrintFormat("Sovereign DAILY PROFIT TARGET | %s | %s — blocking new entries", _Symbol, g_lastBlockReason);
      }

      //--- set telemetry phase based on entry allowance
      if(!allowEntries)
         g_sovPhase = SOV_PHASE_IDLE_WAITING_BAR;
      else if(InpStrategy == SOV_RSI_BAND)
         g_sovPhase = SOV_PHASE_EVALUATING_RSI_BAND;
      else
         g_sovPhase = SOV_PHASE_IDLE_WAITING_BAR;

      //--- track why entries are blocked (for the status panel)
      //--- NOTE: every guard above already writes a specific reason. Do not
      //--- clobber it with a generic string - that hid "spread too wide" from
      //--- the panel and the Journal, which made "the EA is not trading"
      //--- impossible to diagnose from the log.
      if(!allowEntries)
      {
         if(dailyLossPct >= InpMaxDailyDD)
            g_lastBlockReason = "daily DD limit (" + DoubleToString(dailyLossPct, 2) + "% >= " + DoubleToString(InpMaxDailyDD, 2) + "%)";
         else if(!terminalOk)
            g_lastBlockReason = "algo trading disabled (enable green Algo Trading button)";
          else if(g_century.Enabled() && g_century.HaltTrading())
             g_lastBlockReason = "century kill-switch halt";
          else if(g_lastBlockReason == "")
             g_lastBlockReason = "entries blocked (no specific guard reported a reason)";
         g_lastBlockTime = TimeCurrent();
      }

     //--- handle flat-all request: close all Sovereign magic positions on this symbol
    if(g_flatAllRequested)
    {
       int flatClosed = 0;
       int flatFailed = 0;
       for(int i = PositionsTotal() - 1; i >= 0; i--)
       {
          ulong ticket = PositionGetTicket(i);
          if(ticket == 0)
          {
             flatFailed++;
             SovLogError("OnTick FLAT_ALL", "PositionGetTicket returned 0");
             continue;
          }
          if(!PositionSelectByTicket(ticket))
          {
             flatFailed++;
             SovLogError("OnTick FLAT_ALL", "PositionSelectByTicket failed for ticket=" + IntegerToString((long)ticket));
             continue;
          }
          if(PositionGetInteger(POSITION_MAGIC) != SOVEREIGN_MAGIC) continue;
          if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
          if(!trade.PositionClose(ticket))
          {
             flatFailed++;
             SovLogTradeRetcode(trade, "OnTick FLAT_ALL PositionClose", _Symbol);
             continue;
          }
          flatClosed++;
       }
       g_flatAllRequested = false;
       PrintFormat("Sovereign telemetry: FLAT_ALL executed on %s (closed=%d failed=%d)", _Symbol, flatClosed, flatFailed);
    }

    //--- send init snapshot once on first tick
    static bool g_initSent = false;
    static datetime g_lastBarSend = 0;
    if(!g_initSent)
    {
       bool algoAllowed = (bool)MQLInfoInteger(MQL_TRADE_ALLOWED);
       if(!g_telemetry.SendInitSnapshot(_Symbol, (int)InpStrategy, InpRsiBandEnabled,
                                        InpRsiBandDeadband, InpRiskPerPosition,
                                        InpMaxDailyDD, InpMaxSpread, algoAllowed))
       {
          SovLogWarn("OnTick: SendInitSnapshot failed - telemetry uplink not connected");
       }
       g_initSent = true;
    }

    //--- send bar state on every new bar for Python oversight
    datetime barTime = iTime(_Symbol, _Period, 0);
    if(barTime <= 0)
    {
       SovLogError("OnTick", "iTime(_Symbol, _Period, 0) returned <= 0");
    }
    else if(barTime != g_lastBarSend)
    {
       g_lastBarSend = barTime;
       double rsi = 0.0;
       int trend = 0;
       if(g_rsiBand.InPosition())
       {
          rsi = 0.0;
          trend = g_rsiBand.PositionSide();
       }
        if(!g_telemetry.SendBarState(_Symbol, _Period,
                                     0.0, 0.0, 0.0, 0.0,
                                     rsi, trend,
                                     g_rsiBand.InPosition(), g_rsiBand.PositionSide(),
                                     g_rsiBand.EntryPrice(), g_rsiBand.InPosition() ? 1 : 0,
                                     equity, dailyLossPct, allowEntries))
        {
           SovLogWarn("OnTick: SendBarState failed - telemetry uplink not connected");
        }
     }

    //--- macro trend filter (EMA 200): update buffer on new bar
    if(g_macroMaHandle != INVALID_HANDLE && barTime != g_macroMaBar)
    {
       if(CopyBuffer(g_macroMaHandle, 0, 0, 3, g_macroMaBuf) >= 3)
          g_macroMaBar = barTime;
    }

    //--- dynamic analyst: refresh indicator buffers on new bar (analysis only)
    if(InpAnalystEnabled)
       g_plan.Refresh();

    //--- pyramiding state is refreshed at the top of OnTick (UpdatePyramidState)

   if(InpStrategy == SOV_CARRY_MOM)
   {
      if(g_carry.IsNewMonth())
         g_carry.Rebalance(now, allowEntries, equity, g_perf, g_traffic, g_audit, trade,
                           g_century);

      //--- build + draw dynamic plan for carry mom too
      if(InpAnalystEnabled && g_plan.Ready())
      {
         g_plan.Build(g_planView, g_pyramidLegs, g_pyramidNetProfit, g_pyramidArmed);
         g_paint.DrawDynamicPlan(g_planView);
         g_paint.Flush();
      }

      PaintChart(allowEntries, equity, dailyLossPct);
      return;
   }

   if(InpStrategy == SOV_RSI_BAND)
   {
      //--- per-symbol gate: RSI band trades on any chart timeframe (template: EMA21/50, RSI9, levels 20/50/80).
      //--- When InpRsiBandSymbols is empty, any attached symbol is allowed.
      if(StringLen(InpRsiBandSymbols) > 0)
      {
         string symList[];
         int nSyms = StringSplit(InpRsiBandSymbols, ',', symList);
         bool symAllowed = false;
         for(int si = 0; si < nSyms; si++)
         {
            string s = symList[si];
            StringTrimLeft(s);
            StringTrimRight(s);
            if(s == _Symbol)
            {
               symAllowed = true;
               break;
            }
         }
         if(!symAllowed && !InpDebugMode && !InpForceBypassAllFilters)
         {
            g_sovPhase = SOV_PHASE_IDLE_WAITING_BAR;
            PrintFormat("Sovereign EA: EXECUTION BLOCKED: symbol not whitelisted | Current symbol='%s' | Whitelist='%s'", _Symbol, InpRsiBandSymbols);
            PaintChart(allowEntries, equity, dailyLossPct);
            BroadcastMarketPlanAndState(allowEntries, equity, dailyLossPct, (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD));
            return;
         }

         if(InpDebugMode && !symAllowed)
            PrintFormat("Sovereign EA [%s]: DEBUG MODE active - bypassing symbol whitelist ('%s' not in InpRsiBandSymbols='%s')", _Symbol, _Symbol, InpRsiBandSymbols);
         if(InpForceBypassAllFilters && !symAllowed)
            PrintFormat("Sovereign EA [%s]: FORCE BYPASS ALL FILTERS - bypassing symbol whitelist ('%s' not in InpRsiBandSymbols='%s')", _Symbol, _Symbol, InpRsiBandSymbols);
      }

      //--- wire macro MA + pyramid + trail state into RSI band (Rule 1 & Rule 4)
      double macroMaVal = 0.0;
      if(InpRsiBandUseMacroFilter)
      {
         //--- prefer the analyst's slow MA when ready (single source of truth),
         //--- else fall back to the legacy macro handle. Never invents a value.
         if(g_plan.Ready() && g_plan.SlowMa() > 0.0)
            macroMaVal = g_plan.SlowMa();
         else if(ArraySize(g_macroMaBuf) >= 3 && g_macroMaBuf[0] > 0.0)
            macroMaVal = g_macroMaBuf[0];
      }
      g_rsiBand.SetMacroMaValue(macroMaVal);
      g_rsiBand.SetStopAtr(InpRsiBandStopAtr);
      g_rsiBand.SetTrailAtrMult(InpTrailAtrMultiplier);
      g_rsiBand.SetRsiExhaustionLevel(InpRsiExhaustionLevel);
      g_rsiBand.SetMaxPyramidLayers(InpMaxPyramidLayers);
      g_rsiBand.SetPyramidExec(InpPyramidExecEnabled, InpMaxPyramidLayers, InpPyramidScaleStepAtr);
      g_rsiBand.SetPyramidAllowLoss(InpPyramidAllowLoss);
      g_rsiBand.SetForceBypassAllFilters(InpForceBypassAllFilters);
      g_rsiBand.SetPyramidState(g_pyramidLegs, g_pyramidNetProfit, g_pyramidArmed);

      //--- track position state for telemetry
      g_sovInPosition = g_rsiBand.InPosition();
      g_sovPositionSide = g_rsiBand.PositionSide();
      g_sovEntryPrice = g_rsiBand.EntryPrice();
      g_sovStopPrice = g_rsiBand.StopPrice();
      g_sovTpPrice = g_rsiBand.TpPrice();

      //--- set phase based on position state
      if(g_sovInPosition)
         g_sovPhase = SOV_PHASE_MANAGING_ACTIVE_TRADE;
      else
         g_sovPhase = SOV_PHASE_EVALUATING_RSI_BAND;

      g_rsiBand.OnNewBar(now, allowEntries, equity, g_perf, g_traffic, g_audit, trade);

      //--- if RSI band just entered a position, update phase to execution
      if(!g_sovInPosition && g_rsiBand.InPosition())
      {
         g_sovPhase = SOV_PHASE_PENDING_EXECUTION;
         g_sovInPosition = true;
         g_sovPositionSide = g_rsiBand.PositionSide();
         g_sovEntryPrice = g_rsiBand.EntryPrice();
         g_sovStopPrice = g_rsiBand.StopPrice();
         g_sovTpPrice = g_rsiBand.TpPrice();
      }

      //======================================================================
      //--- DYNAMIC PLAN BUILD + DRAW + AUDIT LOG (wired into all strategies)
      //======================================================================
      datetime barTime = iTime(_Symbol, _Period, 0);
      bool newBarForPlan = (barTime > 0 && barTime != g_lastPlanBuildTime);
      if(newBarForPlan) g_lastPlanBuildTime = barTime;

      //--- Build the dynamic plan snapshot (trend, RSI, ATR, narrative, etc.)
      if(InpAnalystEnabled && g_plan.Ready())
      {
         g_plan.Build(g_planView,
                      g_pyramidLegs, g_pyramidNetProfit, g_pyramidArmed);

         //--- Draw the plan on the chart (flicker-free via CSovereignChartVisualizer)
         g_paint.DrawDynamicPlan(g_planView);
         g_paint.Flush();

         //--- Log every signal evaluation to signal_audit.csv
         string signalSide = (g_planView.side > 0) ? "BUY" : (g_planView.side < 0 ? "SELL" : "NONE");
         string reason = g_planView.note;
         if(reason == "") reason = g_planView.biasText + " | " + g_planView.trendState + " | " + g_planView.momentumState;
         int spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
         g_audit.LogSignal(signalSide, g_planView.entry, true, reason, g_planView.confidence,
                           g_planView.entry, 0.0, g_planView.atrPct, spread, 0);

         //--- Throttled Journal print: once per minute OR on state transition
         int curMinute = (int)(TimeCurrent() / 60);
         if(curMinute != g_lastPrintMinute || g_planView.side != g_lastPlanSide)
         {
            g_lastPrintMinute = curMinute;
         g_lastPlanSide  = g_planView.side;
            PrintFormat("SOV PLAN | %s | side=%d trend=%s rsi=%.1f atr=%.5f entry=%.5f stop=%.5f tp=%.5f | note=%s",
                        _Symbol, g_planView.side, g_planView.trendState, g_planView.rsi,
                        g_planView.atr, g_planView.entry, g_planView.stop, g_planView.tp, reason);
            if(g_planView.risks != "")
               PrintFormat("SOV RISKS | %s", g_planView.risks);
         }

         //--- Explicit block reporting: if entries blocked, say WHY.
         //--- The specific guard (spread / margin / heat / news ...) already wrote
         //--- g_lastBlockReason - report THAT, not the plan narrative.
         if(!allowEntries)
         {
            string blockDetail = g_lastBlockReason;
            if(dailyLossPct >= InpMaxDailyDD)
               blockDetail = "daily DD " + DoubleToString(dailyLossPct,2) + "% >= " + DoubleToString(InpMaxDailyDD,2) + "%";
            else if(!terminalOk)
               blockDetail = "algo trading disabled (enable green Algo Trading button)";
            else if(g_century.Enabled() && g_century.HaltTrading())
               blockDetail = "century kill-switch halt";
            else if(blockDetail == "")
               blockDetail = g_planView.note;

            g_lastBlockReason = blockDetail;
            g_lastBlockTime = TimeCurrent();

            //--- once per bar (this used to print on every tick)
            static datetime lastBlockLogBarCarry = 0;
            datetime blkBarC = iTime(_Symbol, _Period, 0);
            if(blkBarC != lastBlockLogBarCarry)
            {
               lastBlockLogBarCarry = blkBarC;
               PrintFormat("SOV BLOCKED | %s | entries=HALTED | reason=%s | plan.note=%s",
                           _Symbol, blockDetail, g_planView.note);
            }
         }
       else if(g_traffic.LastVetoReason() != "" && !InpForceBypassAllFilters)
       {
          PrintFormat("SOV BLOCKED | %s | entries=%s | reason=Traffic Light veto: %s",
                      _Symbol, (allowEntries ? "ALLOWED" : "HALTED"), g_traffic.LastVetoReason());
          g_lastBlockReason = "Traffic Light veto: " + g_traffic.LastVetoReason();
          g_lastBlockTime = TimeCurrent();
       }
       else if(g_traffic.LastVetoReason() != "" && InpForceBypassAllFilters)
       {
          PrintFormat("Sovereign EA [%s]: FORCE BYPASS ALL FILTERS - bypassing Traffic Light veto: %s", _Symbol, g_traffic.LastVetoReason());
       }
      }
      else
      {
         //--- Plan not ready yet - still log that we're waiting
         if(newBarForPlan && InpVerboseBarLog)
            PrintFormat("SOV PLAN | %s | warming up | %s", _Symbol, (g_planView.note != "" ? g_planView.note : "collecting indicator data"));
      }

      PaintChart(allowEntries, equity, dailyLossPct);
      int spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
      BroadcastMarketPlanAndState(allowEntries, equity, dailyLossPct, spread);
      return;
   }

   if(InpStrategy == SOV_HEDGE_BLEND && _Period == PERIOD_D1)
   {
      //--- hedge blend: carry (monthly) + RSI band (H1) with regime-dependent weight
      if(g_carry.IsNewMonth())
         g_hedgeBlend.OnNewMonth(now, allowEntries, equity, syms, n);
      //--- RSI band overlay on H1 ticks: only meaningful when attached to H1 chart
      g_rsiBand.OnNewBar(now, allowEntries, equity, g_perf, g_traffic, g_audit, trade);

      //--- build + draw dynamic plan for hedge blend too
      if(InpAnalystEnabled && g_plan.Ready())
      {
         g_plan.Build(g_planView, g_pyramidLegs, g_pyramidNetProfit, g_pyramidArmed);
         g_paint.DrawDynamicPlan(g_planView);
         g_paint.Flush();
      }

      PaintChart(allowEntries, equity, dailyLossPct);
      return;
   }

   if(g_portfolio.IsNewDay())
      g_portfolio.Rebalance(now, allowEntries, equity, g_python, g_perf,
                            g_traffic, g_audit, trade);

    if(InpStructRideEnabled && _Period == PERIOD_H1)
    {
       //--- per-symbol gate: struct ride is certified on USDJPY H1 only
       string symList[];
       int nSyms = StringSplit(InpStructRideSymbols, ',', symList);
       bool symAllowed = false;
       for(int si = 0; si < nSyms; si++)
       {
         string s = symList[si];
          StringTrimLeft(s);
          StringTrimRight(s);
          if(s == _Symbol)
          {
             symAllowed = true;
             break;
          }
        }
       if(symAllowed && g_structAtrHandle != INVALID_HANDLE)
       {
          double atr = 0.0;
          double atrBuf[1];
          if(CopyBuffer(g_structAtrHandle, 0, 1, 1, atrBuf) >= 1)
             atr = atrBuf[0];

          g_structRide.UpdateStructure(TimeCurrent(), SymbolInfoDouble(_Symbol, SYMBOL_BID), atr);
          g_structRide.OnNewBar(now, allowEntries, equity, g_perf, g_traffic, g_audit, trade);
       }
    }

    if(InpRegimeFilterEnabled && _Period == PERIOD_H1)
    {
       string symList[];
       int nSyms = StringSplit(InpRegimeFilterSymbols, ',', symList);
       bool symAllowed = false;
       for(int si = 0; si < nSyms; si++)
       {
          string s = symList[si];
          StringTrimLeft(s);
          StringTrimRight(s);
          if(s == _Symbol)
          {
             symAllowed = true;
             break;
          }
       }
        if(symAllowed)
        {
           g_regimeFilter.OnNewBar(now, allowEntries, equity, g_perf, g_traffic, g_audit, trade);
        }
     }

     if(InpCompressionEnabled && _Period == PERIOD_H1)
     {
        string symList[];
        int nSyms = StringSplit(InpCompressionSymbols, ',', symList);
        bool symAllowed = false;
        for(int si = 0; si < nSyms; si++)
        {
           string s = symList[si];
           StringTrimLeft(s);
          StringTrimRight(s);
           if(s == _Symbol)
           {
              symAllowed = true;
              break;
           }
        }
         if(symAllowed)
         {
            g_compressionRubber.OnNewBar(now, allowEntries, equity, g_perf, g_traffic, g_audit, trade);
         }
     }

     if(InpEmaHarvestEnabled)
     {
        string symList[];
        int nSyms = StringSplit(InpEmaHarvestSymbols, ',', symList);
        bool symAllowed = false;
        for(int si = 0; si < nSyms; si++)
        {
           string s = symList[si];
           StringTrimLeft(s);
          StringTrimRight(s);
           if(s == _Symbol)
           {
              symAllowed = true;
              break;
           }
        }
      if(symAllowed)
      {
         g_emaHarvest.OnNewBar(now, allowEntries, equity, g_perf, g_traffic, g_audit, trade);
      }
   }

   //======================================================================
   //--- EMA21 RUBBER BAND OVERLAY (enter NEAR EMA21, exit FAR from EMA21)
   //======================================================================
   if(InpEmaRbEnabled)
   {
      string symList2[];
      int nSyms2 = StringSplit(InpEmaRbSymbols, ',', symList2);
      bool symAllowed2 = (StringLen(InpEmaRbSymbols) == 0);
      if(!symAllowed2)
      {
         for(int si = 0; si < nSyms2; si++)
         {
            string s = symList2[si];
            StringTrimLeft(s);
            StringTrimRight(s);
            if(s == _Symbol) { symAllowed2 = true; break; }
         }
      }
      if(symAllowed2)
      {
         OnNewBarEmaRb(now, allowEntries, equity, trade);
      }
   }

   //======================================================================
   //--- DYNAMIC PLAN BUILD + DRAW + AUDIT LOG (fallback for all strategies)
   //======================================================================
   bool newBarForPlan = (barTime > 0 && barTime != g_lastPlanBuildTime);
   if(newBarForPlan) g_lastPlanBuildTime = barTime;

   if(InpAnalystEnabled && g_plan.Ready())
   {
      g_plan.Build(g_planView,
                   g_pyramidLegs, g_pyramidNetProfit, g_pyramidArmed);

      g_paint.DrawDynamicPlan(g_planView);
      g_paint.Flush();

      string signalSide = (g_planView.side > 0) ? "BUY" : (g_planView.side < 0 ? "SELL" : "NONE");
      string reason = g_planView.note;
      if(reason == "") reason = g_planView.biasText + " | " + g_planView.trendState + " | " + g_planView.momentumState;
      int spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
      g_audit.LogSignal(signalSide, g_planView.entry, true, reason, g_planView.confidence,
                        g_planView.entry, 0.0, g_planView.atrPct, spread, 0);

      int curMinute = (int)(TimeCurrent() / 60);
      if(curMinute != g_lastPrintMinute || g_planView.side != g_lastPlanSide)
      {
         g_lastPrintMinute = curMinute;
         g_lastPlanSide  = g_planView.side;
         PrintFormat("SOV PLAN | %s | side=%d trend=%s rsi=%.1f atr=%.5f entry=%.5f stop=%.5f tp=%.5f | note=%s",
                     _Symbol, g_planView.side, g_planView.trendState, g_planView.rsi,
                     g_planView.atr, g_planView.entry, g_planView.stop, g_planView.tp, reason);
         if(g_planView.risks != "")
            PrintFormat("SOV RISKS | %s", g_planView.risks);
      }

      if(!allowEntries)
      {
         string blockDetail = g_lastBlockReason;
         if(dailyLossPct >= InpMaxDailyDD)
            blockDetail = "daily DD " + DoubleToString(dailyLossPct,2) + "% >= " + DoubleToString(InpMaxDailyDD,2) + "%";
         else if(!terminalOk)
            blockDetail = "algo trading disabled (enable green Algo Trading button)";
         else if(g_century.Enabled() && g_century.HaltTrading())
            blockDetail = "century kill-switch halt";
         else if(blockDetail == "")
            blockDetail = g_planView.note;

         g_lastBlockReason = blockDetail;
         g_lastBlockTime = TimeCurrent();

         //--- once per bar (this used to print on every tick)
         static datetime lastBlockLogBarMain = 0;
         datetime blkBarM = iTime(_Symbol, _Period, 0);
         if(blkBarM != lastBlockLogBarMain)
         {
            lastBlockLogBarMain = blkBarM;
            PrintFormat("SOV BLOCKED | %s | entries=HALTED | reason=%s | plan.note=%s",
                        _Symbol, blockDetail, g_planView.note);
         }
      }
      else if(g_traffic.LastVetoReason() != "" && !InpForceBypassAllFilters)
      {
         PrintFormat("SOV BLOCKED | %s | entries=%s | reason=Traffic Light veto: %s",
                     _Symbol, (allowEntries ? "ALLOWED" : "HALTED"), g_traffic.LastVetoReason());
         g_lastBlockReason = "Traffic Light veto: " + g_traffic.LastVetoReason();
         g_lastBlockTime = TimeCurrent();
      }
      else if(g_traffic.LastVetoReason() != "" && InpForceBypassAllFilters)
      {
         PrintFormat("Sovereign EA [%s]: FORCE BYPASS ALL FILTERS - bypassing Traffic Light veto: %s", _Symbol, g_traffic.LastVetoReason());
      }
   }
    else if(newBarForPlan && InpVerboseBarLog)
    {
       PrintFormat("SOV PLAN | %s | warming up | %s", _Symbol, (g_planView.note != "" ? g_planView.note : "collecting indicator data"));
    }

    //--- clean up trade chart objects when flat (no open positions) to prevent SL/TP/zone clutter
    if(newBarForPlan && !g_rsiBand.InPosition())
    {
       g_paint.ClearTradeObjects();
    }

   //--- fallback telemetry broadcast for strategies that don't broadcast internally
   int spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   BroadcastMarketPlanAndState(allowEntries, equity, dailyLossPct, spread);

   PaintChart(allowEntries, equity, dailyLossPct);
}

//+------------------------------------------------------------------+
//| Chart renderer. Reads only; it can never place an order.         |
//+------------------------------------------------------------------+
string SovTfText(const ENUM_TIMEFRAMES tf)
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

string NextRebalanceText()
{
   MqlDateTime d;
   TimeToStruct(TimeCurrent(), d);
   if(InpStrategy == SOV_CARRY_MOM)
   {
      int y = d.year;
      int m = d.mon + 1;
      if(m > 12)
      {
         m = 1;
         y++;
      }
      return StringFormat("%04d-%02d-01  (monthly)", y, m);
   }
   d.hour = 0;
   d.min  = 0;
   d.sec  = 0;
   datetime t = (datetime)((long)StructToTime(d) + 86400);
   return TimeToString(t, TIME_DATE) + "  (daily)";
}

//+------------------------------------------------------------------+
//| DrawAnalystMarkup - render the analyst read on the chart         |
//| (SOV_DYN_ objects; redraws on a new bar only)                    |
//+------------------------------------------------------------------+
void DrawAnalystMarkup(const SovereignPlanView &plan)
{
   if(!InpAnalystEnabled)
      return;

   //--- MA paths: slow regime line + fast/mid dynamic resistance band
   if(InpAnalystPathBars > 0)
   {
      datetime ptimes[];
      double   pslow[], pfast[], pmid[];
      int pn = g_plan.FullPath(ptimes, pslow, pfast, pmid, InpAnalystPathBars);
      g_paint.DrawDynamicPaths(ptimes, pslow, pfast, pmid, pn);
   }

   //--- numbered leg markers at the exact bar time + price of live legs
   datetime ltimes[];
   double   lprices[];
   int      lsides[];
   int      ln = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk <= 0) continue;
      if(!PositionSelectByTicket(tk)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != SOVEREIGN_MAGIC) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      int sz = ArraySize(ltimes);
      ArrayResize(ltimes, sz + 1);
      ArrayResize(lprices, sz + 1);
      ArrayResize(lsides, sz + 1);
      ltimes[sz]  = (datetime)PositionGetInteger(POSITION_TIME);
      lprices[sz] = PositionGetDouble(POSITION_PRICE_OPEN);
      lsides[sz]  = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
      ln++;
   }
   g_paint.DrawDynamicLegMarkers(ltimes, lprices, lsides, ln);
}

//+------------------------------------------------------------------+
//| BroadcastMarketPlanAndState — real-time reasoning HUD             |
//| Prints to Journal on bar change + state transition;               |
//| draws Comment() overlay on chart every tick when enabled.         |
//+------------------------------------------------------------------+
void BroadcastMarketPlanAndState(const bool allowEntries, const double equity,
                                 const double dayLossPct, const int spread)
{
   //--- gather live market data
   double bid   = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask   = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   int bars     = Bars(_Symbol, _Period);
   datetime barTime = iTime(_Symbol, _Period, 0);
   bool newBar = (barTime > 0 && barTime != g_sovLastBroadcastBar);

   //--- build market situation string
   double rsi = g_rsiBand.CurrentRsi();
   int trend  = g_rsiBand.CurrentTrend();
   string trendText = (trend > 0 ? "UPTREND" : (trend < 0 ? "DOWNTREND" : "FLAT/NEUTRAL"));
   string sideText  = (g_sovPositionSide > 0 ? "LONG" : (g_sovPositionSide < 0 ? "SHORT" : "FLAT"));

   g_sovMarketSituation = StringFormat(
      "SYM=%s | Bid=%.5f Ask=%.5f Spread=%d pts | RSI=%.1f | Trend=%s (%s) | Bars=%d",
      _Symbol, bid, ask, spread, rsi, trendText, sideText, bars);

   //--- build active plan + reasoning chain based on current phase
   string allowTxt = (allowEntries ? "ALLOWED" : "HALTED");
   string blockTxt = (g_lastBlockReason != "" ? g_lastBlockReason : "none");
   string tlReason = g_traffic.LastVetoReason();
   string tlTxt    = (tlReason != "" ? tlReason : "cleared");

   switch(g_sovPhase)
   {
      case SOV_PHASE_IDLE_WAITING_BAR:
         g_sovActivePlan = StringFormat("WAITING for new bar (current bar time=%s)", TimeToString(barTime, TIME_SECONDS));
         g_sovReasoningChain = "Phase: IDLE | No evaluation until bar progresses";
         break;
      case SOV_PHASE_CHECKING_SPREAD:
         g_sovActivePlan = StringFormat("CHECKING spread=%d vs p90 gate", spread);
         g_sovReasoningChain = StringFormat("Phase: SPREAD CHECK | spread=%d | entries=%s | block=%s", spread, allowTxt, blockTxt);
         break;
      case SOV_PHASE_CHECKING_TRAFFIC_LIGHT:
         g_sovActivePlan = "CHECKING Traffic Light board";
         g_sovReasoningChain = StringFormat("Phase: TRAFFIC LIGHT | veto=%s | entries=%s", tlTxt, allowTxt);
         break;
      case SOV_PHASE_EVALUATING_RSI_BAND:
         g_sovActivePlan = StringFormat("EVALUATING RSI band | trend=%s rsi=%.1f", trendText, rsi);
         g_sovReasoningChain = StringFormat("Phase: RSI BAND | trend=%d rsi=%.1f barsHeld=%d/%d block=%s",
                                           trend, rsi, g_rsiBand.BarsHeld(), g_rsiBand.MinHoldBars(), blockTxt);
         break;
      case SOV_PHASE_PENDING_EXECUTION:
         g_sovActivePlan = StringFormat("PENDING EXECUTION | side=%s entry=%.5f stop=%.5f tp=%.5f",
                                        sideText, g_sovEntryPrice, g_sovStopPrice, g_sovTpPrice);
         g_sovReasoningChain = StringFormat("Phase: EXECUTE | conditions met | riskPct=%.2f%%", InpRsiBandRiskPct);
         break;
      case SOV_PHASE_MANAGING_ACTIVE_TRADE:
         g_sovActivePlan = StringFormat("MANAGING ACTIVE TRADE | side=%s entry=%.5f stop=%.5f tp=%.5f",
                                        sideText, g_sovEntryPrice, g_sovStopPrice, g_sovTpPrice);
         g_sovReasoningChain = StringFormat("Phase: MANAGE | barsHeld=%d | trailATR=%.1f | rsiExhaustion=%d",
                                           g_rsiBand.BarsHeld(), InpTrailAtrMultiplier, InpRsiExhaustionLevel);
         break;
   }

   //--- Journal broadcast on bar change or phase transition (not every tick — avoids spam)
   if(newBar || g_sovPhase != g_sovLastPhase)
   {
      g_sovLastPhase = g_sovPhase;
      Print("=== SOV TELEMETRY ===");
      Print(g_sovMarketSituation);
      Print(g_sovActivePlan);
      Print(g_sovReasoningChain);
      Print(StringFormat("Entries=%s | DailyDD=%.2f%%/%.2f%% | Century=%s | PythonPaused=%s | Debug=%s | ForceBypass=%s",
                         allowTxt, dayLossPct, InpMaxDailyDD,
                         (g_century.Enabled() && g_century.HaltTrading()) ? "HALTED" : "running",
                         g_pythonPaused ? "YES" : "no",
                         InpDebugMode ? "ON" : "off",
                         InpForceBypassAllFilters ? "ON" : "off"));
      Print("=====================");
      g_sovLastBroadcastBar = barTime;
   }

   //--- Comment() HUD on chart (instant visual, every tick)
   if(InpShowTelemetryHud)
   {
      string hud = StringFormat(
         "SOV TELEMETRY\n"
         "%s\n"
         "Phase: %s\n"
         "Plan: %s\n"
         "Reason: %s\n"
         "Entries: %s | Block: %s | TL: %s\n"
         "Pos: %s | Entry=%.5f SL=%.5f TP=%.5f",
         g_sovMarketSituation,
         EnumToString(g_sovPhase),
         g_sovActivePlan,
         g_sovReasoningChain,
         allowTxt, blockTxt, tlTxt,
         sideText, g_sovEntryPrice, g_sovStopPrice, g_sovTpPrice);
      Comment(hud);
   }
}

void PaintChart(const bool allowEntries, const double equity, const double dayLossPct)
{
   if(!InpShowVisuals)
      return;

   string sym = _Symbol;
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   if(digits <= 0)
      digits = 5;

   //--- dynamic market analyst: build the structured read for panel + markup
   SovereignPlanView plan;
   g_plan.Build(plan, g_pyramidLegs, g_pyramidNetProfit, g_pyramidArmed);

   //--- dynamic plan rendering (SOV_DYN_): redraw on a new bar only
   if(InpAnalystEnabled && plan.barTime > 0 && plan.barTime != g_lastDynBar)
   {
      g_lastDynBar = plan.barTime;
      g_paint.DrawDynamicPlan(plan);
      DrawAnalystMarkup(plan);   // MA paths + numbered leg markers
   }

   string parts[];
   int universe = StringSplit(InpSymbols, ',', parts);

   int openAll = 0;
   int openHere = 0;
   if(InpVisualPositions)
   {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong ticket = PositionGetTicket(i);
         if(!PositionSelectByTicket(ticket))
            continue;
         if(PositionGetInteger(POSITION_MAGIC) != SOVEREIGN_MAGIC)
            continue;
         openAll++;
         string psym = PositionGetString(POSITION_SYMBOL);
         if(psym != sym)
            continue;
         openHere++;

         long   type = PositionGetInteger(POSITION_TYPE);
         double ent  = PositionGetDouble(POSITION_PRICE_OPEN);
         double sl   = PositionGetDouble(POSITION_SL);
         double tp   = PositionGetDouble(POSITION_TP);
         datetime ot = (datetime)PositionGetInteger(POSITION_TIME);
         string tag  = IntegerToString((long)ticket);

         if(ot > 0 && ent > 0.0)
         {
            if(type == POSITION_TYPE_BUY)
               g_paint.DrawArrow("Pos_" + tag, ot, ent, SOV_ARROW_UP, clrLime,
                                 "Long #" + tag);
            else
               g_paint.DrawArrow("Pos_" + tag, ot, ent, SOV_ARROW_DOWN, clrTomato,
                                 "Short #" + tag);
         }
         if(sl > 0.0)
            g_paint.DrawLevel("PosSL_" + tag, sl, InpVisualSlColor, STYLE_DASH, 1,
                              "Stop #" + tag);
         if(tp > 0.0)
            g_paint.DrawLevel("PosTP_" + tag, tp, InpVisualTpColor, STYLE_DASH, 1,
                              "Target #" + tag);
      }
   }

   //--- swing markers in the template's own colour (same 5-bar fractal it draws)
   if(InpUseTemplate && g_template.Loaded() && g_template.FractalEnabled())
      g_paint.DrawFractals(sym, _Period, InpTemplateFractals,
                           g_template.FractalColor(), g_template.FractalColor(),
                           g_template.FractalUpArrow(), g_template.FractalDownArrow());

    //--- the AI analyst's read, drawn in the template's palette
    if(InpAiOverlay && g_ai.Loaded())
    {
       bool  haveTpl = (InpUseTemplate && g_template.Loaded());
       color cSup    = haveTpl ? g_template.CandleUp()   : clrLime;
       color cRes    = haveTpl ? g_template.CandleDown() : clrTomato;
       color cTxt    = haveTpl ? g_template.Fg()         : clrWhite;
        g_ai.Draw(g_paint, cSup, cRes, cTxt);
     }

    //--- RSI divergence: pivot-based, causal, display only.
    //--- Scanned once per bar; drawn only when the bar changes so a tick storm
    //--- cannot rebuild the object set. Never sizes or triggers anything.
    if(InpDivEnabled && g_divRsiHandle != INVALID_HANDLE)
    {
       datetime divBar = iTime(_Symbol, _Period, 0);
       if(g_div.Scan(_Symbol, (ENUM_TIMEFRAMES)_Period, InpDivLogOnNew) ||
          divBar != g_divBar)
       {
          g_divBar = divBar;
          bool  haveTplDiv = (InpUseTemplate && g_template.Loaded());
          color cBull = haveTplDiv ? g_template.CandleUp()   : clrLime;
          color cBear = haveTplDiv ? g_template.CandleDown() : clrTomato;
          g_div.Draw(g_paint, cBull, cBear);
       }
    }

    if(!InpVisualPanel)
    {
       g_paint.Flush();
       return;
    }

   string lines[128];
   color  cols[128];
   int n = 0;

   lines[n] = StringFormat("SOV GODS EYE   %s %s", sym, SovTfText(_Period));
   cols[n]  = clrGold; n++;

   lines[n] = StringFormat("STRATEGY  %s",
                           (InpStrategy == SOV_CARRY_MOM
                            ? "monthly carry + 12m momentum"
                            : "daily xsec mean-reversion + carry*"));
   cols[n]  = clrSilver; n++;

   if(InpUseTemplate && g_template.Loaded())
   {
      double rv = g_template.RsiValue(0);
      int lc = g_template.RsiLevelCount();
      string band = "";
      if(lc >= 2)
         band = StringFormat("   RSI%d %.1f  band %.0f/%.0f",
                             g_template.RsiPeriod(), rv,
                             g_template.RsiLevel(0), g_template.RsiLevel(lc - 1));
      lines[n] = g_template.Describe() + band;
      cols[n]  = g_template.RsiColor(); n++;
   }

   if(InpAiOverlay)
   {
      if(!g_ai.Loaded())
      {
         lines[n] = "AI: no overlay yet (run llm_chart_analyst.py)";
         cols[n]  = clrGray; n++;
      }
      else
      {
         bool stale = g_ai.Stale(InpAiOverlayMaxAgeSec);
         color c = (g_ai.Read() == "long"  ? clrLime
                    : g_ai.Read() == "short" ? clrTomato
                    : clrSilver);
         if(g_ai.Veto())
            c = clrOrange;
         if(stale)
            c = clrDimGray;
         lines[n] = g_ai.Describe() + (stale ? "   STALE" : "");
         cols[n]  = c; n++;
         if(g_ai.Narrative() != "")
         {
            string narr = g_ai.Narrative();
            if(StringLen(narr) > 90)
               narr = StringSubstr(narr, 0, 90) + "...";
            lines[n] = "   " + narr;
            cols[n]  = clrGray; n++;
         }
      }
   }

   if(InpDivEnabled && n < 127)
   {
      int dk = g_div.LastKind();
      lines[n] = "DIV  " + g_div.Describe();
      cols[n]  = (dk > 0 ? clrLime : (dk < 0 ? clrTomato : clrGray)); n++;
   }

   lines[n] = StringFormat("universe %d symbols   topN %d   risk/leg %.2f%%   max total %.2f%%",
                           universe, InpTopN, InpRiskPerPosition, InpMaxTotalRisk);
   cols[n]  = clrSilver; n++;

   lines[n] = StringFormat("next rebalance  %s", NextRebalanceText());
   cols[n]  = clrSilver; n++;

   lines[n] = StringFormat("positions  %d open (%d on this chart)   magic %d",
                           openAll, openHere, SOVEREIGN_MAGIC);
   cols[n]  = (openAll > 0 ? clrAqua : clrGray); n++;

   lines[n] = StringFormat("equity %.2f   day guard %.2f%% / %.2f%%   %s",
                           equity, dayLossPct, InpMaxDailyDD,
                           (allowEntries ? "ENTRIES ALLOWED" : "ENTRIES HALTED"));
   cols[n]  = (allowEntries ? clrLime : clrRed); n++;

   lines[n] = g_perf.StatusLine();
   cols[n]  = clrSilver; n++;

   lines[n] = StringFormat("century %s   scalar %.2f   topN %d   %s",
                           (g_century.Enabled() ? "ON" : "off"),
                           g_century.GlobalScalar(), g_century.TopN(),
                           (g_century.HaltTrading() ? "HALTED" : "running"));
   cols[n]  = (g_century.HaltTrading() ? clrRed : clrSilver); n++;

    lines[n] = StringFormat("stack v1 %s   traffic light %s   python %s",
                            (InpUseSovereignStackV1 ? "ON" : "off"),
                            (InpUseTrafficLight ? "ON" : "off"),
                            (InpRequirePythonSignal ? "REQUIRED" : "advisory"));
    cols[n]  = clrGray; n++;

    if(InpStrategy == SOV_RSI_BAND || InpStrategy == SOV_HEDGE_BLEND)
    {
       lines[n] = StringFormat("RSI band %s   side %d   entry %.5f   risk %.2f%%",
                               (g_rsiBand.InPosition() ? "IN" : "flat"),
                               g_rsiBand.PositionSide(),
                               g_rsiBand.EntryPrice(),
                               InpRsiBandRiskPct);
       cols[n]  = (g_rsiBand.InPosition() ? clrAqua : clrGray); n++;
    }

     if(InpStrategy == SOV_HEDGE_BLEND)
     {
        lines[n] = StringFormat("HEDGE BLEND   carry mom3 %.4f   carry %s   RSI band active",
                                g_hedgeBlend.CarryMom3(),
                                (g_hedgeBlend.CarryAllowed() ? "ON" : "OFF"));
        cols[n]  = (g_hedgeBlend.CarryAllowed() ? clrLime : clrOrange); n++;
     }

      if(InpStrategy == SOV_REGIME_FILTER)
      {
         lines[n] = StringFormat("REGIME FILTER   %s   side %d   entry %.5f",
                                 (g_regimeFilter.InPosition() ? "IN" : "flat"),
                                 g_regimeFilter.PositionSide(),
                                 g_regimeFilter.EntryPrice());
         cols[n]  = (g_regimeFilter.InPosition() ? clrAqua : clrGray); n++;
      }

         if(InpCompressionEnabled && _Period == PERIOD_H1)
         {
            lines[n] = StringFormat("COMP RUBBER   %s   side %d   entry %.5f",
                                    (g_compressionRubber.InPosition() ? "IN" : "flat"),
                                    g_compressionRubber.PositionSide(),
                                    g_compressionRubber.EntryPrice());
             cols[n]  = (g_compressionRubber.InPosition() ? clrLime : clrGray); n++;
         }

     if(InpEmaHarvestEnabled)
     {
         lines[n] = StringFormat("EMA HARVEST   ema=%d   %s   side %d   entry %.5f   consec L=%d S=%d   trail=%.1f rsiEntry=%d",
                                 InpEmaHarvestPeriod,
                                 (g_emaHarvest.InPosition() ? "IN" : "flat"),
                                 g_emaHarvest.PositionSide(),
                                 g_emaHarvest.EntryPrice(),
                                 g_emaHarvest.CurrentConsecutiveLong(),
                                 g_emaHarvest.CurrentConsecutiveShort(),
                                 InpEmaHarvestTrailAtr,
                                 InpEmaHarvestRsiEntryMax);
        cols[n]  = (g_emaHarvest.InPosition() ? clrLime : clrGray); n++;
     }

    //--- last block / veto reason (why the EA is not trading)
    string blockReason = g_lastBlockReason;
    datetime blockTime = g_lastBlockTime;
    if(blockReason != "" && blockTime > 0)
    {
       string t = TimeToString(blockTime, TIME_DATE | TIME_SECONDS);
       lines[n] = StringFormat("LAST BLOCK   %s   @ %s", blockReason, t);
       cols[n]  = clrRed; n++;
    }
    string tlReason = g_traffic.LastVetoReason();
    datetime tlTime = g_traffic.LastVetoTime();
    if(tlReason != "" && tlTime > 0)
    {
       string t = TimeToString(tlTime, TIME_DATE | TIME_SECONDS);
       lines[n] = StringFormat("TL VETO      %s   @ %s", tlReason, t);
       cols[n]  = clrOrange; n++;
    }

    if(InpStrategy == SOV_RSI_BAND && !g_rsiBand.InPosition())
    {
       string rbReason = g_rsiBand.LastBlockReason();
       datetime rbTime = g_rsiBand.LastBlockTime();
       if(rbReason != "" && rbTime > 0)
       {
          string t = TimeToString(rbTime, TIME_DATE | TIME_SECONDS);
          lines[n] = StringFormat("RSI BLOCK    %s   @ %s", rbReason, t);
          cols[n]  = clrOrange; n++;
       }
        lines[n] = StringFormat("RSI state    rsi=%.1f trend=%d barsHeld=%d/%d spread=%d mid/hi/lo=%d/%d/%d",
                                g_rsiBand.CurrentRsi(), g_rsiBand.CurrentTrend(),
                                g_rsiBand.BarsHeld(), g_rsiBand.MinHoldBars(),
                                g_rsiBand.CurrentSpread(),
                                g_rsiBand.RsiMid(), g_rsiBand.RsiHi(), g_rsiBand.RsiLo());
        cols[n]  = clrSilver; n++;
        lines[n] = StringFormat("MA filter    crosses=%d/%d   emaAligned=%s",
                                g_rsiBand.CurrentMACrossCount(), g_rsiBand.CurrentMACrossMax(),
                                (g_rsiBand.EMA21AboveEma50() ? "yes" : "no"));
        cols[n]  = (g_rsiBand.EMA21AboveEma50() ? clrLime : clrOrange); n++;
        lines[n] = StringFormat("Consecutive  closes=%d   longNeed=%d shortNeed=%d  slopeReq=%s emaBreak=%s longOnly=%s",
                                g_rsiBand.CurrentConsecutiveCloses(),
                                InpRsiBandMinConsecutiveLong, InpRsiBandMinConsecutiveShort,
                                (InpRsiBandRequireEma21Slope ? "yes" : "no"),
                                (InpRsiBandExitOnEmaBreak ? "yes" : "no"),
                                (InpRsiBandLongOnly ? "yes" : "no"));
        cols[n]  = clrSilver; n++;
    }

    //--- Dynamic market analyst panel (analysis + visualisation)
    if(InpAnalystEnabled)
    {
       if(!plan.valid)
       {
          lines[n] = "ANALYST   warming up (" + plan.note + ")";
          cols[n]  = clrOrange; n++;
       }
       else
       {
          lines[n] = StringFormat("ANALYST   bias=%s  confidence=%.0f%%  trend=%s  momentum=%s",
                                  plan.biasText, plan.confidence * 100.0,
                                  plan.trendState, plan.momentumState);
          cols[n]  = (plan.side > 0 ? clrLime : (plan.side < 0 ? clrTomato : clrSilver)); n++;
          lines[n] = StringFormat("Vol/Struct  %s (ATR p%.0f)   %s   MAspread=%.5f",
                                  plan.volState, plan.atrPct * 100.0,
                                  plan.structureState, plan.maSpread);
          cols[n]  = clrSilver; n++;
          if(plan.side != 0)
          {
             lines[n] = StringFormat("Levels   entry=%.5f  stop=%.5f  tp=%.5f  leg2=%.5f  leg3=%.5f",
                                     plan.entry, plan.stop, plan.tp, plan.scale2, plan.scale3);
             cols[n]  = clrSilver; n++;
          }
          if(g_template.Loaded() && g_template.RsiLevelCount() >= 2)
          {
             lines[n] = StringFormat("RSI bands  template=%.0f/%.0f  exec hi/lo=%d/%d  (drawn = template)",
                                     g_template.RsiLevel(0),
                                     g_template.RsiLevel(g_template.RsiLevelCount() - 1),
                                     InpRsiBandHi, InpRsiBandLo);
             cols[n]  = (InpRsiBandHi == (int)g_template.RsiLevel(g_template.RsiLevelCount() - 1) ? clrSilver : clrOrange);
             n++;
          }
          lines[n] = StringFormat("Structure  resistance=%.5f (%.1f ATR)  support=%.5f (%.1f ATR)",
                                  plan.swingHigh, plan.distResistance,
                                  plan.swingLow, plan.distSupport);
          cols[n]  = clrSilver; n++;
          if(InpAnalystShowNarrative)
          {
             lines[n] = "READ   " + plan.narrative;
             cols[n]  = clrAqua; n++;
             lines[n] = "RISKS  " + plan.risks;
             cols[n]  = (plan.risks == "none flagged" ? clrLime : clrOrange); n++;
          }
          lines[n] = StringFormat("Pyramid   legs=%d/%d   netProfit=%.2f   guard=%s   exec=%s",
                                  g_pyramidLegs, InpMaxPyramidLayers, g_pyramidNetProfit,
                                  (g_pyramidArmed ? "ARMED" : "BLOCKED"),
                                  (InpPyramidExecEnabled ? "ON" : "OFF"));
          cols[n]  = (g_pyramidArmed ? clrLime : clrRed); n++;
       }
    }
    else if(InpVisualSimulation)
    {
       //--- legacy panel (kept for compatibility when the analyst is off)
       lines[n] = StringFormat("Pyramid   legs=%d/%d   netProfit=%.2f   guard=%s",
                               g_pyramidLegs, InpMaxPyramidLayers, g_pyramidNetProfit,
                               (g_pyramidArmed ? "ARMED" : "BLOCKED"));
       cols[n]  = (g_pyramidArmed ? clrLime : clrRed); n++;
       lines[n] = StringFormat("Macro MA %d   value=%.5f   Bid=%.5f",
                               InpMacroMaPeriod,
                               (ArraySize(g_macroMaBuf) >= 3 ? g_macroMaBuf[0] : 0.0),
                               SymbolInfoDouble(_Symbol, SYMBOL_BID));
       cols[n]  = clrSilver; n++;
    }

    lines[n] = "frozen research build - not cleared for live";
    cols[n]  = clrOrange; n++;


    //--- LEGACY hardcoded sim plan (off by default; kept for compatibility)
    //--- gate on new bar so we don't delete/recreate chart objects every tick
    static datetime g_lastSimPlanBar = 0;
    datetime simBar = iTime(_Symbol, _Period, 0);
    if(InpVisualSimulation && InpShowVisuals && simBar > 0 && simBar != g_lastSimPlanBar)
    {
       double trailStopPx = 0.0;
       datetime trailStopT = 0;
       if(g_rsiBand.InPosition())
       {
          trailStopPx = g_rsiBand.EntryPrice();
          trailStopT = simBar;
       }
       g_paint.DrawStrategySimulationPlan(
          InpInitialEntryPrice,
          InpScaleIn1Price,
          InpScaleIn2Price,
          InpStopLossZonePrice,
          trailStopPx,
          trailStopT);
       g_lastSimPlanBar = simBar;
    }

   g_paint.DrawPanel("GE_PANEL", lines, cols, 12, 18, 14, 8,
                     (color)C'16,18,24', clrDimGray);
   g_paint.Flush();
}

//+------------------------------------------------------------------+
//| Trade transaction audit hook                                      |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   g_audit.LogDeal(trans, result.retcode);
   g_perf.OnTradeTransaction(trans);

   //--- send trade event to Python telemetry uplink for oversight
   string action = "";
   long transType = (long)trans.type;
   if(transType == TRADE_TRANSACTION_DEAL_ADD)
   {
      if(!SovHistoryDealSelect(trans.deal, "OnTradeTransaction"))
         return;
      long entry = (long)HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
      if(entry == DEAL_ENTRY_IN)
         action = "open";
      else if(entry == DEAL_ENTRY_OUT)
         action = "close";
      else if(entry == DEAL_ENTRY_INOUT)
         action = "flip";
   }
   else if(transType == TRADE_TRANSACTION_ORDER_ADD)
      action = "order";
   else if(transType == TRADE_TRANSACTION_ORDER_DELETE)
      action = "order_delete";

   if(action != "")
   {
      if(!SovHistoryDealSelect(trans.deal, "OnTradeTransaction event"))
         return;
      string symbol = HistoryDealGetString(trans.deal, DEAL_SYMBOL);
      ulong ticket = (ulong)HistoryDealGetInteger(trans.deal, DEAL_TICKET);
      ulong positionId = (ulong)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
      long magic = (long)HistoryDealGetInteger(trans.deal, DEAL_MAGIC);
      string comment = HistoryDealGetString(trans.deal, DEAL_COMMENT);
      double profit = HistoryDealGetDouble(trans.deal, DEAL_PROFIT);
      double commission = HistoryDealGetDouble(trans.deal, DEAL_COMMISSION);
      double swap = HistoryDealGetDouble(trans.deal, DEAL_SWAP);
      double price = HistoryDealGetDouble(trans.deal, DEAL_PRICE);
      double volume = HistoryDealGetDouble(trans.deal, DEAL_VOLUME);
      int side = 0;
      if(HistoryDealGetInteger(trans.deal, DEAL_TYPE) == DEAL_TYPE_BUY) side = 1;
      else if(HistoryDealGetInteger(trans.deal, DEAL_TYPE) == DEAL_TYPE_SELL) side = -1;

      double sl = 0.0;
      double tp = 0.0;

      string reason = "";
      string retcodeDesc = IntegerToString((int)result.retcode);

   if(!g_telemetry.SendTradeEvent(action, symbol, side,
                                     (double)volume, (double)price, (double)sl, (double)tp,
                                     profit, commission, swap,
                                     ticket, positionId, magic, comment,
                                     reason, (int)result.retcode, retcodeDesc))
   {
      SovLogWarn("OnTradeTransaction: SendTradeEvent failed - telemetry uplink not connected");
   }
}
}

//+------------------------------------------------------------------+
//| OnNewBarEmaRb — EMA21 rubber band: enter NEAR EMA21, exit FAR    |
//+------------------------------------------------------------------+
void OnNewBarEmaRb(const datetime now, const bool allowEntries, const double equity, CTrade &t)
{
   static datetime g_lastEmaRbBar = 0;
   datetime bt = iTime(_Symbol, _Period, 0);
   if(bt <= 0 || bt == g_lastEmaRbBar) return;
   g_lastEmaRbBar = bt;

   const int need = 100;
   if(SovCopyBuffer(g_emaRbHema, 0, 1, need, g_emaRbBufEma, "EMA_RB_EMA") < need) return;
   if(SovCopyBuffer(g_emaRbHrsi, 0, 1, need, g_emaRbBufRsi, "EMA_RB_RSI") < need) return;
   if(SovCopyBuffer(g_emaRbHatr, 0, 1, need, g_emaRbBufAtr, "EMA_RB_ATR") < need) return;
   if(CopyClose(_Symbol, _Period, 1, need, g_emaRbBufCl) < need) return;
   if(CopyOpen(_Symbol, _Period, 1, need, g_emaRbBufOp) < need) return;
   if(CopyHigh(_Symbol, _Period, 1, need, g_emaRbBufHi) < need) return;
    if(CopyLow(_Symbol, _Period, 1, need, g_emaRbBufLo) < need) return;

   double c = g_emaRbBufCl[0];
   double o = g_emaRbBufOp[0];
   double rsi = g_emaRbBufRsi[0];
   double ema = g_emaRbBufEma[0];
   double emaPrev = g_emaRbBufEma[1];
   double atr = g_emaRbBufAtr[0];

   if(!(MathIsValidNumber(ema) && MathIsValidNumber(atr) && atr > 0 && MathIsValidNumber(rsi)))
      return;

   double entryLo = ema - InpEmaRbEntryAtr * atr;
   double entryHi = ema + InpEmaRbEntryAtr * atr;
   double exitLo  = ema - InpEmaRbExitAtr  * atr;
   double exitHi  = ema + InpEmaRbExitAtr  * atr;
   bool slopeUp = (ema > emaPrev);
   bool slopeDn = (ema < emaPrev);
   bool green = (c > o);
   bool red   = (c < o);

   //======================================================================
   //--- IN POSITION: manage exits
   //======================================================================
   if(g_emaRbInPosition)
   {
      g_emaRbBarsHeld++;
      bool exitNow = false;
      string exitReason = "";

      if(g_emaRbSide > 0)
      {
         //--- long exit: price extends far above EMA21
         if(c >= exitHi) { exitNow = true; exitReason = "ema_rb_exit_far"; }
         //--- hard stop / TP
         if(!exitNow)
         {
            double sl = g_emaRbEntryPrice - InpEmaRbStopAtr * g_emaRbEntryAtr;
            double tp = g_emaRbEntryPrice + InpEmaRbTpAtr * g_emaRbEntryAtr;
            if(c <= sl) { exitNow = true; exitReason = "ema_rb_stop"; }
            if(c >= tp) { exitNow = true; exitReason = "ema_rb_tp"; }
         }
      }
      else
      {
         //--- short exit: price extends far below EMA21
         if(c <= exitLo) { exitNow = true; exitReason = "ema_rb_exit_far"; }
         if(!exitNow)
         {
            double sl = g_emaRbEntryPrice + InpEmaRbStopAtr * g_emaRbEntryAtr;
            double tp = g_emaRbEntryPrice - InpEmaRbTpAtr * g_emaRbEntryAtr;
            if(c >= sl) { exitNow = true; exitReason = "ema_rb_stop"; }
            if(c <= tp) { exitNow = true; exitReason = "ema_rb_tp"; }
         }
      }

      if(exitNow)
      {
         double lot = CalcLotEmaRb(g_emaRbSide, c, g_emaRbEntryAtr);
         ulong ticket = 0;
         if(g_emaRbSide > 0) ticket = t.Sell(lot, _Symbol, c, 0, 0, exitReason);
         else                ticket = t.Buy(lot, _Symbol, c, 0, 0, exitReason);
         if(ticket > 0)
         {
            PrintFormat("Sovereign EMA21 RB %s: EXIT %s (side=%d barsHeld=%d c=%.5f ema=%.5f rsi=%.1f)",
                        _Symbol, exitReason, g_emaRbSide, g_emaRbBarsHeld, c, ema, rsi);
            g_emaRbInPosition = false;
            g_emaRbSide = 0;
            g_emaRbEntryPrice = 0.0;
            g_emaRbEntryAtr = 0.0;
            g_emaRbBarsHeld = 0;
         }
      }
      return;
   }

   //======================================================================
   //--- FLAT: look for entry
   //======================================================================
   if(!allowEntries) return;

   //--- long entry: near EMA21 from below, green, RSI not overbought
   bool longOk = (entryLo <= c && c <= ema) && green && (rsi < (double)InpEmaRbRsiMax);
   if(InpEmaRbRequireSlope) longOk = longOk && slopeUp;

   //--- short entry: near EMA21 from above, red, RSI not oversold
   bool shortOk = (ema <= c && c <= entryHi) && red && (rsi > (double)InpEmaRbRsiMin);
   if(InpEmaRbRequireSlope) shortOk = shortOk && slopeDn;

   if(longOk || shortOk)
   {
      int side = longOk ? 1 : -1;
      double slDist = InpEmaRbStopAtr * atr;
      double lot = CalcLotEmaRb(side, c, atr);

      double sl = 0.0, tp = 0.0;
      if(side > 0) { sl = c - slDist; tp = c + InpEmaRbTpAtr * atr; }
      else         { sl = c + slDist; tp = c - InpEmaRbTpAtr * atr; }

      ulong ticket = 0;
      if(side > 0) ticket = t.Buy(lot, _Symbol, c, sl, tp, "EMA21_RB_LONG");
      else         ticket = t.Sell(lot, _Symbol, c, sl, tp, "EMA21_RB_SHORT");

      if(ticket > 0)
      {
         g_emaRbInPosition = true;
         g_emaRbSide = side;
         g_emaRbEntryPrice = c;
         g_emaRbEntryAtr = atr;
         g_emaRbBarsHeld = 0;
         PrintFormat("Sovereign EMA21 RB %s: ENTRY %s (side=%d c=%.5f ema=%.5f rsi=%.1f atr=%.5f sl=%.5f tp=%.5f)",
                     _Symbol, (side > 0 ? "LONG" : "SHORT"), side, c, ema, rsi, atr, sl, tp);
      }
      else
      {
         SovLogError("EMA21 RB order failed", "retcode=" + IntegerToString((int)t.ResultRetcode()) +
                     " desc=" + t.ResultRetcodeDescription() + " err=" + IntegerToString(GetLastError()));
      }
   }
}

//+------------------------------------------------------------------+
//| CalcLotEmaRb — risk-based lot sizing for EMA21 rubber band       |
//+------------------------------------------------------------------+
double CalcLotEmaRb(const int side, const double price, const double atr)
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity <= 0) equity = 1000.0;
   double riskAmt = equity * (InpEmaRbRiskPct / 100.0);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tickSize <= 0) tickSize = _Point;
   if(tickValue <= 0) tickValue = 1.0;
   double slDist = InpEmaRbStopAtr * atr;
   if(slDist <= 0) slDist = 10 * _Point;
   double lots = riskAmt / (slDist / tickSize * tickValue);
   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   lots = MathFloor(lots / lotStep) * lotStep;
   lots = MathMax(minLot, MathMin(maxLot, lots));
   return lots;
}

//+------------------------------------------------------------------+
//| Expert deinit function                                            |
//+------------------------------------------------------------------+
//| Expert deinit function                                            |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(g_macroMaHandle != INVALID_HANDLE)
      IndicatorRelease(g_macroMaHandle);

   if(g_structAtrHandle != INVALID_HANDLE)
      IndicatorRelease(g_structAtrHandle);

   if(g_divRsiHandle != INVALID_HANDLE)
      IndicatorRelease(g_divRsiHandle);

   if(g_emaRbHema != INVALID_HANDLE)
      IndicatorRelease(g_emaRbHema);
   if(g_emaRbHrsi != INVALID_HANDLE)
      IndicatorRelease(g_emaRbHrsi);
   if(g_emaRbHatr != INVALID_HANDLE)
      IndicatorRelease(g_emaRbHatr);

   ObjectsDeleteAll(0, "SOV_DYN_");   // dynamic analyst markup
   g_plan.Deinit();
   g_ai.Clear(g_paint);
   g_paint.ClearAll();
   g_portfolio.Deinit();
   g_carry.Deinit();
   g_structRide.Deinit();
    g_rsiBand.Deinit();
    g_emaHarvest.Deinit();
   g_hedgeBlend.Deinit();
    g_regimeFilter.Deinit();
    g_compressionRubber.Deinit();
   g_perf.Deinit();
   g_traffic.Deinit();
   g_audit.Deinit();
   g_python.Deinit();
   g_telemetry.Deinit();
   g_errorLog.Deinit();
   Print("Sovereign EA deinitialized.");
}
