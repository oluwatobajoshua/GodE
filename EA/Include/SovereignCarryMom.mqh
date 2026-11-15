//+------------------------------------------------------------------+
//|                                         SovereignCarryMom.mqh    |
//|  Monthly G10 carry + 12m momentum; Stack v1: neutral book + VIX   |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_CARRY_MOM_MQH__
#define __SOVEREIGN_CARRY_MOM_MQH__

#include <Trade/PositionInfo.mqh>
#include <Sovereign\SovereignCentury.mqh>
#include <Sovereign\SovereignError.mqh>

#define CARRY_LEG_MAX 32
// Calendar days at the start of the month in which a monthly rebalance may still
// execute (the first tick of the month lands on the rollover: wide spreads,
// closed markets). Weekends inside the window are skipped, not consumed, so a
// month that opens on a Saturday still gets its trading days. Kept short so the
// book never drifts into a mid-month entry.
#define CARRY_ENTRY_GRACE_DAYS 7

class CSovereignCarryMom
{
private:
   struct CarryLeg
   {
      string symbol;
      string rateCode;
      int    ccySign;
   };

   CarryLeg m_legs[CARRY_LEG_MAX];
   int      m_n;
   int      m_topN;
   double   m_riskPerLeg;
   double   m_maxTotalRisk;
   int      m_momBars;
   int      m_maxSpread;
   long     m_magic;

   int      m_lastMonthKey;
   datetime m_lastAttemptTime;
   int      m_lastAttemptMonthKey;
   int      m_book[CARRY_LEG_MAX];
   int      m_bookMonthKey;
   bool     m_bookEntries;
   double   m_bookVixScale;
   bool     m_momDiagDone;
   bool     m_rankDiagDone;

   bool     m_useStackV1;
   bool     m_requireNeutral;
   double   m_vixLo;
   double   m_vixHi;
   double   m_vixMidScale;
   bool     m_stackDdHaltEnable;
   double   m_stackDdHaltPct;
   double   m_peakEquity;
   bool     m_stackDdHalted;

//--- Stop loss control (research holds to month-end with no stop; EA can opt in)
   bool     m_useStopLoss;

//--- Volatility targeting (matches research/carry.py: 10% annual target,
//--- rolling 12-month vol, max 2x scalar).  Scales all legs equally so the
//--- portfolio vol stays near the target and drawdowns are smoother.
   bool     m_volTargetEnabled;
   double   m_volTarget;        // annual vol target (0.10 = 10%)
   int      m_volLookback;      // months of rolling vol
   double   m_volMaxScalar;     // max vol scalar (2.0)
   double   m_volScalar;        // current vol scalar (cached)
   double   m_legReturns[];     // per-leg D1 returns for vol calc
   int      m_volWarmupBars;    // bars needed before vol targeting is active

   int MonthKey(datetime t) const
   {
      if(t <= 0)
         return 0;
      MqlDateTime dt;
      TimeToStruct(t, dt);
      return dt.year * 100 + dt.mon;
   }

   datetime MonthStart(datetime t) const
   {
      MqlDateTime dt;
      TimeToStruct(t, dt);
      dt.day = 1;
      dt.hour = 0;
      dt.min = 0;
      dt.sec = 0;
      return StructToTime(dt);
   }

