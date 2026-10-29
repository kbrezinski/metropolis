"""Command contracts are stable public inputs, independent of target infrastructure."""

from dataclasses import replace

from agent_bench.tasks import get_task, tasks


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
