#!/usr/bin/env python3
"""Project Sovereign - reproducible MetaTrader 5 Strategy Tester runner.

Generates a pinned .set and tester .ini, launches terminal64.exe headless with
ShutdownTerminal=1, waits for the HTML report, and writes a JSON summary.

The default target is EURUSD M5 on the locally installed Deriv-Demo history
(real ticks). Deriv is a B-book demo: use this for functional baselines, not
as a substitute for an ECN broker validation.

Examples:
    python scripts/run_backtest.py --prepare-only
    python scripts/run_backtest.py --from-date 2026.01.01 --to-date 2026.02.01
    python scripts/run_backtest.py --no-telemetry --model 1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKTEST_DIR = ROOT / "backtest"
REPORT_DIR = BACKTEST_DIR / "reports"
IS_WINDOWS = os.name == "nt"

FXMOPOL_EUR_CHART: dict[str, str] = {
    "InpPreset": "0",
    "InpEmaPeriod": "21",
    "InpSlowEma": "50",
    "InpSmmaPeriod": "100",
    "InpRsiPeriod": "9",
    "InpUsePlaybook": "false",
    "InpStreakRide": "false",
    "InpUseProxFilters": "true",
    "InpMaxPerDay": "2",
    "InpGoodSessionsOnly": "true",
}

FXMOPOL_GBP_PLAYBOOK: dict[str, str] = {
    "InpPreset": "2",
    "InpUsePlaybook": "true",
    "InpEnforceSymbolProfile": "false",
    "InpPlaybookSignalExitOnly": "true",
    "InpUseProxFilters": "true",
    "InpStrictStayAway": "true",
    "InpUseRegimeFilter": "true",
    "InpRequireD1Align": "true",
    "InpUseFullPriceAction": "true",
    "InpUseLevelTp": "true",
    "InpStackRegimeBars": "5",
    "InpRsiCoverLevel": "30.0",
    "InpMaxLossesPerDay": "1",
    "InpMaxPerDay": "2",
    "InpGoodSessionsOnly": "true",
    "InpWeekdayMask": "14",
    "InpSlAtr": "3.5",
    "InpTpAtr": "2.0",
    "InpRiskPct": "0.25",
    "InpEcosystemProfessor": "true",
    "InpEcoReloadSec": "120",
    "InpMarketPulse": "true",
}

LONDON_ORB_EURUSD: dict[str, str] = {
    "InpBoxHour0": "5",
    "InpBoxHour1": "6",
    "InpTradeHourStart": "7",
    "InpTradeHourEnd": "11",
    "InpBrokerUtcOffset": "0",
    "InpBufAtr": "0.15",
    "InpTpRMultiple": "1.2",
    "InpMaxHoldBars": "24",
    "InpMaxTradesPerDay": "2",
    "InpRiskPct": "0.25",
    "InpMaxSpread": "25",
    "InpMaxPositions": "1",
    "InpMaxDailyDD": "4.0",
    "InpEnablePerformanceGovernor": "false",
}

FXMOPOL_USDJPY_STREAK: dict[str, str] = {
    "InpPreset": "1",
    "InpUseProxFilters": "true",
    "InpMaxPerDay": "2",
    "InpGoodSessionsOnly": "true",
}

GODS_EYE_CARRY: dict[str, str] = {
    "InpStrategy": "0",
    "InpTopN": "4",
    "InpUseSovereignStackV1": "true",
    "InpStackRequireNeutral": "true",
    "InpStackVixLo": "16.0",
    "InpStackVixHi": "28.0",
    "InpStackVixMidScale": "0.45",
    "InpStackDdHaltEnable": "false",
    "InpRequirePythonSignal": "false",
    "InpEnableTelemetry": "false",
}

GODS_EYE_HEDGE_BLEND_D1: dict[str, str] = {
    "InpStrategy": "4",
    "InpTopN": "4",
    "InpUseSovereignStackV1": "true",
    "InpStackRequireNeutral": "true",
    "InpStackVixLo": "16.0",
    "InpStackVixHi": "28.0",
    "InpStackVixMidScale": "0.45",
    "InpStackDdHaltEnable": "false",
    "InpRequirePythonSignal": "false",
    "InpEnableTelemetry": "false",
    "InpHedgeBlendEnabled": "true",
    "InpHedgeBlendCarryWeight": "50",
    "InpHedgeBlendMomLookback": "3",
    "InpStructRideEnabled": "false",
    "InpRsiBandEnabled": "false",
}

GODS_EYE_RSI_BAND_H1_EURUSD: dict[str, str] = {
    "InpStrategy": "3",
    "InpTopN": "4",
    "InpUseSovereignStackV1": "false",
    "InpRequirePythonSignal": "false",
    "InpEnableTelemetry": "false",
    "InpRsiBandEnabled": "true",
    "InpRsiBandSymbols": "EURUSD",
    "InpRsiBandRiskPct": "0.5",
    "InpRsiBandMinHoldBars": "12",
    "InpRsiBandMargin": "3",
    "InpRsiBandSpreadGatePct": "90",
    "InpStructRideEnabled": "false",
    "InpHedgeBlendEnabled": "false",
}

GODS_EYE_CARRY_ECN: dict[str, str] = {
    **GODS_EYE_CARRY,
    "InpMaxSpread": "8",
}

GODS_EYE_CARRY_HIGH_RISK: dict[str, str] = {
    **GODS_EYE_CARRY,
    "InpRiskPerPosition": "2.0",
    "InpMaxTotalRisk": "8.0",
    "InpStackVixLo": "50.0",
    "InpStackVixHi": "80.0",
    "InpStackVixMidScale": "1.0",
    "InpMaxSpread": "8",
}

PROFILES: dict[str, dict[str, str]] = {
    "gods_eye_legacy_m5": {},  # filled below with BASE_INPUTS
    "gods_eye_carry_d1": GODS_EYE_CARRY,
    "gods_eye_carry_d1_ecn": GODS_EYE_CARRY_ECN,
    "gods_eye_carry_high_risk": GODS_EYE_CARRY_HIGH_RISK,
    "gods_eye_hedge_blend_d1": GODS_EYE_HEDGE_BLEND_D1,
    "gods_eye_rsi_band_h1_eurusd": GODS_EYE_RSI_BAND_H1_EURUSD,
    "fxmopol_eur_chart": FXMOPOL_EUR_CHART,
    "fxmopol_gbp_playbook": FXMOPOL_GBP_PLAYBOOK,
    "fxmopol_usdjpy_streak": FXMOPOL_USDJPY_STREAK,
    "london_orb_eurusd_m5": LONDON_ORB_EURUSD,
}

BASE_INPUTS: dict[str, str] = {
    "InpBaseRiskPercent": "1.0",
    "InpMaxDailyDD": "5.0",
    "InpMaxSpread": "30",
    "InpMaxSlippage": "3",
    "InpKillzoneStart": "7",
    "InpKillzoneEnd": "16",
    "InpStructuralLookback": "50",
    "InpExtremeThreshold": "0.2",
    "InpUseHTFConfluence": "true",
    "InpUseMacroTrend": "true",
    "InpEntryMode": "0",
    "InpEnablePartialClose": "true",
    "InpPartialCloseR": "1.5",
    "InpEnableBreakEven": "true",
    "InpBreakEvenR": "1.5",
    "InpSLATRMultiple": "1.5",
    "InpTPRMultiple": "3.0",
    "InpEntryBufferATR": "0.35",
    "InpPendingExpiryMinutes": "30",
    "InpNewsBufferMinutes": "30",
    "InpEnableTelemetry": "true",
    "InpPythonHost": "127.0.0.1",
    "InpPythonPort": "5556",
    "InpRequirePythonSignal": "false",
    "InpMinSignalConfidence": "0.6",
    "InpHeartbeatTimeoutSec": "3",
    "InpEnableAuditLog": "true",
}

PROFILES["gods_eye_legacy_m5"] = dict(BASE_INPUTS)

EXPERTS: dict[str, str] = {
    "gods_eye": r"Sovereign\SovereignGodsEyeEA.ex5",
    "fxmopol": r"Sovereign\SovereignFxmopolEA.ex5",
    "london_orb": r"Sovereign\SovereignLondonOrbEA.ex5",
}


def log(message: str) -> None:
    print(f"[BACKTEST] {message}", flush=True)


def die(message: str) -> "NoReturn":
    print(f"[FAIL] {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def terminal_data_dirs() -> list[Path]:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return []
    base = Path(appdata) / "MetaQuotes" / "Terminal"
    if not base.exists():
        return []
    return sorted(
        [path for path in base.iterdir() if (path / "MQL5" / "Experts").exists()],
        key=lambda path: (path / "config" / "common.ini").stat().st_mtime
        if (path / "config" / "common.ini").exists()
        else path.stat().st_mtime,
        reverse=True,
    )


def resolve_terminal(data_dir: Path) -> Path:
    origin = data_dir / "origin.txt"
    candidates: list[Path] = []
    if origin.exists():
        try:
            candidates.append(
                Path(origin.read_text(encoding="utf-8", errors="ignore").strip())
                / "terminal64.exe"
            )
        except OSError:
            pass
    candidates.extend(
        [
            Path(r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"),
            Path(r"C:\Program Files\MetaTrader 5\terminal64.exe"),
            Path(r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe"),
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    die("terminal64.exe not found; pass --terminal")


def write_utf16(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-16")


def write_set(path: Path, inputs: dict[str, str]) -> None:
    lines = ["; Project Sovereign baseline - generated by scripts/run_backtest.py"]
    lines.extend(f"{key}={value}" for key, value in inputs.items())
    write_utf16(path, "\n".join(lines) + "\n")
    log(f"wrote parameter set: {path}")


def write_ini(
    path: Path,
    expert: str,
    parameters_name: str,
    symbol: str,
    period: str,
    model: int,
    from_date: str,
    to_date: str,
    deposit: float,
    currency: str,
    leverage: str,
    report_relative: str,
    visual: bool = False,
    chart_template: str = "",
) -> None:
    lines = [
        "; Project Sovereign - generated tester config",
        "[Tester]",
        f"Expert={expert}",
        f"ExpertParameters={parameters_name}",
        f"Symbol={symbol}",
        f"Period={period}",
        f"Model={model}",
        "ExecutionMode=0",
        "Optimization=0",
        "FromDate=" + from_date,
        "ToDate=" + to_date,
        f"Deposit={deposit:g}",
        f"Currency={currency}",
        f"Leverage={leverage}",
        f"Visual={1 if visual else 0}",
        "ProfitInPips=0",
        f"Report={report_relative}",
        "ReplaceReport=1",
        "ShutdownTerminal=1",
    ]
    if chart_template:
        lines.append(f"Template={chart_template}")
    write_utf16(path, "\n".join(lines) + "\n")
    log(f"wrote tester config: {path}")


def start_brain(python: Path, log_path: Path) -> subprocess.Popen | None:
    if not IS_WINDOWS:
        return None

    import socket

    for port in (5555, 5556):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                log(f"port {port} already in use; assuming an existing brain is running")
                return None

    log_path.parent.mkdir(parents=True, exist_ok=True)
    stream = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            str(python),
            "python/institutional_quant_master.py",
            "--duration",
            "0",
        ],
        cwd=str(ROOT),
        stdout=stream,
        stderr=subprocess.STDOUT,
        text=True,
    )
    log(f"started brain pid={process.pid}; log={log_path}")
    time.sleep(2.0)
    return process


def stop_brain(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def tester_root(data_dir: Path) -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    root = Path(appdata) / "MetaQuotes" / "Tester" / data_dir.name
    return root if root.exists() else None


def newest_log(data_dir: Path, since: float) -> Path | None:
    candidates: list[Path] = []
    for folder in (data_dir / "logs", data_dir / "Tester" / "logs"):
        if not folder.exists():
            continue
        candidates.extend(
            path for path in folder.glob("*.log") if path.stat().st_mtime >= since
        )

    agent_root = tester_root(data_dir)
    if agent_root is not None:
        for path in agent_root.rglob("*.log"):
            try:
                if path.stat().st_mtime >= since:
                    candidates.append(path)
            except OSError:
                continue

    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    # MT5 writes the tester report as UTF-16 with a BOM. Check the BOM before
    # trying utf-8-sig, otherwise every byte decodes "successfully" as garbage.
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


def strip_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = value.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", value).strip()


def report_is_complete(
    html: str,
    *,
    symbol: str,
    period: str,
    deposit: float,
) -> bool:
    """Reject placeholder reports MT5 writes before a run (empty expert, M0, 0 bars)."""
    if re.search(r"Period:.*?<b>\s*M0\s*\(", html, re.IGNORECASE | re.DOTALL):
        return False
    if re.search(r"1970\.01\.01\s*-\s*1970\.01\.01", html):
        return False
    expert = re.search(
        r"Expert:</td>.*?<td[^>]*>\s*<b>(.*?)</b>",
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if expert is None or not strip_html(expert.group(1)):
        return False
    dep = re.search(
        r"Initial Deposit:</td>.*?<td[^>]*>\s*<b>(.*?)</b>",
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if dep is not None:
        dep_txt = strip_html(dep.group(1)).replace(",", "").replace(" ", "")
        try:
            if float(dep_txt) <= 0 and deposit > 0:
                return False
        except ValueError:
            pass
    bars = re.search(r"Bars:</td>\s*<td[^>]*>\s*<b>(\d+)</b>", html, re.IGNORECASE)
    if bars is None or int(bars.group(1)) <= 0:
        return False
    if symbol and symbol not in html:
        return False
    if period and period not in html:
        return False
    return True


def extract_metrics(html: str) -> dict[str, str]:
    """Best-effort extraction from the MT5 HTML report."""
    metrics: dict[str, str] = {}
    labels = [
        "Total Net Profit",
        "Gross Profit",
        "Gross Loss",
        "Profit Factor",
        "Expected Payoff",
        "Recovery Factor",
        "Sharpe Ratio",
        "Total Trades",
        "Short Trades (won %)",
        "Long Trades (won %)",
        "Maximal Drawdown",
        "Relative Drawdown",
        "Balance Drawdown Maximal",
        "Balance Drawdown Relative",
        "Total Commission",
        "Total Swap",
    ]
    for label in labels:
        # The report renders every value in a <b> element immediately after
        # the label cell, e.g. `Total Net Profit:</td><td><b>125.82</b>`.
        pattern = re.compile(
            re.escape(label) + r".{0,200}?<b>(.*?)</b>",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(html)
        if match:
            value = strip_html(match.group(1)).strip(" :")
            if value:
                metrics[label] = value
    return metrics


def poll_outcome_pnl(data_dir: Path, since: float) -> tuple[int, float]:
    """Sum Sovereign trade_outcomes.csv rows written during this tester run."""
    try:
        import pandas as pd
    except ImportError:
        return 0, 0.0
    n, pnl = 0, 0.0
    roots: list[Path] = []
    agent_root = tester_root(data_dir)
    if agent_root is not None:
        roots.append(agent_root)
    roots.extend((data_dir / "Tester", data_dir / "MQL5" / "Files"))
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("trade_outcomes.csv"):
            try:
                if path in seen or path.stat().st_mtime < since:
                    continue
                seen.add(path)
                df = pd.read_csv(path)
                if "profit" not in df.columns:
                    continue
                n += len(df)
                pnl += float(df["profit"].sum())
            except OSError:
                continue
    return n, pnl


def find_audit_files(data_dir: Path, since: float) -> list[Path]:
    roots = [data_dir / "Tester", data_dir / "MQL5" / "Files"]
    agent_root = tester_root(data_dir)
    if agent_root is not None:
        roots.append(agent_root)
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.csv"):
            if "Sovereign" in str(path) and path.stat().st_mtime >= since:
                found.append(path)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a reproducible Sovereign backtest")
    parser.add_argument("--terminal-data", help="MT5 data directory")
    parser.add_argument("--terminal", help="Path to terminal64.exe")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--period", default="M5")
    parser.add_argument("--model", type=int, default=4, help="0=OHLC, 1=M1 OHLC, 4=real ticks")
    parser.add_argument("--from-date", default="2026.01.01")
    parser.add_argument("--to-date", default="2026.02.01")
    parser.add_argument("--deposit", type=float, default=10000.0)
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--leverage", default="1:100")
    parser.add_argument("--name", default="baseline_eurusd_m5_202601")
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES.keys()),
        default="gods_eye_legacy_m5",
        help="EA input preset (fxmopol_* / gods_eye_carry_d1)",
    )
    parser.add_argument(
        "--expert",
        choices=sorted(EXPERTS.keys()),
        default="gods_eye",
        help="Expert under MQL5/Experts/",
    )
    parser.add_argument("--visual", action="store_true", help="Strategy Tester visual mode (slower)")
    parser.add_argument(
        "--chart-template",
        default="",
        help="Apply MT5 chart template in tester (e.g. fxmopol.tpl); auto for fxmopol_* profiles",
    )
    parser.add_argument(
        "--allow-running",
        action="store_true",
        help="Launch tester even if terminal64.exe is already open",
    )
    parser.add_argument("--timeout", type=int, default=1800, help="Seconds before aborting")
    parser.add_argument(
        "--early-fail",
        action="store_true",
        help="Abort fxmopol tester when early outcomes stay negative (graph failing)",
    )
    parser.add_argument(
        "--no-early-fail",
        action="store_true",
        help="Disable default early-fail on fxmopol_* profiles",
    )
    parser.add_argument(
        "--early-fail-trades",
        type=int,
        default=4,
        help="Min closed outcomes before early-fail check",
    )
    parser.add_argument(
        "--early-fail-max-net",
        type=float,
        default=-15.0,
        help="Net profit threshold (account currency); at or below = fail",
    )
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--no-brain", action="store_true", help="Do not start the Python brain")
    parser.add_argument("--no-telemetry", action="store_true", help="Set InpEnableTelemetry=false")
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Override one EA input (repeatable), e.g. --set InpTPRMultiple=2.0",
    )
    args = parser.parse_args()

    if not IS_WINDOWS:
        die("MetaTrader 5 Strategy Tester automation is Windows-only")

    data_dir = Path(args.terminal_data) if args.terminal_data else None
    if data_dir is None:
        dirs = terminal_data_dirs()
        if not dirs:
            die("No MT5 data directory found; pass --terminal-data")
        data_dir = dirs[0]
        log(f"using MT5 data directory: {data_dir}")

    terminal = Path(args.terminal) if args.terminal else resolve_terminal(data_dir)

    set_dir = data_dir / "MQL5" / "Profiles" / "Tester"
    set_dir.mkdir(parents=True, exist_ok=True)
    set_name = f"Sovereign_{args.name}.set"
    set_path = set_dir / set_name

    profile = dict(PROFILES.get(args.profile, {}))
    chart_tpl = args.chart_template
    use_visual = args.visual
    early_fail = args.early_fail or (
        args.profile.startswith("fxmopol_") and not args.no_early_fail
    )

    if args.profile.startswith("fxmopol_"):
        expert_rel = EXPERTS["fxmopol"]
        inputs = profile
        if not chart_tpl:
            chart_tpl = "fxmopol.tpl"
        # Visual mode is slow; use --visual when you need the chart (not forced).
    elif args.profile in ("gods_eye_carry_d1", "gods_eye_carry_d1_ecn", "gods_eye_carry_high_risk", "gods_eye_hedge_blend_d1", "gods_eye_rsi_band_h1_eurusd"):
        expert_rel = EXPERTS["gods_eye"]
        inputs = profile
    elif args.profile.startswith("london_orb_"):
        expert_rel = EXPERTS["london_orb"]
        inputs = profile
    else:
        expert_rel = EXPERTS[args.expert]
        inputs = dict(BASE_INPUTS)
        inputs.update(profile)
    if args.no_telemetry:
        inputs["InpEnableTelemetry"] = "false"
    for override in args.overrides:
        if "=" not in override:
            die(f"invalid --set value (expected KEY=VALUE): {override}")
        key, value = override.split("=", 1)
        key = key.strip()
        if not key:
            die(f"invalid --set value (empty key): {override}")
        inputs[key] = value.strip()
        log(f"input override: {key}={value.strip()}")
    write_set(set_path, inputs)

    if args.profile.startswith("fxmopol_"):
        sov_files = data_dir / "MQL5" / "Files" / "Sovereign"
        sov_files.mkdir(parents=True, exist_ok=True)
        for fn in ("performance_buckets.csv", "trade_outcomes.csv"):
            perf_path = sov_files / fn
            if perf_path.exists():
                import shutil

                archive = REPORT_DIR / f"{args.name}_pre_{fn}"
                shutil.copy2(perf_path, archive)
                perf_path.unlink()
                log(f"archived stale {fn} for isolated tester run -> {archive}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_base = REPORT_DIR / args.name
    for suffix in (".html", ".htm", ".json"):
        stale = report_base.with_suffix(suffix)
        if stale.exists():
            stale.unlink()

    #--- MT5 resolves Report relative to the platform directory. Absolute
    #--- paths silently produce no file, so stage it under <data>/reports.
    data_report_dir = data_dir / "reports"
    data_report_dir.mkdir(parents=True, exist_ok=True)
    report_name = f"{args.name}.htm"
    report_relative = f"reports\\{report_name}"
    report_expected = data_report_dir / report_name
    if report_expected.exists():
        report_expected.unlink()

    ini_path = BACKTEST_DIR / f"{args.name}.ini"
    write_ini(
        ini_path,
        expert=expert_rel,
        parameters_name=set_name,
        symbol=args.symbol,
        period=args.period,
        model=args.model,
        from_date=args.from_date,
        to_date=args.to_date,
        deposit=args.deposit,
        currency=args.currency,
        leverage=args.leverage,
        report_relative=report_relative,
        visual=use_visual,
        chart_template=chart_tpl,
    )

    import shutil

    ini_launch = data_dir / f"Sovereign_{args.name}.ini"
    shutil.copy2(ini_path, ini_launch)
    log(f"staged tester config for launch: {ini_launch}")

    tpl_src = ROOT / "templates" / chart_tpl if chart_tpl else None
    if tpl_src and tpl_src.exists():
        for sub in ("Profiles/Templates", "Profiles/Charts"):
            dest = data_dir / "MQL5" / sub
            dest.mkdir(parents=True, exist_ok=True)
            import shutil

            shutil.copy2(tpl_src, dest / chart_tpl)
            log(f"staged chart template: {dest / chart_tpl}")

    if args.prepare_only:
        log("prepare-only requested; not launching MetaTrader")
        return 0

    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        die("venv not found; run `python bootstrap.py` first")

    running = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq terminal64.exe", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    if "terminal64.exe" in running.stdout.lower() and not args.allow_running:
        die(
            "terminal64.exe is already running; close it or pass --allow-running "
            "(or run Strategy Tester manually with the generated .ini / .set)"
        )

    brain = None if args.no_brain else start_brain(python, BACKTEST_DIR / "brain_run.log")
    started = time.time()
    process = None
    try:
        log(f"launching {terminal}")
        process = subprocess.Popen(
            [str(terminal), f"/config:{ini_launch}"],
            cwd=str(terminal.parent),
        )

        report: Path | None = None
        early_failed = False
        deadline = started + args.timeout
        last_size = -1
        stable_since: float | None = None
        report_candidates = (
            report_expected,
            data_report_dir / f"{args.name}.html",
            terminal.parent / "reports" / report_name,
        )

        def pick_complete_report() -> Path | None:
            nonlocal last_size, stable_since
            for candidate in report_candidates:
                if not candidate.exists() or candidate.stat().st_size == 0:
                    continue
                try:
                    html_probe = read_text(candidate)
                except OSError:
                    continue
                if not report_is_complete(
                    html_probe,
                    symbol=args.symbol,
                    period=args.period,
                    deposit=args.deposit,
                ):
                    continue
                size = candidate.stat().st_size
                if size == last_size:
                    if stable_since is None:
                        stable_since = time.time()
                    elif time.time() - stable_since >= 2.0:
                        return candidate
                else:
                    last_size = size
                    stable_since = None
            return None

        while time.time() < deadline:
            if early_fail and expert_rel == EXPERTS["fxmopol"]:
                oc_n, oc_pnl = poll_outcome_pnl(data_dir, started)
                if oc_n >= args.early_fail_trades and oc_pnl <= args.early_fail_max_net:
                    log(
                        f"early-fail: {oc_n} outcomes, net {oc_pnl:.2f} "
                        f"(<= {args.early_fail_max_net}); aborting tester"
                    )
                    early_failed = True
                    if process.poll() is None:
                        process.terminate()
                    break
            report = pick_complete_report()
            if report is not None:
                break
            if process.poll() is not None:
                # MT5 may flush the report shortly after the process exits.
                for _ in range(30):
                    time.sleep(1.0)
                    report = pick_complete_report()
                    if report is not None:
                        break
                break
            time.sleep(2.0)

        if report is None:
            latest = newest_log(data_dir, started)
            if latest:
                print(f"--- tester log tail: {latest} ---")
                print("\n".join(read_text(latest).splitlines()[-60:]))
            hint = ""
            if args.allow_running:
                hint = (
                    " (MT5 was already running with --allow-running; close all "
                    "terminal64.exe windows and retry — a second instance often "
                    "writes an empty stub report and never runs the test.)"
                )
            die("backtest finished without a complete HTML report" + hint)

        #--- Keep a repository copy next to the JSON summary.
        repo_report = REPORT_DIR / report.name
        if report != repo_report:
            import shutil

            shutil.copy2(report, repo_report)

        # Give MT5 a moment to flush the report before parsing.
        time.sleep(1.0)
        html = read_text(repo_report)
        metrics = extract_metrics(html)
        summary = {
            "name": args.name,
            "early_failed": early_failed,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "symbol": args.symbol,
            "period": args.period,
            "model": args.model,
            "from_date": args.from_date,
            "to_date": args.to_date,
            "deposit": args.deposit,
            "currency": args.currency,
            "leverage": args.leverage,
            "telemetry": inputs["InpEnableTelemetry"] == "true",
            "report": str(repo_report),
            "metrics": metrics,
        }
        summary_path = REPORT_DIR / f"{args.name}.json"
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

        print("=" * 68)
        print(f" Backtest report: {repo_report}")
        print(f" Summary JSON:    {summary_path}")
        for label, value in metrics.items():
            print(f"  {label}: {value}")
        if not metrics:
            print("  (could not parse metrics; open the HTML report)")
        print("=" * 68)

        latest = newest_log(data_dir, started)
        if latest:
            text = read_text(latest)
            signal_lines = [
                line for line in text.splitlines()
                if "Sovereign telemetry" in line
                or "Sovereign EA Initialized" in line
                or "Python telemetry lost" in line
            ]
            if signal_lines:
                print("Telemetry log evidence:")
                for line in signal_lines[-10:]:
                    print("  " + line)

        audits = find_audit_files(data_dir, started)
        if audits:
            print("Audit CSVs:")
            for path in audits:
                print(f"  {path}")

        if expert_rel == EXPERTS["fxmopol"] and report is not None:
            review = subprocess.run(
                [str(python), str(ROOT / "research" / "fxmopol_tester_review.py"), str(repo_report)],
                cwd=str(ROOT / "research"),
                capture_output=True,
                text=True,
            )
            if review.stdout:
                print(review.stdout)
            if review.returncode != 0 and review.stderr:
                log(review.stderr[:500])

        return 0
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
        stop_brain(brain)


if __name__ == "__main__":
    raise SystemExit(main())
