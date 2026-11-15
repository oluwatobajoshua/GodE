//+------------------------------------------------------------------+
//|                                   SovereignDynamicPlan.mqh       |
//|  Fully dynamic, indicator-driven trade plan for the Sovereign EA. |
//|                                                                  |
//|  RULE: no hardcoded price levels, no fixed coordinates, no static |
//|  currency values. Every entry reference, stop zone, take-profit,  |
//|  scale-in checkpoint and visual markup is derived at runtime from |
//|  iMA / iATR / iRSI / iFractals buffers and the live Bid/Ask.      |
//|                                                                  |
//|  Consumers:                                                       |
//|    - SovereignGodsEyeEA.mq5  (panel + macro directional lock)     |
//|    - CSovereignChartVisualizer (SOV_DYN_ markup)                  |
//|                                                                  |
//|  Display/decision INPUT only: the plan never sends an order.      |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_DYNAMIC_PLAN_MQH__
#define __SOVEREIGN_DYNAMIC_PLAN_MQH__

#include <Sovereign\SovereignError.mqh>

//--- A snapshot of the dynamic plan, recomputed every tick / new bar.
struct SovereignPlanView
{
   bool     valid;        // enough indicator data to trust the plan
   int      side;         // +1 long-only regime, -1 short-only regime, 0 no plan
   datetime barTime;      // current bar time (chart anchor)
   double   bid;
   double   ask;
   double   atr;
   double   slowMa;       // macro filter (e.g. EMA200)
   double   fastMa;       // dynamic resistance band upper (e.g. EMA21)
   double   midMa;        // dynamic resistance band lower (e.g. EMA50)
   double   maSpread;     // fastMa - midMa (signed)
   double   rsi;
   double   entry;        // reference entry = live price in the trade direction
   double   stop;         // hard stop
   double   tp;           // take profit
   double   zoneTop;      // stop-zone upper bound (swing high / ATR ceiling)
   double   zoneBot;      // stop-zone lower bound (entry stop level)
   double   scale2;       // next scale-in checkpoint
   double   scale3;       // second scale-in checkpoint
   double   swingHigh;    // most recent confirmed fractal high
   double   swingLow;     // most recent confirmed fractal low
   bool     inRetestBand; // price is inside the fast/mid MA band (retest)
   bool     guardArmed;   // profit-gated pyramiding guard
   int      legs;         // open Sovereign legs on this symbol
   double   netProfit;    // net profit of those legs
   string   note;         // human-readable reason for no plan

   //--- analyst synthesis (what an LLM analyst would report)
   string   trendState;      // "uptrend" / "downtrend" / "range"
   string   momentumState;   // "strong up (rising)" etc.
   string   volState;        // "low" / "normal" / "elevated" / "extreme"
   string   structureState;  // "at resistance" / "at support" / "mid-range"
   string   biasText;        // "LONG" / "SHORT" / "NEUTRAL"
   double   confidence;      // 0.05 .. 0.95
   double   atrPct;          // ATR percentile in recent history (0..1)
   double   rsiSlope;        // rsi[0] - rsi[2]
   double   fastSlope;       // (fast[0]-fast[3]) in ATR units
   double   distResistance;  // (swingHigh - bid) / atr  (-1 = unknown)
   double   distSupport;     // (bid - swingLow) / atr   (-1 = unknown)
   string   narrative;       // one-line plain-English read
   string   risks;           // comma-separated risk flags
};

class CSovereignDynamicPlan
{
private:
   string           m_symbol;
   ENUM_TIMEFRAMES  m_tf;
   int              m_hSlow;
   int              m_hFast;
   int              m_hMid;
   int              m_hAtr;
   int              m_hRsi;
   int              m_hFractals;

   int              m_slowPeriod;
   int              m_slowMethod;
   int              m_fastPeriod;
   int              m_fastMethod;
   int              m_midPeriod;
   int              m_midMethod;
   int              m_atrPeriod;
   int              m_rsiPeriod;
   double           m_stopAtr;
   double           m_tpAtr;
   double           m_scaleStepAtr;

