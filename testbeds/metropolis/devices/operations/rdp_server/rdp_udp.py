#!/usr/bin/env python3
"""Synthetic UDP 3389 lab responder; this is not RDP-over-UDP."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import threading
import time
from pathlib import Path


REQUEST_BYTES = 16
NORMAL_REPLY = b"METRDP-UDP-PONG!"
AMPLIFIED_REPLY = NORMAL_REPLY + bytes(512 - len(NORMAL_REPLY))
STATE_FILE = Path(os.getenv("RDP_UDP_STATE_FILE", "/run/metropolis-rdp/udp-mode.json"))
LAB_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")
)


def current_mode(path: Path = STATE_FILE, now: float | None = None) -> str:
    """A missing, damaged, or expired lease always falls back to benign mode."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("mode") != "amplify":
            return "benign"
        if float(state["expires_monotonic"]) <= (time.monotonic() if now is None else now):
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
        "expires_monotonic": time.monotonic() + lease_seconds
        if mode == "amplify"
        else 0,
    }
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"mode": mode, "verified": current_mode(path) == mode}


def response(payload: bytes, peer: str, mode: str) -> bytes | None:
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
    return AMPLIFIED_REPLY if mode == "amplify" else NORMAL_REPLY


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
        print(json.dumps({"event": "rdp_udp_started", "port": listener.getsockname()[1]}), flush=True)
        while not stop.is_set():
            try:
                payload, sender = listener.recvfrom(2048)
            except socket.timeout:
                continue
            reply = response(payload, sender[0], current_mode(path))
            if reply is not None:
                listener.sendto(reply, sender)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("serve")
    mode_parser = subcommands.add_parser("set-mode")
    mode_parser.add_argument("--mode", choices=("benign", "amplify"), required=True)
    mode_parser.add_argument("--lease-seconds", type=float)
    args = parser.parse_args()
    if args.command == "serve":
        serve("0.0.0.0", int(os.getenv("RDP_UDP_PORT", "3389")))
        return 0
    try:
        result = set_mode(args.mode, args.lease_seconds)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result), flush=True)
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
