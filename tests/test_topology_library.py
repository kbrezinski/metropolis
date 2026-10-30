"""Checks for the topology library.

The strongest check here is the round trip: the library must be able to describe
the checked-in dataset exactly, or it is not really a model of it. Everything
else builds on that.
"""

from __future__ import annotations

import sys
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from metropolis.topology import (  # noqa: E402
    Endpoint,
    Interface,
    Network,
    Ports,
    Router,
    Switch,
    SwitchPort,
    Topology,
    TopologyError,
    load_bundled_topology,
    to_topology,
)

DATASET = ROOT / "testbeds/metropolis/datasets/water_treatment_v1"
DOCUMENTS = {
    "address-plan": DATASET / "topology/address-plan.yaml",
    "links": DATASET / "topology/links.yaml",
    "initial-devices": DATASET / "device_instances/initial_devices.yaml",
    "infrastructure": DATASET / "device_instances/infrastructure.yaml",
}


@pytest.fixture(scope="module")
def bundled() -> Topology:
    return load_bundled_topology()


def two_cell() -> Topology:
    """A small topology used by most of the tests."""
    lab = Topology(testbed_id="demo", description="Two-cell test plant")
    lab.add_network(
        "cell-a", "10.0.10.0/24", name="Cell A", vlan_id=10, gateway="10.0.10.1"
    )
    lab.add_network("transit", "10.0.254.0/30", kind="transit", zone="routing_transit")
    lab.add(
        Router(
            "R-EDGE",
            config="router_edge.sh",
            interfaces=(
                Interface("eth0", cidr="10.0.254.1/30"),
                Interface("eth1", kind="trunk", vlans=(10,)),
            ),
        ),
        Switch("sw-a", attachment_points=("router-uplink", "plc-uplink")),
    )
    lab.add_device(
        "PLC-A",
        image="metropolis/controller:dev",
        subnet="10.0.10.0/24",
        address="10.0.10.10/24",
        gateway="10.0.10.1",
        environment={
            "NODE_HOSTNAME": "PLC-A",
            "NODE_IP": "10.0.10.10/24",
            "NODE_GATEWAY": "10.0.10.1",
        },
    )
    lab.link(
        "link-r-sw",
        "R-EDGE:eth1",
        "sw-a:router-uplink",
        kind="trunk",
        vlans=(10,),
        description="Edge trunk",
    )
    lab.link("link-sw-plc", "sw-a:plc-uplink", "PLC-A:eth0")
    return lab


# -- the round trip is the point ----------------------------------------------


@pytest.mark.parametrize("name", ["initial-devices", "links"])
def test_each_bundled_document_is_reproduced_exactly(bundled, name):
    """Read the dataset, model it, write it back: the data must be identical."""
    original = yaml.safe_load(DOCUMENTS[name].read_text(encoding="utf-8"))
    assert bundled.to_dicts()[name] == original


def test_saving_and_reloading_is_stable(tmp_path):
    topology = two_cell()
    topology.save(tmp_path / "copy")
    reloaded = Topology.from_directory(tmp_path / "copy")
    assert reloaded.to_dicts() == topology.to_dicts()


def test_vti_overlays_round_trip_without_becoming_physical_ports(tmp_path):
    lab = Topology(testbed_id="custom_vpn")
    lab.add(Network("vpn", "10.90.0.0/30", kind="overlay"))
    for name, interface, address in (
        ("EDGE", "vti7", "10.90.0.1/30"),
        ("SITE", "vti9", "10.90.0.2/30"),
    ):
        lab.add(
            Router(
                name,
                config=f"{name}.sh",
                role="VPN boundary",
                interfaces=(Interface(interface, kind="vti", cidr=address),),
            )
        )
    overlay = lab.link(
        "vpn-link",
        "EDGE:vti7",
        "SITE:vti9",
        kind="overlay",
        network_id="vpn",
        cidr="10.90.0.0/30",
        description="Logical IPsec",
    )
    lab.validate().save(tmp_path / "custom")
    restored = Topology.from_directory(tmp_path / "custom")
    assert restored.to_dicts() == lab.to_dicts()
    assert overlay.first.interface == "vti7"
    assert overlay.first.port_role is None
    for document, schema_file in (
        ("infrastructure", "infrastructure.schema.json"),
        ("links", "link-plan.schema.json"),
    ):
        schema = json.loads((ROOT / "schemas" / schema_file).read_text())
        Draft202012Validator(schema).validate(restored.to_dicts()[document])
    overlay.kind = "transit"
    with pytest.raises(TopologyError, match="logical overlay, not a physical cable"):
        lab.validate()


