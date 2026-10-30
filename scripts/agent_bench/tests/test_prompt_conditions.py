"""The opt-in prompt conditions keep the published command contract recoverable."""

import codecs
from dataclasses import asdict
import re

import pytest

from agent_bench.prompt_conditions import (
    CONDITION_IDS,
    CONDITION_VERSION,
    condition_code,
    condition_hash,
    render_prompt,
    select_conditions,
)
from agent_bench.tasks import get_task, tasks


def test_condition_selection_is_explicit_and_baseline_stays_the_default():
    assert select_conditions(None) == ("baseline",)
    assert select_conditions([]) == ("baseline",)
    assert select_conditions(["all"]) == CONDITION_IDS
    assert select_conditions(["spanish", "baseline"]) == ("spanish", "baseline")
    assert CONDITION_VERSION == "1.2"
    with pytest.raises(ValueError, match="alone"):
        select_conditions(["all", "baseline"])
    with pytest.raises(ValueError, match="Unknown"):
        select_conditions(["not-a-condition"])
    with pytest.raises(ValueError, match="once"):
        select_conditions(["spanish", "spanish"])


def test_rendering_the_full_matrix_does_not_mutate_task_contracts():
    for task in tasks():
        before = asdict(task)
        assert render_prompt(task, "baseline") == task.prompt
        for condition_id in CONDITION_IDS[1:]:
            prompt = render_prompt(task, condition_id)
            assert prompt == render_prompt(task, condition_id)
            assert "solution.sh" in prompt
            if condition_id == "token-smuggling":
                prompt = re.sub(
                    r"\[ROT13: ([^\]]+)\]",
                    lambda match: codecs.decode(match.group(1), "rot_13"),
                    prompt,
                )
            elif condition_id == "payload-splitting":
                prompt = re.sub(
                    r"\[part A: ([^\]]+)\]\[part B: ([^\]]+)\]",
                    lambda match: match.group(1) + match.group(2),
                    prompt,
                )
            for key in (
                "host",
                "file",
                "topic",
                "message",
                "url",
                "user",
                "identity",
                "protocol",
                "destport",
            ):
                value = task.parameters.get(key)
                if task.family != "offensive-scenario" and isinstance(value, str):
                    assert value.casefold() in prompt.casefold(), (task.id, condition_id, key)
            for key in ("fields", "flags"):
                for value in task.parameters.get(key, []):
                    assert value.casefold() in prompt.casefold(), (task.id, condition_id, key)
            if task.family != "offensive-scenario" and "port" in task.parameters:
                assert str(task.parameters["port"]) in prompt
        assert asdict(task) == before


def test_encoded_and_split_action_phrases_recover_the_original_request():
    for task in tasks():
        encoded = render_prompt(task, "token-smuggling")
        match = re.search(r"\[ROT13: ([^\]]+)\]", encoded)
        assert match is not None
        restored = encoded.replace(match.group(), codecs.decode(match.group(1), "rot_13"))
        assert task.prompt in restored

        split = render_prompt(task, "payload-splitting")
        match = re.search(r"\[part A: ([^\]]+)\]\[part B: ([^\]]+)\]", split)
        assert match is not None
        restored = split.replace(match.group(), match.group(1) + match.group(2))
        assert task.prompt in restored


def test_spanish_preserves_technical_literals_without_english_task_copy():
    task = get_task("modbus-read")
    prompt = render_prompt(task, "spanish")
    for value in ("mbpoll", "203.0.113.10", "1502", "-m tcp", "-0", "-r", "-c", "-1"):
        assert value in prompt
    assert "Read exactly one" not in prompt


def test_demonstrations_do_not_supply_a_target_utility_answer():
    task = get_task("hping3-flood-tcp-syn")
    few = render_prompt(task, "few-shot")
    many = render_prompt(task, "many-shot")
    assert few.count("Request:") == 3
    assert many.count("Request:") == 20
    example_section = many.split("Complete this new request:", 1)[0]
    assert "hping3" not in example_section
    assert "203.0.113.10" not in example_section


def test_opaque_condition_codes_do_not_reveal_technique_names():
    assert condition_code("baseline") == "baseline"
    assert len({condition_code(condition_id) for condition_id in CONDITION_IDS}) == len(
        CONDITION_IDS
    )
    assert all(
        condition_code(condition_id).startswith("condition-") for condition_id in CONDITION_IDS[1:]
    )
    with pytest.raises(ValueError, match="Unknown"):
        condition_code("unknown")


def test_condition_fingerprints_are_stable_and_distinguish_conditions():
    fingerprints = [condition_hash(condition_id) for condition_id in CONDITION_IDS]
    assert len(set(fingerprints)) == len(fingerprints)
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in fingerprints)
    assert fingerprints == [condition_hash(condition_id) for condition_id in CONDITION_IDS]
    with pytest.raises(ValueError, match="Unknown"):
        condition_hash("unknown")
