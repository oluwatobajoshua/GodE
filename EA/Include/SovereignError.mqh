//+------------------------------------------------------------------+
//|                                      SovereignError.mqh          |
//|  Centralized error reporting for the Sovereign EA stack.          |
//|  MQL5 has no exceptions: every API failure must be logged with    |
//|  GetLastError() + IntegerToString(err) to BOTH the Journal and      |
//|  the always-open error log file (MQL5\Files\Sovereign\error_log. |
//|  csv) so no failure is ever silent.                                |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_ERROR_MQH__
#define __SOVEREIGN_ERROR_MQH__

#include <Trade\Trade.mqh>
#include <Sovereign\SovereignErrorLog.mqh>

//+------------------------------------------------------------------+
//| Log level enum: controls Journal + file output verbosity          |
//| LOG_DEBUG (0) = everything including tick heartbeats, indicator  |
//|                snapshots, per-bar detail (noisy, for testing)    |
//| LOG_INFO  (1) = standard operational events (OnNewBar, OrderPlaced)|
//| LOG_ERROR (2) = errors and rejections only (production quiet)    |
//+------------------------------------------------------------------+
enum ENUM_LOG_LEVEL
{
   LOG_DEBUG = 0,
   LOG_INFO  = 1,
   LOG_ERROR = 2
};

//--- Global log level: set from InpLogLevel input in OnInit()
extern ENUM_LOG_LEVEL g_logLevel;

//--- Global error log instance: defined in SovereignGodsEyeEA.mq5,
//  initialized in OnInit(). All includes share it via extern.
extern CSovereignErrorLog g_errorLog;

//--- Helper: should this level be printed/written?
inline bool LogLevelEnabled(const ENUM_LOG_LEVEL level)
{
   return (int)level >= (int)g_logLevel;
}

//--- Log an error with GetLastError() + IntegerToString(err) — always LOG_ERROR level
void SovLogError(const string ctx, const string action = "")
{
   int err = GetLastError();
   string desc = IntegerToString(err);
   string act = (action == "") ? "" : " [" + action + "]";
   PrintFormat("Sovereign ERROR ctx=%s%s: err=%d (%s)", ctx, act, err, desc);
   if(g_errorLog.Enabled())
      g_errorLog.LogError(ctx, action);
}

//--- Log a warning (non-fatal) — always LOG_ERROR level (warnings are actionable)
void SovLogWarn(const string msg)
{
   PrintFormat("Sovereign WARN: %s", msg);
   if(g_errorLog.Enabled())
      g_errorLog.LogWarn(msg);
}

//--- Log an info message at LOG_INFO level
void SovLogInfo(const string msg)
{
   if(!LogLevelEnabled(LOG_INFO)) return;
   PrintFormat("Sovereign INFO: %s", msg);
   if(g_errorLog.Enabled())
      g_errorLog.LogInfo(msg);
}

//--- Log a debug message at LOG_DEBUG level (tick heartbeats, indicator snapshots)
void SovLogDebug(const string msg)
{
   if(!LogLevelEnabled(LOG_DEBUG)) return;
   PrintFormat("Sovereign DEBUG: %s", msg);
   if(g_errorLog.Enabled())
      g_errorLog.LogInfo(msg); // debug written to file as INFO category
}

//--- Log a trade execution with full MqlTradeResult context: retcode, ticket,
//--- requested vs fill price (slippage), GetLastError() on failure
void SovLogTrade(const string ctx, const CTrade &t, const double requestedPrice,
                 const double fillPrice, const ulong ticket, const string symbol = "")
{
   uint retcode = t.ResultRetcode();
   string desc  = t.ResultRetcodeDescription();
   string sym = (symbol == "") ? "" : " sym=" + symbol;
   long err = GetLastError();

   //--- compute slippage in points
   double slippagePts = EMPTY_VALUE;
   if(requestedPrice > 0 && fillPrice > 0)
      slippagePts = MathAbs(fillPrice - requestedPrice) / _Point;

   PrintFormat("Sovereign TRADE %s%s: retcode=%d (%s) ticket=%I64u req=%.5f fill=%.5f slippage=%.1fpts err=%d",
               ctx, sym, (int)retcode, desc, (long)ticket, requestedPrice, fillPrice,
               slippagePts == EMPTY_VALUE ? -1 : slippagePts, (int)err);

   if(g_errorLog.Enabled())
   {
      string act = StringFormat("retcode=%d desc=%s ticket=%I64u req=%.5f fill=%.5f slippage=%.1f err=%d",
                                (int)retcode, desc, (long)ticket, requestedPrice, fillPrice,
                                slippagePts == EMPTY_VALUE ? -1 : slippagePts, (int)err);
      g_errorLog.LogError(ctx + sym, act);
   }
}

