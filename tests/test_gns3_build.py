"""Checks for topology planning, the builder, lifecycle, and capture.

Synthetic topology documents and a fake GNS3 client keep these behavior tests
independent of the bundled testbed's changing inventory.
"""

from __future__ import annotations

import json
import io
import struct
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

GNS3 = Path(__file__).resolve().parents[1] / "scripts" / "gns3"
if str(GNS3) not in sys.path:
    sys.path.insert(0, str(GNS3))

from _cli import template_name_by_image  # noqa: E402
import experiment  # noqa: E402
from builder import build, existing_link_keys  # noqa: E402
from capture import LinkResolution, capture_for, coverage_report, resolve_links  # noqa: E402
from client import Node, Project  # noqa: E402
from experiment import run_experiment  # noqa: E402
from device_logs import DeviceLogCollector  # noqa: E402
from lifecycle import (  # noqa: E402
    start_all,
    startup_order,
    stop_all,
    wait_until_running,
)
from templates import APPLIANCES  # noqa: E402
from topology import (  # noqa: E402
    build_plan,
    port_map,
    switch_port_settings,
)
from switch_config import expected_state  # noqa: E402


@pytest.fixture(scope="module")
def documents():
    address_plan = {"testbed_id": "unit_lab"}
    inventory = {
        "devices": [
            {
                "name": "scada",
                "image": "metropolis/scada:dev",
                "address": "192.0.2.11/24",
                "environment": {"NODE_IP": "192.0.2.11/24"},
            },
            {
                "name": "syslog-receiver",
                "image": "metropolis/ntp:dev",
                "address": "192.0.2.12/24",
                "environment": {"NODE_IP": "192.0.2.12/24"},
            },
            {
                "name": "meter",
                "image": "metropolis/controller:dev",
                "address": "192.0.2.13/24",
                "environment": {"NODE_IP": "192.0.2.13/24"},
            },
        ]
    }
    infrastructure = {
        "routers": [
            {"name": "router-a", "config": "router-a.sh", "interfaces": []},
            {"name": "router-b", "config": "router-b.sh", "interfaces": []},
        ],
        "switches": [
            {
                "name": "sw-a",
                "attachment_points": ["router", "scada", "syslog", "segment", "spare"],
            }
        ],
        "segments": [{"name": "seg-a", "network_id": "cell"}],
    }
    link_plan = {
        "links": [
            {
                "id": "router-transit",
                "kind": "transit",
                "endpoints": [
                    {"node": "router-a", "interface": "eth1"},
                    {"node": "router-b", "interface": "eth1"},
                ],
            },
            {
                "id": "router-uplink",
                "kind": "trunk",
                "vlans": [10],
                "endpoints": [
                    {"node": "router-a", "interface": "eth0"},
                    {"node": "sw-a", "port_role": "router"},
                ],
            },
            {
                "id": "scada-link",
                "kind": "access",
                "network_id": "cell",
                "vlans": [10],
                "endpoints": [
                    {"node": "sw-a", "port_role": "scada"},
                    {"node": "scada", "interface": "eth0"},
                ],
            },
            {
                "id": "syslog-link",
                "kind": "access",
                "network_id": "cell",
                "vlans": [10],
                "endpoints": [
                    {"node": "sw-a", "port_role": "syslog"},
                    {"node": "syslog-receiver", "interface": "eth0"},
                ],
            },
            {
                "id": "segment-uplink",
                "kind": "trunk",
                "vlans": [10],
                "endpoints": [
                    {"node": "sw-a", "port_role": "segment"},
                    {"node": "seg-a", "interface": "eth0"},
                ],
            },
            {
                "id": "meter-link",
                "kind": "access",
                "network_id": "cell",
                "endpoints": [
                    {"node": "seg-a", "interface": "eth1"},
                    {"node": "meter", "interface": "eth0"},
                ],
            },
        ]
    }
    return address_plan, inventory, infrastructure, link_plan


@pytest.fixture(scope="module")
def plan(documents):
    _, inventory, infrastructure, link_plan = documents
    topology = build_plan(
        inventory, infrastructure, link_plan, template_name_by_image()
    )
    return topology, None


# -- topology ----------------------------------------------------------------


def test_plan_covers_every_declared_node_and_link(documents, plan):
    _, inventory, infrastructure, link_plan = documents
    topology, _ = plan

    expected_nodes = (
        len(inventory["devices"])
        + len(infrastructure["routers"])
        + len(infrastructure["switches"])
        + len(infrastructure["segments"])
    )
    assert len(topology.nodes) == expected_nodes
    assert len({node.name for node in topology.nodes}) == len(topology.nodes)
    assert len(topology.links) == len(link_plan["links"])
    assert topology.skipped == []
    assert topology.missing_templates == []


