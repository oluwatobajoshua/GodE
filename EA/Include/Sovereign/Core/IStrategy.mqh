//+------------------------------------------------------------------+
//| IStrategy.mqh — Abstract strategy interface                      |
//| Pure mathematical signal evaluation, zero execution/UI deps      |
//+------------------------------------------------------------------+
#property strict

enum ENUM_SIGNAL
   {
   SIGNAL_NONE   = 0,
   SIGNAL_BUY    = 1,
   SIGNAL_SELL   = -1
   };

class IStrategy
   {
   public:
      virtual ~IStrategy() {}

      //--- evaluate current bar, return signal + human-readable summary
      virtual ENUM_SIGNAL Evaluate(const double &close[], const double &high[],
                                   const double &low[], const double &rsi[],
                                   const double &ema21[], const double &ema50[],
                                   const double &atr[]) = 0;

      virtual string GetEvaluationSummary() const = 0;

      virtual string Name() const = 0;

      virtual void Reset() = 0;
   };