def test_overlay_requires_declared_vti_endpoints_on_its_network():
    lab = Topology(testbed_id="custom_vpn")
    lab.add(Network("vpn", "10.90.0.0/30", kind="overlay"))
    lab.add(
        Router(
            "EDGE",
            config="edge.sh",
            interfaces=(Interface("vti7", kind="vti", cidr="10.90.0.1/30"),),
        )
    )
    lab.add(
        Router(
            "SITE",
            config="site.sh",
            interfaces=(Interface("vti9", kind="vti", cidr="10.91.0.1/30"),),
        )
    )
    lab.link(
        "vpn-link",
        "EDGE:vti7",
        "SITE:vti9",
        kind="overlay",
        network_id="vpn",
        cidr="10.90.0.0/30",
    )
    with pytest.raises(TopologyError, match="does not match overlay CIDR"):
        lab.validate()
    lab.links[0].second = Endpoint("SITE", interface="eth0")
    with pytest.raises(TopologyError, match="must select a declared router VTI"):
        lab.validate()


def test_documents_from_different_testbeds_are_refused():
    documents = {
        name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, path in DOCUMENTS.items()
    }
    documents["links"] = dict(documents["links"], testbed_id="somewhere-else")
    with pytest.raises(TopologyError, match="belongs to testbed"):
        to_topology(documents)


# -- building -----------------------------------------------------------------


def test_ports_are_numbered_by_position():
    """This is the rule the whole switch build depends on."""
    switch = Switch("sw", attachment_points=("router-uplink", "a-uplink", "b-uplink"))
    assert switch.port_number("router-uplink") == 0
    assert switch.port_number("a-uplink") == 1
    assert switch.port_number("b-uplink") == 2


def test_an_unknown_port_role_is_refused():
    switch = Switch("sw", attachment_points=("router-uplink",))
    with pytest.raises(TopologyError, match="has no port role"):
        switch.port_number("nowhere")


@pytest.mark.parametrize(
    ("written", "node", "interface", "port_role"),
    [
        ("MET-PLANT:eth1", "MET-PLANT", "eth1", None),
        ("sw-a:router-uplink", "sw-a", None, "router-uplink"),
    ],
)
def test_endpoint_shorthand_is_parsed(written, node, interface, port_role):
    endpoint = Endpoint.parse(written)
    assert (endpoint.node, endpoint.interface, endpoint.port_role) == (
        node,
        interface,
        port_role,
    )


def test_endpoints_can_be_written_as_mappings():
    endpoint = Endpoint.parse({"node": "sw-a", "port_role": "uplink"})
    assert endpoint.node == "sw-a"
    assert endpoint.port_role == "uplink"


def test_an_unreadable_endpoint_is_refused():
    with pytest.raises(TopologyError, match="cannot read link endpoint"):
        Endpoint.parse(42)


# -- validation catches real mistakes -----------------------------------------


def test_a_duplicate_name_is_caught():
    lab = Topology()
    lab.add(Network("a", "10.0.0.0/24"), Network("a", "10.0.1.0/24"))
    assert any("defined more than once" in problem for problem in lab.problems())


def test_overlapping_networks_are_caught():
    lab = Topology()
    lab.add(Network("a", "10.0.0.0/24"), Network("b", "10.0.0.128/25"))
    assert any("overlap" in problem for problem in lab.problems())


def test_an_address_outside_its_subnet_is_caught():
    lab = two_cell()
    lab.device("PLC-A").address = "10.0.99.5/24"
    assert any("is not in" in problem for problem in lab.problems())


def test_a_device_on_an_unplanned_subnet_is_caught():
    lab = two_cell()
    lab.device("PLC-A").subnet = "192.168.1.0/24"
    assert any("not in the address plan" in problem for problem in lab.problems())


def test_duplicate_device_addresses_are_caught():
    topology = Topology(testbed_id="example")
    topology.add_network("cell", "10.42.0.0/24")
    topology.add_device("sensor-a", subnet="10.42.0.0/24", address="10.42.0.20/24")
    topology.add_device("sensor-b", subnet="10.42.0.0/24", address="10.42.0.20/24")
    assert "devices sensor-a and sensor-b share address 10.42.0.20" in (
        topology.problems()
    )


