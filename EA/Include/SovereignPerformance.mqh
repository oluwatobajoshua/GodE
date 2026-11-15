//+------------------------------------------------------------------+
//|                                      SovereignPerformance.mqh    |
//|  Self-monitoring performance governor for the Sovereign EA.       |
//|                                                                  |
//|  Tracks closed-trade R multiples by setup bucket, forgets stale    |
//|  evidence, blocks persistently negative buckets, cuts risk during  |
//|  drawdowns and loss streaks, and gives blocked buckets a timed      |
//|  probation retry instead of banning them forever.                 |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_PERFORMANCE_MQH__
#define __SOVEREIGN_PERFORMANCE_MQH__

#include <Trade/PositionInfo.mqh>
#include <Sovereign\SovereignError.mqh>

#define SOV_PERF_MAX_BUCKETS 64
#define SOV_PERF_MAX_META    64
#define SOV_PERF_RECENT      30
#define SOV_PERF_MASTER_KEY  "SOV|MASTER"

struct SovPerfBucket
{
   string   key;
   double   n_eff;
   double   wins_eff;
   double   sum_r_eff;
   datetime blocked_until;
   datetime last_update;
};

struct SovPerfMeta
{
   ulong    order_ticket;
   ulong    position_id;
   string   bucket;
   double   risk_money;
   double   profit;
   int      side;
   int      mode;
   double   ml_prob;
   string   features;
   bool     finalized;
};

class CSovereignPerformance
{
private:
   bool     m_enabled;
   string   m_folder;
   string   m_symbol;
   long     m_magic;
   double   m_hwm;
   int      m_loss_streak;
   int      m_recent_n;
   int      m_recent_idx;
   double   m_recent_r[SOV_PERF_RECENT];

   double   m_dd_reduce_start;
   double   m_dd_limit;
   int      m_loss_streak_limit;
   double   m_decay;
   double   m_prior_n;
   double   m_prior_sum_r;
   datetime m_last_save;

   SovPerfBucket m_buckets[];
   SovPerfMeta   m_meta[];

   string BucketPath()  const { return m_folder + "\\performance_buckets.csv"; }
   string GlobalPath()  const { return m_folder + "\\performance_global.csv"; }
   string OutcomePath() const { return m_folder + "\\trade_outcomes.csv"; }

   int FindBucket(const string key) const
   {
      for(int i = 0; i < ArraySize(m_buckets); i++)
         if(m_buckets[i].key == key) return i;
      return -1;
   }

   int EnsureBucket(const string key)
   {
      int idx = FindBucket(key);
      if(idx >= 0) return idx;
      int n = ArraySize(m_buckets);
      if(n >= SOV_PERF_MAX_BUCKETS) return 0;
      ArrayResize(m_buckets, n + 1);
      m_buckets[n].key = key;
      m_buckets[n].n_eff = 0.0;
      m_buckets[n].wins_eff = 0.0;
      m_buckets[n].sum_r_eff = 0.0;
      m_buckets[n].blocked_until = 0;
      m_buckets[n].last_update = 0;
      return n;
   }

   int FindMetaByOrder(const ulong order_ticket) const
   {
      if(order_ticket == 0) return -1;
      for(int i = 0; i < ArraySize(m_meta); i++)
         if(m_meta[i].order_ticket == order_ticket) return i;
      return -1;
   }

   int FindMetaByPosition(const ulong position_id) const
   {
      if(position_id == 0) return -1;
      for(int i = 0; i < ArraySize(m_meta); i++)
         if(m_meta[i].position_id == position_id) return i;
      return -1;
   }

   void RemoveMeta(const int idx)
   {
      int n = ArraySize(m_meta);
      if(idx < 0 || idx >= n) return;
      for(int i = idx; i < n - 1; i++)
         m_meta[i] = m_meta[i + 1];
      ArrayResize(m_meta, n - 1);
   }

