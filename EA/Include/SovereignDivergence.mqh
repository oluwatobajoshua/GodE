//+------------------------------------------------------------------+
//|                                   SovereignDivergence.mqh         |
//|  RSI divergence detector + chart renderer.                        |
//|                                                                  |
//|  Regular (classic) divergence only:                               |
//|    BULLISH : price makes a LOWER LOW  while RSI makes a HIGHER low |
//|    BEARISH : price makes a HIGHER HIGH while RSI makes a LOWER high|
//|                                                                  |
//|  Design rules:                                                    |
//|   * CAUSAL. A swing pivot is only usable once `pivot` bars have    |
//|     closed after it, so nothing on the chart could not have been   |
//|     known at the time. No look-ahead.                             |
//|   * DISPLAY ONLY. This module never sizes, vetoes or triggers a    |
//|     trade. RSI divergence was tested as a filter in                |
//|     research/ema21_fakeout.py and did not produce an edge, so it   |
//|     must not gate entries.                                        |
//|   * Detection is identical in spirit to                            |
//|     research/ema_turn_precursor.py (swing low/high, price extreme   |
//|     vs RSI non-confirmation with a minimum RSI delta).             |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_DIVERGENCE_MQH__
#define __SOVEREIGN_DIVERGENCE_MQH__

#include <Sovereign\SovereignChartVisualizer.mqh>

//--- one swing point of the scan
struct SovPivotPoint
{
   int      shift;        // series shift (0 = forming bar, 1 = last closed)
   datetime time;
   double   price;        // low[] for a low pivot, high[] for a high pivot
   double   rsi;
   double   barHigh;
   double   barLow;
};

//--- one confirmed divergence
struct SovDivergence
{
   int      kind;         // +1 bullish, -1 bearish
   int      olderShift;
   int      newerShift;
   datetime olderTime;
   datetime newerTime;
   double   olderPrice;
   double   newerPrice;
   double   olderRsi;
   double   newerRsi;
   double   newerBarHigh;
   double   newerBarLow;
};

class CSovereignDivergence
{
private:
   int      m_rsiHandle;
   int      m_rsiPeriod;
   int      m_pivot;        // bars each side of a swing pivot
   int      m_minBars;      // min bars between the two pivots
   int      m_maxBars;      // max bars between the two pivots
   int      m_lookback;     // bars scanned on each new bar
   int      m_maxDraw;      // most recent divergences kept / drawn
   double   m_minRsiDelta;  // required RSI improvement at the new extreme
   double   m_bullMaxRsi;   // bullish: RSI at the new low must be <= this
   double   m_bearMinRsi;   // bearish: RSI at the new high must be >= this

   SovDivergence m_div[];

   datetime m_lastScanBar;      // bar time of the last scan
   bool     m_scanned;
   bool     m_primed;           // first scan just adopts the current state
   datetime m_lastSeenTime;     // newest divergence time already reported
   bool     m_newConfirmed;     // a fresh divergence appeared on the last scan

   //--- helpers -----------------------------------------------------
   bool IsPivotLow(const double &low[], const int s) const
   {
      for(int j = 1; j <= m_pivot; j++)
      {
         if(low[s] > low[s + j])   return false;   // an older bar is lower
         if(low[s] >= low[s - j])  return false;   // a newer bar is at or below
      }
      return true;
   }

   bool IsPivotHigh(const double &high[], const int s) const
   {
      for(int j = 1; j <= m_pivot; j++)
      {
         if(high[s] < high[s + j])  return false;  // an older bar is higher
         if(high[s] <= high[s - j]) return false;  // a newer bar is at or above
      }
      return true;
   }

   void AddDiv(const int kind, const SovPivotPoint &older, const SovPivotPoint &newer)
   {
      int n = ArraySize(m_div);
      ArrayResize(m_div, n + 1, 32);
      m_div[n].kind         = kind;
      m_div[n].olderShift   = older.shift;
      m_div[n].newerShift   = newer.shift;
      m_div[n].olderTime    = older.time;
      m_div[n].newerTime    = newer.time;
      m_div[n].olderPrice   = older.price;
      m_div[n].newerPrice   = newer.price;
      m_div[n].olderRsi     = older.rsi;
      m_div[n].newerRsi     = newer.rsi;
      m_div[n].newerBarHigh = newer.barHigh;
      m_div[n].newerBarLow  = newer.barLow;
   }

   void TrimToMaxDraw()
   {
      int n = ArraySize(m_div);
      if(m_maxDraw <= 0 || n <= m_maxDraw)
         return;
      int drop = n - m_maxDraw;
      for(int i = 0; i < m_maxDraw; i++)
         m_div[i] = m_div[drop + i];
      ArrayResize(m_div, m_maxDraw);
   }

