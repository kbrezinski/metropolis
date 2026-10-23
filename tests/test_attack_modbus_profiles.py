"""Modbus action maps must follow the selected controller, not one fixed PLC."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/attacks/run.py"


@pytest.fixture
def dataset(tmp_path):
    root = tmp_path / "custom_v1"
    inventory = root / "device_instances/initial_devices.yaml"
    inventory.parent.mkdir(parents=True)
    roles = [
        ("INTAKE", {"CELL_ROLE": "intake"}),
        ("DOSING", {"CELL_ROLE": "dosing"}),
        ("FILTRATION", {"CELL_ROLE": "filtration"}),
        ("DISINFECTION", {"CELL_ROLE": "disinfection"}),
        (
            "RESERVOIR",
            {
                "DEVICE_ROLE": "RTU",
                "CELL_ROLE": "remote_site",
                "REMOTE_SITE_ROLE": "reservoir",
            },
        ),
        (
            "RAW-WATER",
            {
                "DEVICE_ROLE": "RTU",
                "CELL_ROLE": "remote_site",
                "REMOTE_SITE_ROLE": "raw_water",
            },
        ),
    ]
    devices = []
    for index, (name, environment) in enumerate(roles, start=10):
        devices.append(
            {
                "name": name,
                "address": f"10.77.1.{index}/24",
                "protocols": ["modbus-tcp"],
                "environment": {"DEVICE_ROLE": "PLC", **environment},
            }
        )
    inventory.write_text(
        yaml.safe_dump({"testbed_id": "custom", "devices": devices}), encoding="utf-8"
    )
    profile = root / "protocol_profiles/modbus_actions_v1.yaml"
    profile.parent.mkdir()
    shutil.copyfile(
        ROOT
        / "testbeds/metropolis/datasets/water_treatment_v1/protocol_profiles/modbus_actions_v1.yaml",
        profile,
    )
    return root


def invoke(dataset, *arguments):
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith("METROPOLIS_"):
            environment.pop(key)
    return subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "modbus_manipulation_attack",
            "--dataset",
            str(dataset),
            *arguments,
            "--dry-run",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )


def started(result):
    assert result.returncode == 0, result.stdout + result.stderr
    return next(
        json.loads(line)
        for line in result.stdout.splitlines()
        if line.startswith("{") and json.loads(line).get("event") == "started"
    )


@pytest.mark.parametrize(
    ("node", "action", "profile", "area", "address", "meaning"),
    [
        ("INTAKE", "set-flow", "intake", "holding-register", 1, "process_flow_x10"),
        (
            "DOSING",
            "set-dose-setpoint",
            "dosing",
            "holding-register",
            4,
            "dose_setpoint_x10_percent",
        ),
        (
            "FILTRATION",
            "set-backwash-threshold",
            "filtration",
            "holding-register",
            4,
            "backwash_threshold_x10_kpa",
        ),
        (
            "DISINFECTION",
            "set-residual-target",
            "disinfection",
            "holding-register",
            4,
            "residual_target_x10_mg_per_l",
        ),
        ("RESERVOIR", "force-pump", "reservoir", "coil", 0, "remote_pump_request"),
        (
            "RAW-WATER",
            "set-level",
            "raw_water",
            "holding-register",
            0,
            "remote_level_x10_percent",
        ),
    ],
)
def test_action_follows_selected_cell_profile(
    dataset, node, action, profile, area, address, meaning
):
    record = started(
        invoke(dataset, "--node", node, "--action", action, "--value", "400")
    )
    assert (
        record["profile"],
        record["area"],
        record["address"],
        record["meaning"],
    ) == (
        profile,
        area,
        address,
        meaning,
    )
    assert record["profile_version"] == "1.0.0"
    assert record["scenario_variant"] == {
        "action": action,
        "profile": profile,
        "profile_version": "1.0.0",
    }
    assert len(record["profile_sha256"]) == 64


def test_wrong_cell_action_fails_before_traffic(dataset):
    result = invoke(dataset, "--node", "FILTRATION", "--action", "set-quality")
    assert result.returncode == 2
    assert "not defined for filtration" in result.stdout


def test_custom_plc_without_cell_role_needs_explicit_profile(dataset):
    inventory = dataset / "device_instances/initial_devices.yaml"
    document = yaml.safe_load(inventory.read_text(encoding="utf-8"))
    document["devices"][0]["name"] = "MET-PLC-INTAKE-01"
    document["devices"][0]["environment"].pop("CELL_ROLE")
    inventory.write_text(yaml.safe_dump(document), encoding="utf-8")
    result = invoke(dataset, "--node", "MET-PLC-INTAKE-01", "--action", "set-level")
    assert result.returncode == 2
    assert "use --profile" in result.stdout
    record = started(
        invoke(
            dataset,
            "--node",
            "MET-PLC-INTAKE-01",
            "--profile",
            "intake",
            "--action",
            "set-level",
        )
    )
    assert record["profile"] == "intake"


def test_explicit_unlisted_host_needs_a_profile(dataset):
    result = invoke(
        dataset, "--host", "10.77.9.40", "--port", "1502", "--action", "set-level"
    )
    assert result.returncode == 2
    assert "--profile" in result.stdout
    record = started(
        invoke(
            dataset,
            "--host",
            "10.77.9.40",
            "--port",
            "1502",
            "--profile",
            "intake",
            "--action",
            "set-level",
        )
    )
    assert record["profile"] == "intake"
    assert record["host"] == "10.77.9.40"
    assert record["node"] is None
    assert record["node_attribution"] == "unverified"


def test_host_override_resolves_to_its_inventoried_node(dataset):
    record = started(
        invoke(
            dataset,
            "--host",
            "10.77.1.11",
            "--action",
            "set-dose-setpoint",
            "--value",
            "600",
        )
    )
    assert record["node"] == "DOSING"
    assert record["profile"] == "dosing"
    assert record["node_attribution"] == "inventory"


def test_node_and_host_cannot_identify_different_inventory_targets(dataset):
    result = invoke(
        dataset,
        "--node",
        "INTAKE",
        "--host",
        "10.77.1.11",
        "--profile",
        "dosing",
        "--action",
        "set-dose-setpoint",
    )
    assert result.returncode == 2
    assert "belongs to DOSING" in result.stdout
    assert '"event": "started"' not in result.stdout


def test_selected_node_cannot_claim_another_cell_profile(dataset):
    result = invoke(
        dataset,
        "--node",
        "DOSING",
        "--profile",
        "intake",
        "--action",
        "set-level",
    )
    assert result.returncode == 2
    assert "disagrees" in result.stdout


def test_custom_dataset_profile_is_used_without_code_changes(dataset):
    inventory = dataset / "device_instances/initial_devices.yaml"
    document = yaml.safe_load(inventory.read_text(encoding="utf-8"))
    document["devices"][0]["environment"]["CELL_ROLE"] = "custom_cell"
    inventory.write_text(yaml.safe_dump(document), encoding="utf-8")
    profile = dataset / "protocol_profiles/modbus_actions_v1.yaml"
    profile.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "profile_version": "custom-v2",
                "profiles": {
                    "custom_cell": {
                        "actions": {
                            "set-threshold": {
                                "area": "holding-register",
                                "address": 9,
                                "value": "argument",
                                "repeat": False,
                                "meaning": "custom_threshold",
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    record = started(
        invoke(
            dataset, "--node", "INTAKE", "--action", "set-threshold", "--value", "70"
        )
    )
    assert record["profile"] == "custom_cell"
    assert record["profile_version"] == "custom-v2"
    assert record["profile_file"] == str(profile)
    assert record["address"] == 9


def test_invalid_profile_version_is_reported(dataset):
    profile = dataset / "protocol_profiles/modbus_actions_v1.yaml"
    profile.write_text("schema_version: '2.0'\n", encoding="utf-8")
    result = invoke(dataset, "--node", "INTAKE", "--action", "set-level")
    assert result.returncode == 2
    assert "schema version 1.0" in result.stdout


def test_custom_dataset_without_action_profile_is_rejected(dataset):
    (dataset / "protocol_profiles/modbus_actions_v1.yaml").unlink()
    result = invoke(dataset, "--node", "INTAKE", "--action", "set-level")
    assert result.returncode == 2
    assert "supply --profile-file" in result.stdout
