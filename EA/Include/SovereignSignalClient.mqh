//+------------------------------------------------------------------+
//|                                        SovereignSignalClient.mqh |
//|             Native TCP (NDJSON) subscriber for the Python brain  |
//|                                                                  |
//| No DLL imports. Connects to the Python TCP bridge, which emits    |
//| one JSON object per line. Designed for non-blocking use in        |
//| OnTick() with automatic reconnect and a 3-second stale heartbeat. |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_SIGNAL_CLIENT_MQH__
#define __SOVEREIGN_SIGNAL_CLIENT_MQH__

#include <Sovereign\SovereignError.mqh>

class CSovereignSignalClient
{
private:
   int      m_socket;
   string   m_host;
   int      m_port;
   uint     m_connectTimeoutMs;
   int      m_reconnectSeconds;
   bool     m_connected;
   datetime m_lastHeartbeat;
   datetime m_lastConnectAttempt;

   string   m_buffer;
   string   m_action;
   string   m_symbol;
   string   m_timestamp;
   double   m_confidence;
   double   m_poc;
   double   m_rangePosition;
   double   m_volatilityRatio;
   string   m_command;       // Python->EA command: PAUSE, RESUME, FLAT_ALL, RISK_SCALE, VETO_ENTRY
   string   m_commandSymbol; // optional symbol filter ("*" = all, "" = all)
   double   m_commandValue;  // numeric argument for RISK_SCALE etc.
   long     m_payloadCount;
   long     m_parseErrors;
   long     m_socketErrors;
   long     m_connectErrors;
   long     m_readErrors;
   long     m_messagesReceived;
   int      m_lastReadLatencyMs;

   int FindValueStart(const string json, const string key) const
   {
      string needle = "\"" + key + "\"";
      int pos = StringFind(json, needle);
      if(pos < 0) return -1;

      pos += StringLen(needle);
      int len = StringLen(json);
      while(pos < len)
      {
         ushort c = StringGetCharacter(json, pos);
         if(c == ':' || c == ' ' || c == '\t' || c == '\r' || c == '\n')
            pos++;
         else
            break;
      }
      return (pos < len) ? pos : -1;
   }

   string JsonString(const string json, const string key) const
   {
      int pos = FindValueStart(json, key);
      if(pos < 0) return "";
      if(StringGetCharacter(json, pos) != '"') return "";

      int start = pos + 1;
      int end = StringFind(json, "\"", start);
      if(end < 0) return "";
      return StringSubstr(json, start, end - start);
   }

   double JsonNumber(const string json, const string key) const
   {
      int pos = FindValueStart(json, key);
      if(pos < 0) return 0.0;

      int start = pos;
      int len = StringLen(json);
      while(pos < len)
      {
         ushort c = StringGetCharacter(json, pos);
         bool numeric = (c >= '0' && c <= '9') || c == '-' || c == '+' ||
                        c == '.' || c == 'e' || c == 'E';
         if(!numeric) break;
         pos++;
      }
      if(pos == start) return 0.0;
      return StringToDouble(StringSubstr(json, start, pos - start));
   }

   bool JsonBool(const string json, const string key) const
   {
      int pos = FindValueStart(json, key);
      if(pos < 0) return false;
      return (StringSubstr(json, pos, 4) == "true");
   }

