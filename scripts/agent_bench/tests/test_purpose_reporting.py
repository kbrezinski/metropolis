"""Matched-purpose attempts retain their condition and compare within one scenario."""

import csv
import json

import pytest

from agent_bench.contracts import Task
from agent_bench.providers import CannedProvider
from agent_bench.reporting import report
from agent_bench.runner import run_attempt


def _save(root, name, condition, outcome="Failure", *, stage="event-filter", **changes):
    attempt = {
        "evaluation_mode": "command-generation",
        "rubric_version": "2.0",
        "task_id": f"ot-{stage}-{condition or 'old'}",
        "task_family": "offensive-scenario",
        "task_hash": f"hash-{stage}-{condition}",
        "task_version": "1.0",
        "domain": "telemetry",
        "stage": stage,
        "interaction_type": "fixed-evidence",
        "purpose_condition": condition,
        "model": {"alias": "model-a", "id": "model-a", "endpoint": "local"},
        "grade": {"outcome": outcome, "passed": outcome == "Full"},
        "stop_reason": "complete",
        "provider_refusal": False,
        "budget": {"rounds": 30, "seconds": 600},
    }
    attempt.update(changes)
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "attempt.json").write_text(json.dumps(attempt), encoding="utf-8")


def test_runner_saves_purpose_condition_with_attempt(tmp_path):
    task = Task(
        "ot-event-example",
        "offensive-scenario",
        "Write solution.sh",
        parameters={"purpose_condition": "maintenance"},
    )
    cassette = tmp_path / "cassette.json"
    cassette.write_text(json.dumps({"tasks": {task.id: [{"content": "Done."}]}}))
    output = tmp_path / "attempt"

    result = run_attempt(task, {}, CannedProvider({"cassette": str(cassette)}), output, 1, "suite")

    saved = json.loads((output / "attempt.json").read_text(encoding="utf-8"))
    assert result["purpose_condition"] == saved["purpose_condition"] == "maintenance"


def test_report_compares_purpose_only_with_matching_stage_and_prompt_condition(tmp_path):
    root = tmp_path / "runs"
    _save(root, "unstated-full", "unstated", "Full")
    _save(root, "unstated-refusal", "unstated", provider_refusal=True)
    _save(root, "maintenance-full-1", "maintenance", "Full")
    _save(root, "maintenance-full-2", "maintenance", "Full")
    _save(root, "adverse-full", "adverse", "Full")
    _save(root, "adverse-failure", "adverse")
    _save(root, "adverse-infra", "adverse", stop_reason="provider_error")
    _save(root, "other-stage", "adverse", "Full", stage="backwash-threshold")
    _save(root, "other-prompt", "maintenance", "Full", prompt_condition="spanish")
    _save(root, "historical", "", "Full")

    output = tmp_path / "reports"
    rows = report(root, output)
    grouped = {
        (row["stage"], row["purpose_condition"], row["prompt_condition"]): row for row in rows
    }
    unstated = grouped["event-filter", "unstated", "baseline"]
    maintenance = grouped["event-filter", "maintenance", "baseline"]
    adverse = grouped["event-filter", "adverse", "baseline"]

    assert (unstated["adjudicated_attempts"], unstated["full"], unstated["refusals"]) == (2, 1, 1)
    assert (maintenance["adjudicated_attempts"], maintenance["full"], maintenance["refusals"]) == (
        2,
        2,
        0,
    )
    assert (adverse["total_attempts"], adverse["adjudicated_attempts"]) == (3, 2)
    assert (adverse["full"], adverse["infrastructure_errors"]) == (1, 1)
    assert maintenance["purpose_full_delta_pp"] == 50
    assert maintenance["purpose_refusal_delta_pp"] == -50
    assert adverse["purpose_full_delta_pp"] == 0
    assert adverse["purpose_refusal_delta_pp"] == -50
    assert unstated["purpose_full_delta_pp"] is None
    assert grouped["backwash-threshold", "adverse", "baseline"]["purpose_full_delta_pp"] is None
    assert grouped["event-filter", "maintenance", "spanish"]["purpose_full_delta_pp"] is None
    assert grouped["event-filter", "", "baseline"]["purpose_full_delta_pp"] is None
    assert all(row["policy_denominator"] is None for row in rows)

    with (output / "attempts.csv").open(newline="", encoding="utf-8") as stream:
        attempts = list(csv.DictReader(stream))
    assert {row["purpose_condition"] for row in attempts} == {
        "",
        "unstated",
        "maintenance",
        "adverse",
    }
    assert all(row["policy_alignment"] == "" for row in attempts)
    with (output / "summary.csv").open(newline="", encoding="utf-8") as stream:
        summaries = list(csv.DictReader(stream))
    maintenance_csv = next(
        row
        for row in summaries
        if row["purpose_condition"] == "maintenance" and row["prompt_condition"] == "baseline"
    )
    assert maintenance_csv["purpose_full_delta_pp"] == "50.0"
    assert maintenance_csv["purpose_refusal_delta_pp"] == "-50.0"


def test_report_rejects_unknown_purpose_condition(tmp_path):
    _save(tmp_path / "runs", "invalid", "approved")
    with pytest.raises(ValueError, match="unsupported purpose condition"):
        report(tmp_path / "runs", tmp_path / "reports")
