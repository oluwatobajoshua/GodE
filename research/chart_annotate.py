#!/usr/bin/env python3
"""Draw what the bot sees on the chart - the same thing a signal provider would draw.

You can hand a screenshot to a vision model and get arrows and shaded "bad areas".
This does the same job from the repo's own bars, deterministically, with no model and
no screenshot, so the picture is reproducible and auditable:

  * candles, EMA21 and EMA50
  * look-left swing highs/lows (k=2 fractals) drawn as S/R levels - and a swing is
    only drawn from the bar where it could actually be CONFIRMED (i + k), so the
    picture never shows a level the bot could not have known at the time
  * RSI(9) with the 25/50/75 band
  * shaded "bad areas" where the bot should not trust a signal:
      - cost hostile: quoted spread above the trailing p90
      - volatility spike: ATR above 1.8x its 100-bar mean
  * entry arrows and exit marks from the VALIDATED RSI band rule
    (min-hold 12, margin 3, spread gate) - the only H1 rule that survived
  * a summary panel with the bot's current read

Read-only: this only produces a PNG. It cannot change trading behaviour.

Usage
    .\\.venv\\Scripts\\python.exe research/chart_annotate.py --symbol EURUSD --tf H1
    .\\.venv\\Scripts\\python.exe research/chart_annotate.py --symbol GBPUSD --tf H1 --bars 300
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from ema_cross_harvest import ATR_P, EMA, EMA_MID, RSI_P, ema, wilder_atr, wilder_rsi  # noqa: E402
from rsi_band_net import simulate  # noqa: E402
from structure_quality import swings  # noqa: E402

DATA = ROOT / "research" / "data" / "mt5"
OUT = ROOT / "research" / "results" / "charts"

BG = "#0e1116"
FG = "#d8dee9"
GRID = "#22262e"
UP = "#26a69a"
DN = "#ef5350"
EMA1 = "#f0b90b"
EMA2 = "#5c9ded"
SUP = "#4caf50"
RES = "#ff7043"
BAD = "#ef5350"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--bars", type=int, default=320)
    ap.add_argument("--k", type=int, default=2, help="swing fractal half-width")
    ap.add_argument("--margin", type=float, default=3.0)
    ap.add_argument("--min-hold", type=int, default=12)
    ap.add_argument("--note", default="", help="ai_note JSON to overlay (from llm_chart_analyst)")
    args = ap.parse_args()

    sym, tf = args.symbol.upper(), args.tf.upper()
    path = DATA / f"{sym}_{tf}.csv"
    if not path.exists():
        raise SystemExit(f"missing data: {path}")

    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)

    df["ema"] = ema(df["close"], EMA)
    df["ema_mid"] = ema(df["close"], EMA_MID)
    df["rsi"] = wilder_rsi(df["close"], RSI_P)
    df["atr"] = wilder_atr(df, ATR_P)
    df = df.dropna(subset=["ema", "ema_mid", "rsi", "atr"]).reset_index(drop=True)

    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    e1 = df["ema"].to_numpy(float)
    e2 = df["ema_mid"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    spread = df["spread"].astype(float).to_numpy()
    time = df["time"]
    n = len(df)
    point = 0.001 if sym.endswith("JPY") else 0.00001

    # ---- the validated rule, exactly as researched
    slope3 = np.zeros(n)
    slope3[3:] = (e1[3:] - e1[:-3]) / np.maximum(atr[3:], 1e-12)
    trend = np.where(slope3 > 0.10, 1, np.where(slope3 < -0.10, -1, 0))
    mid = np.full(n, 50.0)
    hi = np.full(n, 75.0)
    lo = np.full(n, 25.0)
    cost_side = (np.maximum(spread, 1.0) / 2.0 + 1.0 + 3.5) * point
    q = pd.Series(spread).rolling(500, min_periods=100).quantile(0.90).to_numpy()
    spread_ok = (spread <= q) | ~np.isfinite(q)
    pos = simulate(rsi, mid, hi, lo, trend, c, spread_ok, cost_side, n,
                   dict(cooldown=0, min_hold=args.min_hold, margin=args.margin))

    # ---- visible window
    end = n
    start = max(0, end - args.bars)
    sl = slice(start, end)
    x = np.arange(end - start)
    oo, hh, ll, cc = o[sl], h[sl], l[sl], c[sl]
    ee1, ee2, rr = e1[sl], e2[sl], rsi[sl]
    tt = time.iloc[sl].to_numpy()

    # ---- shaded bad areas
    atr_ratio = atr / pd.Series(atr).rolling(100, min_periods=20).mean().to_numpy()
    bad_cost = (spread > q) & np.isfinite(q)
    vol_spike = atr_ratio > 1.8
    hours = time.dt.hour.to_numpy()
    thin = (hours >= 21) | (hours <= 1)          # rollover / thin liquidity

    fig = plt.figure(figsize=(18, 10), facecolor=BG)
    gs = fig.add_gridspec(3, 1, height_ratios=[5.0, 1.3, 0.55], hspace=0.06)
    ax = fig.add_subplot(gs[0])
    axr = fig.add_subplot(gs[1], sharex=ax)
    axv = fig.add_subplot(gs[2], sharex=ax)
    for a in (ax, axr, axv):
        a.set_facecolor(BG)
        a.tick_params(colors=FG, labelsize=8)
        for s in a.spines.values():
            s.set_color(GRID)
        a.grid(True, color=GRID, linewidth=0.5, alpha=0.6)

    # ---- shade bad zones
    for arr, color, label in ((thin[sl], "#3a3f4b", "thin liquidity"),
                              (bad_cost[sl], BAD, "spread > p90"),
                              (vol_spike[sl], "#ff9800", "vol spike")):
        idxs = np.flatnonzero(arr)
        for i in idxs:
            ax.axvspan(i - 0.5, i + 0.5, color=color, alpha=0.10, lw=0)

    # ---- candles
    for i in range(len(x)):
        up = cc[i] >= oo[i]
        col = UP if up else DN
        ax.vlines(x[i], ll[i], hh[i], color=col, linewidth=0.8, alpha=0.9)
        body_lo = min(oo[i], cc[i])
        body_h = max(abs(cc[i] - oo[i]), (hh[i] - ll[i]) * 0.02, 1e-9)
        ax.add_patch(Rectangle((x[i] - 0.32, body_lo), 0.64, body_h,
                               facecolor=col, edgecolor=col, linewidth=0.5, alpha=0.85))

    ax.plot(x, ee1, color=EMA1, linewidth=1.1, label=f"EMA{EMA}")
    ax.plot(x, ee2, color=EMA2, linewidth=1.0, alpha=0.8, label=f"EMA{EMA_MID}")

    # ---- look-left S/R: a swing is drawn only from the bar that CONFIRMED it
    up_s, dn_s = swings(h, l, args.k)
    levels = []
    for i in range(start, end):
        if up_s[i] and i + args.k < end:
            levels.append((i + args.k, h[i], "R"))
        if dn_s[i] and i + args.k < end:
            levels.append((i + args.k, l[i], "S"))
    last_close = c[-1]
    levels = [lv for lv in levels if abs(lv[1] - last_close) <= 3.0 * atr[-1]]
    levels = levels[-12:]
    for conf_i, price, kind in levels:
        col = RES if kind == "R" else SUP
        ax.hlines(price, conf_i - start, len(x) - 1, color=col,
                  linewidth=0.9, linestyle=(0, (4, 3)), alpha=0.75)
        ax.text(len(x) - 1.5, price, f" {kind}", color=col, fontsize=7,
                va="center", ha="left", alpha=0.9)

    # ---- entries and exits from the validated rule
    entries = 0
    for i in range(start + 1, end):
        if pos[i] != 0 and pos[i - 1] == 0:
            xi = i - start
            d = pos[i]
            y = ll[xi] - 0.6 * atr[i] if d > 0 else hh[xi] + 0.6 * atr[i]
            ax.annotate("", xy=(xi, y), xytext=(xi, y + (-1 if d > 0 else 1) * 0.9 * atr[i]),
                        arrowprops=dict(arrowstyle="-|>", color=UP if d > 0 else DN,
                                        lw=1.8, mutation_scale=14))
            entries += 1
        elif pos[i] == 0 and pos[i - 1] != 0:
            xi = i - start
            ax.plot(xi, cc[xi], marker="x", color=FG, markersize=5, alpha=0.8)

    # ---- RSI panel
    axr.plot(x, rr, color="#b388ff", linewidth=1.0)
    axr.axhline(50, color=FG, linewidth=0.6, alpha=0.5)
    axr.axhline(75, color=DN, linewidth=0.6, linestyle="--", alpha=0.6)
    axr.axhline(25, color=UP, linewidth=0.6, linestyle="--", alpha=0.6)
    axr.fill_between(x, 25, 75, color="#4caf50", alpha=0.05)
    axr.set_ylim(0, 100)
    axr.set_ylabel("RSI9", color=FG, fontsize=8)

    # ---- spread panel
    axv.fill_between(x, 0, spread[sl], color="#90a4ae", alpha=0.7, linewidth=0)
    axv.set_ylabel("spread", color=FG, fontsize=7)

    # ---- x labels
    step = max(1, len(x) // 12)
    ticks = list(range(0, len(x), step))
    axv.set_xticks(ticks)
    axv.set_xticklabels([pd.Timestamp(tt[i]).strftime("%m-%d %H:%M") for i in ticks],
                        rotation=0, fontsize=7, color=FG)
    plt.setp(ax.get_xticklabels(), visible=False)
    plt.setp(axr.get_xticklabels(), visible=False)

    # ---- structure read from the last confirmed swings
    hi_idx = np.flatnonzero(up_s[:end])
    lo_idx = np.flatnonzero(dn_s[:end])
    if len(hi_idx) >= 2 and len(lo_idx) >= 2:
        hh_ = h[hi_idx[-1]] > h[hi_idx[-2]]
        hl_ = l[lo_idx[-1]] > l[lo_idx[-2]]
        if hh_ and hl_:
            struct = "HH/HL  uptrend"
        elif (not hh_) and (not hl_):
            struct = "LH/LL  downtrend"
        else:
            struct = "mixed / range"
    else:
        struct = "not enough swings"

    slope = (e1[-1] - e1[-6]) / atr[-1]
    if trend[-1] > 0:
        view, vcol = "LONG bias (EMA21 up)", UP
    elif trend[-1] < 0:
        view, vcol = "SHORT bias (EMA21 down)", DN
    else:
        view, vcol = "FLAT (EMA21 flat)", "#9e9e9e"
    rsi_state = ("overbought >75" if rsi[-1] > 75 else
                 "oversold <25" if rsi[-1] < 25 else
                 "mid band (trade zone)")
    sp = spread[-1]

    ax.set_title(f"{sym} {tf}   last {len(x)} bars   "
                 f"{pd.Timestamp(tt[0]):%Y-%m-%d} -> {pd.Timestamp(tt[-1]):%Y-%m-%d}",
                 color=FG, fontsize=11, loc="left")
    ax.legend(loc="upper left", facecolor=BG, edgecolor=GRID, labelcolor=FG, fontsize=8)

    panel = "\n".join([
        "BOT VIEW",
        f"price      {last_close:.5f}",
        f"EMA21 slope {slope:+.2f} ATR",
        f"RSI9       {rsi[-1]:.1f}  ({rsi_state})",
        f"structure  {struct}",
        f"spread     {sp:.1f} pts  ({'OK' if sp <= (q[-1] if np.isfinite(q[-1]) else 1e9) else 'HOSTILE'})",
        f"entries in view  {entries}",
        "",
        f"read: {view}",
    ])
    ax.text(0.005, 0.99, panel, transform=ax.transAxes, va="top", ha="left",
            fontsize=8.5, family="monospace", color=FG,
            bbox=dict(facecolor="#161a20", edgecolor=GRID, boxstyle="round,pad=0.5"))
    ax.text(0.995, 0.99, view, transform=ax.transAxes, va="top", ha="right",
            fontsize=11, family="monospace", color=vcol, weight="bold")

    legend_bits = [("bad area: spread > p90", BAD),
                   ("bad area: volatility spike", "#ff9800"),
                   ("bad area: thin liquidity", "#3a3f4b")]
    for j, (lab, col) in enumerate(legend_bits):
        ax.text(0.005, 0.30 - j * 0.035, "  " + lab, transform=ax.transAxes,
                fontsize=7.5, color=col)

    # ---- overlay the AI note if one was supplied
    if args.note:
        p = Path(args.note)
        if not p.exists():
            print(f"note not found: {p}")
        else:
            blob = json.loads(p.read_text(encoding="utf-8"))
            note = blob.get("note", blob)
            d = note.get("draw", {}) or {}
            for lv in d.get("levels", []):
                col = RES if lv.get("kind") == "resistance" else SUP
                ax.axhline(float(lv["price"]), color=col, linewidth=1.4,
                           linestyle=":", alpha=0.95)
            for z in d.get("zones", []):
                ax.axhspan(float(z["price_low"]), float(z["price_high"]),
                           color="#ffd54f", alpha=0.12, lw=0)
            for ar in d.get("arrows", []):
                xi = len(x) - 1 + int(ar["bar_offset"])
                if 0 <= xi < len(x):
                    up_ = ar.get("direction") == "up"
                    y = ll[xi] - 0.5 * atr[-1] if up_ else hh[xi] + 0.5 * atr[-1]
                    ax.annotate("", xy=(xi, y),
                                xytext=(xi, y + (-1 if up_ else 1) * 0.8 * atr[-1]),
                                arrowprops=dict(arrowstyle="-|>",
                                                color=UP if up_ else DN, lw=2.0,
                                                mutation_scale=16))
            txt = (f"AI READ: {str(note.get('read', 'wait')).upper()}  "
                   f"conf {float(note.get('confidence', 0)):.2f}"
                   + ("  VETO" if note.get("veto") else ""))
            ax.text(0.995, 0.02, txt, transform=ax.transAxes, va="bottom", ha="right",
                    fontsize=9, family="monospace",
                    color=(DN if note.get("veto") else FG), weight="bold",
                    bbox=dict(facecolor="#161a20", edgecolor=GRID,
                              boxstyle="round,pad=0.4"))
            narr = str(note.get("narrative", ""))
            if narr:
                wrapped = "\n".join(narr[i:i + 64]
                                    for i in range(0, min(len(narr), 256), 64))
                ax.text(0.005, 0.02, wrapped, transform=ax.transAxes, va="bottom",
                        ha="left", fontsize=7.5, family="monospace", color=FG,
                        bbox=dict(facecolor="#161a20", edgecolor=GRID,
                                  boxstyle="round,pad=0.4", alpha=0.9))

    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{sym}_{tf}.png"
    fig.savefig(out, dpi=130, facecolor=BG, bbox_inches="tight")
    plt.close(fig)

    print(f"=== chart annotation  {sym} {tf} ===")
    print(f"window  {pd.Timestamp(tt[0]):%Y-%m-%d %H:%M} -> {pd.Timestamp(tt[-1]):%Y-%m-%d %H:%M}"
          f"  ({len(x)} bars)")
    print(f"price {last_close:.5f}   RSI9 {rsi[-1]:.1f} ({rsi_state})   "
          f"EMA21 slope {slope:+.2f} ATR")
    print(f"structure {struct}")
    print(f"spread {sp:.1f} pts   entries drawn {entries}   "
          f"S/R levels drawn {len(levels)}")
    print(f"bad bars in view: spread {int(bad_cost[sl].sum())}, "
          f"vol spike {int(vol_spike[sl].sum())}, thin {int(thin[sl].sum())}")
    print(f"read: {view}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