   string PxText(const double px) const
   {
      double a = MathAbs(px);
      int d = (a >= 1000.0 ? 2 : (a >= 10.0 ? 3 : 5));
      return DoubleToString(px, d);
   }

public:
   CSovereignDivergence()
   {
      m_rsiHandle     = INVALID_HANDLE;
      m_rsiPeriod     = 14;
      m_pivot         = 3;
      m_minBars       = 5;
      m_maxBars       = 60;
      m_lookback      = 500;
      m_maxDraw       = 12;
      m_minRsiDelta   = 1.0;
      m_bullMaxRsi    = 50.0;
      m_bearMinRsi    = 50.0;
      m_lastScanBar   = 0;
      m_scanned       = false;
      m_lastSeenTime  = 0;
      m_newConfirmed  = false;
      m_primed        = false;
   }

   void Configure(const int rsiHandle, const int rsiPeriod, const int pivot,
                  const int minBars, const int maxBars,
                  const double minRsiDelta, const double bullMaxRsi,
                  const double bearMinRsi, const int lookback, const int maxDraw)
   {
      m_rsiHandle   = rsiHandle;
      m_rsiPeriod   = (rsiPeriod > 0 ? rsiPeriod : 14);
      m_pivot       = (pivot > 0 ? pivot : 3);
      m_minBars     = (minBars > 0 ? minBars : 5);
      m_maxBars     = (maxBars > m_minBars ? maxBars : m_minBars + 1);
      m_lookback    = (lookback > m_maxBars ? lookback : m_maxBars + 20);
      m_maxDraw     = (maxDraw > 0 ? maxDraw : 12);
      m_minRsiDelta = minRsiDelta;
      m_bullMaxRsi  = bullMaxRsi;
      m_bearMinRsi  = bearMinRsi;
      m_scanned     = false;
      m_lastScanBar = 0;
      m_primed      = false;
      m_lastSeenTime = 0;
      ArrayResize(m_div, 0);
   }

   void Clear()
   {
      ArrayResize(m_div, 0);
      m_lastScanBar  = 0;
      m_scanned      = false;
      m_primed       = false;
      m_lastSeenTime = 0;
      m_newConfirmed = false;
   }

   //--- scan once per bar. Returns true when a scan actually ran.
   bool Scan(const string sym, const ENUM_TIMEFRAMES tf, const bool logNew)
   {
      m_newConfirmed = false;
      datetime barTime = iTime(sym, tf, 0);
      if(barTime <= 0)
         return false;
      if(m_scanned && barTime == m_lastScanBar)
         return false;
      m_lastScanBar = barTime;
      m_scanned     = true;

      ArrayResize(m_div, 0);
      if(m_rsiHandle == INVALID_HANDLE)
         return false;

      int total = Bars(sym, tf);
      int need  = m_lookback + m_pivot + 2;
      if(need > total)
         need = total;
      if(need < m_pivot * 2 + m_minBars + 2)
         return false;

      double   low[], high[], rsi[];
      datetime tm[];
      ArraySetAsSeries(low,  true);
      ArraySetAsSeries(high, true);
      ArraySetAsSeries(rsi,  true);
      ArraySetAsSeries(tm,   true);

      if(CopyLow(sym, tf, 0, need, low) <= 0)          return false;
      if(CopyHigh(sym, tf, 0, need, high) <= 0)        return false;
      if(CopyTime(sym, tf, 0, need, tm) <= 0)          return false;
      if(CopyBuffer(m_rsiHandle, 0, 0, need, rsi) <= 0) return false;

      int n = ArraySize(low);
      if(ArraySize(high) < n) n = ArraySize(high);
      if(ArraySize(rsi) < n)  n = ArraySize(rsi);
      if(ArraySize(tm) < n)   n = ArraySize(tm);

      //--- oldest shift we may look at: pivot must own `m_pivot` older bars
      int oldest = n - m_pivot - 1;
      if(oldest > m_lookback)
         oldest = m_lookback;
      if(oldest < m_pivot + 1)
         return false;

      SovPivotPoint lows[], highs[];
      int nLow = 0, nHigh = 0;

      //--- walk oldest -> newest so pairs come out in chronological order
      for(int s = oldest; s >= m_pivot + 1; s--)
      {
         if(IsPivotLow(low, s))
         {
            ArrayResize(lows, nLow + 1, 64);
            lows[nLow].shift   = s;
            lows[nLow].time    = tm[s];
            lows[nLow].price   = low[s];
            lows[nLow].rsi     = rsi[s];
            lows[nLow].barHigh = high[s];
            lows[nLow].barLow  = low[s];
            nLow++;
         }
         if(IsPivotHigh(high, s))
         {
            ArrayResize(highs, nHigh + 1, 64);
            highs[nHigh].shift   = s;
            highs[nHigh].time    = tm[s];
            highs[nHigh].price   = high[s];
            highs[nHigh].rsi     = rsi[s];
            highs[nHigh].barHigh = high[s];
            highs[nHigh].barLow  = low[s];
            nHigh++;
         }
      }

      //--- pair consecutive swings: bullish divergence on the lows
      for(int i = 1; i < nLow; i++)
      {
         SovPivotPoint a = lows[i - 1];   // older pivot
         SovPivotPoint b = lows[i];       // newer pivot
         int gap = a.shift - b.shift;
         if(gap < m_minBars || gap > m_maxBars) continue;
         if(a.rsi <= 0.0 || b.rsi <= 0.0)       continue;
         if(b.price < a.price &&
            b.rsi > a.rsi + m_minRsiDelta &&
            b.rsi <= m_bullMaxRsi)
            AddDiv(+1, a, b);
      }

      //--- bearish divergence on the highs
      for(int i = 1; i < nHigh; i++)
      {
         SovPivotPoint a = highs[i - 1];
         SovPivotPoint b = highs[i];
         int gap = a.shift - b.shift;
         if(gap < m_minBars || gap > m_maxBars) continue;
         if(a.rsi <= 0.0 || b.rsi <= 0.0)       continue;
         if(b.price > a.price &&
            b.rsi < a.rsi - m_minRsiDelta &&
            b.rsi >= m_bearMinRsi)
            AddDiv(-1, a, b);
      }

      TrimToMaxDraw();

      //--- report only the moment a divergence first confirms
      int cnt = ArraySize(m_div);
      if(cnt > 0)
      {
         SovDivergence last = m_div[cnt - 1];
         if(!m_primed)
         {
            //--- first scan after attach: adopt the state, do not shout about it
            m_primed       = true;
            m_lastSeenTime = last.newerTime;
         }
         else if(last.newerTime > m_lastSeenTime)
         {
            m_lastSeenTime = last.newerTime;
            m_newConfirmed = true;
            if(logNew)
               PrintFormat("Sovereign DIVERGENCE %s %s: %s  pivots %s -> %s (%d bars)  RSI %.1f -> %.1f  price %s -> %s  (display only)",
                           sym, EnumToString(tf),
                           (last.kind > 0 ? "BULLISH" : "BEARISH"),
                           TimeToString(last.olderTime, TIME_DATE | TIME_MINUTES),
                           TimeToString(last.newerTime, TIME_DATE | TIME_MINUTES),
                           last.olderShift - last.newerShift,
                           last.olderRsi, last.newerRsi,
                           PxText(last.olderPrice), PxText(last.newerPrice));
         }
      }
      else
      {
         m_primed = true;
      }
      return true;
   }