   double RecentAverage() const
   {
      if(m_recent_n <= 0) return 0.0;
      double sum = 0.0;
      for(int i = 0; i < m_recent_n; i++) sum += m_recent_r[i];
      return sum / (double)m_recent_n;
   }

void SaveBuckets()
   {
      if(!m_enabled) return;
      SovResetLastError();
      int h = FileOpen(BucketPath(), FILE_WRITE | FILE_TXT | FILE_ANSI |
                       FILE_SHARE_READ | FILE_SHARE_WRITE);
      if(h == INVALID_HANDLE)
      {
         SovLogWarn("SaveBuckets: cannot open " + BucketPath() + " err=" + IntegerToString(GetLastError()));
         return;
      }
      SovResetLastError();
      if(FileWriteString(h, "key,n_eff,wins_eff,sum_r_eff,blocked_until,last_update\r\n") <= 0)
      {
         SovLogWarn("SaveBuckets: write header failed err=" + IntegerToString(GetLastError()));
         FileClose(h);
         return;
      }
      for(int i = 0; i < ArraySize(m_buckets); i++)
      {
         string line = StringFormat("%s,%.9f,%.9f,%.9f,%I64d,%I64d\r\n",
                         m_buckets[i].key,
                         m_buckets[i].n_eff,
                         m_buckets[i].wins_eff,
                         m_buckets[i].sum_r_eff,
                         (long)m_buckets[i].blocked_until,
                         (long)m_buckets[i].last_update);
         if(FileWriteString(h, line) <= 0)
         {
            SovLogWarn("SaveBuckets: write row " + IntegerToString(i) + " failed err=" + IntegerToString(GetLastError()));
            break;
         }
      }
      SovResetLastError();
      FileClose(h);
      if(GetLastError() != 0)
         SovLogWarn("SaveBuckets: FileClose failed err=" + IntegerToString(GetLastError()));
   }

   void SaveGlobal()
   {
      if(!m_enabled) return;
      SovResetLastError();
      int h = FileOpen(GlobalPath(), FILE_WRITE | FILE_TXT | FILE_ANSI |
                       FILE_SHARE_READ | FILE_SHARE_WRITE);
      if(h == INVALID_HANDLE)
      {
         SovLogWarn("SaveGlobal: cannot open " + GlobalPath() + " err=" + IntegerToString(GetLastError()));
         return;
      }
      SovResetLastError();
      if(FileWriteString(h, "hwm,loss_streak,recent_n,recent_idx,last_save\r\n") <= 0)
      {
         SovLogWarn("SaveGlobal: write header failed err=" + IntegerToString(GetLastError()));
         FileClose(h);
         return;
      }
      string line = StringFormat("%.2f,%d,%d,%d,%I64d\r\n",
                       m_hwm, m_loss_streak, m_recent_n, m_recent_idx,
                       (long)m_last_save);
      if(FileWriteString(h, line) <= 0)
         SovLogWarn("SaveGlobal: write row failed err=" + IntegerToString(GetLastError()));
      SovResetLastError();
      FileClose(h);
      if(GetLastError() != 0)
         SovLogWarn("SaveGlobal: FileClose failed err=" + IntegerToString(GetLastError()));
   }

   void AppendOutcome(const SovPerfMeta &meta, const double r_multiple, const double profit)
   {
      if(!m_enabled) return;
      bool existed = FileIsExist(OutcomePath());
      SovResetLastError();
      int h = FileOpen(OutcomePath(), FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI |
                       FILE_SHARE_READ | FILE_SHARE_WRITE);
      if(h == INVALID_HANDLE)
      {
         SovLogWarn("AppendOutcome: cannot open " + OutcomePath() + " err=" + IntegerToString(GetLastError()));
         return;
      }
      FileSeek(h, 0, SEEK_END);
      if(!existed || FileSize(h) == 0)
      {
         SovResetLastError();
         if(FileWriteString(h, "time,position_id,bucket,side,mode,risk_money,profit,r_multiple,ml_prob,features\r\n") <= 0)
         {
            SovLogWarn("AppendOutcome: write header failed err=" + IntegerToString(GetLastError()));
            FileClose(h);
            return;
         }
      }
      SovResetLastError();
      string line = StringFormat("%s,%I64u,%s,%d,%d,%.2f,%.2f,%.6f,%.6f,%s\r\n",
                       TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
                       meta.position_id, meta.bucket, meta.side, meta.mode,
                       meta.risk_money, profit, r_multiple, meta.ml_prob, meta.features);
      if(FileWriteString(h, line) <= 0)
         SovLogWarn("AppendOutcome: write row failed err=" + IntegerToString(GetLastError()));
      SovResetLastError();
      FileClose(h);
      if(GetLastError() != 0)
         SovLogWarn("AppendOutcome: FileClose failed err=" + IntegerToString(GetLastError()));
   }

