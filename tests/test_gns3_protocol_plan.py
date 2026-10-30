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
                "protocols": ["rdp", "rdp-udp-synthetic", "https", "telemetry-relay"],
                "environment": {
                    "TELEMETRY_WRITER_NODE": "plant-publisher",
                    "TELEMETRY_READER_NODE": "office-reader",
                    "TELEMETRY_PORT": "8443",
                    "TELEMETRY_ENABLED": "true",
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
        (1, "TELEMETRY_ENABLED", "false", "cohosted relay requires"),
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


def opcua_inventory():
    return {
        "devices": [
            {
                "name": "supervisor-west",
                "image": "supervisor:image",
                "address": "192.0.2.25/24",
                "subnet": "192.0.2.0/24",
                "protocols": ["opcua-server"],
                "environment": {
                    "OPCUA_ENABLED": "true",
                    "OPCUA_CLIENT_NODE": "archive-east",
                },
            },
            {
                "name": "archive-east",
                "image": "archive:image",
                "address": "198.51.100.90/24",
                "subnet": "198.51.100.0/24",
                "protocols": ["opcua-client"],
                "environment": {
                    "OPCUA_ENABLED": "true",
                    "OPCUA_SERVER_NODE": "supervisor-west",
                },
            },
        ]
    }


@pytest.mark.parametrize("security", [None, "None"])
def test_opcua_plan_derives_selected_endpoint_and_application_identities(security):
    inventory = opcua_inventory()
    inventory["devices"][0]["address"] = "192.0.2.81/24"
    if security is not None:
        for device in inventory["devices"]:
            device["environment"]["OPCUA_SECURITY_MODE"] = security
    plan = build_plan(
        inventory,
        {"routers": [], "switches": [], "segments": []},
        {"links": []},
        {device["image"]: device["image"] for device in inventory["devices"]},
    )
    nodes = {node.name: node for node in plan.nodes}
    client = nodes["archive-east"].environment
    assert client["OPCUA_ENDPOINT_URL"] == "opc.tcp://192.0.2.81:4840/metropolis/"
    assert client["OPCUA_APPLICATION_URI"] == "urn:metropolis:opcua:archive-east"
    assert (
        client["OPCUA_PEER_APPLICATION_URI"] == "urn:metropolis:opcua:supervisor-west"
    )
    assert client["OPCUA_PEER_ADDRESS"] == "192.0.2.81"
    assert client["OPCUA_SECURITY_MODE"] == (security or "SignAndEncrypt")
    assert client["OPCUA_PUBLISH_INTERVAL_MS"] == "1000"
    assert nodes["supervisor-west"].environment["OPCUA_PEER_ADDRESS"] == "198.51.100.90"
    assert nodes["supervisor-west"].environment["DEVICE_NAME"] == "supervisor-west"
    assert "OPCUA_ENDPOINT_URL" not in inventory["devices"][1]["environment"]


@pytest.mark.parametrize(
    "node,key,value,diagnostic",
    [
        (1, "OPCUA_SERVER_NODE", "missing", "OPCUA_SERVER_NODE"),
        (0, "OPCUA_CLIENT_NODE", "supervisor-west", "must name"),
        (1, "OPCUA_SECURITY_MODE", "None", "differs from peer"),
        (1, "OPCUA_PUBLISH_INTERVAL_MS", "nan", "finite and positive"),
        (0, "OPCUA_PORT", "0", "invalid OPCUA_PORT"),
        (1, "OPCUA_ENDPOINT_URL", "opc.tcp://192.0.2.99:4840/metropolis/", "disagrees"),
        (0, "OPCUA_ENABLED", "false", "requires OPCUA_ENABLED=true"),
    ],
)
def test_opcua_plan_rejects_inconsistent_or_unusable_configuration(
    node, key, value, diagnostic
):
    from opcua_config import compile_opcua

    inventory = opcua_inventory()
    inventory["devices"][node]["environment"][key] = value
    with pytest.raises(ValueError, match=diagnostic):
        compile_opcua(inventory)


def test_opcua_validator_requires_client_to_server_firewall_path():
    sys.path.insert(0, str(GNS3.parent))
    from validate_metropolis_topology import (
        _validate_device_targets,
        _validate_service_flows,
    )

    devices = opcua_inventory()["devices"]
    assert _validate_device_targets(devices) == []
    policy = {
        "flows": [
            {
                "source": "192.0.2.0/24",
                "destination": "198.51.100.0/24",
                "protocol": "tcp",
                "port": 4840,
            }
        ]
    }
    assert _validate_service_flows(devices, [], policy) == [
        "archive-east: no TCP/4840 firewall flow to OPC UA server supervisor-west"
    ]
    policy["flows"][0].update(source="198.51.100.0/24", destination="192.0.2.0/24")
    assert _validate_service_flows(devices, [], policy) == []
    devices[0]["link_policy"] = "intentionally_uncabled"
    assert any(
        "OPC UA peers require physical links" in problem
        for problem in _validate_device_targets(devices)
    )


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
        (
            "OPCUA_ENDPOINT_URL",
            "opc.tcp://192.0.2.8:4840/metropolis/",
            "opc.tcp://198.51.100.6:4840/metropolis/",
            "OPCUA_KEY_FILE",
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
    assert "stale MQTT/DNP3/CoAP/SNMP/telemetry/OPC-UA environment" in result.skipped[0]
    assert "private-lab-value" not in result.skipped[0]
    assert current["properties"]["environment"] == stale_environment
