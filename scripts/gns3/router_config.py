"""Load each VyOS router's configuration script onto its node.

Gotham does this as part of building the topology: after creating a router node
it logs into the console, uploads the config script, runs it, and checks a
checksum. Metropolis does the same job, but drives the console deliberately: it
waits for the prompt instead of sleeping, sends the script's commands one at a
time, and then checks the router's own view of its interfaces rather than
trusting that the commands took.

A GNS3 node's console is delivered over a raw Telnet socket, so this uses a
socket and its own prompt matching instead of the telnetlib module, which was
removed in Python 3.13.

The VyOS appliance's default account is ``vyos``/``vyos``, which is what Gotham
logs in with. A blank appliance needs its image installed interactively before
it will boot at all; that is the one step that cannot be automated.
"""

from __future__ import annotations

import logging
import re
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

from client import Gns3Client

log = logging.getLogger("gns3.routers")

# The VyOS appliance's default account, as Gotham uses.
DEFAULT_USERNAME = "vyos"
DEFAULT_PASSWORD = "vyos"

LOGIN_PROMPT = re.compile(rb"(?:vyos|login):\s*$")
PASSWORD_PROMPT = re.compile(rb"Password:\s*$")
# The operational prompt, and the configuration prompt entered by "configure".
OP_PROMPT = re.compile(rb"[\w.\-]+@[\w.\-]+:~\$\s*$")
CONFIG_PROMPT = re.compile(rb"[\w.\-]+@[\w.\-]+#\s*$")

BOOT_TIMEOUT = 300.0
COMMAND_TIMEOUT = 30.0
# A router script's commands are cheap; committing and saving are not.
CONFIG_TIMEOUT = 120.0


class RouterError(RuntimeError):
    """A router console could not be driven to a usable state."""


@dataclass
class RouterResult:
    """What configuring one router did."""

    node: str
    applied: bool = False
    commands: int = 0
    missing: list[str] = field(default_factory=list)
    error: str | None = None


def config_commands(path: Path) -> list[str]:
    """The commands a router script applies, in order.

    Comments and blank lines are dropped. The ``set`` lines and the trailing
    ``commit`` and ``save`` are kept, because the script is written to be run
    as-is inside configuration mode.
    """
    commands = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        commands.append(stripped)
    return commands


def expected_interfaces(path: Path) -> dict[str, set[str]]:
    """Interface addresses the script claims, keyed by interface number.

    Used to check the router afterwards: the authority is what the device
    reports, not what the script said it would configure.
    """
    pattern = re.compile(
        r"^set interfaces ethernet eth(\d+)(?: vif \d+)? address '([^']+)'"
    )
    expected: dict[str, set[str]] = {}
    for command in config_commands(path):
        match = pattern.match(command)
        if match:
            number, address = match.groups()
            expected.setdefault(number, set()).add(address)
    return expected


class Console:
    """A raw console session that waits for prompts rather than sleeping."""

    def __init__(self, host: str, port: int, timeout: float = BOOT_TIMEOUT):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.socket: socket.socket | None = None

    def __enter__(self) -> "Console":
        try:
            self.socket = socket.create_connection(
                (self.host, self.port), timeout=self.timeout
            )
        except OSError as exc:
            raise RouterError(
                f"could not open a console on {self.host}:{self.port}: {exc}"
            ) from exc
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def close(self) -> None:
        if self.socket is not None:
            try:
                self.socket.close()
            finally:
                self.socket = None

    def expect(
        self,
        pattern: re.Pattern,
        timeout: float,
        buffer: bytes = b"",
        *,
        clear: bool = False,
    ) -> bytes:
        """Read until ``pattern`` matches the tail of what has arrived.

        ``clear`` starts from an empty buffer, which matters after a command
        whose output is wanted: the buffer still ends with the prompt from the
        previous read, and without clearing, that stale prompt would match
        immediately and the output would never be collected.

        Raises rather than returning silently, so a prompt that never appears is
        an error and not an empty string that later code treats as success.
        """
        if self.socket is None:
            raise RouterError("console is not connected")
        if clear:
            buffer = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pattern.search(buffer):
                return buffer
            try:
                chunk = self.socket.recv(4096)
            except socket.timeout:
                continue
            except OSError as exc:
                raise RouterError(f"console read failed: {exc}") from exc
            if not chunk:
                raise RouterError("console closed")
            buffer += chunk
        raise RouterError(f"timed out waiting for {pattern.pattern!r}")

    def login(self, username: str, password: str) -> bytes:
        """Log in and return the buffer positioned at the operational prompt."""
        buffer = self.expect(LOGIN_PROMPT, self.timeout)
        self.send(username)
        buffer = self.expect(PASSWORD_PROMPT, COMMAND_TIMEOUT, buffer)
        self.send(password)
        return self.expect(OP_PROMPT, COMMAND_TIMEOUT, buffer)

    def send(self, line: str) -> None:
        if self.socket is None:
            raise RouterError("console is not connected")
        self.socket.sendall(line.encode("utf-8") + b"\n")


def apply_router_config(
    host: str,
    port: int,
    script: Path,
    *,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
    boot_timeout: float = BOOT_TIMEOUT,
) -> RouterResult:
    """Log in to a router's console and apply its configuration script."""
    commands = config_commands(script)
    expected = expected_interfaces(script)
    result = RouterResult(node=script.stem)

    try:
        with Console(host, port, boot_timeout) as console:
            buffer = console.login(username, password)
            console.send("configure")
            buffer = console.expect(CONFIG_PROMPT, CONFIG_TIMEOUT, buffer)

            for command in commands:
                console.send(command)
                # Each prompt means the line was accepted. A rejected line still
                # returns a prompt, so the read-back below is what confirms the
                # configuration actually took.
                buffer = console.expect(CONFIG_PROMPT, CONFIG_TIMEOUT, buffer)
                result.commands += 1

            console.send("run show interfaces")
            output = console.expect(CONFIG_PROMPT, CONFIG_TIMEOUT, clear=True)
    except RouterError as exc:
        result.error = str(exc)
        return result

    result.applied = True
    text = output.decode("utf-8", errors="replace")
    for number, addresses in sorted(expected.items()):
        for address in sorted(addresses):
            if address not in text:
                result.missing.append(f"eth{number} {address}")
    return result


def configure_project_routers(
    client: Gns3Client,
    project_id: str,
    routers: dict[str, str],
    *,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
) -> list[RouterResult]:
    """Configure every router in a project from its script.

    ``routers`` maps a node name to the repository-relative script path. A node
    that exposes no console, or a script that is missing, is reported rather
    than skipped silently.
    """
    results: list[RouterResult] = []
    nodes = {node["name"]: node for node in client.project_nodes(project_id)}
    for name, script_path in sorted(routers.items()):
        node = nodes.get(name)
        if node is None:
            results.append(RouterResult(node=name, error="node is not in the project"))
            continue
        console = client.node_console(project_id, node["node_id"])
        if console is None:
            results.append(RouterResult(node=name, error="node exposes no console"))
            continue
        host, port = console
        results.append(
            apply_router_config(
                host, port, Path(script_path), username=username, password=password
            )
        )
    return results
