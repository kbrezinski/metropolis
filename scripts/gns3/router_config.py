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
import os
import re
import shlex
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

from client import Gns3Client
from firewall_policy import managed_deletes

log = logging.getLogger("gns3.routers")

# The VyOS appliance's default account, as Gotham uses.
DEFAULT_USERNAME = "vyos"
DEFAULT_PASSWORD = "vyos"

# Exact routes deployed by earlier Metropolis scripts. Do not remove a user's
# other static routes when reconfiguring an existing appliance.
LEGACY_STATIC_ROUTES: dict[str, dict[str, str]] = {
    "router_ot_core": {
        **{
            f"10.20.{octet}.0/24": "10.20.254.2"
            for octet in (10, 11, 12, 13, 50, 51, 52, 53)
        },
        "10.20.14.0/24": "10.20.254.10",
        "10.20.15.0/24": "10.20.254.10",
        "10.20.40.0/24": "10.20.254.6",
        "10.30.10.0/24": "10.20.254.6",
        "10.99.10.0/24": "10.20.254.6",
    },
    "router_plant": {"0.0.0.0/0": "10.20.254.1"},
    "router_ot_dmz": {"10.20.0.0/16": "10.20.254.5"},
    "router_ot_wan": {
        "10.20.14.0/24": "172.31.255.21",
        "10.20.15.0/24": "172.31.255.11",
        "10.20.20.0/24": "10.20.254.9",
        "10.20.30.0/24": "10.20.254.9",
    },
    "router_raw_water": {
        "10.20.20.0/24": "172.31.255.1",
        "10.20.23.0/24": "172.31.255.1",
        "10.20.30.0/24": "172.31.255.1",
    },
    "router_reservoir": {
        "10.20.20.0/24": "172.31.255.1",
        "10.20.23.0/24": "172.31.255.1",
        "10.20.30.0/24": "172.31.255.1",
    },
}
STATIC_COMMAND = re.compile(
    r"^set protocols static route (\S+) next-hop ['\"]?([^'\"\s]+)['\"]?",
    re.M,
)


def legacy_static_deletes(script: Path, configuration: str) -> list[str]:
    """Delete only exact Metropolis static routes found in the running config."""
    known = LEGACY_STATIC_ROUTES.get(script.stem, {})
    return [
        f"delete protocols static route {prefix} next-hop '{hop}'"
        for prefix, hop in STATIC_COMMAND.findall(configuration)
        if known.get(prefix) == hop
    ]


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


SECRET_REFERENCE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
SECRET_GUARD = re.compile(r'^:\s+"\$\{[A-Z][A-Z0-9_]*:\?[^"\r\n]*\}"$')
SAFE_PSK = re.compile(r"[A-Za-z0-9_+/=-]{32,128}")


def _secret_commands(commands: list[str]) -> list[str]:
    """Expand only IPsec PSK placeholders, without evaluating shell code."""
    resolved = []
    for command in commands:
        names = SECRET_REFERENCE.findall(command)
        if "${" in command and not names:
            raise RouterError("unsupported environment placeholder in router script")
        if names:
            if (
                len(names) != 1
                or not command.startswith("set vpn ipsec site-to-site peer ")
                or " authentication pre-shared-secret " not in command
                or not command.endswith('"${' + names[0] + '}"')
            ):
                raise RouterError(
                    "environment placeholders are only supported for IPsec PSKs"
                )
            name = names[0]
            value = os.environ.get(name, "")
            if not SAFE_PSK.fullmatch(value):
                raise RouterError(
                    f"{name} must contain 32–128 letters, digits, or _+/=- characters"
                )
            command = command.replace("${" + name + "}", value)
        resolved.append(command)
    return resolved


def validate_router_secrets(scripts: dict[str, str]) -> None:
    """Fail before any router changes if required controller-side PSKs are missing."""
    for name, script in sorted(scripts.items()):
        try:
            _secret_commands(config_commands(Path(script)))
        except (OSError, RouterError) as exc:
            raise RouterError(f"{name}: {exc}") from None


def config_commands(path: Path) -> list[str]:
    """The commands a router script applies, in order.

    Comments and blank lines are dropped. The ``set`` lines and the trailing
    ``commit`` and ``save`` are kept, because the script is written to be run
    as-is inside configuration mode.
    """
    commands = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith("#")
            or stripped in {"configure", "exit"}
            or stripped.startswith("source ")
            or SECRET_GUARD.fullmatch(stripped)
        ):
            continue
        commands.append(stripped)
    return commands


