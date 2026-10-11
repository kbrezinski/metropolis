"""Load configuration data shipped with the Metropolis package."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
import re
from typing import Any

import yaml

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]*$")
_ADDRESS_PLAN_RESOURCE = Path("data") / "testbeds"


def _validate_identifiers(testbed_id: str, dataset_version: str) -> None:
    for label, value in (
        ("testbed_id", testbed_id),
        ("dataset_version", dataset_version),
    ):
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError(f"Invalid {label}: {value!r}")


def read_dataset_file(
    relative_path: str,
    testbed_id: str = "metropolis",
    dataset_version: str = "water_treatment_v1",
) -> dict[str, Any]:
    """Load one YAML document from a bundled testbed dataset.

    ``relative_path`` is relative to the dataset directory, for example
    ``topology/links.yaml``. The packaged resource is preferred, so an installed
    distribution needs no repository checkout; a source checkout falls back to
    the repository's own copy.
    """
    _validate_identifiers(testbed_id, dataset_version)

    resource_path = (
        _ADDRESS_PLAN_RESOURCE / testbed_id / dataset_version / relative_path
    )
    package_resource = files("metropolis").joinpath(*resource_path.parts)

    try:
        content = package_resource.read_text(encoding="utf-8")
    except FileNotFoundError:
        content = _read_source_checkout(
            Path(relative_path), testbed_id, dataset_version
        )

    document = yaml.safe_load(content)
    if not isinstance(document, dict):
        raise ValueError(f"{relative_path} for {testbed_id!r} must be a YAML mapping")
    if document.get("testbed_id") != testbed_id:
        raise ValueError(
            f"Dataset identity mismatch in {relative_path}: requested "
            f"{testbed_id!r}, found {document.get('testbed_id')!r}"
        )
    return document


def load_address_plan(
    testbed_id: str = "metropolis",
    dataset_version: str = "water_treatment_v1",
) -> dict[str, Any]:
    """Load a bundled testbed address plan as a Python mapping."""
    plan = read_dataset_file("topology/address-plan.yaml", testbed_id, dataset_version)
    if not isinstance(plan.get("networks"), list):
        raise ValueError("Address plan must contain a 'networks' list")
    return plan


def _read_source_checkout(
    relative_path: Path, testbed_id: str, dataset_version: str
) -> str:
    """Read a canonical YAML from a source checkout as a dev-time fallback."""
    repository_root = Path(__file__).resolve().parents[2]
    source_path = (
        repository_root
        / "testbeds"
        / testbed_id
        / "datasets"
        / dataset_version
        / relative_path
    )
    if (repository_root / "pyproject.toml").is_file() and source_path.is_file():
        return source_path.read_text(encoding="utf-8")

    raise FileNotFoundError(
        f"No bundled {relative_path.as_posix()} for testbed {testbed_id!r}, "
        f"dataset {dataset_version!r}"
    )
