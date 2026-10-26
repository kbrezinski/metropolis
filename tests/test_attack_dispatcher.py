"""Checks for the single-entry-point dispatcher in scripts/attacks.

The dispatcher discovers tools from the directory and forwards arguments to
them, so these tests pin the discovery rules and the forwarding behaviour.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ATTACKS = ROOT / "scripts/attacks"
DISPATCHER = ATTACKS / "run.py"

# Helper modules carry protocol and inventory logic, never a CLI.
HELPERS = {"_common", "_mqtt", "_telnet", "_c2", "run"}


def runnable_names():
    """Discover CLI modules independently of the dispatcher's registry."""
    return {
        path.stem
        for path in ATTACKS.glob("*/*.py")
        if not path.name.startswith("_")
        and 'if __name__ == "__main__":' in path.read_text(encoding="utf-8")
    }


@pytest.fixture
def role_inventory(tmp_path):
    """Small role inventory, independent of the bundled topology."""
    devices = [
        {
            "name": "TELNET-A",
            "address": "10.77.1.30/24",
            "protocols": ["telnet"],
            "environment": {"LEGACY_ROOT_PASSWORD": "test-secret"},
        },
        {"name": "PLC-A", "address": "10.77.1.10/24", "protocols": ["modbus-tcp"]},
        {
            "name": "BROKER-A",
            "address": "10.77.2.10/24",
            "protocols": ["mqtt"],
            "environment": {
                "MQTT_OPERATOR_USERNAME": "operator",
                "MQTT_OPERATOR_PASSWORD": "test-secret",
            },
        },
        {"name": "MIRAI-CNC-A", "address": "10.77.4.10/24", "protocols": ["mirai-cnc"]},
        {
            "name": "MIRAI-LISTENER-A",
            "address": "10.77.4.11/24",
            "protocols": ["mirai-report"],
        },
        {
            "name": "MIRAI-LOADER-A",
            "address": "10.77.4.12/24",
            "protocols": ["payload-delivery"],
            "environment": {"LOADER_KIND": "wget"},
        },
        {"name": "MIRAI-BOT-A", "address": "10.77.4.13/24", "protocols": ["mirai-bot"]},
        {
            "name": "MERLIN-CNC-A",
            "address": "10.77.4.20/24",
            "protocols": ["merlin-control"],
            "environment": {"MERLIN_CONTROL_PORT": "2323", "MERLIN_HTTP_PORT": "8444"},
        },
        {
            "name": "MERLIN-AGENT-A",
            "address": "10.77.4.21/24",
            "protocols": ["merlin-agent"],
            "environment": {"MERLIN_CNC_URL": "http://10.77.4.20:8444"},
        },
    ]
    inventory = tmp_path / "initial_devices.yaml"
    inventory.write_text(
        yaml.safe_dump({"testbed_id": "fixture", "devices": devices}), encoding="utf-8"
    )
    return inventory


