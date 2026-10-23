"""Attack targets follow a selected dataset instead of legacy node names."""

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


def device(name, address, protocols, **environment):
    return {
        "name": name,
        "address": f"{address}/24",
        "protocols": protocols,
        "environment": environment,
    }


@pytest.fixture
def custom_dataset(tmp_path):
    root = tmp_path / "custom_v2"
    target = root / "device_instances/initial_devices.yaml"
    target.parent.mkdir(parents=True)
    records = [
        device(
            "PLC-A", "10.77.1.10", ["modbus-tcp"], DEVICE_ROLE="PLC", CELL_ROLE="intake"
        ),
        device("SENSOR-A", "10.77.1.20", ["coap"], COAP_PORT="5683"),
        device(
            "BROKER-A",
            "10.77.2.10",
            ["mqtt"],
            MQTT_OPERATOR_USERNAME="operator",
            MQTT_OPERATOR_PASSWORD="lab-secret",
        ),
        device("SSH-A", "10.77.3.10", ["ssh"], ENGINEER_PASSWORD="lab-secret"),
        device("TELNET-A", "10.77.1.30", ["telnet"], LEGACY_ROOT_PASSWORD="lab-secret"),
        device(
            "MIRAI-CNC-A", "10.77.4.10", ["mirai-cnc", "mirai-report"], C2_PORT="23"
        ),
        device("MIRAI-LISTENER-A", "10.77.4.11", ["mirai-report"], C2_PORT="48101"),
        device(
            "MIRAI-LOADER-A",
            "10.77.4.12",
            ["payload-delivery"],
            LOADER_KIND="wget",
            C2_PORT="80",
        ),
        device("MIRAI-BOT-A", "10.77.4.13", ["mirai-bot"]),
        device(
            "MERLIN-CNC-A",
            "10.77.4.20",
            ["merlin-control", "merlin-agent"],
            C2_PORT="23",
            MERLIN_CONTROL_PORT="23",
            MERLIN_HTTP_PORT="8443",
        ),
        device(
            "MERLIN-AGENT-A",
            "10.77.4.21",
            ["merlin-agent"],
            MERLIN_CNC_URL="http://10.77.4.20:8443",
        ),
    ]
    target.write_text(
        yaml.safe_dump({"testbed_id": "custom", "devices": records}), encoding="utf-8"
    )
    profile = root / "protocol_profiles/modbus_actions_v1.yaml"
    profile.parent.mkdir()
    shutil.copyfile(
        ROOT
        / "testbeds/metropolis/datasets/water_treatment_v1/protocol_profiles/modbus_actions_v1.yaml",
        profile,
    )
    return root, target, records


