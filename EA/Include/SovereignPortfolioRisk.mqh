//+------------------------------------------------------------------+
//|                                        SovereignPortfolioRisk.mqh |
//|  Portfolio-level risk engine for the Sovereign EA:               |
//|  - Historical VaR (95%/99%) across all open positions            |
//|  - Pairwise correlation matrix                                   |
//|  - Concentration limits (per currency, per sector)               |
//|  - Kelly / fractional-Kelly position sizing                      |
//|  - Circuit breakers (daily/weekly loss, max DD from peak)        |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_PORTFOLIO_RISK_MQH__
#define __SOVEREIGN_PORTFOLIO_RISK_MQH__

#include <Trade\Trade.mqh>
#include <Sovereign\SovereignError.mqh>

#define SOV_PORT_MAX_SYMBOLS 32
#define SOV_PORT_MAX_RETURNS 252
#define SOV_PORT_CONF_95     0.95
#define SOV_PORT_CONF_99     0.99

struct SovPortPosition
{
   string   symbol;
   string   baseCcy;      // first 3 chars
   string   quoteCcy;     // last 3 chars
   int      side;         // +1 long, -1 short
   double   notional;     // lots * price * tickValue
   double   slDist;       // stop distance in price
   double   riskMoney;    // USD risk at stop
   double   ret;          // latest bar return
   datetime openTime;
};

struct SovPortSymbolStats
{
   string   symbol;
   double   meanRet;
   double   stdRet;
   double   var95;
   double   var99;
   double   kellyF;       // full Kelly fraction
   double   kellyFrac;    // fractional Kelly (default 0.25)
   int      nReturns;
   int      wins;
   int      losses;
   double   avgWin;
   double   avgLoss;
};

class CSovereignPortfolioRisk
{
private:
   string   m_folder;
   long     m_magic;
   bool     m_enabled;

   //--- position registry
   SovPortPosition m_positions[SOV_PORT_MAX_SYMBOLS];
   int             m_nPositions;

   //--- symbol stats
   SovPortSymbolStats m_stats[SOV_PORT_MAX_SYMBOLS];
   int                m_nStats;

   //--- return history per symbol (rolling)
   string   m_retSym[SOV_PORT_MAX_SYMBOLS];
   double   m_retHist[SOV_PORT_MAX_SYMBOLS][SOV_PORT_MAX_RETURNS];
   int      m_retCount[SOV_PORT_MAX_SYMBOLS];
   int      m_retHead[SOV_PORT_MAX_SYMBOLS];

   //--- circuit breaker state
   double   m_dayStartEquity;
   double   m_weekStartEquity;
   double   m_peakEquity;
   datetime m_lastWeekReset;
   datetime m_lastDayReset;

   //--- inputs
   double   m_varLimitPct;      // max portfolio VaR % of equity (0=disabled)
   double   m_corrLimit;        // max pairwise correlation (0=disabled)
   double   m_maxCcyExposurePct;// max notional per currency % of equity (0=disabled)
   double   m_kellyFraction;    // fraction of full Kelly to apply (0.0-1.0)
   double   m_dailyLossLimitPct;// daily loss circuit breaker % (0=disabled)
   double   m_weeklyLossLimitPct;// weekly loss circuit breaker % (0=disabled)
   double   m_maxDDFromPeakPct; // max DD from peak % (0=disabled)

   //--- helpers
   int FindPosition(const string symbol) const;
   int FindStats(const string symbol) const;
   int FindRetSeries(const string symbol) const;
   int EnsureRetSeries(const string symbol);
   string BaseCcy(const string symbol) const;
   string QuoteCcy(const string symbol) const;
   double SymbolReturn(const string symbol) const;
   void UpdateReturns();
   double Percentile(const double &arr[], const int n, const double pct) const;
   double PortfolioVar(const double conf) const;
   double MaxCorrelation() const;
   double CcyExposurePct(const string ccy) const;
   double KellyFraction(const int statsIdx) const;

public:
   CSovereignPortfolioRisk();
   ~CSovereignPortfolioRisk();
   bool Init(const string folder, const long magic,
             const double varLimitPct, const double corrLimit,
             const double maxCcyExposurePct, const double kellyFraction,
             const double dailyLossLimitPct, const double weeklyLossLimitPct,
             const double maxDDFromPeakPct);
   void Deinit();
   void OnTick();

