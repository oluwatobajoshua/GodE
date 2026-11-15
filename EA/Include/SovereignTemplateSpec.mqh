//+------------------------------------------------------------------+
//|                                   SovereignTemplateSpec.mqh      |
//|                                                                  |
//|  Reads the user's chart template at RUNTIME and exposes it to    |
//|  the EA, so changing the template changes the bot's read and its  |
//|  drawings without a recompile.                                    |
//|                                                                  |
//|  Why a sidecar file: MQL5 FileOpen is sandboxed to MQL5\Files and |
//|  Common\Files. It cannot open Profiles\Templates, so the .tpl is  |
//|  converted to a flat ASCII KEY=VALUE spec by                       |
//|  scripts\sync_template.ps1 into                                 |
//|    Common\Files\Sovereign\template\<name>.ini                     |
//|                                                                  |
//|  The template is the shared visual vocabulary: which averages     |
//|  exist, in what colour, and where the RSI band lines sit. If the  |
//|  bot draws something the template does not contain, the user and  |
//|  the bot are not looking at the same chart.                       |
//|                                                                  |
//|  This is a READ-ONLY input. It never changes the trade rule; it   |
//|  only tells the EA what the chart looks like.                     |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_TEMPLATE_SPEC_MQH__
#define __SOVEREIGN_TEMPLATE_SPEC_MQH__

#include <Sovereign\SovereignError.mqh>

#define SOV_TPL_MAX_MA          8
#define SOV_TPL_MAX_RSI_LEVELS  8
#define SOV_TPL_MAX_KEYS        128

class CSovereignTemplateSpec
{
private:
   string m_name;
   string m_keys[];
   string m_vals[];
   int    m_count;
   long   m_mtime;
   bool   m_loaded;

   int    m_maHandle[SOV_TPL_MAX_MA];
   int    m_maPeriod[SOV_TPL_MAX_MA];
   int    m_maMethod[SOV_TPL_MAX_MA];
   color  m_maColor[SOV_TPL_MAX_MA];
   int    m_maCount;

   int    m_rsiHandle;
   int    m_rsiPeriod;

   //--- helpers -----------------------------------------------------
   string FilePath(const string name) const
   {
      return "Sovereign\\template\\" + name + ".ini";
   }

   void Reset()
   {
      m_count = 0;
      ArrayResize(m_keys, 0);
      ArrayResize(m_vals, 0);
      m_maCount   = 0;
      m_rsiPeriod = 0;
      for(int i = 0; i < SOV_TPL_MAX_MA; i++)
      {
         m_maHandle[i] = INVALID_HANDLE;
         m_maPeriod[i] = 0;
         m_maMethod[i] = (int)MODE_EMA;
         m_maColor[i]  = clrWhite;
      }
      m_rsiHandle = INVALID_HANDLE;
   }

   void ReleaseHandles()
   {
      for(int i = 0; i < SOV_TPL_MAX_MA; i++)
      {
         if(m_maHandle[i] != INVALID_HANDLE)
         {
            IndicatorRelease(m_maHandle[i]);
            m_maHandle[i] = INVALID_HANDLE;
         }
      }
      if(m_rsiHandle != INVALID_HANDLE)
      {
         IndicatorRelease(m_rsiHandle);
         m_rsiHandle = INVALID_HANDLE;
      }
   }

   void Put(const string key, const string val)
   {
      for(int i = 0; i < m_count; i++)
      {
         if(m_keys[i] == key)
         {
            m_vals[i] = val;
            return;
         }
      }
      if(m_count >= SOV_TPL_MAX_KEYS)
         return;
      ArrayResize(m_keys, m_count + 1, 32);
      ArrayResize(m_vals, m_count + 1, 32);
      m_keys[m_count] = key;
      m_vals[m_count] = val;
      m_count++;
   }

   string Get(const string key, const string def = "") const
   {
      for(int i = 0; i < m_count; i++)
         if(m_keys[i] == key)
            return m_vals[i];
      return def;
   }

   int GetInt(const string key, const int def = 0) const
   {
      string s = Get(key, "");
      if(s == "")
         return def;
      return (int)StringToInteger(s);
   }

   double GetDouble(const string key, const double def = 0.0) const
   {
      string s = Get(key, "");
      if(s == "")
         return def;
      return StringToDouble(s);
   }

   static int HexDigit(const ushort ch)
   {
      if(ch >= '0' && ch <= '9') return (int)(ch - '0');
      if(ch >= 'a' && ch <= 'f') return (int)(ch - 'a') + 10;
      if(ch >= 'A' && ch <= 'F') return (int)(ch - 'A') + 10;
      return -1;
   }

