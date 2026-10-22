import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_metropolis_topology.py"
DOCUMENTS = [
    "testbeds/metropolis/datasets/water_treatment_v1/topology/address-plan.yaml",
    "testbeds/metropolis/datasets/water_treatment_v1/topology/links.yaml",
    "testbeds/metropolis/datasets/water_treatment_v1/device_instances/initial_devices.yaml",
    "testbeds/metropolis/datasets/water_treatment_v1/device_instances/infrastructure.yaml",
]


def run_validator(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--repo-root", str(root)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_metropolis_topology_documents_are_consistent():
    result = run_validator(ROOT)

    assert result.returncode == 0, result.stdout + result.stderr
    # Assert every check passed rather than a fixed count, so adding a check
    # does not require editing this test.
    assert re.search(r"\b(\d+)/\1 checks passed", result.stdout), result.stdout


@pytest.fixture
def sandbox(tmp_path):
    """A throwaway checkout holding the validator, schemas, and dataset."""
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for relative in ("schemas", "scripts", "testbeds"):
        shutil.copytree(ROOT / relative, tmp_path / relative, ignore=ignore)
    return tmp_path


def test_validator_rejects_a_document_that_violates_its_schema(sandbox):
    """The schema check must fail the run, not just the check line."""
    target = (
        sandbox / "testbeds/metropolis/datasets/water_treatment_v1/topology/links.yaml"
    )
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "kind: transit", "kind: not-a-kind", 1
        ),
        encoding="utf-8",
    )

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert "Documents conform to their JSON schemas" in result.stdout
    assert "not-a-kind" in result.stdout


def test_validator_rejects_mismatched_testbed_ids(sandbox):
    """Documents describing different testbeds must be caught."""
    target = (
        sandbox
        / "testbeds/metropolis/datasets/water_treatment_v1/device_instances/infrastructure.yaml"
    )
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "testbed_id: metropolis", "testbed_id: somewhere-else", 1
        ),
        encoding="utf-8",
    )

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert "same testbed" in result.stdout
    assert "somewhere-else" in result.stdout


def test_validator_rejects_a_router_address_that_drifts_from_its_script(sandbox):
    """A router address disagreeing with its VyOS script must be reported."""
    target = (
        sandbox
        / "testbeds/metropolis/datasets/water_treatment_v1/device_instances/infrastructure.yaml"
    )
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "cidr: 10.20.254.2/30", "cidr: 10.20.254.2/29", 1
        ),
        encoding="utf-8",
    )

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert "router_plant.sh configures" in result.stdout


def test_validator_rejects_missing_ospf_advertisement(sandbox):
    target = sandbox / "testbeds/metropolis/router/backbone/router_ot_dmz.sh"
    text = target.read_text(encoding="utf-8")
    target.write_text(
        text.replace("set protocols ospf area 0 network '10.99.10.0/24'\n", ""),
        encoding="utf-8",
    )
    result = run_validator(sandbox)
    assert result.returncode != 0
    assert "OSPF networks differ from connected networks" in result.stdout
    assert "10.99.10.0/24" in result.stdout


def test_validator_rejects_trunk_vlan_mismatch(sandbox):
    target = sandbox / DOCUMENTS[1]
    text = target.read_text(encoding="utf-8")
    target.write_text(
        text.replace(
            "vlans: [10, 11, 12, 13, 50, 51, 52, 53]",
            "vlans: [10, 11, 12, 13, 50, 51, 52]",
            1,
        ),
        encoding="utf-8",
    )

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert "link-plant-switch: MET-PLANT eth1 trunk VLANs" in result.stdout


def test_validator_rejects_wrong_segment_vlan(sandbox):
    target = sandbox / DOCUMENTS[1]
    text = target.read_text(encoding="utf-8")
    target.write_text(
        text.replace(
            "id: link-plant-cell-intake-uplink\n    kind: trunk\n"
            "    description: Plant aggregation to the intake cell access switch\n"
            "    vlans: [10]",
            "id: link-plant-cell-intake-uplink\n    kind: trunk\n"
            "    description: Plant aggregation to the intake cell access switch\n"
            "    vlans: [11]",
            1,
        ),
        encoding="utf-8",
    )

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert "sw-plant-cell-intake: expected one uplink carrying VLAN 10" in result.stdout


def test_validator_rejects_host_attached_to_wrong_network(sandbox):
    target = sandbox / DOCUMENTS[1]
    text = target.read_text(encoding="utf-8")
    target.write_text(
        text.replace(
            "id: attach-intake-plc\n    kind: access\n"
            "    description: Intake PLC on the plant intake segment\n"
            "    network_id: plant-intake\n    cidr: 10.20.10.0/24",
            "id: attach-intake-plc\n    kind: access\n"
            "    description: Intake PLC on the plant intake segment\n"
            "    network_id: plant-dosing\n    cidr: 10.20.11.0/24",
            1,
        ),
        encoding="utf-8",
    )

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert "attach-intake-plc: MET-PLC-INTAKE-01 subnet" in result.stdout
    assert "sw-plant-cell-intake access port must use plant-intake" in result.stdout


def test_validator_rejects_router_interface_on_wrong_link_network(sandbox):
    target = sandbox / DOCUMENTS[1]
    text = target.read_text(encoding="utf-8")
    target.write_text(
        text.replace(
            "id: link-dmz-switch\n    kind: access\n"
            "    description: OT/DMZ router to the DMZ switch\n"
            "    network_id: ot-dmz\n    cidr: 10.20.40.0/24",
            "id: link-dmz-switch\n    kind: access\n"
            "    description: OT/DMZ router to the DMZ switch\n"
            "    network_id: enterprise\n    cidr: 10.30.10.0/24",
            1,
        ),
        encoding="utf-8",
    )

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert "link-dmz-switch: MET-OT-DMZ eth1 address" in result.stdout
