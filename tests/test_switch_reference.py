"""Focused behavior checks for switch-reference generation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from metropolis.topology import Device, Router, Switch, Topology  # noqa: E402


@pytest.fixture(scope="module")
def generator():
    spec = importlib.util.spec_from_file_location(
        "generate_switch_reference", SCRIPTS / "generate_switch_reference.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_switch_reference"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sample_topology() -> Topology:
    topology = Topology(testbed_id="switch-demo")
    topology.add(
        Switch("sw-demo", ports=("uplink", "instrument", "spare")),
        Router("r-demo", config="r.sh"),
        Device("meter-demo", image="demo/meter:latest"),
    )
    topology.link(
        "uplink",
        "sw-demo:uplink",
        "r-demo:eth0",
        kind="trunk",
        vlans=(10, 20),
        description="router uplink",
    )
    topology.link(
        "instrument",
        "sw-demo:instrument",
        "meter-demo:eth0",
        kind="access",
        vlans=(10,),
        description="field instrument",
    )
    return topology


def test_port_index_uses_endpoint_roles(generator, sample_topology):
    ports = generator.port_index(sample_topology)
    assert ports[("sw-demo", "uplink")].id == "uplink"
    assert ports[("sw-demo", "instrument")].id == "instrument"
    assert ("sw-demo", "spare") not in ports


def test_render_numbers_ports_and_describes_links(
    generator, sample_topology, monkeypatch
):
    monkeypatch.setattr(generator, "load", lambda: sample_topology)
    text = generator.render()

    assert "## `sw-demo`" in text
    assert "| 0 | `uplink` | trunk | 10, 20 | `r-demo` eth0 — router uplink |" in text
    assert (
        "| 1 | `instrument` | access | 10 | `meter-demo` eth0 — field instrument |"
        in text
    )
    assert "| 2 | `spare` | spare | — | nothing cabled |" in text
