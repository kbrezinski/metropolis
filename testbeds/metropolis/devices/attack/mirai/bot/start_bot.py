#!/usr/bin/env python3
"""Synthetic Mirai bot agent.

Mirrors the Gotham ``iotsim-mirai-bot`` node: the infected host that checks in
with the command-and-control console, probes for further targets, and reports
results to the scan listener.

**This is an inert lab simulation, not malware.** It opens TCP connections and
sends JSON control frames. It does not download or execute anything, does not
fork bomb, and does not carry a DDoS payload. The loader's "payload" is a text
marker. Replacing this with a real bot binary is a deliberate, separate step
for a fully isolated testbed.

Targets are restricted to an explicit allow-list. With no targets configured
the bot stays dormant and only reports its check-in.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
import time
from datetime import datetime, timezone

LOG = logging.getLogger("mirai.bot")

# Bound the allow-list and the probe rate so the lab stays predictable.
MAX_TARGETS = 256
MIN_INTERVAL = 5.0
DEFAULT_INTERVAL = 30.0


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


def parse_targets(value: str) -> list[tuple[str, int]]:
    """Parse ``ip``, ``ip:port`` or ``ip-port`` entries into (host, port) pairs."""
    targets: list[tuple[str, int]] = []
    for entry in (item.strip() for item in value.split(",")):
        if not entry:
            continue
        host, _, port_text = entry.partition(":")
        address = ipaddress.IPv4Address(host)
        if not (address.is_private or address.is_loopback):
            raise ValueError(f"{host} is outside the private lab network")
        port = int(port_text) if port_text else 23
        if not 1 <= port <= 65535:
            raise ValueError(f"{entry} has an invalid port")
        targets.append((str(address), port))
    if len(targets) > MAX_TARGETS:
        raise ValueError(f"At most {MAX_TARGETS} targets are supported")
    return targets


def check_in(cnc_host: str, cnc_port: int, arch: str, timeout: float) -> str | None:
    """Register with the CNC and return the assigned bot id."""
    try:
        with socket.create_connection((cnc_host, cnc_port), timeout=timeout) as sock:
            sock.sendall(f"CHECKIN {local_address(cnc_host)} {arch}\n".encode())
            reply = sock.recv(256).decode("utf-8", errors="replace").strip()
    except OSError as exc:
        LOG.warning("check-in failed: %s", exc)
        return None
    emit("checkin", cnc=f"{cnc_host}:{cnc_port}", reply=reply)
    return reply.split(" ", 1)[1] if reply.startswith("ACK ") else None


def local_address(peer: str) -> str:
    """Local address used to reach ``peer``; falls back to the hostname."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((peer, 9))
            return probe.getsockname()[0]
    except OSError:
        return socket.gethostname()


def probe(host: str, port: int, timeout: float) -> bool:
    """Single bounded TCP connect; the simulation's stand-in for a scan."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def report(
    listener_host: str, listener_port: int, payload: dict, timeout: float
) -> None:
    """Send one infection report to the scan listener."""
    try:
        with socket.create_connection(
            (listener_host, listener_port), timeout=timeout
        ) as sock:
            sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
    except OSError as exc:
        LOG.warning("report failed: %s", exc)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    arch = os.getenv("BOT_ARCH", "linux-x64-synthetic")
    cnc_host = os.getenv("MIRAI_CNC_HOST", "").strip()
    if not cnc_host:
        raise SystemExit("MIRAI_CNC_HOST is required")
    cnc_port = int(os.getenv("MIRAI_CNC_PORT", "48101"))
    listener_host = os.getenv("MIRAI_LISTENER_HOST", cnc_host).strip()
    listener_port = int(os.getenv("MIRAI_REPORT_PORT", "48101"))
    timeout = float(os.getenv("BOT_TIMEOUT", "3"))
    interval = max(MIN_INTERVAL, float(os.getenv("BOT_INTERVAL", DEFAULT_INTERVAL)))
    targets = parse_targets(os.getenv("BOT_TARGETS", ""))

    bot_id = check_in(cnc_host, cnc_port, arch, timeout)
    emit(
        "bot_started",
        bot_id=bot_id,
        arch=arch,
        targets=[f"{h}:{p}" for h, p in targets],
    )
    if not targets:
        emit("note", evidence="No BOT_TARGETS configured; bot remains dormant")
        return

    while True:
        for host, port in targets:
            reachable = probe(host, port, timeout)
            emit("probe", target=f"{host}:{port}", reachable=reachable)
            if reachable:
                report(
                    listener_host,
                    listener_port,
                    {
                        "bot_id": bot_id,
                        "arch": arch,
                        "vulnerable": f"{host}:{port}",
                        "method": "synthetic-tcp-connect",
                    },
                    timeout,
                )
        time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
