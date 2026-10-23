"""Checks for loading router configuration scripts onto VyOS nodes.

The console is exercised against a scripted socket, so the interaction is tested
without a GNS3 server or a booted router.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

GNS3 = Path(__file__).resolve().parents[1] / "scripts" / "gns3"
if str(GNS3) not in sys.path:
    sys.path.insert(0, str(GNS3))

from router_config import (  # noqa: E402
    CONFIG_PROMPT,
    OP_PROMPT,
    Console,
    RouterError,
    apply_router_config,
    config_commands,
    expected_interfaces,
)


class ScriptedSocket:
    """A socket that replies to each command the way a router would.

    Reacting to input is both simpler to reason about than a fixed queue of
    prompt strings and closer to what a real console does: every line gets a
    prompt, and the interface query gets the running configuration.
    """

    TIMEOUT = "<timeout>"

    def __init__(
        self,
        *,
        banner: bytes = b"vyos login: ",
        password_prompt: bytes = b"Password: ",
        op_prompt: bytes = b"vyos@vyos:~$ ",
        config_prompt: bytes = b"vyos@vyos# ",
        interfaces: bytes = b"",
        configuration: bytes = b"",
        respond: bool = True,
    ):
        self.banner = banner
        self.password_prompt = password_prompt
        self.op_prompt = op_prompt
        self.config_prompt = config_prompt
        self.interfaces = interfaces
        self.configuration = configuration
        self.respond = respond
        self.sent: list[bytes] = []
        self.pending = bytearray(banner)
        self.line = b""
        self.stage = "login"

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)
        if not self.respond:
            return
        for byte in data:
            if byte == 0x0A:
                self._react(self.line.decode().strip())
                self.line = b""
            else:
                self.line += bytes([byte])

    def _react(self, command: str) -> None:
        if self.stage == "login":
            self.pending += self.password_prompt
            self.stage = "password"
        elif self.stage == "password":
            self.pending += self.op_prompt
            self.stage = "operational"
        elif command == "show configuration commands":
            self.pending += self.configuration + self.op_prompt
        elif command == "run show configuration commands":
            self.pending += self.configuration + self.config_prompt
        elif command == "run show interfaces":
            self.pending += self.interfaces + self.config_prompt
        elif command.startswith("delete protocols static route "):
            old = command.replace("delete ", "set ", 1).encode()
            self.configuration = b"\n".join(
                line for line in self.configuration.splitlines() if line != old
            )
            self.pending += self.config_prompt
        else:
            self.pending += self.config_prompt

    def recv(self, size: int) -> bytes:
        if not self.pending:
            raise socket.timeout("no data")
        chunk = bytes(self.pending)
        self.pending.clear()
        return chunk

    def close(self) -> None:
        pass


@pytest.fixture
def scripted(monkeypatch):
    """Replace socket.create_connection with a scripted one."""

    def install(**kwargs) -> ScriptedSocket:
        fake = ScriptedSocket(**kwargs)
        monkeypatch.setattr(socket, "create_connection", lambda *a, **k: fake)
        return fake

    return install


@pytest.fixture
def router_script(tmp_path):
    """A router script with a legacy Metropolis stem and synthetic addresses."""
    script = tmp_path / "router_ot_wan.sh"
    script.write_text(
        "configure\n"
        "set interfaces ethernet eth0 address '192.0.2.1/24'\n"
        "set protocols ospf area 0 network '192.0.2.0/24'\n"
        "commit\nsave\nexit\n",
        encoding="utf-8",
    )
    return script


def scripted_interfaces(script: Path) -> bytes:
    expected = expected_interfaces(script)
    return " ".join(sorted({a for group in expected.values() for a in group})).encode()


# -- script reading -----------------------------------------------------------


def test_config_commands_drops_comments_and_blanks(tmp_path):
    script = tmp_path / "router.sh"
    script.write_text(
        "# Comment\nconfigure\n\nset interfaces ethernet eth0 address '192.0.2.1/24'\n"
        "commit\nsave\nexit\n",
        encoding="utf-8",
    )
    commands = config_commands(script)
    assert all(not item.startswith("#") for item in commands)
    assert all(item.strip() for item in commands)
    assert commands == [
        "set interfaces ethernet eth0 address '192.0.2.1/24'",
        "commit",
        "save",
    ]


def test_expected_interfaces_reads_vif_addresses(tmp_path):
    """A trunk interface's subinterface addresses count as its interfaces."""
    script = tmp_path / "router.sh"
    script.write_text(
        "set interfaces ethernet eth1 vif 10 address '192.0.2.1/24'\n",
        encoding="utf-8",
    )
    expected = expected_interfaces(script)
    assert "1" in expected
    assert "192.0.2.1/24" in expected["1"]


# -- console ----------------------------------------------------------------


def test_console_login_waits_for_each_prompt(scripted):
    fake = scripted()
    with Console("127.0.0.1", 1, timeout=1.0) as console:
        console.login("vyos", "vyos")
    # One line per prompt, in order: the username then the password.
    assert fake.sent == [b"vyos\n", b"vyos\n"]


def test_console_expect_raises_on_timeout(scripted):
    """A console that says nothing is a timeout, not a closed connection."""
    # No banner; every read times out.
    scripted(banner=b"")
    with Console("127.0.0.1", 1, timeout=1.0) as console:
        with pytest.raises(RouterError) as caught:
            console.expect(OP_PROMPT, 0.1)
    assert "timed out" in str(caught.value)