   double           m_slowBuf[];
   double           m_fastBuf[];
   double           m_midBuf[];
   double           m_atrBuf[];
   double           m_rsiBuf[];
   double           m_upperBuf[];   // iFractals upper (swing highs)
   double           m_lowerBuf[];   // iFractals lower (swing lows)

   datetime         m_lastBar;
   bool             m_ready;

   bool   CopySeries(const int handle, double &buf[], const int count, const string ctx);
   double LastFractal(const double &buf[], const int count);
   double AtrPercentile(const double &buf[], const int n, const double cur);

public:
   CSovereignDynamicPlan();
   ~CSovereignDynamicPlan();

   bool Init(const string symbol, const ENUM_TIMEFRAMES tf,
             const int slowPeriod, const int slowMethod,
             const int fastPeriod, const int midPeriod,
             const int atrPeriod, const int rsiPeriod,
             const double stopAtr, const double tpAtr,
             const double scaleStepAtr,
             const int fastMethod = 1, const int midMethod = 1);
   void Deinit();
   bool Refresh();

   bool   Ready()      const { return m_ready; }
   double SlowMa()     const { return (ArraySize(m_slowBuf) > 0) ? m_slowBuf[0] : 0.0; }
   double FastMa()     const { return (ArraySize(m_fastBuf) > 0) ? m_fastBuf[0] : 0.0; }
   double MidMa()      const { return (ArraySize(m_midBuf)  > 0) ? m_midBuf[0]  : 0.0; }
   double Atr()        const { return (ArraySize(m_atrBuf)  > 0) ? m_atrBuf[0]  : 0.0; }
   double Rsi()        const { return (ArraySize(m_rsiBuf)  > 0) ? m_rsiBuf[0]  : 0.0; }

   //--- Build the plan snapshot for the current market state.
   void Build(SovereignPlanView &view,
              const int legs, const double netProfit, const bool guardArmed);

   //--- Poly-line of the slow MA over the last `maxPoints` bars (for markup).
   int SlowPath(datetime &times[], double &prices[], const int maxPoints);
   //--- Poly-line of the fast/mid band over the last `maxPoints` bars.
   int BandPath(datetime &times[], double &fast[], double &mid[], const int maxPoints);
   //--- Combined poly-line (slow + fast + mid) in one aligned pass.
   int FullPath(datetime &times[], double &slow[], double &fast[], double &mid[], const int maxPoints);
};

//+------------------------------------------------------------------+
//| Constructor                                                      |
//+------------------------------------------------------------------+
CSovereignDynamicPlan::CSovereignDynamicPlan()
{
   m_symbol        = "";
   m_tf            = PERIOD_CURRENT;
   m_hSlow         = INVALID_HANDLE;
   m_hFast         = INVALID_HANDLE;
   m_hMid          = INVALID_HANDLE;
   m_hAtr          = INVALID_HANDLE;
   m_hRsi          = INVALID_HANDLE;
   m_hFractals     = INVALID_HANDLE;
   m_slowPeriod    = 100;   // matches fxmopol.tpl (Init overrides from template/inputs)
   m_slowMethod    = 1;
   m_fastPeriod    = 21;
   m_fastMethod    = 1;
   m_midPeriod     = 50;
   m_midMethod     = 1;
   m_atrPeriod     = 14;
   m_rsiPeriod     = 9;
   m_stopAtr       = 1.5;
   m_tpAtr         = 2.0;
   m_scaleStepAtr  = 0.75;
   m_lastBar       = 0;
   m_ready         = false;
}

CSovereignDynamicPlan::~CSovereignDynamicPlan()
{
   Deinit();
}