   void LoadBuckets()
   {
      if(!FileIsExist(BucketPath())) return;
      SovResetLastError();
      int h = FileOpen(BucketPath(), FILE_READ | FILE_TXT | FILE_ANSI |
                       FILE_SHARE_READ | FILE_SHARE_WRITE);
      if(h == INVALID_HANDLE)
      {
         SovLogWarn("LoadBuckets: cannot open " + BucketPath() + " err=" + IntegerToString(GetLastError()));
         return;
      }
      FileReadString(h); // header
      while(!FileIsEnding(h))
      {
         string line = FileReadString(h);
         if(StringLen(line) == 0) continue;
         string p[];
         if(StringSplit(line, ',', p) < 6) continue;
         int idx = EnsureBucket(p[0]);
         m_buckets[idx].n_eff = StringToDouble(p[1]);
         m_buckets[idx].wins_eff = StringToDouble(p[2]);
         m_buckets[idx].sum_r_eff = StringToDouble(p[3]);
         m_buckets[idx].blocked_until = (datetime)StringToInteger(p[4]);
         m_buckets[idx].last_update = (datetime)StringToInteger(p[5]);
      }
      SovResetLastError();
      FileClose(h);
      if(GetLastError() != 0)
         SovLogWarn("LoadBuckets: FileClose failed err=" + IntegerToString(GetLastError()));
   }

   void LoadGlobal()
   {
      if(!FileIsExist(GlobalPath())) return;
      SovResetLastError();
      int h = FileOpen(GlobalPath(), FILE_READ | FILE_TXT | FILE_ANSI |
                       FILE_SHARE_READ | FILE_SHARE_WRITE);
      if(h == INVALID_HANDLE)
      {
         SovLogWarn("LoadGlobal: cannot open " + GlobalPath() + " err=" + IntegerToString(GetLastError()));
         return;
      }
      FileReadString(h); // header
      string line = FileReadString(h);
      if(StringLen(line) > 0)
      {
         string p[];
         if(StringSplit(line, ',', p) >= 5)
         {
            m_hwm = StringToDouble(p[0]);
            m_loss_streak = (int)StringToInteger(p[1]);
            m_recent_n = (int)StringToInteger(p[2]);
            m_recent_idx = (int)StringToInteger(p[3]);
            m_last_save = (datetime)StringToInteger(p[4]);
            if(m_recent_idx < 0 || m_recent_idx >= SOV_PERF_RECENT) m_recent_idx = 0;
            if(m_recent_n < 0) m_recent_n = 0;
            if(m_recent_n > SOV_PERF_RECENT) m_recent_n = SOV_PERF_RECENT;
         }
      }
      SovResetLastError();
      FileClose(h);
      if(GetLastError() != 0)
         SovLogWarn("LoadGlobal: FileClose failed err=" + IntegerToString(GetLastError()));
   }

   void UpdateBucket(const string key, const double r_multiple)
   {
      int idx = EnsureBucket(key);
      SovPerfBucket b = m_buckets[idx];
      b.n_eff = b.n_eff * m_decay + 1.0;
      b.wins_eff = b.wins_eff * m_decay + (r_multiple > 0 ? 1.0 : 0.0);
      b.sum_r_eff = b.sum_r_eff * m_decay + r_multiple;
      b.last_update = TimeCurrent();
      m_buckets[idx] = b;
      SaveBuckets();
   }

   void UpdateGlobal(const double r_multiple)
   {
      m_loss_streak = (r_multiple > 0) ? 0 : m_loss_streak + 1;
      m_recent_r[m_recent_idx] = r_multiple;
      m_recent_idx = (m_recent_idx + 1) % SOV_PERF_RECENT;
      if(m_recent_n < SOV_PERF_RECENT) m_recent_n++;
      SaveGlobal();
   }

   void FinalizeMeta(const int idx)
   {
      if(idx < 0 || idx >= ArraySize(m_meta)) return;
      if(m_meta[idx].finalized) return;
m_meta[idx].finalized = true;

       double profit = m_meta[idx].profit;
       double risk = m_meta[idx].risk_money;
      double r = (risk > 0.0) ? profit / risk : 0.0;

      UpdateBucket(m_meta[idx].bucket, r);
      UpdateBucket(SOV_PERF_MASTER_KEY, r);
      UpdateGlobal(r);
      AppendOutcome(m_meta[idx], r, profit);
      RemoveMeta(idx);
   }

