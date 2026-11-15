#!/usr/bin/env python3
"""Shared FXMOPOL chart stack: EMA21/50, SMMA100, RSI(9) 20/50/80, fractals, ATR."""
from __future__ import annotations

import numpy as np
import pandas as pd

RSI_PERIOD = 9

# Known-good FX liquidity (GMT on HistData; broker server time in MT5).
# London AM ~07-11 UTC; overlap peak ~13-16 UTC (Oanda/BIS-style depth, not 17+ chop).
PRIME_SESSION_WINDOWS: tuple[tuple[int, int], ...] = ((7, 11), (13, 16))
# Literature: mid-week liquidity (Tue-Thu); Fri volume often weakest (currency futures studies).
DEFAULT_WEEKDAY_MASK = (1 << 1) | (1 << 2) | (1 << 3)  # Tue, Wed, Thu (Mon=0 … Fri=4)


def session_allows_new_entry(hour: int, *, enabled: bool = True) -> bool:
    """New entries only inside PRIME_SESSION_WINDOWS when enabled."""
    if not enabled:
        return True
    return any(start <= hour <= end for start, end in PRIME_SESSION_WINDOWS)


def weekday_allows_new_entry(dow: int, mask: int = DEFAULT_WEEKDAY_MASK) -> bool:
    """dow: pandas weekday 0=Mon … 4=Fri. mask bit i = allow weekday i."""
    if mask == 0:
        return True
    if dow < 0 or dow > 4:
        return False
    return bool(mask & (1 << dow))


def smma(close: pd.Series, period: int) -> pd.Series:
    out = np.full(len(close), np.nan)
    c = close.values.astype(float)
    if len(c) < period:
        return pd.Series(out, index=close.index)
    out[period - 1] = c[:period].mean()
    for i in range(period, len(c)):
        out[i] = (out[i - 1] * (period - 1) + c[i]) / period
    return pd.Series(out, index=close.index)


def rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).rolling(period).mean()
    down = (-delta.clip(upper=0)).rolling(period).mean().replace(0, np.nan)
    rs = up / down
    return 100 - 100 / (1 + rs)


def stack_long(c, e21, e50, s100, i: int) -> bool:
    if any(pd.isna(x.iloc[i]) for x in (e21, e50, s100)):
        return False
    return bool(c.iloc[i] > e21.iloc[i] > e50.iloc[i] and c.iloc[i] > s100.iloc[i])


def stack_short(c, e21, e50, s100, i: int) -> bool:
    if any(pd.isna(x.iloc[i]) for x in (e21, e50, s100)):
        return False
    return bool(c.iloc[i] < e21.iloc[i] < e50.iloc[i] and c.iloc[i] < s100.iloc[i])


def regime_streak(flags: list[bool], i: int, n: int) -> bool:
    if i < n - 1:
        return False
    return all(flags[i - k] for k in range(n))


def ema21_cross_close_long(c, e21, i: int) -> bool:
    """First bar that **closes** above EMA21 after at least one close at/below."""
    if i < 1 or pd.isna(e21.iloc[i]) or pd.isna(e21.iloc[i - 1]):
        return False
    return bool(c.iloc[i] > e21.iloc[i] and c.iloc[i - 1] <= e21.iloc[i - 1])


def ema21_cross_close_short(c, e21, i: int) -> bool:
    if i < 1 or pd.isna(e21.iloc[i]) or pd.isna(e21.iloc[i - 1]):
        return False
    return bool(c.iloc[i] < e21.iloc[i] and c.iloc[i - 1] >= e21.iloc[i - 1])


def ema21_whipsaw_chop(c, e21, i: int, lookback: int = 4, max_flips: int = 2) -> bool:
    """True when EMA21 was crossed back-and-forth recently (no trade)."""
    if i < lookback:
        return True
    flips = 0
    for j in range(i - lookback + 1, i + 1):
        if j < 1 or pd.isna(e21.iloc[j]) or pd.isna(e21.iloc[j - 1]):
            continue
        above_j = c.iloc[j] > e21.iloc[j]
        above_prev = c.iloc[j - 1] > e21.iloc[j - 1]
        if above_j != above_prev:
            flips += 1
    return flips >= max_flips


