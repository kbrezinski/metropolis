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
)
from metropolis.topology.draw import (  # noqa: E402
    _edge_label,
    _graphs,
    available,
    render,
)

requires_matplotlib = pytest.mark.skipif(
    not available(), reason="matplotlib or networkx is not installed"
)


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


def test_every_node_becomes_a_graph_node(small):
    graph, kinds, _ = _graphs(small)
    assert graph.number_of_nodes() == 3
    assert set(graph.nodes()) == set(kinds)


def test_every_link_becomes_a_graph_edge(small):
    graph, _, _ = _graphs(small)
    assert graph.number_of_edges() == 2


def test_node_kinds_come_from_the_topology(small):
    _, kinds, _ = _graphs(small)
    assert kinds == {"R-EDGE": "router", "sw-1": "switch", "PLC-1": "device"}

    counts: dict[str, int] = {}
    for kind in kinds.values():
        counts[kind] = counts.get(kind, 0) + 1
    assert counts == {"router": 1, "switch": 1, "device": 1}


def test_endpoint_labels_carry_their_address(small):
    _, _, labels = _graphs(small)
    assert labels["PLC-1"].startswith("PLC-1")
    assert str(small.devices[0].address) in labels["PLC-1"]


def test_infrastructure_labels_carry_no_address(small):
    _, _, labels = _graphs(small)
    assert labels["R-EDGE"] == "R-EDGE"


def test_edges_remember_their_kind_and_vlans(small):
    graph, _, _ = _graphs(small)
    trunks = [data for _, _, data in graph.edges(data=True) if data["kind"] == "trunk"]
    assert trunks
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


# -- rendering ----------------------------------------------------------------


@requires_matplotlib
def test_render_writes_a_png(small, tmp_path):
    target = tmp_path / "topology.png"
    assert render(small, target) == target
    assert target.stat().st_size > 1_000
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