//+------------------------------------------------------------------+
//| Init — create every indicator handle once                        |
//+------------------------------------------------------------------+
bool CSovereignDynamicPlan::Init(const string symbol, const ENUM_TIMEFRAMES tf,
                                 const int slowPeriod, const int slowMethod,
                                 const int fastPeriod, const int midPeriod,
                                 const int atrPeriod, const int rsiPeriod,
                                 const double stopAtr, const double tpAtr,
                                 const double scaleStepAtr,
                                 const int fastMethod, const int midMethod)
{
   if(StringLen(symbol) == 0)
   {
      Print("Sovereign plan: ERROR Init called with empty symbol");
      return false;
   }

   m_symbol       = symbol;
   m_tf           = tf;
   m_slowPeriod   = MathMax(2, slowPeriod);
   m_slowMethod   = (slowMethod < 0 || slowMethod > 3) ? 1 : slowMethod;
   m_fastPeriod   = MathMax(2, fastPeriod);
   m_fastMethod   = (fastMethod < 0 || fastMethod > 3) ? 1 : fastMethod;
   m_midPeriod    = MathMax(2, midPeriod);
   m_midMethod    = (midMethod < 0 || midMethod > 3) ? 1 : midMethod;
   m_atrPeriod    = MathMax(2, atrPeriod);
   m_rsiPeriod    = MathMax(2, rsiPeriod);
   m_stopAtr      = (stopAtr      > 0.0) ? stopAtr      : 1.5;
   m_tpAtr        = (tpAtr        > 0.0) ? tpAtr        : 2.0;
   m_scaleStepAtr = (scaleStepAtr > 0.0) ? scaleStepAtr : 0.75;

   m_hSlow = iMA(m_symbol, m_tf, m_slowPeriod, 0, (ENUM_MA_METHOD)m_slowMethod, PRICE_CLOSE);
   m_hFast = iMA(m_symbol, m_tf, m_fastPeriod, 0, (ENUM_MA_METHOD)m_fastMethod, PRICE_CLOSE);
   m_hMid  = iMA(m_symbol, m_tf, m_midPeriod,  0, (ENUM_MA_METHOD)m_midMethod,  PRICE_CLOSE);
   m_hAtr  = iATR(m_symbol, m_tf, m_atrPeriod);
   m_hRsi  = iRSI(m_symbol, m_tf, m_rsiPeriod, PRICE_CLOSE);
   m_hFractals = iFractals(m_symbol, m_tf);

   if(!SovCheckHandle(m_hSlow, "plan iMA slow"))     return false;
   if(!SovCheckHandle(m_hFast, "plan iMA fast"))     return false;
   if(!SovCheckHandle(m_hMid,  "plan iMA mid"))      return false;
   if(!SovCheckHandle(m_hAtr,  "plan iATR"))         return false;
   if(!SovCheckHandle(m_hRsi,  "plan iRSI"))         return false;
   if(!SovCheckHandle(m_hFractals, "plan iFractals")) return false;

   m_lastBar = 0;
   m_ready   = false;
   PrintFormat("Sovereign plan %s %s: INIT OK slow=%d/%d fast=%d/%d mid=%d/%d atr=%d rsi=%d "
               "stopAtr=%.2f tpAtr=%.2f scaleStepAtr=%.2f",
               m_symbol, EnumToString(m_tf), m_slowPeriod, m_slowMethod,
               m_fastPeriod, m_fastMethod, m_midPeriod, m_midMethod,
               m_atrPeriod, m_rsiPeriod, m_stopAtr, m_tpAtr, m_scaleStepAtr);
   return true;
}

//+------------------------------------------------------------------+
//| Deinit                                                           |
//+------------------------------------------------------------------+
void CSovereignDynamicPlan::Deinit()
{
   if(m_hSlow     != INVALID_HANDLE) { IndicatorRelease(m_hSlow);     m_hSlow     = INVALID_HANDLE; }
   if(m_hFast     != INVALID_HANDLE) { IndicatorRelease(m_hFast);     m_hFast     = INVALID_HANDLE; }
   if(m_hMid      != INVALID_HANDLE) { IndicatorRelease(m_hMid);      m_hMid      = INVALID_HANDLE; }
   if(m_hAtr      != INVALID_HANDLE) { IndicatorRelease(m_hAtr);      m_hAtr      = INVALID_HANDLE; }
   if(m_hRsi      != INVALID_HANDLE) { IndicatorRelease(m_hRsi);      m_hRsi      = INVALID_HANDLE; }
   if(m_hFractals != INVALID_HANDLE) { IndicatorRelease(m_hFractals); m_hFractals = INVALID_HANDLE; }
   m_ready = false;
}