def invoke(*args, ack=False):
    env = os.environ.copy()
    env.pop("METROPOLIS_LAB_ACK", None)
    for key in list(env):
        if key.startswith("METROPOLIS_"):
            env.pop(key)
    if ack:
        env["METROPOLIS_LAB_ACK"] = "yes"
    return subprocess.run(
        [sys.executable, str(DISPATCHER), *map(str, args)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def records(result):
    return [
        json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
    ]


def test_dispatcher_lists_runnable_modules_and_no_helpers():
    """Discovery finds the CLI modules and skips the shared helpers."""
    result = invoke("--help")
    assert result.returncode == 0
    listed = {
        line.strip()
        for line in result.stdout.splitlines()
        if line.startswith("  ") and line.strip() and " " not in line.strip()
    }
    assert listed == runnable_names()
    assert listed
    assert not (listed & HELPERS)
    # Every listed tool exists as a module.
    for name in listed:
        assert len(list(ATTACKS.glob(f"*/{name}.py"))) == 1


def test_gotham_mirai_setup_maps_inventory_without_contacting_gns3(role_inventory):
    result = invoke(
        "gotham_mirai_setup",
        "--inventory",
        role_inventory,
        "--external-bot",
        "--dry-run",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    start = records(result)[0]
    assert start["telnet_targets"] == [
        {
            "name": "TELNET-A",
            "address": "10.77.1.30",
            "port": 23,
            "username": "root",
            "credential_source": "LEGACY_ROOT_PASSWORD",
        }
    ]
    assert start["bot"] == {"name": "MIRAI-BOT-A", "address": "10.77.4.13"}
    assert start["bot_source"] == "user_supplied"
    assert start["starts_inert_bot"] is False
    assert start["launches_external_bot"] is False
    default = invoke("gotham_mirai_setup", "--inventory", role_inventory, "--dry-run")
    assert default.returncode == 0
    assert records(default)[0]["bot_source"] == "unspecified"
    assert (
        invoke(
            "gotham_mirai_setup",
            "--inventory",
            role_inventory,
            "--external-bot",
            "--start-inert-bot",
            "--dry-run",
        ).returncode
        == 2
    )
    assert (
        invoke(
            "gotham_mirai_setup",
            "--inventory",
            role_inventory,
            "--target-node",
            "PLC-A",
            "--dry-run",
        ).returncode
        == 2
    )


def test_gotham_mirai_setup_starts_only_the_inert_bot(
    monkeypatch, capsys, role_inventory
):
    if str(ATTACKS) not in sys.path:
        sys.path.insert(0, str(ATTACKS))
    spec = importlib.util.spec_from_file_location(
        "gotham_mirai_setup_test", ATTACKS / "mirai/gotham_mirai_setup.py"
    )
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)

    class Client:
        started = []

        def project_by_name(self, name):
            assert name == "fixture_project"
            return SimpleNamespace(project_id="lab", status="opened")

        def project_nodes(self, project_id):
            assert project_id == "lab"
            return [
                {
                    "name": "TELNET-A",
                    "status": "started",
                    "node_id": "telnet",
                },
                {"name": "MIRAI-CNC-A", "status": "started", "node_id": "cnc"},
                {
                    "name": "MIRAI-LISTENER-A",
                    "status": "started",
                    "node_id": "listener",
                },
                {
                    "name": "MIRAI-LOADER-A",
                    "status": "started",
                    "node_id": "loader",
                },
                {"name": "MIRAI-BOT-A", "status": "stopped", "node_id": "bot"},
            ]

        def start_node(self, project_id, node_id):
            self.started.append((project_id, node_id))

    client = Client()
    monkeypatch.setattr(adapter, "gns3_client", lambda: client)
    monkeypatch.setenv("METROPOLIS_LAB_ACK", "yes")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mirai/gotham_mirai_setup.py",
            "--inventory",
            str(role_inventory),
            "--project",
            "fixture_project",
            "--start-inert-bot",
        ],
    )
    assert adapter.main() == 0
    assert client.started == [("lab", "bot")]
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert any(row["event"] == "inert_bot_start_requested" for row in events)


