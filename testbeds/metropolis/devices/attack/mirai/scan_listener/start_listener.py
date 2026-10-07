#!/usr/bin/env python3
"""Synthetic Mirai scan listener.

Mirrors the Gotham ``iotsim-mirai-scan-listener`` node: the service a bot
reports infection results to. It accepts newline-delimited JSON reports over
TCP and writes them to stdout so a capture or the GNS3 console can observe the
lifecycle traffic.

This is a lab simulation. It performs no scanning, holds no bot binary, and
never acts on a reported host.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
from datetime import datetime, timezone

LOG = logging.getLogger("mirai.scan_listener")

MAX_LINE = 4096
# Bound concurrent reporters so a runaway bot cannot exhaust the node.
MAX_REPORTERS = 64


def handle(
    connection: socket.socket, peer, counter: dict, lock: threading.Lock
) -> None:
    """Read newline-delimited vulnerability reports from one bot."""
    with lock:
        if counter["active"] >= MAX_REPORTERS:
            connection.close()
            return
        counter["active"] += 1
    try:
        buffer = b""
        while True:
            try:
                chunk = connection.recv(1024)
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                record(line.decode("utf-8", errors="replace"), peer)
            if len(buffer) > MAX_LINE:
                LOG.warning("oversized report from %s discarded", peer[0])
                buffer = b""
    finally:
        with lock:
            counter["active"] -= 1


def record(line: str, peer) -> None:
    """Emit one report as a structured event on stdout."""
    line = line.strip()
    if not line:
        return
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        LOG.warning("non-JSON report from %s: %s", peer[0], line[:120])
        return
    print(
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "bot_report",
                "source": peer[0],
                "payload": payload,
            },
            sort_keys=True,
        ),
        flush=True,
    )


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    host = os.getenv("MIRAI_LISTENER_BIND", "0.0.0.0")
    port = int(os.getenv("MIRAI_REPORT_PORT", "48101"))
    counter = {"active": 0}
    lock = threading.Lock()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(64)
        LOG.info("scan listener accepting bot reports on %s:%d", host, port)
        while True:
            connection, peer = server.accept()
            threading.Thread(
                target=handle,
                args=(connection, peer, counter, lock),
                daemon=True,
            ).start()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
