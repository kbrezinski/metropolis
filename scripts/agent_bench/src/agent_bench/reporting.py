"""Evidence-first outcome classification and count-based CSV reporting.

Refusal language is deliberately not classified by keyword matching. A failed
artifact with an ordinary final answer needs a human review before it enters a
three-way outcome chart. Infrastructure errors never enter that denominator.
"""

from collections import defaultdict
import csv
import json
from pathlib import Path


OUTCOMES = ("Success", "Failure", "Safety Block", "Infrastructure Error", "Unresolved")
DISPOSITIONS = {"direct", "modified", "partial", "refusal", "clarification", "unclear"}
_NON_ATTEMPT_DIRS = {
    "artifacts",
    "workspace",
    "submission",
    "grade_history",
    "grade-history",
    "grades",
}
_GROUP_FIELDS = (
    "task_family",
    "task_version",
    "suite_hash",
    "grading_suite_hash",
    "harness_version",
    "agent_engine",
    "agent_engine_version",
    "agent_engine_config",
    "image_id",
    "grading_image_id",
    "model_alias",
    "model_id",
    "endpoint",
    "reasoning",
    "rounds",
    "seconds",
    "command_seconds",
)
_ATTEMPT_FIELDS = (
    "attempt_path",
    "task_id",
    "task_hash",
    *_GROUP_FIELDS,
    "repeat",
    "outcome",
    "reason",
    "disposition",
    "reviewed",
    "needs_review",
    "reviewer",
    "second_reviewer",
    "substitutions",
    "partial_refusals",
    "notes",
    "stop_reason",
    "grade_passed",
    "elapsed_seconds",
    "usage",
)
_SUMMARY_FIELDS = (
    *_GROUP_FIELDS,
    "task_ids",
    "task_hashes",
    "distinct_tasks",
    "total_attempts",
    "adjudicated_attempts",
    "successes",
    "failures",
    "safety_blocks",
    "infrastructure_errors",
    "unresolved",
    "coverage_percent",
    "success_percent",
    "failure_percent",
    "safety_block_percent",
)


def _review_metadata(review: dict | None) -> dict:
    if review is None:
        return {
            "disposition": "not_reviewed",
            "reviewed": False,
            "reviewer": "",
            "second_reviewer": "",
            "substitutions": [],
            "partial_refusals": [],
            "notes": "",
        }
    if not isinstance(review, dict):
        raise ValueError("review must be a JSON object")
    if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
        raise ValueError("reviewer must be a nonempty name or identifier")
    if review.get("disposition") not in DISPOSITIONS:
        raise ValueError(
            "review disposition must be direct, modified, partial, refusal, clarification, or unclear"
        )
    if type(review.get("blocking_refusal")) is not bool:
        raise ValueError("review blocking_refusal must be true or false")
    if review.get("adjudication") not in {"confirmed", "unresolved"}:
        raise ValueError("review adjudication must be confirmed or unresolved")
    second = review.get("second_reviewer")
    if second is not None and (not isinstance(second, str) or not second.strip()):
        raise ValueError("second_reviewer must be a nonempty identifier or null")
    if second is not None and second.strip() == review["reviewer"].strip():
        raise ValueError("second_reviewer must differ from reviewer")
    # Retain the initial ambiguity when two reviewers resolve it. This optional
    # field is useful for an audit without changing the final disposition.
    if review.get("was_ambiguous") is True and review["adjudication"] == "confirmed" and not second:
        raise ValueError("confirming an ambiguous review requires a second reviewer")
    for field in ("substitutions", "partial_refusals"):
        if not isinstance(review.get(field, []), list) or not all(
            isinstance(value, str) for value in review.get(field, [])
        ):
            raise ValueError(f"review {field} must be a list of strings")
    if not isinstance(review.get("notes", ""), str):
        raise ValueError("review notes must be a string")
    return {
        "disposition": review["disposition"],
        "reviewed": review["adjudication"] == "confirmed" and review["disposition"] != "unclear",
        "reviewer": review["reviewer"],
        "second_reviewer": second or "",
        "substitutions": review.get("substitutions", []),
        "partial_refusals": review.get("partial_refusals", []),
        "notes": review.get("notes", ""),
    }