//+------------------------------------------------------------------+
//| CopySeries — series copy with error logging                      |
//+------------------------------------------------------------------+
bool CSovereignDynamicPlan::CopySeries(const int handle, double &buf[], const int count, const string ctx)
{
   if(handle == INVALID_HANDLE)
   {
      SovLogError(ctx + " invalid handle", m_symbol);
      return false;
   }
   if(CopyBuffer(handle, 0, 0, count, buf) < count)
   {
      SovLogError(ctx + " CopyBuffer short", m_symbol);
      return false;
   }
   ArraySetAsSeries(buf, true);
   return true;
}

//+------------------------------------------------------------------+
//| LastFractal — most recent confirmed fractal value (skip bar 0)   |
//+------------------------------------------------------------------+
double CSovereignDynamicPlan::LastFractal(const double &buf[], const int count)
{
   int n = MathMin(count, ArraySize(buf));
   for(int i = 1; i < n; i++)          // bar 0 is still forming
   {
      if(buf[i] > 0.0)
         return buf[i];
   }
   return 0.0;
}

//+------------------------------------------------------------------+
//| AtrPercentile — rank of `cur` within the recent ATR history      |
//+------------------------------------------------------------------+
double CSovereignDynamicPlan::AtrPercentile(const double &buf[], const int n, const double cur)
{
   int m = MathMin(n, ArraySize(buf));
   if(m < 10 || cur <= 0.0)
      return 0.5;
   int below = 0, total = 0;
   for(int i = 1; i < m; i++)          // skip bar 0 (the value being ranked)
   {
      if(buf[i] <= 0.0) continue;
      total++;
      if(buf[i] <= cur) below++;
   }
   if(total < 10)
      return 0.5;
   return (double)below / (double)total;
}
//+------------------------------------------------------------------+
bool CSovereignDynamicPlan::Refresh()
{
   datetime barTime = iTime(m_symbol, m_tf, 0);
   if(barTime <= 0)
      return false;
   if(barTime == m_lastBar && m_ready)
      return true;                     // same bar: buffers already current

   const int need = 240;
   bool ok = true;
   ok = CopySeries(m_hSlow, m_slowBuf, need, "plan slow MA") && ok;
   ok = CopySeries(m_hFast, m_fastBuf, need, "plan fast MA") && ok;
   ok = CopySeries(m_hMid,  m_midBuf,  need, "plan mid MA")  && ok;
   ok = CopySeries(m_hAtr,  m_atrBuf,  need, "plan ATR")     && ok;
   ok = CopySeries(m_hRsi,  m_rsiBuf,  need, "plan RSI")     && ok;
   ok = CopySeries(m_hFractals, m_upperBuf, need, "plan fractals") && ok;

   if(!ok)
   {
      m_ready = false;
      return false;
   }

   //--- iFractals has two buffers: 0 = upper, 1 = lower. CopyBuffer above only
   //--- fetched buffer 0; fetch buffer 1 explicitly for swing lows.
   if(CopyBuffer(m_hFractals, 1, 0, need, m_lowerBuf) < need)
   {
      SovLogError("plan fractals lower CopyBuffer short", m_symbol);
      m_ready = false;
      return false;
   }
   ArraySetAsSeries(m_lowerBuf, true);

   m_lastBar = barTime;
   m_ready   = (m_slowBuf[0] > 0.0 && m_fastBuf[0] > 0.0 && m_midBuf[0] > 0.0 &&
                m_atrBuf[0]  > 0.0 && m_rsiBuf[0]  > 0.0);
   return m_ready;
}

