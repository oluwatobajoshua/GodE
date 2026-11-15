//+------------------------------------------------------------------+
//|                                         SovereignRSIBand.mqh     |
//|              RSI band ride (any TF, EURUSD/GBPUSD/USDJPY certified)
//
//  Senior-dev grade: every MQL5 API call checked, GetLastError() logged,
//  input validation at every boundary, graceful degradation on failure.
//
//  Entry:  trend filter (EMA21 slope + EMA50 alignment), RSI cross  |
//          through mid band in trend direction.                     |
//  Exit:   RSI cross back through hi/lo band (after min-hold) or   |
//          slope flip; optional time-based exit (m_maxBars).        |
//  Min-hold: 12 bars before RSI-level exit may fire.                |
//  Margin: RSI must cross by this many points, not merely touch.    |
//  Spread gate: skip entry when spread > trailing p90 of 500 bars.  |
//  1 position at a time. Risk % of equity per trade.                |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_RSI_BAND_MQH__
#define __SOVEREIGN_RSI_BAND_MQH__

#include <Trade\Trade.mqh>
#include <Sovereign\SovereignError.mqh>
#include <Sovereign\SovereignPortfolioRisk.mqh>
#include <Sovereign\SovereignSmartExecution.mqh>

class CSovereignPerformance;
class CSovereignTrafficLight;
class CSovereignAuditLog;
class CSovereignSmartExecution;
class CSovereignPortfolioRisk;

class CSovereignRSIBand
{
private:
   string   m_symbol;
   long     m_magic;
   int      m_hRsi9;
   int      m_hEma21;
   int      m_hEma50;
   int      m_hSmma100;
   int      m_hAtr14;

   ENUM_TIMEFRAMES m_timeframe;

    double   m_riskPct;     // risk per trade % of equity (or balance if m_riskUseBalance)
    bool     m_riskUseBalance; // when true, size risk off ACCOUNT_BALANCE instead of ACCOUNT_EQUITY
    double   m_minHoldBars; // bars before RSI-level exit may fire
   double   m_margin;      // RSI must cross by this many points
   int      m_spreadGatePct; // spread p90 percentile for gate
   int      m_rsiPeriod;   // RSI period
   int      m_rsiMid;      // RSI mid band
   int      m_rsiHi;       // RSI high exit band
   int      m_rsiLo;       // RSI low exit band
   double   m_deadband;    // EMA slope deadband
   int      m_maxBars;     // max bars held before time-based exit (0=disabled)
   int      m_spreadBuf[]; // rolling spread history
   int      m_spreadCount;
   int      m_spreadHead;

   double   m_atrHist[];   // rolling ATR history for percentile filter
   int      m_atrHistCount;
   int      m_atrHistHead;
   double   m_atrPctMax;   // skip entry when ATR > this percentile (0.80 = p80)

   int      m_maCrossBuf[];   // 1 if price crossed MA21 on that bar, 0 otherwise
   int      m_maCrossCount;   // count of crossovers in window
   int      m_maCrossHead;
   int      m_maCrossWindow;  // lookback window for crossover count
   int      m_maCrossMax;     // max allowed crossovers in window
   bool     m_pxAboveMA21Prev; // previous bar close vs MA21
   bool     m_ema21AboveEma50; // EMA21 above EMA50 (bullish alignment)
   int      m_emaCrossCount;   // EMA21/EMA50 crossover count in window
   int      m_emaCrossHead;   // ring head for EMA crossover buffer
   bool     m_ema21AboveEma50Prev; // previous bar EMA alignment

   int      m_emaCrossBuf[];   // rolling EMA21/EMA50 crossover history
   int      m_minConsecutiveLong;   // min consecutive closes above EMA21 for long entry
   int      m_minConsecutiveShort;  // min consecutive closes below EMA21 for short entry
   bool     m_requireEma21Slope;    // require EMA21 slope in trade direction
   int      m_consecutiveCloseCount; // current consecutive closes above/below EMA21
   int      m_consecLongStreak;      // consecutive closes above EMA21
   int      m_consecShortStreak;     // consecutive closes below EMA21
   bool     m_exitOnEmaBreak;        // exit when price closes beyond EMA21
   bool     m_longOnly;              // long-only mode (skip short entries)

    //--- macro trend filter (directional lock)
    int      m_macroMaHandle;         // external EMA 200 handle (owned by EA)
    double   m_macroMaBuf[];
    int      m_macroMaPeriod;         // macro MA period (e.g. 200)
    bool     m_macroMaReady;          // true once buffer has valid data

    //--- trailing stop / RSI exhaustion
    double   m_trailAtrMult;          // ATR multiplier for trailing stop
    double   m_trailMinStep;          // min price improvement to justify a SL modify
    datetime m_lastModifyTime;        // throttle PositionModify (once per bar minimum)
    int      m_rsiExhaustionLevel;    // RSI exhaustion exit for shorts (e.g. 30)
    int      m_maxPyramidLayers;      // max concurrent pyramid layers
    double   m_stopAtr;               // initial stop distance (x ATR)

    //--- bearish engulfing + RSI overbought short (downtrend continuation)
    bool     m_engulfingEnabled;       // engulfing short entry mode enabled
    int      m_engulfingRsiLevel;      // RSI overbought threshold (default 70)
    double   m_engulfingStopBufferPts; // buffer above engulfing high for stop (pts)
    bool     m_engulfingAllRedExit;    // exit on any green candle while in engulfing short
    bool     m_engulfingNoKissEma21;   // exit if price touches/kisses EMA21
    //--- engulfing trade state
    bool     m_engulfingActive;        // currently in an engulfing short
    double   m_engulfingStopPrice;     // hard stop above engulfing candle high
    bool     m_engulfingHasGreen;      // any green candle since entry
    bool     m_engulfingKissedEma;     // price touched/kissed EMA21 since entry
    //--- green-red short entry: fade EMA21 bounce in downtrend
    bool     m_greenRedEnabled;        // enable green-red short entry mode
    int      m_greenRedRsiLevel;       // RSI threshold on last green candle (default 40)
    bool     m_greenRedWaitForRed;     // wait for next red candle close to confirm (true) vs enter on last green (false)
    int      m_greenRedMaxGreenCandles;// max consecutive green candles near EMA21 before arming (default 3)
    //--- green-red trade state
    bool     m_greenRedArmed;          // armed: saw green candle(s) below EMA21 with RSI > threshold, waiting for red
    int      m_greenRedCount;          // consecutive green candles counted
    datetime m_greenRedBarTime;        // bar time when armed
    double   m_greenRedRsi;            // RSI value at arming bar
    double   m_greenRedEmaTolerance;   // EMA21 proximity tolerance (fraction, e.g. 0.002 = 0.2%)
    //--- EMA21 trend mode: immediate entry on green candle RSI>level in downtrend (short) / red candle RSI<level in uptrend (long)
    bool     m_emaTrendEnabled;        // enable EMA21 trend entry mode
    int      m_emaTrendRsiLevel;       // RSI threshold: short RSI>level, long RSI<(100-level)
    //--- EMA21 trend trade state
    bool     m_emaTrendActive;         // currently in an EMA21 trend trade
    int      m_emaTrendSide;           // side of current EMA21 trend trade (1=long,-1=short)
    datetime m_emaTrendEntryBarTime;   // bar time of entry
    //--- strength filters (bulls vs bears at EMA21)
    int      m_emaTrendSlopeBars;      // bars to measure EMA21 slope (default 5)
    double   m_emaTrendSlopeMin;       // min EMA21 slope (fraction of price per bar) - trend must be strong
    double   m_emaTrendBodyMinAtr;     // min candle body as fraction of ATR - rejection must be decisive
    int      m_emaTrendConsecutive;    // consecutive rejections needed before entry (stacking)
    bool     m_emaTrendHTFFilter;      // require higher-timeframe trend alignment
    ENUM_TIMEFRAMES m_emaTrendHTF;     // higher timeframe for alignment
    double   m_emaTrendEmaDistAtr;     // max distance from EMA21 in ATR for smart exit (exit only when price near EMA21)
    //--- strength state
    int      m_emaTrendConsecutiveCount;// consecutive rejection bars counted
    datetime m_emaTrendLastBarTime;    // last bar time processed for consecutive count
    double   m_emaTrendSlopeValue;     // current EMA21 slope value
    //--- three-consecutive-green below EMA21 short entry
    int      m_threeGreenCount;        // consecutive green candles below EMA21 counted
    datetime m_threeGreenLastBarTime;  // last bar time processed for three-green count
    //--- three-consecutive-red above EMA21 long entry
    int      m_threeRedCount;          // consecutive red candles above EMA21 counted
    datetime m_threeRedLastBarTime;    // last bar time processed for three-red count
    //--- reflex entry: streak below/above EMA21, then one candle crosses, then immediate reversal back
    int      m_reflexMinStreak;        // min consecutive candles below/above EMA21 before reflex triggers
    int      m_belowEmaStreak;         // consecutive candles below EMA21
    int      m_aboveEmaStreak;         // consecutive candles above EMA21
    bool     m_reflexCrossedUp;        // flag: green crossed up above EMA21 after below-ema streak
    bool     m_reflexCrossedDown;      // flag: red crossed down below EMA21 after above-ema streak
    //--- early trend-change detection: price closes above/below EMA21 and holds
    bool     m_earlyTrendEnabled;      // enable early trend-change entries (before EMA21/EMA50 cross)
    int      m_earlyTrendHoldBars;     // consecutive closes above/below EMA21 to confirm early flip
    double   m_earlyTrendSizeMult;     // lot size multiplier for early entries (0.5 = half size)
    int      m_earlyTrendRsiMax;       // early long: RSI must be below this (recovering from oversold)
    int      m_earlyTrendRsiMin;       // early short: RSI must be above this (recovering from overbought)
    int      m_consecAboveEma21;       // consecutive closes above EMA21
    int      m_consecBelowEma21;       // consecutive closes below EMA21
    datetime m_consecLastBarTime;      // last bar time for consecutive count
    //--- market structure filter: candle relationships vs EMA21
    bool     m_structureFilterEnabled; // enable market structure green/red candle filter
    double   m_redOpenHigh;            // highest open of recent red candles (uptrend structure level)
    double   m_greenCloseLow;          // lowest close of recent green candles (downtrend structure level)
    datetime m_structLastBarTime;      // last bar time structure was updated
    bool     m_structureIntactLong;    // uptrend structure intact (green reclaiming above red opens)
    bool     m_structureIntactShort;   // downtrend structure intact (red reclaiming below green closes)
    //--- stop placement
    int      m_emaTrendStopMode;       // 0=wick high/low, 1=ATR multiple, 2=EMA21 +/- buffer
    double   m_emaTrendStopAtrMult;    // ATR multiplier for stop (if mode 1)
    double   m_emaTrendStopDist;       // computed stop distance for ema-trend entries

    //--- breakeven (MFE-based) stage 1
    double   m_mfe;                   // max favorable excursion since entry (price units)
    bool     m_breakevenActivated;    // true once SL moved to breakeven
    double   m_breakevenAtR;          // activate breakeven when price reaches +N x stop distance
    double   m_breakevenBufferPts;    // extra buffer beyond entry price (points)
    double   m_maxLotSize;            // absolute max lot size per trade (0=disabled)
    bool     m_verifySLTP;            // verify SL/TP actually set on broker after order
    int      m_maxPositionAgeBars;    // close positions older than N bars (0=disabled)

   //--- re-entry cooldown (parity with research/rsi_band_net.py --cooldown)
   int      m_cooldownBars;          // bars to wait after an exit before re-entry (0=disabled)
   datetime m_lastExitTime;          // bar time of the last deliberate close
    double   m_emaBreakBufferAtr;     // EMA break buffer in ATR multiples for exits

    //--- pyramid state (mirrors EA globals for panel display)
    int      m_pyramidLegs;
    double   m_pyramidNetProfit;
    bool     m_pyramidArmed;

    //--- pyramiding EXECUTION (opt-in; off by default)
    bool     m_pyramidExec;        // when true, open additional legs in profit
    bool     m_pyramidAllowLoss;   // when true, allow pyramid legs even if existing legs are underwater
    double   m_scaleInAtrStep;     // ATR step between legs
    double   m_scaleInSizeMult;    // scale-in leg size vs initial leg (e.g. 0.5)
    int      m_legCount;           // legs currently open for this magic+symbol
    double   m_lastEntryPrice;     // price of the most recent leg

    //--- position state
   bool     m_inPosition;
   int      m_positionSide;    // +1 long, -1 short
   double   m_entryPrice;
   double   m_stopPrice;
   double   m_tpPrice;
   double   m_entryAtr;
   int      m_barsHeld;
   int      m_entryBarIndex;

   double   m_rsiBuf[];
   double   m_emaBuf[];
   double   m_ema50Buf[];
   double   m_atrBuf[];
   double   m_smma100Buf[];
    double   m_clBuf[];
    double   m_opBuf[];   // open prices (engulfing detection)
    double   m_hiBuf[];   // high prices (engulfing stop)
    datetime m_lastBarTime;
    bool     m_verboseLog;      // log every bar to Journal (false = events only)
    int      m_currentTrend;    // trend of most recent bar
    bool     m_intraBar;        // when true, evaluate entries on every tick (intra-bar)
    bool     m_forceBypassAllFilters; // when true, bypass all RSI band entry gates for core order placement test
    datetime m_lastEntryBarTime;// time of bar we last entered on (prevents same-bar re-entry)
     datetime m_lastBypassLogBar;// bar we last logged the force-bypass signal (log throttle)

    //--- smart execution & Kelly sizing (institutional risk modules)
    CSovereignSmartExecution *m_exec;      // smart execution engine (optional)
    CSovereignPortfolioRisk  *m_portRisk;  // portfolio risk engine (optional)
    bool     m_useSmartExec;               // route entries through smart execution
    double   m_kellySizeMult;              // Kelly size multiplier (1.0 = base lot)

   //--- error counters
   long     m_copyErrors;
   long     m_symbolErrors;
   long     m_tradeErrors;
   long     m_handleErrors;

   string   m_lastBlockReason;
   datetime m_lastBlockTime;
   datetime m_lastSymbolValidCheck;   // throttle SymbolValid to once per second
   bool     m_symbolValidCached;      // cached result from throttle

    //--- helpers with error context
    bool   SymbolValid();
    double SafeSymbolDouble(const int propId, const double def = 0.0, const string ctx = "");
    long   SafeSymbolInteger(const int propId, const long def = 0, const string ctx = "");
   bool   SafeCopyBuffer(const int handle, const int bufNum, const int start, const int count,
                         double &buf[], const string ctx = "");
   void   SovLogError(const string action, const string ctx = "") const;

   bool IsNewBar();
   bool CopyBuffers();
     int  ComputeTrend();
     void UpdateMarketStructure(const double closePx, const double openPx, const double ema21);
     bool IsBearishEngulfing();
    double P90Spread();
   bool SpreadAllowed();
    double NormalizeVolume(const double volume);
    double LotsForRisk(const double riskMoney, const double stopDist);
    void OpenPosition(const int side, const double price, const double atr,
                       CTrade &t, CSovereignPerformance &perf,
                       CSovereignTrafficLight &tl, CSovereignAuditLog &audit,
                       const string comment, const bool isScaleIn = false,
                       const double sizeMult = 1.0);
    void ClosePosition(CTrade &t, const string reason);
    int  CountLegs();
    void LogOrderAttempt(const string action, const double price,
                         const double volume, const uint retcode,
                         const string comment, const int spread,
                         CSovereignAuditLog &audit);
    void ApplyBreakeven(CTrade &t, const double closePx);
    bool VerifySLTP(CTrade &t, const ulong ticket, const double reqSL, const double reqTP, const int side);
    void CheckPositionAge(CTrade &t);

public:
   CSovereignRSIBand();
   ~CSovereignRSIBand();
    bool Init(const string symbol, const long magic,
               const ENUM_TIMEFRAMES timeframe,
               const double riskPct, const int minHoldBars,
               const double margin, const int spreadGatePct,
               const int rsiPeriod, const int rsiMid,
               const int rsiHi, const int rsiLo,
               const double deadband, const int maxBars,
               const double atrPctMax,      // ATR percentile gate (0.80 = p80)
               const bool verboseLog = false,
               const int maCrossMax = 5,    // max MA21 crossovers in window
               const int maCrossWindow = 50, // lookback bars for crossover count
               const int minConsecutiveLong = 0,  // min consecutive closes above EMA21 for long
               const int minConsecutiveShort = 0, // min consecutive closes below EMA21 for short
                const bool requireEma21Slope = false, // require EMA21 slope in trade direction
                const bool exitOnEmaBreak = true,    // exit when price closes beyond EMA21
                const double emaBreakBufferAtr = 0.0,// EMA break buffer in ATR multiples (0=exit on touch)
                const bool longOnly = false);        // long-only mode (skip short entries)
   void Deinit();
   void OnNewBar(const datetime now, const bool allowEntries, const double equity,
                 CSovereignPerformance &perf, CSovereignTrafficLight &tl,
                 CSovereignAuditLog &audit, CTrade &t);
     bool InPosition() const { return m_inPosition; }
     int  PositionSide() const { return m_positionSide; }
     double EntryPrice() const { return m_entryPrice; }
     double StopPrice() const { return m_stopPrice; }
     double TpPrice() const { return m_tpPrice; }
     long CopyErrors() const { return m_copyErrors; }
    long SymbolErrors() const { return m_symbolErrors; }
    long TradeErrors() const { return m_tradeErrors; }
    long HandleErrors() const { return m_handleErrors; }
    string LastBlockReason() const { return m_lastBlockReason; }
    datetime LastBlockTime() const { return m_lastBlockTime; }
    double CurrentRsi() const { return (ArraySize(m_rsiBuf) > 0) ? m_rsiBuf[0] : 0.0; }
    double CurrentRsiPrev() const { return (ArraySize(m_rsiBuf) > 1) ? m_rsiBuf[1] : 0.0; }
    int CurrentTrend() const { return m_currentTrend; }
    int BarsHeld() const { return m_barsHeld; }
    int MinHoldBars() const { return (int)m_minHoldBars; }
    int RsiMid() const { return m_rsiMid; }
    int RsiHi() const { return m_rsiHi; }
    int RsiLo() const { return m_rsiLo; }
    int CurrentSpread() const
    {
       int sz = (int)m_spreadBuf.Size();
       if(sz <= 0 || m_spreadCount <= 0) return 0;
       return m_spreadBuf[(m_spreadHead + m_spreadCount - 1) % sz];
    }
    double CurrentAtrPct() const { return m_atrPctMax; }
    bool AtrAllowed() const;   // ATR percentile gate
    int  CurrentMACrossCount() const { return m_maCrossCount; }
    int  CurrentMACrossMax() const { return m_maCrossMax; }
    bool MAIsClean() const;    // trend is clean (few MA crossovers + EMA21/EMA50 alignment)
    bool EMA21AboveEma50() const { return m_ema21AboveEma50; }
    int  CurrentEMACrossCount() const { return m_emaCrossCount; }
    int  CurrentConsecutiveCloses() const { return m_consecutiveCloseCount; }
    bool ConsecutiveClosesOk(const int side) const;  // enough consecutive closes for entry side
    bool Ema21SlopeOk(const int side) const;         // EMA21 slope points in trade direction
    bool ExitOnEmaBreak() const { return m_exitOnEmaBreak; }
    bool LongOnly() const { return m_longOnly; }

    //--- macro trend filter accessors
   void SetMacroMaValue(const double val) { m_macroMaBuf[0] = val; m_macroMaReady = true; }
   double MacroMaValue() const { return m_macroMaReady ? m_macroMaBuf[0] : 0.0; }
   bool   MacroMaReady() const { return m_macroMaReady; }
   void   SetIntraBar(bool v) { m_intraBar = v; }
   bool   IntraBar() const { return m_intraBar; }
   void   SetForceBypassAllFilters(bool v) { m_forceBypassAllFilters = v; }
   bool   ForceBypassAllFilters() const { return m_forceBypassAllFilters; }

   //--- smart execution & Kelly sizing setters
   void SetSmartExecution(CSovereignSmartExecution *exec, bool useIt)
   {
      m_exec = exec; m_useSmartExec = useIt;
   }
   void SetPortfolioRisk(CSovereignPortfolioRisk *portRisk)
   {
      m_portRisk = portRisk;
   }
   bool UseSmartExec() const { return m_useSmartExec; }

    //--- trailing stop / RSI exhaustion accessors
    void SetTrailAtrMult(const double v) { m_trailAtrMult = (v <= 0.0) ? 0.0 : MathMax(0.1, v); }
    double TrailAtrMult() const { return m_trailAtrMult; }
    void SetStopAtr(const double v) { m_stopAtr = (v > 0.0) ? v : 1.5; }
    double StopAtr() const { return m_stopAtr; }
    void SetRsiExhaustionLevel(const int v) { m_rsiExhaustionLevel = (v <= 0) ? 0 : MathMax(1, MathMin(99, v)); }
    int  RsiExhaustionLevel() const { return m_rsiExhaustionLevel; }
    void SetMaxPyramidLayers(const int v) { m_maxPyramidLayers = MathMax(1, v); }
    int  MaxPyramidLayers() const { return m_maxPyramidLayers; }

    //--- bearish engulfing + RSI overbought short setters
    void SetEngulfingEnabled(const bool v) { m_engulfingEnabled = v; }
    bool EngulfingEnabled() const { return m_engulfingEnabled; }
    void SetEngulfingRsiLevel(const int v) { m_engulfingRsiLevel = MathMax(1, MathMin(99, v)); }
    int  EngulfingRsiLevel() const { return m_engulfingRsiLevel; }
    void SetEngulfingStopBufferPts(const double v) { m_engulfingStopBufferPts = MathMax(0.0, v); }
    double EngulfingStopBufferPts() const { return m_engulfingStopBufferPts; }
    void SetEngulfingAllRedExit(const bool v) { m_engulfingAllRedExit = v; }
    bool EngulfingAllRedExit() const { return m_engulfingAllRedExit; }
    void SetEngulfingNoKissEma21(const bool v) { m_engulfingNoKissEma21 = v; }
    bool EngulfingNoKissEma21() const { return m_engulfingNoKissEma21; }

