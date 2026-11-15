//+------------------------------------------------------------------+
//|                                   SovereignTelemetryUplink.mqh   |
//|  Native TCP (NDJSON) uplink: EA -> Python telemetry receiver     |
//|                                                                  |
//|  Sends trade events, bar state and equity to a Python TCP        |
//|  listener (loopback only, port 5557 by default). Non-blocking,   |
//|  auto-reconnect, fire-and-forget per message. Every socket       |
//|  failure is logged via SovLogError with GetLastError().          |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_TELEMETRY_UPLINK_MQH__
#define __SOVEREIGN_TELEMETRY_UPLINK_MQH__

#include <Trade\Trade.mqh>
#include <Sovereign\SovereignError.mqh>

//+------------------------------------------------------------------+
//| Native TCP (NDJSON) uplink: EA -> Python telemetry receiver      |
//|                                                                  |
//| Sends trade events, bar state and equity to a Python TCP         |
//| listener (loopback only, port 5557 by default). Non-blocking,    |
//| auto-reconnect, fire-and-forget per message. Every socket        |
//| failure is logged via SovLogError/SovLogWarn with GetLastError().|
//|                                                                  |
//| Telemetry health is tracked via connect/send/socket error        |
//| counters and send latency (milliseconds per SendJson call).      |
//| JSON payloads are logged at LOG_DEBUG level.                     |
//+------------------------------------------------------------------+
class CSovereignTelemetryUplink
{
private:
   int      m_socket;
   string   m_host;
   int      m_port;
   uint     m_connectTimeoutMs;
   int      m_reconnectSeconds;
   bool     m_connected;
   datetime m_lastConnectAttempt;
   string   m_buffer;

   long     m_connectErrors;
   long     m_sendErrors;
   long     m_socketErrors;
   long     m_messagesSent;
   long     m_messagesFailed;
   ulong    m_lastSendTick;   // GetTickCount64() of last send for latency
   int      m_lastSendLatencyMs;

   //--- Log a JSON payload at DEBUG level
   void LogPayload(const string dir, const string json)
   {
      if(!LogLevelEnabled(LOG_DEBUG)) return;
      string truncated = json;
      if(StringLen(truncated) > 512)
         truncated = StringSubstr(truncated, 0, 512) + "...";
      SovLogDebug(StringFormat("TelemetryUplink %s payload: %s", dir, truncated));
   }

   bool Connect()
   {
      m_lastConnectAttempt = TimeCurrent();

      int socket = SocketCreate();
      if(socket == INVALID_HANDLE)
      {
         m_connectErrors++;
         SovLogError("TelemetryUplink Connect", "SocketCreate failed err=" + IntegerToString(GetLastError()));
         return false;
      }

      if(!SocketConnect(socket, m_host, m_port, m_connectTimeoutMs))
      {
         m_connectErrors++;
         SocketClose(socket);
         SovLogWarn("TelemetryUplink Connect SocketConnect to " + m_host + ":" + IntegerToString(m_port) +
                    " failed err=" + IntegerToString(GetLastError()));
         return false;
      }

      m_socket = socket;
      m_connected = true;
      m_buffer = "";
      m_lastSendTick = 0;
      m_lastSendLatencyMs = 0;
      PrintFormat("Sovereign telemetry uplink connected to %s:%d", m_host, m_port);
      SovLogInfo("TelemetryUplink connected to " + m_host + ":" + IntegerToString(m_port));
      return true;
   }

   void Disconnect()
   {
      if(m_socket != INVALID_HANDLE)
      {
         SocketClose(m_socket);
         m_socket = INVALID_HANDLE;
      }
      if(m_connected)
      {
         Print("Sovereign telemetry uplink disconnected.");
         SovLogWarn("TelemetryUplink disconnected from " + m_host + ":" + IntegerToString(m_port));
      }
      m_connected = false;
      m_buffer = "";
      m_lastSendTick = 0;
      m_lastSendLatencyMs = 0;
   }