//+------------------------------------------------------------------+
//| Build — derive the full dynamic plan from live buffers + price   |
//+------------------------------------------------------------------+
void CSovereignDynamicPlan::Build(SovereignPlanView &view,
                                  const int legs, const double netProfit, const bool guardArmed)
{
   //--- defaults: an invalid plan is safe (draws nothing, blocks nothing)
   view.valid        = false;
   view.side         = 0;
   view.barTime      = iTime(m_symbol, m_tf, 0);
   view.bid          = SymbolInfoDouble(m_symbol, SYMBOL_BID);
   view.ask          = SymbolInfoDouble(m_symbol, SYMBOL_ASK);
   view.atr          = 0.0;
   view.slowMa       = 0.0;
   view.fastMa       = 0.0;
   view.midMa        = 0.0;
   view.maSpread     = 0.0;
   view.rsi          = 0.0;
   view.entry        = 0.0;
   view.stop         = 0.0;
   view.tp           = 0.0;
   view.zoneTop      = 0.0;
   view.zoneBot      = 0.0;
   view.scale2       = 0.0;
   view.scale3       = 0.0;
   view.swingHigh    = 0.0;
   view.swingLow     = 0.0;
   view.inRetestBand = false;
   view.guardArmed   = guardArmed;
   view.legs         = legs;
   view.netProfit    = netProfit;
   view.note         = "";
   view.trendState     = "n/a";
   view.momentumState  = "n/a";
   view.volState       = "n/a";
   view.structureState = "n/a";
   view.biasText       = "NEUTRAL";
   view.confidence     = 0.0;
   view.atrPct         = 0.5;
   view.rsiSlope       = 0.0;
   view.fastSlope      = 0.0;
   view.distResistance = -1.0;
   view.distSupport    = -1.0;
   view.narrative      = "";
   view.risks          = "";
   if(!m_ready || ArraySize(m_slowBuf) < 1 || ArraySize(m_atrBuf) < 1)
   {
      view.note = "indicators not ready";
      return;
   }

   view.atr      = m_atrBuf[0];
   view.slowMa   = m_slowBuf[0];
   view.fastMa   = m_fastBuf[0];
   view.midMa    = m_midBuf[0];
   view.maSpread = view.fastMa - view.midMa;
   view.rsi      = m_rsiBuf[0];
   view.swingHigh = LastFractal(m_upperBuf, 240);
   view.swingLow  = LastFractal(m_lowerBuf, 240);

   if(view.atr <= 0.0 || view.slowMa <= 0.0 || view.fastMa <= 0.0 || view.midMa <= 0.0)
   {
      view.note = "indicator buffers zero";
      return;
   }
   view.valid = true;

   //--- directional regime: price vs slow MA AND fast/mid alignment
   double px = view.bid;
   int side = 0;
   if(px > view.slowMa && view.fastMa > view.midMa)      side =  1;
   else if(px < view.slowMa && view.fastMa < view.midMa) side = -1;

   //--- retest band: price inside the fast/mid MA corridor
   if(view.fastMa >= view.midMa)
      view.inRetestBand = (px <= view.fastMa && px >= view.midMa);
   else
      view.inRetestBand = (px >= view.fastMa && px <= view.midMa);

   view.side = side;

   //==================================================================
   //  ANALYST SYNTHESIS — the same structured read an LLM analyst
   //  (GPT / Claude / Gemini) would give: regime, momentum, volatility,
   //  structure, bias + confidence, narrative and risk flags.
   //  Purely descriptive: it never sends an order.
   //==================================================================
   view.atrPct  = AtrPercentile(m_atrBuf, 240, view.atr);
   view.rsiSlope = (ArraySize(m_rsiBuf) > 2) ? (m_rsiBuf[0] - m_rsiBuf[2]) : 0.0;
   view.fastSlope = (ArraySize(m_fastBuf) > 3)
                      ? ((m_fastBuf[0] - m_fastBuf[3]) / view.atr) : 0.0;
   view.distResistance = (view.swingHigh > 0.0) ? ((view.swingHigh - view.bid) / view.atr) : -1.0;
   view.distSupport    = (view.swingLow  > 0.0) ? ((view.bid - view.swingLow)  / view.atr) : -1.0;

   //--- regime
   if(side > 0)      view.trendState = "uptrend";
   else if(side < 0) view.trendState = "downtrend";
   else              view.trendState = "range";

   //--- momentum (level + direction of the RSI line)
   string rsiDir = (view.rsiSlope >  0.5) ? "rising"
                 : (view.rsiSlope < -0.5) ? "falling" : "flat";
   if(view.rsi >= 60.0)      view.momentumState = "strong up (" + rsiDir + ")";
   else if(view.rsi >= 50.0) view.momentumState = "mild up (" + rsiDir + ")";
   else if(view.rsi >  40.0) view.momentumState = "mild down (" + rsiDir + ")";
   else                      view.momentumState = "strong down (" + rsiDir + ")";

   //--- volatility regime from the ATR percentile
   if(view.atrPct < 0.33)      view.volState = "low";
   else if(view.atrPct < 0.66) view.volState = "normal";
   else if(view.atrPct < 0.85) view.volState = "elevated";
   else                        view.volState = "extreme";

   //--- structure: proximity to the most recent fractal levels
   if(view.distResistance >= 0.0 && view.distResistance < 0.5)
      view.structureState = "at resistance";
   else if(view.distSupport >= 0.0 && view.distSupport < 0.5)
      view.structureState = "at support";
   else
      view.structureState = "mid-range";

   //--- bias + confidence (transparent additive score, clamped)
   double conf = 0.50;
   if(side != 0) conf += 0.15;                                   // aligned regime present
   if((side > 0 && view.fastMa > view.midMa) ||
      (side < 0 && view.fastMa < view.midMa)) conf += 0.10;      // fast/mid agree
   if((side > 0 && view.rsi > 50.0) ||
      (side < 0 && view.rsi < 50.0)) conf += 0.10;               // momentum agrees
   if(view.inRetestBand) conf -= 0.05;                           // decision point
   if(view.atrPct >= 0.85) conf -= 0.15;                         // extreme vol
   if(side == 0) conf -= 0.20;                                   // no regime
   view.confidence = MathMax(0.05, MathMin(0.95, conf));

   if(side > 0)      view.biasText = "LONG";
   else if(side < 0) view.biasText = "SHORT";
   else              view.biasText = "NEUTRAL";

   //--- risk flags
   string rk = "";
   if(view.atrPct >= 0.85) rk += "extreme volatility; ";
   if(view.inRetestBand)   rk += "price inside MA band; ";
   if(side == 0)           rk += "no aligned trend; ";
   if(side < 0 && view.distResistance >= 0.0 && view.distResistance < 0.3)
      rk += "short into nearby resistance; ";
   if(side > 0 && view.distSupport >= 0.0 && view.distSupport < 0.3)
      rk += "long into nearby support; ";
   view.risks = (rk == "") ? "none flagged" : rk;

   //--- narrative
   view.narrative = StringFormat(
      "%s. RSI %.1f %s. Vol %s (ATR p%.0f). Structure %s. Bias %s %.0f%%.",
      view.trendState, view.rsi, rsiDir, view.volState, view.atrPct * 100.0,
      view.structureState, view.biasText, view.confidence * 100.0);

   if(side == 0)
   {
      view.note = "no directional regime (price vs slow MA / MA alignment)";
      return;
   }

   double stopDist = view.atr * m_stopAtr;
   double tpDist   = view.atr * m_tpAtr;
   double stepDist = view.atr * m_scaleStepAtr;

   if(side < 0)   //--- short plan
   {
      view.entry = view.bid;
      view.stop  = view.entry + stopDist;
      view.tp    = view.entry - tpDist;
      //--- stop zone: from the entry stop up to the swing high / ATR ceiling
      double ceiling = (view.swingHigh > view.stop) ? view.swingHigh : (view.stop + view.atr * 0.25);
      view.zoneTop = MathMax(ceiling, view.stop);
      view.zoneBot = view.stop;
      //--- scale-ins are placed lower (deeper profit) by an ATR step each
      view.scale2 = view.entry - stepDist;
      view.scale3 = view.scale2 - stepDist;
   }
   else           //--- long plan
   {
      view.entry = view.ask;
      view.stop  = view.entry - stopDist;
      view.tp    = view.entry + tpDist;
      double floorPx = (view.swingLow > 0.0 && view.swingLow < view.stop)
                         ? view.swingLow : (view.stop - view.atr * 0.25);
      view.zoneBot = MathMin(floorPx, view.stop);
      view.zoneTop = view.stop;
      view.scale2 = view.entry + stepDist;
      view.scale3 = view.scale2 + stepDist;
   }
}