//--- Log a trade result without a CTrade reference (used for PositionModify/PositionClose)
void SovLogTradeResult(const ulong ticket, const uint retcode, const string ctx,
                       const double requestedPrice, const double fillPrice,
                       const string symbol = "")
{
   string desc = IntegerToString((int)retcode);
   string sym = (symbol == "") ? "" : " sym=" + symbol;
   long err = GetLastError();

   double slippagePts = EMPTY_VALUE;
   if(requestedPrice > 0 && fillPrice > 0)
      slippagePts = MathAbs(fillPrice - requestedPrice) / _Point;

   PrintFormat("Sovereign TRADE %s%s: retcode=%d (%s) ticket=%I64u req=%.5f fill=%.5f slippage=%.1fpts err=%d",
               ctx, sym, (int)retcode, desc, (long)ticket, requestedPrice, fillPrice,
               slippagePts == EMPTY_VALUE ? -1 : slippagePts, (int)err);

   if(g_errorLog.Enabled())
   {
      string act = StringFormat("retcode=%d desc=%s ticket=%I64u req=%.5f fill=%.5f slippage=%.1f err=%d",
                                (int)retcode, desc, (long)ticket, requestedPrice, fillPrice,
                                slippagePts == EMPTY_VALUE ? -1 : slippagePts, (int)err);
      g_errorLog.LogError(ctx + sym, act);
   }
}

//--- Log a skip/rejection reason at LOG_INFO level (always visible in production)
void SovLogSkip(const string ctx, const string reason, const string symbol = "")
{
   if(!LogLevelEnabled(LOG_INFO)) return;
   string sym = (symbol == "") ? "" : " sym=" + symbol;
   PrintFormat("Sovereign SKIP %s%s: %s", ctx, sym, reason);
   if(g_errorLog.Enabled())
      g_errorLog.LogWarn(ctx + sym + " | SKIP: " + reason);
}

//--- Check an indicator handle is valid; log if not
bool SovCheckHandle(const int handle, const string ctx)
{
   if(handle == INVALID_HANDLE)
   {
      SovLogError(ctx, "invalid indicator handle");
      return false;
    }
   return true;
}

//--- Check two handles are valid
bool SovCheckHandle2(const int h1, const int h2, const string ctx)
{
   bool ok = true;
   if(h1 == INVALID_HANDLE) { SovLogError(ctx, "handle 1 invalid"); ok = false; }
   if(h2 == INVALID_HANDLE) { SovLogError(ctx, "handle 2 invalid"); ok = false; }
   return ok;
}

//--- Check four handles are valid
bool SovCheckHandle4(const int h1, const int h2, const int h3, const int h4, const string ctx)
{
   bool ok = true;
   if(h1 == INVALID_HANDLE) { SovLogError(ctx, "handle 1 invalid"); ok = false; }
   if(h2 == INVALID_HANDLE) { SovLogError(ctx, "handle 2 invalid"); ok = false; }
   if(h3 == INVALID_HANDLE) { SovLogError(ctx, "handle 3 invalid"); ok = false; }
   if(h4 == INVALID_HANDLE) { SovLogError(ctx, "handle 4 invalid"); ok = false; }
   return ok;
}

//--- Check a bool result; log if false
bool SovCheckOk(const bool ok, const string ctx)
{
   if(!ok) SovLogError(ctx, "operation returned false");
   return ok;
}

//--- Log a trade retcode with full context
void SovLogTradeRetcode(const CTrade &t, const string ctx, const string symbol = "")
{
   uint retcode = t.ResultRetcode();
   string desc  = t.ResultRetcodeDescription();
   ulong  ticket = t.ResultOrder();
   string sym = (symbol == "") ? "" : " sym=" + symbol;
   PrintFormat("Sovereign TRADE %s%s: retcode=%d (%s) order=%I64u err=%d",
               ctx, sym, (int)retcode, desc, (long)ticket, GetLastError());
   if(g_errorLog.Enabled())
   {
      string act = StringFormat("retcode=%d desc=%s ticket=%I64u", (int)retcode, desc, (long)ticket);
      g_errorLog.LogError(ctx + sym, act);
   }
}

