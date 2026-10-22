"""Checks for the topology drawing.

The drawing is built on networkx: the topology becomes a graph, networkx lays it
out, and networkx's matplotlib functions draw it. These tests check the graph
that gets built and that rendering produces a real image.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from metropolis.topology import (  # noqa: E402
    Device,
    Endpoint,
    Interface,
    Link,
    Router,
    Switch,
    Topology,
    load_bundled_topology,
)
from metropolis.topology.draw import (  # noqa: E402
    COLOURS,
    LINK_COLOURS,
    _edge_label,
    _graphs,
    available,
    render,
)

requires_matplotlib = pytest.mark.skipif(
    not available(), reason="matplotlib or networkx is not installed"
)


@pytest.fixture(scope="module")
def bundled() -> Topology:
    return load_bundled_topology()


@pytest.fixture(scope="module")
def small() -> Topology:
    lab = Topology(testbed_id="demo")
    lab.add_network(
        "cell-a", "10.0.10.0/24", name="Cell A", vlan_id=10, gateway="10.0.10.1"
    )
    lab.add(
        Router(
            "R-EDGE",
            config="r.sh",
            interfaces=(Interface("eth0", kind="trunk", vlans=(10,)),),
        )
    )
    lab.add(Switch("sw-1", ports=("router-uplink", "cell-a-uplink")))
    lab.add_device("PLC-1", image="i", subnet="cell-a", auto_network=True)
    lab.link("l1", "R-EDGE:eth0", "sw-1:router-uplink", kind="trunk", vlans=(10,))
    lab.link("l2", "sw-1:cell-a-uplink", "PLC-1:eth0", network_id="cell-a")
    return lab


# -- the graph handed to networkx ---------------------------------------------


def test_every_node_becomes_a_graph_node(bundled):
    graph, kinds, _ = _graphs(bundled)
    assert graph.number_of_nodes() == 61
    assert set(graph.nodes()) == set(kinds)


def test_every_link_becomes_a_graph_edge(bundled):
    graph, _, _ = _graphs(bundled)
    assert graph.number_of_edges() == 55


def test_node_kinds_come_from_the_topology(bundled):
    _, kinds, _ = _graphs(bundled)
    assert kinds["MET-OT-CORE"] == "router"
    assert kinds["sw-plant-aggregation"] == "switch"
    assert kinds["sw-plant-cell-intake"] == "segment"
    assert kinds["MET-PLC-INTAKE-01"] == "device"

    counts: dict[str, int] = {}
    for kind in kinds.values():
        counts[kind] = counts.get(kind, 0) + 1
    assert counts == {"router": 6, "switch": 8, "segment": 13, "device": 34}


def test_endpoint_labels_carry_their_address(bundled):
    _, _, labels = _graphs(bundled)
    assert labels["MET-PLC-INTAKE-01"].startswith("MET-PLC-INTAKE-01")
    assert "10.20.10.10/24" in labels["MET-PLC-INTAKE-01"]


def test_infrastructure_labels_carry_no_address(bundled):
    _, _, labels = _graphs(bundled)
    assert labels["MET-OT-CORE"] == "MET-OT-CORE"


def test_edges_remember_their_kind_and_vlans(bundled):
    graph, _, _ = _graphs(bundled)
    trunks = [data for _, _, data in graph.edges(data=True) if data["kind"] == "trunk"]
    assert trunks, "the bundled topology has trunks"
    assert all(data["vlans"] for data in trunks), "a trunk should list its VLANs"


def test_a_link_whose_endpoint_is_not_a_node_is_left_out():
    """The graph should only contain nodes the topology knows about."""
    lab = Topology(testbed_id="x")
    lab.add(Switch("sw", ports=("a",)), Device("d", image="i"))
    lab.links.append(
        Link(
            id="ghost",
            first=Endpoint("sw", port_role="a"),
            second=Endpoint("GHOST"),
        )
    )
    graph, _, _ = _graphs(lab)
    assert "GHOST" not in graph
    assert graph.number_of_edges() == 0


def test_an_empty_topology_makes_an_empty_graph():
    graph, kinds, labels = _graphs(Topology(testbed_id="empty"))
    assert graph.number_of_nodes() == 0
    assert kinds == {} and labels == {}


# -- labels -------------------------------------------------------------------


def test_a_link_with_no_vlans_labels_as_its_kind():
    lab = Topology(testbed_id="x")
    lab.add(Switch("sw", ports=("a",)), Device("d", image="i"))
    assert _edge_label(lab.link("l", "sw:a", "d")) == "access"


def test_a_link_with_vlans_labels_with_them():
    lab = Topology(testbed_id="x")
    lab.add(Switch("sw", ports=("a", "b")), Switch("sw2", ports=("c",)))
    link = lab.link("l", "sw:a", "sw2:c", kind="trunk", vlans=(10, 11))
    assert _edge_label(link) == "trunk 10,11"


# -- colours ------------------------------------------------------------------


def test_every_node_type_has_a_colour():
    for kind in ("router", "switch", "segment", "device"):
        assert kind in COLOURS


def test_node_types_are_coloured_differently():
    assert len(set(COLOURS.values())) == len(COLOURS)


def test_trunk_colour_is_distinct_from_the_others():
    assert LINK_COLOURS["trunk"] not in {
        value for kind, value in LINK_COLOURS.items() if kind != "trunk"
    }


# -- rendering ----------------------------------------------------------------


@requires_matplotlib
def test_render_writes_a_png(bundled, tmp_path):
    target = tmp_path / "topology.png"
    assert render(bundled, target) == target
    assert target.stat().st_size > 10_000
    assert target.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"


@requires_matplotlib
def test_render_writes_a_vector_image(small, tmp_path):
    target = tmp_path / "topology.svg"
    render(small, target)
    text = target.read_text(encoding="utf-8")
    assert text.lstrip().startswith("<?xml")
    assert "svg" in text[:400]


@requires_matplotlib
def test_render_puts_the_title_and_note_in_the_picture(small, tmp_path):
    target = tmp_path / "t.svg"
    render(small, target, title="A title", note="A note")
    text = target.read_text(encoding="utf-8")
    assert "A title" in text
    assert "A note" in text


def _same_positions(first: dict, second: dict) -> bool:
    """Compare two networkx layouts, whose values are numpy arrays."""
    import numpy as np

    if set(first) != set(second):
        return False
    return all(np.array_equal(first[name], second[name]) for name in first)


@requires_matplotlib
def test_the_same_seed_gives_the_same_layout(small):
    """A force layout is random unless seeded, so this is the property to pin.

    The rendered file is not compared: matplotlib writes a timestamp into the
    SVG, so two identical layouts still differ byte for byte.
    """
    import networkx as nx

    graph, _, _ = _graphs(small)
    first = nx.spring_layout(graph, seed=3, k=0.55, iterations=250)
    second = nx.spring_layout(graph, seed=3, k=0.55, iterations=250)
    assert _same_positions(first, second)


@requires_matplotlib
def test_a_different_seed_gives_a_different_layout(small):
    import networkx as nx

    graph, _, _ = _graphs(small)
    first = nx.spring_layout(graph, seed=1, k=0.55, iterations=250)
    second = nx.spring_layout(graph, seed=99, k=0.55, iterations=250)
    assert not _same_positions(first, second)


@requires_matplotlib
def test_a_topology_with_one_node_still_renders(tmp_path):
    lab = Topology(testbed_id="one")
    lab.add(Device("ONLY", image="i"))
    target = tmp_path / "one.svg"
    render(lab, target)
    assert target.stat().st_size > 0


def test_an_empty_topology_is_refused():
    """Better to say so than to draw an empty picture."""
    with pytest.raises(ValueError, match="nothing to draw"):
        render(Topology(testbed_id="empty"), "unused.png")


def test_available_reports_the_dependency_state():
    assert isinstance(available(), bool)
