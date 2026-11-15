#!/usr/bin/env python3
"""EMA21 cross harvesting research.

Questions:
1. What makes a good cross — 1-candle vs multi-candle cross?
2. After the cross, how many candles follow in that direction?
3. When to exit — EMA21 break, RSI extreme, time stop?
4. When to re-enter — after pullback to EMA21, after new cross?

Run:
    .\.venv\Scripts\python.exe research\ema21_harvest.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "ema21_harvest"
OUT.mkdir(parents=True, exist_ok=True)

SYMS_TFS = [("EURUSD", "H1"), ("GBPUSD", "H1"), ("USDJPY", "H1"), ("BTCUSD", "H1")]


def load_csv(sym: str, tf: str) -> pd.DataFrame:
    p = DATA / f"{sym}_{tf}.csv"
    if not p.exists():
        p = DATA / "mt5" / f"{sym}_{tf}.csv"
    d = pd.read_csv(p, parse_dates=["time"])
    d["time"] = pd.to_datetime(d["time"], utc=True, errors="coerce").dt.tz_convert(None)
    return d.set_index("time").sort_index()


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 9) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).rolling(period).mean()
    down = (-delta.clip(upper=0)).rolling(period).mean().replace(0, np.nan)
    rs = up / down
    return 100 - 100 / (1 + rs)


def simulate_harvest(df: pd.DataFrame, params: dict) -> dict:
    """Harvest candles above/below EMA21.

    Entry: after cross, wait for `min_consec` consecutive closes on crossed side.
    Exit: close beyond EMA21 (break), or RSI extreme, or max bars.
    Re-entry: after new cross + min_consec confirmation.
    """
    c = df["close"].astype(float)
    o = df["open"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    e21 = ema(c, 21)
    r = rsi(c)
    n = len(df)

    min_consec_long = params["min_consec_long"]
    min_consec_short = params["min_consec_short"]
    exit_on_ema_break = params["exit_on_ema_break"]
    rsi_exit_long = params["rsi_exit_long"]
    rsi_exit_short = params["rsi_exit_short"]
    max_hold = params["max_hold"]
    trail_atr = params["trail_atr"]

    atr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = atr.rolling(14).mean()

    side = 0
    entry_i = -1
    entry_px = 0.0
    high_px = 0.0
    low_px = 0.0
    consec_long = 0
    consec_short = 0

    trades = []  # list of dicts

    for i in range(25, n - 1):
        if side == 0:
            # Count consecutive closes on each side
            if c.iloc[i] > e21.iloc[i]:
                consec_long += 1
                consec_short = 0
            elif c.iloc[i] < e21.iloc[i]:
                consec_short += 1
                consec_long = 0
            else:
                consec_long = 0
                consec_short = 0

            # Entry: long cross confirmed by min_consec_long closes above EMA21
            if consec_long >= min_consec_long and i >= 1 and c.iloc[i - 1] <= e21.iloc[i - 1]:
                side = 1
                entry_i = i
                entry_px = float(c.iloc[i + 1])
                high_px = entry_px
                low_px = entry_px
                consec_long = 0
                consec_short = 0
                continue

            # Entry: short cross confirmed by min_consec_short closes below EMA21
            if consec_short >= min_consec_short and i >= 1 and c.iloc[i - 1] >= e21.iloc[i - 1]:
                side = -1
                entry_i = i
                entry_px = float(c.iloc[i + 1])
                high_px = entry_px
                low_px = entry_px
                consec_long = 0
                consec_short = 0
                continue

        else:
            # In position — track extremes for trailing
            if side > 0:
                high_px = max(high_px, float(h.iloc[i]))
                low_px = min(low_px, float(l.iloc[i]))
            else:
                high_px = max(high_px, float(h.iloc[i]))
                low_px = min(low_px, float(l.iloc[i]))

            exit_now = False
            exit_reason = ""

            c_i = float(c.iloc[i])
            bars_held = i - entry_i

            # EMA21 break exit
            if exit_on_ema_break:
                if side > 0 and c_i < float(e21.iloc[i]):
                    exit_now = True
                    exit_reason = "ema_break"
                if side < 0 and c_i > float(e21.iloc[i]):
                    exit_now = True
                    exit_reason = "ema_break"

            # RSI extreme exit
            rv = float(r.iloc[i]) if pd.notna(r.iloc[i]) else 50.0
            if side > 0 and rv >= rsi_exit_long:
                exit_now = True
                exit_reason = "rsi_extreme"
            if side < 0 and rv <= rsi_exit_short:
                exit_now = True
                exit_reason = "rsi_extreme"

            # Time stop
            if bars_held >= max_hold:
                exit_now = True
                exit_reason = "time_stop"

            # ATR trailing stop
            if trail_atr > 0 and bars_held > 0:
                a = float(atr.iloc[entry_i]) if pd.notna(atr.iloc[entry_i]) else 0.001
                if side > 0:
                    trail_px = high_px - trail_atr * a
                    if c_i <= trail_px:
                        exit_now = True
                        exit_reason = "trail_stop"
                else:
                    trail_px = low_px + trail_atr * a
                    if c_i >= trail_px:
                        exit_now = True
                        exit_reason = "trail_stop"

            if exit_now:
                exit_px = c_i
                if side > 0:
                    gross = (exit_px - entry_px) / entry_px
                else:
                    gross = (entry_px - exit_px) / entry_px
                trades.append({
                    "entry_i": entry_i,
                    "exit_i": i,
                    "side": side,
                    "entry_px": entry_px,
                    "exit_px": exit_px,
                    "bars_held": bars_held,
                    "exit_reason": exit_reason,
                    "gross_pct": gross * 100,
                })
                side = 0

    # Close any open position at end
    if side != 0 and entry_i >= 0:
        exit_px = float(c.iloc[n - 1])
        if side > 0:
            gross = (exit_px - entry_px) / entry_px
        else:
            gross = (entry_px - exit_px) / entry_px
        trades.append({
            "entry_i": entry_i,
            "exit_i": n - 1,
            "side": side,
            "entry_px": entry_px,
            "exit_px": exit_px,
            "bars_held": n - 1 - entry_i,
            "exit_reason": "end",
            "gross_pct": gross * 100,
        })

    return trades


def analyze_crosses(df: pd.DataFrame) -> dict:
    """Analyze EMA21 cross quality: 1-candle vs multi-candle, persistence."""
    c = df["close"].astype(float)
    e21 = ema(c, 21)
    n = len(df)

    above = c > e21
    above_prev = above.shift(1).fillna(False)
    above = above.fillna(False)

    cross_up = above & ~above_prev
    cross_down = ~above & above_prev

    results = {"long": [], "short": []}

    for side_name, cross_mask in (("long", cross_up), ("short", cross_down)):
        idxs = np.flatnonzero(cross_mask.values)
        for i in idxs:
            if i >= n - 1:
                continue
            started_above = bool(above.iloc[i])
            # Count consecutive bars respecting the cross
            k = 0
            j = i + 1
            while j < n:
                ej = float(e21.iloc[j])
                if pd.isna(ej):
                    break
                cj = float(c.iloc[j])
                if started_above:
                    if cj > ej:
                        k += 1
                        j += 1
                    else:
                        break
                else:
                    if cj < ej:
                        k += 1
                        j += 1
                    else:
                        break
            # Was it a 1-candle cross (crossed back immediately) or sustained?
            results[side_name].append(k)

    return results


def main() -> int:
    all_trade_rows = []
    cross_stats = []

    for sym, tf in SYMS_TFS:
        try:
            df = load_csv(sym, tf)
        except FileNotFoundError:
            print(f"SKIP {sym}_{tf}: file not found")
            continue
        print(f"\n=== {sym} {tf} ({len(df)} bars) ===")

        # Cross quality analysis
        cross = analyze_crosses(df)
        for side_name in ("long", "short"):
            bars = np.asarray(cross[side_name], dtype=float)
            if bars.size == 0:
                continue
            cross_stats.append({
                "symbol": sym, "tf": tf, "side": side_name,
                "n_crosses": int(bars.size),
                "median_respect": float(np.median(bars)),
                "p75_respect": float(np.percentile(bars, 75)),
                "p90_respect": float(np.percentile(bars, 90)),
                "immediate_revert_pct": float((bars == 0).mean() * 100),
                "respect_1_pct": float((bars >= 1).mean() * 100),
                "respect_3_pct": float((bars >= 3).mean() * 100),
                "respect_5_pct": float((bars >= 5).mean() * 100),
            })
            print(
                "%s %s crosses: n=%d med=%.0f p75=%.0f p90=%.0f "
                "immediate_revert=%.1f%% respect>=3=%.1f%% respect>=5=%.1f%%"
                % (sym, side_name, bars.size, np.median(bars), np.percentile(bars, 75),
                   np.percentile(bars, 90), (bars == 0).mean() * 100,
                   (bars >= 3).mean() * 100, (bars >= 5).mean() * 100)
            )

        # Harvest simulation — test parameter combos
        for min_consec_long in (1, 3, 5):
            for min_consec_short in (1, 3, 5):
                for exit_on_ema in (True, False):
                    for max_hold in (24, 48):
                        for trail in (0.0, 1.5):
                            params = {
                                "min_consec_long": min_consec_long,
                                "min_consec_short": min_consec_short,
                                "exit_on_ema_break": exit_on_ema,
                                "rsi_exit_long": 80,
                                "rsi_exit_short": 20,
                                "max_hold": max_hold,
                                "trail_atr": trail,
                            }
                            trades = simulate_harvest(df, params)
                            if not trades:
                                continue
                            rets = np.asarray([t["gross_pct"] for t in trades]) / 100.0
                            total = float(np.prod(1 + rets) - 1) * 100
                            wins = rets[rets > 0]
                            losses = rets[rets < 0]
                            pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else 0
                            avg_bars = float(np.mean([t["bars_held"] for t in trades]))
                            n_trades = len(trades)

                            # Count exit reasons
                            reasons = {}
                            for t in trades:
                                reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1

                            all_trade_rows.append({
                                "symbol": sym, "tf": tf,
                                "min_consec_long": min_consec_long,
                                "min_consec_short": min_consec_short,
                                "exit_on_ema": exit_on_ema,
                                "max_hold": max_hold,
                                "trail_atr": trail,
                                "n_trades": n_trades,
                                "winrate": round(100 * (rets > 0).mean(), 1),
                                "total_pct": round(total, 2),
                                "pf": round(pf, 2),
                                "avg_bars_held": round(avg_bars, 1),
                                "ema_break_exits": reasons.get("ema_break", 0),
                                "rsi_extreme_exits": reasons.get("rsi_extreme", 0),
                                "time_stop_exits": reasons.get("time_stop", 0),
                                "trail_exits": reasons.get("trail_stop", 0),
                                "end_exits": reasons.get("end", 0),
                            })

    # Save all results
    df_out = pd.DataFrame(all_trade_rows)
    df_out.to_csv(OUT / "ema21_harvest_all.csv", index=False)

    # Show top configs by total return with pf >= 1
    good = df_out[(df_out["pf"] >= 1.0) & (df_out["n_trades"] >= 10)]
    if len(good):
        good = good.sort_values("total_pct", ascending=False)
        good.to_csv(OUT / "ema21_harvest_profitable.csv", index=False)
        print(f"\n=== {len(good)} profitable configs (pf>=1, n>=10) ===")
        print(good.head(15).to_string(index=False))
    else:
        print("\n=== NO profitable configs found ===")

    # Show cross stats
    cs = pd.DataFrame(cross_stats)
    cs.to_csv(OUT / "ema21_cross_stats.csv", index=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
