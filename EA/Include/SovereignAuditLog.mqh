//+------------------------------------------------------------------+
//|                                        SovereignAuditLog.mqh     |
//|          CSV execution, slippage and signal audit logging        |
//|                                                                  |
//| Files are written to MQL5\Files\Sovereign\ (or the Strategy       |
//| Tester agent sandbox when running in the tester).                 |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_AUDIT_LOG_MQH__
#define __SOVEREIGN_AUDIT_LOG_MQH__

#include <Sovereign\SovereignError.mqh>

class CSovereignAuditLog
{
private:
   bool   m_enabled;
   string m_folder;
   int    m_orderHandle;
   int    m_dealHandle;
   int    m_signalHandle;
   int    m_adaptiveHandle;

   ulong  m_attemptOrder[];
   double m_attemptPrice[];
   int    m_attemptSide[];

   string Csv(string value) const
   {
      if(StringFind(value, ",") >= 0 || StringFind(value, "\"") >= 0 ||
         StringFind(value, "\n") >= 0 || StringFind(value, "\r") >= 0)
      {
         StringReplace(value, "\"", "\"\"");
         return "\"" + value + "\"";
      }
      return value;
   }

   string Fmt(const double value, const int digits = 5) const
   {
      if(value == EMPTY_VALUE) return "";
      return DoubleToString(value, digits);
   }

   int OpenAppend(const string fileName, const string header)
   {
      string path = m_folder + "\\" + fileName;
      bool existed = FileIsExist(path);
      SovResetLastError();
      int handle = FileOpen(path,
                            FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI |
                            FILE_SHARE_READ | FILE_SHARE_WRITE);
      if(handle == INVALID_HANDLE)
      {
         SovLogWarn("AuditLog: cannot open " + path + " err=" + IntegerToString(GetLastError()));
         return INVALID_HANDLE;
      }

      FileSeek(handle, 0, SEEK_END);
      if(!existed || FileSize(handle) == 0)
      {
         SovResetLastError();
         if(FileWriteString(handle, header + "\r\n") <= 0)
         {
            SovLogWarn("AuditLog: write header failed for " + path + " err=" + IntegerToString(GetLastError()));
            FileClose(handle);
            return INVALID_HANDLE;
         }
      }
      SovResetLastError();
      FileFlush(handle);
      if(GetLastError() != 0)
         SovLogWarn("AuditLog: FileFlush failed for " + path + " err=" + IntegerToString(GetLastError()));
      return handle;
   }

    void WriteLine(const int handle, const string line)
   {
      if(handle == INVALID_HANDLE) return;
      SovResetLastError();
      if(FileWriteString(handle, line + "\r\n") <= 0)
         SovLogWarn("AuditLog: WriteLine failed err=" + IntegerToString(GetLastError()));
      SovResetLastError();
      FileFlush(handle);
      if(GetLastError() != 0)
         SovLogWarn("AuditLog: FileFlush failed err=" + IntegerToString(GetLastError()));
   }

    int RotateIfNeeded(int &handle, const string fileName, const string header)
   {
      if(handle == INVALID_HANDLE) return handle;
      if(FileSize(handle) < 1000000) return handle;
      SovResetLastError();
      FileClose(handle);
      if(GetLastError() != 0)
         SovLogWarn("AuditLog: FileClose during rotate failed err=" + IntegerToString(GetLastError()));
      handle = OpenAppend(fileName, header);
      if(handle == INVALID_HANDLE)
         SovLogWarn("AuditLog: rotate reopen failed for " + fileName);
      return handle;
   }

   double LookupAttemptPrice(const ulong orderTicket) const
   {
      for(int i = 0; i < ArraySize(m_attemptOrder); i++)
         if(m_attemptOrder[i] == orderTicket)
            return m_attemptPrice[i];
      return EMPTY_VALUE;
   }