   bool SendJson(const string json)
   {
      ulong startTick = GetTickCount64();

      if(!m_connected)
      {
         if(m_lastConnectAttempt > 0 &&
            (TimeCurrent() - m_lastConnectAttempt) < m_reconnectSeconds)
         {
            m_messagesFailed++;
            return false;
         }
         Connect();
         m_messagesFailed++;
         return false;
      }

      if(!SocketIsConnected(m_socket))
      {
         m_socketErrors++;
         m_messagesFailed++;
         SovLogWarn("TelemetryUplink SendJson: socket no longer connected");
         Disconnect();
         return false;
      }

      uchar data[];
      if(!StringToCharArray(json + "\n", data))
      {
         m_sendErrors++;
         m_messagesFailed++;
         SovLogError("TelemetryUplink SendJson", "StringToCharArray failed err=" + IntegerToString(GetLastError()));
         LogPayload("TX-ERROR", json);
         return false;
      }

      int dataSize = ArraySize(data);
      int sent = SocketSend(m_socket, data, dataSize);
      ulong endTick = GetTickCount64();
      m_lastSendLatencyMs = (int)(endTick - startTick);

      if(sent <= 0)
      {
         m_sendErrors++;
         m_messagesFailed++;
         SovLogError("TelemetryUplink SendJson", "SocketSend failed err=" + IntegerToString(GetLastError()) +
                     " latency=" + IntegerToString(m_lastSendLatencyMs) + "ms");
         Disconnect();
         LogPayload("TX-FAIL", json);
         return false;
      }
      if(sent < dataSize)
      {
         m_sendErrors++;
         m_messagesFailed++;
         SovLogWarn("TelemetryUplink SendJson: partial send " + IntegerToString(sent) + "/" + IntegerToString(dataSize) +
                    " latency=" + IntegerToString(m_lastSendLatencyMs) + "ms");
         LogPayload("TX-PARTIAL", json);
         return false;
      }

      m_messagesSent++;
      LogPayload("TX", json);
      return true;
   }

   string EscapeJson(const string s) const
   {
      string out = s;
      StringReplace(out, "\\", "\\\\");
      StringReplace(out, "\"", "\\\"");
      StringReplace(out, "\n", "\\n");
      StringReplace(out, "\r", "\\r");
      StringReplace(out, "\t", "\\t");
      return out;
   }

public:
   CSovereignTelemetryUplink()
   {
      m_socket = INVALID_HANDLE;
      m_host = "127.0.0.1";
      m_port = 5557;
      m_connectTimeoutMs = 1000;
      m_reconnectSeconds = 30;
      m_connected = false;
      m_lastConnectAttempt = 0;
      m_buffer = "";
      m_connectErrors = 0;
      m_sendErrors = 0;
      m_socketErrors = 0;
      m_messagesSent = 0;
      m_messagesFailed = 0;
      m_lastSendTick = 0;
      m_lastSendLatencyMs = 0;
   }

   ~CSovereignTelemetryUplink()
   {
      Disconnect();
   }

    bool Init(const string host, const int port, const uint connectTimeoutMs = 1000,
              const int reconnectSeconds = 30)
   {
      m_host = host;
      m_port = port;
      m_connectTimeoutMs = connectTimeoutMs;
      m_reconnectSeconds = reconnectSeconds;
      m_lastConnectAttempt = 0;
      return true;
   }

   void Deinit() { Disconnect(); }

   void Poll()
   {
      if(!m_connected)
      {
         if(m_lastConnectAttempt > 0 &&
            (TimeCurrent() - m_lastConnectAttempt) < m_reconnectSeconds)
            return;
         Connect();
         return;
      }
      if(!SocketIsConnected(m_socket))
      {
         m_socketErrors++;
         SovLogWarn("TelemetryUplink Poll: socket no longer connected");
         Disconnect();
      }
   }

   bool IsConnected() const { return m_connected; }

   long ConnectErrors() const { return m_connectErrors; }
   long SendErrors()    const { return m_sendErrors; }
   long SocketErrors()  const { return m_socketErrors; }

