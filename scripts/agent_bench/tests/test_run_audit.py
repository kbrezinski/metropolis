"""Readable inventories agree with existing static scores and keep run coverage visible."""

import csv
import json

import pytest

from agent_bench.run_audit import render_run_overview, write_run_audit


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def attempt(task, *, outcome="Full", stop="complete", rubric="3.0"):
    return {
        "task_id": task,
        "task_family": "benign-command",
        "task_version": "1.0",
        "evaluation_mode": "command-generation",
        "rubric_version": rubric,
        "model": {"alias": "hosted", "id": "model/v1"},
        "prompt_condition": "baseline",
        "repeat": 1,
        "stop_reason": stop,
        "provider_refusal": outcome == "Refusal",
        "grade": {
            "outcome": outcome,
            "passed": outcome == "Full",
            "infrastructure_error": "offline" if stop == "provider_error" else None,
        },
    }


def rows(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_api_inventory_includes_unstarted_and_excluded_attempts(tmp_path):
    root = tmp_path / "run"
    matrix = [
        {
            "model_alias": "hosted",
            "task_id": task,
            "prompt_condition": "baseline",
            "repeat": 1,
            "attempt_path": f"hosted/{task}/repeat-001",
        }
        for task in ("z-full", "a-refusal", "other", "offline", "not-started")
    ]
    save(
        root / "run.json",
        {
            "run_mode": "api",
            "status": "failed",
            "attempts": matrix,
            "model_errors": {"hosted": "Invalid API key"},
        },
    )
    for task, outcome, stop in (
        ("z-full", "Full", "complete"),
        ("a-refusal", "Refusal", "provider_refusal"),
        ("other", "Limited", "complete"),
        ("offline", "Failure", "provider_error"),
    ):
        save(
            root / f"hosted/{task}/repeat-001/attempt.json",
            attempt(task, outcome=outcome, stop=stop),
        )
    full_path = root / "hosted/z-full/repeat-001/attempt.json"
    frozen = full_path.read_bytes()
    save(full_path.parent / "decision-summary.json", {"status": "recorded"})
    result = write_run_audit(root)
    assert result["requested_attempts"] == 5
    assert result["persisted_attempts"] == 4
    assert result["completed_attempts"] == result["adjudicated_attempts"] == 3
    assert result["excluded_attempts"] == result["unstarted_attempts"] == 1
    assert result["decision_summaries"] == {"recorded": 1, "missing": 3}
    group = result["outcomes_by_rubric"][0]
    assert group["model_id"] == "model/v1"
    assert group["rubric_version"] == "3.0"
    assert group["outcomes"] == {"Full": 1, "Refusal": 1, "Other": 1, "Infrastructure Error": 1}
    assert group["adjudicated_attempts"] == 3
    csv_outcomes = {row["task_id"]: row["outcome"] for row in rows(root / "reports/attempts.csv")}
    assert csv_outcomes == {row["task_id"]: row["outcome"] for row in result["attempts"]}
    assert [row["task_id"] for row in result["attempts"]] == [
        "z-full",
        "a-refusal",
        "other",
        "offline",
    ]
    score_text = (root / "reports/SCORES.md").read_text(encoding="utf-8")
    assert "Requested: 5; persisted: 4; completed: 3; adjudicated/valid: 3" in score_text
    assert "Invalid API key" in score_text
    assert full_path.read_bytes() == frozen


def test_legacy_report_uses_discovered_count_and_keeps_rubrics_separate(tmp_path):
    root = tmp_path / "legacy"
    save(root / "older/attempt.json", attempt("older", outcome="Limited", rubric="2.0"))
    save(root / "newer/attempt.json", attempt("newer", outcome="Limited"))
    output = tmp_path / "regenerated"
    result = write_run_audit(root, output=output)
    assert result["requested_count_source"] == "persisted"
    assert result["requested_attempts"] == result["adjudicated_attempts"] == 2
    assert [(row["rubric_version"], row["outcomes"]) for row in result["outcomes_by_rubric"]] == [
        ("2.0", {"Limited": 1}),
        ("3.0", {"Other": 1}),
    ]
    assert (output / "SCORES.md").is_file()
    assert (output / "results.json").is_file()
    assert not (root / "reports").exists()


@pytest.mark.parametrize("suite", ["default", "staged-ot"])
def test_interrupted_run_before_first_dispatch_reports_zero_persisted(tmp_path, suite):
    save(
        tmp_path / "run.json",
        {
            "status": "in-progress",
            "suite_id": suite,
            "attempts": [{"model_alias": "hosted", "task_id": "pending", "repeat": 1}],
        },
    )
    result = write_run_audit(tmp_path, status="interrupted")
    assert result["status"] == "interrupted"
    assert result["persisted_attempts"] == result["adjudicated_attempts"] == 0
    assert result["unstarted_attempts"] == 1
    assert result["groups"] == result["attempts"] == []


def test_staged_audit_preserves_checkpoint_strip_without_episode_grade(tmp_path):
    data = {
        "task_id": "episode-a",
        "task_hash": "episode-hash",
        "task_version": "1.0",
        "scenario_id": "example",
        "arm": "a",
        "suite_id": "staged-ot",
        "rubric_version": "3.0",
        "model": {"alias": "hosted"},
        "repeat": 1,
        "stop_reason": "complete",
        "grade": {},
        "checkpoints": [
            {
                "index": index,
                "reached": True,
                "submitted": True,
                "outcome": outcome,
                "grade": {"passed": outcome == "Full"},
            }
            for index, outcome in enumerate(("Full", "Other", "Full"), 1)
        ],
    }
    save(tmp_path / "episode/attempt.json", data)
    result = write_run_audit(tmp_path)
    row = result["attempts"][0]
    assert "outcome" not in row
    assert row["outcome_strip"] == "Full → Other → Full"
    assert row["full_prefix_depth"] == "1"
    assert result["outcomes_by_rubric"] == []
    assert result["adjudicated_attempts"] == 1
    csv_checkpoints = rows(tmp_path / "reports/staged_checkpoints.csv")
    assert [item["outcome"] for item in csv_checkpoints] == ["Full", "Other", "Full"]
    text = (tmp_path / "reports/SCORES.md").read_text(encoding="utf-8")
    assert "Full → Other → Full" in text and "1/3" in text


def test_shared_overview_does_not_depend_on_native_manifest_shape():
    lines = render_run_overview(
        {
            "status": "finished",
            "requested_attempts": 2,
            "persisted_attempts": 2,
            "adjudicated_attempts": 1,
            "excluded_attempts": 1,
            "unstarted_attempts": 0,
            "reason": "one endpoint unavailable",
            "mailbox_integrity_anomalies": [],
        }
    )
    assert lines == [
        "Run status: finished",
        "Requested: 2; persisted: 2; adjudicated/valid: 1; infrastructure excluded: 1; unstarted: 0.",
        "Reason: one endpoint unavailable",
    ]


def test_blind_manifest_reconstructs_saved_conditions_and_preserves_diagnostics(tmp_path):
    save(
        tmp_path / "run.json",
        {
            "run_mode": "live-codex-subagent-mailbox",
            "tasks": ["first", "second"],
            "repeats": 2,
            "prompt_conditions": ["baseline", "spanish"],
            "task_prompt_conditions": {"first": ["baseline", "spanish"], "second": ["baseline"]},
        },
    )
    diagnostics = {
        "mailbox_integrity_anomalies": [{"reason": "late change"}],
        "unanswered_requests": [{"task_id": "first", "request_id": "pending"}],
    }
    save(tmp_path / "reports/results.json", diagnostics)
    for task in ("first", "second"):
        data = attempt(task)
        data["model"]["alias"] = "codex-subagent"
        save(tmp_path / f"codex-subagent/{task}/repeat-001/attempt.json", data)
    result = write_run_audit(tmp_path)
    assert result["requested_count_source"] == "legacy-manifest"
    assert result["requested_attempts"] == 6
    assert result["persisted_attempts"] == 2
    assert result["unstarted_attempts"] == 4
    for key, value in diagnostics.items():
        assert result[key] == value
    assert "Saved mailbox diagnostics retained: 1 integrity anomalies; 1 unanswered requests." in (
        tmp_path / "reports/SCORES.md"
    ).read_text(encoding="utf-8")


def test_same_model_at_distinct_hosts_keeps_separate_outcome_totals(tmp_path):
    for host, outcome in (("host-a", "Full"), ("host-b", "Refusal")):
        data = attempt("task", outcome=outcome)
        data["model"].update({"provider": "openrouter", "upstream_provider": host})
        save(tmp_path / f"{host}/attempt.json", data)
    result = write_run_audit(tmp_path)
    assert [
        (group["upstream_provider"], group["outcomes"], group["adjudicated_attempts"])
        for group in result["outcomes_by_rubric"]
    ] == [("host-a", {"Full": 1}, 1), ("host-b", {"Refusal": 1}, 1)]
    text = (tmp_path / "reports/SCORES.md").read_text(encoding="utf-8")
    assert "host-a" in text and "host-b" in text
