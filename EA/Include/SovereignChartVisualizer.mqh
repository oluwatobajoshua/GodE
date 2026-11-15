//+------------------------------------------------------------------+
//|                             SovereignChartVisualizer.mqh          |
//|  Renders EA state on the chart (visual mode / tester only).       |
//|                                                                  |
//|  Design rule: every object drawn here must come from a value the  |
//|  EA actually used (engine box, engine SL/TP, real position, the   |
//|  loaded chart template). Never invent a level.                    |
//|                                                                  |
//|  All objects carry a prefix and are removed in OnDeinit.          |
//|  Object creation is a no-op in non-visual tester runs.            |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_CHART_VISUALIZER_MQH__
#define __SOVEREIGN_CHART_VISUALIZER_MQH__

#include <Sovereign\SovereignError.mqh>
#include <Sovereign\SovereignDynamicPlan.mqh>

//--- Wingdings arrow codes used by MT5 charts
#define SOV_ARROW_UP    233   // green up arrow
#define SOV_ARROW_DOWN  234   // red down arrow

class CSovereignChartVisualizer
{
private:
   string   m_prefix;
   bool     m_enabled;
   int      m_maxArrows;      // ring buffer so a long tester run cannot leak objects
   string   m_arrowRing[];
   int      m_arrowHead;
   int      m_arrowCount;
   uint     m_lastRedrawMs;
   int      m_redrawMinMs;
   bool     m_dirty;

   //--- single-slot caches: recompute only on a new bar
   string   m_boxKey;
   datetime m_boxBar;
   string   m_fracKey;
   datetime m_fracBar;

   //--- panel cache
   string   m_panelId;
   int      m_panelLines;
   string   m_panelCache[];

   //--- helpers -----------------------------------------------------
   string Key(const string name) const
   {
      return m_prefix + name;
   }

    bool Ensure(const string name, const ENUM_OBJECT type)
   {
      string obj = Key(name);
      if(ObjectFind(0, obj) >= 0)
      {
         if(ObjectGetInteger(0, obj, OBJPROP_TYPE) == (long)type)
            return true;
         ObjectDelete(0, obj);
      }
      SovResetLastError();
      if(!ObjectCreate(0, obj, type, 0, 0, 0))
      {
         SovLogWarn("ChartVisualizer: ObjectCreate(" + obj + ") failed err=" + IntegerToString(GetLastError()));
         return false;
      }
      return true;
   }

   void Common(const string name)
   {
      string obj = Key(name);
      ObjectSetInteger(0, obj, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, obj, OBJPROP_SELECTED, false);
      ObjectSetInteger(0, obj, OBJPROP_HIDDEN, false);
   }

   void RegisterArrow(const string name)
   {
      if(m_maxArrows <= 0)
         return;
      if(m_arrowCount < m_maxArrows)
      {
         ArrayResize(m_arrowRing, m_arrowCount + 1, 64);
         m_arrowRing[m_arrowCount] = name;
         m_arrowCount++;
         return;
      }
      //--- ring full: drop the oldest arrow
      ObjectDelete(0, Key(m_arrowRing[m_arrowHead]));
      m_arrowRing[m_arrowHead] = name;
      m_arrowHead++;
      if(m_arrowHead >= m_maxArrows)
         m_arrowHead = 0;
   }

public:
   CSovereignChartVisualizer()
   {
      m_prefix        = "SOV_VIS_";
      m_enabled       = false;
      m_maxArrows     = 300;
      m_arrowHead     = 0;
      m_arrowCount    = 0;
      m_lastRedrawMs  = 0;
      m_redrawMinMs   = 200;
      m_dirty         = false;
      m_boxKey        = "";
      m_boxBar        = 0;
      m_fracKey       = "";
      m_fracBar       = 0;
      m_panelId       = "";
      m_panelLines    = 0;
   }

   ~CSovereignChartVisualizer()
   {
      ClearAll();
   }

   //--- lifecycle ---------------------------------------------------
   void Init(const bool enabled, const string prefix = "SOV_VIS_", const int maxArrows = 300)
   {
      m_enabled    = enabled;
      m_prefix     = (prefix == "") ? "SOV_VIS_" : prefix;
      m_maxArrows  = MathMax(0, maxArrows);
      m_dirty      = false;
      m_boxKey     = "";
      m_boxBar     = 0;
      m_fracKey    = "";
      m_fracBar    = 0;
      m_panelId    = "";
      m_panelLines = 0;
      if(!m_enabled)
         ClearAll();
   }

   bool   Enabled() const { return m_enabled; }
   string Prefix()  const { return m_prefix; }

   void ClearAll()
   {
      ObjectsDeleteAll(0, m_prefix);
      ArrayResize(m_arrowRing, 0);
      ArrayResize(m_panelCache, 0);
      m_arrowHead  = 0;
      m_arrowCount = 0;
      m_boxKey     = "";
      m_boxBar     = 0;
      m_fracKey    = "";
      m_fracBar    = 0;
      m_panelId    = "";
      m_panelLines = 0;
      m_dirty      = false;
      ChartRedraw(0);
   }

   void Delete(const string name)
   {
      string obj = Key(name);
      if(ObjectFind(0, obj) >= 0)
      {
         ObjectDelete(0, obj);
         m_dirty = true;
      }
   }

   void DeleteWithPrefix(const string sub)
   {
      ObjectsDeleteAll(0, m_prefix + sub);
      m_dirty = true;
   }