def invoke(tool, *args):
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith("METROPOLIS_"):
            environment.pop(key)
    return subprocess.run(
        [sys.executable, str(RUNNER), tool, *map(str, args), "--dry-run"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )


def started(result):
    assert result.returncode == 0, result.stdout + result.stderr
    return next(
        json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
    )


@pytest.mark.parametrize(
    ("tool", "arguments", "field", "expected"),
    [
        ("modbus_manipulation_attack", ["--action", "set-level"], "host", "10.77.1.10"),
        ("coap_response_measurement", [], "host", "10.77.1.20"),
        ("mqtt_bruteforce_attack", [], "host", "10.77.2.10"),
        ("mqtt_flood_attack", [], "host", "10.77.2.10"),
        ("ssh_bruteforce_attack", [], "host", "10.77.3.10"),
        ("mirai_infection_sim", [], "hosts", ["10.77.1.30"]),
        ("hping3_attack", [], "host", "10.77.1.10"),
        ("iot_discovery", [], "hosts", None),
        ("network_recon_scan", [], "protocol", "both"),
        ("run_mirai_choreography", [], "cnc", "10.77.4.10:23"),
        ("mirai_scenario", [], "targets", ["TELNET-A"]),
        ("run_merlin", [], "cnc", "10.77.4.20:23"),
        ("merlin_scenario", [], "agent", "10.77.4.21"),
        ("gotham_mirai_setup", [], "project", "custom_custom_v2"),
        ("gotham_merlin_setup", [], "project", "custom_custom_v2"),
    ],
)
def test_renamed_dataset_targets(tool, arguments, field, expected, custom_dataset):
    root, _, _ = custom_dataset
    record = started(invoke(tool, "--dataset", root, *arguments))
    if tool == "iot_discovery":
        assert "10.77.1.10" in record["hosts"]
    else:
        assert record[field] == expected


def test_missing_service_reports_why(custom_dataset):
    _, target, records = custom_dataset
    target.write_text(
        yaml.safe_dump(
            {
                "testbed_id": "custom",
                "devices": [item for item in records if item["name"] != "SENSOR-A"],
            }
        ),
        encoding="utf-8",
    )
    result = invoke("coap_response_measurement", "--inventory", target)
    assert result.returncode == 2
    assert "No coap node exists" in result.stdout


def test_ambiguous_service_requires_explicit_node(custom_dataset):
    _, target, records = custom_dataset
    records.append(
        device(
            "PLC-B", "10.77.1.11", ["modbus-tcp"], DEVICE_ROLE="PLC", CELL_ROLE="intake"
        )
    )
    target.write_text(
        yaml.safe_dump({"testbed_id": "custom", "devices": records}), encoding="utf-8"
    )
    ambiguous = invoke(
        "modbus_manipulation_attack", "--inventory", target, "--action", "set-level"
    )
    assert ambiguous.returncode == 2
    assert "select one with --node" in ambiguous.stdout
    selected = started(
        invoke(
            "modbus_manipulation_attack",
            "--inventory",
            target,
            "--node",
            "PLC-B",
            "--action",
            "set-level",
        )
    )
    assert selected["host"] == "10.77.1.11"


def test_dataset_and_inventory_cannot_be_combined(custom_dataset):
    root, target, _ = custom_dataset
    result = invoke("hping3_attack", "--dataset", root, "--inventory", target)
    assert result.returncode == 2
    assert "use --dataset or --inventory" in result.stderr


def test_explicit_private_host_and_port_can_target_an_unlisted_appliance(
    custom_dataset,
):
    root, _, _ = custom_dataset
    record = started(
        invoke(
            "modbus_manipulation_attack",
            "--dataset",
            root,
            "--host",
            "10.77.9.10",
            "--port",
            "1502",
            "--profile",
            "intake",
            "--action",
            "set-level",
        )
    )
    assert (record["host"], record["port"]) == ("10.77.9.10", 1502)
    raw = started(
        invoke(
            "hping3_attack", "--dataset", root, "--host", "10.77.9.20", "--port", "8080"
        )
    )
    assert (raw["host"], raw["port"]) == ("10.77.9.20", 8080)


def test_explicit_unlisted_host_rejects_public_address(custom_dataset):
    root, _, _ = custom_dataset
    result = invoke(
        "modbus_manipulation_attack",
        "--dataset",
        root,
        "--host",
        "8.8.8.8",
        "--port",
        "502",
        "--profile",
        "intake",
        "--action",
        "set-level",
    )
    assert result.returncode == 2
    assert "private lab network" in result.stdout


def test_multi_node_role_requires_the_matching_selector(custom_dataset):
    root, target, records = custom_dataset
    records.append(
        device("MIRAI-LISTENER-B", "10.77.4.14", ["mirai-report"], C2_PORT="48102")
    )
    target.write_text(
        yaml.safe_dump({"testbed_id": "custom", "devices": records}), encoding="utf-8"
    )
    result = invoke("run_mirai_choreography", "--dataset", root)
    assert result.returncode == 2
    assert "--listener-node" in result.stdout
    selected = started(
        invoke(
            "run_mirai_choreography",
            "--dataset",
            root,
            "--listener-node",
            "MIRAI-LISTENER-B",
        )
    )
    assert selected["listener"] == "10.77.4.14:48102"


def test_dataset_environment_selects_inventory(custom_dataset):
    root, _, _ = custom_dataset
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith("METROPOLIS_"):
            environment.pop(key)
    environment["METROPOLIS_DATASET"] = str(root)
    result = subprocess.run(
        [sys.executable, str(RUNNER), "coap_response_measurement", "--dry-run"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert started(result)["host"] == "10.77.1.20"


def test_custom_role_ports_come_from_inventory(custom_dataset):
    root, target, records = custom_dataset
    by_name = {item["name"]: item for item in records}
    by_name["MIRAI-CNC-A"]["environment"]["MIRAI_CNC_PORT"] = "1234"
    by_name["MIRAI-LISTENER-A"]["environment"]["MIRAI_REPORT_PORT"] = "4321"
    by_name["MIRAI-LOADER-A"]["environment"]["LOADER_PORT"] = "8081"
    by_name["MERLIN-CNC-A"]["environment"]["MERLIN_CONTROL_PORT"] = "2223"
    by_name["MERLIN-CNC-A"]["environment"]["MERLIN_HTTP_PORT"] = "8444"
    by_name["MERLIN-AGENT-A"]["environment"]["MERLIN_CNC_URL"] = (
        "http://10.77.4.20:8444"
    )
    target.write_text(
        yaml.safe_dump({"testbed_id": "custom", "devices": records}), encoding="utf-8"
    )
    mirai = started(invoke("run_mirai_choreography", "--dataset", root))
    assert mirai["cnc"] == "10.77.4.10:1234"
    assert mirai["listener"] == "10.77.4.11:4321"
    assert mirai["loader"] == "10.77.4.12:8081"
    merlin = started(invoke("run_merlin", "--dataset", root))
    assert merlin["cnc"] == "10.77.4.20:2223"
    assert merlin["http"] == "10.77.4.20:8444"


def test_http_choreography_rejects_tftp_only_loader(custom_dataset):
    root, target, records = custom_dataset
    loader = next(item for item in records if item["name"] == "MIRAI-LOADER-A")
    loader["environment"]["LOADER_KIND"] = "tftp"
    target.write_text(
        yaml.safe_dump({"testbed_id": "custom", "devices": records}), encoding="utf-8"
    )
    result = invoke("run_mirai_choreography", "--dataset", root)
    assert result.returncode == 2
    assert "LOADER_KIND=wget" in result.stdout


def test_changed_address_must_match_container_node_ip(custom_dataset):
    root, target, records = custom_dataset
    records[0]["environment"]["NODE_IP"] = "10.77.1.99/24"
    target.write_text(
        yaml.safe_dump({"testbed_id": "custom", "devices": records}), encoding="utf-8"
    )
    result = invoke(
        "modbus_manipulation_attack", "--dataset", root, "--action", "set-level"
    )
    assert result.returncode == 2
    assert "disagrees with NODE_IP" in result.stdout


def test_credentials_for_custom_broker_can_come_from_wordlist(custom_dataset, tmp_path):
    root, target, records = custom_dataset
    broker = next(item for item in records if item["name"] == "BROKER-A")
    broker["environment"] = {}
    target.write_text(
        yaml.safe_dump({"testbed_id": "custom", "devices": records}), encoding="utf-8"
    )
    wordlist = tmp_path / "passwords.txt"
    wordlist.write_text("example\n", encoding="utf-8")
    record = started(
        invoke(
            "mqtt_bruteforce_attack",
            "--dataset",
            root,
            "--username",
            "operator",
            "--wordlist",
            wordlist,
        )
    )
    assert record["host"] == "10.77.2.10"
    missing = invoke("mqtt_bruteforce_attack", "--dataset", root)
    assert missing.returncode == 2
    assert "--username" in missing.stdout


def test_unlisted_broker_does_not_inherit_another_brokers_credentials(
    custom_dataset, tmp_path
):
    root, _, _ = custom_dataset
    missing = invoke(
        "mqtt_bruteforce_attack",
        "--dataset",
        root,
        "--host",
        "10.77.9.30",
        "--port",
        "1883",
    )
    assert missing.returncode == 2
    assert "--username" in missing.stdout
    wordlist = tmp_path / "passwords.txt"
    wordlist.write_text("example\n", encoding="utf-8")
    record = started(
        invoke(
            "mqtt_bruteforce_attack",
            "--dataset",
            root,
            "--host",
            "10.77.9.30",
            "--port",
            "1883",
            "--username",
            "operator",
            "--wordlist",
            wordlist,
        )
    )
    assert record["host"] == "10.77.9.30"
