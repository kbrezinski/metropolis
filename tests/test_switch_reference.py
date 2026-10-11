"""Checks that the switch reference cannot drift from the topology data.

The per-switch Markdown specifications were deleted because eight documents that
no code reads will eventually describe a topology that does not exist. The single
reference file is generated instead, and these tests make the generation
authoritative: if the data changes and the file does not, this fails.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DATASET = ROOT / "testbeds/metropolis/datasets/water_treatment_v1"
SWITCH_DIR = ROOT / "testbeds/metropolis/switch"
REFERENCE = SWITCH_DIR / "README.md"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


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
def data():
    infrastructure = yaml.safe_load(
        (DATASET / "device_instances/infrastructure.yaml").read_text(encoding="utf-8")
    )
    links = yaml.safe_load(
        (DATASET / "topology/links.yaml").read_text(encoding="utf-8")
    )["links"]
    return infrastructure, links


# -- the file must match the data ---------------------------------------------


def test_the_reference_file_is_exactly_what_the_generator_writes(generator):
    """The whole point: the document cannot disagree with the topology."""
    on_disk = REFERENCE.read_text(encoding="utf-8")
    assert on_disk == generator.render(), (
        "testbeds/metropolis/switch/README.md is out of date. "
        "Run: uv run python scripts/generate_switch_reference.py --write"
    )


def test_the_reference_says_it_is_generated(generator):
    """A reader must not think this is hand-written and start editing it."""
    text = REFERENCE.read_text(encoding="utf-8")
    assert "Generated file. Do not edit by hand." in text
    assert "scripts/generate_switch_reference.py" in text


def test_check_mode_passes_on_the_current_file(generator):
    assert (SCRIPTS / "generate_switch_reference.py").is_file()
    assert generator.render() == REFERENCE.read_text(encoding="utf-8")


# -- the old per-switch specifications are gone --------------------------------


def test_the_per_switch_specification_files_are_deleted():
    remaining = [path for path in SWITCH_DIR.rglob("*.md") if path.name != "README.md"]
    assert remaining == [], f"these should have been removed: {remaining}"


def test_no_switch_entry_references_a_specification_document(data):
    """The schema no longer allows it, and nothing should be left pointing at one."""
    infrastructure, _ = data
    for switch in infrastructure["switches"]:
        assert "spec" not in switch, f"{switch['name']} still names a spec document"


def test_the_schema_no_longer_requires_a_spec(data):
    schema = json.loads(
        (ROOT / "schemas/infrastructure.schema.json").read_text(encoding="utf-8")
    )
    switch = schema["$defs"]["switch"]
    assert "spec" not in switch["required"]
    assert "spec" not in switch["properties"]


# -- the content must be complete and correct ---------------------------------


def test_every_switch_and_port_appears(data, generator):
    infrastructure, links = data
    text = REFERENCE.read_text(encoding="utf-8")
    ports = generator.index_ports(links)

    for switch in infrastructure["switches"]:
        assert f"## `{switch['name']}`" in text, f"{switch['name']} is missing"
        for number, role in enumerate(switch["attachment_points"]):
            assert f"| {number} | `{role}` |" in text, (
                f"{switch['name']} port {number} ({role}) is missing"
            )
            # A port with no link must be marked spare, not left blank.
            if (switch["name"], role) not in ports:
                assert f"| {number} | `{role}` | spare |" in text


def test_trunk_ports_list_exactly_the_link_vlans(data, generator):
    """VLAN numbers are the part a human would most easily get wrong by hand."""
    infrastructure, links = data
    text = REFERENCE.read_text(encoding="utf-8")
    ports = generator.index_ports(links)

    checked = 0
    for switch in infrastructure["switches"]:
        for role in switch["attachment_points"]:
            link = ports.get((switch["name"], role))
            if link is None or link.get("kind") != "trunk":
                continue
            expected = ", ".join(str(v) for v in link.get("vlans") or [])
            assert f"| trunk | {expected} |" in text, (
                f"{switch['name']}/{role} should carry VLANs {expected}"
            )
            checked += 1
    assert checked >= 15, f"expected the trunk ports to be checked, saw {checked}"


def test_the_mode_is_derived_from_the_link_kind(data, generator):
    """trunk links are tagged; everything else is an access port."""
    infrastructure, links = data
    text = REFERENCE.read_text(encoding="utf-8")
    ports = generator.index_ports(links)

    for switch in infrastructure["switches"]:
        for role in switch["attachment_points"]:
            link = ports.get((switch["name"], role))
            if link is None:
                continue
            mode = "trunk" if link.get("kind") == "trunk" else "access"
            assert f"| `{role}` | {mode} |" in text, (
                f"{switch['name']}/{role} should be {mode}"
            )


def test_every_far_end_named_in_the_document_is_a_real_node(data):
    """The document must not name a device that does not exist."""
    infrastructure, links = data

    devices = yaml.safe_load(
        (DATASET / "device_instances/initial_devices.yaml").read_text(encoding="utf-8")
    )["devices"]
    known = {device["name"] for device in devices}
    known |= {switch["name"] for switch in infrastructure["switches"]}
    known |= {segment["name"] for segment in infrastructure["segments"]}
    known |= {router["name"] for router in infrastructure["routers"]}

    for link in links:
        for endpoint in link["endpoints"]:
            if "port_role" in endpoint:
                continue  # a switch port, not a node
            assert endpoint["node"] in known, f"{endpoint['node']} is not a known node"