   //--- redraw throttle (never call ChartRedraw every tick) ----------
   void Flush(const bool force = false)
   {
      if(!m_enabled || !m_dirty)
         return;
      uint now = GetTickCount();
      if(!force && (uint)(now - m_lastRedrawMs) < (uint)m_redrawMinMs)
         return;
      ChartRedraw(0);
      m_lastRedrawMs = now;
      m_dirty        = false;
   }

   //--- primitives ---------------------------------------------------
   bool DrawBox(const string name, const datetime t1, const double p1,
                const datetime t2, const double p2, const color clr)
   {
      if(!m_enabled || t1 <= 0 || t2 <= 0 || p1 <= 0.0 || p2 <= 0.0)
         return false;
      if(!Ensure(name, OBJ_RECTANGLE))
         return false;
      string obj = Key(name);
      ObjectSetInteger(0, obj, OBJPROP_TIME, 0, (long)t1);
      ObjectSetDouble(0, obj, OBJPROP_PRICE, 0, p1);
      ObjectSetInteger(0, obj, OBJPROP_TIME, 1, (long)t2);
      ObjectSetDouble(0, obj, OBJPROP_PRICE, 1, p2);
      ObjectSetInteger(0, obj, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, obj, OBJPROP_STYLE, STYLE_SOLID);
      ObjectSetInteger(0, obj, OBJPROP_WIDTH, 1);
      ObjectSetInteger(0, obj, OBJPROP_FILL, true);
      ObjectSetInteger(0, obj, OBJPROP_BACK, false);   // behind candles
      ObjectSetInteger(0, obj, OBJPROP_ZORDER, 0);
      Common(name);
      m_dirty = true;
      return true;
   }

   bool DrawLevel(const string name, const double price, const color clr,
                  const ENUM_LINE_STYLE style, const int width,
                  const string tooltip = "")
   {
      if(!m_enabled)
         return false;
      string obj = Key(name);
      if(price <= 0.0)
      {
         if(ObjectFind(0, obj) >= 0)
         {
            ObjectDelete(0, obj);
            m_dirty = true;
         }
         return false;
      }
      if(!Ensure(name, OBJ_HLINE))
         return false;
      ObjectMove(0, obj, 0, 0, price);
      ObjectSetInteger(0, obj, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, obj, OBJPROP_STYLE, style);
      ObjectSetInteger(0, obj, OBJPROP_WIDTH, width);
      ObjectSetInteger(0, obj, OBJPROP_BACK, false);
      ObjectSetString(0, obj, OBJPROP_TOOLTIP, tooltip);
      Common(name);
      m_dirty = true;
      return true;
   }

   bool DrawArrow(const string name, const datetime when, const double price,
                  const int code, const color clr, const string tooltip = "")
   {
      if(!m_enabled || when <= 0 || price <= 0.0)
         return false;
      string obj  = Key(name);
      bool   isNew = (ObjectFind(0, obj) < 0);
      if(!Ensure(name, OBJ_ARROW))
         return false;
      ObjectMove(0, obj, 0, when, price);
      ObjectSetInteger(0, obj, OBJPROP_ARROWCODE, code);
      ObjectSetInteger(0, obj, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, obj, OBJPROP_WIDTH, 1);
      //--- up arrow sits below the bar, down arrow sits above it
      ObjectSetInteger(0, obj, OBJPROP_ANCHOR,
                       (code == SOV_ARROW_DOWN) ? ANCHOR_BOTTOM : ANCHOR_TOP);
      ObjectSetInteger(0, obj, OBJPROP_BACK, false);
      ObjectSetString(0, obj, OBJPROP_TOOLTIP, tooltip);
      Common(name);
      if(isNew)
         RegisterArrow(name);
      m_dirty = true;
      return true;
   }

   bool DrawText(const string name, const datetime when, const double price,
                 const string text, const color clr,
                 const ENUM_ANCHOR_POINT anchor = ANCHOR_LEFT,
                 const int fontsize = 8)
   {
      if(!m_enabled || when <= 0 || price <= 0.0)
         return false;
      if(!Ensure(name, OBJ_TEXT))
         return false;
      string obj = Key(name);
      ObjectMove(0, obj, 0, when, price);
      ObjectSetString(0, obj, OBJPROP_TEXT, text);
      ObjectSetString(0, obj, OBJPROP_FONT, "Consolas");
      ObjectSetInteger(0, obj, OBJPROP_FONTSIZE, fontsize);
      ObjectSetInteger(0, obj, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, obj, OBJPROP_ANCHOR, (long)anchor);
      ObjectSetInteger(0, obj, OBJPROP_BACK, false);
      Common(name);
      m_dirty = true;
      return true;
   }

   bool DrawTrend(const string name, const datetime t1, const double p1,
                  const datetime t2, const double p2, const color clr,
                  const ENUM_LINE_STYLE style, const int width,
                  const bool rayRight = false)
   {
      if(!m_enabled || t1 <= 0 || t2 <= 0 || p1 <= 0.0 || p2 <= 0.0)
         return false;
      if(!Ensure(name, OBJ_TREND))
         return false;
      string obj = Key(name);
      ObjectMove(0, obj, 0, t1, p1);
      ObjectMove(0, obj, 1, t2, p2);
      ObjectSetInteger(0, obj, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, obj, OBJPROP_STYLE, style);
      ObjectSetInteger(0, obj, OBJPROP_WIDTH, width);
      ObjectSetInteger(0, obj, OBJPROP_RAY_RIGHT, rayRight);
      ObjectSetInteger(0, obj, OBJPROP_BACK, false);
      Common(name);
      m_dirty = true;
      return true;
   }