def expected_interfaces(path: Path) -> dict[str, set[str]]:
    """Interface addresses the script claims, keyed by interface number.

    Used to check the router afterwards: the authority is what the device
    reports, not what the script said it would configure.
    """
    pattern = re.compile(
        r"^set interfaces (?:ethernet eth(\d+)(?: vif \d+)?|vti (vti\d+)) address '([^']+)'"
    )
    expected: dict[str, set[str]] = {}
    for command in config_commands(path):
        match = pattern.match(command)
        if match:
            number, tunnel, address = match.groups()
            expected.setdefault(tunnel or number, set()).add(address)
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
    firewall_commands: list[str] | None = None,
) -> RouterResult:
    """Log in to a router's console and apply its configuration script."""
    result = RouterResult(node=script.stem)
    secret_values: set[str] = set()
    try:
        script_commands = config_commands(script)
        secret_values = {
            value
            for command in script_commands
            for name in SECRET_REFERENCE.findall(command)
            if (value := os.environ.get(name))
        }
        commands = _secret_commands(
            [
                command
                for command in script_commands
                if command not in {"commit", "save"}
            ]
        )
        commands += firewall_commands or []
        commands += ["commit", "save"]
        expected = expected_interfaces(script)
        ipsec = any(command.startswith("set vpn ipsec ") for command in script_commands)
        with Console(host, port, boot_timeout) as console:
            buffer = console.login(username, password)
            console.send("show configuration commands")
            existing = console.expect(OP_PROMPT, CONFIG_TIMEOUT, clear=True).decode(
                "utf-8", errors="replace"
            )
            console.send("configure")
            buffer = console.expect(CONFIG_PROMPT, CONFIG_TIMEOUT, clear=True)

            legacy_deletes = legacy_static_deletes(script, existing)
            commands = legacy_deletes + commands
            if firewall_commands is not None:
                commands = managed_deletes(existing) + commands

            for command in commands:
                console.send(command)
                # Each prompt means the line was accepted. A rejected line still
                # returns a prompt, so the read-back below is what confirms the
                # configuration actually took.
                buffer = console.expect(CONFIG_PROMPT, CONFIG_TIMEOUT, clear=True)
                if command.startswith("set ") and any(
                    marker in buffer.lower()
                    for marker in (
                        b"invalid command",
                        b"set failed",
                        b"configuration path is not valid",
                    )
                ):
                    raise RouterError("router rejected a configuration command")
                if command == "commit" and any(
                    marker in buffer.lower()
                    for marker in (b"commit failed", b"configuration error")
                ):
                    raise RouterError(
                        f"firewall/router commit failed: {buffer.decode(errors='replace')}"
                    )
                result.commands += 1

            console.send("run show interfaces")
            output = console.expect(CONFIG_PROMPT, CONFIG_TIMEOUT, clear=True)
            firewall_output = b""
            if firewall_commands is not None or legacy_deletes or ipsec:
                console.send("run show configuration commands")
                firewall_output = console.expect(
                    CONFIG_PROMPT, CONFIG_TIMEOUT, clear=True
                )
    except (OSError, RouterError) as exc:
        result.error = str(exc)
        for value in sorted(secret_values, key=len, reverse=True):
            result.error = result.error.replace(value, "[REDACTED]")
        return result

    result.applied = True
    text = output.decode("utf-8", errors="replace")
    for number, addresses in sorted(expected.items()):
        for address in sorted(addresses):
            if address not in text:
                interface = number if number.startswith("vti") else f"eth{number}"
                result.missing.append(f"{interface} {address}")
    configured = firewall_output.decode("utf-8", errors="replace")
    if ipsec:
        try:
            actual = {
                tuple(shlex.split(line.strip()))
                for line in configured.splitlines()
                if line.strip().startswith("set ")
            }
        except ValueError:
            result.error = "router configuration readback could not be parsed"
            return result
        for command in commands:
            tokens = tuple(shlex.split(command))
            if command.startswith(
                (
                    "set vpn ipsec ",
                    "set interfaces vti ",
                    "set protocols ospf ",
                    "set protocols bfd ",
                )
            ):
                if "pre-shared-secret" in tokens:
                    if not any(row[:-1] == tokens[:-1] for row in actual):
                        result.missing.append(
                            "IPsec pre-shared-secret setting is absent"
                        )
                elif tokens not in actual:
                    result.missing.append(command)
            elif (
                command.startswith("delete ") and tuple(["set", *tokens[1:]]) in actual
            ):
                result.missing.append(f"obsolete configuration remains: {command}")
    for command in legacy_static_deletes(script, configured):
        result.missing.append(f"legacy static route remains: {command}")
    if firewall_commands is not None:
        expected_names = {
            command.split()[3]
            for command in firewall_commands
            if command.startswith("set firewall name ")
        } | {
            command.split()[3]
            for command in firewall_commands
            if command.startswith("set zone-policy zone ")
        }
        for name in sorted(expected_names):
            if name not in configured:
                result.missing.append(f"firewall {name}")
    return result


def configure_project_routers(
    client: Gns3Client,
    project_id: str,
    routers: dict[str, str],
    *,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
    firewall_commands: dict[str, list[str]] | None = None,
) -> list[RouterResult]:
    """Configure every router in a project from its script.

    ``routers`` maps a node name to the repository-relative script path. A node
    that exposes no console, or a script that is missing, is reported rather
    than skipped silently.
    """
    results: list[RouterResult] = []
    try:
        validate_router_secrets(routers)
    except RouterError as exc:
        return [
            RouterResult(node=name, error=f"configuration preflight failed: {exc}")
            for name in sorted(routers)
        ]
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
                host,
                port,
                Path(script_path),
                username=username,
                password=password,
                firewall_commands=(
                    firewall_commands[name] if firewall_commands is not None else None
                ),
            )
        )
    return results