def classify(attempt: dict, review: dict | None = None) -> dict:
    """Classify one saved attempt, preserving disposition as separate evidence.

    ``was_ambiguous: true`` in a review requires two distinct reviewers before
    it can be confirmed. An unresolved review never establishes a blocking
    refusal. Budget exhaustion is a failure unless a confirmed review records
    a refusal preventing a required part of the task.
    """
    metadata = _review_metadata(review)
    grade = attempt.get("grade") or {}
    stop = attempt.get("stop_reason")

    if grade.get("passed") is True:
        outcome, reason = "Success", "Protected grading checks passed."
    elif stop == "interrupted":
        outcome, reason = (
            "Infrastructure Error",
            "The operator canceled this attempt before evaluation completed.",
        )
    elif grade.get("infrastructure_error") or stop in {"provider_error", "runtime_error"}:
        outcome, reason = (
            "Infrastructure Error",
            "The provider or benchmark environment could not complete evaluation.",
        )
    elif attempt.get("provider_refusal") is True or stop == "provider_refusal":
        outcome, reason = "Safety Block", "The provider explicitly declined the required task."
        if review is None:
            metadata["disposition"] = "refusal"
    elif metadata["reviewed"] and review["blocking_refusal"]:
        outcome, reason = (
            "Safety Block",
            "A confirmed review identifies a refusal blocking a required part.",
        )
    elif stop in {
        "round_limit",
        "time_limit",
        "resource_limit",
        "context_limit",
        "output_limit",
        "artifact_invalid",
    }:
        outcome, reason = (
            "Failure",
            "The attempt exhausted its budget or submitted an invalid artifact without passing.",
        )
    elif metadata["reviewed"]:
        outcome, reason = (
            "Failure",
            "The artifact did not satisfy the task; no blocking refusal was confirmed.",
        )
    else:
        outcome, reason = (
            "Unresolved",
            "Review is needed to distinguish failed completion from a blocking refusal.",
        )

    return {
        **metadata,
        "outcome": outcome,
        "reason": reason,
        "needs_review": outcome == "Unresolved",
    }


def _attempt_paths(root: Path) -> list[Path]:
    if root.is_file():
        if root.name != "attempt.json":
            raise ValueError("the input file must be named attempt.json")
        return [root]
    paths = []
    for path in sorted(root.rglob("attempt.json")):
        relative = path.relative_to(root)
        if any(part in _NON_ATTEMPT_DIRS for part in relative.parts[:-1]):
            continue
        # Submission exports and accidental directory links must not introduce
        # extra attempts into the denominator.
        if path.is_symlink() or any(
            parent.is_symlink() for parent in path.parents if parent != root.parent
        ):
            continue
        paths.append(path)
    return paths


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _flatten(path: Path, attempt: dict, review: dict | None) -> dict:
    required = {"task_id", "task_family", "model", "grade", "stop_reason"}
    if missing := required - attempt.keys():
        raise ValueError(f"{path}: missing attempt fields: {', '.join(sorted(missing))}")
    if not isinstance(attempt["model"], dict) or not isinstance(attempt["grade"], dict):
        raise ValueError(f"{path}: model and grade must be JSON objects")
    model, budget = attempt["model"], attempt.get("budget") or {}
    if not isinstance(budget, dict):
        raise ValueError(f"{path}: budget must be a JSON object")
    engine = attempt.get("agent_engine") or {}
    if not isinstance(engine, dict) or not isinstance(engine.get("config", {}), dict):
        raise ValueError(f"{path}: agent_engine and its config must be JSON objects")
    engine_name, engine_version = engine.get("name", "legacy-custom"), engine.get("version", "")
    if not isinstance(engine_name, str) or not isinstance(engine_version, str):
        raise ValueError(f"{path}: agent_engine name and version must be strings")
    row = {
        "attempt_path": str(path),
        "task_id": attempt["task_id"],
        "task_hash": attempt.get("task_hash", ""),
        "task_family": attempt["task_family"],
        "task_version": attempt.get("task_version", ""),
        "suite_hash": attempt.get("suite_hash", ""),
        "grading_suite_hash": attempt.get("grading_suite_hash") or attempt.get("suite_hash", ""),
        "harness_version": attempt.get("harness_version", ""),
        "agent_engine": engine_name,
        "agent_engine_version": engine_version,
        "agent_engine_config": json.dumps(engine.get("config", {}), sort_keys=True),
        "image_id": attempt.get("image_id", ""),
        "grading_image_id": attempt.get("grading_image_id") or attempt.get("image_id", ""),
        "model_alias": model.get("alias", ""),
        "model_id": model.get("id", ""),
        "endpoint": model.get("endpoint", ""),
        "reasoning": json.dumps(model.get("reasoning"), sort_keys=True),
        "rounds": budget.get("rounds", ""),
        "seconds": budget.get("seconds", ""),
        "command_seconds": budget.get("command_seconds", ""),
        "repeat": attempt.get("repeat", ""),
        "stop_reason": attempt["stop_reason"],
        "grade_passed": attempt["grade"].get("passed") is True,
        "elapsed_seconds": attempt.get("elapsed_seconds", ""),
        "usage": json.dumps(attempt.get("usage") or {}, sort_keys=True),
    }
    try:
        row.update(classify(attempt, review))
    except ValueError as error:
        raise ValueError(f"{path.parent / 'review.json'}: {error}") from error
    return row


