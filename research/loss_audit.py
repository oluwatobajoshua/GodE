#!/usr/bin/env python3
"""Loss audit: split an MT5 tester report's P&L into signal vs. friction.

The tester HTML stores execution prices, so the price move already contains the
spread paid on entry and exit. This script reconstructs every round trip and
decomposes the reported net P&L into:

    net  =  gross_at_mid  -  spread  -  commission  +  swap

`gross_at_mid` is the counterfactual "what if we traded at mid with no
commission" number. If gross_at_mid is negative the *entry signal* is the
problem. If gross_at_mid is positive but net is negative, *cost* is the problem.

Usage:
    python research/loss_audit.py backtest/reports/london_breakout_2025_sp12.htm
    python research/loss_audit.py backtest/reports/*.htm --spread 12
    python research/loss_audit.py report.htm --spread 12 --deposit 10000 --risk 1.0

Writes:
    research/results/loss_audit_<report stem>.csv
    research/results/loss_audit_summary.csv   (appended, one row per report)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
SUMMARY = OUT / "loss_audit_summary.csv"

# Point size and value per 1.0 lot, quoted in account currency (USD).
# value = contract_size * point / quote_rate
CONTRACT = 100_000.0
POINT = {
    "EURUSD": 0.00001,
    "GBPUSD": 0.00001,
    "AUDUSD": 0.00001,
    "NZDUSD": 0.00001,
    "USDCAD": 0.00001,
    "USDCHF": 0.00001,
    "EURGBP": 0.00001,
    "EURJPY": 0.001,
    "GBPJPY": 0.001,
    "USDJPY": 0.001,
    "AUDJPY": 0.001,
}
JPY_QUOTE = {"EURJPY", "GBPJPY", "USDJPY", "AUDJPY"}
CHF_QUOTE = {"USDCHF"}
CAD_QUOTE = {"USDCAD"}


def read_report(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="ignore")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="ignore")
    for enc in ("utf-8", "cp1252"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if "Deals" in text:
            return text
    return raw.decode("utf-8", errors="ignore")


def num(cell: str) -> float:
    cell = re.sub(r"<[^>]+>", "", cell).replace("&nbsp;", " ").strip()
    if not cell:
        return 0.0
    cell = cell.replace(" ", "").replace(",", "")
    try:
        return float(cell)
    except ValueError:
        return 0.0


ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)


def parse_deals(html: str) -> pd.DataFrame:
    start = html.find("<b>Deals</b>")
    if start < 0:
        return pd.DataFrame()
    rows = []
    for tr in ROW_RE.finditer(html[start:]):
        cells = CELL_RE.findall(tr.group(1))
        if len(cells) < 13:
            continue
        deal_type = re.sub(r"<[^>]+>", "", cells[3]).strip().lower()
        if deal_type not in ("buy", "sell"):
            continue
        try:
            ts = pd.to_datetime(re.sub(r"<[^>]+>", "", cells[0]).strip())
        except (ValueError, TypeError):
            continue
        rows.append(
            {
                "time": ts,
                "deal": int(num(cells[1])),
                "symbol": re.sub(r"<[^>]+>", "", cells[2]).strip(),
                "deal_type": deal_type,
                "direction": re.sub(r"<[^>]+>", "", cells[4]).strip().lower(),
                "volume": num(cells[5]),
                "price": num(cells[6]),
                "commission": num(cells[8]),
                "swap": num(cells[9]),
                "profit": num(cells[10]),
                "comment": re.sub(r"<[^>]+>", "", cells[12]).strip(),
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def extract_metrics(html: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for label in (
        "Total Net Profit",
        "Gross Profit",
        "Gross Loss",
        "Profit Factor",
        "Total Trades",
        "Initial Deposit",
        "Total Commission",
        "Total Swap",
    ):
        m = re.search(re.escape(label) + r".{0,300}?<b>(.*?)</b>", html, re.I | re.S)
        if m:
            out[label] = num(m.group(1))
    return out


def point_value(symbol: str, price: float) -> float:
    """USD value of one point for 1.0 lot, at `price`."""
    base = symbol[:3]
    quote = symbol[3:6]
    p = POINT.get(symbol, 0.00001)
    if quote == "USD":
        return CONTRACT * p
    if quote == "JPY":
        return CONTRACT * p / max(price, 1e-9)
    if quote == "CHF":
        return CONTRACT * p / max(price, 1e-9)
    if quote == "CAD":
        return CONTRACT * p / max(price, 1e-9)
    if symbol == "EURGBP":
        # quoted in GBP; convert with a rough constant is not honest -> report 0
        return CONTRACT * p
    return CONTRACT * p


def pair_round_trips(deals: pd.DataFrame) -> pd.DataFrame:
    """FIFO match 'in' deals to 'out' deals per symbol."""
    if deals.empty:
        return pd.DataFrame()
    rows = []
    for symbol, grp in deals.groupby("symbol", sort=False):
        grp = grp.sort_values("time")
        open_pos: list[dict] = []
        for _, d in grp.iterrows():
            if d["direction"] == "in":
                open_pos.append(d.to_dict())
                continue
            if not open_pos:
                continue
            entry = open_pos.pop(0)
            side = 1 if entry["deal_type"] == "buy" else -1
            pv = point_value(symbol, entry["price"])
            gross = (d["price"] - entry["price"]) * side * d["volume"] * CONTRACT
            # Convert gross to USD when the pair is not XXX/USD.
            quote = symbol[3:6]
            if quote == "JPY":
                gross = gross / max(entry["price"], 1e-9)
            elif quote in ("CHF", "CAD"):
                gross = gross / max(entry["price"], 1e-9)
            friction = entry["commission"] + d["commission"] + entry["swap"] + d["swap"]
            net = d["profit"] + friction
            rows.append(
                {
                    "entry_time": entry["time"],
                    "exit_time": d["time"],
                    "symbol": symbol,
                    "side": "LONG" if side > 0 else "SHORT",
                    "volume": d["volume"],
                    "entry_price": entry["price"],
                    "exit_price": d["price"],
                    "bars_held": int((d["time"] - entry["time"]).total_seconds() // 60),
                    "commission": entry["commission"] + d["commission"],
                    "swap": entry["swap"] + d["swap"],
                    "net": net,
                    "gross": gross,
                    "cost_total": gross - net,
                    "point_value_usd": pv,
                    "comment_in": entry["comment"],
                    "comment_out": d["comment"],
                }
            )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values("entry_time").reset_index(drop=True)
    df["win"] = df["net"] > 0
    return df


def add_spread(trades: pd.DataFrame, spread_points: float) -> pd.DataFrame:
    """Attach the modelled spread cost and the mid-to-mid counterfactual."""
    t = trades.copy()
    t["spread_cost"] = (
        t["volume"] * t["point_value_usd"] * spread_points
    )
    # net = gross - spread - commission + swap  (swap already signed)
    t["gross_at_mid"] = t["net"] + t["spread_cost"] + t["commission"] - t["swap"]
    t["cost_all"] = t["spread_cost"] + t["commission"] - t["swap"]
    return t


def summarize(trades: pd.DataFrame, spread_points: float, name: str) -> dict:
    if trades.empty:
        return {"report": name, "n_trades": 0}
    net = trades["net"].sum()
    gross = trades["gross_at_mid"].sum()
    spread = trades["spread_cost"].sum()
    comm = trades["commission"].sum()
    swap = trades["swap"].sum()
    wins = trades[trades["win"]]
    losses = trades[~trades["win"]]
    return {
        "report": name,
        "n_trades": len(trades),
        "win_rate": round(100 * trades["win"].mean(), 1),
        "net_usd": round(net, 2),
        "gross_at_mid_usd": round(gross, 2),
        "spread_usd": round(spread, 2),
        "commission_usd": round(comm, 2),
        "swap_usd": round(swap, 2),
        "cost_share_pct": round(100 * (spread + comm - swap) / abs(gross), 1) if gross else 0.0,
        "avg_win": round(wins["net"].mean(), 2) if len(wins) else 0.0,
        "avg_loss": round(losses["net"].mean(), 2) if len(losses) else 0.0,
        "payoff": round(abs(wins["net"].mean() / losses["net"].mean()), 2)
        if len(wins) and len(losses) and losses["net"].mean() != 0
        else 0.0,
        "breakeven_wr_pct": round(
            100 * abs(losses["net"].mean()) / (abs(losses["net"].mean()) + wins["net"].mean()), 1
        )
        if len(wins) and len(losses) and wins["net"].mean() > 0
        else 0.0,
        "spread_points_assumed": spread_points,
        "verdict": (
            "signal negative (cost not the cause)"
            if gross <= 0
            else "signal positive, cost kills it"
            if net <= 0
            else "profitable after cost"
        ),
    }


def sensitivity(
    trades: pd.DataFrame, assumed: float, spreads=(0, 4, 6, 8, 12, 20, 30)
) -> pd.DataFrame:
    """Exact linear re-pricing of the realized net P&L.

    `units` is USD per 1 point of round-trip spread across all trades. Because
    net = gross - spread - commission + swap and only the spread changes,
    net(c) = net(assumed) + (assumed - c) * units.
    """
    units = float((trades["volume"] * trades["point_value_usd"]).sum())
    net_assumed = float(trades["net"].sum())
    rows = []
    for s in spreads:
        rows.append(
            {
                "spread_points": s,
                "net_usd": round(net_assumed + (assumed - s) * units, 2),
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit tester P&L: signal vs friction")
    ap.add_argument("reports", nargs="+", help="tester .htm report(s)")
    ap.add_argument("--spread", type=float, default=12.0, help="assumed spread in points")
    ap.add_argument("--json", action="store_true", help="print JSON only")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    all_rows = []
    for rp in args.reports:
        path = Path(rp)
        if not path.exists():
            print(f"[skip] missing {path}")
            continue
        html = read_report(path)
        deals = parse_deals(html)
        trades = pair_round_trips(deals)
        if trades.empty:
            print(f"[skip] no deals in {path.name}")
            continue
        trades = add_spread(trades, args.spread)
        stem = path.stem
        trades.to_csv(OUT / f"loss_audit_{stem}.csv", index=False)

        row = summarize(trades, args.spread, stem)
        metrics = extract_metrics(html)
        row["report_net_usd"] = metrics.get("Total Net Profit")
        row["report_trades"] = metrics.get("Total Trades")
        row["report_pf"] = metrics.get("Profit Factor")
        all_rows.append(row)

        if not args.json:
            print(f"\n=== {stem} ===")
            print(f"  trades            {row['n_trades']}   win rate {row['win_rate']}%")
            print(f"  reported net      {row['report_net_usd']}")
            print(f"  net (rebuilt)     {row['net_usd']}")
            print(f"  gross at mid      {row['gross_at_mid_usd']}   <- signal only, no friction")
            print(f"  spread cost       {row['spread_usd']}  ({args.spread:g} pts assumed)")
            print(f"  commission        {row['commission_usd']}")
            print(f"  swap              {row['swap_usd']}")
            print(f"  cost as % gross   {row['cost_share_pct']}%")
            print(f"  payoff ratio      {row['payoff']}   break-even WR {row['breakeven_wr_pct']}%")
            print(f"  VERDICT           {row['verdict']}")
            print("  spread sensitivity (re-priced):")
            for _, s in sensitivity(trades, args.spread).iterrows():
                print(f"    {s['spread_points']:>4.0f} pts -> net {s['net_usd']:>9.2f}")

    if all_rows:
        df = pd.DataFrame(all_rows)
        if SUMMARY.exists():
            old = pd.read_csv(SUMMARY)
            old = old[~old["report"].isin(df["report"])]
            df = pd.concat([old, df], ignore_index=True)
        df.to_csv(SUMMARY, index=False)
        print(f"\nWrote {SUMMARY}")
        if args.json:
            print(json.dumps(all_rows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
