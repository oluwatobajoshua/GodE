#!/usr/bin/env python3
"""Playbook backtest + equity graph as standalone HTML/SVG (no matplotlib, no MT5).

Run:
    .\\.venv\\Scripts\\python.exe research\\fxmopol_plot_equity.py --symbol GBPUSD
"""
from __future__ import annotations

import argparse
import html
from pathlib import Path

import pandas as pd

from fxmopol_playbook import SPLITS, backtest, load_h1, metrics, replay_trades
from fxmopol_symbol_train import load_symbol
from period_returns import print_split_monthly, publish_results, to_daily, to_monthly

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"

W, H = 900, 420
PAD = 48


def _polyline(points: list[tuple[float, float]], color: str, stroke: float = 2.0) -> str:
    if len(points) < 2:
        return ""
    pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    return f'<polyline fill="none" stroke="{color}" stroke-width="{stroke}" points="{pts}"/>'


def _scale_series(values: list[float], y0: float, y1: float) -> list[float]:
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        mid = (y0 + y1) / 2
        return [mid] * len(values)
    return [y1 - (v - lo) / (hi - lo) * (y1 - y0) for v in values]


def write_equity_html(name: str, daily: pd.DataFrame, monthly: pd.DataFrame, stats: dict) -> Path:
    out = OUT / f"{name}_equity.html"
    lines: list[str] = []
    bars: list[str] = []

    if not daily.empty and len(daily) > 1:
        eq = daily["equity"].astype(float).tolist()
        n = len(eq)
        xs = [PAD + (W - 2 * PAD) * i / (n - 1) for i in range(n)]
        ys = _scale_series(eq, PAD, H - PAD)
        lines.append(_polyline(list(zip(xs, ys)), "#2563eb", 2.0))

    if not monthly.empty:
        rets = monthly["return_pct"].astype(float).tolist()
        n = len(rets)
        bw = max(4.0, (W - 2 * PAD) / max(n, 1) * 0.7)
        y0, y1 = H * 0.55, H - PAD
        abs_max = max(abs(min(rets)), abs(max(rets)), 0.01)
        for i, r in enumerate(rets):
            x = PAD + (W - 2 * PAD) * (i + 0.5) / n - bw / 2
            hbar = abs(r) / abs_max * (y1 - y0) * 0.45
            y = y0 if r >= 0 else y0
            if r >= 0:
                rect = f'<rect x="{x:.1f}" y="{y0 - hbar:.1f}" width="{bw:.1f}" height="{hbar:.1f}" fill="#16a34a" opacity="0.85"/>'
            else:
                rect = f'<rect x="{x:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{hbar:.1f}" fill="#dc2626" opacity="0.85"/>'
            bars.append(rect)

    title = html.escape(name)
    oos = stats.get("oos", {})
    meta = (
        f"OOS trades={oos.get('n', 0)} exp_bps={oos.get('exp_bps')} "
        f"PF={oos.get('pf')} total%={oos.get('total_pct')}"
    )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px;background:#fafafa">
  <text x="{PAD}" y="28" font-family="system-ui,sans-serif" font-size="14" fill="#111">{title}</text>
  <text x="{PAD}" y="46" font-family="system-ui,sans-serif" font-size="11" fill="#555">{html.escape(meta)}</text>
  <line x1="{PAD}" y1="{H-PAD}" x2="{W-PAD}" y2="{H-PAD}" stroke="#ccc"/>
  <line x1="{PAD}" y1="{PAD}" x2="{PAD}" y2="{H-PAD}" stroke="#ccc"/>
  {''.join(lines)}
  <line x1="{PAD}" y1="{H*0.55:.0f}" x2="{W-PAD}" y2="{H*0.55:.0f}" stroke="#999" stroke-dasharray="4"/>
  {''.join(bars)}
  <text x="{PAD}" y="{H-12}" font-size="10" fill="#666">Daily equity (top) + monthly return % bars (bottom). Research only.</text>
</svg>"""

    body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>{title} equity</title>
<style>body{{font-family:system-ui,sans-serif;margin:1.25rem;max-width:960px}}</style>
</head>
<body>
<h1>{title}</h1>
<p>{meta} — <strong>not deployable</strong> (AGENTS.md). No extra packages; open this file in a browser.</p>
{svg}
<p>CSV: <code>{html.escape(name)}_monthly.csv</code>, <code>{html.escape(name)}_daily.csv</code></p>
</body></html>"""
    out.write_text(body, encoding="utf-8")
    return out


def run_symbol(sym: str, ema_mid: int) -> dict:
    try:
        h1 = load_symbol(sym)
    except FileNotFoundError:
        h1 = load_h1(sym)
    trade_r = backtest(h1, ema_mid=ema_mid)
    name = f"playbook_{sym}_mid{ema_mid}"
    publish_results(name, trade_r)
    print_split_monthly(name, trade_r, SPLITS)
    oos = trade_r[(trade_r.index >= SPLITS["OOS"][0]) & (trade_r.index < SPLITS["OOS"][1])]
    m_oos = metrics(oos)
    print(f"{sym} OOS: n={m_oos['n']} exp_bps={m_oos['exp_bps']} pf={m_oos['pf']} total%={m_oos['total_pct']}")

    daily = to_daily(trade_r)
    monthly = to_monthly(trade_r)
    chart = write_equity_html(name, daily, monthly, {"oos": m_oos})
    trades = replay_trades(h1, ema_mid=ema_mid, symbol=sym)
    trades_path = OUT / f"{name}_trades.csv"
    trades.to_csv(trades_path, index=False)
    print(f"Graph: file:///{chart.resolve()}")
    print(f"Wrote {chart.name}, {trades_path.name}")
    return {"symbol": sym, "oos": m_oos, "html": str(chart)}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", action="append", default=["GBPUSD", "EURUSD"])
    args = p.parse_args()
    for sym in args.symbol:
        ema = 100 if sym.upper() == "USDJPY" else 50
        run_symbol(sym.upper(), ema)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
