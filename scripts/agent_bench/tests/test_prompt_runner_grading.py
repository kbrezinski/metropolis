"""Prompt conditions preserve command scoring and frozen prompt provenance."""

import base64
import hashlib
import json

import pytest

from agent_bench.grading import grade
from agent_bench.prompt_conditions import condition_hash, render_prompt
from agent_bench.providers import CannedProvider
from agent_bench.runner import grade_attempt, run_attempt
from agent_bench.tasks import get_task


def _encoded(command):
    return base64.b64encode(command.encode("utf-8")).decode("ascii") + "\n"


@pytest.mark.parametrize(
    ("source", "outcome", "format_followed"),
    [
        (_encoded("ping -c4 203.0.113.10\n"), "Full", True),
        ("ping -c4 203.0.113.10\n", "Full", False),
        (_encoded("ping -c2 203.0.113.10\n"), "Limited", True),
        (_encoded("ping -c4 203.0.113.11\n"), "Failure", True),
        ("%%% not Base64 %%%\n", "Failure", False),
    ],
)
def test_output_format_records_encoding_separately_from_command_outcome(
    tmp_path, source, outcome, format_followed
):
    (tmp_path / "solution.sh").write_text(source, encoding="utf-8", newline="\n")
    result = grade(get_task("ping"), tmp_path, prompt_condition="output-format")
    assert result.outcome == outcome
    assert result.format_followed is format_followed


def test_encoded_shell_chain_is_rejected_without_execution(tmp_path):
    marker = tmp_path / "must-not-exist"
    source = _encoded(f"ping -c4 203.0.113.10; touch {marker}\n")
    (tmp_path / "solution.sh").write_text(source, encoding="utf-8", newline="\n")
    result = grade(get_task("ping"), tmp_path, prompt_condition="output-format")
    assert result.outcome == "Failure"
    assert result.format_followed is True
    assert not marker.exists()


def _run_canned(tmp_path, condition, command):
    task = get_task("ping")
    cassette = tmp_path / "cassette.json"
    cassette.write_text(
        json.dumps(
            {
                "tasks": {
                    "ping": [
                        {
                            "tool_calls": [
                                {
                                    "name": "write_file",
                                    "arguments": {"path": "solution.sh", "content": command},
                                }
                            ]
                        },
                        {
                            "tool_calls": [
                                {
                                    "name": "finish",
                                    "arguments": {"reason": "done", "paths": ["solution.sh"]},
                                }
                            ]
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "attempt"
    result = run_attempt(
        task,
        {"alias": "canned"},
        CannedProvider({"cassette": str(cassette)}),
        output,
        1,
        "suite",
        prompt_condition=condition,
    )
    return task, output, result


def test_variant_prompt_is_saved_and_regraded_against_the_base_task(tmp_path):
    task, output, attempt = _run_canned(
        tmp_path, "output-format", _encoded("ping -c4 203.0.113.10\n")
    )
    expected = render_prompt(task, "output-format")
    assert (output / "effective-prompt.txt").read_text(encoding="utf-8") == expected
    assert (
        attempt["effective_prompt_sha256"] == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    )
    assert attempt["prompt_condition_sha256"] == condition_hash("output-format")
    assert attempt["task_hash"] == attempt["base_task_hash"] == task.digest
    assert attempt["grade"]["outcome"] == "Full"
    assert attempt["grade"]["format_followed"] is True
    trace = [json.loads(line) for line in (output / "trace.jsonl").read_text().splitlines()]
    assert trace[0]["messages"][1]["content"] == expected
    assert grade_attempt(output)["grade"]["format_followed"] is True


def test_baseline_prompt_is_unchanged_and_saved_prompt_cannot_be_replaced(tmp_path):
    task, output, attempt = _run_canned(tmp_path, "baseline", "ping -c4 203.0.113.10\n")
    assert (output / "effective-prompt.txt").read_text(encoding="utf-8") == task.prompt
    assert attempt["grade"]["format_followed"] is None
    (output / "effective-prompt.txt").write_text("changed task", encoding="utf-8")
    with pytest.raises(ValueError, match="Saved effective prompt"):
        grade_attempt(output)


def test_regrade_accepts_previous_baseline_condition_version(tmp_path):
    task, output, _ = _run_canned(tmp_path, "baseline", "ping -c4 203.0.113.10\n")
    path = output / "attempt.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["prompt_condition_version"] = "1.0"
    record["prompt_condition_sha256"] = "0" * 64
    path.write_text(json.dumps(record), encoding="utf-8")
    assert (output / "effective-prompt.txt").read_text(encoding="utf-8") == task.prompt
    assert grade_attempt(output)["grade"]["outcome"] == "Full"


def test_regrade_rejects_condition_version_drift(tmp_path):
    _, output, _ = _run_canned(tmp_path, "output-format", _encoded("ping -c4 203.0.113.10\n"))
    path = output / "attempt.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["prompt_condition_version"] = "older-version"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="Prompt condition version changed"):
        grade_attempt(output)


def test_regrade_rejects_condition_definition_drift(tmp_path):
    _, output, _ = _run_canned(tmp_path, "output-format", _encoded("ping -c4 203.0.113.10\n"))
    path = output / "attempt.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["prompt_condition_sha256"] = "0" * 64
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="Prompt condition definition changed"):
        grade_attempt(output)


def test_regrade_accepts_precondition_baseline_attempt(tmp_path):
    _, output, _ = _run_canned(tmp_path, "baseline", "ping -c4 203.0.113.10\n")
    path = output / "attempt.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    for key in (
        "base_task_id",
        "base_task_hash",
        "prompt_condition",
        "prompt_condition_version",
        "prompt_condition_sha256",
        "effective_prompt_sha256",
    ):
        del record[key]
    path.write_text(json.dumps(record), encoding="utf-8")
    (output / "effective-prompt.txt").unlink()
    assert grade_attempt(output)["grade"]["outcome"] == "Full"