def test_switch_port_numbers_follow_the_specification_order(documents):
    _, _, infrastructure, _ = documents
    mapping = port_map(infrastructure)
    for switch in infrastructure["switches"]:
        roles = switch["attachment_points"]
        assert mapping[switch["name"]] == dict(zip(roles, range(len(roles))))


def test_switch_settings_follow_declared_roles_and_leave_spares_unassigned(documents):
    _, _, infrastructure, link_plan = documents
    settings = switch_port_settings(infrastructure, link_plan)
    roles = port_map(infrastructure)
    for switch in infrastructure["switches"]:
        name = switch["name"]
        assert set(settings[name]) <= set(roles[name].values())
        for number, setting in settings[name].items():
            assert roles[name][setting["role"]] == number
            declared = next(
                item for item in link_plan["links"] if item["id"] == setting["link_id"]
            )
            assert setting["vlans"] == sorted(declared.get("vlans", []))
            assert setting["mode"] == (
                "trunk"
                if declared["kind"] == "trunk"
                else "access"
                if declared["kind"] == "access" and setting["vlans"]
                else "untagged"
            )


def test_router_interfaces_become_adapter_numbers(documents, plan):
    _, _, infrastructure, link_plan = documents
    topology, _ = plan
    routers = {item["name"] for item in infrastructure["routers"]}
    declared = next(
        item
        for item in link_plan["links"]
        if all(end["node"] in routers for end in item["endpoints"])
    )
    built = next(item for item in topology.links if item.link_id == declared["id"])
    for endpoint in declared["endpoints"]:
        actual = next(
            end for end in (built.first, built.second) if end.node == endpoint["node"]
        )
        assert actual.adapter == int(endpoint["interface"].removeprefix("eth"))


def test_segments_are_created_as_switch_nodes(documents, plan):
    _, _, infrastructure, _ = documents
    topology, _ = plan
    kinds = {node.name: node.kind for node in topology.nodes}
    assert all(
        kinds[segment["name"]] == "switch" for segment in infrastructure["segments"]
    )


# -- builder -----------------------------------------------------------------


class FakeClient:
    """Stands in for Gns3Client, recording what a build would do."""

    def __init__(self, nodes=None, links=None):
        self._nodes = list(nodes or [])
        self._links = list(links or [])
        self.created_nodes: list[dict] = []
        self.created_links: list[dict] = []
        self.deleted: list[str] = []
        self.environments: list[tuple[str, str]] = []
        self.started_nodes: list[str] = []
        self.docker_switches = False

    def ensure_project(self, name):
        return Project("p1", name, "opened"), True

    def project_nodes(self, project_id):
        return list(self._nodes)

    def project_links(self, project_id):
        return list(self._links)

    def create_node(self, project_id, template_id, *, name=None, x=0, y=0, **kw):
        self.created_nodes.append({"name": name, "template_id": template_id})
        node = {"node_id": f"id-{name}", "name": name, "status": "stopped"}
        if self.docker_switches and template_id == "t-Metropolis Open vSwitch":
            node.update(
                {
                    "node_type": "docker",
                    "compute_id": "local",
                    "properties": {
                        "image": "metropolis/openvswitch:dev",
                        "container_id": f"cid-{name}",
                    },
                }
            )
        self._nodes.append(node)
        return Node(node["node_id"], name, "stopped")

    def start_node(self, project_id, node_id):
        self.started_nodes.append(node_id)
        next(node for node in self._nodes if node["node_id"] == node_id)["status"] = (
            "started"
        )

    def node(self, project_id, node_id):
        return next((node for node in self._nodes if node["node_id"] == node_id), None)

    def create_link(self, project_id, first, second):
        self.created_links.append((first, second))
        link = {
            "link_id": f"l{len(self._links)}",
            "nodes": [
                {
                    "node_id": first[0],
                    "adapter_number": first[1],
                    "port_number": first[2],
                },
                {
                    "node_id": second[0],
                    "adapter_number": second[1],
                    "port_number": second[2],
                },
            ],
        }
        self._links.append(link)
        return link

    def delete_node(self, project_id, node_id):
        self.deleted.append(node_id)
        self._nodes = [n for n in self._nodes if n["node_id"] != node_id]

    def set_node_environment(self, project_id, node_id, environment):
        self.environments.append((node_id, environment))
        return {}


def template_ids() -> dict[str, str]:
    """Template name to id, for devices and for the router and switch appliances."""
    names = set(template_name_by_image().values()) | set(APPLIANCES.values())
    return {name: f"t-{name}" for name in names}


