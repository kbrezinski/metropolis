#!/usr/bin/env python3
# Adapted from Gotham's Merlin agent workflow: https://github.com/xsaga/gotham-iot-testbed/blob/master/src/run_merlin.py
"""Synthetic Merlin agent.

Mirrors Gotham's Merlin agent role: check in over HTTPS, poll for commands,
receive a tool stand-in from the CNC, and report an allowlisted chmod result.

**This is an inert lab simulation, not malware.** No real agent binary exists
in this repository. Commands are executed only if they match the allow-list
below. Only chmod of the inert marker changes a local file; no shell runs.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path

LOG = logging.getLogger("merlin.agent")

# Only these command verbs are simulated. Anything else is refused and
# reported as such, mirroring a real agent that cannot run unknown commands.
ALLOWED_VERBS = {"chmod", "echo", "id", "uname", "upload", "sleep"}
MIN_SLEEP = 1.0
INERT_MARKER = b"metropolis-inert-hping3-marker\n"
INERT_SOURCE = "/opt/metropolis/inert-hping3.txt"
INERT_DESTINATION = Path(
    os.getenv("MERLIN_MARKER_PATH", "/tmp/metropolis-merlin/hping3.marker")
)


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


def parse_url(raw: str) -> tuple[str, int]:
    """Parse a CNC address into a (host, port) pair."""
    text = raw.strip()
    for scheme in ("http://", "https://"):
        if text.startswith(scheme):
            text = text[len(scheme) :]
    text = text.split("/", 1)[0]
    host, _, port_text = text.partition(":")
    if not host:
        raise ValueError("Agent URL needs a host")
    return host, int(port_text or "8443")


def request(
    host: str, port: int, method: str, path: str, body: bytes, timeout: float
) -> dict:
    """Small JSON HTTPS client for the synthetic agent channel."""
    status, payload = exchange(host, port, method, path, body, timeout)
    if status >= 400:
        raise ValueError(f"CNC returned HTTP {status} for {path}")
    if not payload.strip():
        return {}
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {"raw": payload.decode("utf-8", errors="replace")}


def exchange(
    host: str, port: int, method: str, path: str, body: bytes, timeout: float
) -> tuple[int, bytes]:
    """Make one certificate-verified HTTPS request and return status and body."""
    ca_file = os.getenv("MERLIN_CA_FILE", "/opt/metropolis/tls/ca.crt")
    context = ssl.create_default_context(cafile=ca_file)
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
    raw_response = b"".join(chunks)
    header, separator, payload = raw_response.partition(b"\r\n\r\n")
    if not separator:
        raise ValueError("CNC returned an incomplete HTTP response")
    status_line = header.split(b"\r\n", 1)[0].split(b" ")
    if len(status_line) < 2:
        raise ValueError("CNC returned an invalid HTTP status")
    return int(status_line[1]), payload


def receive_artifact(
    host: str,
    port: int,
    agent_id: str,
    timeout: float,
    destination: Path = INERT_DESTINATION,
) -> bool:
    """Store only the built-in inert marker transferred from the CNC."""
    try:
        status, body = exchange(
            host, port, "GET", f"/artifact/{agent_id}", b"", timeout
        )
    except (OSError, ValueError) as exc:
        LOG.warning("inert marker transfer failed: %s", exc)
        return False
    if status != 200 or body != INERT_MARKER:
        LOG.warning("CNC did not return the expected inert marker")
        return False
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)
    except OSError as exc:
        LOG.warning("could not store inert marker: %s", exc)
        return False
    emit("tool_received", agent_id=agent_id, bytes=len(body), path=str(destination))
    return True


def local_address(peer: str) -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((peer, 9))
            return probe.getsockname()[0]
    except OSError:
        return socket.gethostname()


def check_in(host: str, port: int, arch: str, timeout: float) -> str | None:
    """Register with the CNC and return the assigned agent uuid."""
    try:
        reply = request(
            host,
            port,
            "POST",
            "/checkin",
            json.dumps({"address": local_address(host), "arch": arch}).encode(),
            timeout,
        )
    except (OSError, ValueError) as exc:
        LOG.warning("check-in failed: %s", exc)
        return None
    agent_id = reply.get("agent_id")
    emit("checkin", cnc=f"{host}:{port}", agent_id=agent_id)
    return agent_id


def run_command(command: str) -> tuple[bool, str]:
    """Apply chmod only to the inert marker; simulate other allowlisted verbs."""
    verb = command.strip().split(" ", 1)[0]
    if verb not in ALLOWED_VERBS:
        return False, f"refused: {verb} is not in the simulated allow-list"
    if command.strip() == f"chmod 775 {INERT_DESTINATION}":
        if not INERT_DESTINATION.is_file():
            return False, "inert marker is missing"
        try:
            INERT_DESTINATION.chmod(0o775)
        except OSError as exc:
            return False, f"inert marker permissions could not be updated: {exc}"
        return True, "inert marker permissions updated"
    if verb == "sleep":
        return True, "sleep simulated"
    # Echo the command back as evidence it was received and accepted.
    return True, f"simulated: {command}"


def upload(host: str, port: int, agent_id: str, timeout: float) -> bool:
    """Upload a synthetic artefact so the transfer path is observable."""
    body = f"metropolis-merlin-artefact {agent_id}\n".encode()
    try:
        reply = request(
            host,
            port,
            "POST",
            f"/upload/{agent_id}",
            body,
            timeout,
        )
        stored = reply.get("stored") is True
        emit("upload", agent_id=agent_id, bytes=len(body), stored=stored)
        return stored
    except (OSError, ValueError) as exc:
        LOG.warning("upload failed: %s", exc)
        return False


def report_result(
    host: str,
    port: int,
    agent_id: str,
    command: str,
    ok: bool,
    detail: str,
    timeout: float,
) -> bool:
    try:
        reply = request(
            host,
            port,
            "POST",
            f"/result/{agent_id}",
            json.dumps({"command": command, "ok": ok, "detail": detail}).encode(),
            timeout,
        )
    except (OSError, ValueError) as exc:
        LOG.warning("command result report failed: %s", exc)
        return False
    return reply.get("stored") is True


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    url = os.getenv("MERLIN_CNC_URL", "").strip()
    if not url:
        raise SystemExit(
            "MERLIN_CNC_URL is required, for example https://10.99.10.10:8443"
        )
    if not url.startswith("https://"):
        raise SystemExit("MERLIN_CNC_URL must use https://")
    host, port = parse_url(url)
    arch = os.getenv("AGENT_ARCH", "linux-x64-synthetic")
    timeout = float(os.getenv("AGENT_TIMEOUT", "3"))
    interval = max(MIN_SLEEP, float(os.getenv("AGENT_SLEEP", "20")))
    upload_once = os.getenv("AGENT_UPLOAD_ONCE", "false").strip().lower() == "true"

    deadline = time.monotonic() + float(os.getenv("AGENT_STARTUP_TIMEOUT", "90"))
    agent_id = check_in(host, port, arch, timeout)
    while agent_id is None and time.monotonic() < deadline:
        time.sleep(min(2, max(0, deadline - time.monotonic())))
        agent_id = check_in(host, port, arch, timeout)
    if agent_id is None:
        raise SystemExit("agent could not check in with the CNC before startup timeout")
    emit("agent_started", agent_id=agent_id, arch=arch, interval=interval)

    uploaded = False
    while True:
        try:
            reply = request(host, port, "GET", f"/commands/{agent_id}", b"", timeout)
        except (OSError, ValueError) as exc:
            LOG.warning("command poll failed: %s", exc)
            time.sleep(interval)
            continue
        for command in reply.get("commands", []):
            if command == f"upload {INERT_SOURCE} {INERT_DESTINATION}":
                ok = receive_artifact(host, port, agent_id, timeout)
                detail = (
                    "inert marker received" if ok else "inert marker transfer failed"
                )
            else:
                ok, detail = run_command(command)
            emit("command", agent_id=agent_id, command=command, ok=ok, detail=detail)
            reported = report_result(host, port, agent_id, command, ok, detail, timeout)
            emit("command_result_report", agent_id=agent_id, reported=reported)
        if upload_once and not uploaded:
            upload(host, port, agent_id, timeout)
            uploaded = True
        time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
