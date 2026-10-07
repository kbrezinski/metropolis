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
