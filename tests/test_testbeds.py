import pytest

from metropolis.testbeds import load_address_plan


def test_load_address_plan_returns_network_records():
    plan = load_address_plan()

    assert plan["testbed_id"] == "metropolis"
    assert plan["schema_version"] == "1.0"
    assert plan["networks"]
    assert {network["id"] for network in plan["networks"]} >= {
        "plant-intake",
        "mqtt",
        "wan-underlay",
    }


def test_load_address_plan_rejects_path_traversal():
    with pytest.raises(ValueError, match="Invalid testbed_id"):
        load_address_plan("../other-testbed")


def test_load_address_plan_reports_missing_testbed():
    with pytest.raises(FileNotFoundError, match="No bundled address plan"):
        load_address_plan("future-testbed", "dataset_v1")
