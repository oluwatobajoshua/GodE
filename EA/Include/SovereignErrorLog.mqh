//+------------------------------------------------------------------+
//|                                   SovereignErrorLog.mqh          |
//|  Always-open error log file for the Sovereign EA stack.           |
//|  Keeps a persistent file handle (opened once at Init, closed at   |
//|  Deinit) so error writes never pay open/close overhead per event. |
//|  Rotates at ~1 MB like SovereignAuditLog. All errors are          |
//|  journaled to MQL5\Files\Sovereign\error_log.csv.                 |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_ERROR_LOG_MQH__
#define __SOVEREIGN_ERROR_LOG_MQH__

#include <Trade\Trade.mqh>

class CSovereignErrorLog
{
private:
   bool   m_enabled;
   string m_folder;
   int    m_handle;
   ulong  m_maxSizeBytes;

   void RotateIfNeeded()
   {
      if(m_handle == INVALID_HANDLE) return;
      if(FileSize(m_handle) < m_maxSizeBytes) return;
      FileClose(m_handle);
      m_handle = OpenAppend();
   }

   int OpenAppend()
   {
      string path = m_folder + "\\error_log.csv";
      bool existed = FileIsExist(path);
      int h = FileOpen(path,
                       FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI |
                       FILE_SHARE_READ | FILE_SHARE_WRITE);
      if(h == INVALID_HANDLE)
      {
         PrintFormat("Sovereign error log: cannot open %s (error %d)", path, GetLastError());
         return INVALID_HANDLE;
      }
      FileSeek(h, 0, SEEK_END);
      if(!existed || FileSize(h) == 0)
         FileWriteString(h, "server_time,context,action,error_code,error_description\n");
      FileFlush(h);
      return h;
   }

public:
   CSovereignErrorLog()
   {
      m_enabled = false;
      m_folder = "Sovereign";
      m_handle = INVALID_HANDLE;
      m_maxSizeBytes = 1000000;
   }

   bool Init(const bool enabled, const string folder = "Sovereign", const ulong maxSizeBytes = 1000000)
   {
      m_enabled = enabled;
      m_folder = folder;
      m_maxSizeBytes = maxSizeBytes;
      if(!enabled) return true;

      if(!FolderCreate(m_folder))
      {
         //--- FolderCreate returns false if folder already exists; FileOpen will surface real failures.
      }

      m_handle = OpenAppend();
      return (m_handle != INVALID_HANDLE);
   }

   void Deinit()
   {
      if(m_handle != INVALID_HANDLE)
      {
         FileClose(m_handle);
         m_handle = INVALID_HANDLE;
      }
   }

   bool Enabled() const { return m_enabled; }

   void LogError(const string context, const string action = "")
   {
      if(!m_enabled) return;
      if(m_handle == INVALID_HANDLE) return;

      int err = GetLastError();
      string desc = IntegerToString(err);

      string line = StringFormat(
         "%s,%s,%s,%d,%s",
         TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS | TIME_MINUTES),
         Csv(context),
         Csv(action),
         err,
         Csv(desc));

      FileWriteString(m_handle, line + "\n");
      FileFlush(m_handle);
      RotateIfNeeded();
   }

   void LogWarn(const string message)
   {
      if(!m_enabled) return;
      if(m_handle == INVALID_HANDLE) return;

      string line = StringFormat(
         "%s,WARN,,0,%s",
         TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS | TIME_MINUTES),
         Csv(message));

      FileWriteString(m_handle, line + "\n");
      FileFlush(m_handle);
      RotateIfNeeded();
   }

   void LogInfo(const string message)
   {
      if(!m_enabled) return;
      if(m_handle == INVALID_HANDLE) return;

      string line = StringFormat(
         "%s,INFO,,0,%s",
         TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS | TIME_MINUTES),
         Csv(message));

      FileWriteString(m_handle, line + "\n");
      FileFlush(m_handle);
      RotateIfNeeded();
   }

private:
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
};

#endif // __SOVEREIGN_ERROR_LOG_MQH__
