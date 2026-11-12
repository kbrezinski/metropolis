"""Readable run inventories derived from the existing versioned CSV reports."""

from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path

from .catalog_presentation import checkpoint_action, coverage_rows, display_labels
from .reporting import _attempt_paths, _load, report
from .staged_reporting import report_staged

_CONFIGURATION_FIELDS = (
    "evaluation_mode",
    "rubric_version",
    "model_alias",
    "model_id",
    "endpoint",
    "model_adapter",
    "upstream_provider",
    "reasoning",
    "agent_engine",
    "agent_engine_version",
    "agent_engine_config",
)
_TRANSPORT_DIAGNOSTICS = ("mailbox_integrity_anomalies", "unanswered_requests")


def render_run_overview(record: dict) -> list[str]:
    """Render counts shared by API and mailbox reports, without transport details."""
    completed = (
        f"completed: {record['completed_attempts']}; " if "completed_attempts" in record else ""
    )
    lines = [
        f"Run status: {record.get('status', 'unknown')}",
        f"Requested: {record['requested_attempts']}; persisted: "
        f"{record['persisted_attempts']}; {completed}adjudicated/valid: "
        f"{record['adjudicated_attempts']}; infrastructure excluded: "
        f"{record['excluded_attempts']}; unstarted: {record['unstarted_attempts']}.",
    ]
    if record.get("reason"):
        lines.append(f"Reason: {record['reason']}")
    return lines


def _csv_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _cell(value) -> str:
    return str(value if value is not None else "unknown").replace("|", "\\|").replace("\n", " ")


def _true(value) -> bool:
    return value is True or str(value).lower() in {"true", "1", "yes"}


def _model_label(row: dict) -> str:
    return (
        " / ".join(
            _cell(row[key])
            for key in ("model_alias", "model_id", "model_adapter", "upstream_provider")
            if row.get(key)
        )
        or "unknown model"
    )


def _attempt_key(row: dict) -> tuple:
    return (
        row.get("model_alias", ""),
        row["task_id"],
        row.get("prompt_condition") or "baseline",
        str(row.get("repeat", "")),
    )


def _saved_matrix(manifest: dict, rows: list[dict]) -> tuple[list[dict] | None, str]:
    if "attempts" in manifest:
        matrix = manifest["attempts"]
        if not isinstance(matrix, list):
            raise ValueError("run.json attempts must be a list")
        return matrix, "manifest"
    if "tasks" not in manifest or "repeats" not in manifest:
        return None, "persisted"
    tasks, repeats = manifest["tasks"], manifest["repeats"]
    if (
        not isinstance(tasks, list)
        or not all(isinstance(task, str) for task in tasks)
        or type(repeats) is not int
        or repeats < 1
    ):
        raise ValueError("run.json tasks and repeats do not describe an attempt matrix")
    by_task = manifest.get("task_prompt_conditions", {})
    if not isinstance(by_task, dict):
        raise ValueError("run.json task_prompt_conditions must be an object")
    aliases = {row.get("model_alias", "") for row in rows}
    alias = next(iter(aliases)) if len(aliases) == 1 else "codex-subagent"
    matrix = []
    for task in tasks:
        conditions = by_task.get(task, manifest.get("prompt_conditions", ["baseline"]))
        if not isinstance(conditions, list) or not all(
            isinstance(item, str) for item in conditions
        ):
            raise ValueError("run.json prompt conditions must be lists of condition IDs")
        matrix.extend(
            {
                "model_alias": alias,
                "task_id": task,
                "prompt_condition": condition,
                "repeat": repeat,
            }
            for condition in conditions
            for repeat in range(1, repeats + 1)
        )
    return matrix, "legacy-manifest"


