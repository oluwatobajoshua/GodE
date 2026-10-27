//+------------------------------------------------------------------+
//|                                        SovereignAuditLog.mqh     |
//|          CSV execution, slippage and signal audit logging        |
//|                                                                  |
//| Files are written to MQL5\Files\Sovereign\ (or the Strategy       |
//| Tester agent sandbox when running in the tester).                 |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_AUDIT_LOG_MQH__
#define __SOVEREIGN_AUDIT_LOG_MQH__

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
      int handle = FileOpen(path,
                            FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI |
                            FILE_SHARE_READ | FILE_SHARE_WRITE);
      if(handle == INVALID_HANDLE)
      {
         PrintFormat("Sovereign audit: cannot open %s (error %d)", path, GetLastError());
         return INVALID_HANDLE;
      }

      FileSeek(handle, 0, SEEK_END);
      if(!existed || FileSize(handle) == 0)
         FileWriteString(handle, header + "\r\n");
      FileFlush(handle);
      return handle;
   }

   void WriteLine(const int handle, const string line)
   {
      if(handle == INVALID_HANDLE) return;
      FileWriteString(handle, line + "\r\n");
      FileFlush(handle);
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
      {
         //--- FolderCreate also returns false when the folder already exists.
         //--- A subsequent FileOpen will surface any real directory failure.
      }

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
      if(m_orderHandle != INVALID_HANDLE)  { FileClose(m_orderHandle);  m_orderHandle = INVALID_HANDLE; }
      if(m_dealHandle != INVALID_HANDLE)   { FileClose(m_dealHandle);   m_dealHandle = INVALID_HANDLE; }
      if(m_signalHandle != INVALID_HANDLE) { FileClose(m_signalHandle); m_signalHandle = INVALID_HANDLE; }
      if(m_adaptiveHandle != INVALID_HANDLE) { FileClose(m_adaptiveHandle); m_adaptiveHandle = INVALID_HANDLE; }
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
         Fmt(AccountInfoDouble(ACCOUNT_EQUITY), 2),
         Fmt(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2));

      WriteLine(m_orderHandle, line);
   }

   void LogDeal(const MqlTradeTransaction &trans, const uint retcode)
   {
      if(!m_enabled) return;
      if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;
      if(!HistoryDealSelect(trans.deal)) return;

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

      string line = StringFormat(
         "%s,DEAL,%I64u,%I64u,%I64u,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%d,%s",
         TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
         (ulong)HistoryDealGetInteger(trans.deal, DEAL_TICKET),
         orderTicket,
         (ulong)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID),
         entryText,
         typeText,
         Csv(HistoryDealGetString(trans.deal, DEAL_SYMBOL)),
         Fmt(HistoryDealGetDouble(trans.deal, DEAL_VOLUME), 2),
         Fmt(fillPrice),
         Fmt(LookupAttemptPrice(orderTicket)),
         Fmt(slippage, 1),
         Fmt(HistoryDealGetDouble(trans.deal, DEAL_COMMISSION), 2),
         Fmt(HistoryDealGetDouble(trans.deal, DEAL_SWAP), 2),
         Fmt(HistoryDealGetDouble(trans.deal, DEAL_PROFIT), 2),
         retcode,
         Csv(HistoryDealGetString(trans.deal, DEAL_COMMENT)));

      WriteLine(m_dealHandle, line);
   }
};

#endif // __SOVEREIGN_AUDIT_LOG_MQH__
