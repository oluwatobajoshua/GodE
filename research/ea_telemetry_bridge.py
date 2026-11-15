#!/usr/bin/env python3
"""
Project Sovereign — EA Telemetry Bridge (uplink listener)

Listens on port 5557 for EA -> Python telemetry (trade events, bar state,
init snapshots). Logs everything to CSV + NDJSON for realtime oversight.

The Python -> EA downlink (commands: PAUSE, RESUME, FLAT_ALL, RISK_SCALE,
RESET) is handled by institutional_quant_master.py on port 5556.

Usage:
    .\.venv\Scripts\python.exe research\ea_telemetry_bridge.py
"""

from __future__ import annotations

import csv
import json
import socket
import socketserver
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "research" / "results"
RESULTS.mkdir(parents=True, exist_ok=True)

LOG_CSV = RESULTS / "ea_telemetry_log.csv"
LOG_NDJSON = RESULTS / "ea_telemetry_log.ndjson"

UPLINK_PORT = 5557   # EA -> Python listens here


class TelemetryLogger:
    """Appends every EA uplink message to CSV + NDJSON for oversight."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._csv_fields = None

    def log(self, msg_type: str, payload: dict, direction: str = "EA->PY") -> None:
        row = {
            "time_utc": datetime.now(timezone.utc).isoformat(),
            "direction": direction,
            "msg_type": msg_type,
        }
        row.update(payload)

        with self._lock:
            with open(LOG_NDJSON, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")

            fields = list(row.keys())
            write_header = self._csv_fields is None
            if write_header:
                self._csv_fields = fields
            with open(LOG_CSV, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self._csv_fields, extrasaction="ignore")
                if write_header:
                    writer.writeheader()
                writer.writerow(row)

    def print_summary(self, msg_type: str, payload: dict) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        if msg_type == "init":
            print(f"[{ts}] EA INIT  {payload.get('symbol')} strat={payload.get('strategy')} "
                  f"rsi_band={payload.get('rsi_band_enabled')} deadband={payload.get('deadband')} "
                  f"risk={payload.get('risk_pct')}% maxDD={payload.get('max_daily_dd')}% "
                  f"spread={payload.get('max_spread')} algo={payload.get('algo_trading_allowed')}")
        elif msg_type == "bar":
            print(f"[{ts}] BAR {payload.get('symbol')} trend={payload.get('trend')} "
                  f"rsi={payload.get('rsi')} inPos={payload.get('in_position')} "
                  f"side={payload.get('position_side')} barsHeld={payload.get('bars_held')} "
                  f"equity={payload.get('equity')} allowEntries={payload.get('allow_entries')}")
        elif msg_type == "trade":
            print(f"[{ts}] TRADE {payload.get('action')} {payload.get('symbol')} side={payload.get('side')} "
                  f"lots={payload.get('lots')} price={payload.get('price')} retcode={payload.get('retcode')} "
                  f"profit={payload.get('profit')} ticket={payload.get('ticket')}")


logger = TelemetryLogger()


# ---------------------------------------------------------------------------
# Uplink server: EA connects here on port 5557 and sends NDJSON messages
# ---------------------------------------------------------------------------

class UplinkHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        print(f"[UPLINK] EA connected from {self.client_address}")
        self.request.settimeout(2.0)
        buf = ""
        while True:
            try:
                data = self.request.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            buf += data.decode("utf-8", errors="replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg_type = payload.get("t", "unknown")
                logger.log(msg_type, payload, "EA->PY")
                logger.print_summary(msg_type, payload)
        print(f"[UPLINK] EA disconnected from {self.client_address}")


class UplinkServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    print("[SOVEREIGN TELEMETRY BRIDGE] Starting...")
    print(f"[UPLINK]   Listening on port {UPLINK_PORT} for EA -> Python telemetry")
    print(f"[DOWNLINK] Commands handled by institutional_quant_master.py on port 5556")
    print(f"[LOG]      CSV: {LOG_CSV}")
    print(f"[LOG]      NDJSON: {LOG_NDJSON}")
    print()

    uplink = UplinkServer(("127.0.0.1", UPLINK_PORT), UplinkHandler)
    uplink_thread = threading.Thread(target=uplink.serve_forever, name="uplink", daemon=True)
    uplink_thread.start()

    print("[BRIDGE] Running. Start the EA, then watch telemetry above. Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[BRIDGE] Shutdown requested.")
    finally:
        uplink.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