def ema21_one_bar_fakeout(c, e21, i: int, side: int) -> bool:
    """Cross this bar but prior bar was a one-candle pop on the wrong side."""
    if i < 2 or pd.isna(e21.iloc[i - 2]):
        return False
    if side > 0:
        return bool(
            c.iloc[i - 2] <= e21.iloc[i - 2]
            and c.iloc[i - 1] > e21.iloc[i - 1]
            and c.iloc[i] > e21.iloc[i]
            and (e21.iloc[i - 1] - c.iloc[i - 1]) / abs(e21.iloc[i - 1] - c.iloc[i - 2] + 1e-12) < 0.35
        )
    return bool(
        c.iloc[i - 2] >= e21.iloc[i - 2]
        and c.iloc[i - 1] < e21.iloc[i - 1]
        and c.iloc[i] < e21.iloc[i]
    )


def ema21_cross_bias_long(c, e21, e50, s100, i: int) -> bool:
    return bool(
        stack_long(c, e21, e50, s100, i)
        or (
            not pd.isna(s100.iloc[i])
            and not pd.isna(e50.iloc[i])
            and c.iloc[i] > s100.iloc[i]
            and e21.iloc[i] > e50.iloc[i]
        )
    )


def ema21_cross_bias_short(c, e21, e50, s100, i: int) -> bool:
    return bool(
        stack_short(c, e21, e50, s100, i)
        or (
            not pd.isna(s100.iloc[i])
            and not pd.isna(e50.iloc[i])
            and c.iloc[i] < s100.iloc[i]
            and e21.iloc[i] < e50.iloc[i]
        )
    )


def stop_hunt_reclaim(o, h, l, c, side: int) -> bool:
    body = abs(c - o)
    rng = h - l
    if rng <= 0 or body <= 0:
        return False
    lower = min(o, c) - l
    upper = h - max(o, c)
    if side > 0:
        return lower >= 2.0 * body and c > o
    return upper >= 2.0 * body and c < o


def stop_hunt_reclaim_stack(c, e21, s100, o, h, l, i: int, side: int) -> bool:
    if not stop_hunt_reclaim(float(o.iloc[i]), float(h.iloc[i]), float(l.iloc[i]), float(c.iloc[i]), side):
        return False
    if any(pd.isna(x.iloc[i]) for x in (e21, s100)):
        return False
    if side > 0:
        return bool(c.iloc[i] > e21.iloc[i] and c.iloc[i] > s100.iloc[i])
    return bool(c.iloc[i] < e21.iloc[i] and c.iloc[i] < s100.iloc[i])


def playbook_chart_entry(
    i: int,
    c,
    e21,
    e50,
    s100,
    o,
    h,
    l,
    atr,
    long_flags: list[bool],
    short_flags: list[bool],
    regime_bars: int = 5,
    use_fractals: bool = False,
) -> tuple[int, str]:
    """Returns (side, reason) or (0, ''). Matches SovereignFxmopol PlaybookChartEntry."""
    if regime_streak(long_flags, i, regime_bars):
        if pullback_long(l, c, e21, atr, i):
            return 1, "entry_pullback_ema21"
        hunt = stop_hunt_reclaim_stack(c, e21, s100, o, h, l, i, 1)
        if use_fractals:
            hunt = hunt and fractal_low(l, i - 1)
        if hunt:
            return 1, "entry_stop_hunt_reclaim"
    if regime_streak(short_flags, i, regime_bars):
        if retest_short(h, c, e21, atr, i):
            return -1, "entry_retest_ema21"
        hunt = stop_hunt_reclaim_stack(c, e21, s100, o, h, l, i, -1)
        if use_fractals:
            hunt = hunt and fractal_high(h, i - 1)
        if hunt:
            return -1, "entry_stop_hunt_reject"
    return 0, ""


def pullback_long(l, c, e21, atr, i: int) -> bool:
    if pd.isna(e21.iloc[i]) or pd.isna(atr.iloc[i]):
        return False
    touch = l.iloc[i] <= e21.iloc[i] + atr.iloc[i] * 0.25
    hold = c.iloc[i] > e21.iloc[i]
    return bool(touch and hold)


def retest_short(h, c, e21, atr, i: int) -> bool:
    if pd.isna(e21.iloc[i]) or pd.isna(atr.iloc[i]):
        return False
    touch = h.iloc[i] >= e21.iloc[i] - atr.iloc[i] * 0.25
    reject = c.iloc[i] < e21.iloc[i]
    return bool(touch and reject)


def fractal_low(l: pd.Series, i: int) -> bool:
    if i < 2 or i + 2 >= len(l):
        return False
    v = l.iloc[i]
    return bool(v < l.iloc[i - 1] and v < l.iloc[i - 2] and v < l.iloc[i + 1] and v < l.iloc[i + 2])


