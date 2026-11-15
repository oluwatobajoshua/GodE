//+------------------------------------------------------------------+
//|                                        SovereignPortfolio.mqh    |
//|             Daily cross-sectional mean-reversion + carry* EA     |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_PORTFOLIO_MQH__
#define __SOVEREIGN_PORTFOLIO_MQH__

#include <Trade/PositionInfo.mqh>
#include <Sovereign\SovereignError.mqh>

class CSovereignPortfolio
{
private:
   string m_syms[10];
   int    m_n;
   int    m_topN;
   int    m_zWindow;
   double m_entryZ;
   int    m_maxHoldBars;
   double m_riskPerPos;
   double m_maxTotalRisk;
   double m_carryTilt;
   long   m_magic;

   datetime m_lastRebalanceDay;

   datetime DayKey(datetime t) const
   {
      if(t <= 0)
         return 0;
      MqlDateTime dt;
      TimeToStruct(t, dt);
      dt.hour = 0;
      dt.min  = 0;
      dt.sec  = 0;
      return StructToTime(dt);
   }

   bool ComputeZScore(const string sym, const int zWindow, double &z)
   {
      SovResetLastError();
      double close[];
      ArraySetAsSeries(close, true);
      int bars = CopyClose(sym, PERIOD_D1, 1, zWindow, close);
      if(bars < zWindow)
      {
         SovLogWarn("ComputeZScore: CopyClose returned " + IntegerToString(bars) + "/" + IntegerToString(zWindow) + " for " + sym);
         return false;
      }

      double sum = 0.0;
      for(int i = 0; i < zWindow; i++)
         sum += close[i];
      double sma = sum / (double)zWindow;

      double sq = 0.0;
      for(int i = 0; i < zWindow; i++)
      {
         double d = close[i] - sma;
         sq += d * d;
      }
      if(zWindow < 2)
         return false;
      double sd = MathSqrt(sq / (double)(zWindow - 1));
      if(sd <= 0.0)
         return false;

      z = (close[0] - sma) / sd;
      return true;
   }

   double ComputeCarry(const string sym)
   {
      double swapBuy = SovSymbolDouble(sym, SYMBOL_SWAP_LONG, 0.0, "ComputeCarry");
      double swapSell = SovSymbolDouble(sym, SYMBOL_SWAP_SHORT, 0.0, "ComputeCarry");
      if(swapBuy == 0.0 && swapSell == 0.0)
         return 0.0;
      return (swapBuy - swapSell) / 10.0;
   }

   double NormalizeVolume(const string sym, double volume)
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

   double LotsForRisk(const string sym, const double riskMoney, const double stopDist)
   {
      if(riskMoney <= 0.0 || stopDist <= 0.0)
         return 0.0;

      SovResetLastError();
      double tickSize  = SovSymbolDouble(sym, SYMBOL_TRADE_TICK_SIZE, 0.0, "LotsForRisk");
      double tickValue = SovSymbolDouble(sym, SYMBOL_TRADE_TICK_VALUE, 0.0, "LotsForRisk");
      if(tickSize <= 0.0 || tickValue <= 0.0)
         return 0.0;

      double lossPerLot = (stopDist / tickSize) * tickValue;
      if(lossPerLot <= 0.0)
         return 0.0;

      return NormalizeVolume(sym, riskMoney / lossPerLot);
   }

