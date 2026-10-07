"""Loopback integration checks for the synthetic Mirai and Merlin lifecycles.

These run the real device models in-process on loopback ports, then drive the
choreography drivers against them. No testbed deployment is required.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socketserver
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
        [sys.executable, str(ATTACKS / name), *map(str, args)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def records(result):
    return [
        json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
    ]


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


@pytest.fixture
def peers():
    """Start in-process model servers on loopback ports."""
    servers = []

    def start(handler):
        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        server = Server(("127.0.0.1", 0), handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        servers.append((server, worker))
        return server.server_address[1]

    yield start
    for server, worker in servers:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_mirai_cnc_registers_bots_and_records_commands():
    """The CNC model enrols bots and records commands sent by an operator."""
    import socket as socket_module

    cnc = load_file("mirai_cnc_model", MIRAI / "cnc/start_cnc.py")
    registry = cnc.Registry()
    bot = registry.check_in("10.99.10.100", "linux-x64-synthetic")
    assert bot.bot_id == "bot-0001"
    # A repeat check-in from the same address is the same bot, not a new one.
    assert registry.check_in("10.99.10.100", "linux-x64-synthetic") is bot
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


def test_mirai_scan_listener_records_json_reports(peers, capsys):
    """The scan listener emits one structured event per bot report."""
    listener = load_file(
        "mirai_listener_model", MIRAI / "scan_listener/start_listener.py"
    )
    payload = {"bot_id": "bot-0001", "vulnerable": "10.99.10.100:23"}
    listener.record(json.dumps(payload), ("127.0.0.1", 40000))
    event = json.loads(capsys.readouterr().out.strip())
    assert event["event"] == "bot_report"
    assert event["source"] == "127.0.0.1"
    assert event["payload"] == payload


def test_mirai_loader_serves_only_an_inert_stub(peers):
    """The delivery path works while the payload stays non-executable text."""
    loader = load_file("mirai_loader_model", MIRAI / "loader/start_loader.py")
    assert loader.STUB.startswith(b"metropolis-synthetic-payload-stub")
    # No ELF or PE magic: the stub cannot be executable content.
    assert not loader.STUB.startswith(b"\x7fELF")
    assert not loader.STUB.startswith(b"MZ")


def test_mirai_bot_targets_are_private_only():
    """The bot refuses targets outside the private lab range."""
    bot = load_file("mirai_bot_model", MIRAI / "bot/start_bot.py")
    assert bot.parse_targets("10.99.10.10,10.99.10.11:23") == [
        ("10.99.10.10", 23),
        ("10.99.10.11", 23),
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
    assert agent.parse_url("http://10.99.10.20:8443") == ("10.99.10.20", 8443)
    assert agent.parse_url("10.99.10.20") == ("10.99.10.20", 8443)


def test_merlin_cnc_listener_and_upload_lifecycle():
    """The CNC model tracks listener state, agents, and uploaded artefacts."""
    cnc = load_file("merlin_cnc_model", MERLIN / "cnc/start_merlin_cnc.py")
    state = cnc.State()
    assert state.listener_info()["state"] == "stopped"
    state.start_listener("https", "0.0.0.0")
    assert state.listener_info()["scheme"] == "https"

    agent = state.check_in("10.99.10.21", "linux-x64-synthetic")
    assert state.check_in("10.99.10.21", "linux-x64-synthetic") is agent
    state.add_command(agent, "chmod 775 /opt/metropolis")
    assert state.take_commands(agent.agent_id) == ["chmod 775 /opt/metropolis"]
    assert state.take_commands(agent.agent_id) == []

    state.add_upload(agent.agent_id, "payload.bin", b"inert artefact")
    assert state.uploads[0]["bytes"] == len(b"inert artefact")


@pytest.mark.parametrize(
    "name,args",
    [
        ("run_mirai_choreography.py", []),
        ("run_merlin.py", []),
    ],
)
def test_choreography_dry_runs_resolve_inventory(name, args):
    """Both drivers resolve every node without sending traffic."""
    result = invoke(name, *args, "--dry-run", ack=False)
    assert result.returncode == 0, result.stdout + result.stderr
    start = records(result)[0]
    assert start["dry_run"] is True
    if name == "run_mirai_choreography.py":
        assert start["cnc"] == "10.99.10.10:23"
        assert start["listener"] == "10.99.10.11:48101"
        assert start["loader"] == "10.99.10.13:80"
    else:
        assert start["cnc"] == "10.99.10.20:23"
        assert start["http"] == "10.99.10.20:8443"
        assert start["agent"] == "10.99.10.21"


def test_choreography_requires_the_lab_acknowledgement():
    """Neither driver sends traffic without the explicit lab acknowledgement."""
    assert invoke("run_mirai_choreography.py", ack=False).returncode == 2
    assert invoke("run_merlin.py", ack=False).returncode == 2


def test_choreography_rejects_unknown_nodes():
    """An unknown node name fails during resolution, before any traffic."""
    result = invoke(
        "run_mirai_choreography.py", "--cnc-node", "MET-NOPE-01", "--dry-run"
    )
    assert result.returncode == 2
    assert "not in the inventory" in result.stdout