def fractal_high(h: pd.Series, i: int) -> bool:
    if i < 2 or i + 2 >= len(h):
        return False
    v = h.iloc[i]
    return bool(v > h.iloc[i - 1] and v > h.iloc[i - 2] and v > h.iloc[i + 1] and v > h.iloc[i + 2])


def criminal_body(o, h, l, c, side: int) -> bool:
    body, rng = abs(c - o), h - l
    if rng <= 0 or body / rng < 0.55:
        return False
    return (side > 0 and c > o and (h - c) / rng <= 0.25) or (side < 0 and c < o and (c - l) / rng <= 0.25)


def build_stack(h1: pd.DataFrame, ema_mid: int = 50) -> dict[str, pd.Series]:
    c = h1["close"].astype(float)
    h = h1["high"].astype(float)
    l = h1["low"].astype(float)
    o = h1["open"].astype(float)
    e21 = c.ewm(span=21, adjust=False).mean()
    e50 = c.ewm(span=ema_mid, adjust=False).mean()
    s100 = smma(c, 100)
    r = rsi(c)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    return {
      "open": o,
      "high": h,
      "low": l,
      "close": c,
      "ema21": e21,
      "ema50": e50,
      "smma100": s100,
      "rsi": r,
      "atr": atr,
  }


def chop_near_smma(c, s100, atr, i: int) -> bool:
    if pd.isna(s100.iloc[i]) or pd.isna(atr.iloc[i]) or atr.iloc[i] <= 0:
        return False
    return abs(c.iloc[i] - s100.iloc[i]) <= atr.iloc[i] * 0.35


def chart_state_at(
    i: int,
    c,
    e21,
    e50,
    s100,
    r,
    atr,
    long_flags: list[bool],
    short_flags: list[bool],
    regime_bars: int = 5,
) -> tuple[str, str, str]:
    """Returns (state, grade for long, grade for short). grade: GOOD / WAIT / NO_TRADE."""
    if pd.isna(r.iloc[i]):
        return "WARMUP", "NO_TRADE", "NO_TRADE"
    rsi_v = float(r.iloc[i])
    chop = chop_near_smma(c, s100, atr, i)
    sl = stack_long(c, e21, e50, s100, i)
    ss = stack_short(c, e21, e50, s100, i)
    ls = regime_streak(long_flags, i, regime_bars)
    ss_streak = regime_streak(short_flags, i, regime_bars)

    if chop and not sl and not ss:
        return "CHOP_SMMA", "NO_TRADE", "NO_TRADE"

    if sl and not ss:
        cross_up = ema21_cross_close_long(c, e21, i) and not ema21_whipsaw_chop(c, e21, i)
        if cross_up and ema21_cross_bias_long(c, e21, e50, s100, i) and 45 < rsi_v < 78:
            return "STACK_LONG", "GOOD", "NO_TRADE"
        if ls:
            if rsi_v >= 80 or rsi_v <= 45:
                return "STACK_LONG", "WAIT", "NO_TRADE"
            return "STACK_LONG", "GOOD", "NO_TRADE"
        return "TRANSITION_BULL", "WAIT", "NO_TRADE"

    if ss and not sl:
        cross_dn = ema21_cross_close_short(c, e21, i) and not ema21_whipsaw_chop(c, e21, i)
        if cross_dn and ema21_cross_bias_short(c, e21, e50, s100, i) and 22 < rsi_v < 55:
            return "STACK_SHORT", "NO_TRADE", "GOOD"
        if ss_streak:
            if rsi_v <= 20 or rsi_v >= 55:
                return "STACK_SHORT", "NO_TRADE", "WAIT"
            return "STACK_SHORT", "NO_TRADE", "GOOD"
        return "TRANSITION_BEAR", "NO_TRADE", "WAIT"

    if chop:
        return "CHOP_MIXED", "NO_TRADE", "NO_TRADE"
    return "FLAT_NEUTRAL", "NO_TRADE", "NO_TRADE"


# --- EMA distance / fan filters (chart discretion) ---
ENTRY_MAX_CLOSE_EMA21_ATR = 0.40
ENTRY_MAX_TOUCH_EMA21_ATR = 0.55
MIN_FAN_SPREAD_ATR = 0.12
MAX_FAN_SPREAD_ATR = 2.50
MIN_EMA21_SLOPE_ATR = 0.02
MAX_CHASE_FROM_SMMA_ATR = 2.80
MIN_ATR_VS_MEDIAN = 0.50
EXIT_EXT_EMA21_ATR = 1.15
EXIT_MIN_BARS = 4
EXIT_MIN_PROFIT_ATR = 0.35


