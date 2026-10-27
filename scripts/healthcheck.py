#!/usr/bin/env python3
"""Project Sovereign - feed health check.

Verifies that the Python brain is publishing well-formed payloads on either:

* ``zmq``  - the ZeroMQ PUB socket (default port 5555)
* ``tcp``  - the native MQL5 NDJSON bridge (default port 5556)
* ``both`` - both transports

Usage:
    python scripts/healthcheck.py
    python scripts/healthcheck.py --transport tcp
    python scripts/healthcheck.py --transport zmq --count 5
"""

from __future__ import annotations

import argparse
import json
import socket
import sys

import zmq

REQUIRED_KEYS = {
    "timestamp": str,
    "symbol": str,
    "action": str,
    "confidence": (int, float),
    "range_position": (int, float),
    "volatility_ratio": (int, float),
    "poc": (int, float),
    "heartbeat": bool,
}


def validate_payload(payload: dict) -> list[str]:
    problems: list[str] = []
    for key in REQUIRED_KEYS:
        if key not in payload:
            problems.append(f"missing '{key}'")
            continue

        value = payload[key]
        if key == "heartbeat":
            valid = isinstance(value, bool)
        elif key in ("timestamp", "symbol", "action"):
            valid = isinstance(value, str) and bool(value.strip())
        else:
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)

        if not valid:
            problems.append(f"'{key}' has invalid value {value!r}")
    return problems


def report_payload(index: int, payload: dict, problems: list[str]) -> bool:
    if problems:
        print(f"[FAIL] Payload #{index} invalid: {', '.join(problems)}")
        return False

    print(
        f"[OK] #{index} {payload['timestamp']} {payload['symbol']} "
        f"{payload['action']} conf={payload['confidence']} "
        f"poc={payload['poc']} source={payload.get('source', 'unknown')}"
    )
    return True


def check_zmq(host: str, port: int, count: int, timeout: float) -> bool:
    context = zmq.Context()
    socket_obj = context.socket(zmq.SUB)
    socket_obj.setsockopt(zmq.SUBSCRIBE, b"")
    socket_obj.setsockopt(zmq.RCVTIMEO, int(timeout * 1000))
    socket_obj.setsockopt(zmq.LINGER, 0)

    endpoint = f"tcp://{host}:{port}"
    socket_obj.connect(endpoint)
    print(f"[HEALTHCHECK:ZMQ] Subscribed to {endpoint}; waiting for {count} payloads...")

    failures = 0
    try:
        for index in range(1, count + 1):
            try:
                raw = socket_obj.recv_string()
            except zmq.Again:
                print(f"[FAIL:ZMQ] Timed out after {timeout:.1f}s waiting for payload #{index}.")
                return False

            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(f"[FAIL:ZMQ] Payload #{index} is not valid JSON: {exc}")
                return False

            if not report_payload(index, payload, validate_payload(payload)):
                failures += 1
    finally:
        socket_obj.close(0)
        context.term()

    if failures:
        print(f"[FAIL:ZMQ] {failures}/{count} payloads failed validation.")
        return False

    print(f"[PASS:ZMQ] Received {count} valid payload(s) from {endpoint}.")
    return True


def check_tcp(host: str, port: int, count: int, timeout: float) -> bool:
    endpoint = f"{host}:{port}"
    print(f"[HEALTHCHECK:TCP] Connecting to {endpoint}; waiting for {count} payloads...")

    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            stream = sock.makefile("rb")
            failures = 0
            for index in range(1, count + 1):
                try:
                    raw = stream.readline()
                except (TimeoutError, socket.timeout):
                    print(f"[FAIL:TCP] Timed out after {timeout:.1f}s waiting for payload #{index}.")
                    return False

                if not raw:
                    print(f"[FAIL:TCP] Server closed the connection before payload #{index}.")
                    return False

                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    print(f"[FAIL:TCP] Payload #{index} is not valid NDJSON: {exc}")
                    return False

                if not report_payload(index, payload, validate_payload(payload)):
                    failures += 1
    except OSError as exc:
        print(f"[FAIL:TCP] Cannot connect to {endpoint}: {exc}")
        return False

    if failures:
        print(f"[FAIL:TCP] {failures}/{count} payloads failed validation.")
        return False

    print(f"[PASS:TCP] Received {count} valid payload(s) from {endpoint}.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the Sovereign feeds")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5555, help="ZeroMQ PUB port")
    parser.add_argument("--tcp-port", type=int, default=5556, help="Native TCP NDJSON port")
    parser.add_argument(
        "--transport",
        choices=("zmq", "tcp", "both"),
        default="both",
        help="Transport(s) to verify (default: both)",
    )
    parser.add_argument("--count", type=int, default=3, help="Number of payloads to validate per transport")
    parser.add_argument("--timeout", type=float, default=5.0, help="Seconds to wait per payload")
    args = parser.parse_args()

    results: list[bool] = []
    if args.transport in ("zmq", "both"):
        results.append(check_zmq(args.host, args.port, args.count, args.timeout))
    if args.transport in ("tcp", "both"):
        results.append(check_tcp(args.host, args.tcp_port, args.count, args.timeout))

    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
