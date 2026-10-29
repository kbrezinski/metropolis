"""Loopback integration checks for the synthetic Mirai and Merlin lifecycles.

These run the real device models in-process on loopback ports, then drive the
choreography drivers against them. No testbed deployment is required.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ATTACKS = ROOT / "scripts/attacks"
MIRAI = ROOT / "testbeds/metropolis/devices/attack/mirai"
MERLIN = ROOT / "testbeds/metropolis/devices/attack/merlin"


def invoke(name, *args, ack=True):
    env = os.environ.copy()
    env.pop("METROPOLIS_LAB_ACK", None)
    for key in list(env):
        if key.startswith("METROPOLIS_"):
            env.pop(key)
    if ack:
        env["METROPOLIS_LAB_ACK"] = "yes"
    return subprocess.run(
        [sys.executable, str(ATTACKS / "run.py"), Path(name).stem, *map(str, args)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def load_file(name, path):
    """Load a model module by path.

    The module is registered in ``sys.modules`` while it executes because these
    models declare dataclasses under ``from __future__ import annotations``, and
    dataclasses resolves those string annotations through the module namespace.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    return module


def test_mirai_cnc_registers_bots_and_records_commands():
    """The CNC model enrols bots and records commands sent by an operator."""
    import socket as socket_module

    cnc = load_file("mirai_cnc_model", MIRAI / "cnc/start_cnc.py")
    registry = cnc.Registry()
    bot = registry.check_in("10.77.4.100", "linux-x64-synthetic")
    assert bot.bot_id == "bot-0001"
    # A repeat check-in from the same address is the same bot, not a new one.
    assert registry.check_in("10.77.4.100", "linux-x64-synthetic") is bot
    assert len(registry.as_rows()) == 1

    # Drive the operator console over a real socket pair.
    operator_end, server_end = socket_module.socketpair()
    try:
        worker = threading.Thread(
            target=cnc.handle_operator, args=(registry, server_end), daemon=True
        )
        worker.start()
        operator_end.settimeout(5)
        stream = operator_end.makefile("rwb")
        assert b"synthetic lab console" in stream.readline()
        for command in (f"use {bot.bot_id}", "run echo synthetic", "bots"):
            stream.write(command.encode() + b"\r\n")
            stream.flush()
            if command == "bots":
                row = json.loads(stream.readline())
                assert row["bot_id"] == bot.bot_id
                assert row["commands"] == 1
                assert stream.readline().strip() == b"END"
            else:
                stream.readline()
    finally:
        operator_end.close()
        server_end.close()
    assert bot.commands == ["echo synthetic"]


def test_mirai_loader_stub_is_inert_text():
    """The bundled loader marker is text, not an executable payload."""
    loader = load_file("mirai_loader_model", MIRAI / "loader/start_loader.py")
    assert loader.STUB.startswith(b"metropolis-synthetic-payload-stub")
    # No ELF or PE magic: the stub cannot be executable content.
    assert not loader.STUB.startswith(b"\x7fELF")
    assert not loader.STUB.startswith(b"MZ")


def test_mirai_bot_targets_are_private_only():
    """The bot refuses targets outside the private lab range."""
    bot = load_file("mirai_bot_model", MIRAI / "bot/start_bot.py")
    assert bot.parse_targets("10.77.4.10,10.77.4.11:23") == [
        ("10.77.4.10", 23),
        ("10.77.4.11", 23),
    ]
    with pytest.raises(ValueError):
        bot.parse_targets("8.8.8.8:23")
    assert bot.parse_targets("") == []


def test_merlin_agent_refuses_commands_outside_the_allow_list():
    """A delivered command is simulated only when it is in the allow-list."""
    agent = load_file("merlin_agent_model", MERLIN / "agent/start_merlin_agent.py")
    ok, detail = agent.run_command("chmod 775 /opt/metropolis")
    assert ok and "simulated" in detail
    refused, reason = agent.run_command("rm -rf /")
    assert not refused and reason.startswith("refused:")
    assert agent.parse_url("http://10.77.4.20:8443") == ("10.77.4.20", 8443)
    assert agent.parse_url("10.77.4.20") == ("10.77.4.20", 8443)


def test_merlin_agent_reports_marker_permission_failure(monkeypatch):
    agent = load_file(
        "merlin_agent_permissions", MERLIN / "agent/start_merlin_agent.py"
    )

    class Marker:
        def __str__(self):
            return "/tmp/marker"

        def is_file(self):
            return True

        def chmod(self, _mode):
            raise OSError("permission denied")

    monkeypatch.setattr(agent, "INERT_DESTINATION", Marker())
    ok, detail = agent.run_command("chmod 775 /tmp/marker")
    assert not ok
    assert "permission denied" in detail


def test_merlin_cnc_listener_and_upload_lifecycle():
    """The CNC model tracks listener state, agents, and uploaded artefacts."""
    cnc = load_file("merlin_cnc_model", MERLIN / "cnc/start_merlin_cnc.py")
    state = cnc.State()
    assert state.listener_info()["state"] == "stopped"
    state.start_listener("https", "0.0.0.0")
    assert state.listener_info()["scheme"] == "https"

    agent = state.check_in("10.77.4.21", "linux-x64-synthetic")
    assert state.check_in("10.77.4.21", "linux-x64-synthetic") is agent
    state.add_command(agent, "chmod 775 /opt/metropolis")
    assert state.take_commands(agent.agent_id) == ["chmod 775 /opt/metropolis"]
    assert state.take_commands(agent.agent_id) == []

    state.add_upload(agent.agent_id, "payload.bin", b"inert artefact")
    assert state.uploads[0]["bytes"] == len(b"inert artefact")


def test_choreography_requires_the_lab_acknowledgement():
    """Neither driver sends traffic without the explicit lab acknowledgement."""
    for name in ("mirai/mirai_scenario.py", "merlin/merlin_scenario.py"):
        result = invoke(name, ack=False)
        assert result.returncode == 2
        assert "METROPOLIS_LAB_ACK" in result.stdout
