"""Export the chart template's indicator settings to a JSON file the EA can read.

The MT5 Strategy Tester / terminal loads templates from `MQL5/Profiles/Templates`,
which MQL5 file functions cannot open (they are sandboxed to `MQL5/Files`). So we
parse the template here and publish a small JSON that `install_ea.ps1` copies to
`MQL5/Files/Sovereign/chart_template.json` (and the shared Common folder).

This is the alignment contract: the EA renders and validates its analysis against
the indicators the human actually has on the chart.

Usage:
    .\\.venv\\Scripts\\python.exe scripts\\export_chart_template.py
    .\\.venv\\Scripts\\python.exe scripts\\export_chart_template.py --template templates\\fxmopol.tpl
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "templates" / "fxmopol.tpl"
DEFAULT_OUT = ROOT / "backtest" / "mt5_files" / "Sovereign" / "chart_template.json"

METHODS = {0: "SMA", 1: "EMA", 2: "SMMA", 3: "LWMA"}
PERIOD_UNITS = {0: "M", 1: "H", 2: "D", 3: "W", 4: "MN"}


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-16", "utf-8-sig", "utf-8", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def field(text: str, key: str) -> str | None:
    m = re.search(rf"(?m)^{re.escape(key)}=(.+)$", text)
    return m.group(1).strip() if m else None


def indicator_blocks(text: str) -> list[str]:
    return re.findall(r"<indicator>(.*?)</indicator>", text, re.DOTALL)


def parse(path: Path) -> dict:
    text = read_text(path)
    symbol = field(text, "symbol") or ""
    ptype = int(field(text, "period_type") or 0)
    psize = int(field(text, "period_size") or 0)
    period = f"{PERIOD_UNITS.get(ptype, '?')}{psize}" if psize else ""

    indicators: list[dict] = []
    emas: list[int] = []
    smma: int | None = None
    rsi: int | None = None
    fractals = False

    for block in indicator_blocks(text):
        name = field(block, "name") or ""
        period_val = field(block, "period")
        method_val = field(block, "method")

        if name == "Moving Average" and period_val:
            method = METHODS.get(int(method_val), "SMA") if method_val else "SMA"
            p = int(period_val)
            indicators.append({"name": name, "period": p, "method": method})
            if method == "EMA":
                emas.append(p)
            elif method == "SMMA" and smma is None:
                smma = p
        elif name == "Relative Strength Index" and period_val:
            rsi = int(period_val)
            indicators.append({"name": name, "period": rsi, "method": "RSI"})
        elif name == "Fractals":
            fractals = True
            indicators.append({"name": name})

    emas.sort()
    return {
        "schema_version": 1,
        "source": str(path.relative_to(ROOT)).replace("\\", "/"),
        "symbol": symbol,
        "period": period,
        "ema_fast": emas[0] if emas else 0,
        "ema_slow": emas[1] if len(emas) > 1 else 0,
        "smma": smma or 0,
        "rsi": rsi or 0,
        "fractals": fractals,
        "indicators": indicators,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", default=str(DEFAULT_SRC))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    src = Path(args.template)
    out = Path(args.out)
    if not src.exists():
        raise SystemExit(f"template not found: {src}")

    data = parse(src)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(
        f"[template] {data['symbol']} {data['period']} "
        f"EMA{data['ema_fast']}/{data['ema_slow']} "
        f"SMMA{data['smma']} RSI{data['rsi']} fractals={data['fractals']}"
    )
    print(f"[template] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
