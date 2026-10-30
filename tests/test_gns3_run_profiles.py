"""Validate portable run profiles without a GNS3 server or Docker engine."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

GNS3 = Path(__file__).resolve().parents[1] / "scripts" / "gns3"
sys.path.insert(0, str(GNS3))

from run_profiles import load_profile  # noqa: E402


@pytest.fixture
def profile_lab(tmp_path):
    inventory = {"devices": [{"name": "custom-controller"}]}
    infrastructure = {
        "routers": [{"name": "custom-router"}],
        "switches": [{"name": "custom-switch"}],
        "segments": [{"name": "custom-segment"}],
    }
    links = {"links": [{"id": "custom-field-link"}]}
    path = tmp_path / "profile.yaml"

    def load(document):
        path.write_text(yaml.safe_dump(document), encoding="utf-8")
        return load_profile(path, inventory, infrastructure, links)

    return path, inventory, infrastructure, links, load


def device_profile(**settings):
    return {
        "schema_version": "1.0",
        "name": "field-device",
        "devices": [{"node": "custom-controller", "cpus": 0.5, **settings}],
    }


def link_profile(**settings):
    return {
        "schema_version": "1.0",
        "name": "field-link",
        "links": [{"link": "custom-field-link", "delay_ms": 20, **settings}],
    }


def test_valid_profile_is_dataset_resolved_and_normalized(profile_lab):
    *_, load = profile_lab
    document = device_profile(memory_bytes=128 << 20)
    document["links"] = link_profile(bandwidth_kbps=1000, jitter_ms=5, loss_percent=1)[
        "links"
    ]
    result = load(document)
    assert result == document


def test_missing_entry_lists_are_empty_and_switches_can_be_selected(profile_lab):
    *_, load = profile_lab
    document = device_profile()
    document["devices"][0]["node"] = "custom-switch"
    assert load(document)["links"] == []
    document["devices"][0]["node"] = "custom-segment"
    assert load(document)["devices"][0]["node"] == "custom-segment"
    assert load(link_profile())["devices"] == []


@pytest.mark.parametrize(
    "document",
    [
        None,
        {"schema_version": "1.0", "name": "empty", "devices": [], "links": []},
        device_profile(cpus=True),
        device_profile(cpus=float("nan")),
        link_profile(bandwidth_kbps=0),
        link_profile(loss_percent=101),
    ],
)
def test_invalid_profiles_fail_before_runtime_access(profile_lab, document):
    *_, load = profile_lab
    with pytest.raises(ValueError):
        load(document)


@pytest.mark.parametrize(
    "entry_key,target_key", [("devices", "node"), ("links", "link")]
)
def test_duplicate_and_unknown_targets_are_rejected(profile_lab, entry_key, target_key):
    *_, load = profile_lab
    document = device_profile() if entry_key == "devices" else link_profile()
    document[entry_key].append(document[entry_key][0].copy())
    with pytest.raises(ValueError, match="duplicate profile"):
        load(document)
    document[entry_key] = [document[entry_key][0]]
    document[entry_key][0][target_key] = "another-dataset-target"
    with pytest.raises(ValueError, match="unknown declared"):
        load(document)


def test_router_is_not_a_declared_docker_profile_target(profile_lab):
    *_, load = profile_lab
    document = device_profile()
    document["devices"][0]["node"] = "custom-router"
    with pytest.raises(ValueError, match="unknown declared profile node"):
        load(document)


def test_link_profiles_reject_logical_tunnels_before_runtime_access(profile_lab):
    _, _, _, links, load = profile_lab
    links["links"].append({"id": "site-vpn", "kind": "overlay"})
    with pytest.raises(ValueError, match="logical overlay, not a physical GNS3 link"):
        load(link_profile(link="site-vpn"))


def test_ambiguous_dataset_names_are_not_silently_selected(profile_lab):
    _, inventory, _, links, load = profile_lab
    inventory["devices"].append({"name": "custom-controller"})
    with pytest.raises(ValueError, match="ambiguous declared profile node"):
        load(device_profile())
    links["links"].append({"id": "custom-field-link"})
    with pytest.raises(ValueError, match="ambiguous declared profile link"):
        load(link_profile())


@pytest.mark.parametrize(
    "source",
    [
        "name: first\nname: second\n",
        "? [a, b]\n: value\n",
        "schema_version: '1.0'\n---\nname: second-document\n",
        "name: [unclosed\n",
    ],
)
def test_yaml_errors_and_duplicate_keys_are_rejected(profile_lab, source):
    path, inventory, infrastructure, links, _ = profile_lab
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ValueError):
        load_profile(path, inventory, infrastructure, links)


def test_zero_link_impairments_and_minimum_memory_are_valid(profile_lab):
    *_, load = profile_lab
    assert load(link_profile(delay_ms=0, jitter_ms=0, loss_percent=0))["links"]
    assert load(device_profile(memory_bytes=6291456))["devices"]
