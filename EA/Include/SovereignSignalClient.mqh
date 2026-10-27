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
   long     m_payloadCount;
   long     m_parseErrors;

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
      string action = JsonString(json, "action");
      if(action == "")
      {
         m_parseErrors++;
         return false;
      }
      if(!JsonBool(json, "heartbeat"))
      {
         m_parseErrors++;
         return false;
      }

      m_action          = action;
      m_symbol          = JsonString(json, "symbol");
      m_timestamp       = JsonString(json, "timestamp");
      m_confidence      = JsonNumber(json, "confidence");
      m_poc             = JsonNumber(json, "poc");
      m_rangePosition   = JsonNumber(json, "range_position");
      m_volatilityRatio = JsonNumber(json, "volatility_ratio");
      m_lastHeartbeat   = TimeCurrent();
      m_payloadCount++;
      return true;
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
         return;

      if(!SocketConnect(socket, m_host, m_port, m_connectTimeoutMs))
      {
         SocketClose(socket);
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
      m_reconnectSeconds = 2;
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
   }

   bool Init(const string host, const int port, const uint connectTimeoutMs = 1000,
             const int reconnectSeconds = 2)
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
         Disconnect();
         return;
      }

      uint available = SocketIsReadable(m_socket);
      if(available == 0)
         return;

      uchar data[];
      int requested = (available > 8192) ? 8192 : (int)available;
      ArrayResize(data, requested);
      int bytes = SocketRead(m_socket, data, requested, 100);
      if(bytes <= 0)
         return;

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

   void     ResetSignal()
   {
      m_action = "";
      m_confidence = 0.0;
      m_poc = 0.0;
      m_rangePosition = 0.0;
      m_volatilityRatio = 0.0;
      m_timestamp = "";
   }
};

#endif // __SOVEREIGN_SIGNAL_CLIENT_MQH__
