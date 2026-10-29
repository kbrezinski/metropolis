#!/usr/bin/env python3
"""Synthetic UDP 3389 lab responder; this is not RDP-over-UDP."""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import socket
import threading
import time
from pathlib import Path


REQUEST_BYTES = 16
REPLY_PREFIX = b"MRDP-ACK"
NORMAL_REPLY = REPLY_PREFIX + bytes(8)
STATE_FILE = Path(os.getenv("RDP_UDP_STATE_FILE", "/run/metropolis-rdp/udp-mode.json"))
SESSION_FILE = Path(
    os.getenv("RDP_SESSION_MARKER_FILE", "/var/lib/metropolis-rdp/sessions/started_ns")
)
LAB_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")
)


def current_mode(path: Path = STATE_FILE, now: float | None = None) -> str:
    """A missing, damaged, or expired lease always falls back to benign mode."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            return "benign"
        if state.get("mode") != "amplify":
            return "benign"
        expiry = float(state["expires_utc_epoch"])
        if not math.isfinite(expiry) or expiry <= (time.time() if now is None else now):
            return "benign"
    except (OSError, ValueError, TypeError, KeyError):
        return "benign"
    return "amplify"


def set_mode(mode: str, lease_seconds: float | None, path: Path = STATE_FILE) -> dict:
    if mode not in {"benign", "amplify"}:
        raise ValueError("mode must be benign or amplify")
    if mode == "amplify" and (lease_seconds is None or not 0 < lease_seconds <= 86400):
        raise ValueError("amplify mode requires lease-seconds in (0, 86400]")
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "mode": mode,
        "expires_utc_epoch": time.time() + lease_seconds if mode == "amplify" else 0,
    }
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"mode": mode, "verified": current_mode(path) == mode}


def session_marker(path: Path = SESSION_FILE) -> int:
    try:
        marker = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return 0
    return marker if 0 <= marker < 2**64 else 0


def response(payload: bytes, peer: str, mode: str, marker: int = 0) -> bytes | None:
    """Recognize only a 16-byte lab probe from a private or loopback address."""
    if len(payload) != REQUEST_BYTES:
        return None
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return None
    if not isinstance(address, ipaddress.IPv4Address) or not any(
        address in network for network in LAB_NETWORKS
    ):
        return None
    reply = REPLY_PREFIX + marker.to_bytes(8, "big")
    return reply + bytes(512 - len(reply)) if mode == "amplify" else reply


def serve(
    host: str,
    port: int,
    path: Path = STATE_FILE,
    stop: threading.Event | None = None,
    ready: threading.Event | None = None,
) -> None:
    stop = stop or threading.Event()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind((host, port))
        listener.settimeout(0.2)
        if ready is not None:
            ready.set()
        print(
            json.dumps({"event": "rdp_udp_started", "port": listener.getsockname()[1]}),
            flush=True,
        )
        while not stop.is_set():
            try:
                payload, sender = listener.recvfrom(2048)
            except socket.timeout:
                continue
            reply = response(payload, sender[0], current_mode(path), session_marker())
            if reply is not None:
                listener.sendto(reply, sender)


def check_ready(
    host: str = "127.0.0.1", tcp_port: int = 3389, udp_port: int = 3389
) -> dict:
    try:
        with socket.create_connection((host, tcp_port), timeout=0.5):
            tcp_ready = True
    except OSError:
        tcp_ready = False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(0.5)
            probe.sendto(b"METRDP-UDP-PING!", (host, udp_port))
            answer, peer = probe.recvfrom(1024)
        udp_ready = (
            peer[0] == host
            and answer.startswith(REPLY_PREFIX)
            and len(answer) in {16, 512}
        )
    except OSError:
        udp_ready = False
    return {"tcp_ready": tcp_ready, "udp_ready": udp_ready}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("serve")
    subcommands.add_parser("ready")
    mode_parser = subcommands.add_parser("set-mode")
    mode_parser.add_argument("--mode", choices=("benign", "amplify"), required=True)
    mode_parser.add_argument("--lease-seconds", type=float)
    args = parser.parse_args()
    if args.command == "serve":
        serve("0.0.0.0", int(os.getenv("RDP_UDP_PORT", "3389")))
        return 0
    if args.command == "ready":
        result = check_ready(udp_port=int(os.getenv("RDP_UDP_PORT", "3389")))
        print(json.dumps(result), flush=True)
        return 0 if all(result.values()) else 1
    try:
        result = set_mode(args.mode, args.lease_seconds)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result), flush=True)
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
