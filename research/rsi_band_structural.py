#!/usr/bin/env python3
r"""RSI band structural research — CORRECTED.

Computes all indicators on the full dataframe before any dropna, matching
rsi_band_net.py methodology. Tests wider parameter grid: RSI period,
mid/hi/lo bands, EMA trend deadband. Compares gross vs net to isolate
signal vs cost failure.

Run:
    .\.venv\Scripts\python.exe research\rsi_band_structural.py --symbol GBPUSD --tf H1
    .\.venv\Scripts\python.exe research\rsi_band_structural.py --symbol USDJPY --tf H1
    .\.venv\Scripts\python.exe research\rsi_band_structural.py --symbol EURUSD --tf H1
"""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from ema_cross_harvest import ATR_P, EMA, RSI_P, ema, wilder_atr, wilder_rsi  # noqa: E402

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"

SPLITS = ("design", "validation", "oos")


def load_csv(symbol: str, tf: str = "H1") -> pd.DataFrame:
    path = DATA / f"{symbol}_{tf}.csv"
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)
    return df


def parse_template(path: Path) -> dict:
    """Parse fxmopol.tpl and return the indicator settings the human sees on chart."""
    text = path.read_text(encoding="utf-8", errors="replace")
    import re

    def field(text, key):
        m = re.search(rf"(?m)^{re.escape(key)}=(.+)$", text)
        return m.group(1).strip() if m else None

    symbol = field(text, "symbol") or "EURUSD"
    ptype = int(field(text, "period_type") or 0)
    psize = int(field(text, "period_size") or 0)
    PERIOD_UNITS = {0: "M", 1: "H", 2: "D", 3: "W", 4: "MN"}
    period = f"{PERIOD_UNITS.get(ptype, '?')}{psize}" if psize else "H1"

    indicators = []
    emas = []
    rsi_period = 9
    rsi_levels = [20, 50, 80]

    blocks = re.findall(r"<indicator>(.*?)</indicator>", text, re.DOTALL)
    for block in blocks:
        name = field(block, "name") or ""
        period_val = field(block, "period")
        method_val = field(block, "method")
        if name == "Moving Average" and period_val:
            method = {0: "SMA", 1: "EMA", 2: "SMMA", 3: "LWMA"}.get(int(method_val), "SMA") if method_val else "SMA"
            p = int(period_val)
            indicators.append({"name": name, "period": p, "method": method})
            if method == "EMA":
                emas.append(p)
        elif name == "Relative Strength Index" and period_val:
            rsi_period = int(period_val)
            indicators.append({"name": name, "period": rsi_period, "method": "RSI"})
        elif name == "Fractals":
            indicators.append({"name": name})

    # Extract RSI levels from <level> blocks
    levels = re.findall(r"<level>\s*level=([\d.]+)", text)
    if levels:
        rsi_levels = sorted([float(l) for l in levels])

    emas.sort()
    return {
        "symbol": symbol,
        "period": period,
        "ema_fast": emas[0] if emas else 21,
        "ema_slow": emas[1] if len(emas) > 1 else 50,
        "rsi_period": rsi_period,
        "rsi_levels": rsi_levels,
        "indicators": indicators,
    }


def simulate(rsi, mid, hi, lo, trend, close, spread_ok, cost_side, n, cfg):
    pos = np.zeros(n, dtype=int)
    state = 0
    entry_bar = -10 ** 9
    exit_bar = -10 ** 9
    cooldown = cfg["cooldown"]
    min_hold = cfg["min_hold"]
    margin = cfg["margin"]

    for t in range(1, n):
        r, rp = rsi[t], rsi[t - 1]
        m, mp, h, l = mid[t], mid[t - 1], hi[t], lo[t]
        if not (np.isfinite(r) and np.isfinite(m) and np.isfinite(h)
                and np.isfinite(l) and np.isfinite(rp) and np.isfinite(mp)):
            pos[t] = state
            continue

        if state > 0:
            held = t - entry_bar
            if trend[t] < 0:
                state = 0
                exit_bar = t
            elif r > h and held >= min_hold:
                state = 0
                exit_bar = t
        elif state < 0:
            held = t - entry_bar
            if trend[t] > 0:
                state = 0
                exit_bar = t
            elif r < l and held >= min_hold:
                state = 0
                exit_bar = t
        else:
            if t - exit_bar >= cooldown and spread_ok[t]:
                if trend[t] > 0 and rp <= mp and r > m + margin:
                    state = 1
                    entry_bar = t
                elif trend[t] < 0 and rp >= mp and r < m - margin:
                    state = -1
                    entry_bar = t
        pos[t] = state
    return pos


