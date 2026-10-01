"""Load configuration data shipped with the Metropolis package."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
import re
from typing import Any

import yaml

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]*$")
_ADDRESS_PLAN_RESOURCE = Path("data") / "testbeds"


def load_address_plan(
    testbed_id: str = "metropolis",
    dataset_version: str = "water_treatment_v1",
) -> dict[str, Any]:
    """Load a bundled testbed address plan as a Python mapping.

    The packaged resource is used for installed distributions. In a source
    checkout, the repository's testbed YAML is used when the package resource
    is not present yet.
    """
    for label, value in (
        ("testbed_id", testbed_id),
        ("dataset_version", dataset_version),
    ):
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError(f"Invalid {label}: {value!r}")

    resource_path = (
        _ADDRESS_PLAN_RESOURCE
        / testbed_id
        / dataset_version
        / "topology"
        / "address-plan.yaml"
    )
    package_resource = files("metropolis").joinpath(*resource_path.parts)

    try:
        content = package_resource.read_text(encoding="utf-8")
    except FileNotFoundError:
        content = _read_source_checkout_plan(testbed_id, dataset_version)

    plan = yaml.safe_load(content)
    if not isinstance(plan, dict):
        raise ValueError(f"Address plan for {testbed_id!r} must be a YAML mapping")
    if plan.get("testbed_id") != testbed_id:
        raise ValueError(
            f"Address plan identity mismatch: requested {testbed_id!r}, "
            f"found {plan.get('testbed_id')!r}"
        )
    if not isinstance(plan.get("networks"), list):
        raise ValueError("Address plan must contain a 'networks' list")
    return plan


def _read_source_checkout_plan(testbed_id: str, dataset_version: str) -> str:
    """Read the canonical YAML from a source checkout as a dev-time fallback."""
    repository_root = Path(__file__).resolve().parents[2]
    source_path = (
        repository_root
        / "testbeds"
        / testbed_id
        / "datasets"
        / dataset_version
        / "topology"
        / "address-plan.yaml"
    )
    if (repository_root / "pyproject.toml").is_file() and source_path.is_file():
        return source_path.read_text(encoding="utf-8")

    raise FileNotFoundError(
        f"No bundled address plan for testbed {testbed_id!r}, "
        f"dataset {dataset_version!r}"
    )