    //--- green-red short/long entry setters
    void SetGreenRedEnabled(const bool v) { m_greenRedEnabled = v; }
    bool GreenRedEnabled() const { return m_greenRedEnabled; }
    void SetGreenRedRsiLevel(const int v) { m_greenRedRsiLevel = MathMax(1, MathMin(99, v)); }
    int  GreenRedRsiLevel() const { return m_greenRedRsiLevel; }
    void SetGreenRedWaitForRed(const bool v) { m_greenRedWaitForRed = v; }
    bool GreenRedWaitForRed() const { return m_greenRedWaitForRed; }
    void SetGreenRedMaxGreenCandles(const int v) { m_greenRedMaxGreenCandles = MathMax(1, v); }
    int  GreenRedMaxGreenCandles() const { return m_greenRedMaxGreenCandles; }
    void SetGreenRedEmaTolerance(const double v) { m_greenRedEmaTolerance = MathMax(0.0, MathMin(0.05, v)); }
    double GreenRedEmaTolerance() const { return m_greenRedEmaTolerance; }

    //--- EMA21 trend mode setters
    void SetEmaTrendEnabled(const bool v) { m_emaTrendEnabled = v; }
    bool EmaTrendEnabled() const { return m_emaTrendEnabled; }
    void SetEmaTrendRsiLevel(const int v) { m_emaTrendRsiLevel = MathMax(1, MathMin(99, v)); }
    int  EmaTrendRsiLevel() const { return m_emaTrendRsiLevel; }
    void SetEmaTrendSlopeBars(const int v) { m_emaTrendSlopeBars = MathMax(2, v); }
    int  EmaTrendSlopeBars() const { return m_emaTrendSlopeBars; }
    void SetEmaTrendSlopeMin(const double v) { m_emaTrendSlopeMin = MathMax(0.0, v); }
    double EmaTrendSlopeMin() const { return m_emaTrendSlopeMin; }
    void SetEmaTrendBodyMinAtr(const double v) { m_emaTrendBodyMinAtr = MathMax(0.0, v); }
    double EmaTrendBodyMinAtr() const { return m_emaTrendBodyMinAtr; }
    void SetEmaTrendConsecutive(const int v) { m_emaTrendConsecutive = MathMax(1, v); }
    int  EmaTrendConsecutive() const { return m_emaTrendConsecutive; }
    void SetEmaTrendHTFFilter(const bool v) { m_emaTrendHTFFilter = v; }
    bool EmaTrendHTFFilter() const { return m_emaTrendHTFFilter; }
    void SetEmaTrendHTF(const ENUM_TIMEFRAMES v) { m_emaTrendHTF = v; }
    ENUM_TIMEFRAMES EmaTrendHTF() const { return m_emaTrendHTF; }
    void SetEmaTrendStopMode(const int v) { m_emaTrendStopMode = MathMax(0, MathMin(2, v)); }
    int  EmaTrendStopMode() const { return m_emaTrendStopMode; }
    void SetEmaTrendStopAtrMult(const double v) { m_emaTrendStopAtrMult = MathMax(0.5, v); }
    double EmaTrendStopAtrMult() const { return m_emaTrendStopAtrMult; }
    void SetEmaTrendEmaDistAtr(const double v) { m_emaTrendEmaDistAtr = MathMax(0.5, v); }
    double EmaTrendEmaDistAtr() const { return m_emaTrendEmaDistAtr; }

    //--- Reflex entry setters
    void SetReflexMinStreak(const int v) { m_reflexMinStreak = MathMax(2, v); }
    int  ReflexMinStreak() const { return m_reflexMinStreak; }

    //--- early trend-change setters
    void SetEarlyTrendEnabled(const bool v) { m_earlyTrendEnabled = v; }
    bool EarlyTrendEnabled() const { return m_earlyTrendEnabled; }
    void SetEarlyTrendHoldBars(const int v) { m_earlyTrendHoldBars = MathMax(1, v); }
    int  EarlyTrendHoldBars() const { return m_earlyTrendHoldBars; }
    void SetEarlyTrendSizeMult(const double v) { m_earlyTrendSizeMult = MathMax(0.1, MathMin(1.0, v)); }
    double EarlyTrendSizeMult() const { return m_earlyTrendSizeMult; }
    void SetEarlyTrendRsiMax(const int v) { m_earlyTrendRsiMax = MathMax(1, MathMin(99, v)); }
    int  EarlyTrendRsiMax() const { return m_earlyTrendRsiMax; }
    void SetEarlyTrendRsiMin(const int v) { m_earlyTrendRsiMin = MathMax(1, MathMin(99, v)); }
    int  EarlyTrendRsiMin() const { return m_earlyTrendRsiMin; }
    int  ConsecutiveAboveEma21() const { return m_consecAboveEma21; }
    int  ConsecutiveBelowEma21() const { return m_consecBelowEma21; }

    //--- market structure filter setters
    void SetStructureFilterEnabled(const bool v) { m_structureFilterEnabled = v; }
    bool StructureFilterEnabled() const { return m_structureFilterEnabled; }
    bool StructureIntactLong() const { return m_structureIntactLong; }
    bool StructureIntactShort() const { return m_structureIntactShort; }
    double RedOpenHigh() const { return m_redOpenHigh; }
    double GreenCloseLow() const { return m_greenCloseLow; }

    //--- pyramid state (mirrors EA globals for panel display)
    void SetPyramidState(const int legs, const double netProfit, const bool armed)
    {
       m_pyramidLegs = legs; m_pyramidNetProfit = netProfit; m_pyramidArmed = armed;
    }
    int  PyramidLegs() const { return m_pyramidLegs; }
    double PyramidNetProfit() const { return m_pyramidNetProfit; }
    bool PyramidArmed() const { return m_pyramidArmed; }

    //--- pyramiding EXECUTION control (opt-in, default off)
     void SetPyramidExec(const bool enabled, const int maxLayers, const double stepAtr)
     {
        m_pyramidExec    = enabled;
        m_maxPyramidLayers = MathMax(1, maxLayers);
        m_scaleInAtrStep = (stepAtr > 0.0) ? stepAtr : 0.75;
     }
      void SetPyramidAllowLoss(const bool v) { m_pyramidAllowLoss = v; }
   bool   PyramidAllowLoss() const { return m_pyramidAllowLoss; }
    bool   PyramidExecEnabled() const { return m_pyramidExec; }
    int  LegCount() const { return m_legCount; }
    double LastEntryPrice() const { return m_lastEntryPrice; }

    //--- risk sizing controls
    void SetRiskPct(const double v) { m_riskPct = (v > 0.0) ? v : 0.1; }
    void SetRiskUseBalance(const bool v) { m_riskUseBalance = v; }
    bool RiskUseBalance() const { return m_riskUseBalance; }

   //--- trailing stop tuning
   void SetTrailMinStep(const double v) { m_trailMinStep = MathMax(0.0, v); }
   double TrailMinStep() const { return m_trailMinStep; }

   //--- breakeven controls
   void SetBreakevenAtR(const double v) { m_breakevenAtR = MathMax(0.0, v); }
   double BreakevenAtR() const { return m_breakevenAtR; }
   void SetBreakevenBufferPts(const double v) { m_breakevenBufferPts = MathMax(0.0, v); }
   double BreakevenBufferPts() const { return m_breakevenBufferPts; }

   //--- hard limits & verification
   void SetMaxLotSize(const double v) { m_maxLotSize = MathMax(0.0, v); }
   double MaxLotSize() const { return m_maxLotSize; }
   void SetVerifySLTP(const bool v) { m_verifySLTP = v; }
   bool VerifySLTP() const { return m_verifySLTP; }
    void SetMaxPositionAgeBars(const int v) { m_maxPositionAgeBars = MathMax(0, v); }
    //--- re-entry cooldown in bars (research/rsi_band_net.py --cooldown)
    void SetCooldownBars(const int v) { m_cooldownBars = MathMax(0, v); }
    int  CooldownBars() const { return m_cooldownBars; }
    void SetEmaBreakBufferAtr(const double v) { m_emaBreakBufferAtr = MathMax(0.0, v); }
    double EmaBreakBufferAtr() const { return m_emaBreakBufferAtr; }
   int MaxPositionAgeBars() const { return m_maxPositionAgeBars; }

   //--- state recovery: re-adopt live Sovereign positions after EA restart /
   //--- timeframe change so open positions are never orphaned (no trailing /
   //--- RSI-exit management after a terminal restart).
   bool RecoverState(CTrade &t);
};

//+------------------------------------------------------------------+
//| Constructor                                                      |
//+------------------------------------------------------------------+
CSovereignRSIBand::CSovereignRSIBand()
{
   m_symbol = "";
   m_magic = 0;
   m_hRsi9 = INVALID_HANDLE;
    m_hEma21 = INVALID_HANDLE;
    m_hEma50 = INVALID_HANDLE;
    m_hSmma100 = INVALID_HANDLE;
    m_hAtr14 = INVALID_HANDLE;
   m_timeframe = PERIOD_CURRENT;
   m_riskPct = 0.5;
   m_riskUseBalance = false;
   m_minHoldBars = 12;
   m_margin = 3.0;
   m_spreadGatePct = 90;
   m_rsiPeriod = 9;
   m_rsiMid = 50;
   m_rsiHi = 75;
   m_rsiLo = 25;
   m_deadband = 0.20;
   m_maxBars = 0;
   m_inPosition = false;
   m_positionSide = 0;
   m_entryPrice = 0.0;
   m_stopPrice = 0.0;
   m_tpPrice = 0.0;
   m_entryAtr = 0.0;
   m_barsHeld = 0;
   m_entryBarIndex = 0;
    m_spreadCount = 0;
    m_spreadHead = 0;
    m_lastBarTime = 0;
      m_verboseLog = false;
      m_intraBar = false;
      m_forceBypassAllFilters = false;
      m_lastEntryBarTime = 0;
      m_lastBypassLogBar = 0;
      m_exec = NULL;
      m_portRisk = NULL;
      m_useSmartExec = false;
      m_kellySizeMult = 1.0;
      m_currentTrend = 0;
     m_copyErrors = 0;
     m_symbolErrors = 0;
     m_tradeErrors = 0;
     m_handleErrors = 0;
     m_lastBlockReason = "";
     m_lastBlockTime = 0;
     m_lastSymbolValidCheck = 0;
     m_symbolValidCached = true;
     m_atrPctMax = 0.80;
     m_atrHistCount = 0;
     m_atrHistHead = 0;
     m_maCrossCount = 0;
     m_maCrossHead = 0;
     m_maCrossWindow = 50;
     m_maCrossMax = 5;
     m_pxAboveMA21Prev = false;
   ArrayInitialize(m_rsiBuf, 0.0);
   ArrayInitialize(m_emaBuf, 0.0);
   ArrayInitialize(m_ema50Buf, 0.0);
   ArrayInitialize(m_atrBuf, 0.0);
   ArrayInitialize(m_clBuf, 0.0);
   ArrayResize(m_atrHist, 500);
   ArrayInitialize(m_atrHist, 0.0);
   ArrayResize(m_macroMaBuf, 3);
   ArrayInitialize(m_macroMaBuf, 0.0);
   m_macroMaHandle = INVALID_HANDLE;
   m_macroMaPeriod = 200;
   m_macroMaReady = false;
   m_trailAtrMult = 1.5;
   m_trailMinStep = 0.0;
   m_lastModifyTime = 0;
   m_rsiExhaustionLevel = 30;
   m_maxPyramidLayers = 3;
   m_stopAtr = 1.5;
   m_mfe = 0.0;
   m_breakevenActivated = false;
   m_breakevenAtR = 1.0;
   m_breakevenBufferPts = 0.0;
   m_maxLotSize = 0.0;
   m_verifySLTP = true;
    m_maxPositionAgeBars = 0;
   m_cooldownBars = 0;
   m_lastExitTime = 0;
    m_emaBreakBufferAtr = 0.0;
    m_pyramidLegs = 0;
   m_pyramidNetProfit = 0.0;
   m_pyramidArmed = true;
    m_pyramidExec = false;        // opt-in: never pyramids unless enabled
    m_pyramidAllowLoss = false;   // allow pyramid legs even if existing legs are underwater
    m_scaleInAtrStep = 0.75;
   m_scaleInSizeMult = 0.5;      // each scale-in leg is half the initial size
   m_legCount = 0;
    m_lastEntryPrice = 0.0;
    m_engulfingEnabled = false;
    m_engulfingRsiLevel = 70;
    m_engulfingStopBufferPts = 10.0;
    m_engulfingAllRedExit = true;
    m_engulfingNoKissEma21 = true;
    m_engulfingActive = false;
    m_engulfingStopPrice = 0.0;
    m_engulfingHasGreen = false;
    m_engulfingKissedEma = false;
    m_greenRedEnabled = false;
    m_greenRedRsiLevel = 40;
    m_greenRedWaitForRed = true;
    m_greenRedMaxGreenCandles = 3;
    m_greenRedArmed = false;
    m_greenRedCount = 0;
    m_greenRedBarTime = 0;
    m_greenRedRsi = 0.0;
    m_greenRedEmaTolerance = 0.002;
    m_emaTrendEnabled = false;
    m_emaTrendRsiLevel = 45;
    m_emaTrendActive = false;
    m_emaTrendSide = 0;
    m_emaTrendEntryBarTime = 0;
    m_emaTrendSlopeBars = 5;
    m_emaTrendSlopeMin = 0.0001;
    m_emaTrendBodyMinAtr = 0.5;
    m_emaTrendConsecutive = 2;
    m_emaTrendHTFFilter = false;
    m_emaTrendHTF = PERIOD_H4;
    m_emaTrendConsecutiveCount = 0;
    m_emaTrendLastBarTime = 0;
    m_emaTrendSlopeValue = 0.0;
    m_threeGreenCount = 0;
    m_threeGreenLastBarTime = 0;
    m_threeRedCount = 0;
    m_threeRedLastBarTime = 0;
    m_reflexMinStreak = 3;
    m_belowEmaStreak = 0;
    m_aboveEmaStreak = 0;
    m_reflexCrossedUp = false;
    m_reflexCrossedDown = false;
    m_emaTrendStopMode = 0;
    m_emaTrendStopAtrMult = 1.5;
    m_emaTrendStopDist = 0.0;
    m_emaTrendEmaDistAtr = 1.5;
    m_earlyTrendEnabled = true;
    m_earlyTrendHoldBars = 2;
    m_earlyTrendSizeMult = 0.5;
    m_earlyTrendRsiMax = 50;
    m_earlyTrendRsiMin = 50;
    m_consecAboveEma21 = 0;
    m_consecBelowEma21 = 0;
    m_consecLastBarTime = 0;
    m_structureFilterEnabled = false;
    m_redOpenHigh = 0.0;
    m_greenCloseLow = 0.0;
    m_structLastBarTime = 0;
    m_structureIntactLong = false;
    m_structureIntactShort = false;
}

//+------------------------------------------------------------------+
//| Destructor                                                       |
//+------------------------------------------------------------------+
CSovereignRSIBand::~CSovereignRSIBand()
{
   Deinit();
}

//+------------------------------------------------------------------+
//| SymbolValid â€” confirm the symbol is tradable in Market Watch     |
//+------------------------------------------------------------------+
bool CSovereignRSIBand::SymbolValid()
{
   if(StringLen(m_symbol) == 0)
      return false;

   //--- throttle: only re-check once per second to avoid Journal spam
   datetime now = TimeCurrent();
   if(now - m_lastSymbolValidCheck < 1)
      return m_symbolValidCached;   // cached result
   m_lastSymbolValidCheck = now;

   long sel = SymbolSelect(m_symbol, false);
   if(sel <= 0)
   {
      //--- symbol not in watchlist, try to add it
      if(!SymbolSelect(m_symbol, true))
      {
         m_symbolErrors++;
         m_symbolValidCached = false;
         return false;
      }
   }
   //--- SYMBOL_DIGITS is an ENUM_SYMBOL_INFO_INTEGER property, so use
   //--- SymbolInfoInteger directly (no double overload exists for it).
   long digitsInt = SymbolInfoInteger(m_symbol, SYMBOL_DIGITS);
   if(digitsInt == 0 && GetLastError() != 0)
   {
      ResetLastError();
      m_symbolErrors++;
      m_symbolValidCached = false;
      return false;
   }
   m_symbolValidCached = true;
   return true;
}

//+------------------------------------------------------------------+
//| SafeSymbolDouble â€” SymbolInfoDouble with error logging           |
//+------------------------------------------------------------------+
double CSovereignRSIBand::SafeSymbolDouble(const int propId, const double def, const string ctx)
{
   double v = SovSymbolDouble(m_symbol, propId, def, ctx);
   if(v == def && GetLastError() != 0)
      m_symbolErrors++;
   return v;
}

//+------------------------------------------------------------------+
//| SafeSymbolInteger â€” SymbolInfoInteger with error logging         |
//+------------------------------------------------------------------+
long CSovereignRSIBand::SafeSymbolInteger(const int propId, const long def, const string ctx)
{
   long v = SovSymbolInteger(m_symbol, propId, def, ctx);
   if(v == def && GetLastError() != 0)
      m_symbolErrors++;
   return v;
}

