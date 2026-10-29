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


def test_reused_node_protocol_drift_is_detected_without_disclosing_values():
    planned = PlannedNode(
        "sensor",
        "template",
        environment={"MQTT_USERNAME": "new-user", "MQTT_PASSWORD": "private-lab-value"},
    )
    current = {
        "properties": {
            "environment": 'MQTT_USERNAME="new-user"\nMQTT_PASSWORD="private-lab-value"'
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
        'MQTT_USERNAME="old-user"\nMQTT_PASSWORD="private-lab-value"'
    )
    _, result = ensure_nodes(Client(), "project", topology, {})
    assert len(result.skipped) == 1
    assert "stale MQTT/DNP3/CoAP environment" in result.skipped[0]
    assert "private-lab-value" not in result.skipped[0]
