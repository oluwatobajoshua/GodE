#!/usr/bin/env python3
"""Pick best InpBrokerUtcOffset from sweep JSON reports."""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
REPORTS = REPO / "backtest" / "reports"
OUT = HERE / "results" / "london_orb_utc_pick.json"
PROMO = HERE / "results" / "scalp_promotion_verdict.json"


def _float(s: str) -> float:
    return float(re.sub(r"[^\d.\-]", "", str(s).replace(" ", "")) or "0")


def _int(s: str) -> int:
    return int(_float(s))


def load_report(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    py_n = 768
    if PROMO.exists():
        promo = json.loads(PROMO.read_text(encoding="utf-8"))
        for c in promo.get("candidates", []):
            if c.get("symbol") == "EURUSD" and c.get("strategy") == "london_orb":
                py_n = int(c.get("oos_n") or py_n)
                break

    rows = []
    for p in sorted(REPORTS.glob("london_orb_eurusd_oos_utc*.json")):
        m = re.search(r"utc(-?\d+)", p.name)
        if not m:
            continue
        off = int(m.group(1))
        if off < -2 or off > 5:
            continue
        data = load_report(p)
        if not data:
            continue
        met = data.get("metrics") or {}
        net = _float(met.get("Total Net Profit", "0"))
        pf = _float(met.get("Profit Factor", "0"))
        n = _int(met.get("Total Trades", "0"))
        rows.append(
            {
                "utc_offset": off,
                "net_profit": net,
                "pf": pf,
                "trades": n,
                "report": str(p),
                "passes_pf": pf >= 1.05,
                "passes_net": net > 0,
                "trade_delta": abs(n - py_n),
            }
        )

    if not rows:
        default = REPORTS / "london_orb_eurusd_m5_oos.json"
        if default.exists():
            data = load_report(default)
            met = (data or {}).get("metrics") or {}
            rows.append(
                {
                    "utc_offset": 0,
                    "net_profit": _float(met.get("Total Net Profit", "0")),
                    "pf": _float(met.get("Profit Factor", "0")),
                    "trades": _int(met.get("Total Trades", "0")),
                    "report": str(default),
                    "passes_pf": _float(met.get("Profit Factor", "0")) >= 1.05,
                    "passes_net": _float(met.get("Total Net Profit", "0")) > 0,
                    "trade_delta": abs(_int(met.get("Total Trades", "0")) - py_n),
                }
            )

    def score(r: dict) -> tuple:
        ok = r["passes_pf"] and r["passes_net"]
        return (ok, r["pf"], r["net_profit"], -r["trade_delta"])

    rows.sort(key=score, reverse=True)
    pick = rows[0] if rows else None
    payload = {
        "python_oos_trades": py_n,
        "candidates": rows,
        "pick": pick,
        "parity_ok": bool(pick and pick["passes_pf"] and pick["passes_net"]),
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"\nWrote {OUT}")
    return 0 if payload["parity_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