   bool ReadLatestRate(const string code, const datetime before, double &rate) const
   {
      rate = 0.0;
      string path = "Sovereign\\rates\\" + code + ".csv";
      SovResetLastError();
      int h = FileOpen(path, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
      if(h == INVALID_HANDLE)
      {
         SovLogWarn("ReadLatestRate[" + code + "]: cannot open " + path + " err=" + IntegerToString(GetLastError()));
         return false;
      }

      bool found = false;
      datetime bestD = 0;
      while(!FileIsEnding(h))
      {
         string line = FileReadString(h);
         if(line == "" || StringFind(line, "date") >= 0)
            continue;
         string parts[];
         int np = StringSplit(line, ',', parts);
         if(np < 2)
            continue;
         datetime d = StringToTime(parts[0]);
         if(d <= 0 || d >= before || d < bestD)
            continue;
         bestD = d;
         rate = StringToDouble(parts[1]);
         found = true;
      }
      SovResetLastError();
      FileClose(h);
      if(GetLastError() != 0)
         SovLogWarn("ReadLatestRate[" + code + "]: FileClose failed err=" + IntegerToString(GetLastError()));
      return found;
   }

   bool ReadLatestVix(const datetime before, double &vix) const
   {
      vix = 0.0;
      SovResetLastError();
      int h = FileOpen("Sovereign\\rates\\VIX.csv", FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
      if(h == INVALID_HANDLE)
      {
         SovLogWarn("ReadLatestVix: cannot open VIX.csv err=" + IntegerToString(GetLastError()));
         return false;
      }

      bool found = false;
      datetime bestD = 0;
      while(!FileIsEnding(h))
      {
         string line = FileReadString(h);
         if(line == "" || StringFind(line, "observation_date") >= 0 || StringFind(line, "date") == 0)
            continue;
         string parts[];
         int np = StringSplit(line, ',', parts);
         if(np < 2)
            continue;
         datetime d = StringToTime(parts[0]);
         if(d <= 0 || d >= before || d < bestD)
            continue;
         bestD = d;
         vix = StringToDouble(parts[1]);
         found = (vix > 0.0);
      }
      SovResetLastError();
      FileClose(h);
      if(GetLastError() != 0)
         SovLogWarn("ReadLatestVix: FileClose failed err=" + IntegerToString(GetLastError()));
      return found;
   }

   double VixExposureScale(const datetime beforeMonth) const
   {
      if(!m_useStackV1)
         return 1.0;
      double vix = 0.0;
      if(!ReadLatestVix(beforeMonth, vix))
         return 1.0;
      if(vix > m_vixHi)
         return 0.0;
      if(vix > m_vixLo)
         return m_vixMidScale;
      return 1.0;
   }

   void UpdateStackDdHalt(const double equity)
   {
      if(!m_useStackV1 || !m_stackDdHaltEnable)
      {
         m_stackDdHalted = false;
         return;
      }
      if(m_peakEquity <= 0.0)
         m_peakEquity = equity;
      if(equity > m_peakEquity)
         m_peakEquity = equity;
      double ddPct = (equity - m_peakEquity) / m_peakEquity * 100.0;
      if(!m_stackDdHalted && ddPct <= -m_stackDdHaltPct)
         m_stackDdHalted = true;
      if(m_stackDdHalted && ddPct >= -m_stackDdHaltPct * 0.5)
         m_stackDdHalted = false;
   }

   void ApplyNeutralBookFilter(int &target[]) const
   {
      if(!m_useStackV1 || !m_requireNeutral)
         return;
      int nLong = 0;
      int nShort = 0;
      for(int i = 0; i < m_n; i++)
      {
         if(target[i] > 0)
            nLong++;
         else if(target[i] < 0)
            nShort++;
      }
      if(nLong == 0 || nShort == 0)
         ArrayInitialize(target, 0);
   }

   double SwapCarryScore(const string sym) const
   {
      double swapBuy  = SovSymbolDouble(sym, SYMBOL_SWAP_LONG, 0.0, "SwapCarryScore");
      double swapSell = SovSymbolDouble(sym, SYMBOL_SWAP_SHORT, 0.0, "SwapCarryScore");
      return swapBuy - swapSell;
   }

   bool RateDiffs(double &diff[])
   {
      ArrayResize(diff, m_n);
      ArrayInitialize(diff, 0.0);
      datetime priorMonth = MonthStart(TimeCurrent());

      double usd = 0.0;
      if(!ReadLatestRate("US", priorMonth, usd))
         usd = 0.0;

      int valid = 0;
      for(int i = 0; i < m_n; i++)
      {
         double r = 0.0;
         if(ReadLatestRate(m_legs[i].rateCode, priorMonth, r))
         {
            diff[i] = r - usd;
            valid++;
         }
         else
            diff[i] = SwapCarryScore(m_legs[i].symbol);
      }
      return (valid > 0 || m_n > 0);
   }

   double Mom12(const int idx) const
   {
      const string sym = m_legs[idx].symbol;
      const int sign = m_legs[idx].ccySign;
      double close[];
      ArraySetAsSeries(close, true);
      int need = m_momBars + 2;
      SovResetLastError();
      int bars = CopyClose(sym, PERIOD_D1, 1, need, close);
      if(bars <= 0)
      {
         SovLogWarn("Mom12: CopyClose(" + sym + ") failed err=" + IntegerToString(GetLastError()));
         return 0.0;
      }
      if(bars < m_momBars + 1)
         return 0.0;
      double p0 = close[0];
      double p1 = close[m_momBars];
      if(p1 <= 0.0)
         return 0.0;
      double ret = (p0 - p1) / p1;
      if(sign < 0)
         ret = -ret;
      return ret;
   }

   double NormalizeVolume(const string sym, double volume) const
   {
      double vmin  = SovSymbolDouble(sym, SYMBOL_VOLUME_MIN, 0.01, "NormalizeVolume");
      double vmax  = SovSymbolDouble(sym, SYMBOL_VOLUME_MAX, 100.0, "NormalizeVolume");
      double vstep = SovSymbolDouble(sym, SYMBOL_VOLUME_STEP, 0.01, "NormalizeVolume");
      if(vstep <= 0.0)
         vstep = 0.01;
      volume = MathFloor(volume / vstep) * vstep;
      if(volume < vmin)
         return 0.0;
      if(volume > vmax)
         volume = vmax;
      return volume;
   }

   double QuoteToUsd(const string sym, const double price) const
   {
      if(StringLen(sym) < 6)
         return 1.0;
      string quote = StringSubstr(sym, 3, 3);
      if(quote == "USD")
         return 1.0;
      string direct = quote + "USD";
      SovResetLastError();
      if(SymbolSelect(direct, true))
      {
         double p = SovSymbolDouble(direct, SYMBOL_BID, 0.0, "QuoteToUsd");
         if(p > 0.0)
            return p;
      }
      if(price > 0.0)
         return 1.0 / price;
      return 1.0;
   }

   double LossPerLot(const string sym, const double stopDist) const
   {
      if(stopDist <= 0.0)
         return 0.0;
      double tickValue = SovSymbolDouble(sym, SYMBOL_TRADE_TICK_VALUE, 0.0, "LossPerLot");
      double tickSize  = SovSymbolDouble(sym, SYMBOL_TRADE_TICK_SIZE, 0.0, "LossPerLot");
      if(tickValue > 0.0 && tickSize > 0.0)
         return (stopDist / tickSize) * tickValue;
      SovResetLastError();
      double contract = SovSymbolDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE, 0.0, "LossPerLot");
      double price = SovSymbolDouble(sym, SYMBOL_BID, 0.0, "LossPerLot");
      if(contract > 0.0)
         return stopDist * contract * QuoteToUsd(sym, price);
      return 0.0;
   }

   double LotsForRisk(const string sym, const double riskMoney, const double stopDist) const
   {
      if(riskMoney <= 0.0 || stopDist <= 0.0)
         return 0.0;
      double lossPerLot = LossPerLot(sym, stopDist);
      if(lossPerLot <= 0.0)
         return 0.0;
      return NormalizeVolume(sym, riskMoney / lossPerLot);
   }

   int PositionSide(const string sym) const
   {
      CPositionInfo pos;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!pos.SelectByIndex(i))
            continue;
         if(pos.Magic() != m_magic || pos.Symbol() != sym)
            continue;
         if(pos.PositionType() == POSITION_TYPE_BUY)
            return 1;
         if(pos.PositionType() == POSITION_TYPE_SELL)
            return -1;
      }
      return 0;
   }

   void CloseLeg(const string sym, CTrade &t) const
   {
      CPositionInfo pos;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!pos.SelectByIndex(i))
            continue;
         if(pos.Magic() != m_magic || pos.Symbol() != sym)
            continue;
         SovResetLastError();
         if(!t.PositionClose(pos.Ticket()))
            SovLogTradeRetcode(t, "CloseLeg PositionClose", sym);
      }
   }

   // Ranks every leg by carry (descending). Selection must advance past legs
   // that fail the momentum filter; otherwise one rejected leg is re-picked
   // forever and the whole book stays empty.
   void RankByCarry(const double &diff[], int &order[]) const
   {
      ArrayResize(order, m_n);
      for(int i = 0; i < m_n; i++)
         order[i] = i;
      for(int i = 1; i < m_n; i++)
      {
         int key = order[i];
         int j = i - 1;
         while(j >= 0 && diff[order[j]] < diff[key])
         {
            order[j + 1] = order[j];
            j--;
         }
         order[j + 1] = key;
      }
   }

   string RateCodeFor(const string sym) const
   {
      if(sym == "EURUSD") return "EZ";
      if(sym == "GBPUSD") return "GB";
      if(sym == "USDJPY") return "JP";
      if(sym == "USDCHF") return "CH";
      if(sym == "AUDUSD") return "AU";
      if(sym == "USDCAD") return "CA";
      if(sym == "NZDUSD") return "NZ";
      return "";
   }

   int DetectCcySign(const string sym) const
   {
      if(StringFind(sym, "USD") == 0 && StringLen(sym) > 3)
         return -1;
      return 1;
   }

   void BuildTargets(const int topN, int &target[])
   {
      ArrayResize(target, m_n);
      ArrayInitialize(target, 0);

      double diff[];
      if(!RateDiffs(diff))
         return;

      double mom[];
      ArrayResize(mom, m_n);
      int barsOk = 0;
      for(int i = 0; i < m_n; i++)
      {
         mom[i] = Mom12(i);
         double probe[];
         ArraySetAsSeries(probe, true);
         if(CopyClose(m_legs[i].symbol, PERIOD_D1, 1, m_momBars + 2, probe) >= m_momBars + 1)
            barsOk++;
      }
      if(!m_momDiagDone)
      {
         m_momDiagDone = true;
         PrintFormat("Sovereign carry diag: %d/%d legs have %d D1 bars for momentum",
                     barsOk, m_n, m_momBars + 1);
         if(barsOk < m_n)
            Print("Sovereign carry diag: momentum unavailable for some legs. "
                  "The Strategy Tester loads price history only for the chart "
                  "symbol, so the basket cannot be evaluated in a single-symbol "
                  "test. Run this basket from Python research or a live/demo chart.");
      }

      // `diff` is (foreign rate - USD rate) and `mom` is the non-USD currency's
      // 12m momentum, so both live in "non-USD currency" space.  Carry means
      // long the highest-yielding names and short the lowest-yielding ones
      // (matches research/carry.py carry_weights: rank descending, long top_n).
      // `ccySign` turns "long the foreign currency" into a pair side:
      //    EURUSD (ccySign +1): long EUR -> buy
      //    USDJPY (ccySign -1): long JPY -> sell
      int nPick = MathMax(1, MathMin(6, topN));
      int order[];
      RankByCarry(diff, order);

      if(!m_rankDiagDone)
      {
         m_rankDiagDone = true;
         for(int k = 0; k < m_n; k++)
         {
            int idx = order[k];
            PrintFormat("Sovereign carry rank %d/%d: %s rate=%s diff=%.3f mom=%.3f sign=%d",
                        k + 1, m_n, m_legs[idx].symbol,
                        (m_legs[idx].rateCode == "" ? "-" : m_legs[idx].rateCode),
                        diff[idx], mom[idx], m_legs[idx].ccySign);
         }
      }

      // Longs: walk from the highest carry down, taking legs whose foreign
      // currency is also trending up, until nPick are filled.
      int taken = 0;
      for(int k = 0; k < m_n && taken < nPick; k++)
      {
         int idx = order[k];
         if(mom[idx] > 0.0)
         {
            target[idx] = m_legs[idx].ccySign;
            taken++;
         }
      }

      // Shorts: walk from the lowest carry up, skipping legs already used.
      taken = 0;
      for(int k = m_n - 1; k >= 0 && taken < nPick; k--)
      {
         int idx = order[k];
         if(target[idx] != 0)
            continue;
         if(mom[idx] < 0.0)
         {
            target[idx] = -m_legs[idx].ccySign;
            taken++;
         }
      }
   }