   string DealBucketKey(const string comment, const string symbol) const
   {
      if(StringFind(comment, "SOV-CARRY-MOM") >= 0)
         return "SOV-CARRY-MOM|" + symbol;
      if(StringFind(comment, "SOV-XSEC") >= 0)
         return "SOV-XSEC|" + symbol;
      return "SOV-LEG|" + symbol;
   }

   void RecordClosedDeal(const string bucket, const double profit,
                         const double risk_money)
   {
      double risk = (risk_money > 0.0) ? risk_money
                     : AccountInfoDouble(ACCOUNT_EQUITY) * 0.01;
      if(risk == 0.0 && GetLastError() != 0)
         SovLogWarn("RecordClosedDeal: AccountInfoDouble(ACCOUNT_EQUITY) failed err=" + IntegerToString(GetLastError()));
      double r = (risk > 0.0) ? profit / risk : 0.0;
      UpdateBucket(bucket, r);
      UpdateBucket(SOV_PERF_MASTER_KEY, r);
      UpdateGlobal(r);
   }

public:
   CSovereignPerformance()
   {
      m_enabled = false;
      m_folder = "Sovereign";
      m_symbol = "";
      m_magic = 0;
      m_hwm = 0.0;
      m_loss_streak = 0;
      m_recent_n = 0;
      m_recent_idx = 0;
      m_dd_reduce_start = 3.0;
      m_dd_limit = 12.0;
      m_loss_streak_limit = 3;
      m_decay = 0.97;
      m_prior_n = 10.0;
      m_prior_sum_r = -0.20;
      m_last_save = 0;
   }

   bool Init(const bool enabled, const string symbol, const long magic,
             const double dd_reduce_start, const double dd_limit,
             const int loss_streak_limit, const string folder = "Sovereign")
   {
      m_enabled = enabled;
      m_folder = folder;
      m_symbol = symbol;
      m_magic = magic;
      m_dd_reduce_start = MathMax(0.1, dd_reduce_start);
      m_dd_limit = MathMax(m_dd_reduce_start + 0.1, dd_limit);
      m_loss_streak_limit = MathMax(1, loss_streak_limit);
      m_hwm = AccountInfoDouble(ACCOUNT_EQUITY);
      if(m_hwm == 0.0 && GetLastError() != 0)
         SovLogWarn("Init: AccountInfoDouble(ACCOUNT_EQUITY) failed err=" + IntegerToString(GetLastError()));
      if(!m_enabled) return true;

      SovResetLastError();
      if(!FolderCreate(m_folder))
         SovLogWarn("Init: FolderCreate(" + m_folder + ") failed err=" + IntegerToString(GetLastError()));
      LoadGlobal();
      LoadBuckets();
      if(m_hwm <= 0.0)
      {
         m_hwm = AccountInfoDouble(ACCOUNT_EQUITY);
         if(m_hwm == 0.0 && GetLastError() != 0)
            SovLogWarn("Init: AccountInfoDouble(ACCOUNT_EQUITY) failed err=" + IntegerToString(GetLastError()));
      }
      m_last_save = TimeCurrent();
      SovLogInfo("Sovereign governor: loaded " + IntegerToString(ArraySize(m_buckets)) +
                 " buckets, HWM=" + DoubleToString(m_hwm, 2) + ", loss streak=" +
                 IntegerToString(m_loss_streak) + ".");
      return true;
   }

   void Deinit()
   {
      if(!m_enabled) return;
      SaveBuckets();
      SaveGlobal();
   }

   void Reset()
   {
      ArrayResize(m_buckets, 0);
      ArrayResize(m_meta, 0);
      m_hwm = AccountInfoDouble(ACCOUNT_EQUITY);
      m_loss_streak = 0;
      m_recent_n = 0;
      m_recent_idx = 0;
      ArrayInitialize(m_recent_r, 0.0);
      if(m_enabled)
      {
         SovResetLastError();
         if(!FileDelete(BucketPath()))
            SovLogWarn("Reset: FileDelete(" + BucketPath() + ") failed err=" + IntegerToString(GetLastError()));
         if(!FileDelete(GlobalPath()))
            SovLogWarn("Reset: FileDelete(" + GlobalPath() + ") failed err=" + IntegerToString(GetLastError()));
         if(!FileDelete(OutcomePath()))
            SovLogWarn("Reset: FileDelete(" + OutcomePath() + ") failed err=" + IntegerToString(GetLastError()));
      }
      SovLogInfo("Sovereign governor: state reset.");
   }