def test_build_reruns_without_duplicating(plan):
    """A second build must reuse everything, not create it again."""
    topology, _ = plan
    client = FakeClient()
    first = build(client, "lab", topology, template_ids())
    again = build(client, "lab", topology, template_ids())

    assert again.created_nodes == []
    assert again.created_links == []
    assert len(again.reused_nodes) == len(topology.nodes)
    assert len(again.reused_links) == len(topology.links)
    assert again.switch_configuration == first.switch_configuration


def test_build_configures_new_and_reused_docker_switches(plan):
    topology, _ = plan
    client = FakeClient()
    client.docker_switches = True
    calls = []

    def execute(command, *, input, **_kwargs):
        payload = json.loads(input)
        calls.append((command, payload))
        changed = (
            list(expected_state(payload))
            if len(calls) <= len([n for n in topology.nodes if n.kind == "switch"])
            else []
        )
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {"verified": True, "changed": changed, "ports": expected_state(payload)}
            ),
            "",
        )

    first = build(
        client,
        "lab",
        topology,
        template_ids(),
        docker_hosts={"local": "unix:///var/run/docker.sock"},
        switch_execute=execute,
    )
    second = build(
        client,
        "lab",
        topology,
        template_ids(),
        docker_hosts={"local": "unix:///var/run/docker.sock"},
        switch_execute=execute,
    )
    count = len([node for node in topology.nodes if node.kind == "switch"])
    assert len(calls) == 2 * count
    assert len(client.started_nodes) == count
    assert all(
        item["status"] == "configured" for item in first.switch_configuration.values()
    )
    assert all(
        item["status"] == "unchanged" for item in second.switch_configuration.values()
    )
    assert calls[0][0][:3] == ["docker", "--host", "unix:///var/run/docker.sock"]
    assert second.created_nodes == []


def test_build_sets_the_environment_of_inventoried_devices(plan):
    topology, _ = plan
    client = FakeClient()
    build(client, "lab", topology, template_ids())

    names = {node.name for node in topology.nodes if node.environment}
    assert names, "the inventory should supply an environment for some node"
    touched = {node_id for node_id, _ in client.environments}
    assert touched == {f"id-{name}" for name in names}


def test_build_reports_a_node_whose_template_is_missing(plan):
    topology, _ = plan
    client = FakeClient()
    result = build(client, "lab", topology, {})

    assert result.created_nodes == []
    assert result.created_links == []
    # Every node is reported once, and then every link loses an endpoint.
    assert len(result.skipped) == len(topology.nodes) + len(topology.links)
    assert any("no template named" in item for item in result.skipped)
    assert any("an endpoint node is absent" in item for item in result.skipped)


def test_build_prune_deletes_nodes_absent_from_the_plan(plan):
    topology, _ = plan
    stale = {"node_id": "id-stale", "name": "MET-STALE-01", "status": "stopped"}
    client = FakeClient(nodes=[stale])
    build(client, "lab", topology, template_ids(), prune_missing=True)

    assert client.deleted == ["id-stale"]


def test_existing_link_keys_is_order_independent():
    links = [
        {
            "nodes": [
                {"node_id": "b", "adapter_number": 1, "port_number": 0},
                {"node_id": "a", "adapter_number": 2, "port_number": 3},
            ]
        }
    ]
    assert existing_link_keys(links) == {(("a", 2, 3), ("b", 1, 0))}


def test_segment_attachments_get_distinct_physical_ports(documents, plan):
    _, _, infrastructure, _ = documents
    topology, _ = plan
    for segment in infrastructure["segments"]:
        name = segment["name"]
        ends = [
            end
            for link in topology.links
            for end in (link.first, link.second)
            if end.node == name
        ]
        assert len({(end.adapter, end.port) for end in ends}) == len(ends)


def test_builder_rejects_wrong_existing_ports(plan):
    topology, _ = plan
    client = FakeClient()
    first = topology.links[0]
    client._nodes = [
        {"node_id": f"id-{node.name}", "name": node.name, "status": "stopped"}
        for node in topology.nodes
    ]
    client._links = [
        {
            "link_id": "wrong",
            "nodes": [
                {
                    "node_id": f"id-{first.first.node}",
                    "adapter_number": first.first.adapter + 1,
                    "port_number": first.first.port,
                },
                {
                    "node_id": f"id-{first.second.node}",
                    "adapter_number": first.second.adapter,
                    "port_number": first.second.port,
                },
            ],
        }
    ]
    result = build(client, "lab", topology, template_ids())
    assert first.link_id not in result.reused_links
    assert first.link_id not in result.created_links
    assert any(
        first.link_id in item and "wrong ports" in item for item in result.skipped
    )
    switch_ends = {first.first.node, first.second.node} & set(
        result.switch_configuration
    )
    for name in switch_ends:
        assert result.switch_configuration[name]["status"] == "failed"


