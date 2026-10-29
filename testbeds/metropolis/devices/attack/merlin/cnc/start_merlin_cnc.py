#!/usr/bin/env python3
# Adapted from Gotham's Merlin C&C workflow: https://github.com/xsaga/gotham-iot-testbed/blob/master/src/run_merlin.py
"""Synthetic Merlin command-and-control console.

Mirrors the Gotham ``iotsim-merlin-cnc`` node. Gotham drives this console over
Telnet with a fixed command sequence::

    listeners
    use https
    set Interface 0.0.0.0
    start
    info
    agent list
    agent interact <uuid>
    upload <local> <remote>
    run <command>
    back

This simulation implements that vocabulary. Agents poll a TLS-protected
command channel; operators drive the control channel. The only transferable
content is an inert marker. No real Merlin or hping3 binary is present.

Channels:

* control port (``MERLIN_CONTROL_PORT``, default 23) - operator console
* command port (``MERLIN_HTTP_PORT``, default 8443) - HTTPS agent channel
"""

from __future__ import annotations

import json
import logging
import os
import socket
import ssl
import threading
import time
import uuid
from dataclasses import dataclass, field

LOG = logging.getLogger("merlin.cnc")

MAX_LINE = 1024
MAX_BODY = 65536
MAX_AGENTS = 256
# Bound stored uploads so a runaway experiment cannot fill the node.
MAX_UPLOADS = 64
INERT_SOURCE = "/opt/metropolis/inert-hping3.txt"
INERT_DESTINATION = os.getenv(
    "MERLIN_MARKER_PATH", "/tmp/metropolis-merlin/hping3.marker"
)
INERT_TOOL = b"metropolis-inert-hping3-marker\n"


@dataclass
class Agent:
    """One checked-in agent, identified the way Gotham's console identifies it."""

    agent_id: str
    address: str
    arch: str
    last_seen: float
    peer_address: str | None = None
    commands: list[str] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    result_sequence: int = 0
    transfers: list[dict] = field(default_factory=list)
    transfer_sequence: int = 0


