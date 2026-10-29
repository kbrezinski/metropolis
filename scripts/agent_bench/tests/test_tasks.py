"""Command contracts are stable public inputs, independent of target infrastructure."""

from dataclasses import replace

import pytest

from agent_bench.tasks import get_task, tasks


def test_benign_catalog_is_versioned_and_parameter_changes_affect_provenance():
    catalog = [task for task in tasks() if task.family == "benign-command"]
    assert len(catalog) == 12
    for task in catalog:
        assert get_task(task.id) == task
        assert task.version == "1.0"
        assert not task.starter_files
        assert "Do not execute it or set up a service" in task.prompt
        assert len(task.digest) == 64
        assert (
            replace(task, parameters={**task.parameters, "host": "203.0.113.99"}).digest
            != task.digest
        )


def test_offensive_cases_are_separate_contracts_with_a_provided_inert_payload():
    catalog = tasks()
    assert len(catalog) == len({task.id for task in catalog}) == 17
    offensive = [task for task in catalog if task.family == "offensive-command"]
    assert len(offensive) == 5
    for task in offensive:
        assert task.version == "1.0"
        assert get_task(task.id) == task
        assert task.parameters["host"] == "203.0.113.10"
        assert "do not execute it or set up a service" in task.prompt
        assert replace(task, parameters={**task.parameters, "ttl": 63}).digest != task.digest
    vse = get_task("hping3-flood-udp-vse")
    assert vse.starter_files["table_atk_vse"].encode() == b"TSource Engine Query\0"
    assert len(vse.starter_files["table_atk_vse"].encode()) == vse.parameters["data"]


def test_obsolete_service_tasks_cannot_be_selected_under_new_contracts():
    with pytest.raises(ValueError, match="Unknown task"):
        get_task("inert-beacon")
