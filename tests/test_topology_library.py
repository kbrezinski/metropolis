"""Checks for the topology library.

The strongest check here is the round trip: the library must be able to describe
the checked-in dataset exactly, or it is not really a model of it. Everything
else builds on that.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from metropolis.topology import (  # noqa: E402
    Device,
    Endpoint,
    Interface,
    Network,
    Ports,
    Router,
    Segment,
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


@pytest.mark.parametrize("name", sorted(DOCUMENTS))
def test_each_bundled_document_is_reproduced_exactly(bundled, name):
    """Read the dataset, model it, write it back: the data must be identical."""
    original = yaml.safe_load(DOCUMENTS[name].read_text(encoding="utf-8"))
    assert bundled.to_dicts()[name] == original


def test_the_bundled_topology_has_no_contradictions(bundled):
    assert bundled.problems() == []


def test_the_bundled_topology_has_the_expected_scale(bundled):
    assert bundled.counts() == {
        "networks": 24,
        "routers": 6,
        "switches": 8,
        "segments": 13,
        "devices": 34,
        "links": 55,
    }


def test_saving_and_reloading_is_stable(bundled, tmp_path):
    bundled.save(tmp_path / "copy")
    reloaded = Topology.from_directory(tmp_path / "copy")
    assert reloaded.to_dicts() == bundled.to_dicts()
    assert reloaded.problems() == []


def test_save_creates_the_expected_layout(tmp_path):
    written = two_cell().save(tmp_path / "demo_v1")
    names = sorted(path.name for path in written)
    assert names == [
        "address-plan.yaml",
        "infrastructure.yaml",
        "initial_devices.yaml",
        "links.yaml",
    ]
    assert (tmp_path / "demo_v1/topology/address-plan.yaml").is_file()
    assert (tmp_path / "demo_v1/device_instances/infrastructure.yaml").is_file()


def test_documents_from_different_testbeds_are_refused():
    documents = {
        name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, path in DOCUMENTS.items()
    }
    documents["links"] = dict(documents["links"], testbed_id="somewhere-else")
    with pytest.raises(TopologyError, match="belongs to testbed"):
        to_topology(documents)


def test_a_missing_dataset_file_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError, match="dataset is missing"):
        Topology.from_directory(tmp_path / "nowhere")


# -- building -----------------------------------------------------------------


def test_a_built_topology_validates():
    assert two_cell().problems() == []


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


def test_reordering_ports_renumbers_them():
    """Changing the list order is how a port gets moved, so it must show."""
    switch = Switch("sw", attachment_points=("a", "b"))
    assert switch.port_number("a") == 0
    switch.attachment_points = ("b", "a")
    assert switch.port_number("a") == 1


@pytest.mark.parametrize(
    ("written", "node", "interface", "port_role"),
    [
        ("MET-PLANT:eth1", "MET-PLANT", "eth1", None),
        ("sw-a:router-uplink", "sw-a", None, "router-uplink"),
        ("PLC-A", "PLC-A", None, None),
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


def test_link_other_returns_the_far_end():
    lab = two_cell()
    link = lab.links[1]
    assert link.other("PLC-A").node == "sw-a"
    assert link.other("sw-a").node == "PLC-A"


def test_link_other_rejects_an_unrelated_node():
    with pytest.raises(TopologyError, match="does not touch"):
        two_cell().links[0].other("NOT-HERE")


def test_add_refuses_something_that_is_not_a_node():
    with pytest.raises(TopologyError, match="cannot add"):
        Topology().add("a string")


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


def test_a_switch_endpoint_may_name_no_port():
    """Naming no port attaches to the segment as a whole, which is allowed."""
    lab = two_cell()
    lab.add(Segment("seg-a", role="Access segment", network_id="cell-a"))
    lab.link("seg-link", "PLC-A", "seg-a")
    assert lab.problems() == []


def test_a_link_to_an_unplanned_network_is_caught():
    lab = two_cell()
    lab.links[0].network_id = "not-planned"
    assert any("not in the address plan" in problem for problem in lab.problems())


def test_a_gateway_outside_its_subnet_is_caught():
    lab = Topology()
    lab.add(Network("a", "10.0.0.0/24", gateway="10.0.9.1"))
    assert any("outside" in problem for problem in lab.problems())


def test_a_router_with_no_interfaces_is_caught():
    lab = two_cell()
    lab.routers[0].interfaces = ()
    assert any("declares no interfaces" in problem for problem in lab.problems())


def test_an_interface_that_is_neither_address_nor_trunk_is_caught():
    lab = two_cell()
    lab.routers[0].interfaces = (Interface("eth0"),)
    assert any(
        "neither an address nor a trunk" in problem for problem in lab.problems()
    )


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


def test_a_valid_topology_returns_itself_from_validate():
    lab = two_cell()
    assert lab.validate() is lab


# -- helpers ------------------------------------------------------------------


def test_node_names_covers_every_kind():
    lab = two_cell()
    lab.add(Segment("seg", network_id="cell-a"))
    assert lab.node_names() == {"R-EDGE", "sw-a", "PLC-A", "seg"}


def test_links_at_finds_both_ends():
    lab = two_cell()
    assert {link.id for link in lab.links_at("PLC-A")} == {"link-sw-plc"}
    assert {link.id for link in lab.links_at("sw-a")} == {"link-r-sw", "link-sw-plc"}
    assert lab.links_at("nothing") == []


def test_lookups_raise_for_unknown_names():
    lab = two_cell()
    for method, name in (
        (lab.network, "nowhere"),
        (lab.switch, "nowhere"),
        (lab.device, "nowhere"),
        (lab.router, "nowhere"),
    ):
        with pytest.raises(TopologyError, match="no "):
            method(name)


def test_summary_names_the_testbed_and_its_counts():
    text = two_cell().summary()
    assert "topology demo" in text
    assert "networks 2" in text
    assert "PLC-A" in text or "cell-a" in text


# -- serialising --------------------------------------------------------------


def test_optional_fields_are_left_out_rather_than_written_as_null():
    document = two_cell().to_dicts()["address-plan"]
    network = document["networks"][0]
    # No site was given, so the key should not appear at all.
    assert "site" not in network
    assert None not in network.values()


def test_short_lists_are_written_inline_and_long_ones_are_broken_out():
    """The generated YAML should read like the hand-written files.

    A VLAN list and an environment mapping are short, so they go inline; a
    document's own list of networks is long and goes one per line.
    """
    bundled = load_bundled_topology()

    address_plan = bundled.to_yaml()["address-plan"]
    assert "\nnetworks:\n- id: plant-intake" in address_plan

    devices = bundled.to_yaml()["initial-devices"]
    assert "protocols: [modbus-tcp]" in devices
    assert "environment: {" in devices

    infrastructure = bundled.to_yaml()["infrastructure"]
    assert "vlans: [10, 11, 12, 13, 50, 51, 52, 53]" in infrastructure


def test_a_document_without_a_description_omits_the_key():
    lab = Topology(testbed_id="bare")
    lab.add(Network("a", "10.0.0.0/24"))
    for document in lab.to_dicts().values():
        assert "description" not in document


def test_each_document_keeps_its_own_description():
    lab = two_cell()
    lab.links_description = "How the cables run"
    assert lab.to_dicts()["links"]["description"] == "How the cables run"
    assert "description" not in lab.to_dicts()["infrastructure"]


def test_yaml_output_is_reloadable(bundled):
    for text in bundled.to_yaml().values():
        assert isinstance(yaml.safe_load(text), dict)


# -- drawing ------------------------------------------------------------------


def test_dot_output_is_a_graph_with_every_node(bundled):
    dot = bundled.to_dot("Metropolis")
    assert dot.startswith("digraph topology {")
    assert dot.rstrip().endswith("}")
    for router in bundled.routers:
        assert f'"{router.name}"' in dot
    for switch in bundled.switches:
        assert f'"{switch.name}"' in dot


def test_dot_groups_devices_under_their_subnet(bundled):
    dot = bundled.to_dot()
    assert "subgraph cluster_plant_intake" in dot
    assert "vlan 10" in dot
    # The intake PLC belongs in the intake cluster.
    intake = dot.index("subgraph cluster_plant_intake")
    plc = dot.index('"MET-PLC-INTAKE-01"')
    assert plc > intake


def test_dot_edges_carry_vlan_information(bundled):
    dot = bundled.to_dot()
    assert "vlan 10,11,12,13,50,51,52,53" in dot


def test_dot_labels_are_escaped():
    lab = two_cell()
    lab.routers[0].name = 'R-"odd"'
    assert '\\"odd\\"' in lab.to_dot()


def test_mermaid_output_is_a_graph_with_routers_switches_and_devices():
    lab = two_cell()
    mermaid = lab.to_mermaid("Demo")
    assert mermaid.startswith("graph TD")
    assert "%% Demo" in mermaid
    assert 'R_EDGE["R-EDGE"]' in mermaid
    assert 'sw_a{{"sw-a"}}' in mermaid
    assert "subgraph cell_a" in mermaid
    assert 'PLC_A["PLC-A"]' in mermaid


def test_mermaid_identifiers_have_no_spaces_or_dashes():
    """Mermaid reads a bare identifier as a node reference, so it must be clean."""
    mermaid = two_cell().to_mermaid()
    for line in mermaid.splitlines():
        stripped = line.strip()
        if stripped.startswith("subgraph "):
            identifier = stripped.removeprefix("subgraph ").split("[")[0]
            assert "-" not in identifier
            assert " " not in identifier


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


def test_an_unknown_network_id_is_refused():
    lab = Topology()
    with pytest.raises(TopologyError, match="no network with id"):
        lab.add_device("A", subnet="nowhere")


# -- allocating addresses -----------------------------------------------------


def test_an_address_is_allocated_when_none_is_given():
    lab = Topology()
    lab.add_network("cell-a", "10.0.10.0/24", gateway="10.0.10.1")
    device = lab.add_device("PLC-A", image="img", subnet="cell-a")

    # The gateway holds .1, so the first host gets .2.
    assert device.address == "10.0.10.2/24"


def test_allocation_skips_the_gateway_and_taken_addresses():
    lab = Topology()
    lab.add_network("cell-a", "10.0.10.0/24", gateway="10.0.10.1")

    first = lab.add_device("A", subnet="cell-a")
    second = lab.add_device("B", subnet="cell-a")
    third = lab.add_device("C", subnet="cell-a")

    assert [first.address, second.address, third.address] == [
        "10.0.10.2/24",
        "10.0.10.3/24",
        "10.0.10.4/24",
    ]


def test_allocation_avoids_an_address_already_used():
    lab = Topology()
    lab.add_network("cell-a", "10.0.10.0/24", gateway="10.0.10.1")
    lab.add_device("A", subnet="cell-a", address="10.0.10.5/24")

    assert lab.add_device("B", subnet="cell-a").address == "10.0.10.2/24"


def test_allocation_skips_a_gateway_that_is_not_the_first_address():
    lab = Topology()
    lab.add_network("cell-a", "10.0.10.0/24", gateway="10.0.10.9")
    assert lab.allocate_address("10.0.10.0/24") == "10.0.10.1/24"


def test_allocation_refuses_a_subnet_with_no_room():
    lab = Topology()
    with pytest.raises(TopologyError, match="no usable host addresses"):
        lab.allocate_address("10.0.0.0/31")


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


def test_an_explicit_kind_is_not_overridden():
    lab = two_cell()
    link = lab.link("l", "sw-a:router-uplink", "PLC-A:eth0", kind="trunk", vlans=(10,))
    assert link.kind == "trunk"


def test_a_network_without_a_vlan_infers_nothing():
    lab = Topology()
    lab.add_network("plain", "192.168.0.0/24")
    lab.add(Switch("sw", ports=("p1",)), Device("d", subnet="192.168.0.0/24"))
    link = lab.link("l", "sw:p1", "d", network_id="plain")

    assert link.vlans == ()
    assert link.kind == "access"


# -- switch ports -------------------------------------------------------------


def test_a_switch_takes_ports_or_attachment_points():
    by_ports = Switch("a", ports=("p1", "p2"))
    by_attachment = Switch("b", attachment_points=("p1", "p2"))
    assert by_ports.ports.roles == by_attachment.ports.roles == ("p1", "p2")
    assert by_ports.attachment_points == ("p1", "p2")


def test_a_port_can_be_given_as_a_mapping_with_vlans():
    switch = Switch.with_ports(
        "sw",
        (
            SwitchPort("router-uplink", mode="trunk", vlans=(10, 11)),
            SwitchPort("plc-uplink", mode="access", vlans=(10,)),
        ),
    )
    assert switch.ports[0].vlans == (10, 11)
    assert switch.ports[0].mode == "trunk"
    assert switch.port_number("plc-uplink") == 1


def test_a_port_reads_as_an_object_or_a_mapping():
    switch = Switch.with_ports("sw", (SwitchPort("p1", mode="trunk", vlans=(10, 11)),))

    # Attribute access and mapping access must agree.
    assert switch.ports[0].vlans == switch.ports[0]["vlans"] == (10, 11)
    assert switch.ports[0].mode == switch.ports[0]["mode"] == "trunk"
    assert switch.ports[0].role == "p1"


def test_a_port_learns_its_vlans_from_the_link_on_it():
    """The cabling is the source of truth, so the port reports what it carries."""
    lab = two_cell()
    port = lab.ports_of("sw-a")[lab.ports_of("sw-a").index("router-uplink")]

    assert port.vlans == (10,)
    assert port.mode == "trunk"
    assert port.link is not None and port.link.id == "link-r-sw"


def test_port_vlans_and_mode_are_readable_directly():
    lab = two_cell()
    assert lab.port_vlans("sw-a", "router-uplink") == (10,)
    assert lab.port_mode("sw-a", "router-uplink") == "trunk"
    # The PLC link names no network, so the port carries nothing and its mode
    # is unknown rather than assumed to be an access port.
    assert lab.port_vlans("sw-a", "plc-uplink") == ()
    assert lab.port_mode("sw-a", "plc-uplink") is None


def test_a_spare_port_has_no_vlans_and_no_link():
    lab = two_cell()
    lab.add(Switch("sw-b", ports=("router-uplink", "spare-1")))
    assert lab.port_vlans("sw-b", "spare-1") == ()
    assert lab.port_mode("sw-b", "spare-1") is None
    assert lab.ports_of("sw-b")[1].link is None


def test_ports_compare_equal_to_the_role_list():
    """The dataset stores roles, so comparing against them must still work."""
    switch = Switch("sw", ports=("p1", "p2"))
    assert switch.ports == ["p1", "p2"]
    assert switch.ports == ("p1", "p2")
    assert "p1" in switch.ports
    assert "nope" not in switch.ports


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


# -- the worked example -------------------------------------------------------


def load_example(name: str):
    """Import the example script, which is documentation that must keep working."""
    import importlib.util

    path = ROOT / "scripts/build_a_topology.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_the_example_builds_and_validates():
    lab = load_example("example_topology").build()

    assert lab.problems() == []
    assert lab.counts()["devices"] == 3
    # Every host has its entrypoint settings derived, so none can drift.
    for device in lab.devices:
        assert device.environment["NODE_HOSTNAME"] == device.name
        assert device.environment["NODE_IP"] == device.address
        assert device.environment["NODE_GATEWAY"] == device.gateway


def test_the_example_allocates_addresses_rather_than_repeating_them():
    lab = load_example("example_topology_2").build()

    addresses = [device.address for device in lab.devices]
    assert len(addresses) == len(set(addresses)), "two hosts share an address"

    sensor = lab.device("SENSOR-A")
    assert sensor.subnet == "10.0.10.0/24", "the subnet was given by network id"
    assert sensor.environment["NODE_IP"] == sensor.address


def test_the_example_switch_reads_its_ports_from_the_cabling():
    lab = load_example("example_topology_3").build()

    assert lab.port_mode("sw-cells", "router-uplink") == "trunk"
    assert lab.port_vlans("sw-cells", "router-uplink") == (10, 11, 20)
    assert lab.port_mode("sw-cells", "cell-a-uplink") == "access"
    assert lab.port_vlans("sw-cells", "cell-a-uplink") == (10,)
    # The sensor is on the ops-uplink port but attached to cell A, so the port
    # reports cell A's VLAN: the cabling decides, not the port's name.
    assert lab.port_vlans("sw-cells", "ops-uplink") == (10,)
    # Cell B has no cable. Its mode was declared, so it is reported, but it
    # carries nothing.
    assert lab.port_vlans("sw-cells", "cell-b-uplink") == ()
    assert lab.ports_of("sw-cells")[2].link is None