def dist_atr(price: float, level: float, atr: float) -> float:
    if atr <= 0:
        return 999.0
    return abs(price - level) / atr


def ema_fan_spreads(e21, e50, s100, atr, i: int, side: int) -> tuple[float, float]:
    if any(pd.isna(x.iloc[i]) for x in (e21, e50, s100, atr)) or atr.iloc[i] <= 0:
        return 0.0, 0.0
    a = float(atr.iloc[i])
    if side > 0:
        s1 = (e21.iloc[i] - e50.iloc[i]) / a
        s2 = (e50.iloc[i] - s100.iloc[i]) / a
    else:
        s1 = (e50.iloc[i] - e21.iloc[i]) / a
        s2 = (s100.iloc[i] - e50.iloc[i]) / a
    return float(s1), float(s2)


def ema_fan_ok(e21, e50, s100, atr, i: int, side: int) -> bool:
    s1, s2 = ema_fan_spreads(e21, e50, s100, atr, i, side)
    if s1 <= 0 or s2 <= 0:
        return False
    mn = min(s1, s2)
    if mn < MIN_FAN_SPREAD_ATR:
        return False
    if s1 + s2 > MAX_FAN_SPREAD_ATR * 2:
        return False
    return True


def ema21_slope_ok(e21, atr, i: int, side: int) -> bool:
    if i < 3 or pd.isna(e21.iloc[i]) or pd.isna(atr.iloc[i]) or atr.iloc[i] <= 0:
        return False
    slope = (e21.iloc[i] - e21.iloc[i - 3]) / atr.iloc[i]
    return slope >= MIN_EMA21_SLOPE_ATR if side > 0 else slope <= -MIN_EMA21_SLOPE_ATR


def entry_near_ema21(c, h, l, e21, atr, i: int, side: int) -> bool:
    if pd.isna(e21.iloc[i]) or pd.isna(atr.iloc[i]) or atr.iloc[i] <= 0:
        return False
    a = float(atr.iloc[i])
    close_d = dist_atr(float(c.iloc[i]), float(e21.iloc[i]), a)
    if side > 0:
        touch_d = dist_atr(float(l.iloc[i]), float(e21.iloc[i]), a)
        return close_d <= ENTRY_MAX_CLOSE_EMA21_ATR or touch_d <= ENTRY_MAX_TOUCH_EMA21_ATR
    touch_d = dist_atr(float(h.iloc[i]), float(e21.iloc[i]), a)
    return close_d <= ENTRY_MAX_CLOSE_EMA21_ATR or touch_d <= ENTRY_MAX_TOUCH_EMA21_ATR


def chase_from_slow_ok(c, s100, atr, i: int, side: int) -> bool:
    if pd.isna(s100.iloc[i]) or pd.isna(atr.iloc[i]) or atr.iloc[i] <= 0:
        return False
    d = (c.iloc[i] - s100.iloc[i]) / atr.iloc[i]
    if side > 0:
        return 0 <= d <= MAX_CHASE_FROM_SMMA_ATR
    return 0 <= -d <= MAX_CHASE_FROM_SMMA_ATR


def atr_vol_ok(atr, i: int) -> bool:
    if i < 120 or pd.isna(atr.iloc[i]):
        return True
    med = atr.iloc[i - 100 : i].median()
    if pd.isna(med) or med <= 0:
        return True
    return float(atr.iloc[i]) >= MIN_ATR_VS_MEDIAN * float(med)


def passes_entry_filters(
    c,
    h,
    l,
    e21,
    e50,
    s100,
    atr,
    i: int,
    side: int,
) -> bool:
    if not ema_fan_ok(e21, e50, s100, atr, i, side):
        return False
    if not entry_near_ema21(c, h, l, e21, atr, i, side):
        return False
    if not ema21_slope_ok(e21, atr, i, side):
        return False
    if not chase_from_slow_ok(c, s100, atr, i, side):
        return False
    if not atr_vol_ok(atr, i):
        return False
    if chop_near_smma(c, s100, atr, i) and min(*ema_fan_spreads(e21, e50, s100, atr, i, side)) < MIN_FAN_SPREAD_ATR * 1.5:
        return False
    return True


def ema21_flip_count(c, e21, i: int, lookback: int = 4) -> int:
    flips = 0
    for j in range(i - lookback + 1, i + 1):
        if j < 1:
            continue
        if any(pd.isna(e21.iloc[k]) for k in (j, j - 1)):
            continue
        if (c.iloc[j] > e21.iloc[j]) != (c.iloc[j - 1] > e21.iloc[j - 1]):
            flips += 1
    return flips