def test_console_expect_raises_when_the_console_closes(monkeypatch):
    class Closing(ScriptedSocket):
        def recv(self, size: int) -> bytes:
            return b""

    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: Closing())
    with Console("127.0.0.1", 1, timeout=1.0) as console:
        with pytest.raises(RouterError) as caught:
            console.expect(OP_PROMPT, 1.0)
    assert "closed" in str(caught.value)


def test_console_reports_an_unreachable_port(monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(socket, "create_connection", refuse)
    with pytest.raises(RouterError) as caught:
        Console("127.0.0.1", 1).__enter__()
    assert "could not open a console" in str(caught.value)


# -- applying a configuration -------------------------------------------------


def test_apply_sends_every_command_and_verifies_addresses(scripted, router_script):
    script = router_script
    commands = config_commands(script)

    # The router reports every address the script configures.
    fake = scripted(interfaces=scripted_interfaces(script))

    result = apply_router_config("127.0.0.1", 1, script, boot_timeout=1.0)

    assert result.error is None
    assert result.applied is True
    assert result.commands == len(commands)
    assert result.missing == [], result.missing
    sent = b"".join(fake.sent).decode()
    assert "configure" in sent
    for command in commands:
        assert command in sent


def test_apply_reports_addresses_the_router_does_not_have(scripted, router_script):
    """A config that did not take must be visible, not assumed."""
    script = router_script

    # The router comes back with no interfaces at all.
    scripted(interfaces=b"")
    result = apply_router_config("127.0.0.1", 1, script, boot_timeout=1.0)

    assert result.applied is True
    assert result.missing, "unreported addresses should be listed"


def test_apply_replaces_owned_firewall_and_checks_readback(scripted, router_script):
    script = router_script
    firewall = [
        "set zone-policy zone MET_e0 interface 'eth0'",
        "set firewall name MET_e0_e1 default-action 'drop'",
    ]
    existing = b"\n".join(
        [
            b"set zone-policy zone MET_e0 interface eth0",
            b"set firewall name MET_e0_e1 default-action drop",
            b"set firewall name USER_POLICY default-action accept",
        ]
    )
    fake = scripted(interfaces=scripted_interfaces(script), configuration=existing)
    result = apply_router_config(
        "127.0.0.1",
        1,
        script,
        boot_timeout=1.0,
        firewall_commands=firewall,
    )
    sent = b"".join(fake.sent).decode()
    assert result.error is None
    assert result.missing == []
    assert "delete zone-policy zone MET_e0\n" in sent
    assert "delete firewall name MET_e0_e1\n" in sent
    assert "delete firewall name USER_POLICY" not in sent
    for command in firewall:
        assert command in sent


def test_reconfiguration_removes_only_legacy_metropolis_static_routes(
    scripted, router_script
):
    script = router_script
    existing = b"\n".join(
        [
            b"set protocols static route 10.20.20.0/24 next-hop '10.20.254.9'",
            b"set protocols static route 10.20.30.0/24 next-hop '192.0.2.1'",
            b"set protocols static route 192.0.2.0/24 next-hop '10.20.254.9'",
        ]
    )
    fake = scripted(interfaces=scripted_interfaces(script), configuration=existing)
    result = apply_router_config("127.0.0.1", 1, script, boot_timeout=1.0)
    sent = b"".join(fake.sent).decode()
    assert result.error is None
    assert result.missing == []
    assert "delete protocols static route 10.20.20.0/24 next-hop '10.20.254.9'" in sent
    assert "delete protocols static route 10.20.30.0/24" not in sent
    assert "delete protocols static route 192.0.2.0/24" not in sent


def test_reconfiguration_reports_legacy_route_that_remains(monkeypatch, router_script):
    class IgnoredDeletion(ScriptedSocket):
        def _react(self, command: str) -> None:
            if command.startswith("delete protocols static route "):
                self.pending += self.config_prompt
            else:
                super()._react(command)

    script = router_script
    fake = IgnoredDeletion(
        interfaces=scripted_interfaces(script),
        configuration=b"set protocols static route 10.20.20.0/24 next-hop '10.20.254.9'",
    )
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: fake)
    result = apply_router_config("127.0.0.1", 1, script, boot_timeout=1.0)
    assert result.applied is True
    assert any("legacy static route remains" in item for item in result.missing)


def test_apply_reports_firewall_missing_from_router_readback(scripted, router_script):
    script = router_script
    scripted(interfaces=scripted_interfaces(script), configuration=b"")
    result = apply_router_config(
        "127.0.0.1",
        1,
        script,
        boot_timeout=1.0,
        firewall_commands=["set zone-policy zone MET_e0 interface 'eth0'"],
    )
    assert result.error is None
    assert result.missing == ["firewall MET_e0"]


def test_apply_returns_an_error_when_login_never_completes(scripted, router_script):
    script = router_script
    # The appliance prints a boot message but never reaches a login prompt.
    scripted(banner=b"booting...\n")
    result = apply_router_config("127.0.0.1", 1, script, boot_timeout=0.2)

    assert result.applied is False
    assert result.error is not None
    assert "timed out" in result.error


def test_config_prompt_matches_a_vyos_configuration_prompt():
    assert CONFIG_PROMPT.search(b"vyos@vyos# ")
    assert not CONFIG_PROMPT.search(b"vyos@vyos:~$ ")
