"""The Mirai scenario gates synthetic control traffic on verified Telnet access."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ATTACKS = ROOT / "scripts/attacks"
sys.path.insert(0, str(ATTACKS))
sys.path.insert(0, str(ATTACKS / "mirai"))

spec = importlib.util.spec_from_file_location(
    "mirai_scenario_test", ATTACKS / "mirai/mirai_scenario.py"
)
scenario = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scenario)


def events(output):
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


@pytest.fixture
def scenario_inventory(tmp_path):
    devices = [
        {
            "name": "TELNET-A",
            "address": "10.77.1.30/24",
            "protocols": ["telnet"],
            "environment": {"LEGACY_ROOT_PASSWORD": "test-secret"},
        },
        {
            "name": "CNC-A",
            "address": "10.77.4.10/24",
            "protocols": ["mirai-cnc"],
        },
        {
            "name": "LISTENER-A",
            "address": "10.77.4.11/24",
            "protocols": ["mirai-report"],
        },
        {
            "name": "BOT-A",
            "address": "10.77.4.100/24",
            "protocols": ["mirai-bot"],
            "environment": {
                "MIRAI_CNC_HOST": "10.77.4.10",
                "MIRAI_CNC_PORT": "48101",
            },
        },
        {
            "name": "LOADER-A",
            "address": "10.77.4.12/24",
            "protocols": ["payload-delivery"],
            "environment": {"LOADER_KIND": "wget"},
        },
    ]
    inventory = tmp_path / "initial_devices.yaml"
    inventory.write_text(
        yaml.safe_dump({"testbed_id": "fixture", "devices": devices}), encoding="utf-8"
    )
    return inventory


def test_verified_telnet_target_drives_only_its_simulated_identity(
    monkeypatch, capsys, tmp_path, scenario_inventory
):
    attempts = []
    played = []

    class FakeSession:
        def __init__(self, host, port, timeout):
            assert host == "10.77.1.30"
            attempts.append(port)

        def login(self, username, password):
            assert username == "root"
            if len(attempts) == 1:
                raise scenario.Rejected("wrong")

        def prove_command(self, token, write_marker):
            assert not write_marker
            return True

        def close(self):
            pass

    def fake_choreography(run, **kwargs):
        played.append(kwargs)
        run.emit("bot_checkin_observed", address=kwargs["bot_host"])
        return 1

    output = tmp_path / "scenario.jsonl"
    monkeypatch.setattr(scenario, "Session", FakeSession)
    monkeypatch.setattr(scenario, "play_choreography", fake_choreography)
    monkeypatch.setattr(scenario.time, "sleep", lambda _: None)
    monkeypatch.setenv("METROPOLIS_LAB_ACK", "yes")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mirai_scenario.py",
            "--inventory",
            str(scenario_inventory),
            "--output",
            str(output),
        ],
    )
    assert scenario.main() == 0
    records = events(capsys.readouterr().out)
    assert len(attempts) == 2
    assert played[0]["verified_targets"] == [("10.77.1.30", 23)]
    assert played[0]["bot_host"] == "10.77.4.100"
    assert all(
        row["source"] == "invoking_host"
        for row in records
        if row["event"] == "telnet_attempt"
    )
    assert any(row["event"] == "simulation_transition" for row in records)
    assert events(output.read_text(encoding="utf-8")) == records
    assert "LabOnly_Legacy_2026" not in output.read_text(encoding="utf-8")


def test_failed_telnet_stage_skips_control_traffic(
    monkeypatch, capsys, scenario_inventory
):
    class RejectingSession:
        def __init__(self, host, port, timeout):
            pass

        def login(self, username, password):
            raise scenario.Rejected("wrong")

        def close(self):
            pass

    monkeypatch.setattr(scenario, "Session", RejectingSession)
    monkeypatch.setattr(
        scenario,
        "play_choreography",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("called")),
    )
    monkeypatch.setattr(scenario.time, "sleep", lambda _: None)
    monkeypatch.setenv("METROPOLIS_LAB_ACK", "yes")
    monkeypatch.setattr(
        sys, "argv", ["mirai_scenario.py", "--inventory", str(scenario_inventory)]
    )
    assert scenario.main() == 1
    summary = next(
        row for row in events(capsys.readouterr().out) if row["event"] == "summary"
    )
    assert summary["control_stage"] == "skipped"