   //--- trade event: sent on open / close / modify
   bool SendTradeEvent(const string action, const string symbol, const int side,
                       const double lots, const double price, const double sl,
                       const double tp, const double profit, const double commission,
                       const double swap, const ulong ticket, const ulong positionId,
                       const long magic, const string comment, const string reason,
                       const int retcode, const string retcodeDesc)
   {
      string json = StringFormat(
         "{\"t\":\"trade\",\"ts\":\"%s\",\"action\":\"%s\",\"symbol\":\"%s\","
         "\"side\":%d,\"lots\":%.5f,\"price\":%.5f,\"sl\":%.5f,\"tp\":%.5f,"
         "\"profit\":%.2f,\"commission\":%.2f,\"swap\":%.2f,"
         "\"ticket\":%I64u,\"position_id\":%I64u,\"magic\":%I64d,"
         "\"comment\":\"%s\",\"reason\":\"%s\",\"retcode\":%d,\"retcode_desc\":\"%s\"}",
         TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS | TIME_MINUTES),
         EscapeJson(action), EscapeJson(symbol),
         side, lots, price, sl, tp,
         profit, commission, swap,
         (long)ticket, (long)positionId, (long)magic,
         EscapeJson(comment), EscapeJson(reason),
         (int)retcode, EscapeJson(retcodeDesc));
      return SendJson(json);
   }

   //--- bar state: sent on every new bar for oversight
   bool SendBarState(const string symbol, const ENUM_TIMEFRAMES tf,
                     const double open, const double high, const double low,
                     const double close, const double rsi, const int trend,
                     const bool inPosition, const int positionSide,
                     const double entryPrice, const int barsHeld,
                     const double equity, const double dailyLossPct,
                     const bool allowEntries)
   {
      string json = StringFormat(
         "{\"t\":\"bar\",\"ts\":\"%s\",\"symbol\":\"%s\",\"tf\":\"%s\","
         "\"open\":%.5f,\"high\":%.5f,\"low\":%.5f,\"close\":%.5f,"
         "\"rsi\":%.2f,\"trend\":%d,\"in_position\":%s,\"position_side\":%d,"
         "\"entry_price\":%.5f,\"bars_held\":%d,"
         "\"equity\":%.2f,\"daily_loss_pct\":%.4f,\"allow_entries\":%s}",
         TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS | TIME_MINUTES),
         EscapeJson(symbol), EnumToString(tf),
         open, high, low, close,
         rsi, trend,
         (inPosition ? "true" : "false"), positionSide,
         entryPrice, barsHeld,
         equity, dailyLossPct,
         (allowEntries ? "true" : "false"));
      return SendJson(json);
   }

   //--- init snapshot: sent once on attach for oversight
   bool SendInitSnapshot(const string symbol, const int strategy,
                         const bool rsiBandEnabled, const double deadband,
                         const double riskPct, const double maxDailyDD,
                         const int maxSpread, const bool algoTradingAllowed)
   {
      string json = StringFormat(
         "{\"t\":\"init\",\"ts\":\"%s\",\"symbol\":\"%s\","
         "\"strategy\":%d,\"rsi_band_enabled\":%s,\"deadband\":%.2f,"
         "\"risk_pct\":%.2f,\"max_daily_dd\":%.2f,\"max_spread\":%d,"
         "\"algo_trading_allowed\":%s}",
         TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS | TIME_MINUTES),
         EscapeJson(symbol),
         (int)strategy,
         (rsiBandEnabled ? "true" : "false"), deadband,
         riskPct, maxDailyDD, maxSpread,
         (algoTradingAllowed ? "true" : "false"));
       return SendJson(json);
    }

    //--- health / latency accessors
    long     MessagesSent()    const { return m_messagesSent; }
    long     MessagesFailed()  const { return m_messagesFailed; }
    long     GetConnectErrors() const { return m_connectErrors; }
    long     GetSendErrors()   const { return m_sendErrors; }
    long     GetSocketErrors() const { return m_socketErrors; }
    int      LastSendLatencyMs() const { return m_lastSendLatencyMs; }
};

#endif // __SOVEREIGN_TELEMETRY_UPLINK_MQH__