def returns_net(pos, close, cost_side):
    n = len(close)
    ret = np.zeros(n)
    ret[1:] = pos[:-1] * (close[1:] / close[:-1] - 1.0)
    turn = np.abs(np.diff(pos, prepend=0))
    ret -= turn * (cost_side / close)
    return ret


def returns_gross(pos, close):
    n = len(close)
    ret = np.zeros(n)
    ret[1:] = pos[:-1] * (close[1:] / close[:-1] - 1.0)
    return ret


def stats(ret, bars_per_year):
    eq = np.cumprod(1.0 + ret)
    yrs = max(len(ret) / bars_per_year, 1e-9)
    cagr = (eq[-1] ** (1.0 / yrs) - 1.0) if eq[-1] > 0 else -1.0
    peak = np.maximum.accumulate(eq)
    sd = ret.std()
    return dict(total=float(eq[-1] - 1.0), cagr=float(cagr),
                dd=float((eq / peak - 1.0).min()),
                sharpe=float(ret.mean() / sd * np.sqrt(bars_per_year)) if sd > 0 else 0.0)


def split_stats(ret, time, bars_per_year):
    t0, t1 = time.iloc[0], time.iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80
    bounds = {"design": (t0, edge1), "validation": (edge1, edge2), "oos": (edge2, t1)}
    out = {}
    for sp in SPLITS:
        a, b = bounds[sp]
        mask = ((time >= a) & (time < b)).to_numpy()
        out[sp] = stats(ret[mask], bars_per_year)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--comm-pts", type=float, default=7.0)
    ap.add_argument("--slip-pts", type=float, default=1.0)
    ap.add_argument("--floor-pts", type=float, default=1.0)
    ap.add_argument("--gate-pct", type=float, default=90.0)
    ap.add_argument("--template", default=str(ROOT / "templates" / "fxmopol.tpl"))
    ap.add_argument("--rsi-p", type=int, default=None, help="RSI period (template=9)")
    ap.add_argument("--mid", type=int, default=None, help="RSI mid band (template=50)")
    ap.add_argument("--hi", type=int, default=None, help="RSI high exit band (template=80)")
    ap.add_argument("--lo", type=int, default=None, help="RSI low exit band (template=20)")
    ap.add_argument("--db", type=float, default=None, help="EMA slope deadband (template=0.10)")
    args = ap.parse_args()

    sym, tf = args.symbol.upper(), args.tf.upper()
    df = load_csv(sym, tf)

    # --- Template alignment: read fxmopol.tpl (the eye on the market) ---
    tpl = parse_template(Path(args.template))
    TEMPLATE_EMA_FAST = tpl["ema_fast"]
    TEMPLATE_EMA_SLOW = tpl["ema_slow"]
    TEMPLATE_RSI = tpl["rsi_period"]
    TEMPLATE_RSI_MID = int(tpl["rsi_levels"][1]) if len(tpl["rsi_levels"]) >= 2 else 50
    TEMPLATE_RSI_HI = int(tpl["rsi_levels"][2]) if len(tpl["rsi_levels"]) >= 3 else 80
    TEMPLATE_RSI_LO = int(tpl["rsi_levels"][0]) if len(tpl["rsi_levels"]) >= 1 else 20
    TEMPLATE_RSI_MARGIN = 3.0

    print(f"[template] {tpl['symbol']} {tpl['period']} "
          f"EMA{TEMPLATE_EMA_FAST}/{TEMPLATE_EMA_SLOW} "
          f"RSI{TEMPLATE_RSI} levels={tpl['rsi_levels']}")

    # --- Compute ALL indicators on full df BEFORE dropna (match rsi_band_net.py) ---
    df["ema"] = ema(df["close"], TEMPLATE_EMA_FAST)
    df["atr"] = wilder_atr(df, 14)

    close = df["close"].to_numpy(float)
    spread = df["spread"].astype(float).to_numpy()
    time = df["time"]

    # cost model
    point = 0.001 if sym.endswith("JPY") else 0.00001
    spread_eff = np.maximum(spread, args.floor_pts)
    cost_side_pts = spread_eff / 2.0 + args.slip_pts + args.comm_pts / 2.0
    cost_side = cost_side_pts * point

    # spread gate
    q = pd.Series(spread).rolling(500, min_periods=100).quantile(args.gate_pct / 100.0)
    spread_ok_base = (spread <= q.to_numpy()) | ~np.isfinite(q.to_numpy())

    bars_per_year = {"H1": 24 * 252, "H4": 6 * 252, "D1": 252, "M15": 24 * 4 * 252, "M30": 24 * 2 * 252}[tf]

    # Template-driven parameter grid (fxmopol.tpl: EMA21/50, RSI9, levels 20/50/80)
    # Primary test = template config; small local sweep around it for robustness
    rsi_periods = [args.rsi_p] if args.rsi_p else [TEMPLATE_RSI]
    mids = [args.mid] if args.mid else [TEMPLATE_RSI_MID]
    hi_lo_pairs = [(args.hi, args.lo)] if args.hi else [(TEMPLATE_RSI_HI, TEMPLATE_RSI_LO)]
    deadbands = [args.db] if args.db else [0.10]

    rows = []
    for rsi_p in rsi_periods:
        rsi = wilder_rsi(df["close"], rsi_p).to_numpy(float)
        for mid in mids:
            for hi, lo in hi_lo_pairs:
                for db in deadbands:
                    e = df["ema"].to_numpy(float)
                    atr = df["atr"].to_numpy(float)
                    n = len(df)

                    slope3 = np.zeros(n)
                    slope3[3:] = (e[3:] - e[:-3]) / np.maximum(atr[3:], 1e-12)
                    trend = np.where(slope3 > db, 1,
                                     np.where(slope3 < -db, -1, 0))

                    rsi_col = rsi
                    mid_arr = np.full(n, float(mid))
                    hi_arr = np.full(n, float(hi))
                    lo_arr = np.full(n, float(lo))

                    cfg = dict(cooldown=0, min_hold=12, margin=TEMPLATE_RSI_MARGIN)
                    pos = simulate(rsi_col, mid_arr, hi_arr, lo_arr, trend, close,
                                   spread_ok_base, cost_side, n, cfg)

                    ret_net = returns_net(pos, close, cost_side)
                    ret_gross = returns_gross(pos, close)
                    st_net = split_stats(ret_net, time, bars_per_year)
                    st_gross = split_stats(ret_gross, time, bars_per_year)

                    rt = int(np.abs(np.diff(pos, prepend=0)).sum()) // 2
                    rows.append(dict(
                        rsi_p=rsi_p, mid=mid, hi=hi, lo=lo, db=db, rt=rt,
                        gross_design=st_gross["design"]["total"] * 100,
                        gross_validation=st_gross["validation"]["total"] * 100,
                        gross_oos=st_gross["oos"]["total"] * 100,
                        net_design=st_net["design"]["total"] * 100,
                        net_validation=st_net["validation"]["total"] * 100,
                        net_oos=st_net["oos"]["total"] * 100,
                        gross_all3_pos=all(st_gross[sp]["total"] > 0 for sp in SPLITS),
                        net_all3_pos=all(st_net[sp]["total"] > 0 for sp in SPLITS),
                    ))

    tbl = pd.DataFrame(rows)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"rsi_band_structural_{sym}_{tf}.csv"
    tbl.to_csv(out, index=False)

    print(f"\n=== RSI band structural (CORRECTED): {sym} {tf} ===")
    print(f"bars {n}  {time.iloc[0]} -> {time.iloc[-1]}")
    print(f"configs tested: {len(tbl)}")
    print()

    gross_pos = tbl[tbl["gross_all3_pos"]]
    net_pos = tbl[tbl["net_all3_pos"]]
    print(f"Gross positive in all 3 splits: {len(gross_pos)} of {len(tbl)}")
    print(f"Net positive in all 3 splits:  {len(net_pos)} of {len(tbl)}")
    print()

    if len(net_pos):
        print("--- TOP 10 NET configs (ranked by worst of design/validation net) ---")
        g = net_pos.copy()
        g["worst_dv_net"] = g[["net_design", "net_validation"]].min(axis=1)
        g = g.sort_values("worst_dv_net", ascending=False)
        for _, r in g.head(10).iterrows():
            print(f"  rsi_p={r.rsi_p} mid={r.mid} hi={r.hi} lo={r.lo} db={r.db} "
                  f"RT={r.rt}  net D/V/OOS={r.net_design:+.2f}/{r.net_validation:+.2f}/{r.net_oos:+.2f}%  "
                  f"gross D/V/OOS={r.gross_design:+.2f}/{r.gross_validation:+.2f}/{r.gross_oos:+.2f}%")
    else:
        print("NO net-positive config found in all 3 splits.")

    if len(gross_pos):
        print()
        print("--- TOP 10 GROSS configs ---")
        g = gross_pos.copy()
        g["worst_dv_gross"] = g[["gross_design", "gross_validation"]].min(axis=1)
        g = g.sort_values("worst_dv_gross", ascending=False)
        for _, r in g.head(10).iterrows():
            print(f"  rsi_p={r.rsi_p} mid={r.mid} hi={r.hi} lo={r.lo} db={r.db} "
                  f"RT={r.rt}  gross D/V/OOS={r.gross_design:+.2f}/{r.gross_validation:+.2f}/{r.gross_oos:+.2f}%  "
                  f"net D/V/OOS={r.net_design:+.2f}/{r.net_validation:+.2f}/{r.net_oos:+.2f}%")

    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
