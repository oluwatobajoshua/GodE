//+------------------------------------------------------------------+
//|                                       SovereignStructure.mqh     |
//|        Multi-timeframe structure tracker — "look left" S/R levels|
//|                                                                  |
//|  Tracks swing highs/lows across H1, H4, D1.  Scores each level   |
//|  by touches, recency, ATR distance, and cross-TF alignment.      |
//|  Classifies structure as GOOD / WEAK / BAD so the EA can decide  |
//|  whether to trade into structure or stay away.                   |
//|                                                                  |
//|  READ-ONLY.  Nothing here opens, sizes, or closes an order.      |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_STRUCTURE_MQH__
#define __SOVEREIGN_STRUCTURE_MQH__

#include <Trade\Trade.mqh>

#define SOV_STRUCT_MAX_LEVELS   20   // max tracked levels per symbol
#define SOV_STRUCT_TOUCH_TOL    0.35 // ATR multiple for level touch
#define SOV_STRUCT_MAX_AGE_BARS 500  // drop levels older than this

enum SovStructureGrade
{
   STRUCT_GOOD   = 2,   // structure supports the trade direction
   STRUCT_WEAK   = 1,   // marginal — scale down or skip
   STRUCT_BAD    = 0,   // structure opposes — no trade
};

struct SovLevel
{
   datetime    time;       // when the swing formed
   double      price;      // level price
   int         kind;       // +1 resistance (swing high), -1 support (swing low)
   int         tf;         // timeframe of the swing (PERIOD_H1/H4/D1)
   int         touches;    // how many times price returned to this level
   datetime    lastTouch;  // last time price tested the level
   double      distAtr;    // current distance from price in ATR
   bool        broken;     // level has been decisively broken through
   string      tag;        // human label
};

struct SovStructureReport
{
   bool        ok;
   string      symbol;
   ENUM_TIMEFRAMES tf;
   datetime    barTime;
   double      price;
   double      atr;

   //--- nearest levels ahead of price (in trade direction)
   double      nearestResistance;   // above price
   double      nearestSupport;      // below price
   double      distResAtr;          // distance to resistance in ATR
   double      distSupAtr;          // distance to support in ATR
   double      target;              // structure-based target
   double      invalidation;        // structure-based invalidation

   //--- structure grade for each side
   int         gradeLong;   // STRUCT_GOOD/WEAK/BAD for longs
   int         gradeShort;  // STRUCT_GOOD/WEAK/BAD for shorts

   //--- why
   int         confluenceLong;   // number of TFs aligned long
   int         confluenceShort;  // number of TFs aligned short
   string      reason;
   string      zoneText;
   string      targetText;
   string      invalidText;

   //--- raw levels (most recent first)
   SovLevel    levels[SOV_STRUCT_MAX_LEVELS];
   int         nLevels;
};

class CSovereignStructure
{
private:
   string      m_symbol;
   ENUM_TIMEFRAMES m_tf;
   long        m_magic;

   int         m_swingBars;       // fractal confirmation window (k)
   double      m_levelTolAtr;     // tolerance for level touch (ATR mult)
   int         m_maxAgeBars;      // drop stale levels

   SovLevel    m_levels[SOV_STRUCT_MAX_LEVELS];
   int         m_nLevels;

   //--- detect a confirmed swing high/low k bars ago (no lookahead)
   bool SwingHighAt(const int shift) const
   {
      int need = shift + m_swingBars + 1;
      double hi[];
      ArraySetAsSeries(hi, true);
      if(CopyHigh(m_symbol, m_tf, 1, need, hi) < need)
         return false;
      double v = hi[shift];
      if(shift + m_swingBars >= ArraySize(hi) || shift - m_swingBars < 0)
         return false;
      for(int k = 1; k <= m_swingBars; k++)
      {
         if(shift + k < ArraySize(hi) && hi[shift + k] >= v)
            return false;
         if(shift - k >= 0 && hi[shift - k] >= v)
            return false;
      }
      return true;
   }