//+------------------------------------------------------------------+
//| SafeCopyBuffer â€” CopyBuffer with error logging                   |
//+------------------------------------------------------------------+
bool CSovereignRSIBand::SafeCopyBuffer(const int handle, const int bufNum, const int start,
                                       const int count, double &buf[], const string ctx)
{
   if(handle == INVALID_HANDLE)
   {
      m_handleErrors++;
      SovLogError(ctx + " invalid indicator handle", m_symbol);
      return false;
   }
   int copied = CopyBuffer(handle, bufNum, start, count, buf);
   if(copied < count)
   {
      m_copyErrors++;
      SovLogError(ctx + " CopyBuffer returned " + IntegerToString(copied) + "/" + IntegerToString(count), m_symbol);
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| AtrAllowed â€” skip entry when ATR > trailing percentile threshold |
//+------------------------------------------------------------------+
bool CSovereignRSIBand::AtrAllowed() const
{
   if(m_atrPctMax <= 0.0 || m_atrPctMax >= 1.0) return true;   // disabled
   if(m_atrHistCount < 100) return true;                       // not enough history yet
   if(ArraySize(m_atrBuf) < 1) return true;                    // buffers not loaded yet

   double curAtr = m_atrBuf[0];
   if(curAtr <= 0.0) return true;

   //--- count how many historical ATR values are below current ATR
   int below = 0;
   int n = m_atrHistCount;
   for(int i = 0; i < n; i++)
   {
      if(m_atrHist[i] > 0.0 && m_atrHist[i] < curAtr)
         below++;
   }

   double pct = (double)below / (double)n;
    return pct <= m_atrPctMax;
}

//+------------------------------------------------------------------+
//| MAIsClean â€” trend is clean when few MA21 crossovers in window    |
//+------------------------------------------------------------------+
bool CSovereignRSIBand::MAIsClean() const
{
   if(m_maCrossWindow <= 0 || m_maCrossBuf.Size() <= 0) return true;
   if(m_maCrossCount > m_maCrossMax) return false;
   return m_ema21AboveEma50;   // EMA21 vs EMA50 alignment
}

//+------------------------------------------------------------------+
//| ConsecutiveClosesOk â€” enough consecutive closes on entry side    |
//+------------------------------------------------------------------+
bool CSovereignRSIBand::ConsecutiveClosesOk(const int side) const
{
   if(side > 0)
   {
      if(m_minConsecutiveLong <= 0) return true;
      return m_consecutiveCloseCount >= m_minConsecutiveLong;
   }
   if(side < 0)
   {
      if(m_minConsecutiveShort <= 0) return true;
      return m_consecutiveCloseCount >= m_minConsecutiveShort;
   }
   return true;
}

//+------------------------------------------------------------------+
//| Ema21SlopeOk â€” EMA21 slope points in trade direction             |
//+------------------------------------------------------------------+
bool CSovereignRSIBand::Ema21SlopeOk(const int side) const
{
   if(!m_requireEma21Slope) return true;
   if(ArraySize(m_emaBuf) < 4) return true;

   double slope = m_emaBuf[0] - m_emaBuf[3];   // EMA21 change over 3 bars
   if(side > 0) return slope > 0.0;
   return slope < 0.0;
}

//+------------------------------------------------------------------+
//| LogError â€” central error logger with GetLastError                |
//+------------------------------------------------------------------+
void CSovereignRSIBand::SovLogError(const string action, const string ctx) const
{
   int err = GetLastError();
   PrintFormat("Sovereign RSI band %s: ERROR %s ctx=%s err=%d (%s)",
               m_symbol, action, ctx, err, IntegerToString(err));
   g_errorLog.LogError(m_symbol + " " + action + " " + ctx, "err=" + IntegerToString(err) + " desc=" + IntegerToString(err));
}

//+------------------------------------------------------------------+
//| Init                                                             |
//+------------------------------------------------------------------+
bool CSovereignRSIBand::Init(const string symbol, const long magic,
                              const ENUM_TIMEFRAMES timeframe,
                              const double riskPct, const int minHoldBars,
                              const double margin, const int spreadGatePct,
                              const int rsiPeriod, const int rsiMid,
                              const int rsiHi, const int rsiLo,
                              const double deadband, const int maxBars,
                              const double atrPctMax,
                              const bool verboseLog,
                              const int maCrossMax,
                              const int maCrossWindow,
                              const int minConsecutiveLong,
                              const int minConsecutiveShort,
                               const bool requireEma21Slope,
                               const bool exitOnEmaBreak,
                               const double emaBreakBufferAtr,
                               const bool longOnly)
{
   if(StringLen(symbol) == 0)
   {
      Print("Sovereign RSI band: ERROR Init called with empty symbol");
      return false;
   }
   if(magic == 0)
   {
      Print("Sovereign RSI band: ERROR Init called with magic=0");
      return false;
   }

   m_symbol = symbol;
   m_magic = magic;
   m_timeframe = timeframe;
   m_riskPct = MathMax(0.01, riskPct);
   m_minHoldBars = MathMax(1, minHoldBars);
   m_margin = MathMax(0.0, margin);
   m_spreadGatePct = MathMin(100, MathMax(1, spreadGatePct));
   m_rsiPeriod = MathMax(2, rsiPeriod);
   m_rsiMid = rsiMid;
   m_rsiHi = rsiHi;
   m_rsiLo = rsiLo;
   m_deadband = MathMax(0.0, deadband);
    m_maxBars = MathMax(0, maxBars);
    m_atrPctMax = MathMin(1.0, MathMax(0.0, atrPctMax));
    m_verboseLog = verboseLog;
    m_maCrossMax = MathMax(0, maCrossMax);
    m_maCrossWindow = MathMax(10, maCrossWindow);
    ArrayResize(m_maCrossBuf, m_maCrossWindow);
    ArrayInitialize(m_maCrossBuf, 0);
     m_maCrossCount = 0;
     m_maCrossHead = 0;
     ArrayResize(m_atrHist, 500);
     ArrayInitialize(m_atrHist, 0.0);
     m_atrHistCount = 0;
     m_atrHistHead = 0;
     m_pxAboveMA21Prev = false;
     m_ema21AboveEma50 = true;
     m_emaCrossCount = 0;
     m_emaCrossHead = 0;
      m_ema21AboveEma50Prev = true;
      m_minConsecutiveLong = MathMax(0, minConsecutiveLong);
      m_minConsecutiveShort = MathMax(0, minConsecutiveShort);
      m_requireEma21Slope = requireEma21Slope;
      m_consecutiveCloseCount = 0;
      m_consecLongStreak = 0;
      m_consecShortStreak = 0;
      m_exitOnEmaBreak = exitOnEmaBreak;
      m_emaBreakBufferAtr = emaBreakBufferAtr;
      m_longOnly = longOnly;
      ArrayResize(m_emaCrossBuf, m_maCrossWindow);
     ArrayInitialize(m_emaCrossBuf, 0);

   if(!SymbolValid())
   {
      PrintFormat("Sovereign RSI band %s: ERROR symbol not tradable", m_symbol);
      return false;
   }

   m_hRsi9 = iRSI(m_symbol, m_timeframe, m_rsiPeriod, PRICE_CLOSE);
    m_hEma21 = iMA(m_symbol, m_timeframe, 21, 0, MODE_EMA, PRICE_CLOSE);
    m_hEma50 = iMA(m_symbol, m_timeframe, 50, 0, MODE_EMA, PRICE_CLOSE);
    m_hSmma100 = iMA(m_symbol, m_timeframe, 100, 0, MODE_SMMA, PRICE_CLOSE);
    m_hAtr14 = iATR(m_symbol, m_timeframe, 14);

    if(m_hRsi9 == INVALID_HANDLE)
    {
       SovLogError("iRSI handle", m_symbol);
       return false;
    }
    if(m_hEma21 == INVALID_HANDLE)
    {
       SovLogError("iMA(21) handle", m_symbol);
       return false;
    }
    if(m_hEma50 == INVALID_HANDLE)
    {
       SovLogError("iMA(50) handle", m_symbol);
       return false;
    }
    if(m_hSmma100 == INVALID_HANDLE)
    {
       SovLogError("iMA(100 SMMA) handle", m_symbol);
       return false;
    }
    if(m_hAtr14 == INVALID_HANDLE)
    {
       SovLogError("iATR handle", m_symbol);
       return false;
    }

   ArraySetAsSeries(m_spreadBuf, true);
   ArrayResize(m_spreadBuf, 500);
   ArrayInitialize(m_spreadBuf, 0);

   m_inPosition = false;
   m_positionSide = 0;
   m_entryPrice = 0.0;
   m_stopPrice = 0.0;
   m_tpPrice = 0.0;
   m_entryAtr = 0.0;
   m_barsHeld = 0;
   m_entryBarIndex = 0;
   m_lastBarTime = 0;
   m_legCount = 0;
   m_lastEntryPrice = 0.0;

   PrintFormat("Sovereign RSI band %s: INIT OK riskPct=%.2f%% minHold=%d margin=%.1f "
               "spreadGatePct=%d rsiPeriod=%d mid=%d hi=%d lo=%d deadband=%.2f maxBars=%d",
               m_symbol, m_riskPct, (int)m_minHoldBars, m_margin, m_spreadGatePct,
               m_rsiPeriod, m_rsiMid, m_rsiHi, m_rsiLo, m_deadband, m_maxBars);
   return true;
}

//+------------------------------------------------------------------+
//| Deinit                                                           |
//+------------------------------------------------------------------+
void CSovereignRSIBand::Deinit()
{
   if(m_hRsi9 != INVALID_HANDLE) { IndicatorRelease(m_hRsi9); m_hRsi9 = INVALID_HANDLE; }
   if(m_hEma21 != INVALID_HANDLE) { IndicatorRelease(m_hEma21); m_hEma21 = INVALID_HANDLE; }
    if(m_hEma50 != INVALID_HANDLE) { IndicatorRelease(m_hEma50); m_hEma50 = INVALID_HANDLE; }
    if(m_hSmma100 != INVALID_HANDLE) { IndicatorRelease(m_hSmma100); m_hSmma100 = INVALID_HANDLE; }
    if(m_hAtr14 != INVALID_HANDLE) { IndicatorRelease(m_hAtr14); m_hAtr14 = INVALID_HANDLE; }
}

//+------------------------------------------------------------------+
//| IsNewBar â€” detect a new bar                                      |
//+------------------------------------------------------------------+
bool CSovereignRSIBand::IsNewBar()
{
   SovResetLastError();
   datetime barTime = iTime(m_symbol, m_timeframe, 0);
   if(barTime <= 0)
   {
      m_handleErrors++;
      SovLogError("iTime", m_symbol);
      return false;
    }
   if(barTime != m_lastBarTime)
   {
      m_lastBarTime = barTime;
      return true;
    }
   return false;
}

//+------------------------------------------------------------------+
//| CopyBuffers â€” copy completed-bar data (skip forming bar 0)       |
//+------------------------------------------------------------------+
bool CSovereignRSIBand::CopyBuffers()
{
   int need = 60;
   int start = m_intraBar ? 0 : 1; // intra-bar: include forming bar 0 for live entry

   if(!SafeCopyBuffer(m_hRsi9, 0, start, need, m_rsiBuf, "RSI"))
      return false;
   if(!SafeCopyBuffer(m_hEma21, 0, start, need, m_emaBuf, "EMA21"))
      return false;
    if(!SafeCopyBuffer(m_hEma50, 0, start, need, m_ema50Buf, "EMA50"))
       return false;
    if(!SafeCopyBuffer(m_hSmma100, 0, start, need, m_smma100Buf, "SMMA100"))
       return false;
    if(!SafeCopyBuffer(m_hAtr14, 0, start, need, m_atrBuf, "ATR14"))
       return false;

    SovResetLastError();
    if(CopyClose(m_symbol, m_timeframe, start, need, m_clBuf) < need)
    {
       m_copyErrors++;
       SovLogError("CopyClose", m_symbol);
       return false;
    }

    SovResetLastError();
    if(CopyOpen(m_symbol, m_timeframe, start, need, m_opBuf) < need)
    {
       m_copyErrors++;
       SovLogError("CopyOpen", m_symbol);
       return false;
    }

    SovResetLastError();
    if(CopyHigh(m_symbol, m_timeframe, start, need, m_hiBuf) < need)
    {
       m_copyErrors++;
       SovLogError("CopyHigh", m_symbol);
       return false;
    }

    ArraySetAsSeries(m_rsiBuf, true);
    ArraySetAsSeries(m_emaBuf, true);
    ArraySetAsSeries(m_ema50Buf, true);
    ArraySetAsSeries(m_smma100Buf, true);
    ArraySetAsSeries(m_atrBuf, true);
    ArraySetAsSeries(m_clBuf, true);
    ArraySetAsSeries(m_opBuf, true);
    ArraySetAsSeries(m_hiBuf, true);

    //--- ATR history for percentile gate: store most recent completed-bar ATR
    double curAtr = m_atrBuf[0];
    if(curAtr > 0.0)
    {
       if(m_atrHistCount < 500)
       {
          m_atrHist[m_atrHistCount] = curAtr;
          m_atrHistCount++;
       }
       else
       {
          m_atrHist[m_atrHistHead] = curAtr;
          m_atrHistHead = (m_atrHistHead + 1) % 500;
       }
    }

    //--- MA21 crossover tracking for clean-trend filter
    double curClose = m_clBuf[0];
    double curMA21  = m_emaBuf[0];
    double curEMA50 = m_ema50Buf[0];
    bool pxAboveMA21 = (curClose > curMA21);

    // EMA21 vs EMA50 alignment: crossover confirms strong direction
    bool ema21AboveEma50 = (curMA21 > curEMA50);
    int emaCross = 0;
    if(m_ema21AboveEma50Prev != ema21AboveEma50)
    {
       emaCross = 1;
    }
    m_ema21AboveEma50 = ema21AboveEma50;
    m_ema21AboveEma50Prev = ema21AboveEma50;

    // Store EMA crossover flag in ring buffer
    if(m_maCrossBuf.Size() > 0)
    {
       m_emaCrossCount -= m_emaCrossBuf[m_emaCrossHead];
       m_emaCrossBuf[m_emaCrossHead] = emaCross;
       m_emaCrossCount += emaCross;
       m_emaCrossHead = (m_emaCrossHead + 1) % (int)m_emaCrossBuf.Size();
    }

    int cross = 0;
    if(m_pxAboveMA21Prev != pxAboveMA21 && m_pxAboveMA21Prev != false) // not first bar
    {
       cross = 1;
    }
    m_pxAboveMA21Prev = pxAboveMA21;

    // Store price/MA21 crossover flag â€” reuses same ring buffer (count both types)
    if(m_maCrossBuf.Size() > 0)
    {
       m_maCrossCount -= m_maCrossBuf[m_maCrossHead];
       m_maCrossBuf[m_maCrossHead] = cross;
       m_maCrossCount += cross;
       m_maCrossHead = (m_maCrossHead + 1) % (int)m_maCrossBuf.Size();
    }

    //--- Consecutive closes above/below EMA21 tracking
    if(curClose > curMA21)
    {
       m_consecLongStreak++;
       m_consecShortStreak = 0;
       m_consecutiveCloseCount = m_consecLongStreak;
    }
    else if(curClose < curMA21)
    {
       m_consecShortStreak++;
       m_consecLongStreak = 0;
       m_consecutiveCloseCount = m_consecShortStreak;
    }
    else
    {
       m_consecLongStreak = 0;
       m_consecShortStreak = 0;
       m_consecutiveCloseCount = 0;
    }

    return true;
}

//+------------------------------------------------------------------+
//| ComputeTrend â€” EMA21 slope over 3 bars / ATR, deadbanded         |
//+------------------------------------------------------------------+
int CSovereignRSIBand::ComputeTrend()
{
   //--- defensive: ensure arrays have enough data
   if(ArraySize(m_emaBuf) < 4 || ArraySize(m_atrBuf) < 1 || ArraySize(m_ema50Buf) < 1)
      return 0;

   double ema1 = m_emaBuf[0];
   double ema4 = m_emaBuf[3];
   double atr1 = m_atrBuf[0];
   double ema50 = m_ema50Buf[0];
   double closePx = m_clBuf[0];

   if(ema1 <= 0.0 || ema4 <= 0.0 || atr1 <= 0.0 || ema50 <= 0.0)
      return 0;

   double slope3 = (ema1 - ema4) / atr1;

   //--- track consecutive closes above/below EMA21 for early trend-change detection
   datetime curBarTime = iTime(m_symbol, m_timeframe, 0);
   if(curBarTime > 0 && curBarTime != m_consecLastBarTime)
   {
      if(closePx > ema1) { m_consecAboveEma21++; m_consecBelowEma21 = 0; }
      else if(closePx < ema1) { m_consecBelowEma21++; m_consecAboveEma21 = 0; }
      else { m_consecAboveEma21 = 0; m_consecBelowEma21 = 0; }
      m_consecLastBarTime = curBarTime;
   }

   //--- early trend-change: price closes above EMA21 and holds, before EMA21/EMA50 align
   if(m_earlyTrendEnabled && m_consecAboveEma21 >= m_earlyTrendHoldBars && ema1 <= ema50)
      return 2;   // EARLY_LONG — trend changing, not yet EMA-confirmed
   if(m_earlyTrendEnabled && m_consecBelowEma21 >= m_earlyTrendHoldBars && ema1 >= ema50)
      return -2;  // EARLY_SHORT — trend changing, not yet EMA-confirmed

    //--- template alignment: EMA21 must be above EMA50 for long, below for short
    if(slope3 > m_deadband && ema1 > ema50)  return 1;
    if(slope3 < -m_deadband && ema1 < ema50) return -1;
    return 0;
}

//+------------------------------------------------------------------+
//| UpdateMarketStructure â€” track candle open/close vs EMA21         |
//| Uptrend: green close must reclaim above prior red opens.           |
//| Downtrend: red close must break below prior green closes.          |
//+------------------------------------------------------------------+
void CSovereignRSIBand::UpdateMarketStructure(const double closePx, const double openPx, const double ema21)
{
   if(!m_structureFilterEnabled) return;

   datetime curBarTime = iTime(m_symbol, m_timeframe, 0);
   if(curBarTime <= 0 || curBarTime == m_structLastBarTime) return;
   m_structLastBarTime = curBarTime;

   bool isGreen = (closePx > openPx);
   bool isRed   = (closePx < openPx);
   bool aboveEma21 = (closePx > ema21);
   bool belowEma21 = (closePx < ema21);

   //--- Uptrend structure (price above EMA21): green must close above prior red opens
   if(aboveEma21)
   {
      if(isRed)
      {
         //--- track red open as level green must reclaim
         if(m_redOpenHigh == 0.0 || openPx > m_redOpenHigh)
            m_redOpenHigh = openPx;
         m_structureIntactLong = false;   // red appeared, waiting for green reclaim
      }
      else if(isGreen)
      {
         if(m_redOpenHigh > 0.0 && closePx > m_redOpenHigh)
         {
            //--- structure intact: green reclaimed above all red opens
            m_structureIntactLong = true;
            m_redOpenHigh = 0.0;          // reset after successful reclaim
         }
         else if(m_redOpenHigh > 0.0 && closePx <= m_redOpenHigh)
         {
            //--- green failed to reclaim → sellers stepping in
            m_structureIntactLong = false;
         }
         else
         {
            m_structureIntactLong = true; // no prior red to reclaim
         }
      }
   }
   else
   {
      m_redOpenHigh = 0.0;
      m_structureIntactLong = false;
   }

   //--- Downtrend structure (price below EMA21): red must close below prior green closes
   if(belowEma21)
   {
      if(isGreen)
      {
         //--- track green close as level red must break
         if(m_greenCloseLow == 0.0 || closePx < m_greenCloseLow)
            m_greenCloseLow = closePx;
         m_structureIntactShort = false;  // green appeared, waiting for red break
      }
      else if(isRed)
      {
         if(m_greenCloseLow > 0.0 && closePx < m_greenCloseLow)
         {
            //--- structure intact: red broke below all green closes
            m_structureIntactShort = true;
            m_greenCloseLow = 0.0;        // reset after successful break
         }
         else if(m_greenCloseLow > 0.0 && closePx >= m_greenCloseLow)
         {
            //--- red failed to break → buyers stepping in
            m_structureIntactShort = false;
         }
         else
         {
            m_structureIntactShort = true; // no prior green to break
         }
      }
   }
   else
   {
      m_greenCloseLow = 0.0;
      m_structureIntactShort = false;
   }
}

//+------------------------------------------------------------------+
//| IsBearishEngulfing â€” prev green + current red, current body      |
//| fully engulfs prev body (open>prevClose, close<prevOpen)         |
//+------------------------------------------------------------------+
bool CSovereignRSIBand::IsBearishEngulfing()
{
   if(ArraySize(m_opBuf) < 2 || ArraySize(m_clBuf) < 2) return false;
   bool prevGreen = m_clBuf[1] > m_opBuf[1];
   bool currRed   = m_clBuf[0] < m_opBuf[0];
   bool engulf    = (m_opBuf[0] > m_clBuf[1]) && (m_clBuf[0] < m_opBuf[1]);
   return prevGreen && currRed && engulf;
}

//+------------------------------------------------------------------+
//| P90Spread â€” trailing p90 of last 500 spreads                     |
//+------------------------------------------------------------------+
double CSovereignRSIBand::P90Spread()
{
   if(m_spreadCount < 20)
      return 1e9; // not enough data, allow

   double sorted[];
   int n = m_spreadCount;
   ArrayResize(sorted, n);
   int idx = m_spreadHead;
   for(int i = 0; i < n; i++)
   {
      sorted[i] = (double)m_spreadBuf[idx];
      idx++;
      if(idx >= 500) idx = 0;
   }
   ArraySort(sorted);

   int p90Idx = (int)MathFloor((double)(n - 1) * m_spreadGatePct / 100.0);
   if(p90Idx < 0) p90Idx = 0;
   if(p90Idx >= n) p90Idx = n - 1;
   return sorted[p90Idx];
}

//+------------------------------------------------------------------+
//| SpreadAllowed â€” check if current spread is below p90 gate        |
//+------------------------------------------------------------------+
bool CSovereignRSIBand::SpreadAllowed()
{
   if(!SymbolValid())
      return false;

   long spreadLong = SafeSymbolInteger(SYMBOL_SPREAD, -1, "SpreadAllowed");
   if(spreadLong < 0)
      return false;
   int spread = (int)spreadLong;

   //--- record spread in rolling buffer
   if(m_spreadHead >= 500) m_spreadHead = 0;
   m_spreadBuf[m_spreadHead] = spread;
   m_spreadHead++;
   if(m_spreadCount < 500) m_spreadCount++;

   double p90 = P90Spread();
   return (spread <= (int)p90);
}

//+------------------------------------------------------------------+
//| NormalizeVolume                                                  |
//+------------------------------------------------------------------+
double CSovereignRSIBand::NormalizeVolume(const double volume)
{
   if(volume <= 0.0)
      return 0.0;

   double step = SafeSymbolDouble(SYMBOL_VOLUME_STEP, 0.01, "NormalizeVolume");
   double vmin = SafeSymbolDouble(SYMBOL_VOLUME_MIN, 0.01, "NormalizeVolume");
   double vmax = SafeSymbolDouble(SYMBOL_VOLUME_MAX, 100.0, "NormalizeVolume");

   if(step <= 0.0) step = 0.01;
   double v = MathFloor(volume / step) * step;
   if(v < vmin) return 0.0;
   if(v > vmax) v = vmax;
   return v;
}

//+------------------------------------------------------------------+
//| LotsForRisk                                                      |
//+------------------------------------------------------------------+
double CSovereignRSIBand::LotsForRisk(const double riskMoney,
                                      const double stopDist)
{
   if(riskMoney <= 0.0 || stopDist <= 0.0)
      return 0.0;

   double tickSize  = SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "LotsForRisk");
   double tickValue = SafeSymbolDouble(SYMBOL_TRADE_TICK_VALUE, 0.0, "LotsForRisk");
   if(tickSize <= 0.0 || tickValue <= 0.0)
      return 0.0;

   double lossPerLot = (stopDist / tickSize) * tickValue;
   if(lossPerLot <= 0.0)
      return 0.0;

   return NormalizeVolume(riskMoney / lossPerLot);
}

//+------------------------------------------------------------------+
//| OpenPosition                                                     |
//+------------------------------------------------------------------+
void CSovereignRSIBand::OpenPosition(const int side, const double price,
                                      const double atr,
                                      CTrade &t,
                                      CSovereignPerformance &perf,
                                      CSovereignTrafficLight &tl,
                                      CSovereignAuditLog &audit,
                                      const string comment,
                                      const bool isScaleIn,
                                      const double sizeMult)
{
   if(!SymbolValid())
   {
      SovLogError("OpenPosition", "symbol invalid");
      return;
    }
   if(atr <= 0.0)
   {
      SovLogWarn("OpenPosition aborted - atr=0");
      return;
    }
   if(price <= 0.0)
   {
      SovLogWarn("OpenPosition aborted - price=0");
      return;
    }
   if(m_inPosition && !isScaleIn)
   {
      SovLogWarn("OpenPosition aborted - already in position");
      return;
    }

   double stopDist = atr * m_stopAtr;   // configured ATR stop
   double sl = (side > 0) ? (price - stopDist) : (price + stopDist);
   double tp = (side > 0) ? (price + stopDist * 2.0) : (price - stopDist * 2.0);

   SovResetLastError();
   double baseEquity = m_riskUseBalance ? AccountInfoDouble(ACCOUNT_BALANCE) : AccountInfoDouble(ACCOUNT_EQUITY);
   double equity = baseEquity;
   if(equity <= 0.0)
   {
      m_tradeErrors++;
      SovLogError("AccountInfoDouble(" + (m_riskUseBalance ? "ACCOUNT_BALANCE" : "ACCOUNT_EQUITY") + ")", m_symbol);
      return;
   }

   double riskMoney = equity * m_riskPct / 100.0;
   if(isScaleIn)
      riskMoney *= m_scaleInSizeMult;   // additional legs are smaller
    double lot = LotsForRisk(riskMoney, stopDist);
    if(sizeMult < 1.0)
       lot *= sizeMult;   // early trend-change entries use reduced size
    if(lot <= 0.0)
   {
      SovLogWarn("OpenPosition aborted - lot=0 (riskMoney=" + DoubleToString(riskMoney,2) + " stopDist=" + DoubleToString(stopDist,5) + ")");
      return;
    }

   int spread = (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "OpenPosition");
   SovResetLastError();
   datetime barTime = iTime(m_symbol, m_timeframe, 1);
   if(barTime <= 0)
   {
      m_handleErrors++;
      SovLogError("iTime(1)", m_symbol);
      return;
    }
   MqlDateTime barDt;
   TimeToStruct(barTime, barDt);

   TrafficLightResult tlRes;
   tlRes = tl.Evaluate(side, 1, atr, 0.5, side, true, spread,
                       barDt.hour, true, true);
   if(!tlRes.allowed)
   {
      if(!m_forceBypassAllFilters)
      {
         PrintFormat("Sovereign RSI band %s: ENTRY BLOCKED traffic light (reason=%s riskMult=%.2f)",
                     m_symbol, tlRes.reason, tlRes.riskMultiplier);
         return;
      }
      PrintFormat("Sovereign RSI band %s: FORCE BYPASS - ignoring traffic light veto (%s) riskMult %.2f -> 1.00",
                  m_symbol, tlRes.reason, tlRes.riskMultiplier);
      tlRes.allowed        = true;
      tlRes.riskMultiplier = 1.0;
   }

   string legBucket  = CSovereignPerformance::LegBucket("SOV|RSI", m_symbol);
   bool   govAllowed = perf.AllowNewRisk(legBucket);
   double govMult    = perf.CombinedMultiplier(legBucket);
   if(!govAllowed || govMult <= 0.0)
   {
      if(!m_forceBypassAllFilters)
      {
         PrintFormat("Sovereign RSI band %s: ENTRY BLOCKED governor (bucket=%s allowed=%d mult=%.2f)",
                     m_symbol, legBucket, (int)govAllowed, govMult);
         return;
      }
      PrintFormat("Sovereign RSI band %s: FORCE BYPASS - ignoring governor block (bucket=%s allowed=%d mult=%.2f -> 1.00)",
                  m_symbol, legBucket, (int)govAllowed, govMult);
      govMult = 1.0;
   }

   riskMoney = equity * m_riskPct / 100.0 * govMult;
   lot = LotsForRisk(riskMoney, stopDist);
   if(lot <= 0.0)
   {
      PrintFormat("Sovereign RSI band %s: OpenPosition aborted - lot=0 after governor (riskMoney=%.2f)",
                  m_symbol, riskMoney);
      return;
   }
   lot = NormalizeVolume(lot * tlRes.riskMultiplier);
    if(lot <= 0.0)
    {
       PrintFormat("Sovereign RSI band %s: OpenPosition aborted - lot=0 after normalize (govMult=%.2f tlMult=%.2f)",
                   m_symbol, govMult, tlRes.riskMultiplier);
       return;
    }

    //--- Kelly sizing from portfolio risk engine (fractional Kelly)
    if(m_portRisk != NULL)
    {
        double kellyLot = (*m_portRisk).KellySize(m_symbol, equity, stopDist, lot);
       if(kellyLot > 0.0)
       {
          m_kellySizeMult = kellyLot / lot;
          lot = kellyLot;
       }
    }

    //--- hard lot cap (fat-finger protection)
    if(m_maxLotSize > 0.0 && lot > m_maxLotSize)
    {
       PrintFormat("Sovereign RSI band %s: MAX LOT CAP | requested lot %.2f capped to max %.2f",
                   m_symbol, lot, m_maxLotSize);
       lot = m_maxLotSize;
    }
    lot = NormalizeVolume(lot);

   double entryPx = (side > 0) ? SafeSymbolDouble(SYMBOL_ASK, 0.0, "OpenPosition")
                               : SafeSymbolDouble(SYMBOL_BID, 0.0, "OpenPosition");
   if(entryPx <= 0.0)
   {
      SovLogError("SYMBOL_ASK/BID", m_symbol);
      return;
   }

   double newSL = (side > 0) ? entryPx - stopDist : entryPx + stopDist;
   double newTP = (side > 0) ? entryPx + stopDist * 2.0 : entryPx - stopDist * 2.0;

     //--- set trade deviation and slippage
      t.SetDeviationInPoints(3);
      SovResetLastError();
      bool ok = false;
      ulong orderTicket = 0;
      uint retcode = 0;

      //--- Smart execution: route through TWAP/iceberg if enabled
      if(m_useSmartExec && m_exec != NULL && !isScaleIn)
      {
         SovExecMode mode = SOV_EXEC_IMMEDIATE;
         ulong parentTicket = (*m_exec).Submit(m_symbol, side, lot, entryPx, mode, t, audit);
         if(parentTicket > 0)
         {
            ok = true;
            orderTicket = parentTicket;
            retcode = 0;
         }
         else
         {
            ok = false;
            retcode = t.ResultRetcode();
         }
      }
      else
      {
         if(side > 0)
            ok = t.Buy(lot, m_symbol, entryPx, newSL, newTP, comment);
         else
            ok = t.Sell(lot, m_symbol, entryPx, newSL, newTP, comment);
      }

      orderTicket = t.ResultOrder();
      retcode = t.ResultRetcode();

    if(ok)
    {
       perf.RegisterOrder(orderTicket, legBucket, riskMoney,
                          side, 1, 0.0, "");
       LogOrderAttempt((side > 0) ? "BUY" : "SELL", entryPx, lot,
                       retcode, comment, spread, audit);

       //--- verify SL/TP actually set on broker (naked-position guard)
       if(orderTicket > 0 && !VerifySLTP(t, orderTicket, newSL, newTP, side))
       {
          m_tradeErrors++;
          PrintFormat("Sovereign RSI band %s: ORDER PLACED BUT SL/TP MISMATCH â€” review immediately",
                      m_symbol);
       }

       //--- register with portfolio risk engine
       if(m_portRisk != NULL && !isScaleIn)
       {
           (*m_portRisk).RegisterPosition(m_symbol, side, lot, entryPx, stopDist, riskMoney);
       }

       if(isScaleIn)
       {
          //--- additional leg: keep the portfolio (first-leg) entry/stop state,
          //--- advance the leg count and the most-recent entry price only.
          m_legCount++;
          m_lastEntryPrice = entryPx;
       }
        else
        {
           m_inPosition = true;
           m_positionSide = side;
           m_entryPrice = entryPx;
           m_stopPrice = newSL;
           m_tpPrice = newTP;
           m_entryAtr = atr;
           m_barsHeld = 0;
           m_entryBarIndex = 0;
           m_legCount = 1;
           m_lastEntryPrice = entryPx;
           m_mfe = entryPx;                 // MFE starts at entry
           m_breakevenActivated = false;     // breakeven re-arms on every new position
        }

       SovLogInfo("Sovereign RSI band " + m_symbol + ": ORDER PLACED side=" + IntegerToString(side) +
                  " lot=" + DoubleToString(lot,2) + " entry=" + DoubleToString(entryPx,5) +
                  " sl=" + DoubleToString(newSL,5) + " tp=" + DoubleToString(newTP,5) +
                  " riskPct=" + DoubleToString(m_riskPct,2) + "%% retcode=" + IntegerToString((int)retcode) +
                  " order=" + IntegerToString((long)orderTicket));
    }
    else
    {
       m_tradeErrors++;
       SovLogError("ORDER FAILED side=" + IntegerToString(side) + " lot=" + DoubleToString(lot,2) +
                   " price=" + DoubleToString(entryPx,5) + " sl=" + DoubleToString(newSL,5) +
                   " tp=" + DoubleToString(newTP,5) + " retcode=" + IntegerToString((int)retcode) +
                    " (" + t.ResultRetcodeDescription() + ") err=" + IntegerToString(GetLastError()), m_symbol);
     }
}

//+------------------------------------------------------------------+
//| ClosePosition                                                    |
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| CountLegs - open Sovereign legs for this magic+symbol            |
//+------------------------------------------------------------------+
int CSovereignRSIBand::CountLegs()
{
   CPositionInfo p;
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!p.SelectByIndex(i))
         continue;
      if(p.Magic() != m_magic || p.Symbol() != m_symbol)
         continue;
      n++;
   }
   return n;
}

