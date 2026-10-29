"""Verified HTTPS helper shared by the synthetic Merlin scenario.

Gotham's runner starts a real agent binary. Metropolis orchestrates its
declared inert agent through ``merlin_scenario`` instead of impersonating one
from the invoking host.
"""

from __future__ import annotations

import json
import socket
import ssl
from pathlib import Path


def http(
    host: str,
    port: int,
    method: str,
    path: str,
    body: bytes,
    timeout: float,
    ca_file: Path,
) -> dict:
    """Exchange one JSON request with the CNC's verified HTTPS agent channel."""
    context = ssl.create_default_context(cafile=str(ca_file))
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as sock:
            head = (
                f"{method} {path} HTTP/1.0\r\n"
                f"Host: {host}\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode()
            sock.sendall(head + body)
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
    header, separator, payload = b"".join(chunks).partition(b"\r\n\r\n")
    if not separator:
        raise RuntimeError("Merlin CNC returned an incomplete HTTPS response")
    status = int(header.split(b"\r\n", 1)[0].split(b" ")[1])
    if status >= 400:
        raise RuntimeError(f"Merlin CNC returned HTTP {status} for {path}")
    return json.loads(payload) if payload.strip() else {}