   //--- on-chart panel ------------------------------------------------
   bool DrawPanel(const string id, const string &lines[], const color &colors[],
                  const int x, const int y, const int lineHeight,
                  const int fontSize, const color bg, const color border)
   {
      if(!m_enabled)
         return false;
      int n  = ArraySize(lines);
      int nc = ArraySize(colors);
      if(n <= 0)
         return false;

      //--- cap panel size so it can never cover the whole chart
      const int MAX_PANEL_LINES = 24;
      const int MAX_PANEL_WIDTH  = 420;
      const int MAX_PANEL_HEIGHT = 320;
      if(n > MAX_PANEL_LINES) n = MAX_PANEL_LINES;

      if(id != m_panelId)
      {
         m_panelId = id;
         m_panelLines = 0;
         ArrayResize(m_panelCache, 0);
      }

      int maxLen = 12;
      for(int i = 0; i < n; i++)
         maxLen = MathMax(maxLen, StringLen(lines[i]));
      int width  = (int)(maxLen * fontSize * 0.62) + 18;
      int height = n * lineHeight + 12;
      if(width > MAX_PANEL_WIDTH)  width = MAX_PANEL_WIDTH;
      if(height > MAX_PANEL_HEIGHT) height = MAX_PANEL_HEIGHT;

      string bgName = id + "_bg";
      if(Ensure(bgName, OBJ_RECTANGLE_LABEL))
      {
         string obj = Key(bgName);
         ObjectSetInteger(0, obj, OBJPROP_CORNER, CORNER_LEFT_UPPER);
         ObjectSetInteger(0, obj, OBJPROP_XDISTANCE, x);
         ObjectSetInteger(0, obj, OBJPROP_YDISTANCE, y);
         ObjectSetInteger(0, obj, OBJPROP_XSIZE, width);
         ObjectSetInteger(0, obj, OBJPROP_YSIZE, height);
         ObjectSetInteger(0, obj, OBJPROP_BGCOLOR, bg);
         ObjectSetInteger(0, obj, OBJPROP_COLOR, border);
         ObjectSetInteger(0, obj, OBJPROP_BORDER_TYPE, BORDER_FLAT);
         ObjectSetInteger(0, obj, OBJPROP_BACK, false);
         ObjectSetInteger(0, obj, OBJPROP_ZORDER, 0);
         Common(bgName);
      }

      if(ArraySize(m_panelCache) < n)
         ArrayResize(m_panelCache, n);

      for(int i = 0; i < n; i++)
      {
         string ln = id + "_" + IntegerToString(i);
         if(!Ensure(ln, OBJ_LABEL))
            continue;
         string obj = Key(ln);
         ObjectSetInteger(0, obj, OBJPROP_CORNER, CORNER_LEFT_UPPER);
         ObjectSetInteger(0, obj, OBJPROP_XDISTANCE, x + 8);
         ObjectSetInteger(0, obj, OBJPROP_YDISTANCE, y + 6 + i * lineHeight);
         ObjectSetString(0, obj, OBJPROP_FONT, "Consolas");
         ObjectSetInteger(0, obj, OBJPROP_FONTSIZE, fontSize);
         ObjectSetInteger(0, obj, OBJPROP_COLOR, colors[MathMin(i, nc - 1)]);
         ObjectSetInteger(0, obj, OBJPROP_BACK, false);
         ObjectSetInteger(0, obj, OBJPROP_ZORDER, 1);
         Common(ln);
         if(m_panelCache[i] != lines[i])
         {
            ObjectSetString(0, obj, OBJPROP_TEXT, lines[i]);
            m_panelCache[i] = lines[i];
         }
      }
      //--- remove labels left over from a longer previous panel
      for(int i = n; i < m_panelLines; i++)
         ObjectDelete(0, Key(id + "_" + IntegerToString(i)));
      m_panelLines = n;
      m_dirty = true;
      return true;
   }

   //--- session box built from real bars -----------------------------
   //  h0/h1 are SERVER hours and inclusive (h0=5,h1=6 covers hours 5 and 6),
   //  which matches CSovereignLondonOrb::BuildBox().
   bool DrawSessionBoxFromBars(const string name, const string sym,
                               const ENUM_TIMEFRAMES tf, const int dayShift,
                               const int h0, const int h1, const color clr,
                               const bool force = false)
   {
      if(!m_enabled)
         return false;
      if(tf >= PERIOD_D1)          // no intraday session on D1 or higher
         return false;

      datetime dayStart = DayStartOf(TimeCurrent(), dayShift);
      datetime dayEnd   = dayStart + 86400;
      string key = name + "|" + sym + "|" + IntegerToString((int)tf) + "|" +
                   IntegerToString(dayShift) + "|" + IntegerToString(h0) + "|" +
                   IntegerToString(h1) + "|" + IntegerToString((long)dayStart);
      datetime bar0 = iTime(sym, tf, 0);
      if(!force && key == m_boxKey && bar0 == m_boxBar)
         return true;

      MqlRates rates[];
      ArraySetAsSeries(rates, false);
      int n = CopyRates(sym, tf, dayStart, dayEnd - 1, rates);
      if(n <= 0)
         return false;

      datetime t1 = 0, t2 = 0;
      double   hi = -DBL_MAX, lo = DBL_MAX;
      int      got = 0;
      for(int i = 0; i < n; i++)
      {
         MqlDateTime d;
         TimeToStruct(rates[i].time, d);
         if(!InSession(d.hour, h0, h1))
            continue;
         if(rates[i].high > hi) hi = rates[i].high;
         if(rates[i].low  < lo) lo = rates[i].low;
         if(t1 == 0 || rates[i].time < t1) t1 = rates[i].time;
         if(rates[i].time > t2) t2 = rates[i].time;
         got++;
      }
      if(got < 1 || hi <= lo)
         return false;

      t2 += PeriodSeconds(tf);
      if(!DrawBox(name, t1, hi, t2, lo, clr))
         return false;

      m_boxKey = key;
      m_boxBar = bar0;
      return true;
   }