   int PositionSideForSymbol(const string sym) const
   {
      CPositionInfo pos;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!pos.SelectByIndex(i))
            continue;
         if(pos.Magic() != m_magic)
            continue;
         if(pos.Symbol() != sym)
            continue;
         if(pos.PositionType() == POSITION_TYPE_BUY)
            return 1;
         if(pos.PositionType() == POSITION_TYPE_SELL)
            return -1;
      }
      return 0;
   }

   int BarsHeld(const string sym) const
   {
      CPositionInfo pos;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!pos.SelectByIndex(i))
            continue;
         if(pos.Magic() != m_magic || pos.Symbol() != sym)
            continue;
         datetime opened = (datetime)pos.Time();
         int shiftOpen = iBarShift(sym, PERIOD_D1, opened, true);
         int shiftNow  = iBarShift(sym, PERIOD_D1, TimeCurrent(), true);
         if(shiftOpen < 0 || shiftNow < 0)
            return 0;
         return shiftOpen - shiftNow;
      }
      return 0;
   }

     void ClosePortfolioPositions(const string sym, CTrade &t)
   {
      CPositionInfo pos;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!pos.SelectByIndex(i))
            continue;
         if(pos.Magic() != m_magic)
            continue;
         if(sym != "" && pos.Symbol() != sym)
            continue;
         SovResetLastError();
         if(!t.PositionClose(pos.Ticket()))
         {
            SovLogTradeRetcode(t, "ClosePortfolioPositions PositionClose", pos.Symbol());
            continue;
         }
      }
   }

   void SelectCrossSectionalSides(const double &zs[], int &sides[])
   {
      for(int i = 0; i < m_n; i++)
         sides[i] = 0;

      for(int pick = 0; pick < m_topN; pick++)
      {
         int best = -1;
         double bestZ = 0.0;
         for(int i = 0; i < m_n; i++)
         {
            if(sides[i] != 0)
               continue;
            if(best < 0 || zs[i] < bestZ)
            {
               best = i;
               bestZ = zs[i];
            }
         }
         if(best >= 0 && bestZ <= -m_entryZ)
            sides[best] = 1;
         else
            break;
      }

      for(int pick = 0; pick < m_topN; pick++)
      {
         int best = -1;
         double bestZ = 0.0;
         for(int i = 0; i < m_n; i++)
         {
            if(sides[i] != 0)
               continue;
            if(best < 0 || zs[i] > bestZ)
            {
               best = i;
               bestZ = zs[i];
            }
         }
         if(best >= 0 && bestZ >= m_entryZ)
            sides[best] = -1;
         else
            break;
      }
   }

   void ManageOpenPositions(const double &zs[], CTrade &t)
   {
      for(int i = 0; i < m_n; i++)
      {
         int side = PositionSideForSymbol(m_syms[i]);
         if(side == 0)
            continue;

         bool exit = false;
         if(BarsHeld(m_syms[i]) >= m_maxHoldBars)
            exit = true;
         if(side > 0 && zs[i] >= 0.0)
            exit = true;
         if(side < 0 && zs[i] <= 0.0)
            exit = true;

         if(exit)
            ClosePortfolioPositions(m_syms[i], t);
      }
   }

