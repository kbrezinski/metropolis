#!/usr/bin/env python3
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

This simulation implements that vocabulary so the Gotham choreography runs
unchanged against a Metropolis node. Agents poll an HTTP command channel;
operators drive the control channel. Nothing here is a real C2: no agent
binary exists in this repository and delivered commands are recorded, never
executed.

Channels:

* control port (``MERLIN_CONTROL_PORT``, default 23) - operator console
* command port (``MERLIN_HTTP_PORT``, default 8443) - agent poll, upload, report
"""

from __future__ import annotations

import json
import logging
import os
import socket
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


@dataclass
class Agent:
    """One checked-in agent, identified the way Gotham's console identifies it."""

    agent_id: str
    address: str
    arch: str
    last_seen: float
    commands: list[str] = field(default_factory=list)


class State:
    """Listener status, agents, and uploaded artefacts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.listener = {"state": "stopped", "scheme": None, "interface": None}
        self._agents: dict[str, Agent] = {}
        self.uploads: list[dict] = []

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

    def check_in(self, address: str, arch: str) -> Agent:
        with self._lock:
            agent = next(
                (item for item in self._agents.values() if item.address == address),
                None,
            )
            if agent is None:
                if len(self._agents) >= MAX_AGENTS:
                    raise ValueError("Agent registry is full")
                agent = Agent(str(uuid.uuid4()), address, arch, time.time())
                self._agents[agent.agent_id] = agent
                LOG.info("agent %s checked in from %s", agent.agent_id, address)
            else:
                agent.last_seen = time.time()
                agent.arch = arch
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
            if len(self.uploads) >= MAX_UPLOADS:
                self.uploads.pop(0)
            self.uploads.append(
                {
                    "agent_id": agent_id,
                    "name": name,
                    "bytes": len(body),
                    "preview": body[:200].decode("utf-8", errors="replace"),
                }
            )


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
            pending_scheme = argument or "https"
            send_line(stream, f"listener module {pending_scheme}")
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
            send_line(
                stream,
                "upload is an agent-side action here; use the agent channel",
            )
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


def http_response(connection: socket.socket, status: str, body: bytes) -> None:
    connection.sendall(
        f"HTTP/1.0 {status}\r\n".encode()
        + b"Content-Type: application/json\r\n"
        + b"Content-Length: %d\r\n" % len(body)
        + b"Connection: close\r\n\r\n"
        + body
    )


def handle_agent(state: State, connection: socket.socket, peer) -> None:
    """Serve one agent request: check-in, command poll, or upload."""
    try:
        request = connection.recv(MAX_BODY)
        if not request:
            return
        head, _, body = request.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        verb, path = (lines[0].split(b" ") + [b"/", b"/"])[:2]
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(b":")
            headers[name.strip().lower()] = value.strip()
        if verb == b"POST" and path.startswith(b"/checkin"):
            payload = json.loads(body or b"{}")
            try:
                agent = state.check_in(
                    str(payload.get("address", peer[0])),
                    str(payload.get("arch", "unknown")),
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


def agent_channel(state: State, host: str, port: int, stop: threading.Event) -> None:
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
            threading.Thread(
                target=handle_agent, args=(state, connection, peer), daemon=True
            ).start()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    host = os.getenv("MERLIN_BIND", "0.0.0.0")
    control_port = int(os.getenv("MERLIN_CONTROL_PORT", "23"))
    http_port = int(os.getenv("MERLIN_HTTP_PORT", "8443"))
    stop = threading.Event()
    state = State()
    threading.Thread(
        target=agent_channel, args=(state, host, http_port, stop), daemon=True
    ).start()
    try:
        operator_console(state, host, control_port, stop)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


if __name__ == "__main__":
    main()