   //--- swing markers (5-bar fractal), structure context only --------
   int DrawFractals(const string sym, const ENUM_TIMEFRAMES tf, const int lookback,
                     const color clrHigh, const color clrLow, const int arrowUp, const int arrowDown)
   {
      if(!m_enabled)
         return 0;
      int bars = MathMax(10, MathMin(lookback, 500));
      datetime bar0 = iTime(sym, tf, 0);
      string key = sym + "|" + IntegerToString((int)tf) + "|" + IntegerToString(bars);
      if(key == m_fracKey && bar0 == m_fracBar)
         return 0;

      int need = bars + 5;
      double hi[], lo[];
      ArraySetAsSeries(hi, true);
      ArraySetAsSeries(lo, true);
      if(CopyHigh(sym, tf, 0, need, hi) < need)
         return 0;
      if(CopyLow(sym, tf, 0, need, lo) < need)
         return 0;

      int drawn = 0;
      for(int i = 2; i < bars + 2; i++)
      {
         datetime t = iTime(sym, tf, i);
         if(t <= 0)
            continue;
         if(IsUpFractal(hi, i))
         {
            if(DrawArrow("FracH_" + IntegerToString((long)t), t, hi[i],
                         arrowDown, clrHigh, "Swing high"))
               drawn++;
         }
         if(IsDownFractal(lo, i))
         {
            if(DrawArrow("FracL_" + IntegerToString((long)t), t, lo[i],
                         arrowUp, clrLow, "Swing low"))
               drawn++;
         }
      }
      m_fracKey = key;
      m_fracBar = bar0;
      return drawn;
   }

   //--- pure helpers -------------------------------------------------
   static int NormHour(const int h)
   {
      int x = h % 24;
      if(x < 0)
         x += 24;
      return x;
   }

   static datetime DayStartOf(const datetime t, const int dayShift)
   {
      MqlDateTime d;
      TimeToStruct(t, d);
      d.hour = 0;
      d.min  = 0;
      d.sec  = 0;
      datetime s = StructToTime(d);
      return (datetime)((long)s - (long)dayShift * 86400);
   }

   static bool InSession(const int hour, const int h0, const int h1)
   {
      int a = NormHour(h0);
      int b = NormHour(h1);
      if(a == b)
         return (hour == a);
      if(a < b)
         return (hour >= a && hour <= b);
      return (hour >= a || hour <= b);
   }

   static bool IsUpFractal(const double &hi[], const int i)
   {
      if(i < 2)
         return false;
      double m = hi[i];
      return (m > hi[i - 1] && m > hi[i - 2] && m > hi[i + 1] && m > hi[i + 2]);
   }

   static bool IsDownFractal(const double &lo[], const int i)
   {
      if(i < 2)
         return false;
      double m = lo[i];
      return (m < lo[i - 1] && m < lo[i - 2] && m < lo[i + 1] && m < lo[i + 2]);
   }

    //--- strategy simulation / trade plan overlay (SOV_SIM_ prefix) ----
    void ClearSimObjects()
    {
       ObjectsDeleteAll(0, "SOV_SIM_");
       m_dirty = true;
    }

    //--- clear all trade-related chart objects (stop/tp/entry/zone/leg/path)
    void ClearTradeObjects()
    {
       ObjectsDeleteAll(0, "SOV_DYN_PLAN_");
       ObjectsDeleteAll(0, "SOV_DYN_PATH_");
       ObjectsDeleteAll(0, "SOV_DYN_LEG_");
       ObjectsDeleteAll(0, "SOV_SIM_");
       m_dirty = true;
       ChartRedraw(0);
    }

