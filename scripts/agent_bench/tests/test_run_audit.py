"""Readable inventories agree with existing static scores and keep run coverage visible."""

import csv
import json

import pytest

from agent_bench.run_audit import write_run_audit


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


@pytest.mark.parametrize("suite", ["default"])
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


def test_readable_groups_preserve_task_scores_and_raw_fields(tmp_path):
    selected = [
        "ping",
        "hping3-flood-udp-generic",
        "context-ph-acid-a",
        "context-ph-acid-b",
        "ot-case-05",
    ]
    save(
        tmp_path / "run.json",
        {
            "run_mode": "api",
            "suite_id": "default",
            "attempts": [
                {
                    "model_alias": "hosted",
                    "task_id": task,
                    "prompt_condition": "baseline",
                    "repeat": 1,
                }
                for task in selected
            ],
        },
    )
    for task, outcome in (
        ("ping", "Full"),
        ("hping3-flood-udp-generic", "Refusal"),
        ("context-ph-acid-a", "Other"),
        ("context-ph-acid-b", "Full"),
    ):
        save(tmp_path / task / "attempt.json", attempt(task, outcome=outcome))
    record = write_run_audit(tmp_path)
    text = (tmp_path / "reports/SCORES.md").read_text(encoding="utf-8")
    assert "### Standalone requests" in text
    assert "### Changed-observation comparisons" in text
    assert "| Model | Task | OT action | Comparison | Arm | Condition |" in text
    assert "| Standalone requests | Observation and discovery | 1 | 1 |" in text
    assert "| Standalone requests | Availability and device configuration | 2 | 1 |" in text
    assert "| Changed-observation comparisons | Process control and logic | 2 | 2 |" in text
    assert "Observation" in text
    assert "context-ph-acid-a | Process control and logic | Observation | a |" in text
    assert "context-ph-acid-b | Process control and logic | Observation | b |" in text
    assert record["unstarted_attempts"] == 1
    assert {row["task_id"]: row["outcome"] for row in record["attempts"]} == {
        "ping": "Full",
        "hping3-flood-udp-generic": "Refusal",
        "context-ph-acid-a": "Other",
        "context-ph-acid-b": "Full",
    }
    assert "ot_action" not in rows(tmp_path / "reports/attempts.csv")[0]


def test_plc_report_regeneration_uses_current_saved_grade(tmp_path):
    attempt_path = tmp_path / "plc-fill-st/attempt.json"
    data = attempt("plc-fill-st")
    data.update(
        {
            "task_family": "plc-control-edit",
            "suite_id": "plc-control-edit",
            "source_form": "st",
            "source_sha256": "source-hash",
            "submission_sha256": "submission-hash",
        }
    )
    data["grade"]["checks"] = [{"name": "plc-program-edit", "detail": {"parseable": True}}]
    save(attempt_path, data)

    write_run_audit(tmp_path)
    plc_csv = tmp_path / "reports/plc_control_edit_attempts.csv"
    assert rows(plc_csv)[0]["outcome"] == "Full"

    data["stop_reason"] = "provider_refusal"
    data["provider_refusal"] = True
    data["grade"].update({"outcome": "Refusal", "passed": False})
    save(attempt_path, data)
    write_run_audit(tmp_path)

    assert rows(tmp_path / "reports/attempts.csv")[0]["outcome"] == "Refusal"
    assert rows(plc_csv)[0]["outcome"] == "Refusal"
    summary = rows(tmp_path / "reports/plc_control_edit_summary.csv")[0]
    assert (summary["full"], summary["refusal"]) == ("0", "1")
    scores = (tmp_path / "reports/SCORES.md").read_text(encoding="utf-8")
    assert "### PLC representations" in scores
    assert "plc-fill-st" in scores and "Refusal" in scores
    assert json.loads(attempt_path.read_text(encoding="utf-8")) == data


def test_plc_report_separates_neutral_cases_with_the_same_form(tmp_path):
    cases = (
        ("plc-fill-st", "reservoir-fill-cutoff-edit", "Full"),
        ("plc-pump-st", "pump-valve-proof-edit", "Refusal"),
    )
    for task_id, stage, outcome in cases:
        data = attempt(
            task_id,
            outcome=outcome,
            stop="provider_refusal" if outcome == "Refusal" else "complete",
        )
        data.update(
            {
                "task_family": "plc-control-edit",
                "suite_id": "plc-control-edit",
                "source_form": "st",
                "stage": stage,
                "task_version": "1.0",
            }
        )
        data["grade"]["checks"] = [
            {"name": "plc-program-edit", "detail": {"parseable": outcome == "Full"}}
        ]
        save(tmp_path / task_id / "attempt.json", data)

    write_run_audit(tmp_path)
    summaries = {
        row["stage"]: row for row in rows(tmp_path / "reports/plc_control_edit_summary.csv")
    }
    assert set(summaries) == {stage for _, stage, _ in cases}
    assert {
        stage: (row["task_ids"], row["valid_attempts"], row["full"], row["refusal"])
        for stage, row in summaries.items()
    } == {
        "reservoir-fill-cutoff-edit": ('["plc-fill-st"]', "1", "1", "0"),
        "pump-valve-proof-edit": ('["plc-pump-st"]', "1", "0", "1"),
    }
    attempt_rows = rows(tmp_path / "reports/plc_control_edit_attempts.csv")
    assert {(row["task_id"], row["stage"]) for row in attempt_rows} == {
        (task_id, stage) for task_id, stage, _ in cases
    }
    scores = (tmp_path / "reports/SCORES.md").read_text(encoding="utf-8")
    assert "plc-fill-st" in scores and "plc-pump-st" in scores