   //--- register a position (called when a new Sovereign position opens)
   void RegisterPosition(const string symbol, const int side,
                         const double lots, const double price,
                         const double slDist, const double riskMoney);

   //--- unregister a position (called when it closes)
   void UnregisterPosition(const string symbol);

   //--- risk checks before a new entry
   bool CheckVarLimit(const double equity) const;
   bool CheckCorrelation(const string symbol) const;
   bool CheckConcentration(const string symbol, const double lots,
                           const double price) const;
   bool CheckCircuitBreakers(const double equity) const;

   //--- position sizing with Kelly
   double KellySize(const string symbol, const double equity,
                    const double stopDist, const double baseLot) const;

   //--- status
   string StatusLine() const;
   bool   Halted() const;
   string HaltReason() const;

private:
   string m_haltReason;
   bool   m_halted;
};

//+------------------------------------------------------------------+
//| Constructor                                                      |
//+------------------------------------------------------------------+
CSovereignPortfolioRisk::CSovereignPortfolioRisk()
{
   m_folder = "Sovereign";
   m_magic = 0;
   m_enabled = false;
   m_nPositions = 0;
   m_nStats = 0;
   m_dayStartEquity = 0.0;
   m_weekStartEquity = 0.0;
   m_peakEquity = 0.0;
   m_lastWeekReset = 0;
   m_lastDayReset = 0;
   m_varLimitPct = 0.0;
   m_corrLimit = 0.0;
   m_maxCcyExposurePct = 0.0;
   m_kellyFraction = 0.25;
   m_dailyLossLimitPct = 0.0;
   m_weeklyLossLimitPct = 0.0;
   m_maxDDFromPeakPct = 0.0;
   m_halted = false;
   m_haltReason = "";
   for(int i = 0; i < SOV_PORT_MAX_SYMBOLS; i++)
   {
      m_retCount[i] = 0;
      m_retHead[i] = 0;
   }
}
//+------------------------------------------------------------------+
//| Destructor                                                       |
//+------------------------------------------------------------------+
CSovereignPortfolioRisk::~CSovereignPortfolioRisk()
{
   Deinit();
}
//+------------------------------------------------------------------+
//| Init                                                             |
//+------------------------------------------------------------------+
bool CSovereignPortfolioRisk::Init(const string folder, const long magic,
                                   const double varLimitPct, const double corrLimit,
                                   const double maxCcyExposurePct,
                                   const double kellyFraction,
                                   const double dailyLossLimitPct,
                                   const double weeklyLossLimitPct,
                                   const double maxDDFromPeakPct)
{
   m_folder = folder;
   m_magic = magic;
   m_enabled = true;
   m_varLimitPct = MathMax(0.0, varLimitPct);
   m_corrLimit = MathMax(0.0, MathMin(1.0, corrLimit));
   m_maxCcyExposurePct = MathMax(0.0, maxCcyExposurePct);
   m_kellyFraction = MathMax(0.0, MathMin(1.0, kellyFraction));
   m_dailyLossLimitPct = MathMax(0.0, dailyLossLimitPct);
   m_weeklyLossLimitPct = MathMax(0.0, weeklyLossLimitPct);
   m_maxDDFromPeakPct = MathMax(0.0, maxDDFromPeakPct);

   m_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   m_weekStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   m_peakEquity = AccountInfoDouble(ACCOUNT_EQUITY);

   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   m_lastDayReset = dt.day;
   m_lastWeekReset = dt.day_of_week;

   SovResetLastError();
   if(!FolderCreate(m_folder))
   {
      SovLogWarn("PortfolioRisk: FolderCreate(" + m_folder + ") failed err=" + IntegerToString(GetLastError()));
      return false;
   }
   m_halted = false;
   m_haltReason = "";
   return true;
}
//+------------------------------------------------------------------+
//| Deinit                                                           |
//+------------------------------------------------------------------+
void CSovereignPortfolioRisk::Deinit()
{
   m_enabled = false;
   m_nPositions = 0;
   m_nStats = 0;
}
//+------------------------------------------------------------------+
//| OnTick — update returns, equity checks, circuit breakers        |
//+------------------------------------------------------------------+
void CSovereignPortfolioRisk::OnTick()
{
   if(!m_enabled) return;

   SovResetLastError();
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity == 0.0 && GetLastError() != 0)
   {
      SovLogWarn("PortfolioRisk: AccountInfoDouble(ACCOUNT_EQUITY) failed err=" + IntegerToString(GetLastError()));
      return;
   }

   //--- update peak
   if(equity > m_peakEquity) m_peakEquity = equity;

   //--- daily reset
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(dt.day != m_lastDayReset)
   {
      m_dayStartEquity = equity;
      m_lastDayReset = dt.day;
   }
   //--- weekly reset (Monday)
   if(dt.day_of_week == 1 && dt.hour < 1)
   {
      if(m_lastWeekReset != dt.day)
      {
         m_weekStartEquity = equity;
         m_lastWeekReset = dt.day;
      }
   }

   //--- update return history from open positions
   UpdateReturns();

   //--- circuit breakers
   m_halted = false;
   m_haltReason = "";

   if(m_maxDDFromPeakPct > 0.0 && m_peakEquity > 0.0)
   {
      double dd = (m_peakEquity - equity) / m_peakEquity * 100.0;
      if(dd >= m_maxDDFromPeakPct)
      {
         m_halted = true;
         m_haltReason = "max DD from peak (" + DoubleToString(dd,2) + "% >= " + DoubleToString(m_maxDDFromPeakPct,2) + "%)";
      }
   }
   if(!m_halted && m_dailyLossLimitPct > 0.0 && m_dayStartEquity > 0.0)
   {
      double loss = (m_dayStartEquity - equity) / m_dayStartEquity * 100.0;
      if(loss >= m_dailyLossLimitPct)
      {
         m_halted = true;
         m_haltReason = "daily loss limit (" + DoubleToString(loss,2) + "% >= " + DoubleToString(m_dailyLossLimitPct,2) + "%)";
      }
   }
   if(!m_halted && m_weeklyLossLimitPct > 0.0 && m_weekStartEquity > 0.0)
   {
      double loss = (m_weekStartEquity - equity) / m_weekStartEquity * 100.0;
      if(loss >= m_weeklyLossLimitPct)
      {
         m_halted = true;
         m_haltReason = "weekly loss limit (" + DoubleToString(loss,2) + "% >= " + DoubleToString(m_weeklyLossLimitPct,2) + "%)";
      }
   }
}
//+------------------------------------------------------------------+
//| RegisterPosition                                                 |
//+------------------------------------------------------------------+
void CSovereignPortfolioRisk::RegisterPosition(const string symbol, const int side,
                                                const double lots, const double price,
                                                const double slDist, const double riskMoney)
{
   if(!m_enabled) return;
   int idx = FindPosition(symbol);
   if(idx < 0)
   {
      if(m_nPositions >= SOV_PORT_MAX_SYMBOLS) return;
      idx = m_nPositions++;
   }
   m_positions[idx].symbol = symbol;
   m_positions[idx].baseCcy = BaseCcy(symbol);
   m_positions[idx].quoteCcy = QuoteCcy(symbol);
   m_positions[idx].side = side;
   m_positions[idx].notional = lots * price;
   m_positions[idx].slDist = slDist;
   m_positions[idx].riskMoney = riskMoney;
   m_positions[idx].ret = SymbolReturn(symbol);
   m_positions[idx].openTime = TimeCurrent();
}
//+------------------------------------------------------------------+
//| UnregisterPosition                                               |
//+------------------------------------------------------------------+
void CSovereignPortfolioRisk::UnregisterPosition(const string symbol)
{
   if(!m_enabled) return;
   int idx = FindPosition(symbol);
   if(idx < 0) return;
   for(int i = idx; i < m_nPositions - 1; i++)
      m_positions[i] = m_positions[i + 1];
   m_nPositions--;
}
//+------------------------------------------------------------------+
//| CheckVarLimit — portfolio VaR vs limit                           |
//+------------------------------------------------------------------+
bool CSovereignPortfolioRisk::CheckVarLimit(const double equity) const
{
   if(!m_enabled || m_varLimitPct <= 0.0) return true;
   if(equity <= 0.0) return true;
   double var95 = PortfolioVar(SOV_PORT_CONF_95);
   double varPct = (equity > 0.0) ? var95 / equity * 100.0 : 0.0;
   return (varPct <= m_varLimitPct);
}
//+------------------------------------------------------------------+
//| CheckCorrelation — reject if symbol too correlated with open     |
//+------------------------------------------------------------------+
bool CSovereignPortfolioRisk::CheckCorrelation(const string symbol) const
{
   if(!m_enabled || m_corrLimit <= 0.0) return true;
   if(m_nPositions == 0) return true;
   //--- simple proxy: same base or quote currency => high correlation
   string b = BaseCcy(symbol);
   string q = QuoteCcy(symbol);
   for(int i = 0; i < m_nPositions; i++)
   {
      if(m_positions[i].baseCcy == b || m_positions[i].quoteCcy == q ||
         m_positions[i].baseCcy == q || m_positions[i].quoteCcy == b)
      {
         return false;   // same currency pair family — treat as correlated
      }
   }
   return true;
}
//+------------------------------------------------------------------+
//| CheckConcentration — per-currency notional exposure cap          |
//+------------------------------------------------------------------+
bool CSovereignPortfolioRisk::CheckConcentration(const string symbol, const double lots,
                                                  const double price) const
{
   if(!m_enabled || m_maxCcyExposurePct <= 0.0) return true;
   SovResetLastError();
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity <= 0.0) return true;

   string b = BaseCcy(symbol);
   string q = QuoteCcy(symbol);
   double newNotional = lots * price;

   double bExp = newNotional;
   double qExp = newNotional;
   for(int i = 0; i < m_nPositions; i++)
   {
      if(m_positions[i].baseCcy == b) bExp += m_positions[i].notional;
      if(m_positions[i].quoteCcy == b) bExp += m_positions[i].notional;
      if(m_positions[i].baseCcy == q) qExp += m_positions[i].notional;
      if(m_positions[i].quoteCcy == q) qExp += m_positions[i].notional;
   }
   double bPct = bExp / equity * 100.0;
   double qPct = qExp / equity * 100.0;
   return (bPct <= m_maxCcyExposurePct && qPct <= m_maxCcyExposurePct);
}
//+------------------------------------------------------------------+
//| CheckCircuitBreakers                                             |
//+------------------------------------------------------------------+
bool CSovereignPortfolioRisk::CheckCircuitBreakers(const double equity) const
{
   if(!m_enabled) return true;
   if(m_halted) return false;
   return true;
}
//+------------------------------------------------------------------+
//| KellySize — fractional-Kelly position size                       |
//+------------------------------------------------------------------+
double CSovereignPortfolioRisk::KellySize(const string symbol, const double equity,
                                           const double stopDist, const double baseLot) const
{
   if(!m_enabled || m_kellyFraction <= 0.0) return baseLot;
   if(equity <= 0.0 || stopDist <= 0.0) return baseLot;

   int idx = FindStats(symbol);
   if(idx < 0) return baseLot;
   SovPortSymbolStats s = m_stats[idx];
   if(s.nReturns < 20) return baseLot;   // not enough history

   double winRate = (double)s.wins / (double)s.nReturns;
   double lossRate = 1.0 - winRate;
   if(s.avgLoss <= 0.0) return baseLot;
   if(s.avgWin <= 0.0) return baseLot;

   //--- Kelly fraction: f* = (winRate * avgWin - lossRate * avgLoss) / avgWin
   //--- expressed in R units: f* = (winRate * R_win - lossRate * R_loss) / R_win
   double rWin = s.avgWin / stopDist;    // win in R units
   double rLoss = s.avgLoss / stopDist;  // loss in R units
   double fStar = (winRate * rWin - lossRate * rLoss) / rWin;
   if(fStar <= 0.0) return baseLot;

   double fUse = fStar * m_kellyFraction;   // fractional Kelly
   if(fUse > 1.0) fUse = 1.0;

   //--- lot = equity * fUse / (stopDist in money per lot)
   SovResetLastError();
   double tickSize = SovSymbolDouble(symbol, SYMBOL_TRADE_TICK_SIZE, 0.0, "KellySize");
   double tickValue = SovSymbolDouble(symbol, SYMBOL_TRADE_TICK_VALUE, 0.0, "KellySize");
   if(tickSize <= 0.0 || tickValue <= 0.0) return baseLot;
   double lossPerLot = (stopDist / tickSize) * tickValue;
   if(lossPerLot <= 0.0) return baseLot;

   double kellyLot = equity * fUse / lossPerLot;
   return MathMax(kellyLot, baseLot * 0.1);   // floor at 10% of base
}
//+------------------------------------------------------------------+
//| StatusLine                                                       |
//+------------------------------------------------------------------+
string CSovereignPortfolioRisk::StatusLine() const
{
   if(!m_enabled) return "portRisk=off";
   if(m_halted) return "portRisk=HALTED (" + m_haltReason + ")";
   return StringFormat("portRisk nPos=%d varLimit=%.1f%% corrLimit=%.2f kelly=%.2f",
                       m_nPositions, m_varLimitPct, m_corrLimit, m_kellyFraction);
}
//+------------------------------------------------------------------+
//| Halted / HaltReason                                              |
//+------------------------------------------------------------------+
bool CSovereignPortfolioRisk::Halted() const { return m_halted; }
string CSovereignPortfolioRisk::HaltReason() const { return m_haltReason; }