def _render(record: dict) -> str:
    lines = render_run_overview(record)
    lines += ["", f"Decision summaries: {record['decision_summaries']}."]
    if record["requested_count_source"] == "persisted":
        lines.append("No saved attempt matrix; requested count reflects discovered attempts only.")
    for alias, reason in record.get("model_errors", {}).items():
        lines.append(f"Model {_cell(alias)} stopped: {_cell(reason)}")
    if any(key in record for key in _TRANSPORT_DIAGNOSTICS):
        lines.append(
            "Saved mailbox diagnostics retained: "
            f"{len(record.get('mailbox_integrity_anomalies', []))} integrity anomalies; "
            f"{len(record.get('unanswered_requests', []))} unanswered requests."
        )
    if record["suite_id"] == "staged-ot":
        lines += [
            "",
            "| Model | Episode | Repeat | Outcomes by checkpoint | Consecutive Full depth | Excluded |",
            "|---|---|---:|---|---:|---|",
        ]
        for row in record["attempts"]:
            lines.append(
                f"| {_model_label(row)} | {_cell(row['task_id'])} | {row['repeat']} | "
                f"{_cell(row['outcome_strip'])} | {row['full_prefix_depth']}/"
                f"{len(row['outcome_strip'].split(' → '))} | {row['infrastructure_excluded']} |"
            )
        lines += [
            "",
            "| Model | Scenario | Arm | Checkpoint | Action | Reached / valid starts | Full / reached | Confirmed Refusal / reached | Excluded |",
            "|---|---|---|---:|---|---:|---:|---:|---:|",
        ]
        for row in record["groups"]:
            task_ids = row.get("task_ids") or []
            if isinstance(task_ids, str):
                task_ids = json.loads(task_ids)
            task_id = task_ids[0] if len(task_ids) == 1 else ""
            action = checkpoint_action(task_id, row["task_version"], int(row["checkpoint"]))
            lines.append(
                f"| {_model_label(row)} | {_cell(row['scenario_id'])} | {_cell(row['arm'])} "
                f"| {row['checkpoint']} | {_cell(action)} | "
                f"{row['reached']}/{row['valid_starts']} | "
                f"{row['full']}/{row['reached']} | {row['refusal']}/{row['reached']} | "
                f"{row['infrastructure_excluded']} |"
            )
        lines += ["", "Depth measures consecutive authored checkpoints."]
    else:
        historical_ids = (
            {
                row["task_id"]
                for row in record["attempts"]
                if row.get("task_family") == "plc-source-injection"
            }
            if record["suite_id"] in {"default", "plc-control-edit"}
            else set()
        )
        coverage = coverage_rows(
            [task_id for task_id in record["selected_task_ids"] if task_id not in historical_ids],
            [row["task_id"] for row in record["attempts"] if row["task_id"] not in historical_ids],
            suite_id=record["suite_id"],
        )
        if historical_ids:
            coverage.append(
                {
                    "study_design": "Legacy / unclassified",
                    "ot_action": "Legacy / unclassified",
                    "selected_tasks": len(historical_ids & set(record["selected_task_ids"])),
                    "observed_tasks": len(historical_ids),
                }
            )
        lines += [
            "",
            "Coverage counts distinct selected and observed task IDs; staged episodes are separate.",
            "",
            "| Study design | OT action | Selected | Observed |",
            "|---|---|---:|---:|",
        ]
        for item in coverage:
            lines.append(
                f"| {_cell(item['study_design'])} | {_cell(item['ot_action'])} | "
                f"{item['selected_tasks']} | {item['observed_tasks']} |"
            )
        for design in (
            "Standalone requests",
            "Request-note framing",
            "Changed-observation comparisons",
            "PLC representations",
            "Legacy / unclassified",
        ):
            members = [
                row
                for row in record["attempts"]
                if (
                    "Legacy / unclassified"
                    if row.get("task_family") == "plc-source-injection"
                    else display_labels(row["task_id"], suite_id=record["suite_id"])[0]
                )
                == design
            ]
            if not members:
                continue
            lines += [
                "",
                f"### {design}",
                "",
                "| Model | Task | OT action | Comparison | Arm | Condition | Repeat | Rubric | Outcome |",
                "|---|---|---|---|---|---|---:|---|---|",
            ]
            for row in members:
                _, action, comparison = (
                    ("Legacy / unclassified", "Legacy / unclassified", "")
                    if row.get("task_family") == "plc-source-injection"
                    else display_labels(row["task_id"], suite_id=record["suite_id"])
                )
                lines.append(
                    f"| {_model_label(row)} | {_cell(row['task_id'])} | {_cell(action)} | "
                    f"{_cell(comparison)} | "
                    f"{_cell(row['task_id'].rsplit('-', 1)[-1] if comparison else '')} | "
                    f"{_cell(row['prompt_condition'])} | "
                    f"{row['repeat']} | {_cell(row['rubric_version'])} | "
                    f"{_cell(row['outcome'])} |"
                )
        lines += ["", "Counts below retain each model configuration and rubric separately."]
        for group in record["outcomes_by_rubric"]:
            lines.append(
                f"- {_model_label(group)} (engine config "
                f"{group['engine_configuration_sha256'][:12]}), "
                f"{group['evaluation_mode']}, rubric {group['rubric_version']}: "
                f"{group['outcomes']}; adjudicated denominator {group['adjudicated_attempts']}."
            )
    lines += [
        "",
        "Submitted artifacts are graded statically; submitted actions are never executed.",
        "Refusal counts use structured provider evidence or confirmed review. A decision summary "
        "does not adjudicate a refusal or change a score.",
        "See the CSVs in this directory for grouped rates, denominators, and evidence alignment.",
    ]
    return "\n".join(lines) + "\n"


