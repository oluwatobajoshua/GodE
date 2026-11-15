#!/usr/bin/env python3
"""FXMOPOL chart playbook — EMA21/50 + SMMA100, RSI 20/50/80, regime + pullback/hunt.

Rules from docs/FXMOPOL_CHART_SNAPSHOT.md:
  - Long regime: close > EMA21 > EMA50 and close > SMMA100
  - Short regime: close < EMA21 < EMA50 and close < SMMA100
  - Long entry: regime + (pullback to EMA21 or stop-hunt reclaim) + RSI in (50, 80)
  - Short entry: regime + EMA21 retest from below + RSI in (20, 50)
  - Long exit: close < EMA21 or RSI >= 80
  - Short exit: close > EMA21 or RSI <= 20

Run:
    .\\.venv\\Scripts\\python.exe research\\fxmopol_playbook.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from fxmopol_signal_layers import fund_sentiment_allows, prep_macro_series, prep_sr_columns, sr_long_ok, sr_short_ok

from fxmopol_chart_core import (
    DEFAULT_WEEKDAY_MASK,
    chart_state_at,
    ema21_cross_bias_long,
    ema21_cross_bias_short,
    ema21_cross_close_long,
    ema21_cross_close_short,
    ema21_one_bar_fakeout,
    ema21_whipsaw_chop,
    extension_exit_trigger,
    passes_entry_filters,
    playbook_chart_entry,
    pullback_long,
    regime_streak,
    retest_short,
    rsi,
    rsi_bearish_divergence,
    rsi_bullish_divergence,
    smma,
    stack_long,
    stack_short,
    stop_hunt_reclaim,
    stop_hunt_reclaim_stack,
    session_allows_new_entry,
    weekday_allows_new_entry,
    DEFAULT_WEEKDAY_MASK,
)

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT_SUM = HERE / "results" / "fxmopol_playbook_summary.csv"
OUT_MANIFEST = HERE / "results" / "fxmopol_playbook_manifest.json"
SYMS = ("EURUSD", "USDJPY", "GBPUSD")
SPLITS = {
    "design": ("2018-01-01", "2022-01-01"),
    "validation": ("2022-01-01", "2024-07-01"),
    "OOS": ("2024-07-01", "2030-01-01"),
}
COST_BPS = 3.0
MAX_DAY = 2  # GBP playbook default; conviction = timing + PA stack, not a 1-trade cap
RSI_PERIOD = 9
REGIME_BARS = 5  # min consecutive stack bars (closer to chart "many candles" rule)
RSI_COVER_LEVEL = 30.0  # short cover zone per FXMOPOL_CHART_SNAPSHOT
MANUAL_LEVELS = {"USDJPY": 158.5, "EURUSD": 0.0, "GBPUSD": 0.0}
USE_FRACTAL_CONFLUENCE = False
USE_PROXIMITY_FILTERS = True
STRICT_STAY_AWAY = True
TREND_RIDE_EXIT = True  # two consecutive closes vs EMA21 before flatten (ride the move)
USE_RSI_DIVERGENCE = False  # set by symbol trainer
RSI_DIV_EXIT = True  # exit long on bear div / short on bull div
USE_SR_FILTER = False  # require S/R confluence (BabyPips-style)
USE_FUND_SENTIMENT = True  # FRED carry + VIX — aligned with EA + professor fund pillar
USE_D1_GATE = True  # matches EA InpRequireD1Align on GBP playbook
USE_CHART_PLAYBOOK_ONLY = True  # matches EA InpUseFullPriceAction — regime pullbacks/hunts, not legacy cross chase
# Timing: start open in research; run fxmopol_filter_learn.py then promote to EA inputs.
GOOD_SESSIONS_ONLY = False
WEEKDAY_MASK = 0
TIMING_LITERATURE_DEFAULTS = False  # if True: GOOD_SESSIONS + DEFAULT_WEEKDAY_MASK
D1_SMA_PERIOD = 200


def build_d1_gate(h1: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Prior completed D1 bar stack + SMA200, mapped to each H1 timestamp (no lookahead)."""
    dc = h1["close"].resample("1D").last().dropna()
    e21 = dc.ewm(span=21, adjust=False).mean()
    e50 = dc.ewm(span=50, adjust=False).mean()
    s100 = smma(dc, 100)
    sma200 = dc.rolling(D1_SMA_PERIOD).mean()
    long_ok = (dc > e21) & (e21 > e50) & (dc > s100) & (dc > sma200)
    short_ok = (dc < e21) & (e21 < e50) & (dc < s100) & (dc < sma200)
    long_ok = long_ok.shift(1)
    short_ok = short_ok.shift(1)
    day_s = pd.Series(h1.index.normalize(), index=h1.index)
    long_h1 = day_s.map(long_ok).fillna(False).astype(bool)
    short_h1 = day_s.map(short_ok).fillna(False).astype(bool)
    return long_h1, short_h1