public:
   CSovereignPortfolio()
   {
      m_n = 0;
      m_topN = 3;
      m_zWindow = 20;
      m_entryZ = 1.5;
      m_maxHoldBars = 5;
      m_riskPerPos = 1.0;
      m_maxTotalRisk = 4.0;
      m_carryTilt = 0.5;
      m_magic = 0;
      m_lastRebalanceDay = 0;
   }

   bool Init(const string &syms[], const int n, const int topN,
             const int zWindow, const double entryZ,
             const int maxHoldBars, const double riskPerPos,
             const double maxTotalRisk, const double carryTilt,
             const long magic = 0)
   {
      m_n = n;
      m_topN = topN;
      m_zWindow = zWindow;
      m_entryZ = entryZ;
      m_maxHoldBars = maxHoldBars;
      m_riskPerPos = riskPerPos;
      m_maxTotalRisk = maxTotalRisk;
      m_carryTilt = carryTilt;
      m_magic = magic;
      for(int i = 0; i < m_n && i < 10; i++)
         m_syms[i] = syms[i];
      return true;
   }

   bool IsNewDay()
   {
      datetime today = DayKey(TimeCurrent());
      if(today != DayKey(m_lastRebalanceDay))
         return true;
      return false;
   }

   void Rebalance(datetime now, const bool allowEntries, const double equity,
                  CSovereignSignalClient &py, CSovereignPerformance &perf,
                  CSovereignTrafficLight &tl, CSovereignAuditLog &audit,
                   CTrade &t)
   {
      m_lastRebalanceDay = DayKey(now);

      double zs[10];
      double carries[10];
      int sides[10];
      ArrayInitialize(zs, 0.0);
      ArrayInitialize(carries, 0.0);
      ArrayInitialize(sides, 0);

      for(int i = 0; i < m_n; i++)
      {
         double z = 0.0;
         if(!ComputeZScore(m_syms[i], m_zWindow, z))
         {
            zs[i] = 0.0;
            carries[i] = 0.0;
            continue;
         }
         zs[i] = z;
         carries[i] = ComputeCarry(m_syms[i]);
      }

      ManageOpenPositions(zs, t);

      if(!allowEntries)
         return;

      SelectCrossSectionalSides(zs, sides);

      if(!perf.AllowNewRisk("SOV|MASTER"))
         return;

      double totalRiskUsed = 0.0;
      const double maxRiskMoney = equity * m_maxTotalRisk / 100.0;

      for(int i = 0; i < m_n; i++)
      {
         if(sides[i] == 0)
            continue;

         int existing = PositionSideForSymbol(m_syms[i]);
         if(existing == sides[i])
            continue;
          if(existing != 0 && existing != sides[i])
             ClosePortfolioPositions(m_syms[i], t);

         string legBucket = CSovereignPerformance::LegBucket("SOV-XSEC", m_syms[i]);
         if(!perf.AllowNewRisk(legBucket))
            continue;
         double governorMult = perf.CombinedMultiplier(legBucket);
         if(governorMult <= 0.0)
            continue;

         double riskMoney = equity * m_riskPerPos / 100.0 * governorMult;
         if(totalRiskUsed + riskMoney > maxRiskMoney)
            break;

          double atr = 0.0;
          double atrBuf[1];
          SovResetLastError();
         int atrHandle = iATR(m_syms[i], PERIOD_D1, 14);
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
            continue;

         SovResetLastError();
         int spreadPts = (int)SovSymbolInteger(m_syms[i], SYMBOL_SPREAD, 0, "Rebalance spread");
         MqlDateTime utc;
         TimeToStruct(now, utc);
         TrafficLightResult tlResult = tl.Evaluate(sides[i], 0, atr, 0.5, 0,
                                                   governorMult > 0.0, spreadPts,
                                                   utc.hour, true, false);
         if(!tlResult.allowed)
            continue;

         double stopDist = atr * 1.5;
         double lot = LotsForRisk(m_syms[i], riskMoney, stopDist);
         if(lot <= 0.0)
            continue;

         double weight = 1.0 + m_carryTilt * MathMax(-1.0, MathMin(1.0, carries[i]));
         lot = NormalizeVolume(m_syms[i], lot * weight * tlResult.riskMultiplier);
         if(lot <= 0.0)
            continue;

         string comment = StringFormat("SOV-XSEC side=%d z=%.2f carry=%.4f",
                                       sides[i], zs[i], carries[i]);
         SovResetLastError();
         double price = (sides[i] > 0) ? SovSymbolDouble(m_syms[i], SYMBOL_ASK, 0.0, "Rebalance")
                                      : SovSymbolDouble(m_syms[i], SYMBOL_BID, 0.0, "Rebalance");
         if(price <= 0.0)
            continue;

         double sl = (sides[i] > 0) ? price - stopDist : price + stopDist;
         double tp = (sides[i] > 0) ? price + stopDist * 2.0 : price - stopDist * 2.0;

         SovResetLastError();
         bool ok = false;
         if(sides[i] > 0)
             ok = t.Buy(lot, m_syms[i], price, sl, tp, comment);
         else
             ok = t.Sell(lot, m_syms[i], price, sl, tp, comment);

          if(ok)
          {
             perf.RegisterOrder(t.ResultOrder(), legBucket, riskMoney,
                                sides[i], 0, 0.0, "");
             audit.LogOrderAttempt((sides[i] > 0) ? "BUY" : "SELL", "MARKET",
                                   price, t.ResultPrice(), lot, spreadPts,
                                   t.ResultRetcode(), comment,
                                   py.IsHealthy(), py.Action(), py.Confidence(), 0.0);
         }
         else
         {
             SovLogTradeRetcode(t, "Rebalance " + m_syms[i] + " " + comment, m_syms[i]);
         }

         totalRiskUsed += riskMoney;
      }
   }

   void Deinit() { }
};

#endif