def test_a_gateway_mismatch_is_caught():
    lab = two_cell()
    lab.device("PLC-A").gateway = "10.0.10.254"
    assert any("does not match" in problem for problem in lab.problems())


def test_a_contradictory_node_environment_is_caught():
    """The entrypoint applies NODE_IP, so it must agree with the address."""
    lab = two_cell()
    lab.device("PLC-A").environment["NODE_IP"] = "10.0.10.99/24"
    assert any("NODE_IP" in problem for problem in lab.problems())


def test_a_link_to_an_unknown_node_is_caught():
    lab = two_cell()
    lab.link("bad", "PLC-A", "NOT-A-NODE")
    assert any("is not a node" in problem for problem in lab.problems())


def test_a_link_to_an_unknown_switch_port_is_caught():
    lab = two_cell()
    lab.link("bad", "sw-a:nonexistent", "PLC-A")
    assert any("has no port role" in problem for problem in lab.problems())


def test_a_link_to_an_unplanned_network_is_caught():
    lab = two_cell()
    lab.links[0].network_id = "not-planned"
    assert any("not in the address plan" in problem for problem in lab.problems())


def test_a_gateway_outside_its_subnet_is_caught():
    lab = Topology()
    lab.add(Network("a", "10.0.0.0/24", gateway="10.0.9.1"))
    assert any("outside" in problem for problem in lab.problems())


def test_validate_raises_and_lists_every_problem():
    lab = two_cell()
    lab.device("PLC-A").address = "10.0.99.5/24"
    # Keep the entrypoint settings consistent, so the address is the only fault.
    lab.device("PLC-A").environment["NODE_IP"] = "10.0.99.5/24"
    lab.links[0].network_id = "not-planned"
    with pytest.raises(TopologyError) as caught:
        lab.validate()
    message = str(caught.value)
    assert "2 topology problem(s)" in message
    assert "is not in" in message
    assert "not in the address plan" in message


# -- serialising --------------------------------------------------------------


# -- drawing ------------------------------------------------------------------


def test_dot_output_is_a_graph_with_routers_and_switches(bundled):
    dot = bundled.to_dot("Metropolis")
    assert dot.startswith("digraph topology {")
    assert dot.rstrip().endswith("}")
    for router in bundled.routers:
        assert f'"{router.name}"' in dot
    for switch in bundled.switches:
        assert f'"{switch.name}"' in dot


def test_dot_labels_are_escaped():
    lab = two_cell()
    lab.routers[0].name = 'R-"odd"'
    assert '\\"odd\\"' in lab.to_dot()


def test_mermaid_edges_carry_the_kind_and_vlans():
    mermaid = two_cell().to_mermaid()
    assert "|trunk vlan 10|" in mermaid
    assert "|access|" in mermaid


# -- deriving the network settings --------------------------------------------


def test_auto_network_fills_in_the_gateway_and_entrypoint_values():
    """Identity is stated once; the values the container applies follow."""
    lab = Topology()
    lab.add_network("cell-a", "10.0.10.0/24", vlan_id=10, gateway="10.0.10.1")
    device = lab.add_device(
        "PLC-A",
        image="img",
        subnet="cell-a",
        address="10.0.10.10/24",
        auto_network=True,
    )

    assert device.gateway == "10.0.10.1"
    assert device.environment["NODE_HOSTNAME"] == "PLC-A"
    assert device.environment["NODE_IP"] == "10.0.10.10/24"
    assert device.environment["NODE_GATEWAY"] == "10.0.10.1"
    assert lab.problems() == []


def test_auto_network_keeps_the_settings_in_step_when_the_address_changes():
    lab = Topology()
    lab.add_network("cell-a", "10.0.10.0/24", gateway="10.0.10.1")
    device = lab.add_device(
        "PLC-A",
        image="img",
        subnet="cell-a",
        address="10.0.10.10/24",
        auto_network=True,
    )

    device.address = "10.0.10.11/24"
    device.refresh_network()

    assert device.environment["NODE_IP"] == "10.0.10.11/24"
    assert lab.problems() == []


