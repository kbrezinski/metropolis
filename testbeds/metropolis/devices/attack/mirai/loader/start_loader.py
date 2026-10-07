#!/usr/bin/env python3
"""Synthetic Mirai loader.

Mirrors the Gotham ``iotsim-mirai-loader`` and ``iotsim-mirai-wget-loader``
nodes: the delivery stage a reporting bot fetches its payload from. This
simulation serves a fixed, inert stub over HTTP and records every request, so
the delivery choreography appears in captures without any malware binary
existing in this repository.

Serve one payload kind per node with ``LOADER_KIND``:

    wget   plain HTTP fetch, mirroring the wget loader
    tftp   a stub TFTP read request handler, mirroring the tftp loader
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
from datetime import datetime, timezone

LOG = logging.getLogger("mirai.loader")

# The stub is deliberately inert: a marker string, never executable content.
STUB = b"metropolis-synthetic-payload-stub\n"
MAX_REQUEST = 4096
MAX_LINE = 1024


def emit(event: str, **fields) -> None:
    """Emit one structured event on stdout for capture correlation."""
    print(
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event,
                **fields,
            },
            sort_keys=True,
        ),
        flush=True,
    )


def serve_http(host: str, port: int) -> None:
    """Serve the inert stub over HTTP to any requesting bot."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(16)
        LOG.info("HTTP loader serving the inert stub on %s:%d", host, port)
        while True:
            connection, peer = server.accept()
            threading.Thread(
                target=handle_http, args=(connection, peer), daemon=True
            ).start()


def handle_http(connection: socket.socket, peer) -> None:
    try:
        request = connection.recv(MAX_REQUEST)
        if not request:
            return
        path = request.split(b" ", 2)[1].decode("ascii", errors="replace")
        emit("payload_request", source=peer[0], path=path, protocol="http")
        connection.sendall(
            b"HTTP/1.0 200 OK\r\n"
            b"Content-Type: application/octet-stream\r\n"
            b"Content-Length: %d\r\n"
            b"Connection: close\r\n\r\n" % len(STUB) + STUB
        )
    except (OSError, IndexError):
        return
    finally:
        connection.close()


def serve_tftp(host: str, port: int) -> None:
    """Answer stub TFTP read requests so the tftp loader path is observable."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind((host, port))
        LOG.info("TFTP loader answering reads on %s:%d", host, port)
        while True:
            try:
                packet, peer = server.recvfrom(MAX_REQUEST)
            except OSError:
                return
            if len(packet) < 4 or packet[:2] != b"\x00\x01":
                continue
            filename = packet[2:].split(b"\x00", 1)[0].decode("ascii", errors="replace")
            emit("payload_request", source=peer[0], path=filename, protocol="tftp")
            server.sendto(
                b"\x00\x03\x00\x01" + STUB[:512],
                peer,
            )


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    kind = os.getenv("LOADER_KIND", "wget").strip().lower()
    if kind not in {"wget", "tftp"}:
        raise SystemExit("LOADER_KIND must be 'wget' or 'tftp'")
    host = os.getenv("LOADER_BIND", "0.0.0.0")
    default_port = 80 if kind == "wget" else 69
    port = int(os.getenv("LOADER_PORT", str(default_port)))
    if kind == "wget":
        serve_http(host, port)
    else:
        serve_tftp(host, port)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