//+------------------------------------------------------------------+
//| SlowPath — poly-line of the slow MA over the last bars           |
//+------------------------------------------------------------------+
int CSovereignDynamicPlan::SlowPath(datetime &times[], double &prices[], const int maxPoints)
{
   if(!m_ready || ArraySize(m_slowBuf) < 2)
      return 0;
   int n = MathMin(maxPoints, ArraySize(m_slowBuf) - 1);
   if(n < 2) return 0;

   ArrayResize(times, n);
   ArrayResize(prices, n);
   int out = 0;
   //--- oldest -> newest so the trendline reads left to right
   for(int i = n - 1; i >= 1; i--)
   {
      datetime t = iTime(m_symbol, m_tf, i);
      if(t <= 0 || m_slowBuf[i] <= 0.0)
         continue;
      times[out]  = t;
      prices[out] = m_slowBuf[i];
      out++;
   }
   ArrayResize(times, out);
   ArrayResize(prices, out);
   return out;
}

//+------------------------------------------------------------------+
//| BandPath — poly-line of the fast/mid MA band over the last bars  |
//+------------------------------------------------------------------+
int CSovereignDynamicPlan::BandPath(datetime &times[], double &fast[], double &mid[], const int maxPoints)
{
   if(!m_ready || ArraySize(m_fastBuf) < 2 || ArraySize(m_midBuf) < 2)
      return 0;
   int n = MathMin(maxPoints, MathMin(ArraySize(m_fastBuf), ArraySize(m_midBuf)) - 1);
   if(n < 2) return 0;

   ArrayResize(times, n);
   ArrayResize(fast, n);
   ArrayResize(mid, n);
   int out = 0;
   for(int i = n - 1; i >= 1; i--)
   {
      datetime t = iTime(m_symbol, m_tf, i);
      if(t <= 0 || m_fastBuf[i] <= 0.0 || m_midBuf[i] <= 0.0)
         continue;
      times[out] = t;
      fast[out]  = m_fastBuf[i];
      mid[out]   = m_midBuf[i];
      out++;
   }
   ArrayResize(times, out);
   ArrayResize(fast, out);
   ArrayResize(mid, out);
   return out;
}