   bool SwingLowAt(const int shift) const
   {
      int need = shift + m_swingBars + 1;
      double lo[];
      ArraySetAsSeries(lo, true);
      if(CopyLow(m_symbol, m_tf, 1, need, lo) < need)
         return false;
      double v = lo[shift];
      if(shift + m_swingBars >= ArraySize(lo) || shift - m_swingBars < 0)
         return false;
      for(int k = 1; k <= m_swingBars; k++)
      {
         if(shift + k < ArraySize(lo) && lo[shift + k] <= v)
            return false;
         if(shift - k >= 0 && lo[shift - k] <= v)
            return false;
      }
      return true;
   }

   double GetATR() const
   {
      int h = iATR(m_symbol, m_tf, 14);
      if(h == INVALID_HANDLE)
         return 0.0;
      double buf[1];
      ArraySetAsSeries(buf, true);
      bool ok = (CopyBuffer(h, 0, 1, 1, buf) >= 1);
      IndicatorRelease(h);
      if(!ok || buf[0] <= 0.0)
         return 0.0;
      return buf[0];
   }

   //--- add or update a level at a swing price
   void AddLevel(const datetime t, const double price, const int kind,
                 const ENUM_TIMEFRAMES tfLvl)
   {
      //--- check if we already have a level at this price (within tolerance)
      double atr = GetATR();
      double tol = atr * m_levelTolAtr;
      for(int i = 0; i < m_nLevels; i++)
      {
         if(m_levels[i].kind == kind && MathAbs(m_levels[i].price - price) <= tol)
         {
            m_levels[i].touches++;
            m_levels[i].lastTouch = t;
            m_levels[i].time = MathMin(m_levels[i].time, t); // keep earliest
            return;
         }
      }
      //--- new level
      if(m_nLevels >= SOV_STRUCT_MAX_LEVELS)
         return; // full
      int idx = m_nLevels++;
      m_levels[idx].time      = t;
      m_levels[idx].price     = price;
      m_levels[idx].kind      = kind;
      m_levels[idx].tf        = (int)tfLvl;
      m_levels[idx].touches   = 1;
      m_levels[idx].lastTouch = t;
      m_levels[idx].distAtr   = 0.0;
      m_levels[idx].broken    = false;
      m_levels[idx].tag       = "";
   }

   //--- prune stale and broken levels
   void PruneLevels(const datetime nowBar)
   {
      int write = 0;
      for(int i = 0; i < m_nLevels; i++)
      {
         int ageBars = (int)((nowBar - m_levels[i].time) / PeriodSeconds(m_tf));
         bool tooOld = (ageBars > m_maxAgeBars);
         if(m_levels[i].broken || tooOld)
            continue;
         if(write != i)
            m_levels[write] = m_levels[i];
         write++;
      }
      m_nLevels = write;
   }

   //--- update distances and check for breaks/touches
   void UpdateLevels(const double price, const double atr,
                     const datetime nowBar)
   {
      if(atr <= 0.0)
         return;
      for(int i = 0; i < m_nLevels; i++)
      {
         m_levels[i].distAtr = MathAbs(price - m_levels[i].price) / atr;
         //--- touch detection: price came within tolerance of the level
         if(MathAbs(price - m_levels[i].price) <= atr * m_levelTolAtr)
         {
            if(nowBar > m_levels[i].lastTouch + PeriodSeconds(m_tf))
            {
               m_levels[i].touches++;
               m_levels[i].lastTouch = nowBar;
            }
         }
         //--- break detection: close decisively through the level
         if(m_levels[i].kind > 0 && price > m_levels[i].price + atr * 0.5)
            m_levels[i].broken = true;   // resistance broken to upside
         if(m_levels[i].kind < 0 && price < m_levels[i].price - atr * 0.5)
            m_levels[i].broken = true;   // support broken to downside
      }
   }

