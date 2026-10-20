"""Switch plans and Docker execution use mocked GNS3 and Docker responses."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


GNS3 = Path(__file__).resolve().parents[1] / "scripts" / "gns3"
if str(GNS3) not in sys.path:
    sys.path.insert(0, str(GNS3))

from _cli import load_plan  # noqa: E402
from switch_config import configure_switches, expected_state, switch_payload  # noqa: E402

RUNTIME = (
    Path(__file__).resolve().parents[1]
    / "testbeds/metropolis/devices/infrastructure/openvswitch/switch.py"
)
spec = importlib.util.spec_from_file_location("metropolis_ovs_runtime", RUNTIME)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def test_payload_maps_trunk_and_spare_ports():
    topology, _ = load_plan()
    plant = switch_payload(topology, "sw-plant-aggregation")
    assert plant["ports"]["0"]["vlans"] == [10, 11, 12, 13, 50, 51, 52, 53]
    assert expected_state(plant)["eth0"] == {
        "tag": None,
        "trunks": [10, 11, 12, 13, 50, 51, 52, 53],
        "vlan_mode": "trunk",
    }
    assert set(switch_payload(topology, "sw-dmz")["ports"]) == {"0"}
    segment = switch_payload(topology, "sw-plant-cell-intake")
    assert segment["ports"]
    assert [item["mode"] for item in segment["ports"].values()].count("trunk") == 1
    assert all(item["vlans"] == [10] for item in segment["ports"].values())


def test_runtime_rejects_malformed_and_ambiguous_vlan_settings():
    for setting in (
        {"mode": "trunk", "vlans": []},
        {"mode": "access", "vlans": [10, 11]},
    ):
        try:
            runtime.expected(setting)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid VLAN plan accepted")
    assert runtime.expected({"mode": "untagged", "vlans": []}) == {
        "tag": 1,
        "trunks": [],
        "vlan_mode": "access",
    }


def test_runtime_reads_real_ovs_fields_for_verification(monkeypatch):
    responses = {
        ("port-to-br", "eth0"): "br0",
        ("get", "Port", "eth0", "tag"): "[]",
        ("get", "Port", "eth0", "trunks"): "[10, 11]",
        ("get", "Port", "eth0", "vlan_mode"): '"trunk"',
    }
    monkeypatch.setattr(runtime, "command", lambda _binary, *args: responses[args])
    assert runtime.port_state("eth0") == {
        "tag": None,
        "trunks": [10, 11],
        "vlan_mode": "trunk",
    }


def test_runtime_applies_then_skips_matching_ovs_state(monkeypatch):
    plan = {
        "schema_version": 1,
        "ports": {
            "0": {"mode": "trunk", "vlans": [10, 11]},
            "1": {"mode": "access", "vlans": [10]},
        },
    }
    states = {}
    commands = []
    members = {"eth0", "eth1", "eth2"}

    def command(*args):
        commands.append(args)
        if "list-ports" in args:
            return "\n".join(sorted(members))
        if "del-port" in args:
            members.discard(args[-1])
            return ""
        if "add-port" in args:
            members.add(args[-1])
        if "set" in args:
            name = args[3]
            states[name] = runtime.expected(plan["ports"][name.removeprefix("eth")])
        return ""

    monkeypatch.setattr(runtime, "command", command)
    monkeypatch.setattr(runtime, "port_state", lambda name: states.get(name))
    monkeypatch.setattr(runtime.Path, "exists", lambda _self: True)
    first = runtime.reconcile(plan)
    assert first["verified"] is True
    assert {"eth0", "eth1", "eth2"} == set(first["changed"])
    assert any("del-port" in args and "eth2" in args for args in commands)
    commands.clear()
    second = runtime.reconcile(plan)
    assert second["changed"] == []
    assert not any("set" in args for args in commands)


class Client:
    def __init__(self):
        self.records = {
            "sw-plant-aggregation": {
                "name": "sw-plant-aggregation",
                "node_id": "n1",
                "node_type": "docker",
                "compute_id": "remote",
                "status": "started",
                "properties": {
                    "image": "metropolis/openvswitch:dev",
                    "container_id": "c1",
                },
            },
            "sw-operations-aggregation": {
                "name": "sw-operations-aggregation",
                "node_id": "n2",
                "node_type": "docker",
                "compute_id": "remote",
                "status": "started",
                "properties": {
                    "image": "metropolis/openvswitch:dev",
                    "container_id": "c2",
                },
            },
        }

    def project_nodes(self, _project_id):
        return list(self.records.values())

    def node(self, _project_id, node_id):
        return next(
            record for record in self.records.values() if record["node_id"] == node_id
        )


def test_partial_docker_failure_keeps_other_switch_result():
    topology, _ = load_plan()
    topology.nodes = [
        node
        for node in topology.nodes
        if node.name in {"sw-plant-aggregation", "sw-operations-aggregation"}
    ]
    client = Client()

    def execute(command, *, input, **_kwargs):
        if "c1" in command:
            return subprocess.CompletedProcess(command, 1, "", "OVS unavailable")
        payload = json.loads(input)
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {"verified": True, "changed": [], "ports": expected_state(payload)}
            ),
            "",
        )

    results = configure_switches(
        client, "p1", topology, {"remote": "ssh://gns3@example"}, execute=execute
    )
    assert results["sw-plant-aggregation"] == {
        "status": "failed",
        "reason": "OVS unavailable",
    }
    assert results["sw-operations-aggregation"]["status"] == "unchanged"


def test_wrong_image_and_missing_compute_are_reported():
    topology, _ = load_plan()
    topology.nodes = [
        node
        for node in topology.nodes
        if node.name in {"sw-plant-aggregation", "sw-operations-aggregation"}
    ]
    client = Client()
    client.records["sw-plant-aggregation"]["properties"]["image"] = "other/switch"
    results = configure_switches(client, "p1", topology, {})
    assert "not the Metropolis" in results["sw-plant-aggregation"]["reason"]
    assert "no Docker host" in results["sw-operations-aggregation"]["reason"]


def test_gns3_node_lookup_failure_is_recorded_per_switch():
    topology, _ = load_plan()
    topology.nodes = [
        node for node in topology.nodes if node.name == "sw-plant-aggregation"
    ]
    client = Client()

    def fail(_project_id):
        raise RuntimeError("GNS3 API unavailable")

    client.project_nodes = fail
    result = configure_switches(
        client, "p1", topology, {"remote": "ssh://gns3@example"}
    )
    assert result["sw-plant-aggregation"] == {
        "status": "failed",
        "reason": "GNS3 API unavailable",
    }