def market_regime_at(
    i: int,
    c,
    e21,
    e50,
    s100,
    atr,
) -> str:
    """UPTREND | DOWNTREND | SIDEWAY — what you called up-trade, down-trend, wide-way (range).

    SIDEWAY: chop on SMMA100, compressed EMA fan, or EMA21 whipsaw (many crosses).
    UPTREND: EMA21 above EMA50, price above EMA21, fan open, not whipsaw.
    DOWNTREND: mirror.
    """
    if any(pd.isna(x.iloc[i]) for x in (e21, e50, s100, atr, c)):
        return "SIDEWAY"
    if ema21_flip_count(c, e21, i) >= 2:
        return "SIDEWAY"
    if chop_near_smma(c, s100, atr, i) and not stack_long(c, e21, e50, s100, i) and not stack_short(
        c, e21, e50, s100, i
    ):
        return "SIDEWAY"
    s1, s2 = ema_fan_spreads(e21, e50, s100, atr, i, 1)
    s1b, s2b = ema_fan_spreads(e21, e50, s100, atr, i, -1)
    fan_long = min(s1, s2) if s1 > 0 and s2 > 0 else 0.0
    fan_short = min(s1b, s2b) if s1b > 0 and s2b > 0 else 0.0
    if max(fan_long, fan_short) < MIN_FAN_SPREAD_ATR * 0.75:
        return "SIDEWAY"
    a = float(atr.iloc[i])
    slope = (e21.iloc[i] - e21.iloc[i - 2]) / a if i >= 2 and a > 0 else 0.0
    if e21.iloc[i] > e50.iloc[i] and c.iloc[i] > e21.iloc[i] and slope > 0.01:
        return "UPTREND"
    if e21.iloc[i] < e50.iloc[i] and c.iloc[i] < e21.iloc[i] and slope < -0.01:
        return "DOWNTREND"
    return "SIDEWAY"


def market_context_at(
    i: int,
    c,
    e21,
    e50,
    s100,
    atr,
    r,
    side: int,
) -> tuple[str, str]:
    """Regime + tradeability: stay away vs press the trend.

    Returns (regime, quality): quality in CHOPPY, SIDEWAY, BAD_TREND, GOOD_TREND.
    GOOD_TREND = ride / add; BAD_TREND = one careful entry; CHOPPY/SIDEWAY = flat.
    """
    if ema21_flip_count(c, e21, i) >= 3:
        return "SIDEWAY", "CHOPPY"
    reg = market_regime_at(i, c, e21, e50, s100, atr)
    if reg == "SIDEWAY":
        return reg, "CHOPPY" if ema21_flip_count(c, e21, i) >= 2 else "SIDEWAY"
    if side > 0 and reg != "UPTREND":
        return reg, "CHOPPY"
    if side < 0 and reg != "DOWNTREND":
        return reg, "CHOPPY"
    rv = float(r.iloc[i]) if pd.notna(r.iloc[i]) else 50.0
    stacked = stack_long(c, e21, e50, s100, i) if side > 0 else stack_short(c, e21, e50, s100, i)
    s1, s2 = ema_fan_spreads(e21, e50, s100, atr, i, side)
    fan_ok = s1 > MIN_FAN_SPREAD_ATR and s2 > MIN_FAN_SPREAD_ATR
    rsi_ok = (50 < rv < 78) if side > 0 else (22 < rv < 55)
    chop = chop_near_smma(c, s100, atr, i) and not stacked
    if stacked and fan_ok and rsi_ok and not chop:
        return reg, "GOOD_TREND"
    if fan_ok and rsi_ok and not chop:
        return reg, "GOOD_TREND"
    return reg, "BAD_TREND"


def macro_trend_dir(c, e21, e50, s100, i: int) -> int:
    """+1 bullish / -1 bearish / 0 none (EMA fan + price vs EMA50)."""
    if stack_long(c, e21, e50, s100, i):
        return 1
    if stack_short(c, e21, e50, s100, i):
        return -1
    if any(pd.isna(x.iloc[i]) for x in (e21, e50, c)):
        return 0
    if e21.iloc[i] > e50.iloc[i] and c.iloc[i] > e50.iloc[i]:
        return 1
    if e21.iloc[i] < e50.iloc[i] and c.iloc[i] < e50.iloc[i]:
        return -1
    return 0