def test_gotham_merlin_setup_maps_http_contract_without_contacting_gns3(role_inventory):
    result = invoke(
        "gotham_merlin_setup",
        "--inventory",
        role_inventory,
        "--external-agent",
        "--dry-run",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    start = records(result)[0]
    assert start["cnc"] == {"name": "MERLIN-CNC-A", "address": "10.77.4.20"}
    assert start["operator_console"] == {
        "host": "10.77.4.20",
        "port": 2323,
        "transport": "tcp",
    }
    assert start["agent_channel"]["url"] == "http://10.77.4.20:8444"
    assert start["agent_channel"]["transport"] == "http_without_tls"
    assert start["agent_source"] == "user_supplied"
    assert start["starts_inert_agent"] is False
    assert start["launches_external_agent"] is False
    default = invoke("gotham_merlin_setup", "--inventory", role_inventory, "--dry-run")
    assert default.returncode == 0
    assert records(default)[0]["agent_source"] == "unspecified"
    assert (
        invoke(
            "gotham_merlin_setup",
            "--inventory",
            role_inventory,
            "--external-agent",
            "--start-inert-agent",
            "--dry-run",
        ).returncode
        == 2
    )


def test_gotham_merlin_setup_starts_only_the_example_agent(
    monkeypatch, capsys, role_inventory
):
    if str(ATTACKS) not in sys.path:
        sys.path.insert(0, str(ATTACKS))
    spec = importlib.util.spec_from_file_location(
        "gotham_merlin_setup_test", ATTACKS / "merlin/gotham_merlin_setup.py"
    )
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)

    class Client:
        started = []

        def project_by_name(self, name):
            assert name == "fixture_project"
            return SimpleNamespace(project_id="lab", status="opened")

        def project_nodes(self, project_id):
            assert project_id == "lab"
            return [
                {"name": "MERLIN-CNC-A", "status": "started", "node_id": "cnc"},
                {
                    "name": "MERLIN-AGENT-A",
                    "status": "stopped",
                    "node_id": "agent",
                },
            ]

        def start_node(self, project_id, node_id):
            self.started.append((project_id, node_id))

    client = Client()
    monkeypatch.setattr(adapter, "gns3_client", lambda: client)
    monkeypatch.setenv("METROPOLIS_LAB_ACK", "yes")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "merlin/gotham_merlin_setup.py",
            "--inventory",
            str(role_inventory),
            "--project",
            "fixture_project",
            "--start-inert-agent",
        ],
    )
    assert adapter.main() == 0
    assert client.started == [("lab", "agent")]
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert any(row["event"] == "inert_agent_start_requested" for row in events)


def test_dispatcher_rejects_unknown_tools_with_a_suggestion():
    """An unknown tool fails with exit 2 and a close-match suggestion."""
    result = invoke("mqtt_brute", "--dry-run")
    assert result.returncode == 2
    assert "unknown tool" in result.stderr
    assert "mqtt_bruteforce_attack" in result.stderr


def test_dispatcher_preserves_the_lab_acknowledgement_gate(role_inventory):
    """The gate lives in the tools, and dispatch must not bypass it."""
    assert invoke("mqtt_bruteforce_attack").returncode == 2
    with_ack = invoke(
        "mqtt_bruteforce_attack", "--inventory", role_inventory, "--dry-run", ack=True
    )
    assert with_ack.returncode == 0


@pytest.mark.parametrize("name", ["hping3_attack", "coap_experiment_report"])
def test_cli_parser_variants_respond_to_help(name):
    result = invoke(name, "--help")
    assert result.returncode == 0, f"{name}: {result.stdout}{result.stderr}"
    if name != "coap_experiment_report":
        assert "--gotham-script" in result.stdout


def test_gotham_script_keeps_acknowledgement_gate(tmp_path):
    script = tmp_path / "gotham.sh"
    script.write_text("exit 9\n", encoding="utf-8")
    result = invoke("hping3_attack", "--gotham-script", script)
    assert result.returncode == 2
    assert "METROPOLIS_LAB_ACK" in result.stdout


def test_selected_gotham_script_runs_once_and_returns_its_status(tmp_path, monkeypatch):
    if str(ATTACKS) not in sys.path:
        sys.path.insert(0, str(ATTACKS))
    from _common import AttackArgumentParser

    script = tmp_path / "gotham.sh"
    script.write_text("exit 7\n", encoding="utf-8")
    calls = []
    monkeypatch.setenv("METROPOLIS_LAB_ACK", "yes")
    monkeypatch.setattr("_common.shutil.which", lambda name: "/bin/bash")

    def fake_run(command, cwd, check):
        calls.append((command, cwd, check))
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr("_common.subprocess.run", fake_run)
    options = AttackArgumentParser()
    options.add_argument("--dry-run", action="store_true")
    options.add_argument("--output", type=Path)
    with pytest.raises(SystemExit) as stopped:
        options.parse_args(["--gotham-script", str(script), "--gotham-arg", "target"])
    assert stopped.value.code == 7
    assert calls == [(["/bin/bash", str(script), "target"], script.parent, False)]
