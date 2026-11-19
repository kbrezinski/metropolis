"""Script artifacts in the default suite retain a frozen submission boundary."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from agent_bench import cli, runner
from agent_bench.contracts import Grade, Task
from agent_bench.providers import CannedProvider


def _script_task():
    return Task(
        "script-artifact",
        "offensive-scenario",
        "Write solution.py",
        starter_files={"starter/force_session.py": "# online session\n"},
        parameters={
            "artifact_path": "solution.py",
            "source_path": "starter/force_session.py",
            "baseline_only": True,
        },
    )


def test_script_submission_is_hashed_and_regrading_rejects_changes(tmp_path, monkeypatch):
    task = _script_task()
    source = "# online session\nprint('ready')\n"
    cassette = tmp_path / "cassette.json"
    cassette.write_text(
        json.dumps(
            {
                "tasks": {
                    task.id: [
                        {
                            "tool_calls": [
                                {
                                    "name": "write_file",
                                    "arguments": {"path": "solution.py", "content": source},
                                }
                            ]
                        },
                        {
                            "tool_calls": [
                                {
                                    "name": "finish",
                                    "arguments": {"reason": "done", "paths": ["solution.py"]},
                                }
                            ]
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("agent_bench.grading.grade", lambda *_: Grade(True))
    directory = tmp_path / "attempt"
    result = runner.run_attempt(
        task,
        {"alias": "canned"},
        CannedProvider({"cassette": str(cassette)}),
        directory,
        1,
        "pilot-suite",
    )
    assert result["artifact_path"] == "solution.py"
    assert result["source_sha256"] == hashlib.sha256(b"# online session\n").hexdigest()
    assert result["submission_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert result["grade"]["passed"]

    monkeypatch.setattr(runner, "catalog_for_suite", lambda _: [task])
    monkeypatch.setattr(runner, "task_for_suite", lambda *_: task)
    assert runner.grade_attempt(directory)["grade"]["passed"]
    artifact = directory / "submission" / "solution.py"
    artifact.write_text(source + "# changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen code submission changed"):
        runner.grade_attempt(directory)


def test_script_task_uses_baseline_only_in_default_run(tmp_path, monkeypatch):
    script = _script_task()
    command = Task("command-artifact", "benign-command", "Write solution.sh")
    tasks = {task.id: task for task in (script, command)}
    calls = []
    monkeypatch.setattr(cli, "catalog_for_suite", lambda _: list(tasks.values()))
    monkeypatch.setattr(cli, "task_for_suite", lambda task_id, _: tasks[task_id])
    monkeypatch.setattr(cli, "load_models", lambda _: {"demo": {"alias": "demo"}})
    monkeypatch.setattr(cli, "make_provider", lambda _: SimpleNamespace(secret=""))
    monkeypatch.setattr(cli, "write_run_audit", lambda *_: {"groups": []})

    def record(task, model, provider, output, repeat, catalog_hash, *args, **kwargs):
        calls.append((task.id, kwargs["prompt_condition"]))
        return {
            "evaluation_mode": "command-generation",
            "rubric_version": "3.0",
            "grade": {"passed": True, "outcome": "Full"},
            "stop_reason": "complete",
        }

    monkeypatch.setattr(cli, "run_attempt", record)
    assert (
        cli.main(
            [
                "run",
                "--config",
                str(tmp_path / "unused.toml"),
                "--task",
                script.id,
                "--task",
                command.id,
                "--prompt-condition",
                "all",
                "--output",
                str(tmp_path / "run"),
            ]
        )
        == 0
    )
    assert [condition for task_id, condition in calls if task_id == script.id] == ["baseline"]
    assert len([condition for task_id, condition in calls if task_id == command.id]) == 16


def test_script_task_rejects_a_nonbaseline_prompt_before_start(tmp_path):
    with pytest.raises(ValueError, match="baseline prompt only"):
        runner.run_attempt(
            _script_task(),
            {},
            None,
            tmp_path / "attempt",
            1,
            "pilot-suite",
            prompt_condition="spanish",
        )
    assert not (tmp_path / "attempt").exists()
