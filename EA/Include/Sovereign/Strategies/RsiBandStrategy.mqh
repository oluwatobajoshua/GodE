//+------------------------------------------------------------------+
//| RsiBandStrategy.mqh — Concrete RSI band strategy                  |
//| Pure math: EMA trend + RSI band cross, no execution/UI deps       |
//+------------------------------------------------------------------+
#property strict

#include <Sovereign\Core\IStrategy.mqh>

class CRsiBandStrategy : public IStrategy
   {
   private:
      int      m_rsiPeriod;
      int      m_rsiMid;
      int      m_rsiHi;
      int      m_rsiLo;
      double   m_margin;
      double   m_deadband;
      bool     m_longOnly;
      bool     m_requireEmaSlope;

      int      m_currentTrend;
      double   m_currentRsi;
      double   m_currentRsiPrev;
      double   m_currentEma21;
      double   m_currentEma50;
      double   m_currentAtr;
      string   m_lastSummary;

   public:
      void CRsiBandStrategy(int rsiPeriod = 9, int rsiMid = 50, int rsiHi = 75,
                            int rsiLo = 25, double margin = 3.0, double deadband = 0.10,
                            bool longOnly = false, bool requireEmaSlope = false)
         {
         m_rsiPeriod = rsiPeriod;
         m_rsiMid = rsiMid;
         m_rsiHi = rsiHi;
         m_rsiLo = rsiLo;
         m_margin = margin;
         m_deadband = deadband;
         m_longOnly = longOnly;
         m_requireEmaSlope = requireEmaSlope;
         m_currentTrend = 0;
         m_currentRsi = 0;
         m_currentRsiPrev = 0;
         m_currentEma21 = 0;
         m_currentEma50 = 0;
         m_currentAtr = 0;
         m_lastSummary = "Strategy initialized, waiting for data";
         }

      //--- pure math evaluation
      virtual ENUM_SIGNAL Evaluate(const double &close[], const double &high[],
                                   const double &low[], const double &rsi[],
                                   const double &ema21[], const double &ema50[],
                                   const double &atr[])
         {
         if(ArraySize(rsi) < 2 || ArraySize(ema21) < 4 || ArraySize(ema50) < 1)
            {
            m_lastSummary = "INSUFFICIENT DATA";
            return SIGNAL_NONE;
            }

         m_currentRsi    = rsi[0];
         m_currentRsiPrev = rsi[1];
         m_currentEma21  = ema21[0];
         m_currentEma50  = ema50[0];
         m_currentAtr    = atr[0];

         //--- trend: EMA21 slope + EMA21/EMA50 alignment
         double slope3 = (ema21[0] - ema21[3]) / MathMax(m_currentAtr, 0.0001);
         m_currentTrend = 0;
         if(slope3 > m_deadband && ema21[0] > ema50[0])
            m_currentTrend = 1;
         else if(slope3 < -m_deadband && ema21[0] < ema50[0])
            m_currentTrend = -1;

         //--- entry logic
         ENUM_SIGNAL sig = SIGNAL_NONE;

         if(m_currentTrend > 0)
            {
            // uptrend: RSI crosses up through mid + margin
            if(m_currentRsiPrev <= (double)m_rsiMid &&
               m_currentRsi > ((double)m_rsiMid + m_margin))
               {
               sig = SIGNAL_BUY;
               m_lastSummary = "BULLISH TREND | RSI " + DoubleToString(m_currentRsi, 1) +
                               " crossed above mid+" + DoubleToString(m_margin, 1) +
                               " | EMA21=" + DoubleToString(m_currentEma21, 5) +
                               " EMA50=" + DoubleToString(m_currentEma50, 5);
               }
            else
               {
               m_lastSummary = "BULLISH TREND | RSI " + DoubleToString(m_currentRsi, 1) +
                               " no cross above mid+" + DoubleToString(m_margin, 1) +
                               " | waiting";
               }
            }
         else if(m_currentTrend < 0)
            {
            // downtrend: RSI crosses down through mid - margin
            if(!m_longOnly &&
               m_currentRsiPrev >= (double)m_rsiMid &&
               m_currentRsi < ((double)m_rsiMid - m_margin))
               {
               sig = SIGNAL_SELL;
               m_lastSummary = "BEARISH TREND | RSI " + DoubleToString(m_currentRsi, 1) +
                               " crossed below mid-" + DoubleToString(m_margin, 1) +
                               " | EMA21=" + DoubleToString(m_currentEma21, 5) +
                               " EMA50=" + DoubleToString(m_currentEma50, 5);
               }
            else if(m_longOnly)
               {
               m_lastSummary = "BEARISH TREND | long-only mode, skipping short";
               }
            else
               {
               m_lastSummary = "BEARISH TREND | RSI " + DoubleToString(m_currentRsi, 1) +
                               " no cross below mid-" + DoubleToString(m_margin, 1) +
                               " | waiting";
               }
            }
         else
            {
            m_lastSummary = "FLAT/NEUTRAL | EMA21/EMA50 not aligned | deadband=" +
                            DoubleToString(m_deadband, 2);
            }

         return sig;
         }

      virtual string GetEvaluationSummary() const { return m_lastSummary; }
      virtual string Name() const { return "RSI Band"; }
      virtual void Reset()
         {
         m_currentTrend = 0;
         m_currentRsi = 0;
         m_currentRsiPrev = 0;
         m_currentEma21 = 0;
         m_currentEma50 = 0;
         m_currentAtr = 0;
         m_lastSummary = "Strategy reset";
         }

      //--- accessors for HUD
      int    CurrentTrend() const { return m_currentTrend; }
      double CurrentRsi() const { return m_currentRsi; }
      double CurrentRsiPrev() const { return m_currentRsiPrev; }
      double CurrentEma21() const { return m_currentEma21; }
      double CurrentEma50() const { return m_currentEma50; }
      double CurrentAtr() const { return m_currentAtr; }
   };
