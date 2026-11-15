//+------------------------------------------------------------------+
//| RingBuffer.mqh — Fixed-size in-memory log queue                   |
//| Non-blocking audit logging with OnTimer async flush              |
//+------------------------------------------------------------------+
#property strict

#include <Collections\List.mqh>

class CRingBuffer
   {
   private:
      CList       m_buf;        // underlying storage
      int         m_capacity;   // max entries
      int         m_head;       // write index (overwrites old)
      int         m_count;      // current entries
      long        m_totalWrites; // lifetime write count

   public:
      void CRingBuffer(const int capacity = 500)
         {
         m_capacity = MathMax(10, capacity);
         m_head = 0;
         m_count = 0;
         m_totalWrites = 0;
         }

      //--- push an entry (overwrites oldest when full)
      void Push(const string entry)
         {
         string ts = TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS);
         string line = ts + " | " + entry;

         if(m_count < m_capacity)
            {
            m_buf.Add(line);
            m_count++;
            }
         else
            {
            m_buf.At(m_head) = line;
            }

         m_head = (m_head + 1) % m_capacity;
         m_totalWrites++;
         }

      //--- flush all entries to a callback (called from OnTimer)
      void Flush(void (&callback)(const string &line[]))
         {
         if(m_count == 0)
            return;

         string lines[];
         ArrayResize(lines, m_count);

         int idx = (m_head + m_capacity - m_count) % m_capacity;
         for(int i = 0; i < m_count; i++)
            {
            lines[i] = m_buf.At(idx);
            idx = (idx + 1) % m_capacity;
            }

         callback(lines);

         //--- clear after flush
         m_buf.Clear();
         m_head = 0;
         m_count = 0;
         }

      int Count() const { return m_count; }
      long TotalWrites() const { return m_totalWrites; }
      int Capacity() const { return m_capacity; }
   };

//+------------------------------------------------------------------+
//| Global audit buffer + CSV flush helper                           |
//+------------------------------------------------------------------+
CRingBuffer g_auditBuf(500);

void AuditFlushToCSV(const string &lines[])
   {
      int handle = FileOpen("Sovereign\\audit_trail.csv", FILE_CSV | FILE_WRITE | FILE_COMMON, ',');
      if(handle > 0)
         {
         for(int i = 0; i < ArraySize(lines); i++)
            FileWriteString(handle, lines[i] + "\n");
         FileClose(handle);
         }
   }

void AuditPush(const string entry)
   {
      g_auditBuf.Push(entry);
   }
