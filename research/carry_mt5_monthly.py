#!/usr/bin/env python3
"""Monthly equity from MT5 Strategy Tester HTML + side-by-side Python carry research.

Run after carry book backtests:
    .\\.venv\\Scripts\\python.exe research\\carry_mt5_monthly.py
    .\\.venv\\Scripts\\python.exe research\\carry_mt5_monthly.py --report backtest\\reports\\gods_eye_carry_d1_ecn_EURUSD_D1_book.htm
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
REPORTS = REPO / "backtest" / "reports"
OUT_MONTHLY = HERE / "results" / "carry_mt5_book_monthly.csv"
OUT_COMPARE = HERE / "results" / "carry_mt5_python_mt5_monthly_compare.csv"
OUT_MANIFEST = HERE / "results" / "carry_mt5_monthly_manifest.json"
PYTHON_MONTHLY = HERE / "results" / "carry_mom_filter_monthly.csv"

DEFAULT_REPORTS = (
    REPORTS / "gods_eye_carry_d1_EURUSD_D1_book.htm",
    REPORTS / "gods_eye_carry_d1_ecn_EURUSD_D1_book.htm",
)


def read_tester_html(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    for encoding in ("utf-8", "mbcs"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def parse_balance(value: str) -> float:
    return float(str(value).replace(" ", "").replace(",", "") or "0")


def parse_deals(html: str) -> pd.DataFrame:
    """Extract deal rows after <b>Deals</b> header (MT5 tester report)."""
    start = html.find("<b>Deals</b>")
    if start < 0:
        return pd.DataFrame()
    chunk = html[start:]
    rows = []
    for m in re.finditer(
        r'<tr[^>]*><td[^>]*>([\d.\s:]+)</td><td>(\d+)</td><td>([^<]*)</td><td>([^<]*)</td>'
        r"<td>([^<]*)</td><td>([\d.\s]+)</td><td>([\d.\s]*)</td><td>(\d*)</td>"
        r"<td>([^<]*)</td><td>([^<]*)</td><td>([^<]*)</td><td>([^<]*)</td><td>([^<]*)</td>",
        chunk,
    ):
        rows.append(
            {
                "time": m.group(1).strip(),
                "deal": int(m.group(2)),
                "symbol": m.group(3).strip(),
                "deal_type": m.group(4).strip(),
                "direction": m.group(5).strip(),
                "volume": float(m.group(6).replace(" ", "")) if m.group(6).strip() else 0.0,
                "price": float(m.group(7).replace(" ", "")) if m.group(7).strip() else None,
                "commission": parse_balance(m.group(9)) if m.group(9).strip() else 0.0,
                "swap": parse_balance(m.group(10)) if m.group(10).strip() else 0.0,
                "profit": parse_balance(m.group(11)) if m.group(11).strip() else 0.0,
                "balance": parse_balance(m.group(12)) if m.group(12).strip() else None,
                "comment": m.group(13).strip(),
            }
        )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"])
    return df.sort_values("time").reset_index(drop=True)


def load_deposit(report: Path) -> float:
    summary = report.with_suffix(".json")
    if summary.exists():
        data = json.loads(summary.read_text(encoding="utf-8"))
        dep = data.get("deposit")
        if dep is not None:
            return float(dep)
    return 10_000.0


def monthly_equity_from_deals(deals: pd.DataFrame, deposit: float) -> pd.DataFrame:
    """Calendar-month returns from last posted balance each month."""
    if deals.empty or deals["balance"].isna().all():
        return pd.DataFrame(columns=["date", "return_pct", "trades", "equity", "win_period"])

    deals = deals.dropna(subset=["balance"]).copy()
    deals["month"] = deals["time"].dt.to_period("M")
    closed = deals[deals["direction"] == "out"]
    trade_counts = closed.groupby("month").size().rename("trades")

    eom = deals.groupby("month").last()[["balance", "time"]]
    eom = eom.join(trade_counts, how="left").fillna({"trades": 0})
    eom["trades"] = eom["trades"].astype(int)

    months = sorted(eom.index)
    prev = deposit
    rows: list[dict] = []
    equity_factor = 1.0
    for month in months:
        bal = float(eom.loc[month, "balance"])
        ret = bal / prev - 1.0 if prev > 0 else 0.0
        equity_factor *= 1.0 + ret
        rows.append(
            {
                "date": month.to_timestamp(how="start"),
                "return_pct": round(ret * 100.0, 4),
                "trades": int(eom.loc[month, "trades"]),
                "equity": round(equity_factor, 6),
                "win_period": ret > 0,
                "balance_usd": round(bal, 2),
            }
        )
        prev = bal
    return pd.DataFrame(rows)


def profile_slug(report: Path) -> str:
    name = report.name.replace(".htm", "").replace(".html", "")
    if name.startswith("gods_eye_carry_d1_ecn"):
        return "carry_mt5_ecn_book"
    return "carry_mt5_book"


def build_monthly_rows(strategy: str, tab: pd.DataFrame) -> list[dict]:
    rows = []
    for _, row in tab.iterrows():
        rows.append(
            {
                "strategy": strategy,
                "period": "month",
                "date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
                "return_pct": row["return_pct"],
                "trades": int(row["trades"]),
                "equity": row["equity"],
                "win_period": bool(row["win_period"]),
                "balance_usd": row.get("balance_usd"),
                "source_report": row.get("source_report", ""),
            }
        )
    return rows


def compare_python_mt5(mt5: pd.DataFrame, from_date: str, to_date: str) -> pd.DataFrame:
    if not PYTHON_MONTHLY.exists():
        return pd.DataFrame()
    py = pd.read_csv(PYTHON_MONTHLY)
    py["date"] = pd.to_datetime(py["date"])
    py = py[(py["date"] >= from_date) & (py["date"] < to_date)]
    if mt5.empty:
        return pd.DataFrame()
    m = mt5.copy()
    m["date"] = pd.to_datetime(m["date"])
    py["month"] = py["date"].dt.to_period("M")
    m["month"] = m["date"].dt.to_period("M")
    merged = py.merge(m, on="month", suffixes=("_python", "_mt5"))
    merged["date"] = merged["date_python"]
    merged["delta_pct"] = merged["return_pct_mt5"] - merged["return_pct_python"]
    return merged[
        [
            "date",
            "return_pct_python",
            "return_pct_mt5",
            "delta_pct",
            "trades_python",
            "trades_mt5",
            "win_period_python",
            "win_period_mt5",
        ]
    ].sort_values("date")


def summarize_window(tab: pd.DataFrame) -> dict:
    if tab.empty:
        return {}
    r = tab["return_pct"] / 100.0
    compound = float((1 + r).prod() - 1) * 100.0
    return {
        "months": len(tab),
        "compound_return_pct": round(compound, 2),
        "avg_month_pct": round(float(r.mean() * 100), 3),
        "win_months": int((r > 0).sum()),
        "win_month_pct": round(100.0 * float((r > 0).mean()), 1),
        "best_month_pct": round(float(r.max() * 100), 2),
        "worst_month_pct": round(float(r.min() * 100), 2),
    }


def print_last_months(title: str, tab: pd.DataFrame, n: int = 12) -> None:
    if tab.empty:
        print(f"{title}\n  (no data)\n")
        return
    view = tab.tail(n)[["date", "return_pct", "trades", "equity", "win_period"]].copy()
    view["date"] = pd.to_datetime(view["date"]).dt.strftime("%Y-%m")
    print(title)
    print(view.to_string(index=False))
    print()


def process_report(report: Path) -> tuple[str, pd.DataFrame, dict]:
    if not report.exists():
        return profile_slug(report), pd.DataFrame(), {"error": f"missing {report}"}
    html = read_tester_html(report)
    deals = parse_deals(html)
    deposit = load_deposit(report)
    tab = monthly_equity_from_deals(deals, deposit)
    tab["source_report"] = str(report)
    summary_path = report.with_suffix(".json")
    from_date = "1900-01-01"
    to_date = "2100-01-01"
    if summary_path.exists():
        meta = json.loads(summary_path.read_text(encoding="utf-8"))
        from_date = meta.get("from_date", from_date)
        to_date = meta.get("to_date", to_date)
    stats = summarize_window(tab)
    stats.update(
        {
            "report": str(report),
            "deposit": deposit,
            "from_date": from_date,
            "to_date": to_date,
            "deals_parsed": len(deals),
        }
    )
    return profile_slug(report), tab, stats


def main() -> int:
    parser = argparse.ArgumentParser(description="MT5 carry book monthly returns + Python compare")
    parser.add_argument("--report", action="append", default=[], help="Tester .htm (repeatable)")
    parser.add_argument("--last-months", type=int, default=12)
    args = parser.parse_args()

    reports = [Path(p) for p in args.report] if args.report else list(DEFAULT_REPORTS)
    all_rows: list[dict] = []
    manifest: dict = {"profiles": {}, "outputs": {}}
    primary_compare = pd.DataFrame()

    for report in reports:
        slug, tab, stats = process_report(report)
        manifest["profiles"][slug] = stats
        if tab.empty:
            print(f"[WARN] No monthly rows for {report}")
            continue
        all_rows.extend(build_monthly_rows(slug, tab))
        print_last_months(
            f"=== {slug} - last {args.last_months} months (MT5 broker tester) ===",
            tab,
            args.last_months,
        )
        if slug == "carry_mt5_book" and PYTHON_MONTHLY.exists():
            primary_compare = compare_python_mt5(
                tab,
                stats.get("from_date", "2024-01-01"),
                stats.get("to_date", "2026-01-01"),
            )
            if not primary_compare.empty:
                print("=== Python vs MT5 (same calendar months) ===")
                view = primary_compare.tail(args.last_months).copy()
                view["date"] = pd.to_datetime(view["date"]).dt.strftime("%Y-%m")
                print(view.to_string(index=False))
                corr = primary_compare["return_pct_python"].corr(primary_compare["return_pct_mt5"])
                print(f"\nCorrelation (monthly return %): {corr:.2f}")
                print(
                    f"Mean abs delta: {primary_compare['delta_pct'].abs().mean():.2f} pp | "
                    f"months Python+: {(primary_compare['return_pct_python'] > 0).sum()}/"
                    f"{len(primary_compare)} | MT5+: {(primary_compare['return_pct_mt5'] > 0).sum()}/"
                    f"{len(primary_compare)}\n"
                )
                manifest["python_compare"] = {
                    "months": len(primary_compare),
                    "correlation": round(float(corr), 3),
                    "mean_abs_delta_pp": round(float(primary_compare["delta_pct"].abs().mean()), 3),
                }

    if all_rows:
        OUT_MONTHLY.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(all_rows).to_csv(OUT_MONTHLY, index=False)
        manifest["outputs"]["monthly_csv"] = str(OUT_MONTHLY)
    if not primary_compare.empty:
        primary_compare.to_csv(OUT_COMPARE, index=False)
        manifest["outputs"]["compare_csv"] = str(OUT_COMPARE)

    OUT_MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_MONTHLY}")
    if OUT_COMPARE.exists():
        print(f"Wrote {OUT_COMPARE}")
    print(f"Wrote {OUT_MANIFEST}")
    return 0 if all_rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
