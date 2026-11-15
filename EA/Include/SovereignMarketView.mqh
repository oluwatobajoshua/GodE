//+------------------------------------------------------------------+
//|                                  SovereignMarketView.mqh          |
//|  "What the chart is doing right now", expressed with the SAME     |
//|  indicators the loaded chart template uses (EMA21/50, SMMA100,     |
//|  RSI9, fractals, pivot S/R, fib).                                 |
//|                                                                  |
//|  READ-ONLY. Nothing here places, sizes, or blocks an order. It is  |
//|  a description of the tape, not a signal.                         |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_MARKET_VIEW_MQH__
#define __SOVEREIGN_MARKET_VIEW_MQH__

struct SovMarketView
{
   bool     ok;
   string   symbol;
   ENUM_TIMEFRAMES tf;
   datetime barTime;
   double   price;
   double   atr;
   double   emaFast;
   double   emaSlow;
   double   smma;
   double   rsi;
   int      trend;          // +1 stacked up, -1 stacked down, 0 mixed
   int      bias;           // +1 long plan, -1 short plan, 0 wait
   bool     extended;       // price far from EMA21 (no chase)
   double   distEmaAtr;
   double   swingHi;
   double   swingLo;
   double   fib50;
   double   fib618;
   double   s1;
   double   r1;
   double   target;
   double   invalidation;
   double   zoneLo;
   double   zoneHi;
   string   state;
   string   plan;
   string   targetText;
   string   invalidText;
   string   zoneText;
};

class CSovereignMarketView
{
public:
   static string Fmt(const string sym, const double price)
   {
      int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      if(digits <= 0)
         digits = 5;
      return DoubleToString(price, digits);
   }

   static bool Build(const string sym, const ENUM_TIMEFRAMES tf,
                     const int emaFastP, const int emaSlowP, const int smmaP,
                     const int rsiP, const int swingBars,
                     SovMarketView &v)
   {
      v.ok = false;
      v.symbol = sym;
      v.tf = tf;
      v.trend = 0;
      v.bias = 0;
      v.extended = false;
      v.distEmaAtr = 999.0;
      v.atr = 0.0;
      v.rsi = 0.0;
      v.emaFast = 0.0;
      v.emaSlow = 0.0;
      v.smma = 0.0;
      v.swingHi = 0.0;
      v.swingLo = 0.0;
      v.fib50 = 0.0;
      v.fib618 = 0.0;
      v.s1 = 0.0;
      v.r1 = 0.0;
      v.target = 0.0;
      v.invalidation = 0.0;
      v.zoneLo = 0.0;
      v.zoneHi = 0.0;
      v.state = "";
      v.plan = "";
      v.targetText = "";
      v.invalidText = "";
      v.zoneText = "";

      int fastP = MathMax(2, emaFastP);
      int slowP = MathMax(3, emaSlowP);
      int smmaP2 = MathMax(4, smmaP);
      int rsiP2 = MathMax(2, rsiP);
      int sw = MathMax(5, swingBars);
      int need = MathMax(sw + 5, MathMax(MathMax(fastP, slowP), MathMax(smmaP2, 60)) + 5);
      if(need > 2000)
         need = 2000;

      double c[], h[], l[];
      ArraySetAsSeries(c, true);
      ArraySetAsSeries(h, true);
      ArraySetAsSeries(l, true);
      if(CopyClose(sym, tf, 1, need, c) < need)
         return false;
      if(CopyHigh(sym, tf, 1, need, h) < need)
         return false;
      if(CopyLow(sym, tf, 1, need, l) < need)
         return false;

      v.price = c[0];
      v.barTime = iTime(sym, tf, 1);

      //--- ATR14
      int hAtr = iATR(sym, tf, 14);
      if(hAtr == INVALID_HANDLE)
         return false;
      double atr[];
      ArraySetAsSeries(atr, true);
      bool okAtr = (CopyBuffer(hAtr, 0, 1, 1, atr) >= 1);
      IndicatorRelease(hAtr);
      if(!okAtr || atr[0] <= 0.0)
         return false;
      v.atr = atr[0];

      //--- EMA fast / EMA slow / SMMA
      int hFast = iMA(sym, tf, fastP, 0, MODE_EMA, PRICE_CLOSE);
      int hSlow = iMA(sym, tf, slowP, 0, MODE_EMA, PRICE_CLOSE);
      int hSmma = iMA(sym, tf, smmaP2, 0, MODE_SMMA, PRICE_CLOSE);
      if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE || hSmma == INVALID_HANDLE)
      {
         if(hFast != INVALID_HANDLE) IndicatorRelease(hFast);
         if(hSlow != INVALID_HANDLE) IndicatorRelease(hSlow);
         if(hSmma != INVALID_HANDLE) IndicatorRelease(hSmma);
         return false;
      }
      double eFast[], eSlow[], eSmma[];
      ArraySetAsSeries(eFast, true);
      ArraySetAsSeries(eSlow, true);
      ArraySetAsSeries(eSmma, true);
      bool okMa = (CopyBuffer(hFast, 0, 1, 3, eFast) >= 3
                   && CopyBuffer(hSlow, 0, 1, 2, eSlow) >= 2
                   && CopyBuffer(hSmma, 0, 1, 1, eSmma) >= 1);
      IndicatorRelease(hFast);
      IndicatorRelease(hSlow);
      IndicatorRelease(hSmma);
      if(!okMa)
         return false;
      v.emaFast = eFast[0];
      v.emaSlow = eSlow[0];
      v.smma = eSmma[0];