   //--- find nearest resistance above and support below
   void FindNearestLevels(const double price, const double atr,
                          double &res, double &sup,
                          double &resDist, double &supDist) const
   {
      res = 0.0; sup = 0.0;
      resDist = 1e9; supDist = 1e9;
      for(int i = 0; i < m_nLevels; i++)
      {
         if(m_levels[i].broken)
            continue;
         if(m_levels[i].kind > 0 && m_levels[i].price > price)
         {
            double d = (m_levels[i].price - price) / atr;
            if(d < resDist)
            {
               resDist = d;
               res = m_levels[i].price;
            }
         }
         if(m_levels[i].kind < 0 && m_levels[i].price < price)
         {
            double d = (price - m_levels[i].price) / atr;
            if(d < supDist)
            {
               supDist = d;
               sup = m_levels[i].price;
            }
         }
      }
   }

   //--- grade structure for a side based on confluence + level quality
   int GradeSide(const int side, const double price, const double atr,
                 const int confluence, const bool stackAligned) const
   {
      //--- find relevant nearest level
      double nearest = 0.0;
      double nearestDist = 1e9;
      int    nearestTouches = 0;
      for(int i = 0; i < m_nLevels; i++)
      {
         if(m_levels[i].broken)
            continue;
         if(side > 0 && m_levels[i].kind > 0 && m_levels[i].price > price)
         {
            double d = (m_levels[i].price - price) / atr;
            if(d < nearestDist)
            {
               nearestDist = d;
               nearest = m_levels[i].price;
               nearestTouches = m_levels[i].touches;
            }
         }
         if(side < 0 && m_levels[i].kind < 0 && m_levels[i].price < price)
         {
            double d = (price - m_levels[i].price) / atr;
            if(d < nearestDist)
            {
               nearestDist = d;
               nearest = m_levels[i].price;
               nearestTouches = m_levels[i].touches;
            }
         }
      }

      //--- scoring
      int score = 0;

      // confluence across timeframes (0-3)
      score += MathMin(confluence, 3);

      // stack aligned with trade direction
      if(stackAligned)
         score += 2;

      // nearest level far enough (room to run)
      if(nearestDist > 1.5)
         score += 1;
      if(nearestDist > 3.0)
         score += 1;

      // level strength: few touches = strong (untested), many = weak (worn out)
      if(nearestTouches <= 1)
         score += 1;
      else if(nearestTouches >= 5)
         score -= 1;

      //--- grade
      if(score >= 5)
         return STRUCT_GOOD;
      if(score >= 3)
         return STRUCT_WEAK;
      return STRUCT_BAD;
   }

public:
   CSovereignStructure()
   {
      m_symbol = "";
      m_tf = PERIOD_H1;
      m_magic = 0;
      m_swingBars = 2;
      m_levelTolAtr = SOV_STRUCT_TOUCH_TOL;
      m_maxAgeBars = SOV_STRUCT_MAX_AGE_BARS;
      m_nLevels = 0;
   }

   bool Init(const string symbol, const ENUM_TIMEFRAMES tf,
             const long magic = 0,
             const int swingBars = 2,
             const double levelTolAtr = SOV_STRUCT_TOUCH_TOL,
             const int maxAgeBars = SOV_STRUCT_MAX_AGE_BARS)
   {
      m_symbol = symbol;
      m_tf = tf;
      m_magic = magic;
      m_swingBars = MathMax(1, swingBars);
      m_levelTolAtr = MathMax(0.05, levelTolAtr);
      m_maxAgeBars = MathMax(50, maxAgeBars);
      m_nLevels = 0;
      return true;
   }

   void Deinit() { }