   bool DrawStrategySimulationPlan(
      const double initEntry,   // [1. INITIAL ENTRY] e.g. 77,500
      const double scaleIn1,    // [2. SCALE-IN] e.g. 74,800
      const double scaleIn2,    // [3. SCALE-IN] e.g. <70,000
      const double stopZone,    // [INITIAL STOP LOSS] e.g. >81,000
      const double trailStopPx, // trailed stop path current price
      const datetime trailStopT // trailed stop path current bar time
   )
   {
      if(!m_enabled) return false;

      //--- cleanup previous sim objects
      ObjectsDeleteAll(0, "SOV_SIM_");
      m_dirty = true;

      datetime now = TimeCurrent();
      datetime t1 = iTime(_Symbol, _Period, 0);   // current bar time
      if(t1 <= 0) t1 = now;
      datetime t2 = t1 + PeriodSeconds(_Period) * 200;  // extend right ~200 bars

      color clrEntry  = clrAqua;
      color clrScale  = clrGold;
      color clrStop   = clrRed;
      color clrTrail  = clrLime;

      //--- [1. INITIAL ENTRY] horizontal level
      if(initEntry > 0.0)
      {
         string obj = "SOV_SIM_INIT_ENTRY";
         if(ObjectCreate(0, obj, OBJ_HLINE, 0, 0, initEntry))
         {
            ObjectSetInteger(0, obj, OBJPROP_COLOR, clrEntry);
            ObjectSetInteger(0, obj, OBJPROP_STYLE, STYLE_DASH);
            ObjectSetInteger(0, obj, OBJPROP_WIDTH, 2);
            ObjectSetInteger(0, obj, OBJPROP_BACK, false);
            ObjectSetString(0, obj, OBJPROP_TOOLTIP, "[1. INITIAL ENTRY] " + DoubleToString(initEntry, 2));
            ObjectSetInteger(0, obj, OBJPROP_SELECTABLE, false);
            ObjectSetInteger(0, obj, OBJPROP_SELECTED, false);
            ObjectSetInteger(0, obj, OBJPROP_HIDDEN, false);
         }
         string lbl = "SOV_SIM_INIT_ENTRY_LABEL";
         if(ObjectCreate(0, lbl, OBJ_TEXT, 0, t1, initEntry))
         {
            ObjectSetString(0, lbl, OBJPROP_TEXT, "[1. INITIAL ENTRY]");
            ObjectSetString(0, lbl, OBJPROP_FONT, "Consolas");
            ObjectSetInteger(0, lbl, OBJPROP_FONTSIZE, 9);
            ObjectSetInteger(0, lbl, OBJPROP_COLOR, clrEntry);
            ObjectSetInteger(0, lbl, OBJPROP_ANCHOR, (long)ANCHOR_LEFT);
            ObjectSetInteger(0, lbl, OBJPROP_BACK, false);
            ObjectSetInteger(0, lbl, OBJPROP_SELECTABLE, false);
            ObjectSetInteger(0, lbl, OBJPROP_SELECTED, false);
            ObjectSetInteger(0, lbl, OBJPROP_HIDDEN, false);
         }
      }

      //--- [2. SCALE-IN] level 1
      if(scaleIn1 > 0.0)
      {
         string obj = "SOV_SIM_SCALEIN_1";
         if(ObjectCreate(0, obj, OBJ_HLINE, 0, 0, scaleIn1))
         {
            ObjectSetInteger(0, obj, OBJPROP_COLOR, clrScale);
            ObjectSetInteger(0, obj, OBJPROP_STYLE, STYLE_DOT);
            ObjectSetInteger(0, obj, OBJPROP_WIDTH, 1);
            ObjectSetInteger(0, obj, OBJPROP_BACK, false);
            ObjectSetString(0, obj, OBJPROP_TOOLTIP, "[2. SCALE-IN] " + DoubleToString(scaleIn1, 2));
            ObjectSetInteger(0, obj, OBJPROP_SELECTABLE, false);
            ObjectSetInteger(0, obj, OBJPROP_SELECTED, false);
            ObjectSetInteger(0, obj, OBJPROP_HIDDEN, false);
         }
         string lbl = "SOV_SIM_SCALEIN_1_LABEL";
         if(ObjectCreate(0, lbl, OBJ_TEXT, 0, t1, scaleIn1))
         {
            ObjectSetString(0, lbl, OBJPROP_TEXT, "[2. SCALE-IN]");
            ObjectSetString(0, lbl, OBJPROP_FONT, "Consolas");
            ObjectSetInteger(0, lbl, OBJPROP_FONTSIZE, 8);
            ObjectSetInteger(0, lbl, OBJPROP_COLOR, clrScale);
            ObjectSetInteger(0, lbl, OBJPROP_ANCHOR, (long)ANCHOR_LEFT);
            ObjectSetInteger(0, lbl, OBJPROP_BACK, false);
            ObjectSetInteger(0, lbl, OBJPROP_SELECTABLE, false);
            ObjectSetInteger(0, lbl, OBJPROP_SELECTED, false);
            ObjectSetInteger(0, lbl, OBJPROP_HIDDEN, false);
         }
      }

      //--- [3. SCALE-IN] level 2
      if(scaleIn2 > 0.0)
      {
         string obj = "SOV_SIM_SCALEIN_2";
         if(ObjectCreate(0, obj, OBJ_HLINE, 0, 0, scaleIn2))
         {
            ObjectSetInteger(0, obj, OBJPROP_COLOR, clrScale);
            ObjectSetInteger(0, obj, OBJPROP_STYLE, STYLE_DOT);
            ObjectSetInteger(0, obj, OBJPROP_WIDTH, 1);
            ObjectSetInteger(0, obj, OBJPROP_BACK, false);
            ObjectSetString(0, obj, OBJPROP_TOOLTIP, "[3. SCALE-IN] " + DoubleToString(scaleIn2, 2));
            ObjectSetInteger(0, obj, OBJPROP_SELECTABLE, false);
            ObjectSetInteger(0, obj, OBJPROP_SELECTED, false);
            ObjectSetInteger(0, obj, OBJPROP_HIDDEN, false);
         }
         string lbl = "SOV_SIM_SCALEIN_2_LABEL";
         if(ObjectCreate(0, lbl, OBJ_TEXT, 0, t1, scaleIn2))
         {
            ObjectSetString(0, lbl, OBJPROP_TEXT, "[3. SCALE-IN]");
            ObjectSetString(0, lbl, OBJPROP_FONT, "Consolas");
            ObjectSetInteger(0, lbl, OBJPROP_FONTSIZE, 8);
            ObjectSetInteger(0, lbl, OBJPROP_COLOR, clrScale);
            ObjectSetInteger(0, lbl, OBJPROP_ANCHOR, (long)ANCHOR_LEFT);
            ObjectSetInteger(0, lbl, OBJPROP_BACK, false);
            ObjectSetInteger(0, lbl, OBJPROP_SELECTABLE, false);
            ObjectSetInteger(0, lbl, OBJPROP_SELECTED, false);
            ObjectSetInteger(0, lbl, OBJPROP_HIDDEN, false);
         }
      }

      //--- [INITIAL STOP LOSS] zone: shaded rectangle above stopZone
      if(stopZone > 0.0)
      {
         double zoneTop = stopZone * 1.05;   // 5% above stop zone
         double zoneBot = stopZone;
         string obj = "SOV_SIM_STOP_ZONE";
         if(ObjectCreate(0, obj, OBJ_RECTANGLE, 0, t1, zoneTop, t2, zoneBot))
         {
            ObjectSetInteger(0, obj, OBJPROP_COLOR, clrStop);
            ObjectSetInteger(0, obj, OBJPROP_STYLE, STYLE_DASH);
            ObjectSetInteger(0, obj, OBJPROP_WIDTH, 1);
            ObjectSetInteger(0, obj, OBJPROP_FILL, true);
            ObjectSetInteger(0, obj, OBJPROP_BACK, false);
            ObjectSetInteger(0, obj, OBJPROP_ZORDER, 0);
            ObjectSetString(0, obj, OBJPROP_TOOLTIP, "[INITIAL STOP LOSS] >" + DoubleToString(stopZone, 2));
            ObjectSetInteger(0, obj, OBJPROP_SELECTABLE, false);
            ObjectSetInteger(0, obj, OBJPROP_SELECTED, false);
            ObjectSetInteger(0, obj, OBJPROP_HIDDEN, false);
         }
         string lbl = "SOV_SIM_STOP_ZONE_LABEL";
         if(ObjectCreate(0, lbl, OBJ_TEXT, 0, t1, zoneTop))
         {
            ObjectSetString(0, lbl, OBJPROP_TEXT, "[INITIAL STOP LOSS]");
            ObjectSetString(0, lbl, OBJPROP_FONT, "Consolas");
            ObjectSetInteger(0, lbl, OBJPROP_FONTSIZE, 8);
            ObjectSetInteger(0, lbl, OBJPROP_COLOR, clrStop);
            ObjectSetInteger(0, lbl, OBJPROP_ANCHOR, (long)ANCHOR_LEFT);
            ObjectSetInteger(0, lbl, OBJPROP_BACK, false);
            ObjectSetInteger(0, lbl, OBJPROP_SELECTABLE, false);
            ObjectSetInteger(0, lbl, OBJPROP_SELECTED, false);
            ObjectSetInteger(0, lbl, OBJPROP_HIDDEN, false);
         }
      }

      //--- Trailed stop path: descending dashed trendline hugging MAs
      if(trailStopPx > 0.0 && trailStopT > 0)
      {
         string obj = "SOV_SIM_TRAIL_STOP";
         if(ObjectCreate(0, obj, OBJ_TREND, 0, trailStopT, trailStopPx, t2, trailStopPx * 0.985))
         {
            ObjectSetInteger(0, obj, OBJPROP_COLOR, clrTrail);
            ObjectSetInteger(0, obj, OBJPROP_STYLE, STYLE_DASH);
            ObjectSetInteger(0, obj, OBJPROP_WIDTH, 1);
            ObjectSetInteger(0, obj, OBJPROP_RAY_RIGHT, true);
            ObjectSetInteger(0, obj, OBJPROP_BACK, false);
            ObjectSetString(0, obj, OBJPROP_TOOLTIP, "Trailed SL path");
            ObjectSetInteger(0, obj, OBJPROP_SELECTABLE, false);
            ObjectSetInteger(0, obj, OBJPROP_SELECTED, false);
            ObjectSetInteger(0, obj, OBJPROP_HIDDEN, false);
         }
         string lbl = "SOV_SIM_TRAIL_STOP_LABEL";
         if(ObjectCreate(0, lbl, OBJ_TEXT, 0, trailStopT, trailStopPx))
         {
            ObjectSetString(0, lbl, OBJPROP_TEXT, "Trailed SL");
            ObjectSetString(0, lbl, OBJPROP_FONT, "Consolas");
            ObjectSetInteger(0, lbl, OBJPROP_FONTSIZE, 8);
            ObjectSetInteger(0, lbl, OBJPROP_COLOR, clrTrail);
            ObjectSetInteger(0, lbl, OBJPROP_ANCHOR, (long)ANCHOR_LEFT);
            ObjectSetInteger(0, lbl, OBJPROP_BACK, false);
            ObjectSetInteger(0, lbl, OBJPROP_SELECTABLE, false);
            ObjectSetInteger(0, lbl, OBJPROP_SELECTED, false);
            ObjectSetInteger(0, lbl, OBJPROP_HIDDEN, false);
         }
      }

      return true;
   }