# -- lifecycle ---------------------------------------------------------------


def test_startup_order_enforces_infrastructure_and_service_dependencies():
    nodes = [
        {"name": "MET-SCADA-01"},
        {"name": "MET-MQTT-BROKER-01"},
        {"name": "sw-plant-aggregation"},
        {"name": "MET-PLANT"},
        {"name": "mirai-cnc"},
        {"name": "mirai-listener"},
        {"name": "mirai-tftp"},
        {"name": "mirai-http"},
        {"name": "mirai-bot"},
        {"name": "merlin-cnc"},
        {"name": "merlin-agent"},
        {"name": "rdp-server"},
        {"name": "ops-client"},
        {"name": "engineer"},
    ]
    kinds = {
        "sw-plant-aggregation": "switch",
        "MET-PLANT": "router",
        "MET-MQTT-BROKER-01": "device",
        "MET-SCADA-01": "device",
    }
    images = {
        "MET-MQTT-BROKER-01": "metropolis/mqtt-broker:dev",
        "MET-SCADA-01": "metropolis/scada:dev",
        "mirai-cnc": "metropolis/mirai-cnc:dev",
        "mirai-listener": "metropolis/mirai-scan-listener:dev",
        "mirai-tftp": "metropolis/mirai-loader:dev",
        "mirai-http": "metropolis/mirai-wget-loader:dev",
        "mirai-bot": "metropolis/mirai-bot:dev",
        "merlin-cnc": "metropolis/merlin-cnc:dev",
        "merlin-agent": "metropolis/merlin-agent:dev",
        "rdp-server": "metropolis/rdp-server:dev",
        "ops-client": "metropolis/rdp-client:dev",
        "engineer": "metropolis/engineering:dev",
    }
    order = startup_order(nodes, kinds, images)
    assert order[:2] == ["sw-plant-aggregation", "MET-PLANT"]
    assert order.index("MET-MQTT-BROKER-01") < order.index("MET-SCADA-01")
    services = {"mirai-cnc", "mirai-listener", "mirai-tftp", "mirai-http", "merlin-cnc"}
    agents = {"mirai-bot", "merlin-agent"}
    assert max(order.index(name) for name in services) < min(
        order.index(name) for name in agents
    )
    assert order.index("rdp-server") < min(
        order.index(name) for name in ("ops-client", "engineer")
    )


def test_field_devices_start_before_controller_and_scada():
    images = {
        "scada": "metropolis/scada:dev",
        "controller": "metropolis/controller:dev",
        "drive": "metropolis/actuator:dev",
        "analyzer": "metropolis/analyzer:dev",
        "pressure": "metropolis/dp-instrument:dev",
        "level": "metropolis/level-instrument:dev",
    }
    order = startup_order([{"name": name} for name in images], {}, images)
    assert (
        max(order.index(name) for name in ("drive", "analyzer", "pressure", "level"))
        < order.index("controller")
        < order.index("scada")
    )


def test_start_all_starts_in_order_and_reports_failures():
    class Flaky(FakeClient):
        def __init__(self):
            super().__init__(
                nodes=[
                    {"node_id": "n-scada", "name": "MET-SCADA-01", "status": "stopped"},
                    {
                        "node_id": "n-sw",
                        "name": "sw-plant-aggregation",
                        "status": "stopped",
                    },
                ]
            )
            self.started: list[str] = []

        def start_node(self, project_id, node_id):
            if node_id == "n-scada":
                raise RuntimeError("no compute available")
            self.started.append(node_id)

    client = Flaky()
    result = start_all(
        client,
        "p1",
        {"sw-plant-aggregation": "switch", "MET-SCADA-01": "device"},
        {"MET-SCADA-01": "metropolis/scada:dev"},
        wait=False,
    )

    assert client.started == ["n-sw"]
    assert result.started == ["sw-plant-aggregation"]
    assert any("MET-SCADA-01" in item for item in result.failed)


def test_stop_all_reverses_the_start_order():
    class Recorder(FakeClient):
        def __init__(self):
            super().__init__(
                nodes=[
                    {"node_id": "n-sw", "name": "sw-a", "status": "started"},
                    {"node_id": "n-dev", "name": "MET-SCADA-01", "status": "started"},
                ]
            )
            self.stopped: list[str] = []

        def stop_node(self, project_id, node_id):
            self.stopped.append(node_id)

    client = Recorder()
    stop_all(client, "p1", {"sw-a": "switch"}, {"MET-SCADA-01": "metropolis/scada:dev"})
    assert client.stopped == ["n-dev", "n-sw"]