def test_without_auto_network_nothing_is_derived():
    """The default stays explicit, so the existing dataset round-trips."""
    lab = Topology()
    lab.add_network("cell-a", "10.0.10.0/24", gateway="10.0.10.1")
    device = lab.add_device("PLC-A", image="img", subnet="cell-a")

    assert device.gateway is None
    assert device.environment == {}


def test_a_subnet_can_be_named_by_network_id_or_cidr():
    lab = Topology()
    lab.add_network("cell-a", "10.0.10.0/24", gateway="10.0.10.1")

    by_id = lab.add_device("A", subnet="cell-a")
    by_cidr = lab.add_device("B", subnet="10.0.10.0/24")

    assert by_id.subnet == by_cidr.subnet == "10.0.10.0/24"


# -- allocating addresses -----------------------------------------------------


def test_allocation_skips_the_gateway_and_taken_addresses():
    lab = Topology()
    lab.add_network("cell-a", "10.0.10.0/24", gateway="10.0.10.1")
    lab.add_device("reserved", subnet="cell-a", address="10.0.10.2/24")

    first = lab.add_device("A", subnet="cell-a")
    second = lab.add_device("B", subnet="cell-a")
    third = lab.add_device("C", subnet="cell-a")

    assert [first.address, second.address, third.address] == [
        "10.0.10.3/24",
        "10.0.10.4/24",
        "10.0.10.5/24",
    ]


def test_allocation_reports_a_full_subnet():
    lab = Topology()
    lab.add_device("A", subnet="10.0.0.0/30", address="10.0.0.1/30")
    lab.add_device("B", subnet="10.0.0.0/30", address="10.0.0.2/30")
    with pytest.raises(TopologyError, match="no free host addresses"):
        lab.allocate_address("10.0.0.0/30")


# -- inferring VLANs from the network -----------------------------------------


def test_link_infers_vlans_from_the_network():
    """A port's membership is stated once, on the network."""
    lab = two_cell()
    link = lab.link("l", "sw-a:router-uplink", "PLC-A:eth0", network_id="cell-a")

    assert link.vlans == (10,)
    assert link.kind == "access", "one VLAN is an access port"


def test_link_infers_a_trunk_when_several_vlans_are_named():
    lab = two_cell()
    link = lab.link("l", "sw-a:router-uplink", "PLC-A:eth0", vlans=(10, 11))

    assert link.kind == "trunk"


# -- switch ports -------------------------------------------------------------


def test_a_port_learns_its_vlans_from_the_link_on_it():
    """The cabling is the source of truth, so the port reports what it carries."""
    lab = two_cell()
    port = lab.ports_of("sw-a")[lab.ports_of("sw-a").index("router-uplink")]

    assert port.vlans == (10,)
    assert port.mode == "trunk"
    assert port.link is not None and port.link.id == "link-r-sw"


def test_a_spare_port_has_no_vlans_and_no_link():
    lab = two_cell()
    lab.add(Switch("sw-b", ports=("router-uplink", "spare-1")))
    assert lab.port_vlans("sw-b", "spare-1") == ()
    assert lab.port_mode("sw-b", "spare-1") is None
    assert lab.ports_of("sw-b")[1].link is None


def test_a_port_stated_twice_with_different_vlans_is_caught():
    """A port and its link must not disagree about what the port carries."""
    lab = two_cell()
    lab.switch("sw-a").ports = Ports(
        (SwitchPort("router-uplink", vlans=(99,)), SwitchPort("plc-uplink"))
    )
    assert any("its link carries" in problem for problem in lab.problems())


def test_a_port_whose_mode_contradicts_its_vlans_is_caught():
    lab = two_cell()
    lab.switch("sw-a").ports = Ports(
        (
            SwitchPort("router-uplink", mode="access", vlans=(10, 11)),
            SwitchPort("plc-uplink"),
        )
    )
    assert any("does not match" in problem for problem in lab.problems())


def test_only_roles_are_written_to_yaml():
    """A port's VLANs live on its link, so writing them again would duplicate."""
    lab = two_cell()
    lab.switch("sw-a").ports = Ports(
        (
            SwitchPort("router-uplink", mode="trunk", vlans=(10,)),
            SwitchPort("plc-uplink"),
        )
    )
    document = lab.to_dicts()["infrastructure"]
    switch = next(item for item in document["switches"] if item["name"] == "sw-a")

    assert switch["attachment_points"] == ["router-uplink", "plc-uplink"]
    assert "ports" not in switch
