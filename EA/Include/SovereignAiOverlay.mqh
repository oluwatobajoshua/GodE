//+------------------------------------------------------------------+
//|                                     SovereignAiOverlay.mqh       |
//|                                                                  |
//|  Draws the AI analyst's read on the live chart.                   |
//|                                                                  |
//|  Input is the flat CSV written by research/llm_chart_analyst.py:  |
//|    Common\Files\Sovereign\ai_overlay_<SYM>_<TF>.csv               |
//|                                                                  |
//|  Format is one record per line, comma separated, because that is   |
//|  the only thing MQL5 can parse without a JSON library. Records:    |
//|    READ,<long|short|wait>,<conf>,<veto>                            |
//|    LEVEL,<price>,<support|resistance>,<label>                      |
//|    ZONE,<lo>,<hi>,<label>                                          |
//|    ARROW,<bar_offset>,<up|down>,<label>                            |
//|    plus PRICE/EMA21/EMA50/RSI/ATR/SLOPE/STRUCT/NARR/VETO/RISK      |
//|                                                                  |
//|  Safety: this layer is READ-ONLY. It draws what the analyst said.  |
//|  It never opens, sizes, or closes a position. If the file is       |
//|  missing or stale it draws nothing and says so in the panel.       |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_AI_OVERLAY_MQH__
#define __SOVEREIGN_AI_OVERLAY_MQH__

#include <Sovereign\SovereignError.mqh>

#define SOV_AI_MAX_LEVELS  16
#define SOV_AI_MAX_ZONES    8
#define SOV_AI_MAX_ARROWS  24
#define SOV_AI_MAX_RISKS    6

class CSovereignAiOverlay
{
private:
   string m_sym;
   string m_tf;
   bool   m_loaded;
   long   m_mtime;

   string m_read;
   double m_conf;
   bool   m_veto;
   string m_source;
   string m_narrative;
   string m_vetoReason;
   string m_asof;
   string m_structure;

   double m_price;
   double m_ema21;
   double m_ema50;
   double m_rsi;
   double m_atr;
   double m_slope;
   double m_spread;
   bool   m_spreadHostile;

   string m_risks[SOV_AI_MAX_RISKS];
   int    m_riskCount;

   double m_lvPrice[SOV_AI_MAX_LEVELS];
   string m_lvKind[SOV_AI_MAX_LEVELS];
   string m_lvLabel[SOV_AI_MAX_LEVELS];
   int    m_lvCount;

   double m_znLo[SOV_AI_MAX_ZONES];
   double m_znHi[SOV_AI_MAX_ZONES];
   string m_znLabel[SOV_AI_MAX_ZONES];
   int    m_znCount;

   int    m_arOffset[SOV_AI_MAX_ARROWS];
   string m_arDir[SOV_AI_MAX_ARROWS];
   string m_arLabel[SOV_AI_MAX_ARROWS];
   int    m_arCount;

   //--- helpers -----------------------------------------------------
   string FilePath() const
   {
      return "Sovereign\\ai_overlay_" + m_sym + "_" + m_tf + ".csv";
   }

   void Reset()
   {
      m_read       = "";
      m_conf       = 0.0;
      m_veto       = false;
      m_source     = "";
      m_narrative  = "";
      m_vetoReason = "";
      m_asof       = "";
      m_structure  = "";
      m_price      = 0.0;
      m_ema21      = 0.0;
      m_ema50      = 0.0;
      m_rsi        = 0.0;
      m_atr        = 0.0;
      m_slope      = 0.0;
      m_spread     = 0.0;
      m_spreadHostile = false;
      m_riskCount  = 0;
      m_lvCount    = 0;
      m_znCount    = 0;
      m_arCount    = 0;
      for(int i = 0; i < SOV_AI_MAX_RISKS; i++)
         m_risks[i] = "";
      for(int i = 0; i < SOV_AI_MAX_LEVELS; i++)
      {
         m_lvPrice[i] = 0.0;
         m_lvKind[i]  = "";
         m_lvLabel[i] = "";
      }
      for(int i = 0; i < SOV_AI_MAX_ZONES; i++)
      {
         m_znLo[i]    = 0.0;
         m_znHi[i]    = 0.0;
         m_znLabel[i] = "";
      }
      for(int i = 0; i < SOV_AI_MAX_ARROWS; i++)
      {
         m_arOffset[i] = 0;
         m_arDir[i]    = "";
         m_arLabel[i]  = "";
      }
   }

   static double ToDouble(const string s, const double def = 0.0)
   {
      if(s == "")
         return def;
      return StringToDouble(s);
   }

public:
   CSovereignAiOverlay()
   {
      m_sym    = "";
      m_tf     = "";
      m_loaded = false;
      m_mtime  = 0;
      Reset();
   }