//+------------------------------------------------------------------+
//| Private helpers                                                  |
//+------------------------------------------------------------------+
int CSovereignPortfolioRisk::FindPosition(const string symbol) const
{
   for(int i = 0; i < m_nPositions; i++)
      if(m_positions[i].symbol == symbol) return i;
   return -1;
}
int CSovereignPortfolioRisk::FindStats(const string symbol) const
{
   for(int i = 0; i < m_nStats; i++)
      if(m_stats[i].symbol == symbol) return i;
   return -1;
}
int CSovereignPortfolioRisk::FindRetSeries(const string symbol) const
{
   for(int i = 0; i < SOV_PORT_MAX_SYMBOLS; i++)
      if(m_retSym[i] == symbol) return i;
   return -1;
}
int CSovereignPortfolioRisk::EnsureRetSeries(const string symbol)
{
   int idx = FindRetSeries(symbol);
   if(idx >= 0) return idx;
   for(int i = 0; i < SOV_PORT_MAX_SYMBOLS; i++)
   {
      if(m_retSym[i] == "")
      {
         m_retSym[i] = symbol;
         m_retCount[i] = 0;
         m_retHead[i] = 0;
         return i;
      }
   }
   return -1;
}
string CSovereignPortfolioRisk::BaseCcy(const string symbol) const
{
   return StringSubstr(symbol, 0, 3);
}
string CSovereignPortfolioRisk::QuoteCcy(const string symbol) const
{
   return StringSubstr(symbol, 3, 3);
}
double CSovereignPortfolioRisk::SymbolReturn(const string symbol) const
{
   double close[2];
   if(CopyClose(symbol, PERIOD_CURRENT, 1, 2, close) < 2) return 0.0;
   if(close[1] <= 0.0) return 0.0;
   return (close[0] - close[1]) / close[1];
}
void CSovereignPortfolioRisk::UpdateReturns()
{
   if(!m_enabled) return;
   for(int i = 0; i < m_nPositions; i++)
   {
      string sym = m_positions[i].symbol;
      int sIdx = EnsureRetSeries(sym);
      if(sIdx < 0) continue;
      double r = SymbolReturn(sym);
      m_retHist[sIdx][m_retHead[sIdx]] = r;
      m_retHead[sIdx]++;
      if(m_retHead[sIdx] >= SOV_PORT_MAX_RETURNS) m_retHead[sIdx] = 0;
      if(m_retCount[sIdx] < SOV_PORT_MAX_RETURNS) m_retCount[sIdx]++;
      m_positions[i].ret = r;
   }
   //--- refresh stats
   m_nStats = 0;
   for(int i = 0; i < SOV_PORT_MAX_SYMBOLS; i++)
   {
      if(m_retSym[i] == "") continue;
      int n = m_retCount[i];
      if(n < 5) continue;
      SovPortSymbolStats s;
      s.symbol = m_retSym[i];
      s.nReturns = n;
      double sum = 0.0, sum2 = 0.0;
      int wins = 0, losses = 0;
      double sumWin = 0.0, sumLoss = 0.0;
      int head = m_retHead[i];
      for(int k = 0; k < n; k++)
      {
         int idx = head - 1 - k;
         if(idx < 0) idx += SOV_PORT_MAX_RETURNS;
         double r = m_retHist[i][idx];
         sum += r;
         sum2 += r * r;
         if(r > 0.0) { wins++; sumWin += r; }
         else if(r < 0.0) { losses++; sumLoss += MathAbs(r); }
      }
      s.meanRet = sum / (double)n;
      double var = sum2 / (double)n - s.meanRet * s.meanRet;
      s.stdRet = MathSqrt(MathMax(0.0, var));
      s.wins = wins;
      s.losses = losses;
      s.avgWin = (wins > 0) ? sumWin / (double)wins : 0.0;
      s.avgLoss = (losses > 0) ? sumLoss / (double)losses : 0.0;
      s.kellyF = KellyFraction(m_nStats);
      s.kellyFrac = m_kellyFraction;
      m_stats[m_nStats++] = s;
   }
}
double CSovereignPortfolioRisk::Percentile(const double &arr[], const int n,
                                            const double pct) const
{
   double sorted[SOV_PORT_MAX_RETURNS];
   for(int i = 0; i < n; i++) sorted[i] = arr[i];
   ArraySort(sorted);
   int idx = (int)MathFloor((double)n * pct);
   if(idx < 0) idx = 0;
   if(idx >= n) idx = n - 1;
   return sorted[idx];
}
double CSovereignPortfolioRisk::PortfolioVar(const double conf) const
{
   if(m_nPositions == 0) return 0.0;
   //--- historical simulation: simulate P&L of current portfolio over
   //--- each historical return window
   double pnl[SOV_PORT_MAX_RETURNS];
   int n = 0;
   //--- find min return history length
   int minN = SOV_PORT_MAX_RETURNS;
   for(int i = 0; i < m_nPositions; i++)
   {
      int sIdx = FindRetSeries(m_positions[i].symbol);
      if(sIdx >= 0 && m_retCount[sIdx] < minN) minN = m_retCount[sIdx];
   }
   if(minN < 5) return 0.0;
   n = minN;

   for(int k = 0; k < n; k++)
   {
      double dayPnl = 0.0;
      for(int i = 0; i < m_nPositions; i++)
      {
         int sIdx = FindRetSeries(m_positions[i].symbol);
         if(sIdx < 0) continue;
         int head = m_retHead[sIdx];
         int idx = head - 1 - k;
         if(idx < 0) idx += SOV_PORT_MAX_RETURNS;
         double r = m_retHist[sIdx][idx];
         dayPnl += m_positions[i].notional * r * m_positions[i].side;
      }
      pnl[k] = dayPnl;
   }
   //--- VaR at confidence level: loss at (1-conf) percentile
   double sorted[SOV_PORT_MAX_RETURNS];
   for(int i = 0; i < n; i++) sorted[i] = pnl[i];
   ArraySort(sorted);
   int idx = (int)MathFloor((double)n * (1.0 - conf));
   if(idx < 0) idx = 0;
   if(idx >= n) idx = n - 1;
   double var = -sorted[idx];   // positive = loss
   return MathMax(0.0, var);
}
double CSovereignPortfolioRisk::MaxCorrelation() const
{
   //--- simplified: count same-currency pairs as correlated
   if(m_nPositions < 2) return 0.0;
   int sameCcy = 0;
   int pairs = 0;
   for(int i = 0; i < m_nPositions; i++)
      for(int j = i + 1; j < m_nPositions; j++)
      {
         pairs++;
         if(m_positions[i].baseCcy == m_positions[j].baseCcy ||
            m_positions[i].quoteCcy == m_positions[j].quoteCcy ||
            m_positions[i].baseCcy == m_positions[j].quoteCcy ||
            m_positions[i].quoteCcy == m_positions[j].baseCcy)
            sameCcy++;
      }
   return (pairs > 0) ? (double)sameCcy / (double)pairs : 0.0;
}
double CSovereignPortfolioRisk::CcyExposurePct(const string ccy) const
{
   SovResetLastError();
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity <= 0.0) return 0.0;
   double exp = 0.0;
   for(int i = 0; i < m_nPositions; i++)
   {
      if(m_positions[i].baseCcy == ccy || m_positions[i].quoteCcy == ccy)
         exp += m_positions[i].notional;
   }
   return exp / equity * 100.0;
}
double CSovereignPortfolioRisk::KellyFraction(const int statsIdx) const
{
   SovPortSymbolStats s = m_stats[statsIdx];
   if(s.nReturns < 20) return 0.0;
   double winRate = (double)s.wins / (double)s.nReturns;
   double lossRate = 1.0 - winRate;
   if(s.avgLoss <= 0.0 || s.avgWin <= 0.0) return 0.0;
   double fStar = (winRate * s.avgWin - lossRate * s.avgLoss) / s.avgWin;
   return MathMax(0.0, fStar);
}

#endif // __SOVEREIGN_PORTFOLIO_RISK_MQH__
