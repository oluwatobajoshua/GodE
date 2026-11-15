//+------------------------------------------------------------------+
//|                                   SovereignPatterns.mqh          |
//|  Swing structure (zigzag) and harmonic ratio matching.            |
//|                                                                  |
//|  CONTEXT ONLY. Nothing in this file can open, size, or close a    |
//|  trade. It exists so the chart can show the shape a human would   |
//|  notice (higher highs / lower lows, Gartley / Bat / Butterfly /   |
//|  Crab / ABCD) next to the EA's real, mechanical signals.          |
//|                                                                  |
//|  The project rule stands: a pattern is only allowed to affect     |
//|  trading if it clears the certification harness. Until then it is  |
//|  a drawing, and it is labelled as one on the chart.               |
//+------------------------------------------------------------------+
#ifndef __SOVEREIGN_PATTERNS_MQH__
#define __SOVEREIGN_PATTERNS_MQH__

#define SOV_PATTERN_CONTEXT_TAG  "context only - not an entry trigger"

struct SovPivot
{
   datetime time;
   double   price;
   int      kind;      // +1 swing high, -1 swing low
};

struct SovPattern
{
   bool     valid;
   string   name;
   int      side;      // +1 bullish (D is a low), -1 bearish
   double   prZ;       // B retrace of XA
   double   prC;       // C retrace of AB
   double   prD;       // D extension of BC
   double   prXD;      // D retrace of XA
   string   text;
};

class CSovereignPatterns
{
private:
   static bool Between(const double v, const double lo, const double hi)
   {
      return (v >= lo && v <= hi);
   }

   static double Ratio(const double num, const double den)
   {
      if(den <= 0.0)
         return 0.0;
      return num / den;
   }

public:
   //--- ATR-thresholded zigzag over completed bars -------------------
   static int BuildZigZag(const string sym, const ENUM_TIMEFRAMES tf,
                          const int lookback, const double atrMult,
                          SovPivot &out[])
   {
      ArrayResize(out, 0);
      int bars = MathMax(20, MathMin(lookback, 400));

      int hAtr = iATR(sym, tf, 14);
      if(hAtr == INVALID_HANDLE)
         return 0;
      double atr[];
      ArraySetAsSeries(atr, true);
      bool okAtr = (CopyBuffer(hAtr, 0, 1, 1, atr) >= 1);
      IndicatorRelease(hAtr);
      if(!okAtr || atr[0] <= 0.0)
         return 0;

      double h[], l[];
      datetime t[];
      ArraySetAsSeries(h, true);
      ArraySetAsSeries(l, true);
      ArraySetAsSeries(t, true);
      if(CopyHigh(sym, tf, 1, bars, h) < bars)
         return 0;
      if(CopyLow(sym, tf, 1, bars, l) < bars)
         return 0;
      if(CopyTime(sym, tf, 1, bars, t) < bars)
         return 0;

      double thr = atr[0] * MathMax(0.1, atrMult);
      int    kind = 0;
      double ext = 0.0;
      datetime extT = 0;

      //--- oldest bar first
      for(int i = bars - 1; i >= 0; i--)
      {
         if(kind == 0)
         {
            kind = 1;
            ext  = h[i];
            extT = t[i];
            continue;
         }
         if(kind > 0)
         {
            if(h[i] > ext)
            {
               ext  = h[i];
               extT = t[i];
            }
            else if(l[i] < ext - thr)
            {
               int n = ArraySize(out);
               ArrayResize(out, n + 1);
               out[n].time  = extT;
               out[n].price = ext;
               out[n].kind  = 1;
               kind = -1;
               ext  = l[i];
               extT = t[i];
            }
         }
         else
         {
            if(l[i] < ext)
            {
               ext  = l[i];
               extT = t[i];
            }
            else if(h[i] > ext + thr)
            {
               int n = ArraySize(out);
               ArrayResize(out, n + 1);
               out[n].time  = extT;
               out[n].price = ext;
               out[n].kind  = -1;
               kind = 1;
               ext  = h[i];
               extT = t[i];
            }
         }
      }
      int n2 = ArraySize(out);
      ArrayResize(out, n2 + 1);
      out[n2].time  = extT;
      out[n2].price = ext;
      out[n2].kind  = kind;

      //--- keep the most recent 8 legs
      int total = ArraySize(out);
      if(total > 8)
      {
         int start = total - 8;
         SovPivot tmp[];
         ArrayResize(tmp, 8);
         for(int i = 0; i < 8; i++)
            tmp[i] = out[start + i];
         ArrayResize(out, 8);
         for(int i = 0; i < 8; i++)
            out[i] = tmp[i];
      }
      return ArraySize(out);
   }