   //--- "#rrggbb" (COLORREF hex from the template parser) -> MQL5 color
   static color HexToColor(const string hex, const color def = clrWhite)
   {
      string s = hex;
      StringTrimLeft(s);
      StringTrimRight(s);
      if(StringLen(s) < 7 || StringGetCharacter(s, 0) != '#')
         return def;

      int rgb[3];
      for(int i = 0; i < 3; i++)
      {
         int hi = HexDigit(StringGetCharacter(s, 1 + i * 2));
         int lo = HexDigit(StringGetCharacter(s, 2 + i * 2));
         if(hi < 0 || lo < 0)
            return def;
         rgb[i] = hi * 16 + lo;
      }
      int r = rgb[0], g = rgb[1], b = rgb[2];
      return (color)((b << 16) | (g << 8) | r);
   }

   void ApplyDefaults()
   {
      Put("SYMBOL", "EURUSD");
      Put("TIMEFRAME", "H1");
      Put("COLOR_BG", "#000000");
      Put("COLOR_FG", "#ffffff");
      Put("COLOR_UP", "#00ff00");
      Put("COLOR_DOWN", "#ff0000");
      Put("COLOR_GRID", "#778899");
      Put("MA_COUNT", "3");
      Put("MA0_PERIOD", "21");  Put("MA0_METHOD", "EMA");  Put("MA0_COLOR", "#00ffff");
      Put("MA1_PERIOD", "50");  Put("MA1_METHOD", "EMA");  Put("MA1_COLOR", "#ffd700");
      Put("MA2_PERIOD", "100"); Put("MA2_METHOD", "SMMA"); Put("MA2_COLOR", "#ffffff");
      Put("FRACTAL_ENABLED", "1");
      Put("FRACTAL_COLOR", "#ffffff");
      Put("FRACTAL_UP_ARROW", "217");
      Put("FRACTAL_DOWN_ARROW", "218");
      Put("RSI_PERIOD", "9");
      Put("RSI_COLOR", "#1e90ff");
      Put("RSI_LEVEL_COUNT", "3");
      Put("RSI_LEVEL_0", "20");
      Put("RSI_LEVEL_1", "50");
      Put("RSI_LEVEL_2", "80");
   }

   void BuildIndicators()
   {
      ReleaseHandles();

      m_maCount = GetInt("MA_COUNT", 0);
      if(m_maCount > SOV_TPL_MAX_MA)
         m_maCount = SOV_TPL_MAX_MA;

      for(int i = 0; i < m_maCount; i++)
      {
         string pfx = "MA" + IntegerToString(i) + "_";
         m_maPeriod[i] = GetInt(pfx + "PERIOD", 0);

         string meth = Get(pfx + "METHOD", "EMA");
         ENUM_MA_METHOD mm = MODE_EMA;
         if(meth == "SMA")       mm = MODE_SMA;
         else if(meth == "SMMA") mm = MODE_SMMA;
         else if(meth == "LWMA") mm = MODE_LWMA;
         m_maMethod[i] = (int)mm;
         m_maColor[i]  = HexToColor(Get(pfx + "COLOR", "#ffffff"), clrWhite);

         if(m_maPeriod[i] > 0)
         {
            SovResetLastError();
            m_maHandle[i] = iMA(_Symbol, _Period, m_maPeriod[i], 0, mm, PRICE_CLOSE);
             if(m_maHandle[i] == INVALID_HANDLE)
                SovLogWarn("Template spec: iMA(" + IntegerToString(m_maPeriod[i]) + ") failed");
          }
       }

      m_rsiPeriod = GetInt("RSI_PERIOD", 9);
      if(m_rsiPeriod > 1)
      {
         SovResetLastError();
         m_rsiHandle = iRSI(_Symbol, _Period, m_rsiPeriod, PRICE_CLOSE);
         if(m_rsiHandle == INVALID_HANDLE)
            SovLogWarn("Template spec: iRSI(" + IntegerToString(m_rsiPeriod) + ") failed");
      }
   }

public:
   CSovereignTemplateSpec()
   {
      m_name   = "";
      m_mtime  = 0;
      m_loaded = false;
      Reset();
   }

   ~CSovereignTemplateSpec()
   {
      ReleaseHandles();
   }

