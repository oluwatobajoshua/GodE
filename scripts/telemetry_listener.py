#!/usr/bin/env python3
"""Minimal TCP listener on port 5557 for Sovereign EA telemetry uplink.

Accepts NDJSON lines from the MQL5 EA (SendInitSnapshot, SendBarState,
SendTradeEvent) and echoes an ack. Loopback only.
"""

import socket
import threading
import sys
import signal

HOST = "127.0.0.1"
PORT = 5557

_stop = False


def handle_conn(conn, addr):
    conn.settimeout(5.0)
    buf = b""
    try:
        while not _stop:
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                # Echo a minimal ack so the EA knows we received it
                try:
                    conn.sendall(b'{"ack":1}\n')
                except OSError:
                    pass
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def main():
    global _stop
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(8)
    srv.settimeout(1.0)
    print(f"[telemetry] listening on {HOST}:{PORT}", flush=True)

    def handler(signum, frame):
        global _stop
        _stop = True

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    threads = []
    try:
        while not _stop:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            t = threading.Thread(target=handle_conn, args=(conn, addr), daemon=True)
            t.start()
            threads.append(t)
    finally:
        _stop = True
        srv.close()
        for t in threads:
            t.join(timeout=2)
    print("[telemetry] stopped", flush=True)


if __name__ == "__main__":
    main()
