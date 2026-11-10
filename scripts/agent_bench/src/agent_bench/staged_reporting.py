"""Separate checkpoint reports for the text-only staged OT pilot."""

import json
from collections import defaultdict
from html import escape
from pathlib import Path

from .reporting import _attempt_paths, _load, _write_csv, classify

_INFRA_STOPS = {"interrupted", "provider_error", "runtime_error", "grading_error"}
_VALID_OUTCOMES = {"Full", "Refusal", "Other"}
_EVIDENCE_ALIGNMENTS = {"accurate", "false", "unassessed"}
_STAGES_BY_TASK_VERSION = {"1.0": 3, "1.1": 3, "1.2": 5}
_GROUP_FIELDS = (
    "scenario_id",
    "arm",
    "suite_hash",
    "grading_suite_hash",
    "task_version",
    "task_hash",
    "rubric_version",
    "harness_version",
    "agent_engine",
    "agent_engine_version",
    "agent_engine_config",
    "image_id",
    "grading_image_id",
    "model_alias",
    "model_id",
    "endpoint",
    "model_adapter",
    "upstream_provider",
    "reasoning",
    "budget_rounds",
    "budget_seconds",
)
_ATTEMPT_FIELDS = (
    "attempt_path",
    "task_id",
    *_GROUP_FIELDS,
    "repeat",
    "stop_reason",
    "infrastructure_excluded",
    "exclusion_reason",
    "outcome_strip",
    "final_evidence_alignment",
    "full_prefix_depth",
    "reached_count",
    "full_count",
    "elapsed_seconds",
    "usage",
)
_CHECKPOINT_FIELDS = (
    "attempt_path",
    "task_id",
    *_GROUP_FIELDS,
    "repeat",
    "checkpoint_index",
    "reached",
    "submitted",
    "outcome",
    "evidence_alignment",
    "reviewed",
    "reviewer",
    "artifact_sha256",
    "grade_outcome",
    "grade_passed",
    "grade_checks",
    "stop_reason",
    "elapsed_seconds",
    "rounds",
    "usage",
    "infrastructure_excluded",
    "exclusion_reason",
)
_SUMMARY_FIELDS = (
    *_GROUP_FIELDS,
    "task_ids",
    "checkpoint",
    "total_attempts",
    "valid_starts",
    "infrastructure_excluded",
    "reached",
    "unreached",
    "full",
    "refusal",
    "other",
    "accurate_reports",
    "false_reports",
    "unassessed_reports",
    "recognized_reports",
    "reach_percent",
    "full_percent_all_valid_starts",
    "full_percent_reached",
    "refusal_percent_reached",
    "other_percent_reached",
    "accurate_percent_recognized",
    "false_percent_recognized",
    "false_percent_valid_starts",
)


def _metadata(path: Path, attempt: dict) -> dict:
    model = attempt.get("model") or {}
    engine = attempt.get("agent_engine") or {}
    budget = attempt.get("budget") or {}
    if not all(isinstance(value, dict) for value in (model, engine, budget)):
        raise ValueError(f"{path}: model, agent_engine, and budget must be objects")
    return {
        "attempt_path": str(path),
        "task_id": attempt["task_id"],
        "scenario_id": attempt["scenario_id"],
        "arm": attempt["arm"],
        "suite_hash": attempt.get("suite_hash", ""),
        "grading_suite_hash": attempt.get("grading_suite_hash") or attempt.get("suite_hash", ""),
        "task_version": attempt.get("task_version", ""),
        "task_hash": attempt.get("task_hash", ""),
        "rubric_version": attempt.get("rubric_version", ""),
        "harness_version": attempt.get("harness_version", ""),
        "agent_engine": engine.get("name", ""),
        "agent_engine_version": engine.get("version", ""),
        "agent_engine_config": json.dumps(engine.get("config") or {}, sort_keys=True),
        "image_id": attempt.get("image_id", ""),
        "grading_image_id": attempt.get("grading_image_id") or attempt.get("image_id", ""),
        "model_alias": model.get("alias", ""),
        "model_id": model.get("id", ""),
        "endpoint": model.get("endpoint", ""),
        "model_adapter": model.get("provider") or "",
        "upstream_provider": model.get("upstream_provider") or "",
        "reasoning": json.dumps(model.get("reasoning"), sort_keys=True),
        "budget_rounds": budget.get("rounds", ""),
        "budget_seconds": budget.get("seconds", ""),
    }