public:
   CSovereignCarryMom()
   {
      m_n = 0;
      m_topN = 3;
      m_riskPerLeg = 1.0;
      m_maxTotalRisk = 6.0;
      m_momBars = 252;
      m_maxSpread = 30;
      m_magic = 0;
      m_lastMonthKey = 0;
      m_lastAttemptTime = 0;
      m_lastAttemptMonthKey = 0;
      ArrayInitialize(m_book, 0);
      m_bookMonthKey = 0;
      m_bookEntries = false;
      m_bookVixScale = 1.0;
      m_momDiagDone = false;
      m_rankDiagDone = false;
      m_useStackV1 = true;
      m_requireNeutral = true;
      m_vixLo = 20.0;
      m_vixHi = 30.0;
      m_vixMidScale = 0.75;
      m_stackDdHaltEnable = true;
      m_stackDdHaltPct = 20.0;
      m_peakEquity = 0.0;
      m_stackDdHalted = false;
      m_useStopLoss = false;
      m_volTargetEnabled = true;
      m_volTarget = 0.10;
      m_volLookback = 12;
      m_volMaxScalar = 2.0;
      m_volScalar = 1.0;
      ArrayInitialize(m_legReturns, 0.0);
      m_volWarmupBars = m_volLookback * 21;  // ~21 trading days per month
   }

   void ConfigureStackV1(const bool enable, const bool requireNeutral,
                       const double vixLo, const double vixHi, const double vixMidScale,
                       const bool ddHaltEnable, const double ddHaltPct)
   {
      m_useStackV1 = enable;
      m_requireNeutral = requireNeutral;
      m_vixLo = vixLo;
      m_vixHi = vixHi;
      m_vixMidScale = vixMidScale;
      m_stackDdHaltEnable = ddHaltEnable;
      m_stackDdHaltPct = MathMax(5.0, ddHaltPct);
   }

   void ConfigureVolTargeting(const bool enable,
                              const double volTarget,
                              const int lookbackMonths,
                              const double maxScalar)
   {
      m_volTargetEnabled = enable;
      m_volTarget      = volTarget;
      m_volLookback    = lookbackMonths;
      m_volMaxScalar   = maxScalar;
      m_volScalar      = 1.0;
   }

   void ConfigureStopLoss(const bool use)
   {
      m_useStopLoss = use;
   }

   //--- Compute portfolio vol scalar from the current target weights and
   //--- per-leg D1 returns over the lookback.  Returns 1.0 if insufficient
   //--- history or vol targeting disabled.
   double ComputeVolScalar(const int &target[])
   {
      if(!m_volTargetEnabled || m_volTarget <= 0.0)
         return 1.0;

      int need = m_volLookback * 21 + 5;  // approx bars for lookback months
      double portRet[];
      ArrayResize(portRet, need);
      ArrayInitialize(portRet, 0.0);
      int validBars = 0;

      // Build per-leg return series (D1 close-to-close)
      for(int i = 0; i < m_n; i++)
      {
         if(target[i] == 0)
            continue;
         string sym = m_legs[i].symbol;
         double close[];
         ArraySetAsSeries(close, true);
         int bars = CopyClose(sym, PERIOD_D1, 1, need, close);
         if(bars < need)
            continue;
         double legRet[];
         ArrayResize(legRet, need - 1);
         for(int b = 0; b < need - 1; b++)
         {
            if(close[b] <= 0.0 || close[b + 1] <= 0.0)
               legRet[b] = 0.0;
            else
               legRet[b] = (close[b] - close[b + 1]) / close[b + 1];
         }
         // Weighted by target direction (target[i] is +1/-1/0)
         double w = (double)target[i];
         for(int b = 0; b < need - 1; b++)
            portRet[b] += w * legRet[b];
         validBars = need - 1;
      }

      if(validBars < m_volLookback * 10)
         return 1.0;  // not enough history yet

      // Rolling std of portfolio returns (simple sample std over the window)
      double mean = 0.0;
      for(int b = 0; b < validBars; b++)
         mean += portRet[b];
      mean /= validBars;
      double var = 0.0;
      for(int b = 0; b < validBars; b++)
      {
         double d = portRet[b] - mean;
         var += d * d;
      }
      var /= (validBars - 1);
      double volDaily = MathSqrt(var);
      double volAnnual = volDaily * MathSqrt(252.0);

      if(volAnnual <= 0.0)
         return 1.0;

      double scalar = m_volTarget / volAnnual;
      scalar = MathMin(scalar, m_volMaxScalar);
      scalar = MathMax(scalar, 0.0);
      return scalar;
   }

   bool InitUniverse(const string &syms[], const int symCount, const long magic,
                     const int topN, const double riskPerLeg, const double maxTotalRisk,
                     const int momBars = 252, const int maxSpread = 30)
   {
      m_magic = magic;
      m_topN = topN;
      m_riskPerLeg = riskPerLeg;
      m_maxTotalRisk = maxTotalRisk;
      m_momBars = momBars;
      m_maxSpread = maxSpread;
      m_n = 0;

      for(int i = 0; i < symCount && m_n < CARRY_LEG_MAX; i++)
      {
         string sym = syms[i];
         StringTrimLeft(sym);
         StringTrimRight(sym);
         if(sym == "")
            continue;
         SovResetLastError();
         if(!SymbolSelect(sym, true))
         {
            SovLogWarn("InitUniverse: SymbolSelect(" + sym + ") failed err=" + IntegerToString(GetLastError()));
            continue;
         }
         m_legs[m_n].symbol = sym;
         m_legs[m_n].rateCode = RateCodeFor(sym);
         m_legs[m_n].ccySign = DetectCcySign(sym);
         m_n++;
      }
      if(m_n == 0)
      {
         SovLogError("InitUniverse: no symbols selected");
         return false;
      }
      SovLogInfo("InitUniverse: universe size " + IntegerToString(m_n));
      return true;
   }

   bool IsNewMonth() const
   {
      int key = MonthKey(TimeCurrent());
      return (key != m_lastMonthKey);
   }

   // A monthly rebalance that fires on the very first tick of the month runs at
   // the rollover, when spreads spike and several pairs are still closed (MT5
   // retcode 10018). Retry on weekdays inside the first few days of the month
   // until the book is actually deployed, throttled to one attempt per hour.
   // A month that opens on a weekend must not be skipped, so weekends return
   // without expiring the window.
   bool InEntryWindow(const datetime now, bool &expired) const
   {
      MqlDateTime dt;
      TimeToStruct(now, dt);
      expired = (dt.day > CARRY_ENTRY_GRACE_DAYS);
      if(expired)
         return false;
      if(dt.day_of_week == 0 || dt.day_of_week == 6)
         return false;
      return true;
   }

   void Rebalance(const datetime now, const bool allowEntries, const double equity,
                  CSovereignPerformance &perf, CSovereignTrafficLight &tl,
                   CSovereignAuditLog &audit, CTrade &t,
                   CSovereignCentury &century)
   {
      int monthKey = MonthKey(now);
      if(monthKey == m_lastAttemptMonthKey && now - m_lastAttemptTime < 3600)
         return;
      m_lastAttemptTime = now;
      m_lastAttemptMonthKey = monthKey;

      bool expired = false;
      if(!InEntryWindow(now, expired))
      {
         if(expired)
         {
            // Entry window closed for this month: accept the current book and
            // wait for the next month rather than entering mid-month.
            m_lastMonthKey = monthKey;
         }
         return;
      }

      century.Reload(false);
      UpdateStackDdHalt(equity);
      datetime monthStart = MonthStart(now);

      int target[];
      ArrayResize(target, m_n);
      ArrayInitialize(target, 0);

      bool entries = false;
      int topN = m_topN;

      if(m_bookMonthKey != monthKey)
      {
         // The monthly signal is computed exactly once per month and then held.
         // Retries only re-attempt execution: recomputing on every retry let a
         // single D1 bar shift flip the book and flatten positions mid-deployment.
         double vixScale = VixExposureScale(monthStart);
         entries = allowEntries && !(century.Enabled() && century.HaltTrading());
         if(m_useStackV1 && vixScale <= 0.0)
            entries = false;
         topN = century.Enabled() ? century.TopN() : m_topN;

         BuildTargets(topN, target);
         ApplyNeutralBookFilter(target);

         //--- Volatility targeting: compute once per month and cache.
         //--- Matches research/carry.py: scale all legs by volTarget / rolling12mVol.
         if(m_volTargetEnabled)
         {
            m_volScalar = ComputeVolScalar(target);
            PrintFormat("Sovereign carry vol: scalar=%.3f target=%.0f%% lookback=%dmo",
                        m_volScalar, m_volTarget * 100.0, m_volLookback);
         }
         else
         {
            m_volScalar = 1.0;
         }

         if(m_useStackV1)
         {
            PrintFormat("Sovereign Stack v1: vixScale=%.2f neutral=%s ddHalt=%s",
                        vixScale, m_requireNeutral ? "on" : "off", m_stackDdHalted ? "Y" : "N");
         }

         ArrayCopy(m_book, target, 0, 0, m_n);
         m_bookEntries = entries;
         m_bookVixScale = vixScale;
         m_bookMonthKey = monthKey;

         int nLong = 0;
         int nShort = 0;
         for(int i = 0; i < m_n; i++)
         {
            if(target[i] > 0)
               nLong++;
            else if(target[i] < 0)
               nShort++;
         }
         PrintFormat("Sovereign carry: book longs=%d shorts=%d (topN=%d, entries=%s)",
                     nLong, nShort, topN, entries ? "Y" : "N");
      }
      else
      {
         for(int i = 0; i < m_n; i++)
            target[i] = m_book[i];
         entries = m_bookEntries;
      }

      // Kill switch: a drawdown halt acts immediately, even mid-month.
      if(m_useStackV1 && m_stackDdHalted)
         entries = false;

      // Target-aware closing for both modes: a retry must not close legs that
      // are already positioned correctly.
      for(int i = 0; i < m_n; i++)
      {
         int want = entries ? target[i] : 0;
         int have = PositionSide(m_legs[i].symbol);
          if(want == 0 || want != have)
             CloseLeg(m_legs[i].symbol, t);
      }

      if(!entries)
      {
         m_lastMonthKey = monthKey;
         return;
      }

      if(!perf.AllowNewRisk("SOV|MASTER"))
      {
         m_lastMonthKey = monthKey;
         return;
      }

      int active = 0;
      for(int i = 0; i < m_n; i++)
         if(target[i] != 0)
            active++;
      if(active == 0)
      {
         m_lastMonthKey = monthKey;
         return;
      }

      double legWeightBase = MathMin(m_riskPerLeg / 100.0, m_maxTotalRisk / 100.0 / (double)active);
      legWeightBase *= m_volScalar;  // vol targeting: scale all legs by vol scalar
      double riskCap = equity * m_maxTotalRisk / 100.0;
      double used = 0.0;
      int pending = 0;
      // Count legs already in place so a retry cannot exceed the risk cap.
      for(int i = 0; i < m_n; i++)
         if(target[i] != 0 && PositionSide(m_legs[i].symbol) == target[i])
            used += equity * legWeightBase;

      for(int i = 0; i < m_n; i++)
      {
         if(target[i] == 0)
            continue;
         if(PositionSide(m_legs[i].symbol) == target[i])
            continue;

          string sym = m_legs[i].symbol;
          string legBucket = CSovereignPerformance::LegBucket("SOV-CARRY-MOM", sym);
          if(!perf.AllowNewRisk(legBucket))
          {
             SovLogWarn("Sovereign carry: " + sym + " skipped, governor blocked new risk");
             continue;
          }
          double governorMult = perf.CombinedMultiplier(legBucket);
          if(governorMult <= 0.0)
          {
             SovLogWarn("Sovereign carry: " + sym + " skipped, governor multiplier " + DoubleToString(governorMult, 2));
             continue;
          }
          SovResetLastError();
          int spreadPts = (int)SovSymbolInteger(sym, SYMBOL_SPREAD, 0, "Rebalance spread");
          if(spreadPts > m_maxSpread)
          {
             SovLogWarn("Sovereign carry: " + sym + " skipped, spread " + IntegerToString(spreadPts) +
                        " > " + IntegerToString(m_maxSpread) + " points");
             pending++;
             continue;
          }

          double atr = 0.0;
          double atrBuf[1];
          SovResetLastError();
          int atrHandle = iATR(sym, PERIOD_D1, 14);
          if(!SovCheckHandle(atrHandle, "iATR"))
             continue;
          if(!SovCopyBuffer(atrHandle, 0, 1, 1, atrBuf, "ATR14"))
          {
             IndicatorRelease(atrHandle);
             continue;
          }
          atr = atrBuf[0];
          IndicatorRelease(atrHandle);
          if(atr <= 0.0)
             atr = SovSymbolDouble(sym, SYMBOL_POINT, 0.0, "Rebalance ATR fallback") * 100.0;

          MqlDateTime utc;
          TimeToStruct(now, utc);
          TrafficLightResult tlResult = tl.Evaluate(target[i], 0, atr, 0.5, 0,
                                                    true, spreadPts, utc.hour, true, false);
          if(!tlResult.allowed)
          {
             SovLogWarn("Sovereign carry: " + sym + " skipped, traffic light blocked (" + tlResult.reason + ")");
             continue;
          }

          double centuryMult = century.CombinedLegScalar(sym);
          if(centuryMult <= 0.0)
          {
             SovLogWarn("Sovereign carry: " + sym + " skipped, century scalar " +
                        DoubleToString(centuryMult, 2));
             continue;
          }
          double stackMult = m_useStackV1 ? m_bookVixScale : 1.0;
          double riskWeight = legWeightBase * governorMult * tlResult.riskMultiplier
                              * centuryMult * stackMult;
          double riskMoney = equity * riskWeight;
          if(riskMoney <= 0.0)
             continue;
          if(used + riskMoney > riskCap)
             break;

          double stopDist = atr * 4.0;
          double sl = 0.0;
          double lot = LotsForRisk(sym, riskMoney, stopDist);
          if(lot <= 0.0)
          {
             double vmin = SovSymbolDouble(sym, SYMBOL_VOLUME_MIN, 0.0, "LotsForRisk min");
             double minRisk = (vmin > 0.0) ? vmin * LossPerLot(sym, stopDist) : 0.0;
             if(vmin > 0.0 && minRisk > 0.0 && minRisk <= riskMoney * 4.0
                && used + minRisk <= riskCap)
             {
                lot = vmin;
                riskMoney = minRisk;
                SovLogInfo("Sovereign carry: " + sym + " sized below minimum, using " +
                           DoubleToString(lot, 2) + " lots (risk " + DoubleToString(riskMoney, 2) +
                           " vs target " + DoubleToString(equity * riskWeight, 2) + ")");
             }
             else
             {
                SovLogWarn("Sovereign carry: " + sym + " skipped, lot below minimum "
                           "(target risk " + DoubleToString(equity * riskWeight, 2) +
                           ", min-lot risk " + DoubleToString(minRisk, 2) +
                           ", equity " + DoubleToString(equity, 2) + ")");
                pending++;
                continue;
             }
          }

          SovResetLastError();
          double price = (target[i] > 0) ? SovSymbolDouble(sym, SYMBOL_ASK, 0.0, "Rebalance price")
                                         : SovSymbolDouble(sym, SYMBOL_BID, 0.0, "Rebalance price");
          if(price <= 0.0)
          {
             SovLogWarn("Sovereign carry: " + sym + " skipped, no " +
                        ((target[i] > 0) ? "ask" : "bid") + " price");
             pending++;
             continue;
          }

          if(m_useStopLoss)
             sl = (target[i] > 0) ? price - stopDist : price + stopDist;

          double tp = 0.0;

          string comment = StringFormat("SOV-CARRY-MOM side=%d", target[i]);
          SovResetLastError();
          bool ok = false;
          if(target[i] > 0)
             ok = t.Buy(lot, sym, price, sl, tp, comment);
          else
             ok = t.Sell(lot, sym, price, sl, tp, comment);

          if(ok)
          {
             perf.RegisterOrder(t.ResultOrder(), legBucket, riskMoney,
                                target[i], 0, 0.0, "");
             audit.LogOrderAttempt((target[i] > 0) ? "BUY" : "SELL", "MARKET",
                                   price, t.ResultPrice(), lot, spreadPts,
                                   t.ResultRetcode(), comment,
                                   false, "HOLD", 0.0, 0.0);
             used += riskMoney;
          }
          else
          {
             SovLogTradeRetcode(t, "Sovereign carry: " + sym + " order FAILED side=" +
                                IntegerToString(target[i]) + " lot=" + DoubleToString(lot, 2) +
                                " price=" + DoubleToString(price, 5), sym);
             pending++;
          }
      }

      // Only mark the month as handled once every targeted leg is either
      // positioned or permanently skipped. Transient failures (wide rollover
      // spread, closed market, rejected order) keep the month open for retry.
      if(pending == 0)
         m_lastMonthKey = monthKey;
      else
         PrintFormat("Sovereign carry: %d leg(s) pending, will retry this month",
                     pending);
   }

   void Deinit() { }
};

#endif
