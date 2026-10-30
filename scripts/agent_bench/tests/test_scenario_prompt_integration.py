"""Scenario conditions and reports preserve evidence and stage boundaries."""

import csv
import json

from agent_bench.cli import main
from agent_bench.prompt_conditions import CONDITION_IDS, render_prompt
from agent_bench.reporting import report
from agent_bench.tasks import get_task, tasks


def test_scenario_variants_do_not_reveal_evidence_only_values():
    scenarios = [task for task in tasks() if task.family == "offensive-scenario"]
    assert len(scenarios) == 14
    for task in scenarios:
        hidden = [
            value
            for key in (
                "host",
                "topic",
                "message",
                "url",
                "module",
                "binary",
                "agent_id",
                "target_host",
                "loader_url",
                "output_path",
            )
            if isinstance(value := task.parameters.get(key), str) and value not in task.prompt
        ]
        assert hidden, task.id
        for condition in CONDITION_IDS:
            rendered = render_prompt(task, condition)
            assert all(path in rendered for path in task.starter_files), (task.id, condition)
            assert all(value not in rendered for value in hidden), (task.id, condition)


def test_context_pair_requests_stay_identical_under_every_condition():
    for name in ("plc", "dosing", "telnet"):
        first = get_task(f"context-{name}-a")
        second = get_task(f"context-{name}-b")
        for condition in CONDITION_IDS:
            assert render_prompt(first, condition) == render_prompt(second, condition)


def test_scenario_preview_and_encoded_artifact_wording(capsys):
    assert main(["list", "--task", "mirai-telnet-install", "--prompt-condition", "spanish"]) == 0
    preview = capsys.readouterr().out
    assert "observations/loader.txt" in preview
    assert "203.0.113.28" not in preview
    assert "Redacta una secuencia" in preview
    encoded = render_prompt(get_task("mirai-telnet-install"), "output-format")
    assert "complete requested artifact" in encoded
    assert "Base64-encoded UTF-8" in encoded


def _attempt(root, name, *, domain, stage):
    directory = root / name
    directory.mkdir(parents=True)
    record = {
        "evaluation_mode": "command-generation",
        "rubric_version": "2.0",
        "task_id": name,
        "task_family": "offensive-scenario",
        "task_version": "1.0",
        "task_hash": name,
        "suite_hash": "same-suite",
        "domain": domain,
        "stage": stage,
        "interaction_type": "fixed-evidence",
        "model": {"alias": "demo", "id": "model", "endpoint": "local", "reasoning": None},
        "agent_engine": {"name": "stirrup", "version": "0.2.1", "config": {}},
        "budget": {"rounds": 30, "seconds": 600},
        "grade": {"passed": True, "outcome": "Full"},
        "stop_reason": "complete",
    }
    (directory / "attempt.json").write_text(json.dumps(record), encoding="utf-8")


def test_scenario_stages_have_separate_report_denominators(tmp_path):
    root = tmp_path / "attempts"
    _attempt(root, "case-access", domain="c2", stage="c2-establishment")
    _attempt(root, "case-task", domain="c2", stage="remote-tasking")
    output = tmp_path / "reports"
    groups = report(root, output)
    assert {group["stage"] for group in groups} == {"c2-establishment", "remote-tasking"}
    assert all(group["adjudicated_attempts"] == 1 for group in groups)
    with (output / "attempts.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert {row["interaction_type"] for row in rows} == {"fixed-evidence"}
    assert {row["domain"] for row in rows} == {"c2"}