   int LookupAttemptSide(const ulong orderTicket) const
   {
      for(int i = 0; i < ArraySize(m_attemptOrder); i++)
         if(m_attemptOrder[i] == orderTicket)
            return m_attemptSide[i];
      return 0;
   }

public:
   CSovereignAuditLog()
   {
      m_enabled = false;
      m_folder = "Sovereign";
      m_orderHandle = INVALID_HANDLE;
      m_dealHandle = INVALID_HANDLE;
      m_signalHandle = INVALID_HANDLE;
      m_adaptiveHandle = INVALID_HANDLE;
   }

   bool Init(const bool enabled, const string folder = "Sovereign")
   {
      m_enabled = enabled;
      m_folder = folder;
      if(!enabled) return true;

      if(!FolderCreate(m_folder))
         SovLogWarn("AuditLog: FolderCreate(" + m_folder + ") failed");

      m_orderHandle = OpenAppend(
         "order_attempts.csv",
         "server_time,event,action,order_type,requested_price,result_price,volume,"
         "spread_points,slippage_points,retcode,comment,python_healthy,python_action,"
         "python_confidence,poc,equity,free_margin");

      m_dealHandle = OpenAppend(
         "trade_events.csv",
         "server_time,event,deal_ticket,order_ticket,position_id,entry,deal_type,symbol,"
         "volume,price,requested_price,slippage_points,commission,swap,profit,retcode,comment");

      m_signalHandle = OpenAppend(
         "signal_audit.csv",
         "server_time,event,local_bias,price,python_healthy,python_action,python_confidence,"
         "poc,range_position,volatility_ratio,spread_points,heartbeat_age_s");

      m_adaptiveHandle = OpenAppend(
         "adaptive_audit.csv",
         "server_time,event,bucket,mode,side,ml_prob,perf_mult,lot,risk_money,note");

      return (m_orderHandle != INVALID_HANDLE &&
              m_dealHandle != INVALID_HANDLE &&
              m_signalHandle != INVALID_HANDLE &&
              m_adaptiveHandle != INVALID_HANDLE);
   }

    void Deinit()
   {
      if(m_orderHandle != INVALID_HANDLE)  { SovResetLastError(); FileClose(m_orderHandle);  if(GetLastError()!=0) SovLogWarn("AuditLog: FileClose(order) failed"); m_orderHandle = INVALID_HANDLE; }
      if(m_dealHandle != INVALID_HANDLE)   { SovResetLastError(); FileClose(m_dealHandle);   if(GetLastError()!=0) SovLogWarn("AuditLog: FileClose(deal) failed"); m_dealHandle = INVALID_HANDLE; }
      if(m_signalHandle != INVALID_HANDLE) { SovResetLastError(); FileClose(m_signalHandle); if(GetLastError()!=0) SovLogWarn("AuditLog: FileClose(signal) failed"); m_signalHandle = INVALID_HANDLE; }
      if(m_adaptiveHandle != INVALID_HANDLE) { SovResetLastError(); FileClose(m_adaptiveHandle); if(GetLastError()!=0) SovLogWarn("AuditLog: FileClose(adaptive) failed"); m_adaptiveHandle = INVALID_HANDLE; }
   }

   bool Enabled() const { return m_enabled; }

   void RegisterAttempt(const ulong orderTicket, const double requestedPrice, const int side)
   {
      if(orderTicket == 0 || requestedPrice <= 0 || side == 0) return;

      int n = ArraySize(m_attemptOrder);
      if(n >= 256)
      {
         //--- Bounded registry; old fills are already logged.
         ArrayResize(m_attemptOrder, 0);
         ArrayResize(m_attemptPrice, 0);
         ArrayResize(m_attemptSide, 0);
         n = 0;
      }

      ArrayResize(m_attemptOrder, n + 1);
      ArrayResize(m_attemptPrice, n + 1);
      ArrayResize(m_attemptSide, n + 1);
      m_attemptOrder[n] = orderTicket;
      m_attemptPrice[n] = requestedPrice;
      m_attemptSide[n]  = side;
   }

   double SlippagePoints(const ulong orderTicket, const double fillPrice)
   {
      double requested = LookupAttemptPrice(orderTicket);
      int side = LookupAttemptSide(orderTicket);
      if(requested == EMPTY_VALUE || side == 0) return EMPTY_VALUE;

      //--- Positive = adverse fill.
      if(side > 0) return (fillPrice - requested) / _Point;
      return (requested - fillPrice) / _Point;
   }