   bool Enabled() const { return m_enabled; }
   double HighWaterMark() const { return m_hwm; }
   int LossStreak() const { return m_loss_streak; }

   string BucketKey(const int mode, const int side,
                    const double atr_ratio, const int utc_hour) const
   {
      string vol = "N";
      if(atr_ratio < 0.85) vol = "L";
      else if(atr_ratio > 1.20) vol = "H";
      string session = "N";
      if(utc_hour < 7) session = "A";
      else if(utc_hour < 12) session = "L";
      return StringFormat("%d|%d|%s|%s", mode, (side > 0 ? 1 : -1), vol, session);
   }

   double GlobalMultiplier()
   {
      if(!m_enabled) return 1.0;
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      if(equity == 0.0 && GetLastError() != 0)
         SovLogWarn("GlobalMultiplier: AccountInfoDouble(ACCOUNT_EQUITY) failed err=" + IntegerToString(GetLastError()));
      if(equity > m_hwm) m_hwm = equity;
      double dd = (m_hwm > 0.0) ? ((m_hwm - equity) / m_hwm) * 100.0 : 0.0;

      if(dd >= m_dd_limit) return 0.0;
      if(dd >= (m_dd_reduce_start + m_dd_limit) * 0.5) return 0.25;
      if(dd >= m_dd_reduce_start) return 0.5;

      if(m_loss_streak >= m_loss_streak_limit * 2) return 0.25;
      if(m_loss_streak >= m_loss_streak_limit) return 0.5;

      if(m_recent_n >= 10 && RecentAverage() < -0.10) return 0.5;
      return 1.0;
   }

   double BucketMultiplier(const string key, const bool allow_block = true)
   {
      if(!m_enabled) return 1.0;
      int idx = EnsureBucket(key);
      SovPerfBucket b = m_buckets[idx];
      datetime now = TimeCurrent();

      if(b.blocked_until > 0 && now < b.blocked_until) return 0.0;

      //--- Time-boxed block expired: give the bucket a small probation trial.
      if(b.blocked_until > 0 && now >= b.blocked_until)
      {
         b.n_eff = 0.0;
         b.wins_eff = 0.0;
         b.sum_r_eff = 0.0;
          b.blocked_until = 0;
          b.last_update = now;
          m_buckets[idx] = b;
          SaveBuckets();
          SovLogInfo("Sovereign governor: bucket " + key + " probation retry.");
          return 0.5;
      }

      double n = b.n_eff;
      double win_rate = (b.wins_eff + 5.0) / (n + 10.0);
      double score = (b.sum_r_eff + m_prior_sum_r) / (n + m_prior_n);
      const bool fxBucket = (StringFind(key, "SOV-FXMOPOL|") == 0);
      const double blockN = fxBucket ? 8.0 : 12.0;
      const double blockScore = fxBucket ? -0.03 : -0.05;
      const double wrBlock = fxBucket ? 0.38 : 0.34;

      if(n >= blockN)
      {
         if(allow_block && (score <= blockScore || win_rate < wrBlock))
         {
            b.blocked_until = now + 14 * 86400;
            m_buckets[idx] = b;
            SaveBuckets();
            SovLogInfo("Sovereign governor: bucket " + key + " blocked 14d (n=" +
                       DoubleToString(n, 1) + " score=" + DoubleToString(score, 3) +
                       " wr=" + DoubleToString(win_rate, 2) + ").");
            return 0.0;
         }
         if(score < 0.02) return 0.5;
         if(score >= 0.12 && win_rate >= 0.50) return 1.0;
         return 0.75;
      }

      if(fxBucket && n >= 5.0 && score < -0.12) return 0.0;
      if(n >= 6.0 && score < -0.10) return 0.5;
      return 0.75;
   }

   double ExpectancyMultiplier() const
   {
      if(!m_enabled || m_recent_n < 8)
         return 1.0;
      double avg = RecentAverage();
      if(avg <= -0.20)
         return 0.55;
      if(avg <= -0.08)
         return 0.75;
      if(avg >= 0.15)
         return 1.12;
      if(avg >= 0.06)
         return 1.06;
      return 1.0;
   }

