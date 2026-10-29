"""MQTT access policy is derived from a selected inventory, not node names."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest
import paho.mqtt.client as mqtt


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compiler = _load("mqtt_acl", ROOT / "scripts/gns3/mqtt_acl.py")
broker_runtime = _load(
    "mqtt_access",
    ROOT / "testbeds/metropolis/devices/services/mqtt_broker/mqtt_access.py",
)


def _inventory():
    host = "198.51.100.10"
    return {
        "devices": [
            {
                "name": "BROKER-CUSTOM",
                "address": f"{host}/24",
                "protocols": ["mqtt"],
                "environment": {
                    "MQTT_OPERATOR_USERNAME": "experimenter",
                    "MQTT_OPERATOR_PASSWORD": "LabOnly_Operator",
                },
            },
            {
                "name": "SENSOR-CUSTOM",
                "protocols": ["mqtt", "coap"],
                "environment": {
                    "MQTT_ACCESS_ROLE": "publisher",
                    "MQTT_HOST": host,
                    "MQTT_USERNAME": "sensor_alpha",
                    "MQTT_PASSWORD": "LabOnly_Sensor",
                    "MQTT_TOPIC": "metropolis/plant/alpha/telemetry",
                },
            },
            {
                "name": "SCADA-CUSTOM",
                "protocols": ["mqtt"],
                "environment": {
                    "MQTT_ACCESS_ROLE": "scada",
                    "MQTT_HOST": host,
                    "MQTT_USERNAME": "scada_alpha",
                    "MQTT_PASSWORD": "LabOnly_SCADA",
                    "SCADA_TARGETS": json.dumps(
                        [
                            {
                                "id": "alpha",
                                "topic": "metropolis/scada/alpha/status",
                            },
                            {
                                "id": "remote",
                                "topic": "metropolis/scada/remote/status",
                            },
                        ]
                    ),
                },
            },
            {
                "name": "HMI-CUSTOM",
                "protocols": ["mqtt", "http"],
                "environment": {
                    "MQTT_ACCESS_ROLE": "hmi",
                    "MQTT_HOST": host,
                    "MQTT_USERNAME": "hmi_alpha",
                    "MQTT_PASSWORD": "LabOnly_HMI",
                    "HMI_CELL_ID": "alpha",
                    "MQTT_TOPIC": "metropolis/plant/alpha/command",
                    "MQTT_ACK_TOPIC": "metropolis/scada/alpha/ack",
                    "MQTT_TELEMETRY_TOPIC": "metropolis/scada/alpha/status",
                },
            },
            {
                "name": "HISTORIAN-CUSTOM",
                "protocols": ["mqtt"],
                "environment": {
                    "MQTT_ACCESS_ROLE": "subscriber",
                    "MQTT_HOST": host,
                    "MQTT_USERNAME": "historian_alpha",
                    "MQTT_PASSWORD": "LabOnly_Historian",
                    "MQTT_TOPIC": "metropolis/#",
                },
            },
        ]
    }


def _accounts(inventory):
    environment = compiler.compile_mqtt_access(inventory)
    payload = base64.b64decode(environment["MQTT_ACCESS_B64"])
    assert hashlib.sha256(payload).hexdigest() == environment["MQTT_ACCESS_SHA256"]
    return {item["username"]: item for item in json.loads(payload)["accounts"]}


def test_mqtt_access_is_specific_to_declared_roles_and_topics():
    accounts = _accounts(_inventory())
    assert accounts["sensor_alpha"]["write"] == ["metropolis/plant/alpha/telemetry"]
    assert accounts["sensor_alpha"]["read"] == []
    assert accounts["hmi_alpha"]["write"] == ["metropolis/plant/alpha/command"]
    assert accounts["hmi_alpha"]["read"] == [
        "metropolis/scada/alpha/ack",
        "metropolis/scada/alpha/status",
    ]
    assert accounts["scada_alpha"]["read"] == ["metropolis/plant/alpha/command"]
    assert accounts["scada_alpha"]["write"] == [
        "metropolis/scada/alpha/ack",
        "metropolis/scada/alpha/status",
        "metropolis/scada/remote/status",
    ]
    assert accounts["historian_alpha"]["read"] == ["metropolis/#"]
    assert accounts["historian_alpha"]["write"] == []
    assert accounts["experimenter"]["write"] == ["metropolis/experiment/#"]
    assert accounts["experimenter"]["read"] == []


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (
            lambda data: data["devices"][3]["environment"].pop("MQTT_ACCESS_ROLE"),
            "MQTT_ACCESS_ROLE",
        ),
        (
            lambda data: data["devices"][3]["environment"].update(
                MQTT_USERNAME="sensor_alpha"
            ),
            "duplicate MQTT username",
        ),
        (
            lambda data: data["devices"][1]["environment"].update(
                MQTT_TOPIC="metropolis/plant/alpha/telemetry\nuser intruder"
            ),
            "invalid MQTT_TOPIC",
        ),
        (
            lambda data: data["devices"][1]["environment"].update(
                MQTT_HOST="198.51.100.99"
            ),
            "MQTT_HOST",
        ),
        (
            lambda data: data["devices"][3]["environment"].update(
                MQTT_TOPIC="metropolis/plant/remote/command"
            ),
            "topics do not match",
        ),
    ],
)
def test_mqtt_access_rejects_unapproved_or_inconsistent_clients(change, expected):
    inventory = _inventory()
    change(inventory)
    with pytest.raises(ValueError, match=expected) as error:
        compiler.compile_mqtt_access(inventory)
    assert "LabOnly" not in str(error.value)


def test_mqtt_access_allows_non_mqtt_synthetic_topologies():
    assert (
        compiler.compile_mqtt_access({"devices": [{"name": "PLC", "protocols": []}]})
        == {}
    )


def test_broker_runtime_writes_acl_and_calls_password_hasher(tmp_path, monkeypatch):
    environment = compiler.compile_mqtt_access(_inventory())
    accounts = broker_runtime.decode_access(
        environment["MQTT_ACCESS_B64"], environment["MQTT_ACCESS_SHA256"]
    )

    hash_calls = []

    def fake_hash(command, *, check, capture_output):
        assert command[:2] == ["mosquitto_passwd", "-U"]
        assert check and capture_output
        path = Path(command[2])
        assert "LabOnly" in path.read_text(encoding="utf-8")
        hash_calls.append(command)
        path.write_text("hashed-placeholder\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(broker_runtime.subprocess, "run", fake_hash)
    broker_runtime.write_access(accounts, tmp_path)
    acl = (tmp_path / "aclfile").read_text(encoding="utf-8")
    assert "user hmi_alpha\ntopic read metropolis/scada/alpha/ack" in acl
    assert "topic write metropolis/plant/alpha/command" in acl
    assert "topic read metropolis/#" in acl
    assert "LabOnly" not in acl
    assert len(hash_calls) == 1
    assert {path.name for path in tmp_path.iterdir()} == {"passwordfile", "aclfile"}
    if os.name != "nt":
        assert all(
            stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600
            for name in ("passwordfile", "aclfile")
        )


def test_broker_runtime_rejects_corrupt_or_duplicate_accounts():
    environment = compiler.compile_mqtt_access(_inventory())
    with pytest.raises(ValueError, match="invalid MQTT access payload"):
        broker_runtime.decode_access(environment["MQTT_ACCESS_B64"], "0" * 64)
    accounts = _accounts(_inventory())
    invalid = {"version": 1, "accounts": [accounts["hmi_alpha"]] * 2}
    payload = json.dumps(invalid).encode()
    with pytest.raises(ValueError, match="invalid MQTT access account"):
        broker_runtime.decode_access(
            base64.b64encode(payload).decode(), hashlib.sha256(payload).hexdigest()
        )


@pytest.mark.skipif(
    os.name == "nt"
    or (hasattr(os, "geteuid") and os.geteuid() == 0)
    or not shutil.which("mosquitto")
    or not shutil.which("mosquitto_passwd"),
    reason="requires a local non-root Mosquitto installation",
)
def test_mosquitto_enforces_allowed_and_cross_cell_denied_publish(tmp_path):
    """Exercise the broker's actual ACL engine when Mosquitto is available."""
    environment = compiler.compile_mqtt_access(_inventory())
    accounts = broker_runtime.decode_access(
        environment["MQTT_ACCESS_B64"], environment["MQTT_ACCESS_SHA256"]
    )
    broker_runtime.write_access(accounts, tmp_path)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = tmp_path / "mosquitto.conf"
    config.write_text(
        f"listener {port} 127.0.0.1\n"
        "allow_anonymous false\n"
        f"password_file {tmp_path / 'passwordfile'}\n"
        f"acl_file {tmp_path / 'aclfile'}\n"
        "persistence false\n",
        encoding="utf-8",
    )
    process = subprocess.Popen(
        ["mosquitto", "-c", str(config)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    received = []
    subscribed = threading.Event()
    historian = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    historian.username_pw_set("historian_alpha", "LabOnly_Historian")
    historian.on_connect = lambda client, *_: client.subscribe("metropolis/#")
    historian.on_subscribe = lambda *_: subscribed.set()
    historian.on_message = lambda _client, _data, message: received.append(
        message.topic
    )
    hmi = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    hmi.username_pw_set("hmi_alpha", "LabOnly_HMI")
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                historian.connect("127.0.0.1", port)
                break
            except ConnectionRefusedError:
                time.sleep(0.05)
        else:
            pytest.fail("Mosquitto did not start on loopback")
        historian.loop_start()
        assert subscribed.wait(3)
        hmi.connect("127.0.0.1", port)
        hmi.loop_start()
        hmi.publish("metropolis/plant/alpha/command", b"{}", qos=1).wait_for_publish(3)
        hmi.publish("metropolis/plant/remote/command", b"{}", qos=1).wait_for_publish(3)
        deadline = time.monotonic() + 3
        while (
            "metropolis/plant/alpha/command" not in received
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert "metropolis/plant/alpha/command" in received
        time.sleep(0.3)
        assert "metropolis/plant/remote/command" not in received
    finally:
        hmi.loop_stop()
        hmi.disconnect()
        historian.loop_stop()
        historian.disconnect()
        process.terminate()
        process.wait(timeout=5)