   //--- match the last X-A-B-C-D against classic ratios --------------
   static bool MatchHarmonic(const SovPivot &piv[], SovPattern &pat)
   {
      pat.valid = false;
      pat.name  = "";
      pat.side  = 0;
      pat.prZ   = 0.0;
      pat.prC   = 0.0;
      pat.prD   = 0.0;
      pat.prXD  = 0.0;
      pat.text  = "";

      int n = ArraySize(piv);
      if(n < 5)
         return false;

      int iX = n - 5, iA = n - 4, iB = n - 3, iC = n - 2, iD = n - 1;
      double x = piv[iX].price, a = piv[iA].price, b = piv[iB].price;
      double c = piv[iC].price, d = piv[iD].price;

      //--- legs must alternate
      if(piv[iX].kind == piv[iA].kind || piv[iA].kind == piv[iB].kind
         || piv[iB].kind == piv[iC].kind || piv[iC].kind == piv[iD].kind)
         return false;

      double xa = MathAbs(a - x);
      double ab = MathAbs(b - a);
      double bc = MathAbs(c - b);
      double cd = MathAbs(d - c);
      double xd = MathAbs(d - a);
      if(xa <= 0.0 || ab <= 0.0 || bc <= 0.0 || cd <= 0.0)
         return false;

      double z  = Ratio(ab, xa);
      double y  = Ratio(bc, ab);
      double w  = Ratio(cd, bc);
      double xdr = Ratio(xd, xa);

      pat.prZ  = z;
      pat.prC  = y;
      pat.prD  = w;
      pat.prXD = xdr;
      pat.side = (piv[iD].kind < 0) ? 1 : -1;

      string nm = "";
      if(Between(z, 0.55, 0.68) && Between(y, 0.35, 0.90)
         && Between(w, 1.10, 1.70) && Between(xdr, 0.70, 0.86))
         nm = "Gartley";
      else if(Between(z, 0.35, 0.52) && Between(y, 0.35, 0.90)
              && Between(w, 1.55, 2.70) && Between(xdr, 0.84, 0.94))
         nm = "Bat";
      else if(Between(z, 0.74, 0.82) && Between(y, 0.35, 0.90)
              && Between(w, 1.55, 2.30) && Between(xdr, 1.20, 1.65))
         nm = "Butterfly";
      else if(Between(z, 0.35, 0.65) && Between(y, 0.35, 0.90)
              && Between(w, 2.55, 3.70) && Between(xdr, 1.55, 1.70))
         nm = "Crab";
      else if(Between(z, 0.35, 0.65) && Between(y, 1.25, 1.65)
              && Between(w, 0.60, 1.05))
         nm = "ABCD";

      if(nm == "")
         return false;

      pat.valid = true;
      pat.name  = nm;
      pat.text  = StringFormat("%s %s  B%.2f C%.2f D%.2f XD%.2f  [%s]",
                               nm, (pat.side > 0 ? "bull" : "bear"),
                               z, y, w, xdr, SOV_PATTERN_CONTEXT_TAG);
      return true;
   }
};

#endif