def load_h1(sym: str) -> pd.DataFrame:
    from bar_store import load_h1 as _canonical_load_h1  # noqa: PLC0415

    return _canonical_load_h1(sym)


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


def replay_trades(
    h1: pd.DataFrame,
    ema_mid: int = 50,
    good_sessions_only: bool | None = None,
    symbol: str = "",
    use_d1_gate: bool | None = None,
    weekday_mask: int | None = None,
    allowed_hours: list[int] | None = None,
    allowed_entry_reasons: list[str] | None = None,
) -> pd.DataFrame:
    """Bar-by-bar playbook with entry/exit map for history replay."""
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
    day = h1.index.normalize()
    pip = 0.01 if "JPY" in symbol else 0.0001

    long_flags = [stack_long(c, e21, e50, s100, i) for i in range(len(h1))]
    short_flags = [stack_short(c, e21, e50, s100, i) for i in range(len(h1))]
    d1_gate = use_d1_gate if use_d1_gate is not None else USE_D1_GATE
    d1_long, d1_short = build_d1_gate(h1) if d1_gate else (None, None)
    sr = prep_sr_columns(h1)
    carry_s, vix_chg_s = prep_macro_series(h1.index, symbol) if symbol else (None, None)

    rows = []
    pos = 0
    entry_px = 0.0
    entry_atr = 0.0
    entry_t = None
    signal_t = None
    entry_reason = ""
    entry_bar_i = 0
    entry_chart_state = ""
    entry_grade = ""
    counts: dict = {}

    def close_trade(exit_i: int, exit_reason: str) -> None:
        nonlocal pos, entry_px, entry_t, signal_t, entry_reason, entry_chart_state, entry_grade, entry_bar_i
        if pos == 0:
            return
        exit_px = c.iloc[exit_i]
        exit_t = h1.index[exit_i]
        if pos > 0:
            gross = (exit_px - entry_px) / entry_px - COST_BPS / 10000.0
            pips = (exit_px - entry_px) / 0.0001
        else:
            gross = (entry_px - exit_px) / entry_px - COST_BPS / 10000.0
            pips = (entry_px - exit_px) / 0.0001
        rows.append(
            {
                "signal_time": signal_t,
                "entry_time": entry_t,
                "exit_time": exit_t,
                "side": "LONG" if pos > 0 else "SHORT",
                "entry_reason": entry_reason,
                "exit_reason": exit_reason,
                "entry_price": round(float(entry_px), 5),
                "exit_price": round(float(exit_px), 5),
                "pips": round(float(pips), 1),
                "return_pct": round(gross * 100, 4),
                "rsi_at_signal": round(float(r.iloc[exit_i - 1]), 2) if exit_i > 0 else None,
                "entry_bar_i": entry_bar_i,
                "chart_state": entry_chart_state,
                "entry_grade": entry_grade,
            }
        )
        pos = 0

    for i in range(110, len(h1) - 1):
        sess_on = GOOD_SESSIONS_ONLY if good_sessions_only is None else good_sessions_only
        if TIMING_LITERATURE_DEFAULTS and good_sessions_only is None:
            sess_on = True
        wd_mask = WEEKDAY_MASK if weekday_mask is None else weekday_mask
        if TIMING_LITERATURE_DEFAULTS and weekday_mask is None:
            wd_mask = DEFAULT_WEEKDAY_MASK
        hr = int(h1.index[i].hour)
        if pos == 0:
            if allowed_hours is not None:
                if hr not in allowed_hours:
                    continue
            elif sess_on and not session_allows_new_entry(hr, enabled=True):
                continue
            if wd_mask and not weekday_allows_new_entry(h1.index[i].dayofweek, wd_mask):
                continue
        dk = day[i]
        counts.setdefault(dk, 0)

        if pos == 1:
            ema_break = (
                c.iloc[i] < e21.iloc[i]
                and (not TREND_RIDE_EXIT or (i > 0 and c.iloc[i - 1] < e21.iloc[i - 1]))
            )
            if ema_break:
                close_trade(i, "exit_close_below_ema21")
                continue
            if pd.notna(r.iloc[i]) and r.iloc[i] >= 80:
                close_trade(i, "exit_rsi80")
                continue
            if i > 0 and pd.notna(r.iloc[i]) and pd.notna(r.iloc[i - 1]) and r.iloc[i - 1] >= 80 and r.iloc[i] < 75:
                close_trade(i, "exit_rsi80_rollover")
                continue
            ml = MANUAL_LEVELS.get(symbol, 0.0) if symbol else 0.0
            if ml > 0 and h.iloc[i] >= ml - (atr.iloc[i] * 0.15 if pd.notna(atr.iloc[i]) else 0):
                close_trade(i, "exit_manual_level")
                continue
            if pd.notna(atr.iloc[i]) and extension_exit_trigger(
                1, i - entry_bar_i, entry_px, float(c.iloc[i]), float(e21.iloc[i]), float(atr.iloc[i])
            ):
                close_trade(i, "exit_ema21_extension")
                continue
            if USE_RSI_DIVERGENCE and RSI_DIV_EXIT and rsi_bearish_divergence(h, r, i):
                close_trade(i, "exit_rsi_bear_div")
                continue
        elif pos == -1:
            ema_break = (
                c.iloc[i] > e21.iloc[i]
                and (not TREND_RIDE_EXIT or (i > 0 and c.iloc[i - 1] > e21.iloc[i - 1]))
            )
            if ema_break:
                close_trade(i, "exit_close_above_ema21")
                continue
            if pd.notna(r.iloc[i]) and r.iloc[i] <= RSI_COVER_LEVEL:
                close_trade(i, "exit_rsi_cover")
                continue
            ml = MANUAL_LEVELS.get(symbol, 0.0) if symbol else 0.0
            if ml > 0 and l.iloc[i] <= ml + (atr.iloc[i] * 0.15 if pd.notna(atr.iloc[i]) else 0):
                close_trade(i, "exit_manual_level")
                continue
            if pd.notna(atr.iloc[i]) and extension_exit_trigger(
                -1, i - entry_bar_i, entry_px, float(c.iloc[i]), float(e21.iloc[i]), float(atr.iloc[i])
            ):
                close_trade(i, "exit_ema21_extension")
                continue
            if USE_RSI_DIVERGENCE and RSI_DIV_EXIT and rsi_bullish_divergence(l, r, i):
                close_trade(i, "exit_rsi_bull_div")
                continue

        if pos != 0 or counts[dk] >= MAX_DAY:
            continue

        side = 0
        reason = ""
        rv = r.iloc[i]

        if USE_CHART_PLAYBOOK_ONLY and pd.notna(rv):
            if 50 < rv < 80 or 20 < rv < 50:
                side, reason = playbook_chart_entry(
                    i,
                    c,
                    e21,
                    e50,
                    s100,
                    o,
                    h,
                    l,
                    atr,
                    long_flags,
                    short_flags,
                    REGIME_BARS,
                    USE_FRACTAL_CONFLUENCE,
                )
            if side == 0:
                continue

        # Legacy early EMA21 cross (off when USE_CHART_PLAYBOOK_ONLY)
        if not USE_CHART_PLAYBOOK_ONLY and pd.notna(rv) and ema21_cross_close_long(c, e21, i):
            if (
                not ema21_whipsaw_chop(c, e21, i)
                and not ema21_one_bar_fakeout(c, e21, i, 1)
                and ema21_cross_bias_long(c, e21, e50, s100, i)
                and 50 < rv < 80
            ):
                side, reason = 1, "entry_ema21_close_cross_long"
        if not USE_CHART_PLAYBOOK_ONLY and side == 0 and pd.notna(rv) and ema21_cross_close_short(c, e21, i):
            if (
                not ema21_whipsaw_chop(c, e21, i)
                and not ema21_one_bar_fakeout(c, e21, i, -1)
                and ema21_cross_bias_short(c, e21, e50, s100, i)
                and 20 < rv < 50
            ):
                side, reason = -1, "entry_ema21_close_cross_short"

        if not USE_CHART_PLAYBOOK_ONLY and side == 0 and regime_streak(long_flags, i, REGIME_BARS) and pd.notna(rv) and 50 < rv < 80:
            if pullback_long(l, c, e21, atr, i):
                side, reason = 1, "entry_pullback_ema21"
            elif stop_hunt_reclaim_stack(c, e21, s100, o, h, l, i, 1):
                side, reason = 1, "entry_stop_hunt_reclaim"
        if not USE_CHART_PLAYBOOK_ONLY and side == 0 and regime_streak(short_flags, i, REGIME_BARS) and pd.notna(rv) and 20 < rv < 50:
            if retest_short(h, c, e21, atr, i):
                side, reason = -1, "entry_retest_ema21"
            elif stop_hunt_reclaim_stack(c, e21, s100, o, h, l, i, -1):
                side, reason = -1, "entry_stop_hunt_reject"

        if side == 0 and USE_RSI_DIVERGENCE and pd.notna(rv):
            if (
                rsi_bullish_divergence(l, r, i)
                and ema21_cross_bias_long(c, e21, e50, s100, i)
                and 22 < rv < 58
                and c.iloc[i] > e21.iloc[i]
            ):
                side, reason = 1, "entry_rsi_bull_div"
            elif (
                rsi_bearish_divergence(h, r, i)
                and ema21_cross_bias_short(c, e21, e50, s100, i)
                and 42 < rv < 78
                and c.iloc[i] < e21.iloc[i]
            ):
                side, reason = -1, "entry_rsi_bear_div"

        if side == 0:
            continue

        if allowed_entry_reasons and reason not in allowed_entry_reasons:
            continue

        if d1_gate:
            if side > 0 and not bool(d1_long.iloc[i]):
                continue
            if side < 0 and not bool(d1_short.iloc[i]):
                continue

        if USE_SR_FILTER and not reason.startswith("entry_rsi_"):
            atr_i = float(atr.iloc[i]) if pd.notna(atr.iloc[i]) else 0.0
            if side > 0:
                if not sr_long_ok(
                    i,
                    float(l.iloc[i]),
                    float(c.iloc[i]),
                    atr_i,
                    float(sr["sh"].iloc[i]),
                    float(sr["sl"].iloc[i]),
                    float(sr["fib618"].iloc[i]),
                    float(sr["s1"].iloc[i]),
                ):
                    continue
            else:
                if not sr_short_ok(
                    i,
                    float(h.iloc[i]),
                    float(c.iloc[i]),
                    atr_i,
                    float(sr["sh"].iloc[i]),
                    float(sr["sl"].iloc[i]),
                    float(sr["fib50"].iloc[i]),
                    float(sr["r1"].iloc[i]),
                ):
                    continue

        if USE_FUND_SENTIMENT and carry_s is not None:
            t = h1.index[i]
            if not fund_sentiment_allows(
                side,
                float(carry_s.loc[t]) if t in carry_s.index else np.nan,
                float(vix_chg_s.loc[t]) if t in vix_chg_s.index else np.nan,
            ):
                continue

        cross_entry = reason.startswith("entry_ema21_close_cross") or reason.startswith("entry_rsi_")
        if USE_PROXIMITY_FILTERS and not cross_entry and not passes_entry_filters(
            c, h, l, e21, e50, s100, atr, i, side
        ):
            continue
        if STRICT_STAY_AWAY:
            state, gl, gs = chart_state_at(
                i, c, e21, e50, s100, r, atr, long_flags, short_flags
            )
            grade = gl if side > 0 else gs
            cross_entry = reason.startswith("entry_ema21_close_cross") or reason.startswith("entry_rsi_")
            if cross_entry:
                if state in ("CHOP_SMMA", "FLAT_NEUTRAL", "CHOP_MIXED") or grade == "NO_TRADE":
                    continue
            elif grade != "GOOD" or state in ("CHOP_SMMA", "FLAT_NEUTRAL"):
                continue

        signal_t = h1.index[i]
        entry_t = h1.index[i + 1]
        entry_px = c.iloc[i + 1]
        entry_atr = float(atr.iloc[i]) if pd.notna(atr.iloc[i]) else 0.0
        entry_bar_i = i + 1
        entry_reason = reason
        st_gate, gl, gs = chart_state_at(
            i, c, e21, e50, s100, r, atr, long_flags, short_flags
        )
        entry_chart_state = st_gate
        entry_grade = gl if side > 0 else gs
        pos = side
        counts[dk] += 1

    if pos == 1:
        close_trade(len(h1) - 1, "exit_end_of_data")
    elif pos == -1:
        close_trade(len(h1) - 1, "exit_end_of_data")

    return pd.DataFrame(rows)