def trend_phase_at(
    i: int,
    o,
    h,
    l,
    c,
    e21,
    e50,
    s100,
    atr,
    trigger_open: float | None = None,
) -> tuple[int, str]:
    """Classify bar into trend / pullback / retracement / correction (user chart language).

    Returns (macro_dir, phase). macro_dir: +1 up, -1 down, 0 chop.
    Phases (per macro_dir): TREND, PULLBACK, RETRACEMENT, CORRECTION, CHOP.
    """
    d = macro_trend_dir(c, e21, e50, s100, i)
    if d == 0:
        return 0, "CHOP"
    if chop_near_smma(c, s100, atr, i) and not stack_long(c, e21, e50, s100, i) and not stack_short(
        c, e21, e50, s100, i
    ):
        return 0, "CHOP"

    if d > 0:
        if trigger_open is not None and bearish_bar(o, c, i) and c.iloc[i] < trigger_open:
            return 1, "CORRECTION"
        if not pd.isna(e50.iloc[i]) and c.iloc[i] < e50.iloc[i]:
            return 1, "CORRECTION"
        if c.iloc[i] < e21.iloc[i]:
            return 1, "RETRACEMENT"
        if pd.notna(atr.iloc[i]) and atr.iloc[i] > 0 and l.iloc[i] <= e21.iloc[i] + atr.iloc[i] * 0.35:
            return 1, "PULLBACK"
        return 1, "TREND"

    if trigger_open is not None and bullish_bar(o, c, i) and c.iloc[i] > trigger_open:
        return -1, "CORRECTION"
    if not pd.isna(e50.iloc[i]) and c.iloc[i] > e50.iloc[i]:
        return -1, "CORRECTION"
    if c.iloc[i] > e21.iloc[i]:
        return -1, "RETRACEMENT"
    if pd.notna(atr.iloc[i]) and atr.iloc[i] > 0 and h.iloc[i] >= e21.iloc[i] - atr.iloc[i] * 0.35:
        return -1, "PULLBACK"
    return -1, "TREND"


def allow_trend_entry(i: int, o, h, l, c, e21, e50, s100, atr, r, side: int) -> bool:
    """Initial reclaim only when leaving chop into directional structure."""
    reg, qual = market_context_at(i, c, e21, e50, s100, atr, r, side)
    if qual in ("CHOPPY", "SIDEWAY"):
        return False
    if qual == "BAD_TREND" and not ema21_cross_close_long(c, e21, i) and side > 0:
        if not trigger_reclaim_long(o, h, l, c, e21, i):
            return False
    elif qual == "BAD_TREND" and side < 0:
        if not ema21_cross_close_short(c, e21, i) and not trigger_reclaim_short(o, h, l, c, e21, i):
            return False
    d, phase = trend_phase_at(i, o, h, l, c, e21, e50, s100, atr)
    if phase == "CHOP":
        return False
    if side > 0:
        fan_ok = not pd.isna(e50.iloc[i]) and e21.iloc[i] > e50.iloc[i]
        if not fan_ok and d <= 0:
            return False
        return trigger_reclaim_long(o, h, l, c, e21, i)
    fan_ok = not pd.isna(e50.iloc[i]) and e21.iloc[i] < e50.iloc[i]
    if not fan_ok and d >= 0:
        return False
    return trigger_reclaim_short(o, h, l, c, e21, i)


def allow_pullback_add(i: int, o, h, l, c, e21, e50, s100, atr, r, side: int) -> bool:
    """Pyramid on EMA21 retest only in GOOD_TREND pullback (don't over-trade weak tape)."""
    _, qual = market_context_at(i, c, e21, e50, s100, atr, r, side)
    if qual != "GOOD_TREND":
        return False
    d, phase = trend_phase_at(i, o, h, l, c, e21, e50, s100, atr)
    if side > 0:
        if d < 1 or phase in ("CORRECTION", "CHOP", "RETRACEMENT"):
            return False
        return phase == "PULLBACK" and retest_ema_entry_long(l, c, e21, atr, i)
    if d > -1 or phase in ("CORRECTION", "CHOP", "RETRACEMENT"):
        return False
    return phase == "PULLBACK" and retest_ema_entry_short(h, c, e21, atr, i)


def correction_exit(i: int, o, h, l, c, e21, e50, s100, atr, side: int, trigger_open: float) -> bool:
    d, phase = trend_phase_at(i, o, h, l, c, e21, e50, s100, atr, trigger_open=trigger_open)
    if side > 0:
        return d < 1 or phase == "CORRECTION"
    return d > -1 or phase == "CORRECTION"