   //--- load / reload ------------------------------------------------
   bool Load(const string sym, const string tf, const bool force = false)
   {
      string fname = "Sovereign\\ai_overlay_" + sym + "_" + tf + ".csv";
      long mt = FileGetInteger(fname, FILE_MODIFY_DATE, true);

      if(!force && m_loaded && sym == m_sym && tf == m_tf && mt == m_mtime)
         return true;

      Reset();
      m_sym = sym;
      m_tf  = tf;

      int fh = FileOpen(fname, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
      if(fh == INVALID_HANDLE)
      {
         m_loaded = false;
         m_mtime  = 0;
         return false;                       // nothing to draw yet - not an error
      }

      while(!FileIsEnding(fh))
      {
         string line = FileReadString(fh);
         StringTrimLeft(line);
         StringTrimRight(line);
         if(line == "" || StringGetCharacter(line, 0) == '#')
            continue;

         string p[];
         int n = StringSplit(line, ',', p);
         if(n < 2)
            continue;

         string tag = p[0];
         if(tag == "READ")
         {
            m_read = p[1];
            if(n >= 3) m_conf = ToDouble(p[2], 0.0);
            if(n >= 4) m_veto = (p[3] != "0" && p[3] != "");
         }
         else if(tag == "SOURCE")     m_source     = p[1];
         else if(tag == "NARR")       m_narrative  = p[1];
         else if(tag == "VETO")       m_vetoReason = p[1];
         else if(tag == "ASOF")       m_asof       = p[1];
         else if(tag == "STRUCT")     m_structure  = p[1];
         else if(tag == "PRICE")      m_price      = ToDouble(p[1]);
         else if(tag == "EMA21")      m_ema21      = ToDouble(p[1]);
         else if(tag == "EMA50")      m_ema50      = ToDouble(p[1]);
         else if(tag == "RSI")        m_rsi        = ToDouble(p[1]);
         else if(tag == "ATR")        m_atr        = ToDouble(p[1]);
         else if(tag == "SLOPE")      m_slope      = ToDouble(p[1]);
         else if(tag == "SPREAD")
         {
            m_spread = ToDouble(p[1]);
            if(n >= 3) m_spreadHostile = (p[2] != "0" && p[2] != "");
         }
         else if(tag == "RISK")
         {
            if(m_riskCount < SOV_AI_MAX_RISKS)
               m_risks[m_riskCount++] = p[1];
         }
         else if(tag == "LEVEL")
         {
            if(m_lvCount < SOV_AI_MAX_LEVELS && n >= 2)
            {
               m_lvPrice[m_lvCount] = ToDouble(p[1]);
               m_lvKind[m_lvCount]  = (n >= 3 ? p[2] : "level");
               m_lvLabel[m_lvCount] = (n >= 4 ? p[3] : "");
               if(m_lvPrice[m_lvCount] > 0.0)
                  m_lvCount++;
            }
         }
         else if(tag == "ZONE")
         {
            if(m_znCount < SOV_AI_MAX_ZONES && n >= 3)
            {
               double lo = ToDouble(p[1]);
               double hi = ToDouble(p[2]);
               if(lo > hi)
               {
                  double t = lo; lo = hi; hi = t;
               }
               if(lo > 0.0 && hi > 0.0)
               {
                  m_znLo[m_znCount]    = lo;
                  m_znHi[m_znCount]    = hi;
                  m_znLabel[m_znCount] = (n >= 4 ? p[3] : "");
                  m_znCount++;
               }
            }
         }
         else if(tag == "ARROW")
         {
            if(m_arCount < SOV_AI_MAX_ARROWS && n >= 2)
            {
               m_arOffset[m_arCount] = (int)StringToInteger(p[1]);
               m_arDir[m_arCount]    = (n >= 3 ? p[2] : "up");
               m_arLabel[m_arCount]  = (n >= 4 ? p[3] : "");
               m_arCount++;
            }
         }
      }
      FileClose(fh);

      m_mtime  = mt;
      m_loaded = true;
      return true;
   }

   bool ReloadIfChanged()
   {
      if(m_sym == "" || m_tf == "")
         return false;
      long mt = FileGetInteger(FilePath(), FILE_MODIFY_DATE, true);
      if(mt == m_mtime)
         return false;
      return Load(m_sym, m_tf, true);
   }

   //--- drawing ------------------------------------------------------
   void Clear(CSovereignChartVisualizer &paint)
   {
      paint.DeleteWithPrefix("AI_");
   }

   void Draw(CSovereignChartVisualizer &paint,
             const color clrSupport, const color clrResistance,
             const color clrText)
   {
      if(!m_loaded)
         return;

      //--- 1. the analyst's support / resistance levels
      for(int i = 0; i < m_lvCount; i++)
      {
         bool  isSup = (m_lvKind[i] == "support");
         color clr   = isSup ? clrSupport : clrResistance;
         string tip  = (m_lvLabel[i] != "" ? m_lvLabel[i] : m_lvKind[i]) +
                        "  (AI " + m_source + ")";
         paint.DrawLevel("AI_LV_" + IntegerToString(i), m_lvPrice[i], clr,
                         STYLE_DOT, 1, tip);
      }

      //--- 2. shaded zones, drawn from the visible window back 80 bars
      datetime t1 = iTime(m_sym, PERIOD_CURRENT, 80);
      datetime t2 = iTime(m_sym, PERIOD_CURRENT, 0);
      if(t1 <= 0) t1 = t2 - 80 * PeriodSeconds(PERIOD_CURRENT);
      for(int i = 0; i < m_znCount; i++)
         paint.DrawBox("AI_ZN_" + IntegerToString(i), t1, m_znLo[i],
                       t2, m_znHi[i], clrResistance);

      //--- 3. the analyst's entry arrows
      int bars = Bars(m_sym, PERIOD_CURRENT);
      for(int i = 0; i < m_arCount; i++)
      {
         int shift = -m_arOffset[i];
         if(shift < 0 || shift >= bars)
            continue;
         datetime when = iTime(m_sym, PERIOD_CURRENT, shift);
         if(when <= 0)
            continue;

         bool   up    = (m_arDir[i] != "down");
         double hi    = iHigh(m_sym, PERIOD_CURRENT, shift);
         double lo    = iLow(m_sym, PERIOD_CURRENT, shift);
         double pad   = (m_atr > 0.0 ? m_atr * 0.6 : (hi - lo) * 0.5);
         double price = up ? (lo - pad) : (hi + pad);
         int    code  = up ? SOV_ARROW_UP : SOV_ARROW_DOWN;
         color  clr   = up ? clrSupport : clrResistance;

         paint.DrawArrow("AI_AR_" + IntegerToString(i), when, price, code, clr,
                         m_arLabel[i]);
      }

      //--- 4. one label at the last bar so the read is visible on the chart
      datetime last = iTime(m_sym, PERIOD_CURRENT, 0);
      if(last > 0 && m_read != "")
      {
         double hi = iHigh(m_sym, PERIOD_CURRENT, 0);
         double pad = (m_atr > 0.0 ? m_atr * 1.2 : (hi * 0.0005));
         string rd = m_read;
         StringToUpper(rd);
         string txt = StringFormat("AI %s %.2f%s", rd, m_conf,
                                   (m_veto ? " VETO" : ""));
         paint.DrawText("AI_READ", last, hi + pad, txt, clrText,
                        ANCHOR_LEFT_LOWER, 9);
      }
   }

   //--- accessors for the panel --------------------------------------
   bool   Loaded()       const { return m_loaded; }
   string Read()         const { return m_read; }
   double Confidence()   const { return m_conf; }
   bool   Veto()         const { return m_veto; }
   string Source()       const { return m_source; }
   string Narrative()    const { return m_narrative; }
   string VetoReason()   const { return m_vetoReason; }
   string AsOf()         const { return m_asof; }
   string Structure()    const { return m_structure; }
   double Price()        const { return m_price; }
   double Rsi()          const { return m_rsi; }
   double Atr()          const { return m_atr; }
   double Slope()        const { return m_slope; }
   bool   SpreadHostile() const { return m_spreadHostile; }
   int    LevelCount()   const { return m_lvCount; }
   int    ZoneCount()    const { return m_znCount; }
   int    ArrowCount()   const { return m_arCount; }
   int    RiskCount()    const { return m_riskCount; }
   string Risk(const int i) const
   {
      return (i >= 0 && i < m_riskCount) ? m_risks[i] : "";
   }

   //--- staleness: an old read is not a read
   int AgeSeconds() const
   {
      if(m_mtime <= 0)
         return 0;
      long age = (long)TimeCurrent() - m_mtime;
      return (age > 0) ? (int)age : 0;
   }

   bool Stale(const int maxAgeSec = 7200) const
   {
      return (m_mtime > 0 && AgeSeconds() > maxAgeSec);
   }

   string Describe() const
   {
      if(!m_loaded)
         return "AI: no overlay file (run llm_chart_analyst.py)";
      string rd = m_read;
      StringToUpper(rd);
      return StringFormat("AI %s %.2f  %s  %d lv  %d zn  %d ar",
                          rd, m_conf, m_source,
                          m_lvCount, m_znCount, m_arCount);
   }
};

#endif // __SOVEREIGN_AI_OVERLAY_MQH__