   //==================================================================
   //  FULLY DYNAMIC TRADE PLAN  (SOV_DYN_ prefix)
   //  Every price here is derived at runtime from indicator buffers
   //  (iMA / iATR / iRSI / iFractals) and the live Bid/Ask. Nothing is
   //  hardcoded. Objects are cleaned per sub-prefix on every redraw.
   //==================================================================

   //--- shared object styling helper
   void DynObjCommon(const string obj, const color clr, const int width, const ENUM_LINE_STYLE style)
   {
      ObjectSetInteger(0, obj, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, obj, OBJPROP_WIDTH, width);
      ObjectSetInteger(0, obj, OBJPROP_STYLE, style);
      ObjectSetInteger(0, obj, OBJPROP_BACK, false);   // draw ON TOP of candles so visible
      ObjectSetInteger(0, obj, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, obj, OBJPROP_SELECTED, false);
      ObjectSetInteger(0, obj, OBJPROP_HIDDEN, false);  // visible in objects list too
   }

   bool DynHLine(const string name, const double price, const color clr,
                 const ENUM_LINE_STYLE style, const int width, const string tip)
   {
      if(price <= 0.0) return false;
      if(!ObjectCreate(0, name, OBJ_HLINE, 0, 0, price)) return false;
      DynObjCommon(name, clr, width, style);
      ObjectSetString(0, name, OBJPROP_TOOLTIP, tip);
      return true;
   }

