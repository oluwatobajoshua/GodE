//+------------------------------------------------------------------+
//| CircuitBreaker.mqh — Centralized pre-trade risk engine            |
//| Validates all hard constraints before strategy evaluation         |
//+------------------------------------------------------------------+
#property strict

struct SSystemHealth
   {
   bool     isCleared = false;
   string   blockReason = "";
   double   currentValue = 0.0;
   double   thresholdValue = 0.0;
   datetime blockTime = 0;
   };

class CCircuitBreaker
   {
   private:
      SSystemHealth m_health;
      bool   m_algoTradingEnabled;
      bool   m_symbolWhitelistEnabled;
      string m_symbolWhitelist[];
      int    m_maxSpread;
      double m_maxDailyDD;
      bool   m_trafficLightEnabled;
      int    m_trafficLightMinGreen;
      bool   m_centuryHalt;

   public:
      void CCircuitBreaker()
         {
         m_algoTradingEnabled = false;
         m_symbolWhitelistEnabled = false;
         m_maxSpread = 1000;
         m_maxDailyDD = 5.0;
         m_trafficLightEnabled = false;
         m_trafficLightMinGreen = 6;
         m_centuryHalt = false;
         m_health.isCleared = false;
         }

      //--- configuration
      void SetAlgoTrading(bool v) { m_algoTradingEnabled = v; }
      void SetSymbolWhitelist(bool enabled, const string &symbols[])
         {
         m_symbolWhitelistEnabled = enabled;
         ArrayResize(m_symbolWhitelist, ArraySize(symbols));
         ArrayCopy(m_symbolWhitelist, symbols);
         }
      void SetMaxSpread(int v) { m_maxSpread = v; }
      void SetMaxDailyDD(double v) { m_maxDailyDD = v; }
      void SetTrafficLight(bool enabled, int minGreen)
         { m_trafficLightEnabled = enabled; m_trafficLightMinGreen = minGreen; }
      void SetCenturyHalt(bool v) { m_centuryHalt = v; }

      //--- evaluate all constraints, return health state
      SSystemHealth Evaluate(const string currentSymbol, int currentSpread,
                             double dailyLossPct, int trafficLightGreenCount,
                             bool trafficLightVeto)
         {
         m_health = SSystemHealth();
         m_health.blockTime = TimeCurrent();

         //--- 1. Global Algo Trading
         if(!m_algoTradingEnabled)
            {
            m_health.isCleared = false;
            m_health.blockReason = "BLOCKED: Algo Trading disabled (green button OFF)";
            m_health.currentValue = 0;
            m_health.thresholdValue = 1;
            return m_health;
            }

         //--- 2. Symbol whitelist
         if(m_symbolWhitelistEnabled)
            {
            bool found = false;
            for(int i = 0; i < ArraySize(m_symbolWhitelist); i++)
               {
               if(m_symbolWhitelist[i] == currentSymbol)
                  { found = true; break; }
               }
            if(!found)
               {
               m_health.isCleared = false;
               m_health.blockReason = "BLOCKED: Symbol '" + currentSymbol + "' not in whitelist";
               m_health.currentValue = 0;
               m_health.thresholdValue = 1;
               return m_health;
               }
            }

         //--- 3. Spread tolerance
         if(currentSpread > m_maxSpread)
            {
            m_health.isCleared = false;
            m_health.blockReason = "BLOCKED: Spread " + IntegerToString(currentSpread) +
                                   " pts > max " + IntegerToString(m_maxSpread) + " pts";
            m_health.currentValue = (double)currentSpread;
            m_health.thresholdValue = (double)m_maxSpread;
            return m_health;
            }

         //--- 4. Daily drawdown limit
         if(dailyLossPct >= m_maxDailyDD)
            {
            m_health.isCleared = false;
            m_health.blockReason = "BLOCKED: Daily DD " + DoubleToString(dailyLossPct, 2) +
                                   "% >= " + DoubleToString(m_maxDailyDD, 2) + "%";
            m_health.currentValue = dailyLossPct;
            m_health.thresholdValue = m_maxDailyDD;
            return m_health;
            }

         //--- 5. Traffic Light board
         if(m_trafficLightEnabled)
            {
            if(trafficLightVeto)
               {
               m_health.isCleared = false;
               m_health.blockReason = "BLOCKED: Traffic Light veto active";
               m_health.currentValue = (double)trafficLightGreenCount;
               m_health.thresholdValue = (double)m_trafficLightMinGreen;
               return m_health;
               }
            if(trafficLightGreenCount < m_trafficLightMinGreen)
               {
               m_health.isCleared = false;
               m_health.blockReason = "BLOCKED: Traffic Light green=" +
                                      IntegerToString(trafficLightGreenCount) +
                                      " < min " + IntegerToString(m_trafficLightMinGreen);
               m_health.currentValue = (double)trafficLightGreenCount;
               m_health.thresholdValue = (double)m_trafficLightMinGreen;
               return m_health;
               }
            }

         //--- 6. Century kill-switch
         if(m_centuryHalt)
            {
            m_health.isCleared = false;
            m_health.blockReason = "BLOCKED: Century kill-switch halt";
            m_health.currentValue = 1;
            m_health.thresholdValue = 0;
            return m_health;
            }

         m_health.isCleared = true;
         m_health.blockReason = "CLEARED";
         m_health.currentValue = 1;
         m_health.thresholdValue = 1;
         return m_health;
         }

      SSystemHealth Health() const { return m_health; }
      bool IsCleared() const { return m_health.isCleared; }
      string BlockReason() const { return m_health.blockReason; }
   };