def write_run_audit(root: Path, *, output: Path | None = None, status: str | None = None) -> dict:
    """Regenerate native reports and an audit inventory; preserve saved task grades."""
    root = Path(root)
    output = Path(output) if output is not None else root / "reports"
    output.mkdir(parents=True, exist_ok=True)
    manifest = _load(root / "run.json") if (root / "run.json").is_file() else {}
    active = _load(root / "active.json") if (root / "active.json").is_file() else {}
    prior_path = root / "reports/results.json"
    prior = _load(prior_path) if prior_path.is_file() else {}
    paths = _attempt_paths(root)
    saved = [_load(path) for path in paths]
    suite_ids = {item.get("suite_id", "default") for item in saved}
    if "staged-ot" in suite_ids and suite_ids != {"staged-ot"}:
        raise ValueError("Report staged-ot and other suites from separate run directories")
    staged = suite_ids == {"staged-ot"} or (not paths and manifest.get("suite_id") == "staged-ot")
    groups = []
    rows = []
    if staged:
        groups = report_staged(root, output)
        rows = _csv_rows(output / "staged_attempts.csv")
    elif paths:
        groups = report(root, output)
        rows = _csv_rows(output / "attempts.csv")

    matrix, count_source = _saved_matrix(manifest, rows)
    requested = len(matrix) if matrix is not None else len(rows)
    order = {_attempt_key(item): index for index, item in enumerate(matrix or [])}
    rows.sort(key=lambda row: order.get(_attempt_key(row), len(order)))
    if staged:
        excluded = sum(_true(row["infrastructure_excluded"]) for row in rows)
        unresolved = 0
    else:
        excluded = sum(row["outcome"] == "Infrastructure Error" for row in rows)
        unresolved = sum(row["outcome"] == "Unresolved" for row in rows)
    summaries = Counter()
    for path in paths:
        sidecar = path.parent / "decision-summary.json"
        if sidecar.is_symlink():
            raise ValueError(f"{sidecar}: decision summaries must not be symlinks")
        summaries[_load(sidecar).get("status", "unknown") if sidecar.is_file() else "missing"] += 1
    outcome_groups = defaultdict(Counter)
    if not staged:
        for row in rows:
            configuration = tuple(row.get(field, "") for field in _CONFIGURATION_FIELDS)
            outcome_groups[configuration][row["outcome"]] += 1
    record = {
        "schema_version": "1.0",
        "run_mode": manifest.get("run_mode", "unknown"),
        "status": status or active.get("status") or manifest.get("status", "unknown"),
        "reason": active.get("reason") or manifest.get("reason"),
        "suite_id": "staged-ot" if staged else manifest.get("suite_id", "default"),
        "requested_count_source": count_source,
        "selected_task_ids": list(dict.fromkeys(item["task_id"] for item in matrix or rows)),
        "requested_attempts": requested,
        "persisted_attempts": len(rows),
        "completed_attempts": len(rows) - excluded,
        "adjudicated_attempts": len(rows) - excluded - unresolved,
        "valid_attempts": len(rows) - excluded - unresolved,
        "excluded_attempts": excluded,
        "unresolved_attempts": unresolved,
        "unstarted_attempts": max(0, requested - len(rows)),
        "decision_summaries": dict(summaries),
        "model_errors": manifest.get("model_errors", {}),
        "attempts": rows,
        "groups": groups,
        **{key: prior[key] for key in _TRANSPORT_DIAGNOSTICS if key in prior},
        "outcomes_by_rubric": [
            {
                **dict(zip(_CONFIGURATION_FIELDS, configuration)),
                "engine_configuration_sha256": hashlib.sha256(
                    configuration[-1].encode("utf-8")
                ).hexdigest(),
                "outcomes": dict(counts),
                "adjudicated_attempts": sum(
                    count
                    for outcome, count in counts.items()
                    if outcome not in {"Infrastructure Error", "Unresolved"}
                ),
            }
            for configuration, counts in sorted(outcome_groups.items())
        ],
    }
    (output / "results.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output / "SCORES.md").write_text(_render(record), encoding="utf-8")
    return record