   bool DynRect(const string name, const datetime t1, const double p1,
                const datetime t2, const double p2, const color clr)
   {
      if(!ObjectCreate(0, name, OBJ_RECTANGLE, 0, t1, p1, t2, p2)) return false;
      DynObjCommon(name, clr, 1, STYLE_SOLID);
      ObjectSetInteger(0, name, OBJPROP_FILL, true);
      return true;
   }

   bool DynText(const string name, const datetime when, const double price,
                 const string text, const color clr, const int size = 8)
   {
      if(!ObjectCreate(0, name, OBJ_TEXT, 0, when, price)) return false;
      ObjectSetString(0, name, OBJPROP_TEXT, text);
      ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, size);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, name, OBJPROP_ANCHOR, (long)ANCHOR_LEFT);
      ObjectSetInteger(0, name, OBJPROP_BACK, false);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_SELECTED, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, false);
      return true;
   }

   bool DynTrend(const string name, const datetime t1, const double p1,
                 const datetime t2, const double p2, const color clr,
                 const ENUM_LINE_STYLE style, const int width)
   {
      if(!ObjectCreate(0, name, OBJ_TREND, 0, t1, p1, t2, p2)) return false;
      DynObjCommon(name, clr, width, style);
      ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
      return true;
   }

   bool DynArrow(const string name, const datetime when, const double price,
                  const int code, const color clr)
   {
      if(!ObjectCreate(0, name, OBJ_ARROW, 0, when, price)) return false;
      ObjectSetInteger(0, name, OBJPROP_ARROWCODE, code);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_SELECTED, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, false);
      return true;
   }

   //--- draw the plan: entry / stop / tp / scale-ins / stop-zone / banner
   bool DrawDynamicPlan(const SovereignPlanView &plan)
   {
      if(!m_enabled) return false;
      ObjectsDeleteAll(0, "SOV_DYN_PLAN_");
      m_dirty = true;

      datetime t1 = (plan.barTime > 0) ? plan.barTime : iTime(_Symbol, _Period, 0);
      if(t1 <= 0) t1 = TimeCurrent();
      datetime t2 = t1 + PeriodSeconds(_Period) * 120;

      if(!plan.valid)
      {
         DynText("SOV_DYN_PLAN_STATE", t1, plan.bid, "NO PLAN: " + plan.note, clrGray);
         return false;
      }
      if(plan.side == 0)
      {
         DynText("SOV_DYN_PLAN_STATE", t1, plan.bid, "NO REGIME: " + plan.note, clrOrange);
         return false;
      }

      int   digits   = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
      color clrEntry = clrAqua;
      color clrStop  = clrRed;
      color clrTp    = clrLimeGreen;
      color clrScale = clrGold;

      DynHLine("SOV_DYN_PLAN_ENTRY", plan.entry, clrEntry, STYLE_SOLID, 2,
              "Dynamic entry " + DoubleToString(plan.entry, digits));
      DynHLine("SOV_DYN_PLAN_STOP", plan.stop, clrStop, STYLE_DASH, 1,
              "Dynamic stop (ATR) " + DoubleToString(plan.stop, digits));
      DynHLine("SOV_DYN_PLAN_TP", plan.tp, clrTp, STYLE_DASH, 1,
              "Dynamic TP (ATR) " + DoubleToString(plan.tp, digits));
      DynHLine("SOV_DYN_PLAN_SCALE2", plan.scale2, clrScale, STYLE_DOT, 1,
              "Scale-in 2 (ATR step) " + DoubleToString(plan.scale2, digits));
      DynHLine("SOV_DYN_PLAN_SCALE3", plan.scale3, clrScale, STYLE_DOT, 1,
              "Scale-in 3 (ATR step) " + DoubleToString(plan.scale3, digits));

      //--- shaded stop-loss zone: swing high / ATR ceiling -> entry stop
      double top = MathMax(plan.zoneTop, plan.zoneBot);
      double bot = MathMin(plan.zoneTop, plan.zoneBot);
      if(top > bot)
         DynRect("SOV_DYN_PLAN_STOPZONE", t1, top, t2, bot, (color)C'70,20,20');

      //--- shaded profit zone: entry -> take profit (matches the plan schematic)
      double pTop = MathMax(plan.entry, plan.tp);
      double pBot = MathMin(plan.entry, plan.tp);
      if(pTop > pBot)
         DynRect("SOV_DYN_PLAN_TPZONE", t1, pTop, t2, pBot, (color)C'16,60,24');

      //--- structural support / resistance from confirmed fractals
      if(plan.swingHigh > 0.0)
      {
         DynHLine("SOV_DYN_PLAN_RES", plan.swingHigh, clrTomato, STYLE_DASH, 1,
                 "Resistance (fractal) " + DoubleToString(plan.swingHigh, digits));
         DynText("SOV_DYN_PLAN_LBL_RES", t1, plan.swingHigh,
                 "RES " + DoubleToString(plan.swingHigh, digits), clrTomato);
      }
      if(plan.swingLow > 0.0)
      {
         DynHLine("SOV_DYN_PLAN_SUP", plan.swingLow, clrLime, STYLE_DASH, 1,
                 "Support (fractal) " + DoubleToString(plan.swingLow, digits));
         DynText("SOV_DYN_PLAN_LBL_SUP", t1, plan.swingLow,
                 "SUP " + DoubleToString(plan.swingLow, digits), clrLime);
      }

      //--- numbered callouts
      DynText("SOV_DYN_PLAN_LBL_ENTRY", t1, plan.entry,
              StringFormat("ENTRY %s (%s)", DoubleToString(plan.entry, digits),
                           (plan.side < 0 ? "SHORT" : "LONG")), clrEntry, 9);
      DynText("SOV_DYN_PLAN_LBL_STOP", t1, plan.stop,
              "STOP " + DoubleToString(plan.stop, digits), clrStop);
      DynText("SOV_DYN_PLAN_LBL_TP", t1, plan.tp,
              "TP " + DoubleToString(plan.tp, digits), clrTp);
      DynText("SOV_DYN_PLAN_LBL_S2", t1, plan.scale2,
              "LEG2 " + DoubleToString(plan.scale2, digits), clrScale);
      DynText("SOV_DYN_PLAN_LBL_S3", t1, plan.scale3,
              "LEG3 " + DoubleToString(plan.scale3, digits), clrScale);

      //--- live analysis banner
      string banner = StringFormat("%s  rsi=%.1f  MAspread=%.5f  ATR=%.5f  legs=%d  net=%.2f  guard=%s",
                                   (plan.side < 0 ? "SHORT PLAN" : "LONG PLAN"),
                                   plan.rsi, plan.maSpread, plan.atr,
                                   plan.legs, plan.netProfit,
                                   (plan.guardArmed ? "ARMED" : "BLOCKED"));
      DynText("SOV_DYN_PLAN_BANNER", t1, plan.bid, banner,
              (plan.guardArmed ? clrLime : clrRed), 9);

      //--- directional arrow at the reference entry
      if(plan.side < 0)
         DynArrow("SOV_DYN_PLAN_ARROW", t1, plan.entry, SOV_ARROW_DOWN, clrRed);
      else
         DynArrow("SOV_DYN_PLAN_ARROW", t1, plan.entry, SOV_ARROW_UP, clrLime);

      return true;
   }

   //--- draw the slow-MA path and the fast/mid dynamic band as polylines
   void DrawDynamicPaths(const datetime &times[], const double &slow[],
                         const double &fast[], const double &mid[], const int count)
   {
      if(!m_enabled) return;
      ObjectsDeleteAll(0, "SOV_DYN_PATH_");
      if(count < 2) return;
      int n = MathMin(count, MathMin(ArraySize(times),
                        MathMin(ArraySize(slow), MathMin(ArraySize(fast), ArraySize(mid)))));
      if(n < 2) return;
      m_dirty = true;

      int step = MathMax(1, n / 20);   // cap object count (~20 segments per line)
      int prev = 0;
      int seg  = 0;
      for(int i = step; i < n; i += step)
      {
         string nm = "SOV_DYN_PATH_SLOW_" + IntegerToString(seg);
         DynTrend(nm, times[prev], slow[prev], times[i], slow[i], clrDodgerBlue, STYLE_SOLID, 2);
         nm = "SOV_DYN_PATH_FAST_" + IntegerToString(seg);
         DynTrend(nm, times[prev], fast[prev], times[i], fast[i], clrOrange, STYLE_DOT, 1);
         nm = "SOV_DYN_PATH_MID_" + IntegerToString(seg);
         DynTrend(nm, times[prev], mid[prev], times[i], mid[i], clrSilver, STYLE_DOT, 1);
         prev = i;
         seg++;
      }
      if(prev != n - 1)
      {
         string nm = "SOV_DYN_PATH_SLOW_LAST";
         DynTrend(nm, times[prev], slow[prev], times[n - 1], slow[n - 1], clrDodgerBlue, STYLE_SOLID, 2);
         nm = "SOV_DYN_PATH_FAST_LAST";
         DynTrend(nm, times[prev], fast[prev], times[n - 1], fast[n - 1], clrOrange, STYLE_DOT, 1);
         nm = "SOV_DYN_PATH_MID_LAST";
         DynTrend(nm, times[prev], mid[prev], times[n - 1], mid[n - 1], clrSilver, STYLE_DOT, 1);
      }
   }

   //--- numbered markers at the exact bar time + price of each live leg
   void DrawDynamicLegMarkers(const datetime &times[], const double &prices[],
                              const int &sides[], const int count)
   {
      if(!m_enabled) return;
      ObjectsDeleteAll(0, "SOV_DYN_LEG_");
      if(count <= 0) return;
      int n = MathMin(count, MathMin(ArraySize(times),
                        MathMin(ArraySize(prices), ArraySize(sides))));
      if(n <= 0) return;
      m_dirty = true;

      int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
      for(int i = 0; i < n; i++)
      {
         string nm = "SOV_DYN_LEG_ARROW_" + IntegerToString(i);
         DynArrow(nm, times[i], prices[i],
                  (sides[i] < 0 ? SOV_ARROW_DOWN : SOV_ARROW_UP),
                  (sides[i] < 0 ? clrRed : clrLime));
         nm = "SOV_DYN_LEG_LABEL_" + IntegerToString(i);
         DynText(nm, times[i], prices[i],
                 StringFormat("Leg %d  %s", i + 1, DoubleToString(prices[i], digits)),
                 clrWhite, 8);
      }
   }
};
#endif