def test_wait_until_running_gives_up_and_reports_the_node():
    class Late(FakeClient):
        def project_nodes(self, project_id):
            return [{"node_id": "n1", "name": "slow", "status": "starting"}]

    clock_values = iter([0.0, 0.5, 1.0, 100.0])
    running, pending = wait_until_running(
        Late(),
        "p1",
        ["slow"],
        timeout=1.0,
        sleep=lambda _: None,
        clock=lambda: next(clock_values),
    )
    assert running == []
    assert pending == ["slow"]


# -- capture -----------------------------------------------------------------


class FakeCaptureClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.capture_payloads: list[dict] = []
        self.fail_start: str | None = None
        self.fail_after_start: str | None = None
        self.interrupt_after_start: str | None = None
        self.fail_stop: str | None = None
        self.fail_download: str | None = None
        self.missing_path: str | None = None
        self.empty_file: str | None = None

    def start_capture(self, project_id, link_id, **kwargs):
        if link_id == self.fail_start:
            raise RuntimeError("start rejected")
        self.started.append(link_id)
        self.capture_payloads.append(kwargs)
        if link_id == self.fail_after_start:
            raise RuntimeError("start response lost")
        if link_id == self.interrupt_after_start:
            raise KeyboardInterrupt
        return {"capture_file_path": f"/server/{link_id}.pcap"}

    def stop_capture(self, project_id, link_id):
        if link_id == self.fail_stop:
            raise RuntimeError("stop rejected")
        self.stopped.append(link_id)
        return {
            "link_id": link_id,
            "capture_file_path": (
                None if link_id == self.missing_path else f"/server/{link_id}.pcap"
            ),
        }

    def link(self, project_id, link_id):
        return {
            "link_id": link_id,
            "capturing": link_id in self.started and link_id not in self.stopped,
            "capture_file_path": None,
        }

    def download_pcap(self, project_id, link_id, destination):
        if link_id == self.fail_download:
            raise RuntimeError("download rejected")
        header = b"\xd4\xc3\xb2\xa1" + struct.pack("<HHIIII", 2, 4, 0, 0, 65535, 1)
        record = struct.pack("<IIII", 1, 0, 4, 4) + b"abcd"
        destination.write_bytes(
            header if link_id == self.empty_file else header + record
        )


@pytest.fixture
def built_capture(plan):
    topology, _ = plan
    client = FakeCaptureClient()
    build(client, "lab", topology, template_ids())
    declared = [link.link_id for link in topology.links]
    resolution = resolve_links(client, "p1", topology, declared)
    return client, topology, resolution


def test_builder_to_capture_mapping_uses_ports(built_capture):
    client, topology, resolution = built_capture
    assert len(resolution.resolved) == len(topology.links)
    assert all(item["status"] == "built" for item in resolution.status.values())
    chosen = topology.links[0]
    assert resolution.resolved[chosen.link_id] in {
        link["link_id"] for link in client.project_links("p1")
    }
    actual = next(
        link
        for link in client._links
        if link["link_id"] == resolution.resolved[chosen.link_id]
    )
    actual["nodes"][0]["adapter_number"] += 1
    changed = resolve_links(client, "p1", topology, [chosen.link_id])
    assert changed.status[chosen.link_id]["status"] == "miswired"
    assert f"gns3_{actual['link_id']}" in changed.capturable


def test_capture_detects_ambiguous_and_missing_links(built_capture):
    client, topology, resolution = built_capture
    first, second = topology.links[:2]
    link_id = resolution.resolved[first.link_id]
    matching = next(item for item in client._links if item["link_id"] == link_id)
    client._links.append({**matching, "link_id": "duplicate"})
    client._links = [
        item
        for item in client._links
        if item["link_id"] != resolution.resolved[second.link_id]
    ]
    changed = resolve_links(client, "p1", topology, [first.link_id, second.link_id])
    assert changed.status[first.link_id]["status"] == "ambiguous"
    assert changed.status[second.link_id]["status"] == "missing"
    assert f"gns3_{link_id}" in changed.capturable
    assert "gns3_duplicate" in changed.capturable