   //--- call on every new bar to rebuild the level map (look left)
   void OnNewBar(const datetime nowBar, const double price, const double atr)
   {
      if(m_symbol == "")
         return;

      //--- scan recent completed bars for new swings (shift 1 = last completed)
      int scan = MathMax(m_swingBars * 2 + 5, 30);
      double h[], l[];
      datetime t[];
      ArraySetAsSeries(h, true);
      ArraySetAsSeries(l, true);
      ArraySetAsSeries(t, true);
      if(CopyHigh(m_symbol, m_tf, 1, scan, h) < scan)
         return;
      if(CopyLow(m_symbol, m_tf, 1, scan, l) < scan)
         return;
      if(CopyTime(m_symbol, m_tf, 1, scan, t) < scan)
         return;

      //--- look left: confirm swings on completed bars only
      for(int shift = m_swingBars + 1; shift < scan - m_swingBars; shift++)
      {
         if(SwingHighAt(shift))
         {
            datetime swingT = t[shift];
            double swingP = h[shift];
            //--- also check higher TFs for alignment (sample H4, D1)
            AddLevel(swingT, swingP, +1, m_tf);
            //--- H4 alignment: if H1 swing high is also near H4 swing high
            if(m_tf <= PERIOD_H1)
               AddLevelHigherTF(swingP, +1, PERIOD_H4);
            if(m_tf <= PERIOD_H4)
               AddLevelHigherTF(swingP, +1, PERIOD_D1);
         }
         if(SwingLowAt(shift))
         {
            datetime swingT = t[shift];
            double swingP = l[shift];
            AddLevel(swingT, swingP, -1, m_tf);
            if(m_tf <= PERIOD_H1)
               AddLevelHigherTF(swingP, -1, PERIOD_H4);
            if(m_tf <= PERIOD_H4)
               AddLevelHigherTF(swingP, -1, PERIOD_D1);
         }
      }

      PruneLevels(nowBar);
      UpdateLevels(price, atr, nowBar);
   }

   //--- add a level from a higher timeframe if price is near it
   void AddLevelHigherTF(const double price, const int kind,
                         const ENUM_TIMEFRAMES tfLvl)
   {
      double atr = GetATR();
      double tol = atr * m_levelTolAtr * 2.0; // wider tolerance for higher TF
      for(int i = 0; i < m_nLevels; i++)
      {
         if(m_levels[i].tf == (int)tfLvl &&
            MathAbs(m_levels[i].price - price) <= tol)
         {
            m_levels[i].touches++; // reinforce existing
            return;
         }
      }
      if(m_nLevels >= SOV_STRUCT_MAX_LEVELS)
         return;
      int idx = m_nLevels++;
      m_levels[idx].time      = TimeCurrent();
      m_levels[idx].price     = price;
      m_levels[idx].kind      = kind;
      m_levels[idx].tf        = (int)tfLvl;
      m_levels[idx].touches   = 1;
      m_levels[idx].lastTouch = TimeCurrent();
      m_levels[idx].distAtr   = 0.0;
      m_levels[idx].broken    = false;
      m_levels[idx].tag       = "HTF";
   }