//--- Log a trade result (ticket + retcode) without a CTrade reference
void SovLogTradeResult(const ulong ticket, const uint retcode, const string ctx, const string symbol = "")
{
   string desc = IntegerToString((int)retcode);
   string sym = (symbol == "") ? "" : " sym=" + symbol;
   PrintFormat("Sovereign TRADE %s%s: retcode=%d (%s) ticket=%I64u err=%d",
               ctx, sym, (int)retcode, desc, (long)ticket, GetLastError());
   if(g_errorLog.Enabled())
   {
      string act = StringFormat("retcode=%d desc=%s ticket=%I64u", (int)retcode, desc, (long)ticket);
      g_errorLog.LogError(ctx + sym, act);
   }
}

//--- Safe SymbolInfoDouble with error logging
double SovSymbolDouble(const string symbol, const int propId, const double def = 0.0, const string ctx = "")
{
   double v = SymbolInfoDouble(symbol, (ENUM_SYMBOL_INFO_DOUBLE)propId);
   if(v == 0.0 && GetLastError() != 0)
   {
      SovLogError(ctx + " SymbolInfoDouble(" + IntegerToString(propId) + ")", symbol);
      return def;
   }
   return v;
}

//--- Safe SymbolInfoInteger with error logging
long SovSymbolInteger(const string symbol, const int propId, const long def = 0, const string ctx = "")
{
   long v = SymbolInfoInteger(symbol, (ENUM_SYMBOL_INFO_INTEGER)propId);
   if(v == 0 && GetLastError() != 0)
   {
      SovLogError(ctx + " SymbolInfoInteger(" + IntegerToString(propId) + ")", symbol);
      return def;
   }
   return v;
}

//--- Safe SymbolInfoString with error logging
string SovSymbolString(const string symbol, const int propId, const string def = "", const string ctx = "")
{
   string v = SymbolInfoString(symbol, (ENUM_SYMBOL_INFO_STRING)propId);
   if(v == "" && GetLastError() != 0)
   {
      SovLogError(ctx + " SymbolInfoString(" + IntegerToString(propId) + ")", symbol);
      return def;
   }
   return v;
}

//--- Safe CopyBuffer with error logging
bool SovCopyBuffer(const int handle, const int bufNum, const int start, const int count,
                   double &buf[], const string ctx = "")
{
   if(handle == INVALID_HANDLE)
   {
      SovLogError(ctx + " invalid indicator handle", "");
      return false;
   }
   int copied = CopyBuffer(handle, bufNum, start, count, buf);
   if(copied < count)
   {
      SovLogError(ctx + " CopyBuffer returned " + IntegerToString(copied) + "/" + IntegerToString(count), "");
      return false;
   }
   return true;
}

//--- Safe FileWriteString with error logging
bool SovFileWriteLine(const int handle, const string line, const string ctx = "")
{
   if(handle == INVALID_HANDLE) return false;
   if(!FileWriteString(handle, line + "\r\n"))
   {
      SovLogError(ctx + " FileWriteString failed", "");
      return false;
   }
   FileFlush(handle);
   return true;
}

//--- ResetLastError before critical calls so stale codes don't mask real failures
void SovResetLastError()
{
   ResetLastError();
}

//--- Check automated trading is allowed at the terminal level.
//--- This is a RUNTIME condition (the user can flip Algo Trading at any time),
//--- so it must never be treated as a fatal OnInit error: the EA should attach,
//--- explain why it is idle, and start trading the moment the button is green.
bool SovCheckTradeAllowed()
{
   bool algoAllowed = (bool)MQLInfoInteger(MQL_TRADE_ALLOWED);
   PrintFormat("Sovereign SovCheckTradeAllowed: MQL_TRADE_ALLOWED=%d", algoAllowed);
   if(!algoAllowed)
   {
      Print("Sovereign WARN: MQL_TRADE_ALLOWED is false - enable the green Algo Trading button; EA will observe only.");
      return false;
   }
   long tradeExpert = AccountInfoInteger(ACCOUNT_TRADE_EXPERT);
   PrintFormat("Sovereign SovCheckTradeAllowed: ACCOUNT_TRADE_EXPERT=%d", tradeExpert);
   if(tradeExpert == 0)
   {
      Print("Sovereign WARN: ACCOUNT_TRADE_EXPERT is 0 - this account does not permit expert trading; EA will observe only.");
      return false;
   }
   PrintFormat("Sovereign SovCheckTradeAllowed: OK");
   return true;
}

//--- Runtime gate: terminal + account both allow expert trading right now.
bool SovTerminalTradingEnabled()
{
   return ((bool)MQLInfoInteger(MQL_TRADE_ALLOWED)) &&
          (AccountInfoInteger(ACCOUNT_TRADE_EXPERT) != 0);
}