def test_all_built_includes_undeclared_physical_links(documents, built_capture):
    _, inventory, infrastructure, link_plan = documents
    client, topology, _ = built_capture
    client._links.append(
        {
            "link_id": "extra",
            "nodes": [
                {"node_id": "outside-a", "adapter_number": 0, "port_number": 0},
                {"node_id": "outside-b", "adapter_number": 1, "port_number": 0},
            ],
        }
    )
    declared = [link.link_id for link in topology.links]
    resolution = resolve_links(client, "p1", topology, declared)
    assert "gns3_extra" in resolution.capturable
    report = coverage_report(
        resolution, resolution.capturable, link_plan, inventory, infrastructure
    )
    assert report["undeclared_physical_links"] == ["gns3_extra"]
    assert report["all_network_traffic_verified"] is False


def test_coverage_distinguishes_local_routed_and_uncabled():
    resolution = LinkResolution(
        resolved={
            "host-link": "gns3-a",
            "router-link": "gns3-b",
            "other-router-link": "gns3-c",
        },
        status={
            "host-link": {"status": "built"},
            "router-link": {"status": "built"},
            "other-router-link": {"status": "built"},
        },
        declared_ids={"host-link", "router-link", "other-router-link"},
    )
    link_plan = {
        "links": [
            {
                "id": "host-link",
                "kind": "access",
                "endpoints": [{"node": "PLC"}, {"node": "SW"}],
            },
            {
                "id": "router-link",
                "kind": "transit",
                "endpoints": [{"node": "R"}, {"node": "SW"}],
            },
            {
                "id": "other-router-link",
                "kind": "transit",
                "endpoints": [{"node": "R"}, {"node": "R2"}],
            },
        ]
    }
    inventory = {"devices": [{"name": "PLC"}, {"name": "UNCABLED"}]}
    infrastructure = {
        "routers": [{"name": "R"}, {"name": "R2"}],
        "segments": [{"name": "UNCABLED-SEGMENT"}],
    }
    report = coverage_report(
        resolution, ["router-link"], link_plan, inventory, infrastructure
    )
    assert report["missing_local_points"] == ["host-link"]
    assert report["missing_routed_points"] == ["other-router-link"]
    assert report["uncabled_devices"] == ["UNCABLED"]
    assert report["uncabled_segments"] == ["UNCABLED-SEGMENT"]
    assert report["all_network_traffic_verified"] is False
    all_report = coverage_report(
        resolution, resolution.capturable, link_plan, inventory, infrastructure
    )
    assert all_report["modeled_local_paths_selected"]
    assert all_report["modeled_routed_paths_selected"]


def test_capture_stops_and_downloads_each_started_link(built_capture, tmp_path):
    client, _, resolution = built_capture
    selected = ["router-transit", "scada-link"]
    result = capture_for(
        client,
        "p1",
        resolution,
        selected,
        1,
        tmp_path,
        {"dataset_version": "v1"},
        sleep=lambda _: None,
    )
    assert set(client.started) == set(client.stopped)
    assert set(result.files) == set(selected)
    assert result.failed == []
    manifest = json.loads(result.manifest_path.read_text())
    for name in selected:
        entry = manifest["captures"][name]
        assert entry["gns3_link_id"] == resolution.resolved[name]
        assert entry["packet_count"] == 1
        assert len(entry["sha256"]) == 64
        assert entry["started_at_utc"] and entry["stopped_at_utc"]


def test_capture_interrupt_stops_only_acknowledged_starts(built_capture, tmp_path):
    client, topology, resolution = built_capture
    selected = [link.link_id for link in topology.links[:2]]

    def interrupt(_):
        raise KeyboardInterrupt

    result = capture_for(
        client, "p1", resolution, selected, 1, tmp_path, {}, sleep=interrupt
    )
    assert set(client.stopped) == set(client.started)
    assert any("KeyboardInterrupt" in item for item in result.failed)
    assert result.manifest_path.is_file()


def test_capture_timer_failure_still_stops_every_started_link(built_capture, tmp_path):
    client, topology, resolution = built_capture
    selected = [link.link_id for link in topology.links[:2]]

    def fail_timer(_):
        raise RuntimeError("timer failed")

    result = capture_for(
        client, "p1", resolution, selected, 1, tmp_path, {}, sleep=fail_timer
    )
    assert set(client.stopped) == set(client.started)
    assert any("timer failed" in item for item in result.failed)


def test_capture_partial_start_failure_stops_only_started(built_capture, tmp_path):
    client, topology, resolution = built_capture
    selected = [link.link_id for link in topology.links[:2]]
    client.fail_start = resolution.resolved[selected[1]]
    result = capture_for(client, "p1", resolution, selected, 1, tmp_path, {})
    assert client.started == client.stopped == [resolution.resolved[selected[0]]]
    assert selected[1] not in result.files
    assert any("start rejected" in item for item in result.failed)