//+------------------------------------------------------------------+
//| FullPath — slow + fast + mid MA aligned in one pass (markup)     |
//+------------------------------------------------------------------+
int CSovereignDynamicPlan::FullPath(datetime &times[], double &slow[],
                                    double &fast[], double &mid[], const int maxPoints)
{
   if(!m_ready)
      return 0;
   int avail = MathMin(ArraySize(m_slowBuf), MathMin(ArraySize(m_fastBuf), ArraySize(m_midBuf)));
   if(avail < 3)
      return 0;
   int n = MathMin(maxPoints, avail - 1);
   if(n < 2)
      return 0;

   ArrayResize(times, n);
   ArrayResize(slow, n);
   ArrayResize(fast, n);
   ArrayResize(mid, n);

   int out = 0;
   for(int i = n - 1; i >= 1; i--)     // oldest -> newest
   {
      datetime t = iTime(m_symbol, m_tf, i);
      if(t <= 0 || m_slowBuf[i] <= 0.0 || m_fastBuf[i] <= 0.0 || m_midBuf[i] <= 0.0)
         continue;
      times[out] = t;
      slow[out]  = m_slowBuf[i];
      fast[out]  = m_fastBuf[i];
      mid[out]   = m_midBuf[i];
      out++;
   }
   ArrayResize(times, out);
   ArrayResize(slow, out);
   ArrayResize(fast, out);
   ArrayResize(mid, out);
   return out;
}

#endif // __SOVEREIGN_DYNAMIC_PLAN_MQH__