   static string LegBucket(const string family, const string symbol)
   {
      return family + "|" + symbol;
   }

   static string FeatureBucket(const string family, const string symbol,
                               const string setup, const int dayOfWeek, const int hour)
   {
      return family + "|" + symbol + "|" + setup + "|d" + IntegerToString(dayOfWeek)
             + "|h" + IntegerToString(hour);
   }

   double CombinedMultiplier(const string key)
   {
      double global = GlobalMultiplier();
      if(global <= 0.0)
         return 0.0;
      double master = BucketMultiplier(SOV_PERF_MASTER_KEY);
      if(master <= 0.0)
         return 0.0;
      double bucket = BucketMultiplier(key);
      double base = MathMin(global, MathMin(master, bucket));
      return base * ExpectancyMultiplier();
   }

   bool AllowNewRisk(const string legKey)
   {
      if(!m_enabled)
         return true;
      return (CombinedMultiplier(legKey) > 0.01);
   }

   void BootstrapFromHistory(const long magic, const int lookbackDays,
                             const double nominalRiskPct)
   {
      if(!m_enabled || lookbackDays <= 0 || magic == 0)
         return;
      if(ArraySize(m_buckets) > 0)
      {
         Print("Sovereign governor: skip history bootstrap; bucket CSV already loaded.");
         return;
      }

      datetime from = TimeCurrent() - (datetime)lookbackDays * 86400;
      SovResetLastError();
      if(!HistorySelect(from, TimeCurrent()))
      {
         SovLogWarn("BootstrapFromHistory: HistorySelect failed err=" + IntegerToString(GetLastError()));
         return;
      }

      int seeded = 0;
      const int total = HistoryDealsTotal();
      for(int i = 0; i < total; i++)
      {
         ulong ticket = HistoryDealGetTicket(i);
         if(ticket == 0)
            continue;
         SovResetLastError();
         if((long)HistoryDealGetInteger(ticket, DEAL_MAGIC) != magic)
            continue;
         if(HistoryDealGetInteger(ticket, DEAL_ENTRY) != DEAL_ENTRY_OUT)
            continue;

         double profit = HistoryDealGetDouble(ticket, DEAL_PROFIT);
         profit += HistoryDealGetDouble(ticket, DEAL_SWAP);
         profit += HistoryDealGetDouble(ticket, DEAL_COMMISSION);
         string sym = HistoryDealGetString(ticket, DEAL_SYMBOL);
         string comment = HistoryDealGetString(ticket, DEAL_COMMENT);
         string bucket = DealBucketKey(comment, sym);
         double risk = AccountInfoDouble(ACCOUNT_EQUITY) * nominalRiskPct / 100.0;
         RecordClosedDeal(bucket, profit, risk);
         seeded++;
      }
      if(seeded > 0)
      {
         SovLogInfo("Sovereign governor: bootstrapped " + IntegerToString(seeded) +
                    " closed deals from " + IntegerToString(lookbackDays) + "-day history.");
         SaveBuckets();
         SaveGlobal();
      }
   }

   string StatusLine() const
   {
      if(!m_enabled)
         return "governor=off";
      double exp = ExpectancyMultiplier();
      return StringFormat("governor HWM=%.0f streak=%d recentR=%.3f expMult=%.2f",
                          m_hwm, m_loss_streak,
                          (m_recent_n > 0 ? RecentAverage() : 0.0), exp);
   }

    void OnTick()
   {
      if(!m_enabled) return;
      SovResetLastError();
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      if(equity == 0.0 && GetLastError() != 0)
      {
         SovLogWarn("OnTick: AccountInfoDouble(ACCOUNT_EQUITY) failed err=" + IntegerToString(GetLastError()));
         return;
      }
      if(equity > m_hwm) m_hwm = equity;
      PrunePositions();
      if(TimeCurrent() - m_last_save >= 300)
      {
         SaveGlobal();
         m_last_save = TimeCurrent();
      }
   }

