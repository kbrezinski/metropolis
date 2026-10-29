#!/usr/bin/env python3
"""Run one lab RDP desktop session and separately labelled synthetic UDP probes."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import signal
import socket
import subprocess
import threading
import time


UDP_REQUEST = b"METRDP-UDP-PING!"
UDP_REPLY_PREFIX = b"MRDP-ACK"


def sample_udp(host: str) -> bytes | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.settimeout(1.0)
        try:
            connection.sendto(UDP_REQUEST, (host, 3389))
            payload, peer = connection.recvfrom(1024)
        except OSError:
            return None
    return payload if peer[0] == host and len(payload) in {16, 512} else None


def session_marker(payload: bytes | None) -> int | None:
    if payload is None or not payload.startswith(UDP_REPLY_PREFIX):
        return None
    return int.from_bytes(payload[8:16], "big")


def probe_udp(host: str, stop: threading.Event, interval: float = 5.0) -> dict:
    counts = {
        "udp_sent": 0,
        "udp_received": 0,
        "udp_response_bytes": 0,
    }
    while not stop.is_set():
        counts["udp_sent"] += 1
        payload = sample_udp(host)
        if payload is not None:
            counts["udp_received"] += 1
            counts["udp_response_bytes"] += len(payload)
        stop.wait(interval)
    return counts


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def run_session(host: str, duration: float) -> dict:
    username = os.getenv("RDP_USERNAME", "")
    password = os.environ.pop("RDP_PASSWORD", "")
    source = os.getenv("NODE_HOSTNAME", "unknown")
    result = {
        "source": source,
        "target_host": host,
        "status": "failed",
        "tcp_success": False,
        "udp_sent": 0,
        "udp_received": 0,
        "udp_response_bytes": 0,
    }
    if (
        not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", username)
        or not password
        or any(c in password for c in "\r\n\x00")
    ):
        result["reason"] = "credential_configuration"
        return result
    if duration <= 0:
        result["reason"] = "invalid_duration"
        return result
    child_env = os.environ.copy()
    child_env.pop("RDP_PASSWORD", None)
    arguments = (
        "\n".join(
            [
                f"/v:{host}:3389",
                f"/u:{username}",
                f"/p:{password}",
                # Server certificates are regenerated with recreated lab containers.
                # Keep TLS transport, but do not pin these ephemeral identities.
                "/cert:ignore",
                "/size:800x600",
                "/bpp:16",
                "/network:lan",
            ]
        )
        + "\n"
    )
    del password
    process = None
    stop = threading.Event()
    cancelled = threading.Event()
    udp_thread = None
    udp_result: dict = {}
    connected = False
    final_marker = None
    baseline_reply = sample_udp(host)
    baseline_marker = session_marker(baseline_reply)
    result["udp_sent"] = 1
    if baseline_reply is not None:
        result["udp_received"] = 1
        result["udp_response_bytes"] = len(baseline_reply)

    previous_handlers = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, lambda _number, _frame: cancelled.set())

    def collect_udp() -> None:
        udp_result.update(probe_udp(host, stop))

    try:
        process = subprocess.Popen(
            ["xvfb-run", "-a", "xfreerdp3", "/args-from:stdin"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            env=child_env,
            start_new_session=True,
        )
        process.stdin.write(arguments)
        process.stdin.close()
        started = time.monotonic()
        deadline = started + duration
        # A failed credential check disconnects when xrdp requires credentials.
        while time.monotonic() < min(started + 5, deadline) and not cancelled.is_set():
            if process.poll() is not None:
                result["reason"] = "rdp_disconnected"
                return result
            time.sleep(0.2)
        if cancelled.is_set():
            result["reason"] = "interrupted"
            return result
        udp_thread = threading.Thread(target=collect_udp, daemon=True)
        udp_thread.start()
        while time.monotonic() < deadline and not cancelled.is_set():
            if process.poll() is not None:
                result["reason"] = "rdp_disconnected"
                return result
            time.sleep(0.2)
        if cancelled.is_set():
            result["reason"] = "interrupted"
            return result
        connected = process.poll() is None
        if connected:
            final_reply = sample_udp(host)
            result["udp_sent"] += 1
            if final_reply is not None:
                result["udp_received"] += 1
                result["udp_response_bytes"] += len(final_reply)
                final_marker = session_marker(final_reply)
    except OSError as exc:
        result["reason"] = type(exc).__name__
    finally:
        stop.set()
        if udp_thread is not None:
            udp_thread.join(timeout=2)
        for key in ("udp_sent", "udp_received", "udp_response_bytes"):
            result[key] += udp_result.get(key, 0)
        if process is not None:
            _stop_process(process)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    if (
        connected
        and baseline_marker is not None
        and (final_marker or 0) > baseline_marker
    ):
        result["tcp_success"] = True
    elif connected:
        result["reason"] = "authentication_not_observed"
    if result["tcp_success"] and result["udp_received"]:
        result["status"] = "complete"
    elif "reason" not in result:
        result["reason"] = (
            "udp_unanswered" if result["tcp_success"] else "rdp_disconnected"
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--duration", type=float, default=30.0)
    args = parser.parse_args()
    try:
        address = ipaddress.ip_address(args.target_host)
    except ValueError:
        parser.error("target-host must be a private IPv4 address")
    if not isinstance(address, ipaddress.IPv4Address) or not address.is_private:
        parser.error("target-host must be a private IPv4 address")
    result = run_session(str(address), args.duration)
    print(json.dumps(result), flush=True)
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