//+------------------------------------------------------------------+
//| RecoverState â€” re-adopt live Sovereign positions after EA restart  |
//| / timeframe change so open positions are never orphaned.           |
//| Sets m_inPosition, side, entry/stop/TP, bars-held estimate, leg   |
//| count from the live terminal position. Call once in OnInit after   |
//| indicator buffers are loaded.                                      |
//+------------------------------------------------------------------+
bool CSovereignRSIBand::RecoverState(CTrade &t)
{
   m_inPosition = false;
   m_positionSide = 0;
   m_entryPrice = 0.0;
   m_stopPrice = 0.0;
   m_tpPrice = 0.0;
   m_barsHeld = 0;
   m_entryBarIndex = 0;
   m_legCount = 0;
   m_lastEntryPrice = 0.0;
   m_emaTrendActive = false;
   m_emaTrendSide = 0;
   m_emaTrendEntryBarTime = 0;
   m_emaTrendStopDist = 0.0;

   CPositionInfo p;
   int n = 0;
   double sumPriceVol = 0.0;
   double sumVol = 0.0;
   datetime oldestOpen = 0;
   int side = 0;
   double sl = 0.0;
   double tp = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!p.SelectByIndex(i))
      {
         SovLogError("PositionSelectByIndex", m_symbol);
         continue;
      }
      if(p.Magic() != m_magic || p.Symbol() != m_symbol)
         continue;
      n++;
      double vol = p.Volume();
      double px = p.PriceOpen();
      sumPriceVol += px * vol;
      sumVol += vol;
      if(oldestOpen == 0 || p.Time() < oldestOpen)
         oldestOpen = p.Time();
      int ps = (p.PositionType() == POSITION_TYPE_BUY) ? 1 : -1;
      if(side == 0) side = ps;
      sl = p.StopLoss();
      tp = p.TakeProfit();
   }

   if(n == 0)
   {
      SovLogInfo("RecoverState: no live Sovereign positions on " + m_symbol + " â€” flat start");
      return false;
   }

   m_inPosition = true;
   m_positionSide = side;
    m_entryPrice = (sumVol > 0.0) ? sumPriceVol / sumVol : 0.0;
    m_stopPrice = sl;
    m_tpPrice = tp;
    m_legCount = n;
    m_lastEntryPrice = m_entryPrice;
    m_mfe = m_entryPrice;              // MFE seeded at recovered entry
    m_breakevenActivated = false;       // conservative: re-arm breakeven after recovery

   //--- estimate bars held from position open time (bar-granular)
   if(oldestOpen > 0)
   {
      datetime now = TimeCurrent();
      int secondsPerPeriod = PeriodSeconds(m_timeframe);
      if(secondsPerPeriod > 0)
         m_barsHeld = (int)((now - oldestOpen) / secondsPerPeriod);
      if(m_barsHeld < 0) m_barsHeld = 0;
   }

    PrintFormat("Sovereign RSI band %s: RECOVERED state side=%d entry=%.5f stop=%.5f tp=%.5f legs=%d barsHeld~=%d",
                m_symbol, side, m_entryPrice, sl, tp, n, m_barsHeld);
    return true;
}

//+------------------------------------------------------------------+
//| ApplyBreakeven â€” Stage 1 of multi-stage risk engine.               |
//| When price reaches +m_breakevenAtR x entry stop distance, move SL |
//| to entry (+ optional buffer) to lock in risk-free trade. Only      |
//| ever improves SL (moves up for longs, down for shorts).            |
//+------------------------------------------------------------------+
void CSovereignRSIBand::ApplyBreakeven(CTrade &t, const double closePx)
{
   if(m_breakevenAtR <= 0.0 || m_breakevenActivated) return;
   if(m_entryAtr <= 0.0 || m_entryPrice <= 0.0) return;

   double point = SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "breakeven");
   double stopDist = m_entryAtr * m_stopAtr;
   if(stopDist <= 0.0) return;

   double triggerPx = 0.0;
   double beSL = 0.0;
   bool improve = false;

   if(m_positionSide > 0)  // long
   {
      triggerPx = m_entryPrice + stopDist * m_breakevenAtR;
      beSL = m_entryPrice + m_breakevenBufferPts * point;
      if(closePx >= triggerPx)
      {
         m_breakevenActivated = true;
         if(m_stopPrice <= 0.0 || beSL > m_stopPrice)
            improve = true;
      }
   }
   else if(m_positionSide < 0)  // short
   {
      triggerPx = m_entryPrice - stopDist * m_breakevenAtR;
      beSL = m_entryPrice - m_breakevenBufferPts * point;
      if(closePx <= triggerPx)
      {
         m_breakevenActivated = true;
         if(m_stopPrice <= 0.0 || beSL < m_stopPrice)
            improve = true;
      }
   }

   if(!improve) return;

   //--- find live position ticket and modify SL on the broker
   CPositionInfo p;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!p.SelectByIndex(i)) continue;
      if(p.Magic() != m_magic || p.Symbol() != m_symbol) continue;
      if(p.StopLoss() != beSL || p.TakeProfit() != m_tpPrice)
      {
         if(t.PositionModify(p.Ticket(), beSL, m_tpPrice))
         {
            m_stopPrice = beSL;
            m_lastModifyTime = TimeCurrent();
            PrintFormat("Sovereign RSI band %s: BREAKEVEN %s (new stop=%.5f entry=%.5f trigger=%.5f ticket=%d)",
                        m_symbol, (m_positionSide > 0 ? "long" : "short"),
                        m_stopPrice, m_entryPrice, triggerPx, (int)p.Ticket());
         }
         else
         {
            PrintFormat("Sovereign RSI band %s: BREAKEVEN MODIFY FAILED ticket=%d retcode=%d err=%d",
                        m_symbol, (int)p.Ticket(), (int)t.ResultRetcode(), GetLastError());
         }
      }
      break;
   }
}

//+------------------------------------------------------------------+
//| VerifySLTP â€” confirm SL/TP were actually set on the broker.        |
//+------------------------------------------------------------------+
bool CSovereignRSIBand::VerifySLTP(CTrade &t, const ulong ticket, const double reqSL,
                                    const double reqTP, const int side)
{
   if(!m_verifySLTP) return true;
   if(ticket <= 0) return false;

   if(!PositionSelectByTicket(ticket)) return false;

   double sl = PositionGetDouble(POSITION_SL);
   double tp = PositionGetDouble(POSITION_TP);

   bool slOK = (reqSL <= 0.0) || (MathAbs(sl - reqSL) < _Point * 10);
   bool tpOK = (reqTP <= 0.0) || (MathAbs(tp - reqTP) < _Point * 10);

   if(!slOK || !tpOK)
   {
      PrintFormat("Sovereign RSI band %s: SL/TP MISMATCH | ticket=%I64u reqSL=%.5f reqTP=%.5f "
                  "actualSL=%.5f actualTP=%.5f â€” POSITION MAY BE NAKED",
                  m_symbol, (long)ticket, reqSL, reqTP, sl, tp);
      return false;
   }
    PrintFormat("Sovereign RSI band %s: SL/TP VERIFIED | ticket=%I64u SL=%.5f TP=%.5f",
                m_symbol, (long)ticket, sl, tp);
    return true;
}

//+------------------------------------------------------------------+
//| CheckPositionAge â€” close positions older than max age bars.        |
//+------------------------------------------------------------------+
void CSovereignRSIBand::CheckPositionAge(CTrade &t)
{
   if(m_maxPositionAgeBars <= 0) return;

   datetime now = TimeCurrent();
   int secondsPerPeriod = PeriodSeconds(m_timeframe);
   if(secondsPerPeriod <= 0) return;

   CPositionInfo p;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!p.SelectByIndex(i)) continue;
      if(p.Magic() != m_magic || p.Symbol() != m_symbol) continue;

      datetime openTime = (datetime)p.Time();
      int barsHeld = (int)((now - openTime) / secondsPerPeriod);
      if(barsHeld >= m_maxPositionAgeBars)
      {
         ulong ticket = p.Ticket();
         int side = (p.PositionType() == POSITION_TYPE_BUY) ? 1 : -1;
         string sideTxt = (side > 0) ? "long" : "short";
         if(t.PositionClose(ticket))
         {
            PrintFormat("Sovereign RSI band %s: TIME STOP | %s position ticket=%I64u closed after %d bars (max=%d)",
                        m_symbol, sideTxt, (long)ticket, barsHeld, m_maxPositionAgeBars);
         }
         else
         {
            PrintFormat("Sovereign RSI band %s: TIME STOP CLOSE FAILED | ticket=%I64u retcode=%d err=%d",
                        m_symbol, (long)ticket, (int)t.ResultRetcode(), GetLastError());
         }
      }
   }
}

void CSovereignRSIBand::ClosePosition(CTrade &t, const string reason)
{
   if(!m_inPosition)
   {
      SovLogWarn("ClosePosition aborted - not in position (reason=" + reason + ")");
      return;
    }

   CPositionInfo p;
   ulong tickets[];
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!p.SelectByIndex(i))
      {
         SovLogError("PositionSelectByIndex", m_symbol);
         continue;
      }
      if(p.Magic() != m_magic || p.Symbol() != m_symbol)
         continue;
      ArrayResize(tickets, n + 1);
      tickets[n++] = p.Ticket();
   }

   if(n == 0)
   {
      SovLogWarn("ClosePosition - no matching position found (magic=" + IntegerToString((long)m_magic) + " reason=" + reason + ")");
      m_inPosition = false;
      m_positionSide = 0;
      m_entryPrice = 0.0;
      m_stopPrice = 0.0;
      m_tpPrice = 0.0;
      m_entryAtr = 0.0;
      m_barsHeld = 0;
      m_entryBarIndex = 0;
      m_legCount = 0;
      m_lastEntryPrice = 0.0;
      m_lastExitTime = iTime(m_symbol, m_timeframe, 0);   // start the re-entry cooldown
      return;
    }

   int closedCount = 0;
   int failedCount = 0;
   for(int k = 0; k < n; k++)
   {
      SovResetLastError();
      bool closed = t.PositionClose(tickets[k]);
      uint retcode = t.ResultRetcode();
      if(closed)
      {
         closedCount++;
         SovLogInfo("POSITION CLOSED reason=" + reason + " ticket=" + IntegerToString((long)tickets[k]) +
                    " leg=" + IntegerToString(k + 1) + "/" + IntegerToString(n) +
                    " retcode=" + IntegerToString((int)retcode));
      }
      else
      {
         failedCount++;
         m_tradeErrors++;
         SovLogError("CLOSE FAILED reason=" + reason + " ticket=" + IntegerToString((long)tickets[k]) +
                     " retcode=" + IntegerToString((int)retcode) + " (" + t.ResultRetcodeDescription() +
                     ") err=" + IntegerToString(GetLastError()), m_symbol);
      }
   }

   //--- the sleeve is flat only when every leg closed
   if(failedCount == 0)
   {
      if(closedCount > 0)
         SovLogInfo("ClosePosition: all " + IntegerToString(closedCount) +
                    " leg(s) closed (reason=" + reason + ")");
      m_inPosition = false;
      m_positionSide = 0;
      m_entryPrice = 0.0;
      m_stopPrice = 0.0;
      m_tpPrice = 0.0;
      m_entryAtr = 0.0;
      m_barsHeld = 0;
      m_entryBarIndex = 0;
      m_legCount = 0;
      m_lastEntryPrice = 0.0;
      m_lastExitTime = iTime(m_symbol, m_timeframe, 0);   // start the re-entry cooldown
      m_engulfingActive = false;
      m_engulfingStopPrice = 0.0;
      m_engulfingHasGreen = false;
      m_engulfingKissedEma = false;
      m_greenRedArmed = false;
      m_greenRedCount = 0;
      m_greenRedBarTime = 0;
      m_greenRedRsi = 0.0;
      m_emaTrendActive = false;
      m_emaTrendSide = 0;
      m_emaTrendEntryBarTime = 0;
      m_emaTrendStopDist = 0.0;
      //--- unregister from portfolio risk engine
      if(m_portRisk != NULL)
         (*m_portRisk).UnregisterPosition(m_symbol);
   }
   else
   {
      //--- some leg survived: reconcile to reality
      m_legCount = CountLegs();
   }

}

//+------------------------------------------------------------------+
//| LogOrderAttempt                                                  |
//+------------------------------------------------------------------+
void CSovereignRSIBand::LogOrderAttempt(const string action, const double price,
                                         const double volume, const uint retcode,
                                         const string comment, const int spread,
                                         CSovereignAuditLog &audit)
{
   audit.LogOrderAttempt(action, "MARKET", price, price, volume,
                         spread, retcode, comment,
                         false, "", 0.0, 0.0);
}