   void RegisterPosition(const ulong position_id, const string bucket,
                         const double risk_money, const int side, const int mode,
                         const double ml_prob, const string features_csv)
   {
      if(!m_enabled || position_id == 0) return;
      int idx = FindMetaByPosition(position_id);
      if(idx < 0)
      {
         int n = ArraySize(m_meta);
         if(n >= SOV_PERF_MAX_META) return;
         ArrayResize(m_meta, n + 1);
         idx = n;
         m_meta[idx].order_ticket = 0;
      }
      m_meta[idx].position_id = position_id;
      m_meta[idx].bucket = bucket;
      m_meta[idx].risk_money = risk_money;
      m_meta[idx].profit = 0.0;
      m_meta[idx].side = side;
      m_meta[idx].mode = mode;
      m_meta[idx].ml_prob = ml_prob;
      m_meta[idx].features = features_csv;
      m_meta[idx].finalized = false;
   }

   void RegisterOrder(const ulong order_ticket, const string bucket,
                      const double risk_money, const int side, const int mode,
                      const double ml_prob, const string features_csv)
   {
      if(!m_enabled || order_ticket == 0) return;
      int idx = FindMetaByOrder(order_ticket);
      if(idx < 0)
      {
         int n = ArraySize(m_meta);
         if(n >= SOV_PERF_MAX_META) return;
         ArrayResize(m_meta, n + 1);
         idx = n;
         m_meta[idx].position_id = 0;
      }
      m_meta[idx].order_ticket = order_ticket;
      m_meta[idx].bucket = bucket;
      m_meta[idx].risk_money = risk_money;
      m_meta[idx].profit = 0.0;
      m_meta[idx].side = side;
      m_meta[idx].mode = mode;
      m_meta[idx].ml_prob = ml_prob;
      m_meta[idx].features = features_csv;
      m_meta[idx].finalized = false;
   }

    void OnTradeTransaction(const MqlTradeTransaction &trans)
   {
      if(!m_enabled) return;
      if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;
      SovResetLastError();
      if(!HistoryDealSelect(trans.deal))
      {
         SovLogWarn("OnTradeTransaction: HistoryDealSelect(" + IntegerToString((long)trans.deal) +
                    ") failed err=" + IntegerToString(GetLastError()));
         return;
      }

      SovResetLastError();
      long entry = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
      if(entry < 0 && GetLastError() != 0)
      {
         SovLogWarn("OnTradeTransaction: HistoryDealGetInteger(DEAL_ENTRY) failed err=" + IntegerToString(GetLastError()));
         return;
      }
      ulong order_ticket = (ulong)HistoryDealGetInteger(trans.deal, DEAL_ORDER);
      ulong position_id = (ulong)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);

      if(entry == DEAL_ENTRY_IN)
      {
         int idx = FindMetaByOrder(order_ticket);
         if(idx >= 0 && m_meta[idx].position_id == 0)
            m_meta[idx].position_id = position_id;
         return;
      }

      int idx = FindMetaByPosition(position_id);
      if(idx >= 0)
      {
         double profit = HistoryDealGetDouble(trans.deal, DEAL_PROFIT);
         profit += HistoryDealGetDouble(trans.deal, DEAL_SWAP);
         profit += HistoryDealGetDouble(trans.deal, DEAL_COMMISSION);
         m_meta[idx].profit += profit;
         if(!PositionSelectByTicket(position_id))
            FinalizeMeta(idx);
         return;
      }

      SovResetLastError();
      if(m_magic != 0 && (long)HistoryDealGetInteger(trans.deal, DEAL_MAGIC) == m_magic)
      {
         double profit = HistoryDealGetDouble(trans.deal, DEAL_PROFIT);
         profit += HistoryDealGetDouble(trans.deal, DEAL_SWAP);
         profit += HistoryDealGetDouble(trans.deal, DEAL_COMMISSION);
         string sym = HistoryDealGetString(trans.deal, DEAL_SYMBOL);
         string comment = HistoryDealGetString(trans.deal, DEAL_COMMENT);
         string bucket = DealBucketKey(comment, sym);
         RecordClosedDeal(bucket, profit, 0.0);
      }
   }

   void PrunePositions()
   {
      if(!m_enabled) return;
      for(int i = ArraySize(m_meta) - 1; i >= 0; i--)
      {
         if(m_meta[i].finalized)
         {
            RemoveMeta(i);
            continue;
         }
         if(m_meta[i].position_id == 0)
         {
            if(m_meta[i].order_ticket > 0 && !OrderSelect(m_meta[i].order_ticket))
               RemoveMeta(i);
            continue;
         }
         if(!PositionSelectByTicket(m_meta[i].position_id))
            FinalizeMeta(i);
      }
   }
};

#endif // __SOVEREIGN_PERFORMANCE_MQH__