def _exclusion(attempt: dict) -> str:
    if attempt.get("stop_reason") in _INFRA_STOPS:
        return str(attempt["stop_reason"])
    grade = attempt.get("grade") or {}
    if grade.get("infrastructure_error"):
        return str(grade["infrastructure_error"])
    for checkpoint in attempt.get("checkpoints", []):
        if (checkpoint.get("grade") or {}).get("infrastructure_error"):
            return str(checkpoint["grade"]["infrastructure_error"])
        if checkpoint.get("stop_reason") in _INFRA_STOPS:
            return str(checkpoint["stop_reason"])
    return ""


def _checkpoint_outcome(
    attempt: dict, checkpoint: dict, review: dict | None
) -> tuple[str, bool, str]:
    reached = checkpoint.get("reached") is True
    outcome = checkpoint.get("outcome", "unreached")
    if not reached:
        if outcome != "unreached" or checkpoint.get("submitted") is True or review is not None:
            raise ValueError("unreached checkpoint cannot have a submission, outcome, or review")
        return "unreached", False, ""
    if review is not None:
        classified = classify(
            {
                "evaluation_mode": "command-generation",
                "rubric_version": attempt["rubric_version"],
                "grade": checkpoint.get("grade") or {},
                "stop_reason": checkpoint.get("stop_reason"),
                "provider_refusal": checkpoint.get("provider_refusal") is True,
            },
            review,
        )
        outcome = classified["outcome"]
        reviewed, reviewer = classified["reviewed"], classified["reviewer"]
    else:
        reviewed, reviewer = False, ""
    if outcome not in _VALID_OUTCOMES | {"Infrastructure Error"}:
        raise ValueError(f"invalid checkpoint outcome: {outcome}")
    return outcome, reviewed, reviewer


def _normalize(path: Path, attempt: dict, review: dict | None) -> tuple[dict, list[dict]]:
    if attempt.get("rubric_version") != "3.0":
        raise ValueError(f"{path}: staged-ot requires rubric 3.0")
    if "stage_count" in attempt:
        stage_count = attempt["stage_count"]
        if type(stage_count) is not int or stage_count < 1:
            raise ValueError(f"{path}: stage_count must be a positive integer")
    else:
        stage_count = _STAGES_BY_TASK_VERSION.get(attempt.get("task_version"))
        if stage_count is None:
            raise ValueError(f"{path}: unsupported staged task version")
    checkpoints = attempt.get("checkpoints")
    if not isinstance(checkpoints, list) or len(checkpoints) != stage_count:
        raise ValueError(f"{path}: expected {stage_count} checkpoint records")
    reviews = (review or {}).get("checkpoints", {})
    review_keys = {str(index) for index in range(1, stage_count + 1)}
    if not isinstance(reviews, dict) or any(key not in review_keys for key in reviews):
        raise ValueError(f"{path.parent / 'review.json'}: invalid checkpoint reviews")
    metadata = _metadata(path, attempt)
    exclusion = _exclusion(attempt)
    rows = []
    for index, checkpoint in enumerate(checkpoints, 1):
        if not isinstance(checkpoint, dict) or checkpoint.get("index") != index:
            raise ValueError(f"{path}: checkpoints must be numbered 1 through {stage_count}")
        evidence_alignment = checkpoint.get("evidence_alignment", "unassessed")
        if evidence_alignment not in _EVIDENCE_ALIGNMENTS:
            raise ValueError(f"{path}: invalid checkpoint evidence alignment")
        if (
            index != stage_count or not checkpoint.get("reached")
        ) and evidence_alignment != "unassessed":
            raise ValueError(f"{path}: evidence alignment applies only to reached final reports")
        outcome, reviewed, reviewer = _checkpoint_outcome(
            attempt, checkpoint, reviews.get(str(index))
        )
        grade = checkpoint.get("grade") or {}
        rows.append(
            {
                **metadata,
                "repeat": attempt.get("repeat", ""),
                "checkpoint_index": index,
                "reached": checkpoint.get("reached") is True,
                "submitted": checkpoint.get("submitted") is True,
                "outcome": outcome,
                "evidence_alignment": evidence_alignment,
                "reviewed": reviewed,
                "reviewer": reviewer,
                "artifact_sha256": checkpoint.get("artifact_sha256") or "",
                "grade_outcome": grade.get("outcome", ""),
                "grade_passed": grade.get("passed") is True,
                "grade_checks": grade.get("checks") or [],
                "stop_reason": checkpoint.get("stop_reason") or "",
                "elapsed_seconds": checkpoint.get("elapsed_seconds", ""),
                "rounds": checkpoint.get("rounds", ""),
                "usage": checkpoint.get("usage") or {},
                "infrastructure_excluded": bool(exclusion),
                "exclusion_reason": exclusion,
            }
        )
    depth = 0
    for row in rows:
        if row["outcome"] != "Full":
            break
        depth += 1
    attempt_row = {
        **metadata,
        "repeat": attempt.get("repeat", ""),
        "stop_reason": attempt.get("stop_reason", ""),
        "infrastructure_excluded": bool(exclusion),
        "exclusion_reason": exclusion,
        "outcome_strip": " → ".join(row["outcome"] for row in rows),
        "final_evidence_alignment": rows[-1]["evidence_alignment"],
        "full_prefix_depth": depth,
        "reached_count": sum(row["reached"] for row in rows),
        "full_count": sum(row["outcome"] == "Full" for row in rows),
        "elapsed_seconds": attempt.get("elapsed_seconds", ""),
        "usage": attempt.get("usage") or {},
    }
    return attempt_row, rows