def backtest(
    h1: pd.DataFrame,
    ema_mid: int = 50,
    use_d1_gate: bool | None = None,
    good_sessions_only: bool | None = None,
    symbol: str = "",
    weekday_mask: int | None = None,
    allowed_hours: list[int] | None = None,
    allowed_entry_reasons: list[str] | None = None,
) -> pd.Series:
    df = replay_trades(
        h1,
        ema_mid=ema_mid,
        use_d1_gate=use_d1_gate,
        good_sessions_only=good_sessions_only,
        symbol=symbol,
        weekday_mask=weekday_mask,
        allowed_hours=allowed_hours,
        allowed_entry_reasons=allowed_entry_reasons,
    )
    if df.empty:
        return pd.Series(dtype=float)
    return pd.Series(df["return_pct"].values / 100.0, index=pd.to_datetime(df["exit_time"]))


def metrics(r: pd.Series) -> dict:
    if len(r) < 10:
        return {"n": len(r), "exp_bps": -999, "pf": 0, "winrate": 0, "total_pct": 0}
    wins, losses = r[r > 0], r[r < 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else 0
    return {
        "n": len(r),
        "exp_bps": round(r.mean() * 10000, 2),
        "pf": round(pf, 2),
        "winrate": round(100 * (r > 0).mean(), 1),
        "total_pct": round(((1 + r).prod() - 1) * 100, 2),
    }


def compare_legacy(sym: str, trade_r: pd.Series) -> dict:
    try:
        from fxmopol_visual import backtest as legacy_bt, load_h1 as legacy_load

        leg = legacy_bt(legacy_load(sym))
        return {"legacy_exp_bps": round(leg.mean() * 10000, 2) if len(leg) else 0, "playbook_exp_bps": round(trade_r.mean() * 10000, 2) if len(trade_r) else 0}
    except Exception:
        return {}


def main() -> int:
    rows = []
    manifest_syms = {}
    for sym in SYMS:
        mid = 100 if sym == "USDJPY" else 50
        print(f"=== {sym} (EMA{mid}) ===", flush=True)
        h1 = load_h1(sym)
        prime = sym == "GBPUSD"
        trade_r = backtest(
            h1,
            ema_mid=mid,
            symbol=sym,
            good_sessions_only=prime,
            weekday_mask=DEFAULT_WEEKDAY_MASK if prime else 0,
        )
        name = f"playbook_{sym}_mid{mid}"
        from period_returns import print_split_monthly, publish_results

        publish_results(name, trade_r)
        print_split_monthly(name, trade_r, SPLITS)
        cmp = compare_legacy(sym, trade_r)
        manifest_syms[sym] = cmp
        for split, (a, b) in SPLITS.items():
            part = trade_r[(trade_r.index >= a) & (trade_r.index < b)]
            rows.append({"symbol": sym, "ema_mid": mid, "split": split, **metrics(part)})

    pd.DataFrame(rows).to_csv(OUT_SUM, index=False)

    oos = pd.DataFrame(rows)[pd.DataFrame(rows)["split"] == "OOS"]
    manifest = {
        "rule_set": "full PA + D1 + proximity; timing open (learn via filter_learn / performance buckets)",
        "good_sessions_only": GOOD_SESSIONS_ONLY,
        "timing_literature_defaults": TIMING_LITERATURE_DEFAULTS,
        "weekday_mask": WEEKDAY_MASK,
        "max_trades_per_day": MAX_DAY,
        "use_chart_playbook_only": USE_CHART_PLAYBOOK_ONLY,
        "vs_visual_legacy": manifest_syms,
        "summary_csv": str(OUT_SUM.name),
    }
    OUT_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("=== FXMOPOL playbook vs legacy visual (OOS exp bps/trade) ===")
    for sym, c in manifest_syms.items():
        print(sym, c)
    print(f"\nWrote {OUT_SUM}, {OUT_MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