class State:
    """Listener status, agents, and uploaded artefacts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.listener = {"state": "stopped", "scheme": None, "interface": None}
        self._agents: dict[str, Agent] = {}
        self.uploads: list[dict] = []
        self._upload_sequence = 0

    def start_listener(self, scheme: str, interface: str) -> None:
        with self._lock:
            self.listener = {
                "state": "running",
                "scheme": scheme,
                "interface": interface,
                "started_at": time.time(),
            }

    def listener_info(self) -> dict:
        with self._lock:
            return dict(self.listener)

    def check_in(
        self, address: str, arch: str, peer_address: str | None = None
    ) -> Agent:
        with self._lock:
            agent = next(
                (item for item in self._agents.values() if item.address == address),
                None,
            )
            if agent is None:
                if len(self._agents) >= MAX_AGENTS:
                    raise ValueError("Agent registry is full")
                agent = Agent(
                    str(uuid.uuid4()), address, arch, time.time(), peer_address
                )
                self._agents[agent.agent_id] = agent
                LOG.info("agent %s checked in from %s", agent.agent_id, address)
            else:
                agent.last_seen = time.time()
                agent.arch = arch
                agent.peer_address = peer_address
            return agent

    def get(self, agent_id: str) -> Agent | None:
        with self._lock:
            return self._agents.get(agent_id)

    def as_rows(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "agent_id": agent.agent_id,
                    "address": agent.address,
                    "peer_address": agent.peer_address,
                    "arch": agent.arch,
                    "commands": len(agent.commands),
                }
                for agent in self._agents.values()
            ]

    def take_commands(self, agent_id: str) -> list[str]:
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                return []
            pending, agent.commands = agent.commands, []
            return pending

    def add_command(self, agent: Agent, command: str) -> None:
        with self._lock:
            agent.commands.append(command)

    def add_upload(self, agent_id: str, name: str, body: bytes) -> None:
        with self._lock:
            self._upload_sequence += 1
            if len(self.uploads) >= MAX_UPLOADS:
                self.uploads.pop(0)
            self.uploads.append(
                {
                    "agent_id": agent_id,
                    "sequence": self._upload_sequence,
                    "name": name,
                    "bytes": len(body),
                    "preview": body[:200].decode("utf-8", errors="replace"),
                }
            )

    def add_result(self, agent_id: str, command: str, ok: bool, detail: str) -> bool:
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                return False
            agent.result_sequence += 1
            agent.results.append(
                {
                    "sequence": agent.result_sequence,
                    "command": command,
                    "ok": ok,
                    "detail": detail,
                }
            )
            del agent.results[:-64]
            return True

    def record_transfer(self, agent_id: str, peer_address: str) -> bool:
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                return False
            agent.transfer_sequence += 1
            agent.transfers.append(
                {
                    "sequence": agent.transfer_sequence,
                    "bytes": len(INERT_TOOL),
                    "peer_address": peer_address,
                }
            )
            del agent.transfers[:-64]
            return True

    def status(self, agent_id: str) -> dict | None:
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                return None
            return {
                "agent_id": agent_id,
                "address": agent.address,
                "peer_address": agent.peer_address,
                "results": list(agent.results),
                "transfers": list(agent.transfers),
                "uploads": [
                    {
                        "sequence": item["sequence"],
                        "name": item["name"],
                        "bytes": item["bytes"],
                    }
                    for item in self.uploads
                    if item["agent_id"] == agent_id
                ],
            }


def send_line(stream, text: str) -> None:
    stream.write((text + "\r\n").encode("utf-8"))
    stream.flush()


def read_line(connection: socket.socket) -> str:
    try:
        data = connection.recv(MAX_LINE)
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace").strip() if data else ""


def handle_operator(state: State, connection: socket.socket) -> None:
    """Serve one operator console session using Gotham's vocabulary."""
    stream = connection.makefile("wb")
    send_line(stream, "metropolis-merlin-cnc (synthetic lab console)")
    selection: Agent | None = None
    pending_scheme = "https"
    while True:
        line = read_line(connection)
        if not line:
            return
        parts = line.split(" ")
        verb, argument = parts[0].lower(), " ".join(parts[1:]).strip()
        if verb == "listeners":
            send_line(stream, json.dumps(state.listener_info(), sort_keys=True))
        elif verb == "use":
            if argument and argument != "https":
                send_line(stream, "only https is supported")
                continue
            pending_scheme = "https"
            send_line(stream, "listener module https")
        elif verb == "set":
            key, _, value = argument.partition(" ")
            send_line(
                stream, f"{key} => {value}" if key else "set needs a key and value"
            )
        elif verb == "start":
            state.start_listener(pending_scheme, "0.0.0.0")
            send_line(stream, f"listener {pending_scheme} started")
        elif verb == "info":
            send_line(stream, json.dumps(state.listener_info(), sort_keys=True))
        elif verb == "agent":
            sub, _, rest = argument.partition(" ")
            if sub == "list":
                for row in state.as_rows():
                    send_line(stream, json.dumps(row, sort_keys=True))
                send_line(stream, "END")
            elif sub == "interact":
                selection = state.get(rest.strip())
                send_line(
                    stream,
                    f"interacting with {selection.agent_id}"
                    if selection
                    else f"unknown agent {rest.strip()}",
                )
            else:
                send_line(stream, "agent needs 'list' or 'interact'")
        elif verb == "run":
            if selection is None:
                send_line(stream, "interact with an agent first")
            elif not argument:
                send_line(stream, "run needs a command")
            else:
                # Recorded only; this lab never executes delivered commands.
                state.add_command(selection, argument)
                send_line(stream, f"queued for {selection.agent_id}: {argument}")
        elif verb == "upload":
            source, _, destination = argument.partition(" ")
            if selection is None:
                send_line(stream, "interact with an agent first")
            elif (source, destination.strip()) != (INERT_SOURCE, INERT_DESTINATION):
                send_line(stream, "only the built-in inert marker is transferable")
            else:
                state.add_command(selection, f"upload {source} {destination}")
                send_line(stream, f"queued inert transfer for {selection.agent_id}")
        elif verb == "back":
            if selection is not None:
                selection = None
                send_line(stream, "selection cleared")
            else:
                send_line(stream, "at top level")
        elif verb in {"exit", "quit"}:
            send_line(stream, "bye")
            return
        else:
            send_line(stream, "unknown command")


def operator_console(state: State, host: str, port: int, stop: threading.Event) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(16)
        server.settimeout(0.5)
        LOG.info("operator console listening on %s:%d", host, port)
        while not stop.is_set():
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(
                target=handle_operator, args=(state, connection), daemon=True
            ).start()


def http_response(
    connection: socket.socket,
    status: str,
    body: bytes,
    content_type: bytes = b"application/json",
) -> None:
    connection.sendall(
        f"HTTP/1.0 {status}\r\n".encode()
        + b"Content-Type: "
        + content_type
        + b"\r\n"
        + b"Content-Length: %d\r\n" % len(body)
        + b"Connection: close\r\n\r\n"
        + body
    )