@pytest.mark.parametrize("failure", ["fail_after_start", "interrupt_after_start"])
def test_capture_reconciles_start_that_succeeded_without_a_reply(
    built_capture, tmp_path, failure
):
    client, topology, resolution = built_capture
    selected = [link.link_id for link in topology.links[:2]]
    setattr(client, failure, resolution.resolved[selected[1]])
    result = capture_for(client, "p1", resolution, selected, 1, tmp_path, {})
    assert set(client.stopped) == set(client.started)
    assert set(result.files) == set(selected)
    assert any("capture start failed" in item for item in result.failed)


def test_capture_stop_and_download_failures_are_recorded(built_capture, tmp_path):
    client, topology, resolution = built_capture
    selected = [link.link_id for link in topology.links[:3]]
    client.fail_stop = resolution.resolved[selected[0]]
    client.fail_download = resolution.resolved[selected[1]]
    result = capture_for(
        client, "p1", resolution, selected, 1, tmp_path, {}, sleep=lambda _: None
    )
    assert selected[2] in result.files
    assert selected[0] not in result.files
    assert selected[1] not in result.files
    assert any("stop rejected" in item for item in result.failed)
    assert any("download rejected" in item for item in result.failed)
    assert len(client.stopped) == 2
    assert len(client.started) == 3
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["failures"] == result.failed


def test_capture_missing_path_and_empty_file_are_failures(built_capture, tmp_path):
    client, topology, resolution = built_capture
    name = topology.links[0].link_id
    link_id = resolution.resolved[name]
    client.missing_path = link_id
    client.start_capture = lambda *_args, **_kwargs: {}
    result = capture_for(client, "p1", resolution, [name], 1, tmp_path, {})
    assert any("capture_file_path" in item for item in result.failed)
    assert result.files == {}

    client.empty_file = link_id
    client.start_capture = lambda *_args, **_kwargs: {
        "capture_file_path": "/server/file.pcap"
    }
    again = capture_for(client, "p1", resolution, [name], 1, tmp_path, {})
    assert any("contains no packets" in item for item in again.failed)
    assert again.files == {}


def test_experiment_shares_run_id_and_reports_missing_log_sources(
    documents, built_capture, tmp_path
):
    address_plan, inventory, infrastructure, link_plan = documents
    client, topology, resolution = built_capture
    chosen = topology.links[0].link_id
    run_id = "test_shared_run"

    def fast_capture(*args, **kwargs):
        return capture_for(*args, **kwargs, sleep=lambda _: None)

    manifest = run_experiment(
        client,
        Project("p1", "lab", "opened"),
        topology,
        inventory,
        infrastructure,
        link_plan,
        tmp_path,
        run_id,
        0.01,
        {},
        selected_links=[chosen],
        metadata={"testbed_id": address_plan["testbed_id"], "dataset_version": "v1"},
        capture_fn=fast_capture,
        metrics_enabled=False,
    )
    capture_entry = manifest["capture"]["links"][chosen]
    assert capture_entry["gns3_link_id"] == resolution.resolved[chosen]
    assert capture_entry["sha256"] and capture_entry["packet_count"] == 1
    assert Path(capture_entry["local_file"]).is_file()
    assert Path(manifest["captures"][0]).parent == tmp_path / "captures" / run_id
    assert manifest["syslog"]["missing_sources"]
    assert manifest["status"] == "failed"
    timeline = [
        json.loads(line)
        for line in (tmp_path / "runs" / run_id / "timeline.jsonl")
        .read_text()
        .splitlines()
    ]
    assert [entry["event"] for entry in timeline] == [
        "run_started",
        "capture_started",
        "capture_finished",
        "run_finished",
    ]
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "schemas/experiment-run.schema.json"
        ).read_text()
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)