   void LogSignal(const string localBias, const double price, const bool pythonHealthy,
                  const string pythonAction, const double pythonConfidence,
                  const double poc, const double rangePosition, const double volatilityRatio,
                  const int spreadPoints, const int heartbeatAge)
   {
      if(!m_enabled) return;

      string line = StringFormat(
         "%s,SIGNAL,%s,%s,%s,%s,%s,%s,%s,%s,%d,%d",
         TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
         Csv(localBias),
         Fmt(price),
         pythonHealthy ? "true" : "false",
         Csv(pythonAction),
         Fmt(pythonConfidence, 3),
         Fmt(poc),
         Fmt(rangePosition, 4),
         Fmt(volatilityRatio, 3),
         spreadPoints,
         heartbeatAge);

       m_signalHandle = RotateIfNeeded(m_signalHandle, "signal_audit.csv",
            "server_time,event,local_bias,price,python_healthy,python_action,python_confidence,"
            "poc,range_position,volatility_ratio,spread_points,heartbeat_age_s");

       WriteLine(m_signalHandle, line);
   }

   void LogAdaptive(const string event, const string bucket, const int mode,
                    const int side, const double mlProb, const double perfMult,
                    const double lot, const double riskMoney, const string note)
   {
      if(!m_enabled) return;

      string line = StringFormat(
         "%s,%s,%s,%d,%d,%s,%s,%s,%s,%s",
         TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
         Csv(event),
         Csv(bucket),
         mode,
         side,
         Fmt(mlProb, 4),
         Fmt(perfMult, 3),
         Fmt(lot, 2),
         Fmt(riskMoney, 2),
         Csv(note));

       m_adaptiveHandle = RotateIfNeeded(m_adaptiveHandle, "adaptive_audit.csv",
            "server_time,event,bucket,mode,side,ml_prob,perf_mult,lot,risk_money,note");

       WriteLine(m_adaptiveHandle, line);
   }

   void LogOrderAttempt(const string action, const string orderType,
                        const double requestedPrice, const double resultPrice,
                        const double volume, const int spreadPoints, const uint retcode,
                        const string comment, const bool pythonHealthy,
                        const string pythonAction, const double pythonConfidence,
                        const double poc)
   {
      if(!m_enabled) return;

      double slippage = EMPTY_VALUE;
      if(resultPrice > 0 && requestedPrice > 0)
         slippage = ((action == "BUY") ? (resultPrice - requestedPrice)
                                       : (requestedPrice - resultPrice)) / _Point;

      SovResetLastError();
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
      if(equity == 0.0 && GetLastError() != 0)
         SovLogWarn("AuditLog: AccountInfoDouble(ACCOUNT_EQUITY) failed err=" + IntegerToString(GetLastError()));
      if(freeMargin == 0.0 && GetLastError() != 0)
         SovLogWarn("AuditLog: AccountInfoDouble(ACCOUNT_MARGIN_FREE) failed err=" + IntegerToString(GetLastError()));

      string line = StringFormat(
         "%s,ORDER,%s,%s,%s,%s,%s,%d,%s,%d,%s,%s,%s,%s,%s,%s,%s",
         TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
         Csv(action),
         Csv(orderType),
         Fmt(requestedPrice),
         Fmt(resultPrice),
         Fmt(volume, 2),
         spreadPoints,
         Fmt(slippage, 1),
         retcode,
         Csv(comment),
         pythonHealthy ? "true" : "false",
         Csv(pythonAction),
         Fmt(pythonConfidence, 3),
         Fmt(poc),
         Fmt(equity, 2),
         Fmt(freeMargin, 2));

       m_orderHandle = RotateIfNeeded(m_orderHandle, "order_attempts.csv",
            "server_time,event,action,order_type,requested_price,result_price,volume,"
            "spread_points,slippage_points,retcode,comment,python_healthy,python_action,"
            "python_confidence,poc,equity,free_margin");

        WriteLine(m_orderHandle, line);
    }