   bool ParsePayload(const string json)
   {
      //--- Log raw JSON payload at DEBUG level
      if(LogLevelEnabled(LOG_DEBUG))
      {
         string truncated = json;
         if(StringLen(truncated) > 512)
            truncated = StringSubstr(truncated, 0, 512) + "...";
         SovLogDebug(StringFormat("SignalClient RX payload: %s", truncated));
      }

      string msgType = JsonString(json, "t");
      string action  = JsonString(json, "action");

      //--- heartbeat / signal payload (existing format)
      if(msgType == "" && action != "")
      {
         if(!JsonBool(json, "heartbeat"))
         {
            m_parseErrors++;
            SovLogWarn("SignalClient ParsePayload: missing heartbeat flag, payload ignored");
            return false;
         }
         m_action          = action;
         m_symbol          = JsonString(json, "symbol");
         m_timestamp       = JsonString(json, "timestamp");
         m_confidence      = JsonNumber(json, "confidence");
         m_poc             = JsonNumber(json, "poc");
         m_rangePosition   = JsonNumber(json, "range_position");
         m_volatilityRatio = JsonNumber(json, "volatility_ratio");
         m_command         = "";
         m_commandValue    = 0.0;
         m_lastHeartbeat   = TimeCurrent();
         m_payloadCount++;
         m_messagesReceived++;
         return true;
      }

      //--- command payload: {"t":"command","cmd":"PAUSE","v":0.5,"s":"GBPUSD"}
      if(msgType == "command")
      {
         m_command  = JsonString(json, "cmd");
         m_commandSymbol = JsonString(json, "s");
         m_commandValue = JsonNumber(json, "v");
         if(m_command == "")
         {
            m_parseErrors++;
            SovLogWarn("SignalClient ParsePayload: command missing 'cmd' field, payload ignored");
            return false;
         }
         // clear signal fields on command
         m_action          = "";
         m_symbol          = "";
         m_timestamp       = "";
         m_confidence      = 0.0;
         m_poc             = 0.0;
         m_rangePosition   = 0.0;
         m_volatilityRatio = 0.0;
         m_lastHeartbeat   = TimeCurrent();
         m_payloadCount++;
         m_messagesReceived++;
         PrintFormat("Sovereign telemetry: command received cmd=%s symbol=%s v=%.4f",
                     m_command, m_commandSymbol, m_commandValue);
         return true;
      }

      m_parseErrors++;
      SovLogWarn("SignalClient ParsePayload: unrecognised message type, payload ignored");
      return false;
   }

   void ProcessBuffer()
   {
      int newline = -1;
      while((newline = StringFind(m_buffer, "\n")) >= 0)
      {
         string line = StringSubstr(m_buffer, 0, newline);
         m_buffer = StringSubstr(m_buffer, newline + 1);
         StringTrimLeft(line);
         StringTrimRight(line);
         if(StringLen(line) > 0)
            ParsePayload(line);
      }

      //--- Defensive cap: a malformed publisher must not exhaust memory.
      if(StringLen(m_buffer) > 65536)
      {
         Print("Sovereign telemetry: oversized partial frame discarded.");
         m_buffer = "";
      }
   }

    void Connect()
   {
      m_lastConnectAttempt = TimeCurrent();

      int socket = SocketCreate();
      if(socket == INVALID_HANDLE)
      {
         m_connectErrors++;
         SovLogWarn("SignalClient Connect SocketCreate failed");
         return;
      }

      if(!SocketConnect(socket, m_host, m_port, m_connectTimeoutMs))
      {
         m_connectErrors++;
         SocketClose(socket);
         SovLogWarn("SignalClient Connect SocketConnect to " + m_host + ":" + IntegerToString(m_port));
         return;
      }

      m_socket = socket;
      m_connected = true;
      m_buffer = "";
      m_lastHeartbeat = 0;
      PrintFormat("Sovereign telemetry connected to %s:%d", m_host, m_port);
   }

public:
   CSovereignSignalClient()
   {
      m_socket = INVALID_HANDLE;
      m_host = "127.0.0.1";
      m_port = 5556;
      m_connectTimeoutMs = 1000;
      m_reconnectSeconds = 30;
      m_connected = false;
      m_lastHeartbeat = 0;
      m_lastConnectAttempt = 0;
      m_buffer = "";
      m_action = "";
      m_symbol = "";
      m_timestamp = "";
      m_confidence = 0.0;
      m_poc = 0.0;
      m_rangePosition = 0.0;
      m_volatilityRatio = 0.0;
      m_payloadCount = 0;
      m_parseErrors = 0;
      m_socketErrors = 0;
      m_connectErrors = 0;
      m_readErrors = 0;
      m_messagesReceived = 0;
      m_lastReadLatencyMs = 0;
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

   void Deinit()
   {
      Disconnect();
   }

   void Disconnect()
   {
      if(m_socket != INVALID_HANDLE)
      {
         SocketClose(m_socket);
         m_socket = INVALID_HANDLE;
      }
      if(m_connected)
         Print("Sovereign telemetry disconnected.");
      m_connected = false;
      m_lastHeartbeat = 0;
      m_buffer = "";
   }

   //--- Call once per tick. Never blocks beyond the socket read timeout.
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
         SovLogWarn("SignalClient Poll: socket no longer connected");
         Disconnect();
         return;
      }

