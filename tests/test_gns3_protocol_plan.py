"""Protocol peers and reused-node settings follow the selected inventory."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

GNS3 = Path(__file__).resolve().parents[1] / "scripts" / "gns3"
if str(GNS3) not in sys.path:
    sys.path.insert(0, str(GNS3))

from builder import ensure_nodes  # noqa: E402
from topology import PlannedNode, Topology, build_plan  # noqa: E402


def _inventory() -> dict:
    return {
        "devices": [
            {
                "name": "master",
                "image": "master:image",
                "address": "192.0.2.10/24",
                "protocols": ["dnp3-master", "coap-client"],
                "environment": {
                    "DNP3_PEER_NODES": "site-rtu",
                    "COAP_TARGET_NODE": "status-sensor",
                },
            },
            {
                "name": "site-rtu",
                "image": "rtu:image",
                "address": "198.51.100.40/24",
                "protocols": ["dnp3-outstation"],
                "environment": {"DNP3_ENABLED": "true", "DNP3_PORT": "20001"},
            },
            {
                "name": "status-sensor",
                "image": "sensor:image",
                "address": "203.0.113.45/24",
                "protocols": ["coap"],
                "environment": {},
            },
        ]
    }


def _plan(inventory: dict):
    templates = {name: name for name in ("master:image", "rtu:image", "sensor:image")}
    return build_plan(
        inventory,
        {"routers": [], "switches": [], "segments": []},
        {"links": []},
        templates,
    )


def test_plan_resolves_named_protocol_peers_after_address_changes():
    inventory = _inventory()
    inventory["devices"][1]["address"] = "198.51.100.88/24"
    plan = _plan(inventory)
    master = next(node for node in plan.nodes if node.name == "master")
    targets = json.loads(base64.b64decode(master.environment["DNP3_TARGETS_B64"]))
    assert targets == [{"host": "198.51.100.88", "name": "site-rtu", "port": 20001}]
    assert master.environment["COAP_HOST"] == "203.0.113.45"


def test_plan_rejects_missing_protocol_peer():
    inventory = _inventory()
    inventory["devices"][0]["environment"]["DNP3_PEER_NODES"] = "missing-rtu"
    with pytest.raises(ValueError, match="not a DNP3 outstation"):
        _plan(inventory)


def telemetry_inventory():
    return {
        "devices": [
            {
                "name": "plant-publisher",
                "image": "scada:image",
                "address": "192.0.2.10/24",
                "subnet": "192.0.2.0/24",
                "protocols": ["telemetry-publisher"],
                "environment": {"TELEMETRY_RELAY_NODE": "boundary-cache"},
            },
            {
                "name": "boundary-cache",
                "image": "relay:image",
                "address": "198.51.100.30/24",
                "subnet": "198.51.100.0/24",
                "protocols": ["telemetry-relay"],
                "environment": {
                    "TELEMETRY_WRITER_NODE": "plant-publisher",
                    "TELEMETRY_READER_NODE": "office-reader",
                    "TELEMETRY_PORT": "8443",
                },
            },
            {
                "name": "office-reader",
                "image": "consumer:image",
                "address": "203.0.113.10/24",
                "subnet": "203.0.113.0/24",
                "protocols": ["telemetry-consumer"],
                "environment": {"TELEMETRY_RELAY_NODE": "boundary-cache"},
            },
        ]
    }


def test_telemetry_plan_resolves_selected_relay_and_certificate_source():
    inventory = telemetry_inventory()
    inventory["devices"][1]["address"] = "198.51.100.99/24"
    plan = build_plan(
        inventory,
        {"routers": [], "switches": [], "segments": []},
        {"links": []},
        {device["image"]: device["image"] for device in inventory["devices"]},
    )
    nodes = {node.name: node for node in plan.nodes}
    assert (
        nodes["plant-publisher"].environment["TELEMETRY_RELAY_URL"]
        == "https://198.51.100.99:8443/v1/status"
    )
    assert (
        nodes["office-reader"].environment["TELEMETRY_RELAY_URL"]
        == "https://198.51.100.99:8443/v1/status"
    )
    assert nodes["plant-publisher"].environment["DEVICE_NAME"] == "plant-publisher"
    assert "TELEMETRY_RELAY_URL" not in inventory["devices"][0]["environment"]


@pytest.mark.parametrize(
    "node,key,value,diagnostic",
    [
        (0, "TELEMETRY_RELAY_NODE", "missing", "must name"),
        (1, "TELEMETRY_READER_NODE", "plant-publisher", "telemetry-consumer"),
        (1, "TELEMETRY_PORT", "65536", "invalid TELEMETRY_PORT"),
        (2, "TELEMETRY_RELAY_URL", "http://198.51.100.30/v1/status", "disagrees"),
    ],
)
def test_telemetry_plan_rejects_unusable_or_unauthorized_peer(
    node, key, value, diagnostic
):
    from telemetry_config import compile_telemetry

    inventory = telemetry_inventory()
    inventory["devices"][node]["environment"][key] = value
    with pytest.raises(ValueError, match=diagnostic):
        compile_telemetry(inventory)


def test_telemetry_validator_checks_firewall_direction_and_port():
    sys.path.insert(0, str(GNS3.parent))
    from validate_metropolis_topology import (
        _validate_device_targets,
        _validate_service_flows,
    )

    inventory = telemetry_inventory()
    assert _validate_device_targets(inventory["devices"]) == []
    policy = {
        "flows": [
            {
                "source": "203.0.113.0/24",
                "destination": "198.51.100.0/24",
                "protocol": "tcp",
                "port": 8443,
            }
        ]
    }
    assert _validate_service_flows(inventory["devices"], [], policy) == [
        "plant-publisher: no TCP/8443 firewall flow to telemetry relay boundary-cache"
    ]
    policy["flows"].append(
        {
            "source": "192.0.2.0/24",
            "destination": "198.51.100.0/24",
            "protocol": "tcp",
            "port": 8443,
        }
    )
    assert _validate_service_flows(inventory["devices"], [], policy) == []


@pytest.mark.parametrize(
    "key,expected,previous,private_key",
    [
        ("MQTT_USERNAME", "new-user", "old-user", "MQTT_PASSWORD"),
        ("SNMP_TARGETS", "edge=192.0.2.5", "edge=198.51.100.6", "SNMP_COMMUNITY"),
        (
            "TELEMETRY_RELAY_URL",
            "https://192.0.2.5:443/v1/status",
            "https://198.51.100.6:443/v1/status",
            "TELEMETRY_KEY_FILE",
        ),
    ],
)
def test_reused_node_protocol_drift_is_detected_without_disclosing_values(
    key, expected, previous, private_key
):
    environment = {key: expected, private_key: "private-lab-value"}
    planned = PlannedNode(
        "sensor",
        "template",
        environment=environment,
    )
    current = {
        "properties": {
            "environment": "\n".join(
                f'{name}="{value}"' for name, value in environment.items()
            )
        }
    }
    current.update({"name": "sensor", "node_id": "gns3-1", "status": "started"})

    class Client:
        def project_nodes(self, project_id):
            return [current]

    topology = Topology([planned], [], {}, [], [])
    _, result = ensure_nodes(Client(), "project", topology, {})
    assert result.skipped == []
    current["properties"]["environment"] = (
        f'{key}="{previous}"\n{private_key}="private-lab-value"'
    )
    stale_environment = current["properties"]["environment"]
    _, result = ensure_nodes(Client(), "project", topology, {})
    assert len(result.skipped) == 1
    assert "stale MQTT/DNP3/CoAP/SNMP/telemetry environment" in result.skipped[0]
    assert "private-lab-value" not in result.skipped[0]
    assert current["properties"]["environment"] == stale_environment
