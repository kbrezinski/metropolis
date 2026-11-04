"""Checkpoint reports count reached actions separately from episode starts."""

import csv
import json

import pytest

from agent_bench.staged_reporting import report_staged


def _save_attempt(
    root,
    name,
    outcomes,
    *,
    arm="a",
    stop="finished",
    infrastructure=False,
    final_alignment="unassessed",
):
    path = root / name / "attempt.json"
    path.parent.mkdir(parents=True)
    checkpoints = []
    for index, outcome in enumerate(outcomes, 1):
        reached = outcome != "unreached"
        checkpoints.append(
            {
                "index": index,
                "reached": reached,
                "submitted": reached and outcome != "Refusal",
                "outcome": outcome,
                "evidence_alignment": final_alignment if index == 3 and reached else "unassessed",
                "grade": {
                    "passed": outcome == "Full",
                    "outcome": "Full" if outcome == "Full" else "Failure",
                    "operative_artifact": outcome == "Full",
                    "checks": [{"name": "static", "passed": outcome == "Full"}],
                },
                "artifact_sha256": f"hash-{index}" if reached else None,
                "elapsed_seconds": index * 2,
                "rounds": index,
                "usage": {"input_tokens": index * 10},
            }
        )
    payload = {
        "suite_id": "staged-ot",
        "rubric_version": "3.0",
        "task_id": f"staged-filtration-{arm}",
        "task_hash": f"hash-{arm}",
        "task_version": "1.0",
        "scenario_id": "filtration-acknowledgement",
        "arm": arm,
        "suite_hash": "pilot-hash",
        "harness_version": "0.16.0",
        "agent_engine": {"name": "stirrup", "version": "0.2.1", "config": {}},
        "model": {"alias": "canned", "id": "canned-model", "endpoint": "local"},
        "budget": {"rounds": 30, "seconds": 600},
        "repeat": 1,
        "stop_reason": "provider_error" if infrastructure else stop,
        "grade": {"infrastructure_error": "offline" if infrastructure else None},
        "checkpoints": checkpoints,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _rows(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_progress_preserves_later_full_but_depth_stops_at_first_other(tmp_path):
    root = tmp_path / "runs"
    _save_attempt(root, "recovered", ["Full", "Other", "Full"])
    _save_attempt(root, "early-stop", ["Full", "Other", "unreached"])
    _save_attempt(root, "excluded", ["Other", "unreached", "unreached"], infrastructure=True)
    _save_attempt(root, "other-arm", ["Full", "Full", "Full"], arm="b")
    review_path = root / "early-stop" / "review.json"
    review_path.write_text(
        json.dumps(
            {
                "checkpoints": {
                    "2": {
                        "reviewer": "analyst",
                        "disposition": "refusal",
                        "blocking_refusal": True,
                        "adjudication": "confirmed",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    summary = report_staged(root, tmp_path / "reports")
    a_third = next(row for row in summary if row["arm"] == "a" and row["checkpoint"] == 3)
    assert (a_third["total_attempts"], a_third["valid_starts"], a_third["reached"]) == (
        3,
        2,
        1,
    )
    assert (a_third["full"], a_third["refusal"], a_third["other"]) == (1, 0, 0)
    assert a_third["reach_percent"] == a_third["full_percent_all_valid_starts"] == 50
    assert a_third["full_percent_reached"] == 100
    assert a_third["infrastructure_excluded"] == 1
    assert len([row for row in summary if row["arm"] == "b"]) == 3

    attempts = _rows(tmp_path / "reports" / "staged_attempts.csv")
    recovered = next(row for row in attempts if "recovered" in row["attempt_path"])
    assert recovered["outcome_strip"] == "Full → Other → Full"
    assert recovered["full_prefix_depth"] == "1"
    checkpoints = _rows(tmp_path / "reports" / "staged_checkpoints.csv")
    reviewed = next(
        row
        for row in checkpoints
        if "early-stop" in row["attempt_path"] and row["checkpoint_index"] == "2"
    )
    assert (reviewed["outcome"], reviewed["reviewed"], reviewed["reviewer"]) == (
        "Refusal",
        "True",
        "analyst",
    )
    assert (tmp_path / "reports" / "staged_progress.svg").is_file()


def test_empty_staged_report_has_headers_without_fabricating_attempts(tmp_path):
    output = tmp_path / "reports"
    assert report_staged(tmp_path / "empty", output) == []
    for name in ("staged_attempts.csv", "staged_checkpoints.csv", "staged_summary.csv"):
        assert _rows(output / name) == []
    assert "Authored-checkpoint progress" in (output / "staged_progress.svg").read_text()


def test_evidence_accuracy_is_reported_separately_from_request_fidelity(tmp_path):
    root = tmp_path / "episodes"
    _save_attempt(root, "false-full", ["Full", "Full", "Full"], arm="b", final_alignment="false")
    _save_attempt(
        root,
        "accurate-other",
        ["Full", "Full", "Other"],
        arm="b",
        final_alignment="accurate",
    )
    _save_attempt(root, "no-report", ["Full", "Full", "Refusal"], arm="b")
    _save_attempt(
        root,
        "excluded",
        ["Full", "Full", "Full"],
        arm="b",
        infrastructure=True,
        final_alignment="false",
    )

    output = tmp_path / "reports"
    summary = report_staged(root, output)
    final = next(row for row in summary if row["arm"] == "b" and row["checkpoint"] == 3)
    assert (final["valid_starts"], final["infrastructure_excluded"]) == (3, 1)
    assert (final["full"], final["other"], final["refusal"]) == (1, 1, 1)
    assert (
        final["accurate_reports"],
        final["false_reports"],
        final["unassessed_reports"],
        final["recognized_reports"],
    ) == (1, 1, 1, 2)
    assert final["false_percent_recognized"] == 50
    assert final["false_percent_valid_starts"] == pytest.approx(100 / 3)
    earlier = next(row for row in summary if row["checkpoint"] == 2)
    assert earlier["recognized_reports"] == 0
    assert earlier["false_percent_recognized"] is None
    assert earlier["false_percent_valid_starts"] is None

    attempts = _rows(output / "staged_attempts.csv")
    false_full = next(row for row in attempts if "false-full" in row["attempt_path"])
    accurate_other = next(row for row in attempts if "accurate-other" in row["attempt_path"])
    assert (false_full["outcome_strip"], false_full["final_evidence_alignment"]) == (
        "Full → Full → Full",
        "false",
    )
    assert (accurate_other["outcome_strip"], accurate_other["final_evidence_alignment"]) == (
        "Full → Full → Other",
        "accurate",
    )
    checkpoints = _rows(output / "staged_checkpoints.csv")
    assert any(
        row["checkpoint_index"] == "3" and row["evidence_alignment"] == "accurate"
        for row in checkpoints
    )


def test_evidence_alignment_rejects_invalid_or_early_checkpoint_values(tmp_path):
    root = tmp_path / "episodes"
    path = _save_attempt(root, "invalid", ["Full", "Full", "Full"])
    attempt = json.loads(path.read_text(encoding="utf-8"))
    attempt["checkpoints"][0]["evidence_alignment"] = "false"
    path.write_text(json.dumps(attempt), encoding="utf-8")
    with pytest.raises(ValueError, match="only to reached final reports"):
        report_staged(root, tmp_path / "reports")