    //+------------------------------------------------------------------+
    //| LogDecision — per-bar entry decision audit (enter/skip + full   |
    //| analysis snapshot for optimization)                              |
    //+------------------------------------------------------------------+
    void LogDecision(const string symbol, const string timeframe, const datetime barTime,
                      const int entryMode, const int side, const string decision,
                      const string reason,
                      double rsi, double rsiPrev, double rsiPrior,
                      double ema21, double ema50, double smma100, int trend,
                      double closePx, double openPx, double highPx, double lowPx,
                      double atr, int spread, double p90Spread,
                      bool gateSpread, bool gateAtr, bool gateMaClean,
                      bool gateConsecutive, bool gateEmaSlope, bool gateMacro,
                      bool gateDiscretion, bool gatePortfolio,
                      bool engulfingActive, double engulfingStop,
                      const string note)
    {
       if(!m_enabled) return;

       string line = StringFormat(
          "%s,%s,%s,%d,%d,%s,%s,%s,%s,%s,%s,%s,%s,%d,%s,%s,%s,%s,%s,%s,%s,%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s",
          TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
          Csv(symbol),
          Csv(timeframe),
          TimeToString(barTime, TIME_DATE | TIME_SECONDS),
          entryMode,
          side,
          Csv(decision),
          Csv(reason),
          Fmt(rsi, 2), Fmt(rsiPrev, 2), Fmt(rsiPrior, 2),
          Fmt(ema21, 5), Fmt(ema50, 5), Fmt(smma100, 5),
          trend,
          Fmt(closePx, 5), Fmt(openPx, 5), Fmt(highPx, 5), Fmt(lowPx, 5),
          Fmt(atr, 5),
          spread, Fmt(p90Spread, 1),
          gateSpread ? "PASS" : "FAIL",
          gateAtr ? "PASS" : "FAIL",
          gateMaClean ? "PASS" : "FAIL",
          gateConsecutive ? "PASS" : "FAIL",
          gateEmaSlope ? "PASS" : "FAIL",
          gateMacro ? "PASS" : "FAIL",
          gateDiscretion ? "PASS" : "FAIL",
          gatePortfolio ? "PASS" : "FAIL",
          engulfingActive ? "true" : "false",
          Fmt(engulfingStop, 5),
          Csv(note));

       m_signalHandle = RotateIfNeeded(m_signalHandle, "decision_audit.csv",
            "server_time,symbol,timeframe,bar_time,entry_mode,side,decision,reason,"
            "rsi,rsi_prev,rsi_prior,ema21,ema50,smma100,trend,"
            "close,open,high,low,atr,spread,p90_spread,"
            "gate_spread,gate_atr,gate_ma_clean,gate_consecutive,gate_ema_slope,"
            "gate_macro,gate_discretion,gate_portfolio,engulfing_active,engulfing_stop,note");

        WriteLine(m_signalHandle, line);
    }

    //+------------------------------------------------------------------+
    //| LogExit — per-exit decision audit (why trade closed + full      |
    //| trade PnL details for optimization)                              |
    //+------------------------------------------------------------------+
    void LogExit(const string symbol, const string timeframe, const datetime barTime,
                  const int entryMode, const int side, const string exitReason,
                  double entryPrice, double exitPrice, double pnlPoints,
                  double pnlMoney, int barsHeld, int legCount,
                  double rsi, double rsiPrev, double ema21, double ema50,
                  double closePx, double openPx, double highPx, double lowPx,
                  double atr, int spread, double p90Spread,
                  bool engulfingActive, double engulfingStop,
                  const string note)
    {
       if(!m_enabled) return;

       double winLoss = (pnlMoney > 0.0) ? 1.0 : ((pnlMoney < 0.0) ? -1.0 : 0.0);
       string line = StringFormat(
          "%s,%s,%s,%d,%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%d,%d,%s,%s,%s,%s,%s,%s,%s",
          TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
          Csv(symbol),
          Csv(timeframe),
          TimeToString(barTime, TIME_DATE | TIME_SECONDS),
          entryMode,
          side,
          Csv(exitReason),
          Fmt(entryPrice, 5),
          Fmt(exitPrice, 5),
          Fmt(pnlPoints, 2),
          Fmt(pnlMoney, 2),
          Fmt(winLoss, 2),
          barsHeld,
          legCount,
          Fmt(rsi, 2), Fmt(rsiPrev, 2), Fmt(ema21, 5), Fmt(ema50, 5),
          Fmt(closePx, 5), Fmt(openPx, 5), Fmt(highPx, 5), Fmt(lowPx, 5),
          Fmt(atr, 5),
          spread, Fmt(p90Spread, 1),
          engulfingActive ? "true" : "false",
          Fmt(engulfingStop, 5),
          Csv(note));

       m_signalHandle = RotateIfNeeded(m_signalHandle, "decision_audit.csv",
            "server_time,symbol,timeframe,bar_time,entry_mode,side,exit_reason,"
            "entry_price,exit_price,pnl_points,pnl_money,win_loss,bars_held,leg_count,"
            "rsi,rsi_prev,ema21,ema50,close,open,high,low,atr,spread,p90_spread,"
            "engulfing_active,engulfing_stop,note");

       WriteLine(m_signalHandle, line);
    }

