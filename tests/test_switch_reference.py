"""Checks that the switch reference cannot drift from the topology data.

The per-switch Markdown specifications were deleted because eight documents that
no code reads will eventually describe a topology that does not exist. The single
reference file is generated instead, and these tests make the generation
authoritative: if the data changes and the file does not, this fails.

The reference is rendered through the topology library, so these tests use the
same library rather than re-parsing the YAML.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DATASET = ROOT / "testbeds/metropolis/datasets/water_treatment_v1"
SWITCH_DIR = ROOT / "testbeds/metropolis/switch"
REFERENCE = SWITCH_DIR / "README.md"

if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from metropolis.topology import Topology  # noqa: E402


def load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_switch_reference", SCRIPTS / "generate_switch_reference.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_switch_reference"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator():
    return load_generator()


@pytest.fixture(scope="module")
def topology() -> Topology:
    return Topology.from_directory(DATASET)


@pytest.fixture(scope="module")
def ports(topology) -> dict[tuple[str, str], object]:
    """(switch, port_role) -> the link using that port."""
    indexed = {}
    for link in topology.links:
        for endpoint in link.endpoints:
            if endpoint.port_role:
                indexed[(endpoint.node, endpoint.port_role)] = link
    return indexed


# -- the file must match the data ---------------------------------------------


def test_the_reference_file_is_exactly_what_the_generator_writes(generator):
    """The whole point: the document cannot disagree with the topology."""
    on_disk = REFERENCE.read_text(encoding="utf-8")
    assert on_disk == generator.render(), (
        "testbeds/metropolis/switch/README.md is out of date. "
        "Run: uv run python scripts/generate_switch_reference.py --write"
    )


def test_the_reference_says_it_is_generated():
    """A reader must not think this is hand-written and start editing it."""
    text = REFERENCE.read_text(encoding="utf-8")
    assert "Generated file. Do not edit by hand." in text
    assert "scripts/generate_switch_reference.py" in text
    assert "metropolis.topology" in text


# -- the old per-switch specifications are gone --------------------------------


def test_the_per_switch_specification_files_are_deleted():
    remaining = [path for path in SWITCH_DIR.rglob("*.md") if path.name != "README.md"]
    assert remaining == [], f"these should have been removed: {remaining}"


def test_the_schema_no_longer_requires_a_spec():
    schema = json.loads(
        (ROOT / "schemas/infrastructure.schema.json").read_text(encoding="utf-8")
    )
    switch = schema["$defs"]["switch"]
    assert "spec" not in switch["required"]
    assert "spec" not in switch["properties"]


# -- the content must be complete and correct ---------------------------------


def test_every_switch_and_port_appears(topology, ports):
    text = REFERENCE.read_text(encoding="utf-8")

    for switch in topology.switches:
        assert f"## `{switch.name}`" in text, f"{switch.name} is missing"
        for number, role in enumerate(switch.attachment_points):
            assert f"| {number} | `{role}` |" in text, (
                f"{switch.name} port {number} ({role}) is missing"
            )
            # A port with no link must be marked spare, not left blank.
            if (switch.name, role) not in ports:
                assert f"| {number} | `{role}` | spare |" in text


def test_trunk_ports_list_exactly_the_link_vlans(topology, ports):
    """VLAN numbers are the part a human would most easily get wrong by hand."""
    text = REFERENCE.read_text(encoding="utf-8")

    checked = 0
    for switch in topology.switches:
        for role in switch.attachment_points:
            link = ports.get((switch.name, role))
            if link is None or link.kind != "trunk":
                continue
            expected = ", ".join(str(v) for v in link.vlans)
            assert f"| trunk | {expected} |" in text, (
                f"{switch.name}/{role} should carry VLANs {expected}"
            )
            checked += 1
    assert checked >= 15, f"expected the trunk ports to be checked, saw {checked}"


def test_the_mode_is_derived_from_the_link_kind(topology, ports):
    """Trunk links are tagged; everything else is an access port."""
    text = REFERENCE.read_text(encoding="utf-8")

    for switch in topology.switches:
        for role in switch.attachment_points:
            link = ports.get((switch.name, role))
            if link is None:
                continue
            mode = "trunk" if link.kind == "trunk" else "access"
            assert f"| `{role}` | {mode} |" in text, (
                f"{switch.name}/{role} should be {mode}"
            )


def test_every_switch_in_the_reference_is_a_real_switch(topology):
    for switch in topology.switches:
        assert switch.attachment_points, f"{switch.name} has no ports to document"
        assert len(set(switch.attachment_points)) == len(switch.attachment_points), (
            f"{switch.name} repeats a port role, which would renumber ports"
        )


def test_the_bundled_topology_it_documents_is_valid(topology):
    assert topology.problems() == []