   //--- build the full structure report for a side
   bool BuildReport(const int side, const double price, const double atr,
                    const bool stackAligned, SovStructureReport &r)
   {
      r.ok = false;
      r.symbol = m_symbol;
      r.tf = m_tf;
      r.barTime = iTime(m_symbol, m_tf, 1);
      r.price = price;
      r.atr = atr;
      r.nearestResistance = 0.0;
      r.nearestSupport = 0.0;
      r.distResAtr = 1e9;
      r.distSupAtr = 1e9;
      r.gradeLong = STRUCT_BAD;
      r.gradeShort = STRUCT_BAD;
      r.confluenceLong = 0;
      r.confluenceShort = 0;
      r.reason = "";
      r.zoneText = "";
      r.targetText = "";
      r.invalidText = "";
      r.nLevels = m_nLevels;
      for(int i = 0; i < m_nLevels; i++)
         r.levels[i] = m_levels[i];

      if(atr <= 0.0 || m_nLevels == 0)
         return false;

      FindNearestLevels(price, atr, r.nearestResistance, r.nearestSupport,
                        r.distResAtr, r.distSupAtr);

      //--- confluence: count TFs with levels near price in each direction
      int cLong = 0, cShort = 0;
      for(int i = 0; i < m_nLevels; i++)
      {
         if(m_levels[i].broken)
            continue;
         double d = MathAbs(price - m_levels[i].price) / atr;
         if(d > 2.0)
            continue;
         if(m_levels[i].kind > 0) // resistance above — confluence for shorts
            cShort++;
         if(m_levels[i].kind < 0) // support below — confluence for longs
            cLong++;
      }
      r.confluenceLong = cLong;
      r.confluenceShort = cShort;

      //--- grade each side
      r.gradeLong  = GradeSide(1,  price, atr, cLong,  stackAligned);
      r.gradeShort = GradeSide(-1, price, atr, cShort, stackAligned);

      //--- reason text
      if(side > 0)
      {
         if(r.gradeLong == STRUCT_GOOD)
            r.reason = "LONG structure GOOD — support below, room to resistance";
         else if(r.gradeLong == STRUCT_WEAK)
            r.reason = "LONG structure WEAK — nearby resistance or worn support";
         else
            r.reason = "LONG structure BAD — heavy resistance above or chop";
      }
      else
      {
         if(r.gradeShort == STRUCT_GOOD)
            r.reason = "SHORT structure GOOD — resistance above, room to support";
         else if(r.gradeShort == STRUCT_WEAK)
            r.reason = "SHORT structure WEAK — nearby support or worn resistance";
         else
            r.reason = "SHORT structure BAD — heavy support below or chop";
      }

      //--- zone text
      if(r.nearestSupport > 0.0)
         r.zoneText = StringFormat("support %s (%.1f ATR below)",
                                   FormatPrice(r.nearestSupport), r.distSupAtr);
      if(r.nearestResistance > 0.0)
      {
         if(r.zoneText != "")
            r.zoneText += " | ";
         r.zoneText += StringFormat("resistance %s (%.1f ATR above)",
                                    FormatPrice(r.nearestResistance), r.distResAtr);
      }

      //--- target / invalidation
      if(side > 0)
      {
         r.target = r.nearestResistance;
         r.invalidation = r.nearestSupport;
         if(r.target > 0.0)
            r.targetText = StringFormat("target %s (resistance)", FormatPrice(r.target));
         if(r.invalidation > 0.0)
            r.invalidText = StringFormat("invalidate below %s (support)",
                                         FormatPrice(r.invalidation));
      }
      else
      {
         r.target = r.nearestSupport;
         r.invalidation = r.nearestResistance;
         if(r.target > 0.0)
            r.targetText = StringFormat("target %s (support)", FormatPrice(r.target));
         if(r.invalidation > 0.0)
            r.invalidText = StringFormat("invalidate above %s (resistance)",
                                         FormatPrice(r.invalidation));
      }

      r.ok = true;
      return true;
   }

   //--- grade for a specific side (convenience)
   int Grade(const int side, const double price, const double atr,
             const bool stackAligned) const
   {
      int c = 0;
      for(int i = 0; i < m_nLevels; i++)
      {
         if(m_levels[i].broken)
            continue;
         double d = MathAbs(price - m_levels[i].price) / atr;
         if(d > 2.0)
            continue;
         if((side > 0 && m_levels[i].kind < 0) ||
            (side < 0 && m_levels[i].kind > 0))
            c++;
      }
      return GradeSide(side, price, atr, c, stackAligned);
   }

   string FormatPrice(const double p) const
   {
      int digits = (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS);
      if(digits <= 0)
         digits = 5;
      return DoubleToString(p, digits);
   }

   //--- accessors
   int    LevelsCount()   const { return m_nLevels; }
   int    SwingBars()     const { return m_swingBars; }
   double LevelTolAtr()   const { return m_levelTolAtr; }
};

#endif