    void LogDeal(const MqlTradeTransaction &trans, const uint retcode)
   {
      if(!m_enabled) return;
      if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;
      SovResetLastError();
      if(!HistoryDealSelect(trans.deal))
      {
         SovLogWarn("AuditLog: HistoryDealSelect(" + IntegerToString((long)trans.deal) +
                    ") failed err=" + IntegerToString(GetLastError()));
         return;
      }

      ulong  orderTicket = (ulong)HistoryDealGetInteger(trans.deal, DEAL_ORDER);
      long   entry       = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
      long   dealType    = HistoryDealGetInteger(trans.deal, DEAL_TYPE);
      double fillPrice   = HistoryDealGetDouble(trans.deal, DEAL_PRICE);

      double slippage = EMPTY_VALUE;
      if(entry == DEAL_ENTRY_IN)
         slippage = SlippagePoints(orderTicket, fillPrice);

      string entryText = (entry == DEAL_ENTRY_IN) ? "IN" :
                         (entry == DEAL_ENTRY_OUT) ? "OUT" :
                         (entry == DEAL_ENTRY_INOUT) ? "INOUT" : "UNKNOWN";
      string typeText = (dealType == DEAL_TYPE_BUY) ? "BUY" :
                        (dealType == DEAL_TYPE_SELL) ? "SELL" : "OTHER";

      SovResetLastError();
      ulong  dealTicket = (ulong)HistoryDealGetInteger(trans.deal, DEAL_TICKET);
      string symbol     = HistoryDealGetString(trans.deal, DEAL_SYMBOL);
      double volume     = HistoryDealGetDouble(trans.deal, DEAL_VOLUME);
      double commission = HistoryDealGetDouble(trans.deal, DEAL_COMMISSION);
      double swap       = HistoryDealGetDouble(trans.deal, DEAL_SWAP);
      double profit     = HistoryDealGetDouble(trans.deal, DEAL_PROFIT);
      string comment    = HistoryDealGetString(trans.deal, DEAL_COMMENT);

      string line = StringFormat(
         "%s,DEAL,%I64u,%I64u,%I64u,%s,%s,%s,%s,%s,%s,%s,%s,%s,%d,%s",
         TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
         dealTicket,
         orderTicket,
         (ulong)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID),
         entryText,
         typeText,
         Csv(symbol),
         Fmt(volume, 2),
         Fmt(fillPrice),
         Fmt(LookupAttemptPrice(orderTicket)),
         Fmt(slippage, 1),
         Fmt(commission, 2),
         Fmt(swap, 2),
         Fmt(profit, 2),
         retcode,
         Csv(comment));

      m_dealHandle = RotateIfNeeded(m_dealHandle, "trade_events.csv",
         "server_time,event,deal_ticket,order_ticket,position_id,entry,deal_type,symbol,"
         "volume,price,requested_price,slippage_points,commission,swap,profit,retcode,comment");

      WriteLine(m_dealHandle, line);
   }
};

#endif // __SOVEREIGN_AUDIT_LOG_MQH__