//--- Check symbol is tradable (full trade mode).
//--- IMPORTANT: a symbol that is not selected in Market Watch reports trade
//--- mode 0 and looks "untradable" even when it is perfectly tradable. Always
//--- ensure the symbol is selected first, otherwise OnInit fails on symbols
//--- (e.g. BTCUSD) that are simply hidden from Market Watch.
bool SovCheckSymbolTradable(const string symbol)
{
   PrintFormat("Sovereign SovCheckSymbolTradable: symbol=%s", symbol);
   if(!SymbolSelect(symbol, true))
      SovLogWarn("SymbolTradable: could not add " + symbol + " to Market Watch (err=" +
                 IntegerToString(GetLastError()) + ")");

   SovResetLastError();
   long mode = SymbolInfoInteger(symbol, SYMBOL_TRADE_MODE);
   PrintFormat("Sovereign SovCheckSymbolTradable: SYMBOL_TRADE_MODE=%d (SYMBOL_TRADE_MODE_FULL=%d)", (int)mode, (int)SYMBOL_TRADE_MODE_FULL);
   if(mode == 0 && GetLastError() != 0)
   {
      SovLogError("SymbolTradable", "SYMBOL_TRADE_MODE failed err=" + IntegerToString(GetLastError()));
      return false;
   }
   if(mode != SYMBOL_TRADE_MODE_FULL)
   {
      SovLogWarn("SymbolTradable: " + symbol + " trade mode=" + IntegerToString((int)mode) + " (not full)");
      return false;
   }
   PrintFormat("Sovereign SovCheckSymbolTradable: OK");
   return true;
}

//--- Set trade filling mode based on the symbol's actual capabilities.
//--- ORDER_FILLING_RETURN is NOT universal; sending an unsupported mode makes
//--- every order fail with retcode 10030 (unsupported filling mode). Read
//--- SYMBOL_FILLING_MODE and pick the first mode the broker advertises.
bool SovSetTradeFilling(CTrade &t, const string symbol)
{
   long filling = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0)
   {
      t.SetTypeFilling(ORDER_FILLING_FOK);
      PrintFormat("Sovereign SovSetTradeFilling: %s -> ORDER_FILLING_FOK", symbol);
      return true;
   }
   if((filling & SYMBOL_FILLING_IOC) != 0)
   {
      t.SetTypeFilling(ORDER_FILLING_IOC);
      PrintFormat("Sovereign SovSetTradeFilling: %s -> ORDER_FILLING_IOC", symbol);
      return true;
   }
   //--- market/exchange execution: RETURN is the only valid fallback
   t.SetTypeFilling(ORDER_FILLING_RETURN);
   PrintFormat("Sovereign SovSetTradeFilling: %s -> ORDER_FILLING_RETURN (no FOK/IOC advertised, filling=%d)",
               symbol, (int)filling);
   return true;
}

//--- Check if the EA has been stopped (terminal shutdown, chart remove, etc.)
bool SovCheckStopped(const string ctx)
{
   return false;
}

//--- Safe HistoryDealSelect with error logging
bool SovHistoryDealSelect(const ulong ticket, const string ctx = "")
{
   if(!HistoryDealSelect(ticket))
   {
      SovLogError(ctx + " HistoryDealSelect failed for ticket=" + IntegerToString((long)ticket), "");
      return false;
   }
   return true;
}

//--- Safe PositionSelectByTicket with error logging
bool SovPositionSelectByTicket(const ulong ticket, const string ctx = "")
{
   if(!PositionSelectByTicket(ticket))
   {
      SovLogError(ctx + " PositionSelectByTicket failed for ticket=" + IntegerToString((long)ticket), "");
      return false;
   }
   return true;
}

//--- Pyramiding guard: subsequent positions on a symbol are only opened when
//--- every existing open position with the given magic number is in net profit
//--- (profit + swap + commission). Initial entry (zero positions) is permitted.
bool CanPyramidInProfit(const ulong magicNumber, const string symbol)
{
   int total = PositionsTotal();
   if(total <= 0) return true;
   for(int i = total - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != magicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != symbol) continue;
      double net = SovPositionNetProfit(ticket);
      if(net <= 0.0) return false;
   }
   return true;
}

//--- Net profit of an open position (profit + swap + commission).
//--- POSITION_COMMISSION is deprecated but still the only way to read it.
double SovPositionNetProfit(const ulong ticket)
{
   if(!PositionSelectByTicket(ticket)) return 0.0;
   return PositionGetDouble(POSITION_PROFIT) +
          PositionGetDouble(POSITION_SWAP) +
          PositionGetDouble(POSITION_COMMISSION);
}

#endif // __SOVEREIGN_ERROR_MQH__