   //--- load / reload ------------------------------------------------
   bool Load(const string name = "fxmopol", const bool force = false)
   {
      string fname = FilePath(name);
      long mt = 0;
      ResetOnModifyTime(fname, mt);

      if(!force && m_loaded && mt == m_mtime)
         return true;

      Reset();
      m_name = name;

      int fh = FileOpen(fname, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
      if(fh == INVALID_HANDLE)
      {
         SovLogWarn("Template spec: cannot open " + fname + " err=" + IntegerToString(GetLastError()) +
                    " - using built-in defaults. Run scripts\\sync_template.ps1 to publish it.");
         ApplyDefaults();
         m_loaded = true;
         m_mtime  = 0;
         BuildIndicators();
         return false;
      }

      while(!FileIsEnding(fh))
      {
         string line = FileReadString(fh);
         StringTrimLeft(line);
         StringTrimRight(line);
         if(line == "")
            continue;
         if(StringGetCharacter(line, 0) == '#')
            continue;
         int eq = StringFind(line, "=");
         if(eq <= 0)
            continue;
         string k = StringSubstr(line, 0, eq);
         string v = StringSubstr(line, eq + 1);
         StringTrimLeft(k);
         StringTrimRight(k);
         StringTrimLeft(v);
         StringTrimRight(v);
         Put(k, v);
      }
      SovResetLastError();
      FileClose(fh);
      if(GetLastError() != 0)
         SovLogWarn("Template spec: FileClose failed err=" + IntegerToString(GetLastError()));

      m_mtime  = mt;
      m_loaded = true;
      BuildIndicators();
      return true;
   }

   //--- pick up a template edit without recompiling or restarting
   bool ReloadIfChanged()
   {
      if(m_name == "")
         return false;
      long mt = 0;
      if(!ResetOnModifyTime(FilePath(m_name), mt))
         return false;
      if(mt == m_mtime)
         return false;
      PrintFormat("Template spec %s changed on disk - reloading", m_name);
      Load(m_name, true);
      return true;
   }

   static bool ResetOnModifyTime(const string fname, long &mt)
   {
      mt = FileGetInteger(fname, FILE_MODIFY_DATE, true);
      return (mt > 0);
   }

   //--- accessors ----------------------------------------------------
   bool   Loaded()     const { return m_loaded; }
   string Name()       const { return m_name; }
   string Symbol()     const { return Get("SYMBOL", ""); }
   string Timeframe()  const { return Get("TIMEFRAME", ""); }

   color  Bg()         const { return HexToColor(Get("COLOR_BG", "#000000"), clrBlack); }
   color  Fg()         const { return HexToColor(Get("COLOR_FG", "#ffffff"), clrWhite); }
   color  CandleUp()   const { return HexToColor(Get("COLOR_UP", "#00ff00"), clrLime); }
   color  CandleDown() const { return HexToColor(Get("COLOR_DOWN", "#ff0000"), clrRed); }
   color  Grid()       const { return HexToColor(Get("COLOR_GRID", "#778899"), clrDimGray); }

   int    MaCount()           const { return m_maCount; }
   int    MaPeriod(const int i)  const { return (i >= 0 && i < m_maCount) ? m_maPeriod[i] : 0; }
   int    MaMethod(const int i)  const { return (i >= 0 && i < m_maCount) ? m_maMethod[i] : 0; }
   color  MaColor(const int i)   const { return (i >= 0 && i < m_maCount) ? m_maColor[i] : clrWhite; }
   int    MaHandle(const int i)  const { return (i >= 0 && i < m_maCount) ? m_maHandle[i] : INVALID_HANDLE; }

   double MaValue(const int i, const int shift = 0) const
   {
      if(i < 0 || i >= m_maCount || m_maHandle[i] == INVALID_HANDLE)
         return 0.0;
      double buf[];
      if(CopyBuffer(m_maHandle[i], 0, shift, 1, buf) != 1)
         return 0.0;
      return buf[0];
   }

   bool   FractalEnabled() const { return GetInt("FRACTAL_ENABLED", 1) != 0; }
   color  FractalColor()   const { return HexToColor(Get("FRACTAL_COLOR", "#ffffff"), clrWhite); }
   int    FractalUpArrow()   const { return GetInt("FRACTAL_UP_ARROW", 217); }
   int    FractalDownArrow() const { return GetInt("FRACTAL_DOWN_ARROW", 218); }

   int    RsiPeriod()  const { return m_rsiPeriod; }
   color  RsiColor()   const { return HexToColor(Get("RSI_COLOR", "#1e90ff"), clrDodgerBlue); }
   int    RsiHandle()  const { return m_rsiHandle; }

   int    RsiLevelCount() const
   {
      int c = GetInt("RSI_LEVEL_COUNT", 0);
      return (c > SOV_TPL_MAX_RSI_LEVELS) ? SOV_TPL_MAX_RSI_LEVELS : c;
   }

   double RsiLevel(const int i) const
   {
      return GetDouble("RSI_LEVEL_" + IntegerToString(i), 0.0);
   }

   double RsiValue(const int shift = 0) const
   {
      if(m_rsiHandle == INVALID_HANDLE)
         return 0.0;
      double buf[];
      if(CopyBuffer(m_rsiHandle, 0, shift, 1, buf) != 1)
         return 0.0;
      return buf[0];
   }

   //--- one-line proof for the panel: what the EA actually read
   string Describe() const
   {
      if(!m_loaded)
         return "template: not loaded";
      string s = StringFormat("template %s %s %s", m_name, Symbol(), Timeframe());
      for(int i = 0; i < m_maCount; i++)
         s += StringFormat("  MA%d", MaPeriod(i));
      s += StringFormat("  RSI%d", RsiPeriod());
      return s;
   }
};

#endif // __SOVEREIGN_TEMPLATE_SPEC_MQH__
