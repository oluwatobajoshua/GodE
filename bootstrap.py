#!/usr/bin/env python3
"""Project Sovereign - one-command workstation bootstrap.

Rebuilds the Python environment and, on Windows, installs/compiles the MQL5 EA
into the detected MetaTrader 5 data directory. Then runs an end-to-end feed
smoke test over both ZeroMQ and the native TCP bridge.

Typical fresh-machine flow:

    copy/clone the repository
    python bootstrap.py
    .\\scripts\\start_brain.ps1

Useful flags:

    python bootstrap.py --skip-mt5          # brain only (Linux/macOS/CI)
    python bootstrap.py --skip-smoke        # setup only
    python bootstrap.py --terminal-data "C:\\...\\Terminal\\<id>"
    python bootstrap.py --metaeditor "C:\\...\\MetaEditor64.exe"
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
IS_WINDOWS = os.name == "nt"

OK = "[OK]"
WARN = "[WARN]"
FAIL = "[FAIL]"


def log(message: str) -> None:
    print(f"[BOOTSTRAP] {message}", flush=True)


def die(message: str) -> "NoReturn":
    print(f"{FAIL} {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def run(
    command: list[str],
    *,
    cwd: Path = ROOT,
    timeout: int | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess:
    log("run: " + " ".join(command))
    return subprocess.run(
        command,
        cwd=str(cwd),
        timeout=timeout,
        check=check,
        text=True,
        capture_output=capture,
    )


def ensure_venv() -> Path:
    if sys.version_info < (3, 10):
        die(f"Python 3.10+ required; found {sys.version.split()[0]}")

    python = venv_python()
    if not python.exists():
        log(f"creating virtual environment: {VENV}")
        run([sys.executable, "-m", "venv", str(VENV)], timeout=180)

    if not python.exists():
        die(f"virtual environment interpreter not found: {python}")

    log("installing Python dependencies")
    run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", "requirements.txt"])
    return python


def verify_python(python: Path) -> None:
    code = "import numpy, pandas, zmq; print(f'numpy={numpy.__version__} pandas={pandas.__version__} pyzmq={zmq.__version__}')"
    result = run([str(python), "-c", code], capture=True, timeout=120)
    print(f"{OK} Python dependencies: {result.stdout.strip()}")


def terminal_data_dirs() -> list[Path]:
    if not IS_WINDOWS:
        return []
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


def find_tool(explicit: str | None, data_dir: Path | None, name: str) -> Path | None:
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None

    candidates: list[Path] = []
    if data_dir is not None:
        origin = data_dir / "origin.txt"
        if origin.exists():
            try:
                origin_path = Path(origin.read_text(encoding="utf-8", errors="ignore").strip())
                candidates.append(origin_path / name)
            except OSError:
                pass

    which = shutil.which(name)
    if which:
        candidates.append(Path(which))

    for base in (
        Path(r"C:\Program Files\MetaTrader 5 Terminal"),
        Path(r"C:\Program Files\MetaTrader 5"),
        Path(r"C:\Program Files (x86)\MetaTrader 5"),
    ):
        candidates.append(base / name)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def install_mt5(
    data_dir: Path | None,
    metaeditor: Path | None,
) -> tuple[Path, Path | None]:
    if not IS_WINDOWS:
        die("MT5 installation is Windows-only. Re-run with --skip-mt5 on this OS.")

    if data_dir is None:
        dirs = terminal_data_dirs()
        if not dirs:
            die(
                "No MT5 data directory found. Open MetaTrader 5 once, or pass "
                "--terminal-data, or use --skip-mt5."
            )
        data_dir = dirs[0]
        if len(dirs) > 1:
            log(f"multiple terminals found; using most recently used: {data_dir}")
            for other in dirs:
                log(f"  candidate: {other}")

    if not (data_dir / "MQL5" / "Experts").exists():
        die(f"not a valid MT5 data directory: {data_dir}")

    editor = find_tool(str(metaeditor) if metaeditor else None, data_dir, "MetaEditor64.exe")
    terminal = find_tool(None, data_dir, "terminal64.exe")

    experts_dir = data_dir / "MQL5" / "Experts" / "Sovereign"
    include_dir = data_dir / "MQL5" / "Include" / "Sovereign"
    experts_dir.mkdir(parents=True, exist_ok=True)
    include_dir.mkdir(parents=True, exist_ok=True)

    include_source = ROOT / "EA" / "Include"
    if not include_source.exists():
        die(f"EA include directory missing: {include_source}")
    for source in sorted(include_source.glob("*.mqh")):
        shutil.copy2(source, include_dir / source.name)
        log(f"installed include: {source.name}")

    ea_source = ROOT / "EA" / "SovereignGodsEyeEA.mq5"
    if not ea_source.exists():
        die(f"EA source missing: {ea_source}")
    destination = experts_dir / ea_source.name
    shutil.copy2(ea_source, destination)
    log(f"installed EA source: {destination}")

    if editor is None:
        print(f"{WARN} MetaEditor64.exe not found; copy the EA and compile manually (F7).")
        return data_dir, terminal

    log(f"compiling with {editor}")
    compile_log = Path(tempfile.gettempdir()) / "SovereignGodsEyeEA_bootstrap.log"
    if compile_log.exists():
        compile_log.unlink()

    run(
        [
            str(editor),
            f"/compile:{destination}",
            f"/log:{compile_log}",
            f"/inc:{data_dir / 'MQL5'}",
        ],
        timeout=180,
        check=False,
    )
    time.sleep(1.5)

    if not compile_log.exists():
        die(f"compiler did not produce a log: {compile_log}")

    raw = compile_log.read_bytes()
    text = ""
    for encoding in ("utf-16", "utf-8-sig", "utf-8", "mbcs"):
        try:
            text = raw.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if not text:
        die(f"cannot decode compiler log: {compile_log}")

    for line in text.splitlines():
        if "error" in line.lower() or "warning" in line.lower() or "Result:" in line:
            print(line.strip())

    if "0 errors" not in text:
        die(f"EA compilation failed. Review {compile_log}")

    ex5 = destination.with_suffix(".ex5")
    if not ex5.exists():
        die("compiler reported success but produced no .ex5")
    print(f"{OK} EA compiled: {ex5}")

    if terminal is None:
        print(f"{WARN} terminal64.exe not found; launch MT5 manually.")
    else:
        print(f"{OK} MT5 terminal: {terminal}")

    return data_dir, terminal


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def smoke_test(python: Path, host: str, port: int, tcp_port: int, timeout: float) -> None:
    if not port_is_free(host, port) or not port_is_free(host, tcp_port):
        print(f"{WARN} Port {port} or {tcp_port} is already in use; validating the running feed instead.")
        result = subprocess.run(
            [
                str(python),
                "scripts/healthcheck.py",
                "--transport",
                "both",
                "--count",
                "2",
                "--timeout",
                str(timeout),
            ],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
        )
        print(result.stdout.rstrip())
        if result.returncode != 0:
            print(result.stderr.rstrip(), file=sys.stderr)
            die("existing feed failed health check")
        print(f"{OK} existing feed is healthy")
        return

    log("starting a 12-second brain for an end-to-end feed smoke test")
    brain = subprocess.Popen(
        [
            str(python),
            "python/institutional_quant_master.py",
            "--duration",
            "12",
            "--port",
            str(port),
            "--tcp-port",
            str(tcp_port),
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        time.sleep(2.0)
        if brain.poll() is not None:
            stderr = brain.stderr.read() if brain.stderr else ""
            die(f"brain exited during startup:\n{stderr}")

        result = subprocess.run(
            [
                str(python),
                "scripts/healthcheck.py",
                "--transport",
                "both",
                "--count",
                "2",
                "--timeout",
                str(timeout),
            ],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
        )
        print(result.stdout.rstrip())
        if result.returncode != 0:
            print(result.stderr.rstrip(), file=sys.stderr)
            die("feed smoke test failed")
        print(f"{OK} both feeds passed")

    finally:
        if brain.poll() is None:
            brain.terminate()
            try:
                brain.wait(timeout=5)
            except subprocess.TimeoutExpired:
                brain.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap Project Sovereign")
    parser.add_argument("--skip-mt5", action="store_true", help="Skip MT5 install/compile")
    parser.add_argument("--skip-smoke", action="store_true", help="Skip feed smoke test")
    parser.add_argument("--terminal-data", help="MT5 data directory (contains MQL5)")
    parser.add_argument("--metaeditor", help="Path to MetaEditor64.exe")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5555, help="ZeroMQ PUB port")
    parser.add_argument("--tcp-port", type=int, default=5556, help="Native MQL5 TCP port")
    parser.add_argument("--smoke-timeout", type=float, default=5.0)
    args = parser.parse_args()

    print("=" * 68)
    print(" Project Sovereign - bootstrap")
    print("=" * 68)
    print(" 1. Create/reuse .venv and install Python dependencies")
    print(" 2. Verify imports")
    print(" 3. Install MQL5 includes and compile the EA (Windows)")
    print(" 4. Start a short brain run and validate ZMQ + TCP feeds")
    print("=" * 68)

    python = ensure_venv()
    verify_python(python)

    data_dir: Path | None = Path(args.terminal_data) if args.terminal_data else None
    metaeditor: Path | None = Path(args.metaeditor) if args.metaeditor else None

    if args.skip_mt5:
        print(f"{WARN} MT5 install skipped.")
    else:
        data_dir, _terminal = install_mt5(data_dir, metaeditor)

    if args.skip_smoke:
        print(f"{WARN} feed smoke test skipped.")
    else:
        smoke_test(python, args.host, args.port, args.tcp_port, args.smoke_timeout)

    print("=" * 68)
    print(f"{OK} Bootstrap complete.")
    if data_dir:
        print(f"     MT5 data: {data_dir}")
    print(" Next:")
    print("   1. Start the brain:  .\\scripts\\start_brain.ps1")
    print("   2. Open MT5, enable Algo Trading, attach SovereignGodsEyeEA to EURUSD M5.")
    print("   3. Keep InpRequirePythonSignal=false until the brain consumes real ticks.")
    print("   4. Run a baseline:   python scripts\\run_backtest.py --prepare-only")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
