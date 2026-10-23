"""Attack actions retain their run, scenario, capture, and source context."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
ATTACKS = ROOT / "scripts/attacks"
RUNNER = ATTACKS / "run.py"
sys.path.insert(0, str(ATTACKS))

from _common import Run, parser  # noqa: E402
from _scenario import catalog  # noqa: E402


@pytest.fixture
def dataset(tmp_path):
    root = tmp_path / "custom_v1"
    inventory_file = root / "device_instances/initial_devices.yaml"
    links_file = root / "topology/links.yaml"
    inventory_file.parent.mkdir(parents=True)
    links_file.parent.mkdir(parents=True)
    inventory_file.write_text(
        yaml.safe_dump(
            {
                "testbed_id": "custom",
                "devices": [
                    {
                        "name": "PLC-A",
                        "address": "10.77.1.10/24",
                        "protocols": ["modbus-tcp"],
                        "environment": {"DEVICE_ROLE": "PLC"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    links_file.write_text(
        yaml.safe_dump({"links": [{"id": "attach-plc-a"}, {"id": "router-uplink"}]}),
        encoding="utf-8",
    )
    return root


def parse_hping(monkeypatch, dataset, *arguments):
    monkeypatch.setattr(sys, "argv", ["hping3_attack.py"])
    return parser("test attack", "PLC-A").parse_args(
        ["--dataset", str(dataset), *arguments]
    )


def test_bound_actions_are_written_incrementally_with_shared_context(
    dataset, monkeypatch, capsys
):
    run_dir = dataset / "runs" / "run_001"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run_001",
                "status": "running",
                "selected_capture_links": ["attach-plc-a", "router-uplink"],
            }
        ),
        encoding="utf-8",
    )
    args = parse_hping(
        monkeypatch,
        dataset,
        "--run-id",
        "run_001",
        "--capture-link",
        "attach-plc-a",
        "--capture-link",
        "router-uplink",
    )
    monkeypatch.setenv("METROPOLIS_LAB_ACK", "yes")
    action_path = run_dir / "attacks.jsonl"
    with Run(args, "bounded_hping3_style", host="10.77.1.10") as run:
        run.emit("packet_sent", sequence=1)
        during_run = [json.loads(line) for line in action_path.read_text().splitlines()]
        assert [record["event"] for record in during_run] == [
            "started",
            "packet_sent",
        ]
    recorded = [json.loads(line) for line in action_path.read_text().splitlines()]
    event_schema = json.loads((ROOT / "schemas/attack-event.schema.json").read_text())
    validator = Draft202012Validator(event_schema, format_checker=FormatChecker())
    for record in recorded:
        validator.validate(record)
    assert [record["event"] for record in recorded] == [
        "started",
        "packet_sent",
        "finished",
    ]
    assert recorded[1]["sequence"] == 1
    for record in recorded:
        assert record["run_id"] == "run_001"
        assert record["scenario_id"] == "hping3.traffic.v1"
        assert record["scenario_catalog_version"] == "1.0"
        assert isinstance(record["scenario_variant"], dict)
        assert record["declared_capture_links"] == ["attach-plc-a", "router-uplink"]
        assert record["traffic_origin"]["kind"] == "invoking_host"
        assert record["traffic_origin"]["execution_host"] == "invoking_host"
        assert record["traffic_origin"]["execution_hostname"]
        assert record["traffic_origin"]["source_verification"] == "check_pcap"
        assert datetime.fromisoformat(record["timestamp"]).utcoffset() == timedelta(0)
    assert len(capsys.readouterr().out.splitlines()) == len(recorded)


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        (["--run-id", "run_001"], "requires at least one --capture-link"),
        (["--run-id", "missing", "--capture-link", "attach-plc-a"], "does not exist"),
        (
            ["--run-id", "run_001", "--capture-link", "unknown"],
            "unknown declared capture",
        ),
        (
            ["--capture-link", "attach-plc-a", "--capture-link", "attach-plc-a"],
            "must be unique",
        ),
        (
            ["--run-id", "../bad", "--capture-link", "attach-plc-a"],
            "--run-id may contain",
        ),
        (["--execution-ip", "8.8.8.8"], "--execution-ip"),
    ],
)
def test_invalid_run_or_capture_binding_fails_closed(
    dataset, monkeypatch, capsys, arguments, error
):
    run_dir = dataset / "runs" / "run_001"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"run_id": "run_001", "status": "complete"}), encoding="utf-8"
    )
    with pytest.raises(SystemExit) as stopped:
        parse_hping(monkeypatch, dataset, *arguments)
    assert stopped.value.code == 2
    assert error in capsys.readouterr().err
    assert not (run_dir / "attacks.jsonl").exists()


def test_completed_run_cannot_receive_live_actions(dataset, monkeypatch, capsys):
    run_dir = dataset / "runs" / "run_001"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"run_id": "run_001", "status": "complete"}), encoding="utf-8"
    )
    with pytest.raises(SystemExit) as stopped:
        parse_hping(
            monkeypatch,
            dataset,
            "--run-id",
            "run_001",
            "--capture-link",
            "attach-plc-a",
        )
    assert stopped.value.code == 2
    assert "running experiment bundle" in capsys.readouterr().err


def test_live_actions_require_capture_points_selected_by_experiment(
    dataset, monkeypatch, capsys
):
    run_dir = dataset / "runs" / "run_001"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run_001",
                "status": "running",
                "selected_capture_links": ["router-uplink"],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as stopped:
        parse_hping(
            monkeypatch,
            dataset,
            "--run-id",
            "run_001",
            "--capture-link",
            "attach-plc-a",
        )
    assert stopped.value.code == 2
    assert "selected capture links" in capsys.readouterr().err
    assert not (run_dir / "attacks.jsonl").exists()


def test_bound_actions_cannot_write_outside_the_run_bundle(
    dataset, monkeypatch, capsys, tmp_path
):
    run_dir = dataset / "runs" / "run_001"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run_001",
                "status": "running",
                "selected_capture_links": ["attach-plc-a"],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as stopped:
        parse_hping(
            monkeypatch,
            dataset,
            "--run-id",
            "run_001",
            "--capture-link",
            "attach-plc-a",
            "--output",
            str(tmp_path / "elsewhere.jsonl"),
        )
    assert stopped.value.code == 2
    assert "experiment's attacks.jsonl" in capsys.readouterr().err


def test_dry_run_can_describe_an_unbound_future_run(dataset):
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith("METROPOLIS_"):
            environment.pop(key)
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "hping3_attack",
            "--dataset",
            str(dataset),
            "--run-id",
            "planned_001",
            "--capture-link",
            "attach-plc-a",
            "--mode",
            "udp-generic",
            "--execution-ip",
            "10.77.1.50",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert records[0]["run_id"] == "planned_001"
    assert records[0]["declared_capture_links"] == ["attach-plc-a"]
    assert records[0]["scenario_id"] == "hping3.traffic.v1"
    assert records[0]["scenario_variant"]["mode"] == "udp-generic"
    assert records[0]["traffic_origin"]["reported_execution_ip"] == "10.77.1.50"
    assert not (dataset / "runs" / "planned_001").exists()


def test_dry_run_does_not_append_to_a_completed_experiment(dataset, monkeypatch):
    run_dir = dataset / "runs" / "run_001"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run_001",
                "status": "complete",
                "selected_capture_links": ["attach-plc-a"],
            }
        ),
        encoding="utf-8",
    )
    action_file = run_dir / "attacks.jsonl"
    action_file.write_text("existing\n", encoding="utf-8")
    args = parse_hping(
        monkeypatch,
        dataset,
        "--run-id",
        "run_001",
        "--capture-link",
        "attach-plc-a",
        "--dry-run",
    )
    with Run(args, "bounded_hping3_style"):
        pass
    assert action_file.read_text(encoding="utf-8") == "existing\n"


def test_external_shell_dry_run_records_provenance_without_executing(dataset, tmp_path):
    script = tmp_path / "external.sh"
    script.write_text("exit 9\n", encoding="utf-8")
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith("METROPOLIS_"):
            environment.pop(key)
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "hping3_attack",
            "--dataset",
            str(dataset),
            "--gotham-script",
            str(script),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    start = json.loads(result.stdout.splitlines()[0])
    assert start["external_script"]["path"] == str(script.resolve())
    assert (
        start["external_script"]["sha256"]
        == hashlib.sha256(script.read_bytes()).hexdigest()
    )
    assert "user-supplied" in start["external_script"]["provenance"]
    assert start["traffic_origin"]["kind"] == "external_script"
    assert start["traffic_origin"]["packet_source"] == "unverified"
    assert start["scenario_id"] == "external.shell.v1"
    assert (
        start["scenario_variant"]["external_script_sha256"]
        == start["external_script"]["sha256"]
    )


def test_versioned_scenario_catalog_matches_attack_tool(tmp_path):
    catalog_document = json.loads((ATTACKS / "scenarios/v1.json").read_text())
    catalog_schema = json.loads(
        (ROOT / "schemas/attack-scenarios.schema.json").read_text()
    )
    Draft202012Validator(catalog_schema).validate(catalog_document)
    mapped = catalog()
    assert mapped["hping3_attack"]["id"] == "hping3.traffic.v1"
    assert mapped["modbus_manipulation_attack"]["family"] == "modbus"
    assert all(item["id"].endswith(".v1") for item in mapped.values())
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "scenarios": [mapped["hping3_attack"], mapped["hping3_attack"]],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        catalog(duplicate)
