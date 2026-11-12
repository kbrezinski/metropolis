"""Container baselines resolve dataset identities and reject invalid setup settings."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
import yaml

GNS3 = Path(__file__).resolve().parents[1] / "scripts/gns3"
sys.path.insert(0, str(GNS3))

from baseline_config import SWITCH_IMAGE, SWITCH_MODEL, load_baseline  # noqa: E402

MIB = 1024 * 1024


def document():
    return {
        "schema_version": "1.0",
        "defaults": {
            "cpus": 1,
            "memory_bytes": 512 * MIB,
            "memory_swap_bytes": 512 * MIB,
        },
    }


@pytest.fixture
def lab(tmp_path):
    inventory = {
        "devices": [
            {
                "name": "controller-a",
                "model": "models/controller",
                "image": "custom/controller:v2",
            },
            {
                "name": "controller-b",
                "model": "models/controller",
                "image": "custom/controller:v3",
            },
            {"name": "operator", "model": "models/hmi", "image": "custom/hmi:v2"},
        ]
    }
    infrastructure = {
        "switches": [{"name": "aggregation"}],
        "segments": [{"name": "access"}],
        "routers": [{"name": "router"}],
    }
    path = tmp_path / "baseline.yaml"

    def load(value):
        path.write_text(yaml.safe_dump(value), encoding="utf-8")
        return load_baseline(path, inventory, infrastructure)

    return path, inventory, infrastructure, load


def test_defaults_apply_to_all_declared_containers_and_switches(lab):
    path, _inventory, _infra, load = lab
    result = load(document())
    by_name = {target["name"]: target for target in result["targets"]}
    assert all(
        target["settings"] == document()["defaults"] for target in by_name.values()
    )
    assert by_name["aggregation"]["image"] == SWITCH_IMAGE
    assert by_name["access"]["model"] == SWITCH_MODEL
    assert result["unsupported"] == [
        {"name": "router", "reason": "router appliances are not Docker containers"}
    ]
    assert result["path"] == str(path.resolve())
    assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_partial_model_overrides_then_exact_image_override(lab):
    *_, load = lab
    value = document()
    value["overrides"] = {
        "models": {"models/controller": {"cpus": 0.5}},
        "images": {"custom/controller:v2": {"cpus": 0.25, "memory_bytes": 128 * MIB}},
    }
    result = {target["name"]: target["settings"] for target in load(value)["targets"]}
    assert result["controller-a"] == {
        "cpus": 0.25,
        "memory_bytes": 128 * MIB,
        "memory_swap_bytes": 512 * MIB,
    }
    assert result["controller-b"]["cpus"] == 0.5
    assert result["operator"] == value["defaults"]


def test_swap_relationship_is_checked_after_override_merging(lab):
    *_, load = lab
    value = document()
    value["overrides"] = {"images": {"custom/hmi:v2": {"memory_bytes": 1024 * MIB}}}
    with pytest.raises(ValueError, match="cover memory_bytes"):
        load(value)
    value["overrides"]["images"]["custom/hmi:v2"]["memory_swap_bytes"] = -1
    hmi = next(
        target for target in load(value)["targets"] if target["name"] == "operator"
    )
    assert hmi["settings"]["memory_swap_bytes"] == -1


def test_conflicting_node_names_are_not_silently_targeted(lab):
    _path, _inventory, infrastructure, load = lab
    infrastructure["routers"].append({"name": "controller-a"})
    with pytest.raises(ValueError, match="ambiguous baseline node"):
        load(document())
