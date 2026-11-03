"""Checks for the topology drawing.

The drawing is built on networkx: the topology becomes a graph, networkx lays it
out, and networkx's matplotlib functions draw it. The rendering check verifies
that it produces a real image.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from metropolis.topology import (  # noqa: E402
    Interface,
    Router,
    Switch,
    Topology,
)
from metropolis.topology.draw import (  # noqa: E402
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


# -- rendering ----------------------------------------------------------------


@requires_matplotlib
def test_render_writes_a_png(small, tmp_path):
    target = tmp_path / "topology.png"
    assert render(small, target) == target
    assert target.stat().st_size > 1_000
    assert target.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