def test_experiment_records_hmi_origin_mqtt_check_after_capture_starts(
    documents, built_capture, tmp_path, monkeypatch
):
    _, inventory, infrastructure, link_plan = documents
    client, topology, _ = built_capture
    chosen = topology.links[0].link_id

    class FakeMqttRun:
        def __init__(self, *_args):
            self.result = {
                "scenario_id": "mqtt.acl_cross_cell.v1",
                "status": "planned",
                "source": "source-hmi",
                "target": "target-hmi",
                "broker": "broker",
                "scada": "scada",
                "own_topic": "lab/source/command",
                "cross_topic": "lab/target/command",
                "attempts": [],
                "broker_evidence": {},
                "subscriber_evidence": {},
                "downstream_delivery": "unverified",
                "capture_links": {},
                "failures": [],
            }

        def snapshot(self):
            return dict(self.result)

        def prepare(self):
            pass

        def set_capture_points(self, *_args):
            self.result["capture_links"] = {"source": [chosen], "broker": [chosen]}

        def mark_capture_started(self, started):
            assert chosen in started

        def start(self):
            assert client.started  # Capture starts before the simulated HMI publishes.
            self.result["status"] = "running"

        def finish(self):
            self.result["broker_evidence"] = {
                "own_received": True,
                "cross_denied": True,
            }
            self.result["subscriber_evidence"] = {
                "own_received": True,
                "cross_received": False,
            }
            self.result["downstream_delivery"] = "blocked_by_broker"
            self.result["status"] = "pending_capture"

        def mark_capture_saved(self, files):
            self.result["status"] = "complete" if chosen in files else "failed"

        def _failure(self, source, reason):
            self.result["failures"].append({"source": source, "reason": reason})
            self.result["status"] = "failed"

    def fast_capture(*args, **kwargs):
        return capture_for(*args, **kwargs, sleep=lambda _: None)

    monkeypatch.setattr(experiment, "MqttAclRun", FakeMqttRun)
    manifest = run_experiment(
        client,
        Project("p1", "lab", "opened"),
        topology,
        inventory,
        infrastructure,
        link_plan,
        tmp_path,
        "mqtt_acl_run",
        0.01,
        {},
        selected_links=[chosen],
        capture_fn=fast_capture,
        metrics_enabled=False,
        mqtt_acl_probe=("source-hmi", "target-hmi"),
    )
    assert manifest["mqtt_acl"]["status"] == "complete"
    event = json.loads(
        (tmp_path / "runs/mqtt_acl_run/attacks.jsonl").read_text().splitlines()[-1]
    )
    assert event["scenario_id"] == "mqtt.acl_cross_cell.v1"
    assert event["traffic_origin"]["node"] == "source-hmi"
    assert event["declared_capture_links"] == [chosen]
    assert event["subscriber_evidence"] == {
        "own_received": True,
        "cross_received": False,
    }
    manifest_schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "schemas/experiment-run.schema.json"
        ).read_text()
    )
    Draft202012Validator(manifest_schema, format_checker=FormatChecker()).validate(
        manifest
    )
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "schemas/attack-event.schema.json"
        ).read_text()
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(event)


def test_experiment_collects_router_syslog_and_pcap_under_one_run(
    documents, built_capture, tmp_path
):
    _, inventory, infrastructure, link_plan = documents
    client, topology, _ = built_capture
    syslog_receiver = next(
        item["name"]
        for item in inventory["devices"]
        if item["image"] == "metropolis/ntp:dev"
    )
    for node in client._nodes:
        if node["name"] == syslog_receiver:
            node.update(
                node_type="docker",
                status="started",
                compute_id="local",
                properties={"container_id": "ntp-container"},
            )
    router_lines = b"".join(
        (
            json.dumps(
                {
                    "_metropolis_syslog": True,
                    "timestamp": "2026-10-20T03:15:12Z",
                    "source": router["name"],
                    "event": "syslog_message",
                    "message": "interface up",
                }
            )
            + "\n"
        ).encode()
        for router in infrastructure["routers"]
    )
    attached = threading.Event()

    class Process:
        stdout = io.BytesIO(router_lines)
        stderr = io.BytesIO(b"")
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

    def collector_factory(*args, **kwargs):
        # This fixture supplies router relay output only; switch Docker nodes
        # and their log followers are covered by the focused collector tests.
        kwargs["expected_switch_syslog_sources"] = []

        def start(*_args, **_kwargs):
            attached.set()
            return Process()

        return DeviceLogCollector(*args, **kwargs, popen=start)

    def fast_capture(*args, **kwargs):
        assert attached.wait(2)
        return capture_for(*args, **kwargs, sleep=lambda _: None)

    chosen = topology.links[0].link_id
    manifest = run_experiment(
        client,
        Project("p1", "lab", "opened"),
        topology,
        inventory,
        infrastructure,
        link_plan,
        tmp_path,
        "complete_run",
        0.1,
        {"local": "ssh://host"},
        selected_links=[chosen],
        collector_factory=collector_factory,
        capture_fn=fast_capture,
        metrics_enabled=False,
    )
    assert manifest["status"] == "complete"
    assert len(manifest["syslog"]["sources"]) == len(infrastructure["routers"])
    assert manifest["syslog"]["missing_sources"] == []
    assert manifest["capture"]["links"][chosen]["sha256"]