//+------------------------------------------------------------------+
//| OnNewBar â€” called every tick; acts only on new bars              |
//+------------------------------------------------------------------+
void CSovereignRSIBand::OnNewBar(const datetime now, const bool allowEntries,
                                  const double equity,
                                  CSovereignPerformance &perf,
                                  CSovereignTrafficLight &tl,
                                  CSovereignAuditLog &audit,
                                  CTrade &t)
{
   SovResetLastError();
   int decEntryMode = 0;   // 0=none,1=forceBypass,2=rsiBandLong,3=rsiBandShort,4=engulfingShort
   if(SovCheckStopped("RSI band OnNewBar")) return;

   if(!SymbolValid())
   {
      SovLogWarn("OnNewBar aborted - symbol invalid");
      return;
    }

   if(!m_intraBar)
   {
      if(!IsNewBar()) return;
   }
   else
   {
      //--- intra-bar: process every tick, track bar time for visual caching.
      //--- NOTE: m_barsHeld must count BARS, not ticks - otherwise min-hold /
      //--- time-stop / max-bars logic all fire after N ticks instead of N bars.
      datetime bt = iTime(m_symbol, m_timeframe, 0);
      if(bt > 0)
      {
         if(m_lastBarTime > 0 && bt != m_lastBarTime)
            m_barsHeld++;
         m_lastBarTime = bt;
      }
   }
   if(!CopyBuffers())
   {
      m_copyErrors++;
      SovLogWarn("CopyBuffers failed - skipping bar");
      return;
   }

   if(m_verboseLog)
      PrintFormat("Sovereign RSI band %s: OnNewBar entered (intraBar=%d)", m_symbol, (int)m_intraBar);

   if(!m_intraBar)
      m_barsHeld++;

   int trend = ComputeTrend();
    m_currentTrend = trend;
    double rsi = m_rsiBuf[0];    // RSI of most recent completed bar (bar 1)
    double rsiPrev = m_rsiBuf[1]; // RSI of previous bar
    double ema21 = m_emaBuf[0];
    double ema50 = m_ema50Buf[0];
    double atr = m_atrBuf[0];
    double closePx = m_clBuf[0];
    double openPx = m_opBuf[0];

    //--- market structure update: candle open/close vs EMA21
    UpdateMarketStructure(closePx, openPx, ema21);

   //--- per-bar indicator snapshot â€” LOG_DEBUG (suppressed in production)
   SovLogDebug(StringFormat("Sovereign RSI band %s: bar trend=%d rsi=%.2f prev=%.2f ema21=%.5f ema50=%.5f "
                            "atr=%.5f close=%.5f barsHeld=%d inPos=%d",
                            m_symbol, trend, rsi, rsiPrev, ema21, ema50, atr, closePx, m_barsHeld, (int)m_inPosition));

   //--- check if position still exists externally (SL/TP hit)
   if(m_inPosition)
   {
      bool stillThere = false;
      CPositionInfo p;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!p.SelectByIndex(i))
         {
            SovLogError("PositionSelectByIndex", m_symbol);
            continue;
         }
         if(p.Magic() == m_magic && p.Symbol() == m_symbol)
         {
            stillThere = true;
            break;
         }
      }
      if(!stillThere)
      {
         PrintFormat("Sovereign RSI band %s: position closed externally - resetting state", m_symbol);
         double exitPx = closePx;
         double pnlPts = (m_positionSide > 0) ? (exitPx - m_entryPrice) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl")
                                             : (m_entryPrice - exitPx) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl");
         double pnlMoney = pnlPts * SafeSymbolDouble(SYMBOL_TRADE_TICK_VALUE, 0.0, "pnl") * m_entryAtr;
         audit.LogExit(m_symbol, EnumToString(_Period), now, decEntryMode, m_positionSide, "external close",
                       m_entryPrice, exitPx, pnlPts, pnlMoney, m_barsHeld, m_legCount,
                       rsi, rsiPrev, ema21, ema50, closePx, m_opBuf[0], m_hiBuf[0], closePx,
                       atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "exit audit"), P90Spread(),
                       m_engulfingActive, m_engulfingStopPrice, "position closed externally (SL/TP/manual)");
         m_inPosition = false;
         m_positionSide = 0;
         m_entryPrice = 0.0;
         m_stopPrice = 0.0;
         m_tpPrice = 0.0;
         m_entryAtr = 0.0;
         m_barsHeld = 0;
         m_entryBarIndex = 0;
         m_legCount = 0;
         m_lastEntryPrice = 0.0;
         //--- unregister from portfolio risk engine
         if(m_portRisk != NULL)
            (*m_portRisk).UnregisterPosition(m_symbol);
      }
   }

   //==================================================================
   //--- IN POSITION: manage exits                                    |
   //==================================================================
   if(m_inPosition)
   {
      //--- position age time stop: close positions held too long
      CheckPositionAge(t);

      //--- MARKET STRUCTURE BREAK EXIT: close when structure fails
      // Long: green failed to reclaim above prior red opens above EMA21 → sellers stepping in
      if(m_structureFilterEnabled && m_positionSide > 0 && !m_structureIntactLong)
      {
         PrintFormat("Sovereign RSI band %s: EXIT structure break long (green failed to reclaim above red opens close=%.5f redOpenHigh=%.5f)",
                     m_symbol, closePx, m_redOpenHigh);
         double exitPx = closePx;
         double pnlPts = (exitPx - m_entryPrice) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl");
         double pnlMoney = pnlPts * SafeSymbolDouble(SYMBOL_TRADE_TICK_VALUE, 0.0, "pnl") * m_entryAtr;
         audit.LogExit(m_symbol, EnumToString(_Period), now, decEntryMode, m_positionSide, "structure break",
                       m_entryPrice, exitPx, pnlPts, pnlMoney, m_barsHeld, m_legCount,
                       rsi, rsiPrev, ema21, ema50, closePx, m_opBuf[0], m_hiBuf[0], closePx,
                       atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "exit audit"), P90Spread(),
                       m_engulfingActive, m_engulfingStopPrice, "structure break: green failed to reclaim red opens");
         ClosePosition(t, "structure break long");
         return;
      }
      // Short: red failed to break below prior green closes below EMA21 → buyers stepping in
      if(m_structureFilterEnabled && m_positionSide < 0 && !m_structureIntactShort)
      {
         PrintFormat("Sovereign RSI band %s: EXIT structure break short (red failed to break below green closes close=%.5f greenCloseLow=%.5f)",
                     m_symbol, closePx, m_greenCloseLow);
         double exitPx = closePx;
         double pnlPts = (m_entryPrice - exitPx) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl");
         double pnlMoney = pnlPts * SafeSymbolDouble(SYMBOL_TRADE_TICK_VALUE, 0.0, "pnl") * m_entryAtr;
         audit.LogExit(m_symbol, EnumToString(_Period), now, decEntryMode, m_positionSide, "structure break",
                       m_entryPrice, exitPx, pnlPts, pnlMoney, m_barsHeld, m_legCount,
                       rsi, rsiPrev, ema21, ema50, closePx, m_opBuf[0], m_hiBuf[0], closePx,
                       atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "exit audit"), P90Spread(),
                       m_engulfingActive, m_engulfingStopPrice, "structure break: red failed to break green closes");
         ClosePosition(t, "structure break short");
         return;
      }

       //--- time-based exit: hard max bars held
       if(m_maxBars > 0 && m_barsHeld >= m_maxBars)
       {
          PrintFormat("Sovereign RSI band %s: EXIT time exit (barsHeld=%d >= maxBars=%d)",                     m_symbol, m_barsHeld, m_maxBars);
          double exitPx = closePx;
          double pnlPts = (m_positionSide > 0) ? (exitPx - m_entryPrice) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl")
                                              : (m_entryPrice - exitPx) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl");
          double pnlMoney = pnlPts * SafeSymbolDouble(SYMBOL_TRADE_TICK_VALUE, 0.0, "pnl") * m_entryAtr;
          audit.LogExit(m_symbol, EnumToString(_Period), now, decEntryMode, m_positionSide, "time exit",
                        m_entryPrice, exitPx, pnlPts, pnlMoney, m_barsHeld, m_legCount,
                        rsi, rsiPrev, ema21, ema50, closePx, m_opBuf[0], m_hiBuf[0], closePx,
                        atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "exit audit"), P90Spread(),
                        m_engulfingActive, m_engulfingStopPrice, "max bars held reached");
          ClosePosition(t, "time exit");
          return;
       }
      if(m_positionSide > 0)  // long
      {
           //--- EMA21 TREND LONG EXIT:
           // 1) RSI overbought (>75) when price far from EMA21 â†’ rubber band snap back
           // 2) Bearish divergence â†’ trend weakening
           // 3) Red candle closes below EMA21 â†’ bears broke through, fallback exit
           // Hold through everything else - trend strong, red candles are noise
           if(m_emaTrendActive && m_emaTrendSide == 1)
           {
              double distAtr = (m_atrBuf[0] > 0.0) ? (closePx - ema21) / m_atrBuf[0] : 0.0;
              bool farFromEma21 = (distAtr >= m_emaTrendEmaDistAtr);

              //--- Exit 1: RSI overbought when price far from EMA21 = rubber band
              if(farFromEma21 && rsi > 75.0)
              {
                 m_emaTrendActive = false;
                 m_emaTrendSide = 0;
                 m_emaTrendEntryBarTime = 0;
                 PrintFormat("Sovereign RSI band %s: EXIT ema-trend long SMART (RSI overbought rsi=%.2f far from EMA21 distAtr=%.2f - rubber band)", m_symbol, rsi, distAtr);
                 double exitPx = closePx;
                 double pnlPts = (exitPx - m_entryPrice) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl");
                 double pnlMoney = pnlPts * SafeSymbolDouble(SYMBOL_TRADE_TICK_VALUE, 0.0, "pnl") * m_entryAtr;
                 audit.LogExit(m_symbol, EnumToString(_Period), now, decEntryMode, m_positionSide, "ema-trend long exit",
                               m_entryPrice, exitPx, pnlPts, pnlMoney, m_barsHeld, m_legCount,
                               rsi, rsiPrev, ema21, ema50, closePx, m_opBuf[0], m_hiBuf[0], closePx,
                               atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "exit audit"), P90Spread(),
                               m_engulfingActive, m_engulfingStopPrice, "RSI overbought far from EMA21 rubber band");
                 ClosePosition(t, "ema-trend long exit");
                 return;
              }

              //--- Exit 2: bearish divergence (price higher high, RSI lower high)
              if(m_barsHeld >= 3)
              {
                 double pxHi1 = iHigh(m_symbol, m_timeframe, 2);
                 double pxHi2 = iHigh(m_symbol, m_timeframe, 1);
                 double rsi1 = (ArraySize(m_rsiBuf) > 2) ? m_rsiBuf[2] : rsiPrev;
                 double rsi2 = (ArraySize(m_rsiBuf) > 1) ? m_rsiBuf[1] : rsiPrev;
                 bool bearishDiv = (pxHi2 > pxHi1 && rsi2 < rsi1);
                 if(bearishDiv)
                 {
                    m_emaTrendActive = false;
                    m_emaTrendSide = 0;
                    m_emaTrendEntryBarTime = 0;
                    PrintFormat("Sovereign RSI band %s: EXIT ema-trend long SMART (bearish divergence rsi=%.2f)", m_symbol, rsi);
                    double exitPx = closePx;
                    double pnlPts = (exitPx - m_entryPrice) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl");
                    double pnlMoney = pnlPts * SafeSymbolDouble(SYMBOL_TRADE_TICK_VALUE, 0.0, "pnl") * m_entryAtr;
                    audit.LogExit(m_symbol, EnumToString(_Period), now, decEntryMode, m_positionSide, "ema-trend long exit",
                                  m_entryPrice, exitPx, pnlPts, pnlMoney, m_barsHeld, m_legCount,
                                  rsi, rsiPrev, ema21, ema50, closePx, m_opBuf[0], m_hiBuf[0], closePx,
                                  atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "exit audit"), P90Spread(),
                                  m_engulfingActive, m_engulfingStopPrice, "bearish divergence");
                    ClosePosition(t, "ema-trend long exit");
                    return;
                 }
              }

              //--- Exit 3: red candle closes below EMA21 â†’ fallback exit
              bool currRed = (closePx < m_opBuf[0]);
              if(currRed && closePx < ema21)
              {
                 m_emaTrendActive = false;
                 m_emaTrendSide = 0;
                 m_emaTrendEntryBarTime = 0;
                 PrintFormat("Sovereign RSI band %s: EXIT ema-trend long (red candle below EMA21 close=%.5f ema21=%.5f)", m_symbol, closePx, ema21);
                 double exitPx = closePx;
                 double pnlPts = (exitPx - m_entryPrice) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl");
                 double pnlMoney = pnlPts * SafeSymbolDouble(SYMBOL_TRADE_TICK_VALUE, 0.0, "pnl") * m_entryAtr;
                 audit.LogExit(m_symbol, EnumToString(_Period), now, decEntryMode, m_positionSide, "ema-trend long exit",
                               m_entryPrice, exitPx, pnlPts, pnlMoney, m_barsHeld, m_legCount,
                               rsi, rsiPrev, ema21, ema50, closePx, m_opBuf[0], m_hiBuf[0], closePx,
                               atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "exit audit"), P90Spread(),
                               m_engulfingActive, m_engulfingStopPrice, "red candle closed below EMA21");
                 ClosePosition(t, "ema-trend long exit");
                 return;
              }

              //--- HOLD: trend strong, hold through everything else
              if(m_verboseLog)
                 PrintFormat("Sovereign RSI band %s: HOLD long rsi=%.2f distAtr=%.2f (trend strong, hold)", m_symbol, rsi, distAtr);
           }
          //--- HOLD THROUGH PULLBACK: if price still above EMA21 (with tolerance) and RSI not overbought,
          // do not close even if in loss â€” wait for TP or decisive trend/EMA breakdown
          bool longStillGood = (closePx >= (ema21 - m_emaBreakBufferAtr * m_atrBuf[0])) &&
                               (rsi < (double)m_rsiHi);

          //--- EMA21 break exit: price closed decisively below EMA21 (buffer x ATR)
          // Only exit if the EMA breakdown is real AND RSI confirms weakness
           if(m_exitOnEmaBreak && m_atrBuf[0] > 0.0 && closePx < (ema21 - m_emaBreakBufferAtr * m_atrBuf[0]) && rsi < (double)(m_rsiMid - 10.0))
           {
               PrintFormat("Sovereign RSI band %s: EXIT EMA21 break (close=%.5f < ema21=%.5f - %.2f*ATR=%.5f rsi=%.2f weak)", m_symbol, closePx, ema21, m_emaBreakBufferAtr, m_emaBreakBufferAtr * m_atrBuf[0], rsi);
              double exitPx = closePx;
              double pnlPts = (m_positionSide > 0) ? (exitPx - m_entryPrice) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl")
                                                  : (m_entryPrice - exitPx) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl");
              double pnlMoney = pnlPts * SafeSymbolDouble(SYMBOL_TRADE_TICK_VALUE, 0.0, "pnl") * m_entryAtr;
              audit.LogExit(m_symbol, EnumToString(_Period), now, decEntryMode, m_positionSide, "ema break",
                            m_entryPrice, exitPx, pnlPts, pnlMoney, m_barsHeld, m_legCount,
                            rsi, rsiPrev, ema21, ema50, closePx, m_opBuf[0], m_hiBuf[0], closePx,
                            atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "exit audit"), P90Spread(),
                            m_engulfingActive, m_engulfingStopPrice, "price closed below EMA21 weak RSI");
              ClosePosition(t, "ema break");
              return;
           }
          //--- RSI sustained above m_rsiHi: exit (only after min-hold, and only if not holding through pullback)
          if(m_barsHeld >= m_minHoldBars)
          {
               if(rsiPrev > (double)m_rsiHi && rsi > (double)m_rsiHi && !longStillGood)
               {
                  PrintFormat("Sovereign RSI band %s: EXIT RSI high (prev=%.2f curr=%.2f hi=%d sustained price-below-ema)", m_symbol, rsiPrev, rsi, m_rsiHi);
                 double exitPx = closePx;
                 double pnlPts = (m_positionSide > 0) ? (exitPx - m_entryPrice) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl")
                                                     : (m_entryPrice - exitPx) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl");
                 double pnlMoney = pnlPts * SafeSymbolDouble(SYMBOL_TRADE_TICK_VALUE, 0.0, "pnl") * m_entryAtr;
                 audit.LogExit(m_symbol, EnumToString(_Period), now, decEntryMode, m_positionSide, "rsi high",
                               m_entryPrice, exitPx, pnlPts, pnlMoney, m_barsHeld, m_legCount,
                               rsi, rsiPrev, ema21, ema50, closePx, m_opBuf[0], m_hiBuf[0], closePx,
                               atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "exit audit"), P90Spread(),
                               m_engulfingActive, m_engulfingStopPrice, "RSI sustained overbought below EMA21");
                 ClosePosition(t, "rsi high");
                 return;
              }
             else if(m_verboseLog)
             {
                PrintFormat("Sovereign RSI band %s: holding long rsi=%.2f hi=%d closeAboveEma=%d barsHeld=%d/%d longStillGood=%d",
                            m_symbol, rsi, m_rsiHi, (closePx >= ema21 ? 1 : 0), m_barsHeld, m_minHoldBars, (longStillGood ? 1 : 0));
             }
          }
          else if(m_verboseLog)
          {
             PrintFormat("Sovereign RSI band %s: holding long (min-hold not met %d/%d) rsi=%.2f hi=%d",
                         m_symbol, m_barsHeld, m_minHoldBars, rsi, m_rsiHi);
          }
          //--- hard stop / TP
           if(closePx <= m_stopPrice)
           {
               PrintFormat("Sovereign RSI band %s: EXIT stop/tp long (close=%.5f <= stop=%.5f)",
                          m_symbol, closePx, m_stopPrice);
              double exitPx = closePx;
              double pnlPts = (m_positionSide > 0) ? (exitPx - m_entryPrice) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl")
                                                  : (m_entryPrice - exitPx) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl");
              double pnlMoney = pnlPts * SafeSymbolDouble(SYMBOL_TRADE_TICK_VALUE, 0.0, "pnl") * m_entryAtr;
              audit.LogExit(m_symbol, EnumToString(_Period), now, decEntryMode, m_positionSide, "stop/tp",
                           m_entryPrice, exitPx, pnlPts, pnlMoney, m_barsHeld, m_legCount,
                           rsi, rsiPrev, ema21, ema50, closePx, m_opBuf[0], m_hiBuf[0], closePx,
                           atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "exit audit"), P90Spread(),
                           m_engulfingActive, m_engulfingStopPrice, "hard stop hit long");
            ClosePosition(t, "stop/tp");
            return;
          }
           if(closePx >= m_tpPrice)
           {
              PrintFormat("Sovereign RSI band %s: EXIT stop/tp long (close=%.5f >= tp=%.5f)",
                          m_symbol, closePx, m_tpPrice);
              double exitPx = closePx;
              double pnlPts = (m_positionSide > 0) ? (exitPx - m_entryPrice) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl")
                                                  : (m_entryPrice - exitPx) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl");
              double pnlMoney = pnlPts * SafeSymbolDouble(SYMBOL_TRADE_TICK_VALUE, 0.0, "pnl") * m_entryAtr;
              audit.LogExit(m_symbol, EnumToString(_Period), now, decEntryMode, m_positionSide, "take profit",
                            m_entryPrice, exitPx, pnlPts, pnlMoney, m_barsHeld, m_legCount,
                            rsi, rsiPrev, ema21, ema50, closePx, m_opBuf[0], m_hiBuf[0], closePx,
                            atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "exit audit"), P90Spread(),
                            m_engulfingActive, m_engulfingStopPrice, "take profit hit long");
              ClosePosition(t, "stop/tp");
              return;
           }

          //--- Rule 4a: Trailing stop for longs â€” trail up as price makes higher lows
          if(m_trailAtrMult > 0.0 && m_atrBuf[0] > 0.0)
          {
             double newStop = closePx - m_trailAtrMult * m_atrBuf[0];   // above price for longs
             double point = SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "trail stop");
             double minStepPx = m_trailMinStep * point;
             bool improve = (m_stopPrice <= 0.0) || (newStop > m_stopPrice + minStepPx);
             bool cooldownOk = (m_lastModifyTime == 0) || (TimeCurrent() - m_lastModifyTime >= 60);
             if(improve && cooldownOk)
             {
                //--- find live position ticket and modify SL on the broker
                CPositionInfo p;
                for(int i = PositionsTotal() - 1; i >= 0; i--)
                {
                   if(!p.SelectByIndex(i)) continue;
                   if(p.Magic() == m_magic && p.Symbol() == m_symbol)
                   {
                      if(p.StopLoss() != newStop || p.TakeProfit() != m_tpPrice)
                      {
                         if(t.PositionModify(p.Ticket(), newStop, m_tpPrice))
                         {
                            m_stopPrice = newStop;
                            m_lastModifyTime = TimeCurrent();
                            PrintFormat("Sovereign RSI band %s: TRAIL STOP long (new stop=%.5f close=%.5f atr=%.5f ticket=%d)",
                                        m_symbol, m_stopPrice, closePx, m_atrBuf[0], (int)p.Ticket());
                         }
                         else
                         {
                            PrintFormat("Sovereign RSI band %s: TRAIL MODIFY FAILED ticket=%d retcode=%d err=%d",
                                        m_symbol, (int)p.Ticket(), (int)t.ResultRetcode(), GetLastError());
                         }
                      }
                      break;
                   }
                }
             }
          }
       }
          else  // short
        {
              //--- EMA21 TREND SHORT EXIT:
              // 1) RSI oversold (<25) when price far from EMA21 â†’ rubber band snap back
              // 2) Bullish divergence â†’ trend weakening
              // 3) Green candle closes above EMA21 â†’ bulls broke through, fallback exit
              // Hold through everything else - trend strong, green candles are noise
              if(m_emaTrendActive && m_emaTrendSide == -1)
              {
                 double distAtr = (m_atrBuf[0] > 0.0) ? (ema21 - closePx) / m_atrBuf[0] : 0.0;
                 bool farFromEma21 = (distAtr >= m_emaTrendEmaDistAtr);

                 //--- Exit 1: RSI oversold when price far from EMA21 = rubber band
                 if(farFromEma21 && rsi < 25.0)
                 {
                    m_emaTrendActive = false;
                    m_emaTrendSide = 0;
                    m_emaTrendEntryBarTime = 0;
                    PrintFormat("Sovereign RSI band %s: EXIT ema-trend short SMART (RSI oversold rsi=%.2f far from EMA21 distAtr=%.2f - rubber band)", m_symbol, rsi, distAtr);
                    double exitPx = closePx;
                    double pnlPts = (m_positionSide > 0) ? (exitPx - m_entryPrice) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl")
                                                       : (m_entryPrice - exitPx) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl");
                    double pnlMoney = pnlPts * SafeSymbolDouble(SYMBOL_TRADE_TICK_VALUE, 0.0, "pnl") * m_entryAtr;
                    audit.LogExit(m_symbol, EnumToString(_Period), now, decEntryMode, m_positionSide, "ema-trend short exit",
                                  m_entryPrice, exitPx, pnlPts, pnlMoney, m_barsHeld, m_legCount,
                                  rsi, rsiPrev, ema21, ema50, closePx, m_opBuf[0], m_hiBuf[0], closePx,
                                  atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "exit audit"), P90Spread(),
                                  m_engulfingActive, m_engulfingStopPrice, "RSI oversold far from EMA21 rubber band");
                    ClosePosition(t, "ema-trend short exit");
                    return;
                 }

                 //--- Exit 2: bullish divergence (price lower low, RSI higher low)
                 if(m_barsHeld >= 3)
                 {
                    double pxLo1 = iLow(m_symbol, m_timeframe, 2);
                    double pxLo2 = iLow(m_symbol, m_timeframe, 1);
                    double rsi1 = (ArraySize(m_rsiBuf) > 2) ? m_rsiBuf[2] : rsiPrev;
                    double rsi2 = (ArraySize(m_rsiBuf) > 1) ? m_rsiBuf[1] : rsiPrev;
                    bool bullishDiv = (pxLo2 < pxLo1 && rsi2 > rsi1);
                    if(bullishDiv)
                    {
                       m_emaTrendActive = false;
                       m_emaTrendSide = 0;
                       m_emaTrendEntryBarTime = 0;
                       PrintFormat("Sovereign RSI band %s: EXIT ema-trend short SMART (bullish divergence rsi=%.2f)", m_symbol, rsi);
                       double exitPx = closePx;
                       double pnlPts = (m_positionSide > 0) ? (exitPx - m_entryPrice) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl")
                                                          : (m_entryPrice - exitPx) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl");
                       double pnlMoney = pnlPts * SafeSymbolDouble(SYMBOL_TRADE_TICK_VALUE, 0.0, "pnl") * m_entryAtr;
                       audit.LogExit(m_symbol, EnumToString(_Period), now, decEntryMode, m_positionSide, "ema-trend short exit",
                                     m_entryPrice, exitPx, pnlPts, pnlMoney, m_barsHeld, m_legCount,
                                     rsi, rsiPrev, ema21, ema50, closePx, m_opBuf[0], m_hiBuf[0], closePx,
                                     atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "exit audit"), P90Spread(),
                                     m_engulfingActive, m_engulfingStopPrice, "bullish divergence");
                       ClosePosition(t, "ema-trend short exit");
                       return;
                    }
                 }

                 //--- Exit 3: green candle closes above EMA21 â†’ fallback exit
                 bool currGreen = (closePx > m_opBuf[0]);
                 if(currGreen && closePx > ema21)
                 {
                    m_emaTrendActive = false;
                    m_emaTrendSide = 0;
                    m_emaTrendEntryBarTime = 0;
                    PrintFormat("Sovereign RSI band %s: EXIT ema-trend short (green candle above EMA21 close=%.5f ema21=%.5f)", m_symbol, closePx, ema21);
                    double exitPx = closePx;
                    double pnlPts = (m_positionSide > 0) ? (exitPx - m_entryPrice) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl")
                                                       : (m_entryPrice - exitPx) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl");
                    double pnlMoney = pnlPts * SafeSymbolDouble(SYMBOL_TRADE_TICK_VALUE, 0.0, "pnl") * m_entryAtr;
                    audit.LogExit(m_symbol, EnumToString(_Period), now, decEntryMode, m_positionSide, "ema-trend short exit",
                                  m_entryPrice, exitPx, pnlPts, pnlMoney, m_barsHeld, m_legCount,
                                  rsi, rsiPrev, ema21, ema50, closePx, m_opBuf[0], m_hiBuf[0], closePx,
                                  atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "exit audit"), P90Spread(),
                                  m_engulfingActive, m_engulfingStopPrice, "green candle closed above EMA21");
                    ClosePosition(t, "ema-trend short exit");
                    return;
                 }

                 //--- HOLD: trend strong, hold through everything else
                 if(m_verboseLog)
                    PrintFormat("Sovereign RSI band %s: HOLD short rsi=%.2f distAtr=%.2f (trend strong, hold)", m_symbol, rsi, distAtr);
              }

            //--- HOLD THROUGH PULLBACK: if price still below EMA21 (with tolerance) and RSI not oversold,
            // do not close even if in loss â€” wait for TP or decisive trend/EMA breakdown
            bool shortStillGood = (closePx <= (ema21 + m_emaBreakBufferAtr * m_atrBuf[0])) &&
                                  (rsi > (double)m_rsiLo);

            //--- EMA21 break exit: price closed decisively above EMA21 (buffer x ATR)
           // Only exit if the EMA breakout is real AND RSI confirms strength
            if(m_exitOnEmaBreak && m_atrBuf[0] > 0.0 && closePx > (ema21 + m_emaBreakBufferAtr * m_atrBuf[0]) && rsi > (double)(m_rsiMid + 10.0))
           {
               PrintFormat("Sovereign RSI band %s: EXIT EMA21 break (close=%.5f > ema21=%.5f + %.2f*ATR=%.5f rsi=%.2f strong)", m_symbol, closePx, ema21, m_emaBreakBufferAtr, m_emaBreakBufferAtr * m_atrBuf[0], rsi);
              double exitPx = closePx;
              double pnlPts = (m_positionSide > 0) ? (exitPx - m_entryPrice) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl")
                                                  : (m_entryPrice - exitPx) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl");
              double pnlMoney = pnlPts * SafeSymbolDouble(SYMBOL_TRADE_TICK_VALUE, 0.0, "pnl") * m_entryAtr;
              audit.LogExit(m_symbol, EnumToString(_Period), now, decEntryMode, m_positionSide, "ema break",
                            m_entryPrice, exitPx, pnlPts, pnlMoney, m_barsHeld, m_legCount,
                            rsi, rsiPrev, ema21, ema50, closePx, m_opBuf[0], m_hiBuf[0], closePx,
                            atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "exit audit"), P90Spread(),
                            m_engulfingActive, m_engulfingStopPrice, "price closed above EMA21 strong RSI");
              ClosePosition(t, "ema break");
              return;
           }
          //--- RSI sustained below m_rsiLo: exit (only after min-hold, and only if not holding through pullback)
          if(m_barsHeld >= m_minHoldBars)
          {
              if(rsiPrev < (double)m_rsiLo && rsi < (double)m_rsiLo && !shortStillGood)
              {
                 PrintFormat("Sovereign RSI band %s: EXIT RSI low (prev=%.2f curr=%.2f lo=%d sustained price-above-ema)", m_symbol, rsiPrev, rsi, m_rsiLo);
                double exitPx = closePx;
                double pnlPts = (m_positionSide > 0) ? (exitPx - m_entryPrice) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl")
                                                   : (m_entryPrice - exitPx) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl");
                double pnlMoney = pnlPts * SafeSymbolDouble(SYMBOL_TRADE_TICK_VALUE, 0.0, "pnl") * m_entryAtr;
                audit.LogExit(m_symbol, EnumToString(_Period), now, decEntryMode, m_positionSide, "rsi low",
                              m_entryPrice, exitPx, pnlPts, pnlMoney, m_barsHeld, m_legCount,
                              rsi, rsiPrev, ema21, ema50, closePx, m_opBuf[0], m_hiBuf[0], closePx,
                              atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "exit audit"), P90Spread(),
                              m_engulfingActive, m_engulfingStopPrice, "RSI sustained oversold above EMA21");
                ClosePosition(t, "rsi low");
                return;
             }
             else if(m_verboseLog)
             {
                PrintFormat("Sovereign RSI band %s: holding short rsi=%.2f lo=%d closeBelowEma=%d barsHeld=%d/%d shortStillGood=%d",
                            m_symbol, rsi, m_rsiLo, (closePx <= ema21 ? 1 : 0), m_barsHeld, m_minHoldBars, (shortStillGood ? 1 : 0));
             }
          }
          else if(m_verboseLog)
          {
             PrintFormat("Sovereign RSI band %s: holding short (min-hold not met %d/%d) rsi=%.2f lo=%d",
                         m_symbol, m_barsHeld, m_minHoldBars, rsi, m_rsiLo);
          }

            //--- hard stop / TP
           if(closePx >= m_stopPrice)
           {
              PrintFormat("Sovereign RSI band %s: EXIT stop/tp short (close=%.5f >= stop=%.5f)",
                          m_symbol, closePx, m_stopPrice);
              double exitPx = closePx;
              double pnlPts = (m_positionSide > 0) ? (exitPx - m_entryPrice) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl")
                                                  : (m_entryPrice - exitPx) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl");
              double pnlMoney = pnlPts * SafeSymbolDouble(SYMBOL_TRADE_TICK_VALUE, 0.0, "pnl") * m_entryAtr;
              audit.LogExit(m_symbol, EnumToString(_Period), now, decEntryMode, m_positionSide, "stop/tp",
                            m_entryPrice, exitPx, pnlPts, pnlMoney, m_barsHeld, m_legCount,
                            rsi, rsiPrev, ema21, ema50, closePx, m_opBuf[0], m_hiBuf[0], closePx,
                            atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "exit audit"), P90Spread(),
                            m_engulfingActive, m_engulfingStopPrice, "hard stop hit");
              ClosePosition(t, "stop/tp");
              return;
           }
           if(closePx <= m_tpPrice)
           {
              PrintFormat("Sovereign RSI band %s: EXIT stop/tp short (close=%.5f <= tp=%.5f)",
                          m_symbol, closePx, m_tpPrice);
              double exitPx = closePx;
              double pnlPts = (m_positionSide > 0) ? (exitPx - m_entryPrice) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl")
                                                  : (m_entryPrice - exitPx) / SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "pnl");
              double pnlMoney = pnlPts * SafeSymbolDouble(SYMBOL_TRADE_TICK_VALUE, 0.0, "pnl") * m_entryAtr;
              audit.LogExit(m_symbol, EnumToString(_Period), now, decEntryMode, m_positionSide, "take profit",
                            m_entryPrice, exitPx, pnlPts, pnlMoney, m_barsHeld, m_legCount,
                            rsi, rsiPrev, ema21, ema50, closePx, m_opBuf[0], m_hiBuf[0], closePx,
                            atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "exit audit"), P90Spread(),
                            m_engulfingActive, m_engulfingStopPrice, "take profit hit");
              ClosePosition(t, "stop/tp");
              return;
           }

          //--- Rule 4a: Trailing stop for shorts â€” trail down as lower swing highs form
          if(m_trailAtrMult > 0.0 && m_atrBuf[0] > 0.0)
          {
             double newStop = closePx + m_trailAtrMult * m_atrBuf[0];   // below price for shorts
             double point = SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "trail stop");
             double minStepPx = m_trailMinStep * point;
             bool improve = (m_stopPrice <= 0.0) || (newStop < m_stopPrice - minStepPx);
             bool cooldownOk = (m_lastModifyTime == 0) || (TimeCurrent() - m_lastModifyTime >= 60);
             if(improve && cooldownOk)
             {
                //--- find live position ticket and modify SL on the broker
                CPositionInfo p;
                for(int i = PositionsTotal() - 1; i >= 0; i--)
                {
                   if(!p.SelectByIndex(i)) continue;
                   if(p.Magic() == m_magic && p.Symbol() == m_symbol)
                   {
                      if(p.StopLoss() != newStop || p.TakeProfit() != m_tpPrice)
                      {
                         if(t.PositionModify(p.Ticket(), newStop, m_tpPrice))
                         {
                            m_stopPrice = newStop;
                            m_lastModifyTime = TimeCurrent();
                            PrintFormat("Sovereign RSI band %s: TRAIL STOP short (new stop=%.5f close=%.5f atr=%.5f ticket=%d)",
                                        m_symbol, m_stopPrice, closePx, m_atrBuf[0], (int)p.Ticket());
                         }
                         else
                         {
                            PrintFormat("Sovereign RSI band %s: TRAIL MODIFY FAILED ticket=%d retcode=%d err=%d",
                                        m_symbol, (int)p.Ticket(), (int)t.ResultRetcode(), GetLastError());
                         }
                      }
                      break;
                   }
                }
             }
          }

          //--- Rule 4b: RSI exhaustion exit for shorts â€” emergency profit lock on oversold
          if(m_rsiExhaustionLevel > 0 && rsi < (double)m_rsiExhaustionLevel)
          {
             PrintFormat("Sovereign RSI band %s: EXIT RSI exhaustion short (rsi=%.2f < %d)",
                         m_symbol, rsi, m_rsiExhaustionLevel);
             ClosePosition(t, "rsi exhaustion");
             return;
          }
        }

       //--- Track MFE (max favorable excursion) for breakeven / trailing stages
       if(m_positionSide > 0) m_mfe = MathMax(m_mfe, closePx);
       else if(m_positionSide < 0) m_mfe = MathMin(m_mfe, closePx);

       //--- Rule 4c: Breakeven stage 1 â€” move SL to entry (+buffer) at +N x R
       ApplyBreakeven(t, closePx);

       //==================================================================
       //--- PYRAMIDING (opt-in): add a leg only while every open leg is
       //--- in net profit (PROFIT+SWAP+COMMISSION) and price has advanced
       //--- another ATR step from the most recent leg.
       //==================================================================
       if(m_pyramidExec && m_legCount > 0 && m_legCount < m_maxPyramidLayers && atr > 0.0)
       {
          //--- reconcile to reality (a leg may have hit its own broker SL/TP)
          int liveLegs = CountLegs();
          if(liveLegs <= 0)
          {
             m_inPosition = false;
             m_legCount = 0;
             return;
          }
          m_legCount = liveLegs;

          if(m_legCount < m_maxPyramidLayers &&
             (m_pyramidAllowLoss || CanPyramidInProfit((ulong)m_magic, m_symbol)))
          {
             double step = m_scaleInAtrStep * atr;
             if(m_positionSide < 0 && trend < 0 && closePx <= m_lastEntryPrice - step)
             {
                PrintFormat("Sovereign RSI band %s: SCALE-IN short leg=%d/%d price=%.5f last=%.5f step=%.5f",
                            m_symbol, m_legCount + 1, m_maxPyramidLayers, closePx, m_lastEntryPrice, step);
                OpenPosition(-1, closePx, atr, t, perf, tl, audit, "SOV-RSI-PY", true);
             }
             else if(m_positionSide > 0 && trend > 0 && closePx >= m_lastEntryPrice + step)
             {
                PrintFormat("Sovereign RSI band %s: SCALE-IN long leg=%d/%d price=%.5f last=%.5f step=%.5f",
                            m_symbol, m_legCount + 1, m_maxPyramidLayers, closePx, m_lastEntryPrice, step);
                OpenPosition(1, closePx, atr, t, perf, tl, audit, "SOV-RSI-PY", true);
             }
          }
       }
   }

    //--- decision audit context (declared early for entry-skip audit logs)
    string decDecision  = "SKIP";
    string decReason    = "";
    double rsiPrior     = (ArraySize(m_rsiBuf) > 2) ? m_rsiBuf[2] : 0.0;
    double smma100      = m_smma100Buf[0];
    bool   decGateSpread   = false;
    bool   decGateAtr      = false;
    bool   decGateMaClean  = false;
    bool   decGateConsecutive = false;
    bool   decGateEmaSlope = false;
    bool   decGateMacro    = false;
    bool   decGateDiscretion = false;
    bool   decGatePortfolio = false;
    double decEngulfingStop = 0.0;

    //==================================================================
    //--- FLAT: look for entry                                         |
    //==================================================================
       if(!m_inPosition)
      {
         if(!allowEntries)
         {
             audit.LogDecision(m_symbol, EnumToString(_Period), now, 0, 0, "SKIP", "entries blocked (daily DD / century halt)", rsi, rsiPrev, rsiPrior, ema21, ema50, smma100, trend, closePx, m_opBuf[0], m_hiBuf[0], closePx, atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "decision audit"), P90Spread(), decGateSpread, decGateAtr, decGateMaClean, decGateConsecutive, decGateEmaSlope, decGateMacro, decGateDiscretion, decGatePortfolio, m_engulfingActive, decEngulfingStop, "");
             return;
         }
          if(trend == 0)
          {
              audit.LogDecision(m_symbol, EnumToString(_Period), now, 0, 0, "SKIP", "no trend (EMA21/EMA50 flat, no early flip)", rsi, rsiPrev, rsiPrior, ema21, ema50, smma100, trend, closePx, m_opBuf[0], m_hiBuf[0], closePx, atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "decision audit"), P90Spread(), decGateSpread, decGateAtr, decGateMaClean, decGateConsecutive, decGateEmaSlope, decGateMacro, decGateDiscretion, decGatePortfolio, m_engulfingActive, decEngulfingStop, "");
              return;
          }

      bool enter = false;
      int side = 0;
      double entrySizeMult = 1.0;

      if(m_forceBypassAllFilters)
      {
         //--- FORCE BYPASS ALL FILTERS: enter purely on trend direction for core order placement test
         enter = true;
         side = (trend > 0) ? 1 : -1;
         //--- log once per bar (intra-bar evaluation runs this on every tick)
         datetime bySignalBar = iTime(m_symbol, m_timeframe, 0);
         if(bySignalBar != m_lastBypassLogBar)
         {
            m_lastBypassLogBar = bySignalBar;
            SovLogInfo(StringFormat("Sovereign RSI band %s: FORCE BYPASS ALL FILTERS - ENTRY SIGNAL %s (trend=%d)",
                        m_symbol, (side > 0) ? "long" : "short", trend));
         }
      }
      //--- EARLY LONG: price closed above EMA21 and held, but EMA21 not yet > EMA50
      else if(trend == 2 && m_earlyTrendEnabled)
      {
         //--- require RSI recovering from oversold (not overbought)
         if(rsi < (double)m_earlyTrendRsiMax && rsi > rsiPrev)
         {
            enter = true;
            side = 1;
            entrySizeMult = m_earlyTrendSizeMult;
            SovLogInfo(StringFormat("Sovereign RSI band %s: ENTRY SIGNAL early long (trend change: close>EMA21 held %d bars, rsi=%.2f < %d recovering)",
                        m_symbol, m_earlyTrendHoldBars, rsi, m_earlyTrendRsiMax));
         }
         else if(m_verboseLog)
         {
            PrintFormat("Sovereign RSI band %s: flat - early long but RSI filter failed (rsi=%.2f >= %d or not recovering rsiPrev=%.2f)",
                        m_symbol, rsi, m_earlyTrendRsiMax, rsiPrev);
            audit.LogDecision(m_symbol, EnumToString(_Period), now, 2, 1, "SKIP", "early long RSI filter", rsi, rsiPrev, rsiPrior, ema21, ema50, smma100, trend, closePx, m_opBuf[0], m_hiBuf[0], closePx, atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "decision audit"), P90Spread(), decGateSpread, decGateAtr, decGateMaClean, decGateConsecutive, decGateEmaSlope, decGateMacro, decGateDiscretion, decGatePortfolio, m_engulfingActive, decEngulfingStop, "");
            return;
         }
      }
      //--- EARLY SHORT: price closed below EMA21 and held, but EMA21 not yet < EMA50
      else if(trend == -2 && m_earlyTrendEnabled)
      {
         //--- require RSI recovering from overbought (not oversold)
         if(rsi > (double)m_earlyTrendRsiMin && rsi < rsiPrev)
         {
            enter = true;
            side = -1;
            entrySizeMult = m_earlyTrendSizeMult;
            SovLogInfo(StringFormat("Sovereign RSI band %s: ENTRY SIGNAL early short (trend change: close<EMA21 held %d bars, rsi=%.2f > %d recovering)",
                        m_symbol, m_earlyTrendHoldBars, rsi, m_earlyTrendRsiMin));
         }
         else if(m_verboseLog)
         {
            PrintFormat("Sovereign RSI band %s: flat - early short but RSI filter failed (rsi=%.2f <= %d or not recovering rsiPrev=%.2f)",
                        m_symbol, rsi, m_earlyTrendRsiMin, rsiPrev);
            audit.LogDecision(m_symbol, EnumToString(_Period), now, -2, -1, "SKIP", "early short RSI filter", rsi, rsiPrev, rsiPrior, ema21, ema50, smma100, trend, closePx, m_opBuf[0], m_hiBuf[0], closePx, atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "decision audit"), P90Spread(), decGateSpread, decGateAtr, decGateMaClean, decGateConsecutive, decGateEmaSlope, decGateMacro, decGateDiscretion, decGatePortfolio, m_engulfingActive, decEngulfingStop, "");
            return;
         }
      }
      else if(trend > 0)  // uptrend: RSI crosses up through mid + margin
      {
         //--- rsi_prev <= mid and rsi > mid + margin
         if(rsiPrev <= (double)m_rsiMid && rsi > ((double)m_rsiMid + m_margin))
         {
             enter = true;
             side = 1;
             SovLogInfo(StringFormat("Sovereign RSI band %s: ENTRY SIGNAL long (rsiPrev=%.2f <= mid=%d, rsi=%.2f > threshold=%.2f)",
                         m_symbol, rsiPrev, m_rsiMid, rsi, (double)m_rsiMid + m_margin));
         }
           else if(m_verboseLog)
           {
              PrintFormat("Sovereign RSI band %s: flat - uptrend but no RSI cross (rsiPrev=%.2f mid=%d rsi=%.2f threshold=%.2f)",
                          m_symbol, rsiPrev, m_rsiMid, rsi, (double)m_rsiMid + m_margin);
              audit.LogDecision(m_symbol, EnumToString(_Period), now, 2, 1, "SKIP", "uptrend no RSI cross through mid", rsi, rsiPrev, rsiPrior, ema21, ema50, smma100, trend, closePx, m_opBuf[0], m_hiBuf[0], closePx, atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "decision audit"), P90Spread(), decGateSpread, decGateAtr, decGateMaClean, decGateConsecutive, decGateEmaSlope, decGateMacro, decGateDiscretion, decGatePortfolio, m_engulfingActive, decEngulfingStop, "");
              return;
           }
             //--- GREEN-RED LONG ENTRY: uptrend + red candle(s) near/above EMA21 with RSI between 50 and threshold
             // Wait for green candle close to confirm reversal before entering long
             if(m_greenRedEnabled && !m_inPosition && !m_engulfingActive)
             {
                double emaLo = ema21 * (1.0 - m_greenRedEmaTolerance);
                bool currRed = (closePx < m_opBuf[0]);
                bool nearEma21 = (closePx >= emaLo);
                bool rsiOk   = (rsi > 50.0 && rsi < (double)(100 - m_greenRedRsiLevel));
                datetime curBarTime = iTime(m_symbol, m_timeframe, 0);

                //--- disarm if price moved significantly below EMA21
                if(m_greenRedArmed && closePx < emaLo)
                {
                   m_greenRedArmed = false;
                   m_greenRedCount = 0;
                   m_greenRedBarTime = 0;
                   m_greenRedRsi = 0.0;
                }

                //--- arm: red candle near EMA21 with RSI between 50 and threshold
                if(currRed && nearEma21 && rsiOk)
                {
                   if(!m_greenRedArmed || m_greenRedBarTime != curBarTime)
                   {
                      m_greenRedArmed = true;
                      m_greenRedBarTime = curBarTime;
                      m_greenRedCount = 1;
                      m_greenRedRsi = rsi;
                   }
                   else
                   {
                      m_greenRedCount++;
                   }

                   if(m_greenRedCount >= m_greenRedMaxGreenCandles && !m_greenRedWaitForRed)
                   {
                      enter = true;
                      side = 1;
                      m_greenRedArmed = false;
                      m_greenRedCount = 0;
                      m_greenRedBarTime = 0;
                      m_greenRedRsi = 0.0;
                      SovLogInfo(StringFormat("Sovereign RSI band %s: ENTRY SIGNAL green-red long (no wait | redCount=%d rsi=%.2f in[50,%d] close=%.5f ema21=%.5f tol=%.4f)",
                                  m_symbol, m_greenRedCount, rsi, 100 - m_greenRedRsiLevel, closePx, ema21, m_greenRedEmaTolerance));
                   }
                }
                 else if(m_greenRedArmed && !currRed)
                 {
                    //--- green candle confirmed: enter long on close of green candle
                    enter = true;
                    side = 1;
                    m_greenRedArmed = false;
                    m_greenRedCount = 0;
                    m_greenRedBarTime = 0;
                    m_greenRedRsi = 0.0;
                     SovLogInfo(StringFormat("Sovereign RSI band %s: ENTRY SIGNAL green-red long (green confirmed | armedCount=%d rsi=%.2f in[50,%d] close=%.5f ema21=%.5f)",
                                 m_symbol, m_greenRedCount, rsi, 100 - m_greenRedRsiLevel, closePx, ema21));
                  }
               }
                 //--- RED CANDLE ABOVE EMA21 + RSI 50-60: buy long on every qualifying red candle (not far from EMA21)
                 if(m_emaTrendEnabled && !m_inPosition && !m_engulfingActive)
                 {
                    bool currRed = (closePx < m_opBuf[0]);
                    bool redAboveEma21 = (closePx > ema21);
                    bool rsiInRange = (rsi > 50.0 && rsi < (double)(100 - m_greenRedRsiLevel));
                    
                    if(currRed && redAboveEma21 && rsiInRange)
                    {
                       enter = true;
                       side = 1;
                       m_emaTrendActive = true;
                       m_emaTrendSide = 1;
                       m_emaTrendEntryBarTime = iTime(m_symbol, m_timeframe, 0);
                       double pt = SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "red-above-ema stop");
                       double lowPx = iLow(m_symbol, m_timeframe, 0);
                       m_emaTrendStopDist = MathMax(closePx - lowPx, 2.0 * m_atrBuf[0]);
                       SovLogInfo(StringFormat("Sovereign RSI band %s: ENTRY SIGNAL red-above-ema long (red close=%.5f > ema21=%.5f rsi=%.2f in[50,%d] stopDist=%.5f)",
                                   m_symbol, closePx, ema21, rsi, 100 - m_greenRedRsiLevel, m_emaTrendStopDist));
                     }
                  }
                  //--- THREE CONSECUTIVE RED ABOVE EMA21: buy immediately on third red (no downward cross)
                  if(m_emaTrendEnabled && !m_inPosition && !m_engulfingActive)
                  {
                     bool currRed = (closePx < m_opBuf[0]);
                     bool redAboveEma21 = (closePx > ema21);
                     bool redAboveEmaQualifies = currRed && redAboveEma21;
                     datetime curBarTime = iTime(m_symbol, m_timeframe, 0);

                     if(redAboveEmaQualifies)
                     {
                        if(m_threeRedLastBarTime != curBarTime)
                        {
                           m_threeRedCount++;
                           m_threeRedLastBarTime = curBarTime;
                        }
                     }
                     else
                     {
                        m_threeRedCount = 0;
                     }

                     if(m_threeRedCount >= 3)
                     {
                        enter = true;
                        side = 1;
                        m_emaTrendActive = true;
                        m_emaTrendSide = 1;
                        m_emaTrendEntryBarTime = iTime(m_symbol, m_timeframe, 0);
                        m_threeRedCount = 0;
                        double lowPx = iLow(m_symbol, m_timeframe, 0);
                        m_emaTrendStopDist = MathMax(closePx - lowPx, 2.0 * m_atrBuf[0]);
                        SovLogInfo(StringFormat("Sovereign RSI band %s: ENTRY SIGNAL three-red-above-ema long (3 consecutive red above EMA21 close=%.5f > ema21=%.5f stopDist=%.5f)",
                                    m_symbol, closePx, ema21, m_emaTrendStopDist));
                      }
                  }
                 //--- REFLEX LONG: streak of N above EMA21, then red crosses down below EMA21,
                 // then immediate green returns above EMA21 â†’ buy
                 if(m_emaTrendEnabled && !m_inPosition && !m_engulfingActive)
                 {
                    bool currCloseBelowEma = (closePx < ema21);
                    bool currCloseAboveEma = (closePx > ema21);
                    bool currGreen = (closePx > m_opBuf[0]);
                    bool currRed = (closePx < m_opBuf[0]);

                    //--- 1. Reflex trigger: flag set, current bar green above EMA21 â†’ FIRE BUY
                    if(m_reflexCrossedDown && currCloseAboveEma && currGreen)
                    {
                       enter = true;
                       side = 1;
                       m_emaTrendActive = true;
                       m_emaTrendSide = 1;
                       m_emaTrendEntryBarTime = iTime(m_symbol, m_timeframe, 0);
                       m_reflexCrossedDown = false;
                       m_aboveEmaStreak = 0;
                       double lowPx = iLow(m_symbol, m_timeframe, 0);
                       m_emaTrendStopDist = MathMax(closePx - lowPx, 2.0 * m_atrBuf[0]);
                       SovLogInfo(StringFormat("Sovereign RSI band %s: ENTRY SIGNAL reflex long (streak above EMA21 then red-cross-down then green-above close=%.5f > ema21=%.5f stopDist=%.5f)",
                                   m_symbol, closePx, ema21, m_emaTrendStopDist));
                    }

                    //--- 2. Reset flag if set but current bar is NOT the immediate green-above-EMA21 follow-up
                    if(m_reflexCrossedDown && !(currCloseAboveEma && currGreen))
                    {
                       m_reflexCrossedDown = false;
                       m_aboveEmaStreak = 0;
                    }

                    //--- 3. Detect cross-down: streak >= min, red closes below EMA21 (use streak from BEFORE this bar)
                    if(!m_reflexCrossedDown && m_aboveEmaStreak >= m_reflexMinStreak && currCloseBelowEma && currRed)
                    {
                       m_reflexCrossedDown = true;
                    }

                    //--- 4. Update streak tracking AFTER using it in cross-down detection
                    if(currCloseAboveEma)
                    {
                       m_aboveEmaStreak++;
                    }
                    else
                    {
                       m_aboveEmaStreak = 0;
                    }
                 }
                //--- EMA21 TREND LONG ENTRY: uptrend (EMA21 up) + red candle close + RSI < threshold â†’ buy immediately
               // Strength: EMA21 slope up, red body decisive (bears failed at EMA21), consecutive rejections
               if(m_emaTrendEnabled && !m_inPosition && !m_engulfingActive && !m_greenRedArmed)
               {
                  double emaLo = ema21 * (1.0 - m_greenRedEmaTolerance);
                  bool currRed = (closePx < m_opBuf[0]);
                  bool emaUp = (m_emaBuf[0] > m_emaBuf[1]);
                  bool nearEma21 = (closePx >= emaLo);
                  bool rsiOk = (rsi > 50.0 && rsi < (double)(100 - m_emaTrendRsiLevel));
                  bool bodyStrong = (MathAbs(closePx - m_opBuf[0]) >= m_emaTrendBodyMinAtr * m_atrBuf[0]);

                  //--- compute EMA21 slope over m_emaTrendSlopeBars
                  double emaSlope = 0.0;
                  if(ArraySize(m_emaBuf) >= m_emaTrendSlopeBars + 1)
                  {
                     emaSlope = (m_emaBuf[0] - m_emaBuf[m_emaTrendSlopeBars]) / m_emaTrendSlopeBars;
                  }
                  m_emaTrendSlopeValue = emaSlope;
                  bool slopeStrong = (emaSlope > m_emaTrendSlopeMin * m_atrBuf[0]);

                  //--- consecutive rejection counting
                  datetime curBarTime = iTime(m_symbol, m_timeframe, 0);
                  bool rejection = currRed && nearEma21 && bodyStrong && rsiOk;
                  if(rejection)
                  {
                     if(m_emaTrendLastBarTime != curBarTime)
                     {
                        m_emaTrendConsecutiveCount++;
                        m_emaTrendLastBarTime = curBarTime;
                     }
                  }
                  else
                  {
                     m_emaTrendConsecutiveCount = 0;
                  }

                  bool consecutiveOk = (m_emaTrendConsecutiveCount >= m_emaTrendConsecutive);

                  //--- HTF alignment: higher timeframe EMA21 also up
                  bool htfOk = true;
                  if(m_emaTrendHTFFilter)
                  {
                     // check HTF EMA21 slope via separate handle if available, else use current chart EMA slope
                     htfOk = slopeStrong; // fallback: current slope must be strong
                  }

                  if(currRed && emaUp && nearEma21 && rsiOk && bodyStrong && slopeStrong && consecutiveOk && htfOk)
                  {
                     enter = true;
                     side = 1;
                     m_emaTrendActive = true;
                     m_emaTrendSide = 1;
                     m_emaTrendEntryBarTime = iTime(m_symbol, m_timeframe, 0);
                     m_emaTrendConsecutiveCount = 0; // reset after entry
                      m_threeGreenCount = 0; // reset after entry
                      m_threeRedCount = 0; // reset after entry
                     //--- stop placement for long: stop below entry based on mode
                     double pt = SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "ema-trend stop");
                     if(m_emaTrendStopMode == 0) // wick low of red candle
                     {
                        double lowPx = iLow(m_symbol, m_timeframe, 0);
                        m_emaTrendStopDist = MathMax(closePx - lowPx, 2.0 * m_atrBuf[0]);
                     }
                     else if(m_emaTrendStopMode == 1) // ATR multiple
                        m_emaTrendStopDist = m_emaTrendStopAtrMult * m_atrBuf[0];
                     else // mode 2: EMA21 buffer
                        m_emaTrendStopDist = MathMax(closePx - ema21, m_emaTrendStopAtrMult * m_atrBuf[0]);
                     SovLogInfo(StringFormat("Sovereign RSI band %s: ENTRY SIGNAL ema-trend long (bulls reassert at EMA21 | slope=%.5f body=%.5f pts rsi=%.2f < %d close=%.5f ema21=%.5f stopDist=%.5f)",
                                 m_symbol, emaSlope, MathAbs(closePx - m_opBuf[0]), rsi, 100 - m_emaTrendRsiLevel, closePx, ema21, m_emaTrendStopDist));
                  }
               }
        }
   else if(trend < 0)  // downtrend: RSI crosses down through mid - margin
   {
      //--- BASE SHORT ENTRY - the mirror of the long trigger above.
      //--- research/rsi_band_net.py (the certified rule) has both halves:
      //---   trend[t] > 0 and rp <= mp and r >  m + margin  -> long
      //---   trend[t] < 0 and rp >= mp and r <  m - margin  -> short
      //--- This branch only had the long half: m_rsiMid was never referenced on
      //--- the short side, so a plain downtrend could ONLY trade through the
      //--- optional engulfing / green-red / ema-trend / reflex modes, which all
      //--- default to false. On a symbol whose mid band is far from 50 (BTCUSD
      //--- auto-detect mid=30) that meant the EA sat on its hands forever.
      if(rsiPrev >= (double)m_rsiMid && rsi < ((double)m_rsiMid - m_margin))
      {
          enter = true;
          side = -1;
          SovLogInfo(StringFormat("Sovereign RSI band %s: ENTRY SIGNAL short (rsiPrev=%.2f >= mid=%d, rsi=%.2f < threshold=%.2f)",
                      m_symbol, rsiPrev, m_rsiMid, rsi, (double)m_rsiMid - m_margin));
      }
        else if(m_verboseLog)
        {
           PrintFormat("Sovereign RSI band %s: flat - downtrend but no RSI cross (rsiPrev=%.2f mid=%d rsi=%.2f threshold=%.2f)",
                       m_symbol, rsiPrev, m_rsiMid, rsi, (double)m_rsiMid - m_margin);
           audit.LogDecision(m_symbol, EnumToString(_Period), now, -2, -1, "SKIP", "downtrend no RSI cross through mid", rsi, rsiPrev, rsiPrior, ema21, ema50, smma100, trend, closePx, m_opBuf[0], m_hiBuf[0], closePx, atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "decision audit"), P90Spread(), decGateSpread, decGateAtr, decGateMaClean, decGateConsecutive, decGateEmaSlope, decGateMacro, decGateDiscretion, decGatePortfolio, m_engulfingActive, decEngulfingStop, "");
           return;
        }

      //--- ENGULFING SHORT ENTRY: downtrend + RSI overbought + bearish engulfing candle
      // Prev green body fully engulfed by current red body; RSI > engulfingRsiLevel (e.g. 70)
      if(m_engulfingEnabled && !m_engulfingActive && rsi > (double)m_engulfingRsiLevel)
      {
         if(IsBearishEngulfing())
         {
            enter = true;
            side = -1;
            m_engulfingActive = true;
            m_engulfingHasGreen = false;
            m_engulfingKissedEma = false;
            double pt = SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "engulfing stop");
            double hiPx = m_hiBuf[0];   // current (engulfing red) candle high
            m_engulfingStopPrice = hiPx + m_engulfingStopBufferPts * pt;
            SovLogInfo(StringFormat("Sovereign RSI band %s: ENTRY SIGNAL engulfing short (rsi=%.2f > %d, engulfing high=%.5f stop=%.5f)",
                            m_symbol, rsi, m_engulfingRsiLevel, hiPx, m_engulfingStopPrice));
         }
      }

      //--- GREEN-RED SHORT ENTRY: downtrend + green candle(s) near/below EMA21 with RSI > threshold
      if(m_greenRedEnabled && !m_inPosition && !m_engulfingActive)
      {
         double emaHi = ema21 * (1.0 + m_greenRedEmaTolerance);
         bool currGreen = (closePx > m_opBuf[0]);
         bool nearEma21 = (closePx <= emaHi);
         bool rsiAbove = (rsi > (double)m_greenRedRsiLevel);
         datetime curBarTime = iTime(m_symbol, m_timeframe, 0);

         //--- disarm if price moved significantly above EMA21
         if(m_greenRedArmed && closePx > emaHi)
         {
            m_greenRedArmed = false;
            m_greenRedCount = 0;
            m_greenRedBarTime = 0;
            m_greenRedRsi = 0.0;
         }

         //--- arm: green candle near EMA21 with RSI above threshold
         if(currGreen && nearEma21 && rsiAbove)
         {
            if(!m_greenRedArmed || m_greenRedBarTime != curBarTime)
            {
               m_greenRedArmed = true;
               m_greenRedBarTime = curBarTime;
               m_greenRedCount = 1;
               m_greenRedRsi = rsi;
            }
            else
            {
               m_greenRedCount++;
            }

            if(m_greenRedCount >= m_greenRedMaxGreenCandles && !m_greenRedWaitForRed)
            {
               enter = true;
               side = -1;
               m_greenRedArmed = false;
               m_greenRedCount = 0;
               m_greenRedBarTime = 0;
               m_greenRedRsi = 0.0;
               SovLogInfo(StringFormat("Sovereign RSI band %s: ENTRY SIGNAL green-red short (no wait | greenCount=%d rsi=%.2f > %d close=%.5f ema21=%.5f tol=%.4f)",
                               m_symbol, m_greenRedCount, rsi, m_greenRedRsiLevel, closePx, ema21, m_greenRedEmaTolerance));
            }
         }
         else if(m_greenRedArmed && !currGreen)
         {
            //--- red candle confirmed: enter short on close of red candle
            enter = true;
            side = -1;
            m_greenRedArmed = false;
            m_greenRedCount = 0;
            m_greenRedBarTime = 0;
            m_greenRedRsi = 0.0;
            SovLogInfo(StringFormat("Sovereign RSI band %s: ENTRY SIGNAL green-red short (red confirmed | armedCount=%d rsi=%.2f > %d close=%.5f ema21=%.5f)",
                            m_symbol, m_greenRedCount, rsi, m_greenRedRsiLevel, closePx, ema21));
         }
      }

      //--- GREEN CANDLE BELOW EMA21 + RSI 40-50: sell short on every qualifying green candle (not far from EMA21)
      if(m_emaTrendEnabled && !m_inPosition && !m_engulfingActive)
      {
         bool currGreen = (closePx > m_opBuf[0]);
         bool greenBelowEma21 = (closePx < ema21);
         bool rsiInRange = (rsi > (double)m_greenRedRsiLevel && rsi < 50.0);
         
         if(currGreen && greenBelowEma21 && rsiInRange)
         {
            enter = true;
            side = -1;
            m_emaTrendActive = true;
            m_emaTrendSide = -1;
            m_emaTrendEntryBarTime = iTime(m_symbol, m_timeframe, 0);
            double pt = SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "green-below-ema stop");
            double hiPx = iHigh(m_symbol, m_timeframe, 0);
            m_emaTrendStopDist = MathMax(hiPx - closePx, 2.0 * m_atrBuf[0]);
            SovLogInfo(StringFormat("Sovereign RSI band %s: ENTRY SIGNAL green-below-ema short (green close=%.5f < ema21=%.5f rsi=%.2f in[%d,50] stopDist=%.5f)",
                            m_symbol, closePx, ema21, rsi, m_greenRedRsiLevel, m_emaTrendStopDist));
         }
      }

      //--- THREE CONSECUTIVE GREEN BELOW EMA21: sell immediately on third green (no upward cross)
      if(m_emaTrendEnabled && !m_inPosition && !m_engulfingActive)
      {
         bool currGreen = (closePx > m_opBuf[0]);
         bool greenBelowEma21 = (closePx < ema21);
         bool greenBelowEmaQualifies = currGreen && greenBelowEma21;
         datetime curBarTime = iTime(m_symbol, m_timeframe, 0);

         if(greenBelowEmaQualifies)
         {
            if(m_threeGreenLastBarTime != curBarTime)
            {
               m_threeGreenCount++;
               m_threeGreenLastBarTime = curBarTime;
            }
         }
         else
         {
            m_threeGreenCount = 0;
         }

         if(m_threeGreenCount >= 3)
         {
            enter = true;
            side = -1;
            m_emaTrendActive = true;
            m_emaTrendSide = -1;
            m_emaTrendEntryBarTime = iTime(m_symbol, m_timeframe, 0);
            m_threeGreenCount = 0;
            double hiPx = iHigh(m_symbol, m_timeframe, 0);
            m_emaTrendStopDist = MathMax(hiPx - closePx, 2.0 * m_atrBuf[0]);
            SovLogInfo(StringFormat("Sovereign RSI band %s: ENTRY SIGNAL three-green-below-ema short (3 consecutive green below EMA21 close=%.5f < ema21=%.5f stopDist=%.5f)",
                            m_symbol, closePx, ema21, m_emaTrendStopDist));
         }
      }

      //--- EMA21 TREND SHORT ENTRY: downtrend (EMA21 down) + green candle close + RSI > threshold â†’ sell immediately
      // Strength: EMA21 slope down, green body decisive (bulls failed at EMA21), consecutive rejections
      if(m_emaTrendEnabled && !m_inPosition && !m_engulfingActive && !m_greenRedArmed)
      {
         double emaHi = ema21 * (1.0 + m_greenRedEmaTolerance);
         bool currGreen = (closePx > m_opBuf[0]);
         bool emaDown = (m_emaBuf[0] < m_emaBuf[1]);
         bool nearEma21 = (closePx <= emaHi);
         bool rsiOk = (rsi > (double)m_emaTrendRsiLevel);
         bool bodyStrong = (MathAbs(closePx - m_opBuf[0]) >= m_emaTrendBodyMinAtr * m_atrBuf[0]);

         //--- compute EMA21 slope over m_emaTrendSlopeBars
         double emaSlope = 0.0;
         if(ArraySize(m_emaBuf) >= m_emaTrendSlopeBars + 1)
         {
            emaSlope = (m_emaBuf[0] - m_emaBuf[m_emaTrendSlopeBars]) / m_emaTrendSlopeBars;
         }
         m_emaTrendSlopeValue = emaSlope;
         bool slopeStrong = (emaSlope < -m_emaTrendSlopeMin * m_atrBuf[0]);

         //--- consecutive rejection counting
         datetime curBarTime = iTime(m_symbol, m_timeframe, 0);
         bool rejection = currGreen && nearEma21 && bodyStrong && rsiOk;
         if(rejection)
         {
            if(m_emaTrendLastBarTime != curBarTime)
            {
               m_emaTrendConsecutiveCount++;
               m_emaTrendLastBarTime = curBarTime;
            }
         }
         else
         {
            m_emaTrendConsecutiveCount = 0;
         }

         bool consecutiveOk = (m_emaTrendConsecutiveCount >= m_emaTrendConsecutive);
         bool htfOk = true;
         if(m_emaTrendHTFFilter)
         {
            htfOk = slopeStrong;
         }

         if(currGreen && emaDown && nearEma21 && rsiOk && bodyStrong && slopeStrong && consecutiveOk && htfOk)
         {
            enter = true;
            side = -1;
            m_emaTrendActive = true;
            m_emaTrendSide = -1;
            m_emaTrendEntryBarTime = iTime(m_symbol, m_timeframe, 0);
            m_emaTrendConsecutiveCount = 0; // reset after entry
            m_threeGreenCount = 0; // reset after entry
            m_threeRedCount = 0; // reset after entry
            //--- stop placement for short: stop above entry based on mode
            double pt = SafeSymbolDouble(SYMBOL_TRADE_TICK_SIZE, 0.0, "ema-trend stop");
            if(m_emaTrendStopMode == 0) // wick high of green candle
            {
               double hiPx = iHigh(m_symbol, m_timeframe, 0);
               m_emaTrendStopDist = MathMax(hiPx - closePx, 2.0 * m_atrBuf[0]);
            }
            else if(m_emaTrendStopMode == 1) // ATR multiple
               m_emaTrendStopDist = m_emaTrendStopAtrMult * m_atrBuf[0];
            else // mode 2: EMA21 buffer
               m_emaTrendStopDist = MathMax(ema21 - closePx, m_emaTrendStopAtrMult * m_atrBuf[0]);
            SovLogInfo(StringFormat("Sovereign RSI band %s: ENTRY SIGNAL ema-trend short (bears reassert at EMA21 | slope=%.5f body=%.5f pts rsi=%.2f > %d close=%.5f ema21=%.5f stopDist=%.5f)",
                            m_symbol, emaSlope, MathAbs(closePx - m_opBuf[0]), rsi, m_emaTrendRsiLevel, closePx, ema21, m_emaTrendStopDist));
         }
      }

      //--- REFLEX SHORT: streak of N below EMA21, then green crosses up above EMA21,
      // then immediate red returns below EMA21 â†’ sell
      if(m_emaTrendEnabled && !m_inPosition && !m_engulfingActive)
      {
         bool currCloseBelowEma = (closePx < ema21);
         bool currCloseAboveEma = (closePx > ema21);
         bool currGreen = (closePx > m_opBuf[0]);
         bool currRed = (closePx < m_opBuf[0]);

         //--- 1. Reflex trigger: flag set, current bar red below EMA21 â†’ FIRE SELL
         if(m_reflexCrossedUp && currCloseBelowEma && currRed)
         {
            enter = true;
            side = -1;
            m_emaTrendActive = true;
            m_emaTrendSide = -1;
            m_emaTrendEntryBarTime = iTime(m_symbol, m_timeframe, 0);
            m_reflexCrossedUp = false;
            m_belowEmaStreak = 0;
            double hiPx = iHigh(m_symbol, m_timeframe, 0);
            m_emaTrendStopDist = MathMax(hiPx - closePx, 2.0 * m_atrBuf[0]);
            SovLogInfo(StringFormat("Sovereign RSI band %s: ENTRY SIGNAL reflex short (streak below EMA21 then green-cross-up then red-below close=%.5f < ema21=%.5f stopDist=%.5f)",
                            m_symbol, closePx, ema21, m_emaTrendStopDist));
         }

         //--- 2. Reset flag if set but current bar is NOT the immediate red-below-EMA21 follow-up
         if(m_reflexCrossedUp && !(currCloseBelowEma && currRed))
         {
            m_reflexCrossedUp = false;
            m_belowEmaStreak = 0;
         }

         //--- 3. Detect cross-up: streak >= min, green closes above EMA21 (use streak from BEFORE this bar)
         if(!m_reflexCrossedUp && m_belowEmaStreak >= m_reflexMinStreak && currCloseAboveEma && currGreen)
         {
            m_reflexCrossedUp = true;
         }

         //--- 4. Update streak tracking AFTER using it in cross-up detection
         if(currCloseBelowEma)
         {
            m_belowEmaStreak++;
         }
      }
   }   // close trend<0 block

   if(enter)
   {
      //--- re-entry cooldown: parity with research/rsi_band_net.py --cooldown.
      //--- Counted in BARS of the chart timeframe, so the profile table can carry a
      //--- per-timeframe value (BTCUSD H1 was the only BTCUSD config that survived
      //--- all three splits, and it needs 40 bars of quiet after an exit).
      if(m_cooldownBars > 0 && m_lastExitTime > 0)
      {
         int sinceExit = iBarShift(m_symbol, m_timeframe, m_lastExitTime, false);
         if(sinceExit >= 0 && sinceExit < m_cooldownBars)
         {
            m_lastBlockReason = StringFormat("re-entry cooldown %d/%d bars", sinceExit, m_cooldownBars);
            m_lastBlockTime   = TimeCurrent();
            if(m_verboseLog)
               PrintFormat("Sovereign RSI band %s: SKIP entry - re-entry cooldown (%d/%d bars since last exit)",
                           m_symbol, sinceExit, m_cooldownBars);
            return;
         }
      }

      //--- Market structure filter: require structure intact for trade direction
      if(m_structureFilterEnabled && !m_forceBypassAllFilters)
      {
         if(side > 0 && !m_structureIntactLong)
         {
            decGateSpread = false;
            m_lastBlockReason = "market structure long broken (green failed to reclaim above red opens)";
            m_lastBlockTime = TimeCurrent();
            PrintFormat("Sovereign RSI band %s: EXECUTION BLOCKED: market structure long broken | close=%.5f redOpenHigh=%.5f", m_symbol, closePx, m_redOpenHigh);
             audit.LogDecision(m_symbol, EnumToString(_Period), now, decEntryMode, side, "SKIP", "market structure long broken", rsi, rsiPrev, rsiPrior, ema21, ema50, smma100, trend, closePx, m_opBuf[0], m_hiBuf[0], closePx, atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "decision audit"), P90Spread(), decGateSpread, decGateAtr, decGateMaClean, decGateConsecutive, decGateEmaSlope, decGateMacro, decGateDiscretion, decGatePortfolio, m_engulfingActive, decEngulfingStop, "");
            return;
         }
         if(side < 0 && !m_structureIntactShort)
         {
            decGateSpread = false;
            m_lastBlockReason = "market structure short broken (red failed to break below green closes)";
            m_lastBlockTime = TimeCurrent();
            PrintFormat("Sovereign RSI band %s: EXECUTION BLOCKED: market structure short broken | close=%.5f greenCloseLow=%.5f", m_symbol, closePx, m_greenCloseLow);
             audit.LogDecision(m_symbol, EnumToString(_Period), now, decEntryMode, side, "SKIP", "market structure short broken", rsi, rsiPrev, rsiPrior, ema21, ema50, smma100, trend, closePx, m_opBuf[0], m_hiBuf[0], closePx, atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "decision audit"), P90Spread(), decGateSpread, decGateAtr, decGateMaClean, decGateConsecutive, decGateEmaSlope, decGateMacro, decGateDiscretion, decGatePortfolio, m_engulfingActive, decEngulfingStop, "");
            return;
         }
      }

      //--- FORCE BYPASS ALL FILTERS: skip all RSI band entry gates and PLACE THE ORDER.
      //--- This branch used to print "skipping ... gates" and then fall out of the
      //--- function without ever calling OpenPosition(), so the EA logged an entry
      //--- signal on every tick and never traded. The bypass now owns the entry.
      if(m_forceBypassAllFilters)
      {
         decEntryMode = 1;   // 1 = forceBypass in the audit CSV

         //--- one entry per bar, even when intra-bar evaluation is on
         datetime byBar = iTime(m_symbol, m_timeframe, 0);
         if(m_intraBar && m_lastEntryBarTime > 0 && byBar > 0 && byBar == m_lastEntryBarTime)
         {
            m_lastBlockReason = "force bypass same-bar guard";
            m_lastBlockTime   = TimeCurrent();
            return;
         }

         int    bySpread = (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "bypass entry");
         double byP90    = P90Spread();
         double byStop   = (m_emaTrendActive && m_emaTrendStopDist > 0.0) ? m_emaTrendStopDist : m_atrBuf[0];

         if(m_verboseLog)
            PrintFormat("Sovereign RSI band %s: FORCE BYPASS - placing %s without gates (rsi=%.2f close=%.5f atr=%.5f stopDist=%.5f)",
                        m_symbol, (side > 0 ? "LONG" : "SHORT"), rsi, closePx, m_atrBuf[0], byStop);

         m_lastEntryBarTime = byBar;
         decGateSpread      = true;
         decGateAtr         = true;
         decGateMaClean     = true;
         decGateConsecutive = true;
         decGateEmaSlope    = true;
         decGateMacro       = true;
         decGateDiscretion  = true;
         decGatePortfolio   = true;

         audit.LogDecision(m_symbol, EnumToString(_Period), now,
            decEntryMode, side, "ENTER", "force bypass (all gates skipped)",
            rsi, rsiPrev, rsiPrior, ema21, ema50, smma100, trend,
            closePx, m_opBuf[0], m_hiBuf[0], closePx,
            m_atrBuf[0], bySpread, byP90,
            decGateSpread, decGateAtr, decGateMaClean,
            decGateConsecutive, decGateEmaSlope,
            decGateMacro, decGateDiscretion, decGatePortfolio,
            m_engulfingActive, decEngulfingStop,
            "force bypass entry");

         OpenPosition(side, closePx, byStop, t, perf, tl, audit, "SOV-RSI-BYPASS", false, 1.0);
         return;
      }
      else
      {
             //--- spread gate
             int spread = (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "OnNewBar entry");
             double p90 = P90Spread();
             PrintFormat("Sovereign RSI band %s: entry attempt side=%d spread=%d p90=%.1f gatePct=%d",
                         m_symbol, side, spread, p90, m_spreadGatePct);
               if(!SpreadAllowed())
               {
                  decGateSpread = false;
                  m_lastBlockReason = "spread gate";
                  m_lastBlockTime = TimeCurrent();
                  PrintFormat("Sovereign RSI band %s: EXECUTION BLOCKED: spread gate | Current spread=%d | Threshold p90=%.1f", m_symbol, spread, p90);
                   audit.LogDecision(m_symbol, EnumToString(_Period), now, decEntryMode, side, "SKIP", "spread gate", rsi, rsiPrev, rsiPrior, ema21, ema50, smma100, trend, closePx, m_opBuf[0], m_hiBuf[0], closePx, atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "decision audit"), P90Spread(), decGateSpread, decGateAtr, decGateMaClean, decGateConsecutive, decGateEmaSlope, decGateMacro, decGateDiscretion, decGatePortfolio, m_engulfingActive, decEngulfingStop, "");
             return;
               }
               decGateSpread = true;

              //--- ATR percentile gate: skip entry when ATR is in the top (1-pct) of recent history
              if(!AtrAllowed())
              {
                 decGateAtr = false;
                 m_lastBlockReason = "ATR percentile gate";
                 m_lastBlockTime = TimeCurrent();
                 PrintFormat("Sovereign RSI band %s: EXECUTION BLOCKED: ATR percentile gate | Current ATR pct=%.2f | Threshold=%.2f",
                             m_symbol, m_atrPctMax);
                  audit.LogDecision(m_symbol, EnumToString(_Period), now, decEntryMode, side, "SKIP", "ATR percentile gate", rsi, rsiPrev, rsiPrior, ema21, ema50, smma100, trend, closePx, m_opBuf[0], m_hiBuf[0], closePx, atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "decision audit"), P90Spread(), decGateSpread, decGateAtr, decGateMaClean, decGateConsecutive, decGateEmaSlope, decGateMacro, decGateDiscretion, decGatePortfolio, m_engulfingActive, decEngulfingStop, "");
             return;
              }
              decGateAtr = true;

               //--- clean trend: skip entry when too many MA21 crossovers in lookback window
               if(!MAIsClean())
               {
                  decGateMaClean = false;
                  m_lastBlockReason = "MA crossover gate";
                  m_lastBlockTime = TimeCurrent();
                  PrintFormat("Sovereign RSI band %s: EXECUTION BLOCKED: MA crossover gate | Current crosses=%d | Max=%d | Window=%d bars",
                              m_symbol, m_maCrossCount, m_maCrossMax, m_maCrossWindow);
                   audit.LogDecision(m_symbol, EnumToString(_Period), now, decEntryMode, side, "SKIP", "MA crossover gate", rsi, rsiPrev, rsiPrior, ema21, ema50, smma100, trend, closePx, m_opBuf[0], m_hiBuf[0], closePx, atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "decision audit"), P90Spread(), decGateSpread, decGateAtr, decGateMaClean, decGateConsecutive, decGateEmaSlope, decGateMacro, decGateDiscretion, decGatePortfolio, m_engulfingActive, decEngulfingStop, "");
             return;
               }
               decGateMaClean = true;

              //--- consecutive closes: require N closes above/below EMA21 before entry
              if(!ConsecutiveClosesOk(side))
              {
                 int need = (side > 0) ? m_minConsecutiveLong : m_minConsecutiveShort;
                 decGateConsecutive = false;
                 m_lastBlockReason = "consecutive closes gate";
                 m_lastBlockTime = TimeCurrent();
                 PrintFormat("Sovereign RSI band %s: EXECUTION BLOCKED: consecutive closes gate | side=%d | Current consecutive=%d | Need=%d",
                             m_symbol, side, m_consecutiveCloseCount, need);
                  audit.LogDecision(m_symbol, EnumToString(_Period), now, decEntryMode, side, "SKIP", "consecutive closes gate", rsi, rsiPrev, rsiPrior, ema21, ema50, smma100, trend, closePx, m_opBuf[0], m_hiBuf[0], closePx, atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "decision audit"), P90Spread(), decGateSpread, decGateAtr, decGateMaClean, decGateConsecutive, decGateEmaSlope, decGateMacro, decGateDiscretion, decGatePortfolio, m_engulfingActive, decEngulfingStop, "");
             return;
              }
              decGateConsecutive = true;

              //--- EMA21 slope: require EMA21 slope in trade direction
              if(m_requireEma21Slope && !Ema21SlopeOk(side))
              {
                 double slope = m_emaBuf[0] - m_emaBuf[3];
                 decGateEmaSlope = false;
                 m_lastBlockReason = "EMA21 slope gate";
                 m_lastBlockTime = TimeCurrent();
                 PrintFormat("Sovereign RSI band %s: EXECUTION BLOCKED: EMA21 slope gate | side=%d | Current slope=%.5f | Required slope direction=%s",
                             m_symbol, side, slope, (side > 0 ? "positive" : "negative"));
                  audit.LogDecision(m_symbol, EnumToString(_Period), now, decEntryMode, side, "SKIP", "EMA21 slope gate", rsi, rsiPrev, rsiPrior, ema21, ema50, smma100, trend, closePx, m_opBuf[0], m_hiBuf[0], closePx, atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "decision audit"), P90Spread(), decGateSpread, decGateAtr, decGateMaClean, decGateConsecutive, decGateEmaSlope, decGateMacro, decGateDiscretion, decGatePortfolio, m_engulfingActive, decEngulfingStop, "");
             return;
              }
              decGateEmaSlope = true;

              if(!CanPyramidInProfit((ulong)m_magic, m_symbol))
              {
                 decGateDiscretion = false;
                 m_lastBlockReason = "pyramiding guard (position not in profit)";
                 m_lastBlockTime = TimeCurrent();
                 PrintFormat("Sovereign RSI band %s: EXECUTION BLOCKED: pyramiding guard | Existing position not in net profit", m_symbol);
                  audit.LogDecision(m_symbol, EnumToString(_Period), now, decEntryMode, side, "SKIP", "pyramiding guard: position not in profit", rsi, rsiPrev, rsiPrior, ema21, ema50, smma100, trend, closePx, m_opBuf[0], m_hiBuf[0], closePx, atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "decision audit"), P90Spread(), decGateSpread, decGateAtr, decGateMaClean, decGateConsecutive, decGateEmaSlope, decGateMacro, decGateDiscretion, decGatePortfolio, m_engulfingActive, decEngulfingStop, "");
             return;
              }
              decGateDiscretion = true;

              //--- Rule 1: Macro trend bias filter (directional lock)
              //--- Shorts ONLY when Bid < Macro MA; Longs ONLY when Bid > Macro MA
              if(m_macroMaReady && m_macroMaBuf[0] > 0.0)
              {
                 double bid = SafeSymbolDouble(SYMBOL_BID, 0.0, "macro MA lock");
                 if(bid > 0.0)
                 {
                    if(side < 0 && bid >= m_macroMaBuf[0])
                    {
                       decGateMacro = false;
                       m_lastBlockReason = "macro MA lock (short blocked: Bid >= Macro MA)";
                       m_lastBlockTime = TimeCurrent();
                       PrintFormat("Sovereign RSI band %s: EXECUTION BLOCKED: macro MA lock | side=short | Current Bid=%.5f | MacroMA=%.5f | Condition: Bid >= MacroMA",
                                   m_symbol, bid, m_macroMaBuf[0]);
                        audit.LogDecision(m_symbol, EnumToString(_Period), now, decEntryMode, side, "SKIP", "macro MA lock: short blocked", rsi, rsiPrev, rsiPrior, ema21, ema50, smma100, trend, closePx, m_opBuf[0], m_hiBuf[0], closePx, atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "decision audit"), P90Spread(), decGateSpread, decGateAtr, decGateMaClean, decGateConsecutive, decGateEmaSlope, decGateMacro, decGateDiscretion, decGatePortfolio, m_engulfingActive, decEngulfingStop, "");
             return;
                    }
                     if(side > 0 && bid <= m_macroMaBuf[0])
                     {
                        decGateMacro = false;
                        m_lastBlockReason = "macro MA lock (long blocked: Bid <= Macro MA)";
                        m_lastBlockTime = TimeCurrent();
                        PrintFormat("Sovereign RSI band %s: EXECUTION BLOCKED: macro MA lock | side=long | Current Bid=%.5f | MacroMA=%.5f | Condition: Bid <= MacroMA",
                                    m_symbol, bid, m_macroMaBuf[0]);
                        audit.LogDecision(m_symbol, EnumToString(_Period), now, decEntryMode, side, "SKIP", "macro MA lock: long blocked", rsi, rsiPrev, rsiPrior, ema21, ema50, smma100, trend, closePx, m_opBuf[0], m_hiBuf[0], closePx, atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "decision audit"), P90Spread(), decGateSpread, decGateAtr, decGateMaClean, decGateConsecutive, decGateEmaSlope, decGateMacro, decGateDiscretion, decGatePortfolio, m_engulfingActive, decEngulfingStop, "");
             return;
                     }
                  }
               }
               decGateMacro = true;

            //--- intra-bar guard: don't enter twice on the same forming bar
              if(m_intraBar && m_lastEntryBarTime > 0)
              {
                 datetime curBarTime = iTime(m_symbol, m_timeframe, 0);
                 if(curBarTime > 0 && curBarTime == m_lastEntryBarTime)
                 {
                    m_lastBlockReason = "intra-bar same-bar guard";
                    m_lastBlockTime = TimeCurrent();
                    if(m_verboseLog)
                       PrintFormat("Sovereign RSI band %s: ENTRY SKIPPED same-bar guard (barTime=%s)", m_symbol, TimeToString(curBarTime));
                     audit.LogDecision(m_symbol, EnumToString(_Period), now, decEntryMode, side, "SKIP", "intra-bar same-bar guard", rsi, rsiPrev, rsiPrior, ema21, ema50, smma100, trend, closePx, m_opBuf[0], m_hiBuf[0], closePx, atr, (int)SafeSymbolInteger(SYMBOL_SPREAD, 0, "decision audit"), P90Spread(), decGateSpread, decGateAtr, decGateMaClean, decGateConsecutive, decGateEmaSlope, decGateMacro, decGateDiscretion, decGatePortfolio, m_engulfingActive, decEngulfingStop, "");
             return;
                 }
              }

              m_lastEntryBarTime = iTime(m_symbol, m_timeframe, 0);
              decGatePortfolio = true;
              //--- LOG ENTRY DECISION (entered gate, about to place order)
              audit.LogDecision(m_symbol, EnumToString(_Period), now,
                 decEntryMode, side, "ENTER", "all gates passed",
                 rsi, rsiPrev, rsiPrior, ema21, ema50, smma100, trend,
                 closePx, m_opBuf[0], m_hiBuf[0], closePx,
                 atr, spread, p90,
                 decGateSpread, decGateAtr, decGateMaClean,
                 decGateConsecutive, decGateEmaSlope,
                 decGateMacro, decGateDiscretion, decGatePortfolio,
                 m_engulfingActive, decEngulfingStop,
                 "entry executed");
                 double entryStopDist = (m_emaTrendActive && m_emaTrendStopDist > 0.0) ? m_emaTrendStopDist : m_atrBuf[0];
                  OpenPosition(side, closePx, entryStopDist, t, perf, tl, audit, "SOV-RSI", false, entrySizeMult);
         }
    }
}
}

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
#endif // __SOVEREIGN_RSI_BAND_MQH__