def read_http_request(connection: socket.socket) -> tuple[bytes, bytes, dict, bytes]:
    """Read a bounded request even when TLS splits headers and body across records."""
    connection.settimeout(5)
    raw = bytearray()
    while b"\r\n\r\n" not in raw:
        chunk = connection.recv(4096)
        if not chunk:
            raise ValueError("Incomplete HTTP headers")
        raw.extend(chunk)
        if len(raw) > MAX_BODY + 8192:
            raise ValueError("HTTP request is too large")
    head, _, body = bytes(raw).partition(b"\r\n\r\n")
    if len(head) > 8192:
        raise ValueError("HTTP headers are too large")
    lines = head.split(b"\r\n")
    parts = lines[0].split(b" ")
    if len(parts) < 2:
        raise ValueError("Malformed HTTP request line")
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(b":")
        headers[name.strip().lower()] = value.strip()
    length = int(headers.get(b"content-length", b"0"))
    if not 0 <= length <= MAX_BODY:
        raise ValueError("HTTP body is too large")
    while len(body) < length:
        chunk = connection.recv(min(4096, length - len(body)))
        if not chunk:
            raise ValueError("Incomplete HTTP body")
        body += chunk
    return parts[0], parts[1], headers, body[:length]


def handle_agent(state: State, connection: socket.socket, peer) -> None:
    """Serve one HTTPS agent request."""
    try:
        verb, path, headers, body = read_http_request(connection)
        if verb == b"POST" and path.startswith(b"/checkin"):
            payload = json.loads(body or b"{}")
            try:
                agent = state.check_in(
                    str(payload.get("address", peer[0])),
                    str(payload.get("arch", "unknown")),
                    peer[0],
                )
            except ValueError as exc:
                http_response(
                    connection,
                    "503 Service Unavailable",
                    json.dumps({"error": str(exc)}).encode(),
                )
                return
            http_response(
                connection, "200 OK", json.dumps({"agent_id": agent.agent_id}).encode()
            )
        elif verb == b"GET" and path.startswith(b"/commands/"):
            agent_id = path.decode().rsplit("/", 1)[-1]
            http_response(
                connection,
                "200 OK",
                json.dumps({"commands": state.take_commands(agent_id)}).encode(),
            )
        elif verb == b"GET" and path.startswith(b"/artifact/"):
            agent_id = path.decode().rsplit("/", 1)[-1]
            if state.record_transfer(agent_id, peer[0]):
                LOG.info(
                    "inert marker transferred to agent %s at %s", agent_id, peer[0]
                )
                http_response(
                    connection, "200 OK", INERT_TOOL, b"application/octet-stream"
                )
            else:
                http_response(connection, "404 Not Found", b'{"error":"unknown agent"}')
        elif verb == b"GET" and path.startswith(b"/status/"):
            agent_id = path.decode().rsplit("/", 1)[-1]
            status = state.status(agent_id)
            http_response(
                connection,
                "200 OK" if status else "404 Not Found",
                json.dumps(status or {"error": "unknown agent"}).encode(),
            )
        elif verb == b"POST" and path.startswith(b"/result/"):
            agent_id = path.decode().rsplit("/", 1)[-1]
            payload = json.loads(body or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("Result must be an object")
            stored = state.add_result(
                agent_id,
                str(payload.get("command", "")),
                bool(payload.get("ok", False)),
                str(payload.get("detail", "")),
            )
            http_response(
                connection,
                "202 Accepted" if stored else "404 Not Found",
                json.dumps({"stored": stored}).encode(),
            )
        elif verb == b"POST" and path.startswith(b"/upload/"):
            agent_id = path.decode().split("/")[2]
            state.add_upload(
                agent_id,
                headers.get(b"x-merlin-file", b"unnamed").decode(
                    "utf-8", errors="replace"
                ),
                body,
            )
            http_response(connection, "202 Accepted", b'{"stored":true}')
        else:
            http_response(connection, "404 Not Found", b'{"error":"unknown route"}')
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        LOG.warning("agent request failed: %s", exc)
    finally:
        connection.close()


def agent_channel(
    state: State, host: str, port: int, stop: threading.Event, context: ssl.SSLContext
) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(32)
        server.settimeout(0.5)
        LOG.info("agent channel listening on %s:%d", host, port)
        while not stop.is_set():
            try:
                connection, peer = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                connection.settimeout(5)
                secure = context.wrap_socket(connection, server_side=True)
            except (OSError, ssl.SSLError) as exc:
                LOG.warning("TLS handshake failed from %s: %s", peer[0], exc)
                connection.close()
                continue
            threading.Thread(
                target=handle_agent, args=(state, secure, peer), daemon=True
            ).start()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    host = os.getenv("MERLIN_BIND", "0.0.0.0")
    control_port = int(os.getenv("MERLIN_CONTROL_PORT", "23"))
    http_port = int(os.getenv("MERLIN_HTTP_PORT", "8443"))
    cert_file = os.getenv("MERLIN_TLS_CERT_FILE", "/opt/metropolis/tls/server.crt")
    key_file = os.getenv("MERLIN_TLS_KEY_FILE", "/opt/metropolis/tls/server.key")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert_file, keyfile=key_file)
    stop = threading.Event()
    state = State()
    threading.Thread(
        target=agent_channel, args=(state, host, http_port, stop, context), daemon=True
    ).start()
    try:
        operator_console(state, host, control_port, stop)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


if __name__ == "__main__":
    main()