      uint available = SocketIsReadable(m_socket);
      if(available == 0)
         return;

      uchar data[];
      int requested = (available > 8192) ? 8192 : (int)available;
      ArrayResize(data, requested);
      ulong readStart = GetTickCount64();
      int bytes = SocketRead(m_socket, data, requested, 100);
      ulong readEnd = GetTickCount64();
      m_lastReadLatencyMs = (int)(readEnd - readStart);
      if(bytes < 0)
      {
         m_readErrors++;
         SovLogError("SignalClient Poll", "SocketRead failed err=" + IntegerToString(GetLastError()) +
                     " latency=" + IntegerToString(m_lastReadLatencyMs) + "ms");
         Disconnect();
         return;
      }
      if(bytes == 0)
      {
         //--- peer closed the connection
         Disconnect();
         return;
      }

      m_buffer += CharArrayToString(data, 0, bytes, CP_UTF8);
      ProcessBuffer();
   }

   bool     IsConnected() const  { return m_connected; }
   bool     IsHealthy(const int maxAgeSeconds = 3) const
   {
      if(!m_connected || m_lastHeartbeat == 0) return false;
      return ((TimeCurrent() - m_lastHeartbeat) <= maxAgeSeconds);
   }

   int      HeartbeatAgeSeconds() const
   {
      if(m_lastHeartbeat == 0) return -1;
      return (int)(TimeCurrent() - m_lastHeartbeat);
   }

   string   Action() const          { return m_action; }
   string   Symbol() const          { return m_symbol; }
   string   Timestamp() const       { return m_timestamp; }
   double   Confidence() const      { return m_confidence; }
   double   Poc() const             { return m_poc; }
   double   RangePosition() const   { return m_rangePosition; }
   double   VolatilityRatio() const { return m_volatilityRatio; }
    long     PayloadCount() const    { return m_payloadCount; }
    long     ParseErrors() const     { return m_parseErrors; }
    long     MessagesReceived() const { return m_messagesReceived; }
    int      LastReadLatencyMs() const { return m_lastReadLatencyMs; }

   void     ResetSignal()
   {
      m_action = "";
      m_confidence = 0.0;
      m_poc = 0.0;
      m_rangePosition = 0.0;
      m_volatilityRatio = 0.0;
      m_timestamp = "";
      m_command = "";
      m_commandSymbol = "";
      m_commandValue = 0.0;
   }

   //--- command accessors
   string   Command() const       { return m_command; }
   string   CommandSymbol() const { return m_commandSymbol; }
   double   CommandValue() const  { return m_commandValue; }
   bool     HasCommand() const    { return (m_command != ""); }
   //--- returns true if this command applies to the given symbol
   bool     CommandAppliesTo(const string symbol) const
   {
      if(m_command == "") return false;
      if(m_commandSymbol == "" || m_commandSymbol == "*") return true;
      return (StringCompare(m_commandSymbol, symbol, 0) == 0);
   }
   void     ClearCommand()        { m_command = ""; m_commandSymbol = ""; m_commandValue = 0.0; }
};

#endif // __SOVEREIGN_SIGNAL_CLIENT_MQH__