def consecutive_closes_side(c, e21, i: int, side: int) -> int:
    """Count consecutive prior closes on one side of EMA21 (bar i-1, i-2, ...)."""
    if i < 1 or pd.isna(e21.iloc[i - 1]):
        return 0
    n = 0
    for k in range(1, i + 1):
        j = i - k
        if pd.isna(e21.iloc[j]):
            break
        above = c.iloc[j] > e21.iloc[j]
        if side > 0 and not above:
            n += 1
        elif side < 0 and above:
            n += 1
        else:
            break
    return n


def bullish_bar(o, c, i: int) -> bool:
    return bool(c.iloc[i] > o.iloc[i])


def bearish_bar(o, c, i: int) -> bool:
    return bool(c.iloc[i] < o.iloc[i])


def trigger_reclaim_long(
    o, h, l, c, e21, i: int, min_bars_below: int = 2
) -> bool:
    """After closes below EMA21, first green that closes back above (reclaim)."""
    if pd.isna(e21.iloc[i]):
        return False
    if consecutive_closes_side(c, e21, i, side=1) < min_bars_below:
        return False
    if not bullish_bar(o, c, i):
        return False
    return bool(c.iloc[i] > e21.iloc[i])


def trigger_reclaim_short(
    o, h, l, c, e21, i: int, min_bars_above: int = 2
) -> bool:
    if pd.isna(e21.iloc[i]):
        return False
    if consecutive_closes_side(c, e21, i, side=-1) < min_bars_above:
        return False
    if not bearish_bar(o, c, i):
        return False
    return bool(c.iloc[i] < e21.iloc[i])


def exit_red_below_trigger_open(o, c, i: int, trigger_open: float, side: int) -> bool:
    """User exit: bearish close breaks below the *open* of the green trigger (long)."""
    if side > 0:
        return bool(bearish_bar(o, c, i) and c.iloc[i] < trigger_open)
    return bool(bullish_bar(o, c, i) and c.iloc[i] > trigger_open)


def retest_ema_entry_long(l, c, e21, atr, i: int) -> bool:
    if pd.isna(e21.iloc[i]) or pd.isna(atr.iloc[i]):
        return False
    if c.iloc[i] <= e21.iloc[i]:
        return False
    touch = l.iloc[i] <= e21.iloc[i] + atr.iloc[i] * 0.3
    return bool(touch and c.iloc[i] > e21.iloc[i])


def retest_ema_entry_short(h, c, e21, atr, i: int) -> bool:
    if pd.isna(e21.iloc[i]) or pd.isna(atr.iloc[i]):
        return False
    if c.iloc[i] >= e21.iloc[i]:
        return False
    touch = h.iloc[i] >= e21.iloc[i] - atr.iloc[i] * 0.3
    return bool(touch and c.iloc[i] < e21.iloc[i])


def structure_bulk_exit_long(
    o, h, l, c, e21, atr, i: int, lookback: int = 8, far_atr: float = 1.0
) -> bool:
    """Red(s) far from EMA, then 1–2 greens engulf recent red bodies (close all longs)."""
    if i < lookback + 1:
        return False
    reds: list[tuple[float, float]] = []
    for j in range(i - lookback, i):
        if pd.isna(atr.iloc[j]) or atr.iloc[j] <= 0:
            continue
        if not bearish_bar(o, c, j):
            continue
        if dist_atr(c.iloc[j], e21.iloc[j], atr.iloc[j]) >= far_atr:
            reds.append((min(o.iloc[j], c.iloc[j]), max(o.iloc[j], c.iloc[j])))
    if not reds:
        return False
    if not bullish_bar(o, c, i):
        return False
    g_lo, g_hi = min(o.iloc[i], c.iloc[i]), max(o.iloc[i], c.iloc[i])
    recent = reds[-3:]
    engulfed = sum(1 for lo, hi in recent if g_lo <= lo and g_hi >= hi)
    return engulfed >= 1


def structure_bulk_exit_short(
    o, h, l, c, e21, atr, i: int, lookback: int = 8, far_atr: float = 1.0
) -> bool:
    if i < lookback + 1:
        return False
    greens: list[tuple[float, float]] = []
    for j in range(i - lookback, i):
        if pd.isna(atr.iloc[j]) or atr.iloc[j] <= 0:
            continue
        if not bullish_bar(o, c, j):
            continue
        if dist_atr(c.iloc[j], e21.iloc[j], atr.iloc[j]) >= far_atr:
            greens.append((min(o.iloc[j], c.iloc[j]), max(o.iloc[j], c.iloc[j])))
    if not greens:
        return False
    if not bearish_bar(o, c, i):
        return False
    b_lo, b_hi = min(o.iloc[i], c.iloc[i]), max(o.iloc[i], c.iloc[i])
    recent = greens[-3:]
    engulfed = sum(1 for lo, hi in recent if b_lo <= lo and b_hi >= hi)
    return engulfed >= 1


