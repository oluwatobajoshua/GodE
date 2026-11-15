#!/usr/bin/env python3
"""Parse an MT4/MT5 .tpl template so the bot draws in the user's chart language.

The template is the shared visual vocabulary: it says which averages exist, in
which colour, and where the RSI band lines sit. If the bot draws something the
template does not contain, the user and the bot are not looking at the same chart.
This module removes that ambiguity by reading the template instead of guessing.

templates/fxmopol.tpl is UTF-16LE with a BOM, which is why a plain text reader
reports it as binary. It is decoded explicitly here.

What it extracts
    chart colours      background / foreground / candle up-down / grid
    main indicators    Moving Average period + method + colour
    fractals           arrow glyphs and colour
    sub indicators     RSI period, colour and its horizontal levels

Method codes (MT4/MT5): 0=SMA, 1=EMA, 2=SMMA, 3=LWMA.
Colour codes are COLORREF 0x00BBGGRR, so R = c & 0xFF, G = (c>>8) & 0xFF,
B = (c>>16) & 0xFF. Getting this backwards swaps cyan and yellow, which is
exactly the kind of silent mismatch this file exists to prevent.

Usage
    .\\.venv\\Scripts\\python.exe research/chart_template.py
    .\\.venv\\Scripts\\python.exe research/chart_template.py --tpl templates/fxmopol.tpl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TPL = ROOT / "templates" / "fxmopol.tpl"

MA_METHOD = {0: "SMA", 1: "EMA", 2: "SMMA", 3: "LWMA"}
TF_LABEL = {0: "M", 1: "H", 2: "D", 3: "W", 4: "MN"}


def decode_tpl(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] == b"\xff\xfe":
        return raw.decode("utf-16-le")
    if raw[:2] == b"\xfe\xff":
        return raw.decode("utf-16-be")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw.decode("utf-8-sig")
    return raw.decode("utf-8", errors="replace")


def colorref_to_hex(value: str) -> str:
    c = int(str(value).strip() or 0)
    r, g, b = c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF
    return f"#{r:02x}{g:02x}{b:02x}"


def _kv(line: str):
    if "=" not in line:
        return None, None
    k, v = line.split("=", 1)
    return k.strip(), v.strip()


def parse_tpl(path: Path = DEFAULT_TPL) -> dict:
    text = decode_tpl(path)
    lines = text.splitlines()

    spec: dict = {
        "file": str(path),
        "symbol": "",
        "timeframe": {"period_type": None, "period_size": None, "label": ""},
        "colors": {},
        "main": [],
        "fractals": None,
        "rsi": None,
        "raw_indicators": [],
    }

    #--- chart-level keys live before the first <window>
    for line in lines:
        if line.strip().startswith("<window>"):
            break
        k, v = _kv(line)
        if k == "symbol":
            spec["symbol"] = v
        elif k == "period_type":
            spec["timeframe"]["period_type"] = int(v)
        elif k == "period_size":
            spec["timeframe"]["period_size"] = int(v)
        elif k in ("background_color", "foreground_color", "barup_color",
                   "bardown_color", "bullcandle_color", "bearcandle_color",
                   "grid_color", "bidline_color", "askline_color",
                   "chartline_color", "volume_color", "volumes_color"):
            spec["colors"][k.replace("_color", "")] = colorref_to_hex(v)

    pt = spec["timeframe"]["period_type"]
    ps = spec["timeframe"]["period_size"]
    if pt is not None and ps is not None:
        spec["timeframe"]["label"] = f"{TF_LABEL.get(pt, '?')}{ps}"

    #--- indicators are blocks: <indicator> ... </indicator>
    block: dict | None = None
    graph: dict | None = None
    for line in lines:
        s = line.strip()
        if s == "<indicator>":
            block = {"name": "", "graphs": [], "levels": [], "period": None,
                     "method": None}
            graph = None
            continue
        if s == "</indicator>":
            if block is not None:
                spec["raw_indicators"].append(block)
                name = block["name"]
                if name == "Moving Average":
                    spec["main"].append({
                        "period": block["period"],
                        "method": MA_METHOD.get(block["method"], str(block["method"])),
                        "color": block["graphs"][0]["color"] if block["graphs"] else None,
                    })
                elif name == "Fractals":
                    spec["fractals"] = {
                        "up_arrow": block["graphs"][0].get("arrow") if block["graphs"] else None,
                        "down_arrow": block["graphs"][1].get("arrow") if len(block["graphs"]) > 1 else None,
                        "color": block["graphs"][0]["color"] if block["graphs"] else None,
                    }
                elif name == "Relative Strength Index":
                    spec["rsi"] = {
                        "period": block["period"],
                        "color": block["graphs"][0]["color"] if block["graphs"] else None,
                        "levels": sorted(block["levels"]),
                    }
            block = None
            continue
        if block is None:
            continue
        if s == "<graph>":
            graph = {}
            continue
        if s == "</graph>":
            if graph is not None:
                block["graphs"].append(graph)
            graph = None
            continue
        if s == "<level>":
            block.setdefault("_level", {})
            continue
        if s == "</level>":
            lv = block.pop("_level", None)
            if lv and "level" in lv:
                block["levels"].append(float(lv["level"]))
            continue
        k, v = _kv(s)
        if k is None:
            continue
        if graph is not None:
            if k == "color":
                graph["color"] = colorref_to_hex(v)
            elif k == "arrow":
                graph["arrow"] = int(v)
            else:
                graph[k] = v
        elif "_level" in block:
            block["_level"][k] = v
        elif k == "name":
            block["name"] = v
        elif k == "period":
            block["period"] = int(v)
        elif k == "method":
            block["method"] = int(v)

    return spec


def to_ini(spec: dict, name: str = "fxmopol") -> str:
    """Flat ASCII spec for the MQL5 side.

    Deliberately not JSON: the EA has to parse this with no dependencies, and a
    line-oriented KEY=VALUE file is the one format that is impossible to get wrong
    in MQL5. It is written as pure ASCII so FileOpen(FILE_ANSI) reads it cleanly.
    """
    c = spec["colors"]
    out = ["#SOVEREIGN_TEMPLATE_SPEC=1",
           f"NAME={name}",
           f"SYMBOL={spec['symbol']}",
           f"TIMEFRAME={spec['timeframe']['label']}",
           f"PERIOD_TYPE={spec['timeframe']['period_type']}",
           f"PERIOD_SIZE={spec['timeframe']['period_size']}",
           f"COLOR_BG={c.get('background', '#000000')}",
           f"COLOR_FG={c.get('foreground', '#ffffff')}",
           f"COLOR_UP={c.get('barup', c.get('bullcandle', '#00ff00'))}",
           f"COLOR_DOWN={c.get('bardown', c.get('bearcandle', '#ff0000'))}",
           f"COLOR_GRID={c.get('grid', '#778899')}",
           f"COLOR_BID={c.get('bidline', '#778899')}",
           f"COLOR_ASK={c.get('askline', '#ff0000')}"]

    mas = spec["main"]
    out.append(f"MA_COUNT={len(mas)}")
    for i, ma in enumerate(mas):
        out.append(f"MA{i}_PERIOD={ma['period']}")
        out.append(f"MA{i}_METHOD={ma['method']}")
        out.append(f"MA{i}_COLOR={ma['color']}")

    fr = spec["fractals"]
    out.append(f"FRACTAL_ENABLED={0 if fr is None else 1}")
    out.append(f"FRACTAL_COLOR={fr['color'] if fr else '#ffffff'}")
    out.append(f"FRACTAL_UP_ARROW={fr['up_arrow'] if fr else 217}")
    out.append(f"FRACTAL_DOWN_ARROW={fr['down_arrow'] if fr else 218}")

    rsi = spec["rsi"]
    out.append(f"RSI_PERIOD={rsi['period'] if rsi else 9}")
    out.append(f"RSI_COLOR={rsi['color'] if rsi else '#1e90ff'}")
    levels = rsi["levels"] if rsi else [20.0, 50.0, 80.0]
    out.append(f"RSI_LEVEL_COUNT={len(levels)}")
    for i, lv in enumerate(levels):
        out.append(f"RSI_LEVEL_{i}={lv:g}")

    return "\n".join(out) + "\n"


def summary(spec: dict) -> str:
    c = spec["colors"]
    tf = spec["timeframe"]["label"]
    bits = [f"{spec['symbol']} {tf}",
            f"bg {c.get('background')} fg {c.get('foreground')}",
            f"candles {c.get('barup')}/{c.get('bardown')}"]
    for ma in spec["main"]:
        bits.append(f"{ma['method']}{ma['period']} {ma['color']}")
    if spec["fractals"]:
        bits.append(f"fractals {spec['fractals']['color']}")
    if spec["rsi"]:
        bits.append(f"RSI{spec['rsi']['period']} {spec['rsi']['color']} "
                    f"levels {spec['rsi']['levels']}")
    return " | ".join(bits)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tpl", default=str(DEFAULT_TPL))
    ap.add_argument("--json", default="", help="write the parsed spec here")
    ap.add_argument("--ini", default="", help="write the flat ASCII spec the EA reads")
    ap.add_argument("--name", default="", help="spec name (default: template filename stem)")
    args = ap.parse_args()

    spec = parse_tpl(Path(args.tpl))
    print(summary(spec))
    print()
    print("main window indicators:")
    for ma in spec["main"]:
        print(f"  {ma['method']}{ma['period']:>4}  {ma['color']}")
    if spec["fractals"]:
        print(f"fractals: arrows {spec['fractals']['up_arrow']}/"
              f"{spec['fractals']['down_arrow']}  {spec['fractals']['color']}")
    if spec["rsi"]:
        print(f"RSI{spec['rsi']['period']} levels {spec['rsi']['levels']}  "
              f"{spec['rsi']['color']}")
    print()
    print("chart colours:", json.dumps(spec["colors"], indent=2))

    if args.json:
        Path(args.json).write_text(json.dumps(spec, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")

    if args.ini:
        stem = args.name or Path(args.tpl).stem
        Path(args.ini).parent.mkdir(parents=True, exist_ok=True)
        Path(args.ini).write_text(to_ini(spec, stem), encoding="ascii", errors="replace")
        print(f"wrote {args.ini}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
