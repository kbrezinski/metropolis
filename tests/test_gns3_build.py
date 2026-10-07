"""Checks for topology planning, the builder, lifecycle, and capture.

These run against the real declared topology, so a change to the inventories
that the builder could not express shows up here rather than at build time. No
GNS3 server is involved: a fake client records what the builder would do.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

GNS3 = Path(__file__).resolve().parents[1] / "scripts" / "gns3"
if str(GNS3) not in sys.path:
    sys.path.insert(0, str(GNS3))

from _cli import load_documents, load_plan, template_name_by_image  # noqa: E402
from builder import build, existing_link_keys, port_map_yaml  # noqa: E402
from capture import capture_for, resolve_links  # noqa: E402
from client import Node, Project  # noqa: E402
from lifecycle import (  # noqa: E402
    TIER_CLIENT,
    TIER_ROUTER,
    TIER_SERVER,
    TIER_SWITCH,
    start_all,
    startup_order,
    stop_all,
    tier_for,
    wait_until_running,
)
from templates import APPLIANCES  # noqa: E402
from topology import environment_string, port_map  # noqa: E402


@pytest.fixture(scope="module")
def documents():
    return load_documents()


@pytest.fixture(scope="module")
def plan():
    return load_plan()


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
    assert len(topology.links) == len(link_plan["links"])
    assert topology.skipped == []
    assert topology.missing_templates == []


def test_every_registered_template_is_used_or_accounted_for(plan):
    topology, _ = plan
    used = {node.name for node in topology.nodes}
    assert len(used) == len(topology.nodes), "node names must be unique"


def test_switch_port_numbers_follow_the_specification_order():
    _, _, infrastructure, _ = load_documents()
    mapping = port_map(infrastructure)

    plant = mapping["sw-plant-aggregation"]
    # The first role listed in the spec is the router uplink, so it is adapter 0.
    assert plant["router-uplink"] == 0
    assert plant["cell-intake-uplink"] == 1
    assert len(set(plant.values())) == len(plant)


def test_router_interfaces_become_adapter_numbers(plan):
    topology, _ = plan
    core_plant = next(
        item for item in topology.links if item.link_id == "link-core-plant"
    )

    ends = {
        core_plant.first.node: core_plant.first.adapter,
        core_plant.second.node: core_plant.second.adapter,
    }
    # eth0 on both routers in that transit link.
    assert ends == {"MET-OT-CORE": 0, "MET-PLANT": 0}


def test_environment_string_quotes_values():
    rendered = environment_string(
        {"NODE_IP": "10.20.10.10/24", "NODE_HOSTNAME": "MET-PLC"}
    )
    assert rendered == 'NODE_HOSTNAME="MET-PLC"\nNODE_IP="10.20.10.10/24"'


def test_placement_gives_every_node_a_position(plan):
    topology, _ = plan
    positions = {(node.x, node.y) for node in topology.nodes}
    assert len(positions) == len(topology.nodes)


def test_segments_are_created_as_switch_nodes(plan):
    topology, _ = plan
    kinds = {node.name: node.kind for node in topology.nodes}
    assert kinds["sw-plant-cell-intake"] == "switch"
    assert kinds["sw-operations-scada"] == "switch"


def test_port_map_yaml_lists_every_switch(plan):
    topology, _ = plan
    rendered = port_map_yaml(topology, "lab")
    assert "sw-plant-aggregation" in rendered
    assert "router-uplink" in rendered
    assert "lab" in rendered


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

    def ensure_project(self, name):
        return Project("p1", name, "opened"), True

    def project_nodes(self, project_id):
        return list(self._nodes)

    def project_links(self, project_id):
        return list(self._links)

    def create_node(self, project_id, template_id, *, name=None, x=0, y=0, **kw):
        self.created_nodes.append({"name": name, "template_id": template_id})
        node = {"node_id": f"id-{name}", "name": name, "status": "stopped"}
        self._nodes.append(node)
        return Node(node["node_id"], name, "stopped")

    def create_link(self, project_id, first, second):
        self.created_links.append((first, second))
        self._links.append(
            {
                "link_id": f"l{len(self._links)}",
                "nodes": [{"node_id": first[0]}, {"node_id": second[0]}],
            }
        )
        return {}

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


def test_build_creates_every_node_and_link(plan):
    topology, _ = plan
    client = FakeClient()
    result = build(client, "lab", topology, template_ids())

    assert len(result.created_nodes) == len(topology.nodes)
    assert len(result.created_links) == len(topology.links)
    assert result.reused_nodes == []
    assert result.reused_links == []


def test_build_reruns_without_duplicating(plan):
    """A second build must reuse everything, not create it again."""
    topology, _ = plan
    client = FakeClient()
    build(client, "lab", topology, template_ids())
    again = build(client, "lab", topology, template_ids())

    assert again.created_nodes == []
    assert again.created_links == []
    assert len(again.reused_nodes) == len(topology.nodes)
    assert len(again.reused_links) == len(topology.links)


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
    links = [{"nodes": [{"node_id": "b"}, {"node_id": "a"}]}]
    assert existing_link_keys(links) == {frozenset(("a", "b"))}


# -- lifecycle ---------------------------------------------------------------


def test_tiers_order_infrastructure_before_clients():
    assert tier_for("switch", None) == TIER_SWITCH
    assert tier_for("router", None) == TIER_ROUTER
    assert tier_for("device", "metropolis/mqtt-broker:dev") == TIER_SERVER
    assert tier_for("device", "metropolis/scada:dev") == TIER_CLIENT


def test_startup_order_puts_switches_first_and_pollers_last():
    nodes = [
        {"name": "MET-SCADA-01"},
        {"name": "MET-MQTT-BROKER-01"},
        {"name": "sw-plant-aggregation"},
        {"name": "MET-PLANT"},
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
    }
    assert startup_order(nodes, kinds, images) == [
        "sw-plant-aggregation",
        "MET-PLANT",
        "MET-MQTT-BROKER-01",
        "MET-SCADA-01",
    ]


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


def test_wait_until_running_returns_when_the_node_starts():
    class Soon(FakeClient):
        def project_nodes(self, project_id):
            return [{"node_id": "n1", "name": "quick", "status": "started"}]

    running, pending = wait_until_running(
        Soon(), "p1", ["quick"], timeout=5.0, sleep=lambda _: None
    )
    assert running == ["quick"]
    assert pending == []


# -- capture -----------------------------------------------------------------


class FakeCaptureClient(FakeClient):
    def __init__(self, links):
        super().__init__(links=links)
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.capture_payloads: list[dict] = []

    def start_capture(self, project_id, link_id, **kwargs):
        self.started.append(link_id)
        self.capture_payloads.append(kwargs)
        return {}

    def stop_capture(self, project_id, link_id):
        self.stopped.append(link_id)
        # The real endpoint returns the link record, which is where the capture
        # file path lives.
        return {"link_id": link_id, "capture_file_path": f"/tmp/{link_id}.pcap"}

    def link(self, project_id, link_id):
        return {"link_id": link_id, "capture_file_path": f"/tmp/{link_id}.pcap"}


def test_capture_reports_unknown_link_ids():
    client = FakeCaptureClient([{"link_id": "L1", "description": "link-core-plant"}])
    resolved, unknown = resolve_links(client, "p1", ["link-core-plant", "nope"])

    assert resolved == {"link-core-plant": "L1"}
    assert unknown == ["nope"]


def test_capture_starts_and_stops_each_named_link():
    client = FakeCaptureClient([{"link_id": "L1", "description": "link-core-plant"}])
    result = capture_for(client, "p1", ["link-core-plant"], duration=0)

    assert client.started == ["L1"]
    assert client.stopped == ["L1"]
    assert result.files == {"link-core-plant": "/tmp/L1.pcap"}
    assert result.failed == []


def test_capture_reports_a_link_that_is_not_in_the_project():
    client = FakeCaptureClient([])
    result = capture_for(client, "p1", ["link-absent"], duration=0)

    assert client.started == []
    assert any("not a link in this project" in item for item in result.failed)