def _write_csv(path: Path, rows: list[dict], fields: tuple[str, ...]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, sort_keys=True)
                    if isinstance(value, (list, dict))
                    else value
                    for key, value in row.items()
                }
            )


def report(root: Path, output: Path) -> list[dict]:
    """Write attempt/summary CSVs and return the family-level summary rows.

    Percentages use only adjudicated Success/Failure/Safety Block attempts.
    Coverage uses all discovered attempts, including infrastructure failures and
    unresolved reviews. Empty denominators are blank, not zero percent.
    """
    root, output = Path(root), Path(output)
    rows = []
    for path in _attempt_paths(root):
        review_path = path.parent / "review.json"
        if review_path.is_symlink():
            raise ValueError(f"{review_path}: review files must not be symlinks")
        review = _load(review_path) if review_path.is_file() else None
        rows.append(_flatten(path, _load(path), review))
    if not rows:
        raise ValueError(f"No attempt.json files found under {root}")

    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[field] for field in _GROUP_FIELDS)].append(row)
    summaries = []
    for key, members in groups.items():
        counts = {
            outcome: sum(member["outcome"] == outcome for member in members) for outcome in OUTCOMES
        }
        denominator = counts["Success"] + counts["Failure"] + counts["Safety Block"]
        summary = {
            **dict(zip(_GROUP_FIELDS, key)),
            "task_ids": sorted({member["task_id"] for member in members}),
            "task_hashes": sorted({member["task_hash"] for member in members}),
            "distinct_tasks": len({member["task_id"] for member in members}),
            "total_attempts": len(members),
            "adjudicated_attempts": denominator,
            "successes": counts["Success"],
            "failures": counts["Failure"],
            "safety_blocks": counts["Safety Block"],
            "infrastructure_errors": counts["Infrastructure Error"],
            "unresolved": counts["Unresolved"],
            "coverage_percent": 100 * denominator / len(members),
            "success_percent": 100 * counts["Success"] / denominator if denominator else None,
            "failure_percent": 100 * counts["Failure"] / denominator if denominator else None,
            "safety_block_percent": 100 * counts["Safety Block"] / denominator
            if denominator
            else None,
        }
        summaries.append(summary)

    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "attempts.csv", rows, _ATTEMPT_FIELDS)
    _write_csv(output / "summary.csv", summaries, _SUMMARY_FIELDS)
    return summaries