      //--- RSI
      int hRsi = iRSI(sym, tf, rsiP2, PRICE_CLOSE);
      if(hRsi == INVALID_HANDLE)
         return false;
      double rsi[];
      ArraySetAsSeries(rsi, true);
      bool okRsi = (CopyBuffer(hRsi, 0, 1, 1, rsi) >= 1);
      IndicatorRelease(hRsi);
      if(okRsi)
         v.rsi = rsi[0];

      //--- swing range (completed bars)
      v.swingHi = h[1];
      v.swingLo = l[1];
      for(int i = 2; i < sw; i++)
      {
         if(h[i] > v.swingHi) v.swingHi = h[i];
         if(l[i] < v.swingLo) v.swingLo = l[i];
      }
      double span = v.swingHi - v.swingLo;
      v.fib50 = v.swingLo + span * 0.5;
      v.fib618 = v.swingLo + span * 0.618;

      //--- classic daily pivot S1/R1
      double dh[], dl[], dc[];
      ArraySetAsSeries(dh, true);
      ArraySetAsSeries(dl, true);
      ArraySetAsSeries(dc, true);
      if(CopyHigh(sym, PERIOD_D1, 1, 1, dh) >= 1
         && CopyLow(sym, PERIOD_D1, 1, 1, dl) >= 1
         && CopyClose(sym, PERIOD_D1, 1, 1, dc) >= 1)
      {
         double pivot = (dh[0] + dl[0] + dc[0]) / 3.0;
         v.s1 = 2.0 * pivot - dh[0];
         v.r1 = 2.0 * pivot - dl[0];
      }

      //--- state
      bool up = (c[0] > v.emaFast && v.emaFast > v.emaSlow && v.emaSlow > v.smma);
      bool dn = (c[0] < v.emaFast && v.emaFast < v.emaSlow && v.emaSlow < v.smma);
      v.trend = up ? 1 : (dn ? -1 : 0);
      v.distEmaAtr = MathAbs(c[0] - v.emaFast) / v.atr;
      v.extended = (v.distEmaAtr > 1.15);

      if(up)
         v.state = "UPTREND  price>EMA>EMA>SMMA";
      else if(dn)
         v.state = "DOWNTREND  price<EMA<EMA<SMMA";
      else
         v.state = "RANGE / MIXED  stack not aligned";

      //--- plan (advisory description, not an order)
      if(up && v.rsi > 78.0)
      {
         v.bias = 0;
         v.plan = "UPTREND but RSI overbought - wait pullback";
      }
      else if(up)
      {
         v.bias = 1;
         v.plan = v.extended
                  ? "LONG bias - wait pullback to EMA zone"
                  : "LONG bias - trend intact, buy near EMA";
      }
      else if(dn && v.rsi < 22.0)
      {
         v.bias = 0;
         v.plan = "DOWNTREND but RSI oversold - wait bounce";
      }
      else if(dn)
      {
         v.bias = -1;
         v.plan = v.extended
                  ? "SHORT bias - wait bounce to EMA zone"
                  : "SHORT bias - trend intact, sell near EMA";
      }
      else
      {
         v.bias = 0;
         v.plan = "NO TRADE - range; wait for stack/breakout";
      }

      //--- zone / target / invalidation
      v.zoneLo = v.emaFast - v.atr * 0.25;
      v.zoneHi = v.emaFast + v.atr * 0.25;
      if(v.bias > 0)
      {
         v.target = (c[0] < v.swingHi) ? v.swingHi : v.r1;
         v.invalidation = MathMin(v.swingLo, v.emaFast - v.atr);
      }
      else if(v.bias < 0)
      {
         v.target = (c[0] > v.swingLo) ? v.swingLo : v.s1;
         v.invalidation = MathMax(v.swingHi, v.emaFast + v.atr);
      }
      else
      {
         v.target = 0.0;
         v.invalidation = 0.0;
      }

      v.zoneText = StringFormat("zone %s-%s", Fmt(sym, v.zoneLo), Fmt(sym, v.zoneHi));
      if(v.target > 0.0)
         v.targetText = StringFormat("target %s", Fmt(sym, v.target));
      if(v.invalidation > 0.0)
         v.invalidText = StringFormat("invalid < %s", Fmt(sym, v.invalidation));

      v.ok = true;
      return true;
   }
};

#endif
