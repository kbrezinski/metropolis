#!/usr/bin/env python3
"""Synthetic Merlin agent.

Mirrors the Gotham ``iotsim-mirai-bot`` node acting as a Merlin victim: Gotham
starts an agent with::

    /opt/merlin/merlinAgent-Linux-x64 -url https://10.99.10.10:8443 -sleep 20s

It then drives the agent from the CNC console with ``upload`` and ``run``.
This simulation reproduces that lifecycle over HTTP: check in, poll for
commands, execute a bounded allow-list of them, and upload a synthetic
artefact.

**This is an inert lab simulation, not malware.** No real agent binary exists
in this repository. Commands are executed only if they match the allow-list
below, and "execution" means recording and echoing the command, never running
a shell.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from datetime import datetime, timezone

LOG = logging.getLogger("merlin.agent")

# Only these command verbs are simulated. Anything else is refused and
# reported as such, mirroring a real agent that cannot run unknown commands.
ALLOWED_VERBS = {"chmod", "echo", "id", "uname", "upload", "sleep"}
MIN_SLEEP = 1.0


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
    """Parse ``host:port`` or ``http://host:port`` into a (host, port) pair."""
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
    """Small HTTP client; the lab speaks HTTP, not TLS, inside the testbed."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        head = (
            f"{method} {path} HTTP/1.0\r\n"
            f"Host: {host}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        sock.sendall(head + body)
        chunks = []
        while True:
            try:
                chunk = sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
    raw = b"".join(chunks)
    _, _, payload = raw.partition(b"\r\n\r\n")
    if not payload.strip():
        return {}
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {"raw": payload.decode("utf-8", errors="replace")}


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
    except OSError as exc:
        LOG.warning("check-in failed: %s", exc)
        return None
    agent_id = reply.get("agent_id")
    emit("checkin", cnc=f"{host}:{port}", agent_id=agent_id)
    return agent_id


def run_command(command: str) -> tuple[bool, str]:
    """Simulate one command from the allow-list without spawning a shell."""
    verb = command.strip().split(" ", 1)[0]
    if verb not in ALLOWED_VERBS:
        return False, f"refused: {verb} is not in the simulated allow-list"
    if verb == "sleep":
        return True, "sleep simulated"
    # Echo the command back as evidence it was received and accepted.
    return True, f"simulated: {command}"


def upload(host: str, port: int, agent_id: str, timeout: float) -> None:
    """Upload a synthetic artefact so the transfer path is observable."""
    body = f"metropolis-merlin-artefact {agent_id}\n".encode()
    try:
        request(
            host,
            port,
            "POST",
            f"/upload/{agent_id}",
            body,
            timeout,
        )
        emit("upload", agent_id=agent_id, bytes=len(body))
    except OSError as exc:
        LOG.warning("upload failed: %s", exc)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    url = os.getenv("MERLIN_CNC_URL", "").strip()
    if not url:
        raise SystemExit(
            "MERLIN_CNC_URL is required, for example http://10.99.10.10:8443"
        )
    host, port = parse_url(url)
    arch = os.getenv("AGENT_ARCH", "linux-x64-synthetic")
    timeout = float(os.getenv("AGENT_TIMEOUT", "3"))
    interval = max(MIN_SLEEP, float(os.getenv("AGENT_SLEEP", "20")))
    upload_once = os.getenv("AGENT_UPLOAD_ONCE", "true").strip().lower() == "true"

    agent_id = check_in(host, port, arch, timeout)
    if agent_id is None:
        raise SystemExit("agent could not check in with the CNC")
    emit("agent_started", agent_id=agent_id, arch=arch, interval=interval)

    uploaded = False
    while True:
        try:
            reply = request(host, port, "GET", f"/commands/{agent_id}", b"", timeout)
        except OSError as exc:
            LOG.warning("command poll failed: %s", exc)
            time.sleep(interval)
            continue
        for command in reply.get("commands", []):
            ok, detail = run_command(command)
            emit("command", agent_id=agent_id, command=command, ok=ok, detail=detail)
        if upload_once and not uploaded:
            upload(host, port, agent_id, timeout)
            uploaded = True
        time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
