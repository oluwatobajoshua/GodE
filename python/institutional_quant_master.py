#!/usr/bin/env python3
"""Project Sovereign - Python analytics brain.

Publishes one JSON object per signal on two transports:

* ZeroMQ PUB on tcp://127.0.0.1:5555  (external consumers / dashboards)
* Localhost TCP NDJSON on 127.0.0.1:5556 (native MQL5 client, no DLL)

The TCP bridge deliberately binds to loopback only. The MQL5 EA connects to it
with SocketConnect(), so no third-party DLL and no inbound firewall rule is
required when both processes run on the same machine/VPS.

The current market source is a deterministic synthetic random walk. Every
payload is tagged with ``"source": "synthetic"`` so it cannot be mistaken for a
live broker feed. Replace ``build_payload()`` with a real MT5/CSV feed before
enabling ``InpRequirePythonSignal`` on a funded account.
"""

from __future__ import annotations

import argparse
import json
import queue
import signal
import socketserver
import threading
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import zmq


class TcpSignalBridge:
    """Loopback-only NDJSON broadcast server for the native MQL5 client."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5556) -> None:
        self.host = host
        self.port = port
        self._lock = threading.Lock()
        self._queues: set[queue.Queue] = set()
        self._stopping = False

        bridge = self

        class _Handler(socketserver.BaseRequestHandler):
            def setup(self) -> None:
                self.request.settimeout(2.0)
                self.queue = bridge.subscribe()

            def handle(self) -> None:
                while True:
                    try:
                        line = self.queue.get(timeout=1.0)
                    except queue.Empty:
                        if bridge._stopping:
                            break
                        continue

                    if line is None:
                        break
                    try:
                        self.request.sendall(line)
                    except OSError:
                        break

            def finish(self) -> None:
                bridge.unsubscribe(self.queue)

        class _Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._server = _Server((host, port), _Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="sovereign-tcp-bridge",
            daemon=True,
        )
        self._thread.start()

    def subscribe(self) -> queue.Queue:
        client_queue: queue.Queue = queue.Queue(maxsize=64)
        with self._lock:
            self._queues.add(client_queue)
        print(f"[SOVEREIGN BRAIN] TCP client connected ({self.client_count} active).")
        return client_queue

    def unsubscribe(self, client_queue: queue.Queue) -> None:
        with self._lock:
            self._queues.discard(client_queue)
        print(f"[SOVEREIGN BRAIN] TCP client disconnected ({self.client_count} active).")

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._queues)

    def broadcast(self, payload: dict) -> None:
        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            targets = list(self._queues)

        for client_queue in targets:
            try:
                client_queue.put_nowait(line)
            except queue.Full:
                # Slow client: drop the oldest frame, then retry once.
                try:
                    client_queue.get_nowait()
                    client_queue.put_nowait(line)
                except (queue.Empty, queue.Full):
                    pass

    def close(self) -> None:
        self._stopping = True
        with self._lock:
            targets = list(self._queues)
            self._queues.clear()
        for client_queue in targets:
            try:
                client_queue.put_nowait(None)
            except queue.Full:
                pass

        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=3.0)
        print("[SOVEREIGN BRAIN] TCP bridge closed.")


class SovereignQuantEngine:
    def __init__(
        self,
        port: int = 5555,
        symbol: str = "EURUSD",
        interval: float = 0.5,
        tcp_port: int = 5556,
        tcp_host: str = "127.0.0.1",
        seed: int = 42,
        duration: float = 0.0,
    ) -> None:
        self.port = port
        self.symbol = symbol
        self.interval = max(0.05, float(interval))
        self.duration = max(0.0, float(duration))

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUB)
        self.socket.bind(f"tcp://127.0.0.1:{port}")
        print(
            f"[SOVEREIGN BRAIN] ZeroMQ PUB on tcp://127.0.0.1:{port}; "
            f"symbol={symbol}; interval={self.interval:.2f}s"
        )

        self.bridge: TcpSignalBridge | None = None
        if tcp_port > 0:
            try:
                self.bridge = TcpSignalBridge(tcp_host, tcp_port)
                print(f"[SOVEREIGN BRAIN] TCP NDJSON bridge on tcp://{tcp_host}:{tcp_port}")
            except OSError as exc:
                self.socket.close(linger=0)
                self.context.term()
                raise SystemExit(
                    f"[FATAL] Cannot bind TCP bridge on {tcp_host}:{tcp_port}: {exc}"
                ) from exc

        self._rng = np.random.default_rng(seed)
        self._bars: list[list[float]] = []
        self._seed_history(120)

    def _next_bar(self) -> list[float]:
        previous_close = self._bars[-1][3] if self._bars else 1.0850
        drift = 0.00002 * np.sin(len(self._bars) / 25.0)
        close = max(0.5, previous_close + drift + self._rng.normal(0, 0.00025))
        wick = abs(self._rng.normal(0, 0.00008))
        high = max(previous_close, close) + wick
        low = min(previous_close, close) - wick
        volume = int(self._rng.integers(500, 2500))
        return [previous_close, high, low, close, volume]

    def _seed_history(self, count: int) -> None:
        for _ in range(max(60, count)):
            self._bars.append(self._next_bar())
        if len(self._bars) > 240:
            del self._bars[: len(self._bars) - 240]

    def close(self) -> None:
        if self.bridge is not None:
            self.bridge.close()
            self.bridge = None
        self.socket.close(linger=0)
        self.context.term()

    def calculate_volume_profile_poc(self, df: pd.DataFrame) -> float:
        price_bins = pd.cut(df["close"], bins=20)
        volume_profile = df.groupby(price_bins, observed=False)["volume"].sum()
        if volume_profile.empty:
            return float(df["close"].iloc[-1])
        return float(volume_profile.idxmax().mid)

    def evaluate_institutional_features(
        self, df: pd.DataFrame
    ) -> tuple[str, float, float, float, float]:
        df["tr"] = np.maximum(
            df["high"] - df["low"],
            np.maximum(
                np.abs(df["high"] - df["close"].shift()),
                np.abs(df["low"] - df["close"].shift()),
            ),
        )
        atr_short = df["tr"].rolling(window=5).mean().iloc[-1]
        atr_long = df["tr"].rolling(window=20).mean().iloc[-1]
        volatility_ratio = float(atr_short / atr_long) if atr_long > 0 else 1.0

        rolling_high = float(df["high"].rolling(window=50).max().iloc[-1])
        rolling_low = float(df["low"].rolling(window=50).min().iloc[-1])
        current_price = float(df["close"].iloc[-1])
        total_range = rolling_high - rolling_low
        range_pos = (current_price - rolling_low) / total_range if total_range > 0 else 0.5

        poc = self.calculate_volume_profile_poc(df)

        action = "HOLD"
        confidence = 0.0
        if range_pos <= 0.20 and current_price >= poc:
            action = "BUY"
            confidence = 0.94
        elif range_pos >= 0.80 and current_price <= poc:
            action = "SELL"
            confidence = 0.94

        return action, confidence, float(range_pos), volatility_ratio, poc

    def build_payload(self) -> dict:
        self._bars.append(self._next_bar())
        if len(self._bars) > 240:
            del self._bars[0]

        df = pd.DataFrame(
            self._bars,
            columns=["open", "high", "low", "close", "volume"],
        )
        action, confidence, pos, vol_ratio, poc = self.evaluate_institutional_features(df)

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": self.symbol,
            "action": action,
            "confidence": round(confidence, 4),
            "range_position": round(pos, 4),
            "volatility_ratio": round(vol_ratio, 3),
            "poc": round(poc, 5),
            "heartbeat": True,
            "source": "synthetic",
        }

    def run_sovereign_loop(self) -> None:
        started = time.monotonic()
        try:
            while True:
                if self.duration > 0 and (time.monotonic() - started) >= self.duration:
                    print("[SOVEREIGN BRAIN] Duration reached; stopping cleanly.")
                    return

                payload = self.build_payload()
                self.socket.send_string(json.dumps(payload))
                if self.bridge is not None:
                    self.bridge.broadcast(payload)

                print(
                    f"[STREAM] {payload['action']} conf={payload['confidence']:.2f} "
                    f"vol={payload['volatility_ratio']:.2f} "
                    f"poc={payload['poc']:.5f} tcp_clients={self.bridge.client_count if self.bridge else 0}"
                )
                time.sleep(self.interval)
        finally:
            self.close()


def _install_signal_handlers(engine: SovereignQuantEngine) -> None:
    def _stop(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)


def main() -> int:
    parser = argparse.ArgumentParser(description="Project Sovereign Python analytics brain")
    parser.add_argument("--port", type=int, default=5555, help="ZeroMQ PUB port (default: 5555)")
    parser.add_argument("--symbol", default="EURUSD", help="Symbol tag emitted in payloads")
    parser.add_argument("--interval", type=float, default=0.5, help="Seconds between payloads")
    parser.add_argument("--tcp-port", type=int, default=5556, help="Native MQL5 NDJSON port; 0 disables")
    parser.add_argument("--tcp-host", default="127.0.0.1", help="TCP bind address (loopback by default)")
    parser.add_argument("--seed", type=int, default=42, help="Synthetic data seed")
    parser.add_argument("--duration", type=float, default=0.0, help="Run seconds; 0 = forever")
    args = parser.parse_args()

    engine = SovereignQuantEngine(
        port=args.port,
        symbol=args.symbol,
        interval=args.interval,
        tcp_port=args.tcp_port,
        tcp_host=args.tcp_host,
        seed=args.seed,
        duration=args.duration,
    )
    _install_signal_handlers(engine)

    try:
        engine.run_sovereign_loop()
    except KeyboardInterrupt:
        print("\n[SOVEREIGN BRAIN] Shutdown requested.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