def forward_bars_after_cross(
    c, e21, i: int, horizon: int, side: int
) -> float | None:
    """Return close-to-close return over `horizon` bars after bar i (for research stats)."""
    if i + horizon >= len(c) or pd.isna(e21.iloc[i]):
        return None
    c0 = float(c.iloc[i])
    c1 = float(c.iloc[i + horizon])
    if c0 <= 0:
        return None
    ret = (c1 - c0) / c0
    return ret if side > 0 else -ret


def extension_exit_trigger(
    side: int,
    bars_in_trade: int,
    entry_px: float,
    c_i: float,
    e21_i: float,
    atr_i: float,
) -> bool:
    """Close when price stretches from EMA21 but not too early (min bars + min profit)."""
    if atr_i <= 0 or bars_in_trade < EXIT_MIN_BARS:
        return False
    profit_atr = ((c_i - entry_px) if side > 0 else (entry_px - c_i)) / atr_i
    if profit_atr < EXIT_MIN_PROFIT_ATR:
        return False
    ext = dist_atr(c_i, e21_i, atr_i)
    if ext < EXIT_EXT_EMA21_ATR:
        return False
    if side > 0 and c_i < e21_i:
        return False
    if side < 0 and c_i > e21_i:
        return False
    return True


RSI_DIV_LOOKBACK = 48
RSI_DIV_MIN_DELTA = 2.0


def _confirmed_pivot_lows(l: pd.Series, i: int, lookback: int) -> list[tuple[int, float, float]]:
    """Pivot lows at bar j (confirmed at i using left-only fractal: low[j] vs j±1)."""
    pivots: list[tuple[int, float, float]] = []
    start = max(2, i - lookback)
    for j in range(start, i):
        if l.iloc[j] <= l.iloc[j - 1] and l.iloc[j] <= l.iloc[j + 1]:
            pivots.append((j, float(l.iloc[j]), j))
    return pivots


def _confirmed_pivot_highs(h: pd.Series, i: int, lookback: int) -> list[tuple[int, float, float]]:
    pivots: list[tuple[int, float, float]] = []
    start = max(2, i - lookback)
    for j in range(start, i):
        if h.iloc[j] >= h.iloc[j - 1] and h.iloc[j] >= h.iloc[j + 1]:
            pivots.append((j, float(h.iloc[j]), j))
    return pivots


def rsi_bullish_divergence(
    l: pd.Series,
    r: pd.Series,
    i: int,
    *,
    lookback: int = RSI_DIV_LOOKBACK,
    min_rsi_delta: float = RSI_DIV_MIN_DELTA,
) -> bool:
    """Regular bullish div: price lower low, RSI(9) higher low on last two pivots."""
    if i < lookback + 3:
        return False
    pivots = []
    start = max(2, i - lookback)
    for j in range(start, i):
        if l.iloc[j] <= l.iloc[j - 1] and l.iloc[j] <= l.iloc[j + 1]:
            if pd.notna(r.iloc[j]):
                pivots.append((j, float(l.iloc[j]), float(r.iloc[j])))
    if len(pivots) < 2:
        return False
    _, p1, r1 = pivots[-2]
    _, p2, r2 = pivots[-1]
    return p2 < p1 and (r2 - r1) >= min_rsi_delta


def rsi_bearish_divergence(
    h: pd.Series,
    r: pd.Series,
    i: int,
    *,
    lookback: int = RSI_DIV_LOOKBACK,
    min_rsi_delta: float = RSI_DIV_MIN_DELTA,
) -> bool:
    """Regular bearish div: price higher high, RSI lower high on last two pivots."""
    if i < lookback + 3:
        return False
    pivots = []
    start = max(2, i - lookback)
    for j in range(start, i):
        if h.iloc[j] >= h.iloc[j - 1] and h.iloc[j] >= h.iloc[j + 1]:
            if pd.notna(r.iloc[j]):
                pivots.append((j, float(h.iloc[j]), float(r.iloc[j])))
    if len(pivots) < 2:
        return False
    _, p1, r1 = pivots[-2]
    _, p2, r2 = pivots[-1]
    return p2 > p1 and (r1 - r2) >= min_rsi_delta
