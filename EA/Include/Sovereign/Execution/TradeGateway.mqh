//+------------------------------------------------------------------+
//| TradeGateway.mqh — Encapsulated CTrade execution wrapper          |
//| Normalizes volume/SL/TP, logs retcodes, handles position mgmt    |
//+------------------------------------------------------------------+
#property strict

#include <Trade\Trade.mqh>

struct STradeResult
   {
   bool     ok = false;
   uint     retcode = 0;
   ulong    ticket = 0;
   string   comment = "";
   double   price = 0.0;
   double   volume = 0.0;
   };

class CTradeGateway
   {
   private:
      CTrade      m_trade;
      ulong       m_magic;
      int         m_deviation;
      ENUM_ORDER_TYPE_FILLING m_filling;
      double      m_defaultSlippage;

   public:
      void CTradeGateway(ulong magic, int deviation = 10,
                         ENUM_ORDER_TYPE_FILLING filling = ORDER_FILLING_FOK)
         {
         m_magic = magic;
         m_deviation = deviation;
         m_filling = filling;
         m_defaultSlippage = 0;
         m_trade.SetExpertMagicNumber(magic);
         m_trade.SetDeviationInPoints(deviation);
         m_trade.SetTypeFilling(filling);
         }

      //--- normalize volume to symbol lot step
      double NormalizeVolume(const string symbol, double volume)
         {
         double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
         double maxLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
         double lotStep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
         if(lotStep == 0.0) lotStep = 0.01;
         volume = MathFloor(volume / lotStep) * lotStep;
         if(volume < minLot) volume = minLot;
         if(volume > maxLot) volume = maxLot;
         return NormalizeDouble(volume, 2);
         }

      //--- normalize price to symbol digits
      double NormalizePrice(const string symbol, double price)
         {
         int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
         return NormalizeDouble(price, digits);
         }

      //--- normalize stop levels to symbol stops level
      double NormalizeStopDistance(const string symbol, double distance)
         {
         int stopsLevel = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
         double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
         double minDist = stopsLevel * point;
         if(distance < minDist) distance = minDist;
         return NormalizeDouble(distance, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS));
         }

      //--- place a market buy order with SL/TP
      STradeResult Buy(const string symbol, double volume, double sl = 0.0, double tp = 0.0,
                       const string comment = "Sovereign")
         {
         STradeResult res;
         res.comment = comment;

         double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
         volume = NormalizeVolume(symbol, volume);

         if(sl > 0.0)
            sl = NormalizePrice(symbol, ask - NormalizeStopDistance(symbol, MathAbs(ask - sl)));
         if(tp > 0.0)
            tp = NormalizePrice(symbol, ask + NormalizeStopDistance(symbol, MathAbs(tp - ask)));

         res.ok = m_trade.Buy(volume, symbol, ask, sl, tp, comment);
         res.retcode = m_trade.ResultRetcode();
         res.ticket = m_trade.ResultTicket();
         res.price = m_trade.ResultPrice();
         res.volume = volume;

         if(!res.ok)
            Print("TradeGateway BUY failed: retcode=" + IntegerToString((long)res.retcode) +
                  " GetLastError=" + IntegerToString(GetLastError()));
         else
            Print("TradeGateway BUY ok: ticket=" + IntegerToString((long)res.ticket) +
                  " price=" + DoubleToString(res.price, 5) + " vol=" + DoubleToString(res.volume, 2));

         return res;
         }

      //--- place a market sell order with SL/TP
      STradeResult Sell(const string symbol, double volume, double sl = 0.0, double tp = 0.0,
                        const string comment = "Sovereign")
         {
         STradeResult res;
         res.comment = comment;

         double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
         volume = NormalizeVolume(symbol, volume);

         if(sl > 0.0)
            sl = NormalizePrice(symbol, bid + NormalizeStopDistance(symbol, MathAbs(bid - sl)));
         if(tp > 0.0)
            tp = NormalizePrice(symbol, bid - NormalizeStopDistance(symbol, MathAbs(tp - bid)));

         res.ok = m_trade.Sell(volume, symbol, bid, sl, tp, comment);
         res.retcode = m_trade.ResultRetcode();
         res.ticket = m_trade.ResultTicket();
         res.price = m_trade.ResultPrice();
         res.volume = volume;

         if(!res.ok)
            Print("TradeGateway SELL failed: retcode=" + IntegerToString((long)res.retcode) +
                  " GetLastError=" + IntegerToString(GetLastError()));
         else
            Print("TradeGateway SELL ok: ticket=" + IntegerToString((long)res.ticket) +
                  " price=" + DoubleToString(res.price, 5) + " vol=" + DoubleToString(res.volume, 2));

         return res;
         }

      //--- close position by ticket
      bool Close(ulong ticket)
         {
         if(!m_trade.PositionSelect(ticket))
            {
            Print("TradeGateway Close failed: PositionSelect ticket=" + IntegerToString((long)ticket) +
                  " retcode=" + IntegerToString((long)m_trade.ResultRetcode()));
            return false;
            }
         bool ok = m_trade.PositionClose(ticket);
         if(!ok)
            Print("TradeGateway Close failed: retcode=" + IntegerToString((long)m_trade.ResultRetcode()) +
                  " GetLastError=" + IntegerToString(GetLastError()));
         return ok;
         }

      //--- modify SL/TP of a position
      bool Modify(ulong ticket, double sl, double tp)
         {
         if(!m_trade.PositionSelect(ticket))
            return false;
         bool ok = m_trade.PositionModify(ticket, sl, tp);
         if(!ok)
            Print("TradeGateway Modify failed: retcode=" + IntegerToString((long)m_trade.ResultRetcode()));
         return ok;
         }
   };