   //--- accessors (panel / journal) -----------------------------------
   int    Count()        const { return ArraySize(m_div); }
   bool   Has()          const { return ArraySize(m_div) > 0; }
   bool   NewConfirmed() const { return m_newConfirmed; }
   int    RsiPeriod()    const { return m_rsiPeriod; }
   int    LastSeenShift() const
   {
      int n = ArraySize(m_div);
      return (n > 0 ? m_div[n - 1].newerShift : -1);
   }
   int LastKind() const
   {
      int n = ArraySize(m_div);
      return (n > 0 ? m_div[n - 1].kind : 0);
   }
   double LastRsiDelta() const
   {
      int n = ArraySize(m_div);
      return (n > 0 ? m_div[n - 1].newerRsi - m_div[n - 1].olderRsi : 0.0);
   }

   string Describe() const
   {
      int n = ArraySize(m_div);
      if(n <= 0)
         return StringFormat("none (RSI%d, %d-bar lookback)", m_rsiPeriod, m_lookback);
      SovDivergence d = m_div[n - 1];
      return StringFormat("last %s %d bars ago  RSI %.1f->%.1f (%+.1f)  %d found",
                          (d.kind > 0 ? "BULL" : "BEAR"), d.newerShift,
                          d.olderRsi, d.newerRsi, d.newerRsi - d.olderRsi, n);
   }

   //--- draw every kept divergence (line + arrow + label) --------------
   void Draw(CSovereignChartVisualizer &paint, const color cBull, const color cBear)
   {
      paint.DeleteWithPrefix("DIV_");
      int n = ArraySize(m_div);
      for(int i = 0; i < n; i++)
      {
         SovDivergence d = m_div[i];
         string tag = IntegerToString((long)d.olderTime) + "_" + IntegerToString((long)d.newerTime);
         color  c   = (d.kind > 0 ? cBull : cBear);
         bool   bull = (d.kind > 0);

         paint.DrawTrend("DIV_L_" + tag, d.olderTime, d.olderPrice,
                         d.newerTime, d.newerPrice, c, STYLE_SOLID, 2, false);

         double range = d.newerBarHigh - d.newerBarLow;
         if(range <= 0.0)
            range = d.newerPrice * 0.0005;
         if(range <= 0.0)
            range = 0.0001;
         double off = range * 0.9;
         double lvl = (bull ? d.newerBarLow - off : d.newerBarHigh + off);

         string lbl = StringFormat("%s DIV  RSI %.0f>%.0f",
                                   (bull ? "BULL" : "BEAR"), d.olderRsi, d.newerRsi);
         if(bull)
            paint.DrawText("DIV_T_" + tag, d.newerTime, lvl, lbl, c, ANCHOR_UPPER, 8);
         else
            paint.DrawText("DIV_T_" + tag, d.newerTime, lvl, lbl, c, ANCHOR_LOWER, 8);

         paint.DrawArrow("DIV_A_" + tag, d.newerTime,
                         (bull ? d.newerBarLow : d.newerBarHigh),
                         (bull ? SOV_ARROW_UP : SOV_ARROW_DOWN), c, lbl);
      }
   }
};

#endif // __SOVEREIGN_DIVERGENCE_MQH__