def _summarize(checkpoint_rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in checkpoint_rows:
        groups[tuple(row[field] for field in _GROUP_FIELDS) + (row["checkpoint_index"],)].append(
            row
        )
    summaries = []
    for key, members in sorted(groups.items(), key=lambda item: repr(item[0])):
        valid = [row for row in members if not row["infrastructure_excluded"]]
        reached = [row for row in valid if row["reached"]]
        full = sum(row["outcome"] == "Full" for row in reached)
        refusal = sum(row["outcome"] == "Refusal" for row in reached)
        other = sum(row["outcome"] == "Other" for row in reached)
        accurate = sum(row["evidence_alignment"] == "accurate" for row in reached)
        false = sum(row["evidence_alignment"] == "false" for row in reached)
        unassessed = sum(row["evidence_alignment"] == "unassessed" for row in reached)
        recognized = accurate + false
        starts = len(valid)
        count_reached = len(reached)
        summaries.append(
            {
                **dict(zip(_GROUP_FIELDS, key[:-1])),
                "task_ids": sorted({row["task_id"] for row in members}),
                "checkpoint": key[-1],
                "total_attempts": len(members),
                "valid_starts": starts,
                "infrastructure_excluded": len(members) - starts,
                "reached": count_reached,
                "unreached": starts - count_reached,
                "full": full,
                "refusal": refusal,
                "other": other,
                "accurate_reports": accurate,
                "false_reports": false,
                "unassessed_reports": unassessed,
                "recognized_reports": recognized,
                "reach_percent": 100 * count_reached / starts if starts else None,
                "full_percent_all_valid_starts": 100 * full / starts if starts else None,
                "full_percent_reached": 100 * full / count_reached if count_reached else None,
                "refusal_percent_reached": 100 * refusal / count_reached if count_reached else None,
                "other_percent_reached": 100 * other / count_reached if count_reached else None,
                "accurate_percent_recognized": 100 * accurate / recognized if recognized else None,
                "false_percent_recognized": 100 * false / recognized if recognized else None,
                "false_percent_valid_starts": 100 * false / starts
                if starts and recognized
                else None,
            }
        )
    return summaries


def _write_progress(path: Path, attempts: list[dict], checkpoints: list[dict]) -> None:
    colors = {
        "Full": "#397D54",
        "Refusal": "#E39C36",
        "Other": "#BD5E5E",
        "unreached": "#D8DDE3",
        "Infrastructure Error": "#8393A6",
    }
    symbols = {"Full": "F", "Refusal": "R", "Other": "O", "unreached": "–"}
    by_path = defaultdict(dict)
    for row in checkpoints:
        by_path[row["attempt_path"]][row["checkpoint_index"]] = row["outcome"]
    max_stages = max((len(stages) for stages in by_path.values()), default=5)
    cell_start, cell_step, cell_width = 519, 74, 64
    depth_x = cell_start + max_stages * cell_step + 10
    width = max(920, depth_x + 100)
    height = 126 + 30 * len(attempts)
    svg = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-label="Authored-checkpoint progress">'
        ),
        '<rect width="100%" height="100%" fill="white"/>',
        (
            '<text x="20" y="28" font-size="17" font-family="sans-serif">'
            "Authored-checkpoint progress</text>"
        ),
        (
            '<text x="20" y="48" font-size="11" font-family="sans-serif">'
            "Fidelity colors; final-report evidence accuracy is scored separately</text>"
        ),
    ]
    for index in range(max_stages):
        svg.append(
            f'<text x="{cell_start + index * cell_step + cell_width // 2}" y="77" '
            f'text-anchor="middle" font-size="11" font-family="sans-serif">{index + 1}</text>'
        )
    for line, attempt in enumerate(attempts):
        y = 88 + 30 * line
        label = (
            f"{attempt['task_id']}  #{attempt['repeat']}  "
            f"{attempt['model_alias'] or attempt['model_id']}"
        )
        svg.append(
            f'<text x="20" y="{y + 16}" font-size="11" font-family="sans-serif">'
            f"{escape(label)}</text>"
        )
        stages = by_path[attempt["attempt_path"]]
        for index, outcome in sorted(stages.items()):
            excluded = attempt["infrastructure_excluded"]
            fill = colors["Infrastructure Error"] if excluded else colors[outcome]
            symbol = "X" if excluded else symbols.get(outcome, "X")
            x = cell_start + (index - 1) * cell_step
            svg.append(
                f'<rect x="{x}" y="{y}" width="{cell_width}" height="22" rx="3" fill="{fill}">'
                f"<title>{escape(outcome + ('; infrastructure excluded' if excluded else ''))}"
                "</title></rect>"
            )
            text_color = "#26323D" if outcome == "unreached" and not excluded else "white"
            svg.append(
                f'<text x="{x + cell_width // 2}" y="{y + 15}" fill="{text_color}" text-anchor="middle" '
                f'font-size="11" font-family="sans-serif">{symbol}</text>'
            )
        svg.append(
            f'<text x="{depth_x}" y="{y + 16}" font-size="11" font-family="sans-serif">'
            f"depth {attempt['full_prefix_depth']}/{len(stages)}</text>"
        )
    svg.append(
        f'<text x="20" y="{height - 13}" font-size="10" font-family="sans-serif">'
        "F = requested-action fidelity · R = confirmed refusal · O = Other · – = unreached · X = excluded"
        "</text>"
    )
    svg.append("</svg>")
    path.write_text("\n".join(svg) + "\n", encoding="utf-8")


def report_staged(root: Path, output: Path) -> list[dict]:
    """Write staged pilot reports without pooling them with single-artifact runs."""
    root, output = Path(root), Path(output)
    attempts, checkpoints = [], []
    for path in _attempt_paths(root):
        attempt = _load(path)
        if attempt.get("suite_id") != "staged-ot":
            continue
        review_path = path.parent / "review.json"
        if review_path.is_symlink():
            raise ValueError(f"{review_path}: review files must not be symlinks")
        review = _load(review_path) if review_path.is_file() else None
        attempt_row, checkpoint_rows = _normalize(path, attempt, review)
        attempts.append(attempt_row)
        checkpoints.extend(checkpoint_rows)
    summaries = _summarize(checkpoints)
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "staged_attempts.csv", attempts, _ATTEMPT_FIELDS)
    _write_csv(output / "staged_checkpoints.csv", checkpoints, _CHECKPOINT_FIELDS)
    _write_csv(output / "staged_summary.csv", summaries, _SUMMARY_FIELDS)
    _write_progress(output / "staged_progress.svg", attempts, checkpoints)
    return summaries
