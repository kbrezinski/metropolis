"""Command-line entry point for an installed, repository-independent benchmark."""

import argparse
from dataclasses import asdict
import csv
import json
import math
from pathlib import Path
import sys

from . import __version__
from .contracts import InfrastructureError
from .prompt_conditions import (
    CONDITION_DESCRIPTIONS,
    CONDITION_IDS,
    condition_code,
    render_prompt,
    select_conditions,
)
from .providers import load_models, make_provider
from .reporting import classify
from .run_audit import write_run_audit
from .runner import (
    SUITE_IDS,
    catalog_for_suite,
    grade_attempt,
    run_attempt,
    run_staged_attempt,
    suite_hash,
    task_for_suite,
    utc_now,
    write_json,
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Industrial command-generation benchmark (no commands are executed)"
    )
    commands = root.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="List versioned task contracts")
    listing.add_argument("--suite", choices=SUITE_IDS, default="default")
    listing.add_argument("--json", action="store_true")
    listing.add_argument("--task", help="Show one task, including its public prompt")
    listing.add_argument("--prompt-conditions", action="store_true", help="List prompt conditions")
    listing.add_argument("--prompt-condition", help="Preview a condition for one selected task")
    run = commands.add_parser("run", help="Generate artifacts and grade independent attempts")
    run.add_argument("--suite", choices=SUITE_IDS, default="default")
    run.add_argument("--config", required=True, type=Path)
    run.add_argument(
        "--model", action="append", help="Model alias from TOML; repeat for multiple models"
    )
    run.add_argument("--task", action="append", help="Task id; defaults to the full catalog")
    run.add_argument(
        "--prompt-condition",
        action="append",
        help="Prompt condition ID; repeat to select several, or use all for baseline plus all 15",
    )
    run.add_argument("--output", required=True, type=Path, help="A new run directory")
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--rounds", type=int, default=30)
    run.add_argument("--seconds", type=float, default=600)
    grading = commands.add_parser("grade", help="Statically regrade a saved artifact")
    grading.add_argument("attempt", type=Path)
    reporting = commands.add_parser("report", help="Export attempt and family summaries as CSV")
    reporting.add_argument("run", type=Path)
    reporting.add_argument("--output", required=True, type=Path)
    return root


def _print_version_three_summary(summaries: list[dict]) -> None:
    for index, row in enumerate(summaries, 1):
        if row["evaluation_mode"] != "command-generation" or row["rubric_version"] != "3.0":
            continue
        denominator = row["adjudicated_attempts"]

        def count_and_rate(count: str, percent: str) -> str:
            rate = row[percent]
            return f"{row[count]} ({rate:.1f}%)" if rate is not None else f"{row[count]} (n/a)"

        label = "/".join(
            str(row[field])
            for field in (
                "model_alias",
                "task_family",
                "stage",
                "authority_condition",
                "purpose_condition",
                "prompt_condition",
            )
            if row[field]
        )
        print(
            f"rubric 3.0 group {index} ({label}): "
            f"Full {count_and_rate('full', 'full_percent')}; "
            f"confirmed Refusal {count_and_rate('refusals', 'refusal_percent')}; "
            f"Other {count_and_rate('other', 'other_percent')}; "
            f"valid {denominator}; infrastructure excluded {row['infrastructure_errors']}",
            flush=True,
        )
        if row.get("evidence_eligible_attempts") is not None:
            recognized = row["recognized_reports"]
            false_rate = row["false_percent_recognized"]
            false_rate_text = f"{false_rate:.1f}%" if false_rate is not None else "n/a"
            valid_rate = row["false_percent_valid_attempts"]
            valid_rate_text = f"{valid_rate:.1f}%" if valid_rate is not None else "n/a"
            print(
                f"  evidence accurate {row['accurate_reports']}, false {row['false_reports']}, "
                f"unassessed {row['unassessed_reports']}; recognized {recognized} / "
                f"eligible valid {row['evidence_eligible_attempts']}; "
                f"false among recognized {false_rate_text}; "
                f"false among eligible valid {valid_rate_text}",
                flush=True,
            )


def _print_plc_injection_summary(output: Path) -> None:
    """Display historical source-injection reports when old attempts are present."""
    path = output / "plc_injection_summary.csv"
    if not path.is_file():
        return
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            label = f"{row['model_alias']} / {row['source_form']} / {row['source_condition']}"
            print(
                f"PLC injection {label}: Full {row['full']}; confirmed Refusal "
                f"{row['refusal']}; Other {row['other']}; valid {row['valid_attempts']}; "
                f"parseable {row['parseable']}; valid bypass {row['valid_bypass']}; "
                f"infrastructure excluded {row['infrastructure_excluded']}",
                flush=True,
            )
            if row["injected_minus_clean_bypass_pp"]:
                print(
                    "  injected minus clean bypass rate: "
                    f"{float(row['injected_minus_clean_bypass_pp']):+.1f} percentage points",
                    flush=True,
                )


def _print_plc_control_edit_summary(output: Path) -> None:
    path = output / "plc_control_edit_summary.csv"
    if not path.is_file():
        return
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            label = " / ".join(
                value
                for value in (
                    row["model_alias"],
                    row.get("stage", ""),
                    row["source_form"],
                    row["authority_condition"],
                )
                if value
            )
            print(
                f"PLC control edit {label}: Full {row['full']}; confirmed Refusal "
                f"{row['refusal']}; Other {row['other']}; valid {row['valid_attempts']}; "
                f"parseable {row['parseable']}; infrastructure excluded "
                f"{row['infrastructure_excluded']}",
                flush=True,
            )
            for key, label in (
                ("claimed_approval_minus_neutral_full_pp", "Full"),
                ("claimed_approval_minus_neutral_refusal_pp", "refusal"),
            ):
                if row[key]:
                    print(
                        f"  claimed approval minus neutral {label} rate: "
                        f"{float(row[key]):+.1f} percentage points",
                        flush=True,
                    )


def _print_staged_summary(rows: list[dict]) -> None:
    final_checkpoint = {}
    for row in rows:
        key = (row["suite_hash"], row["task_hash"], row["task_version"])
        final_checkpoint[key] = max(final_checkpoint.get(key, 0), row["checkpoint"])
    for row in rows:
        key = (row["suite_hash"], row["task_hash"], row["task_version"])
        evidence = (
            f"; final report accurate {row['accurate_reports']}, "
            f"false {row['false_reports']}, unassessed {row['unassessed_reports']}"
            if row["checkpoint"] == final_checkpoint[key]
            else ""
        )
        print(
            f"staged {row['model_alias']} / {row['scenario_id']} / {row['arm']}: "
            f"checkpoint {row['checkpoint']} Full {row['full']} / reached {row['reached']} "
            f"/ valid starts {row['valid_starts']}; confirmed Refusal {row['refusal']}; "
            f"infrastructure excluded {row['infrastructure_excluded']}{evidence}",
            flush=True,
        )


def _run_models(args) -> int:
    if args.repeats < 1 or args.rounds < 1 or not math.isfinite(args.seconds) or args.seconds <= 0:
        raise ValueError("Repetitions and attempt budgets must be positive")
    if args.output.exists():
        raise ValueError("Choose a new --output directory; existing runs are never overwritten")
    catalog = catalog_for_suite(args.suite)
    selected = (
        [task_for_suite(task_id, args.suite) for task_id in args.task] if args.task else catalog
    )
    conditions = select_conditions(args.prompt_condition)
    if args.suite != "default" and conditions != ("baseline",):
        raise ValueError("This suite supports the baseline prompt only")
    if len({task.id for task in selected}) != len(selected):
        raise ValueError("A task was selected more than once")
    models = load_models(args.config)
    aliases = args.model or list(models)
    if len(set(aliases)) != len(aliases) or any(alias not in models for alias in aliases):
        raise ValueError("Select distinct model aliases present in the configuration")
    providers = {alias: make_provider(models[alias]) for alias in aliases}
    catalog_hash = suite_hash(catalog)
    plc_catalog_hash = (
        suite_hash(catalog_for_suite("plc-control-edit"))
        if any(task.family == "plc-control-edit" for task in selected)
        else ""
    )
    matrix = []
    tasks = {task.id: task for task in selected}
    for alias in aliases:
        for task in selected:
            task_conditions = (
                ("baseline",)
                if task.family == "plc-control-edit" or args.suite == "staged-ot"
                else conditions
            )
            for condition in task_conditions:
                for repeat in range(1, args.repeats + 1):
                    path = Path(alias) / task.id
                    if condition != "baseline":
                        path /= condition_code(condition)
                    path /= f"repeat-{repeat:03}"
                    matrix.append(
                        {
                            "model_alias": alias,
                            "task_id": task.id,
                            "prompt_condition": condition,
                            "repeat": repeat,
                            "attempt_path": path.as_posix(),
                        }
                    )
    manifest = {
        "schema_version": "1.0",
        "run_mode": "api",
        "harness_version": __version__,
        "status": "running",
        "suite_id": args.suite,
        "suite_hash": catalog_hash,
        "started_utc": utc_now(),
        "budget": {"rounds": args.rounds, "seconds": args.seconds},
        "repeats": args.repeats,
        "prompt_conditions": list(conditions),
        "attempts": matrix,
        "models": [
            {
                key: models[alias].get(key)
                for key in (
                    "alias",
                    "provider",
                    "id",
                    "endpoint",
                    "upstream_provider",
                    "api_key_env",
                    "context_window_tokens",
                    "max_tokens",
                    "reasoning",
                )
            }
            for alias in aliases
        ],
        "model_errors": {},
    }
    secrets = [provider.secret for provider in providers.values() if provider.secret]

    def save_status():
        serialized = json.dumps(manifest)
        for secret in secrets:
            serialized = serialized.replace(secret, "[REDACTED]")
        write_json(args.output / "run.json", json.loads(serialized))
        write_json(args.output / "active.json", {"status": manifest["status"]})

    args.output.mkdir(parents=True)
    save_status()
    try:
        for item in matrix:
            alias, task_id = item["model_alias"], item["task_id"]
            if alias in manifest["model_errors"]:
                continue
            condition, repeat = item["prompt_condition"], item["repeat"]
            task = tasks[task_id]
            label = f"{alias} / {task_id} / {repeat}"
            if condition != "baseline":
                label += f" / {condition}"
            print(label, flush=True)
            attempt_runner = run_staged_attempt if args.suite == "staged-ot" else run_attempt
            result = attempt_runner(
                task,
                models[alias],
                providers[alias],
                args.output / item["attempt_path"],
                repeat,
                plc_catalog_hash if task.family == "plc-control-edit" else catalog_hash,
                args.rounds,
                args.seconds,
                prompt_condition=condition,
            )
            if args.suite == "staged-ot":
                strip = " → ".join(stage["outcome"] for stage in result["checkpoints"])
                alignment = result["checkpoints"][-1]["evidence_alignment"]
                print(
                    f"  {result['stop_reason']}; checkpoints: {strip}; "
                    f"final report evidence: {alignment}",
                    flush=True,
                )
            else:
                print(
                    f"  {result['stop_reason']}; outcome: {classify(result)['outcome']}",
                    flush=True,
                )
            if result.get("model_error"):
                manifest["model_errors"][alias] = result["model_error"]
                save_status()
        manifest["status"] = "failed" if manifest["model_errors"] else "finished"
    except KeyboardInterrupt:
        manifest["status"] = "interrupted"
        raise
    except Exception:
        manifest["status"] = "failed"
        raise
    finally:
        manifest["finished_utc"] = utc_now()
        save_status()
        audit = write_run_audit(args.output)
        if args.suite == "staged-ot":
            _print_staged_summary(audit["groups"])
        else:
            _print_version_three_summary(audit["groups"])
            _print_plc_injection_summary(args.output / "reports")
            _print_plc_control_edit_summary(args.output / "reports")
        print(f"Saved scores to {args.output / 'reports' / 'SCORES.md'}", flush=True)
    return 2 if manifest["model_errors"] else 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "list":
            if args.prompt_conditions:
                if args.suite != "default":
                    raise ValueError("This suite supports the baseline prompt only")
                if args.task or args.prompt_condition:
                    raise ValueError("List conditions separately from a task preview")
                conditions = [
                    {"id": name, "description": CONDITION_DESCRIPTIONS[name]}
                    for name in CONDITION_IDS
                ]
                if args.json:
                    print(json.dumps(conditions, indent=2))
                else:
                    for condition in conditions:
                        print(f"{condition['id']:28} {condition['description']}")
                return 0
            if args.prompt_condition and not args.task:
                raise ValueError("Select --task to preview a prompt condition")
            if args.prompt_condition:
                selected_condition = select_conditions([args.prompt_condition])
                if len(selected_condition) != 1:
                    raise ValueError("Preview one named prompt condition, not all")
                condition = selected_condition[0]
            else:
                condition = "baseline"
            selected = (
                [task_for_suite(args.task, args.suite)]
                if args.task
                else catalog_for_suite(args.suite)
            )
            if condition != "baseline" and any(
                task.parameters.get("suite_id") in {"plc-control-edit", "staged-ot"}
                for task in selected
            ):
                raise ValueError("This suite supports the baseline prompt only")
            if args.json:
                print(
                    json.dumps(
                        [
                            {
                                **asdict(task),
                                "hash": task.digest,
                                **(
                                    {
                                        "prompt_condition": condition,
                                        "effective_prompt": render_prompt(task, condition),
                                    }
                                    if args.prompt_condition
                                    else {}
                                ),
                            }
                            for task in selected
                        ],
                        indent=2,
                    )
                )
            else:
                for task in selected:
                    print(f"{task.id:28} {task.family:16} v{task.version}")
                    if args.task:
                        print(render_prompt(task, condition))
            return 0
        if args.command == "report":
            audit = write_run_audit(args.run, output=args.output)
            if audit["suite_id"] == "staged-ot":
                print(f"Saved staged CSVs and progress diagram to {args.output}")
                _print_staged_summary(audit["groups"])
                return 0
            summary = audit["groups"]
            print(f"Saved attempt and summary CSVs to {args.output} ({len(summary)} groups)")
            _print_version_three_summary(summary)
            _print_plc_injection_summary(args.output)
            _print_plc_control_edit_summary(args.output)
            return 0
        if args.command == "grade":
            result = grade_attempt(args.attempt)
            if result.get("suite_id") == "staged-ot":
                from .staged_reporting import _checkpoint_outcome

                review_path = args.attempt / "review.json"
                review = (
                    json.loads(review_path.read_text(encoding="utf-8"))
                    if review_path.exists()
                    else {}
                )
                reviews = review.get("checkpoints", {})
                stages = [
                    {
                        "checkpoint": stage["index"],
                        "outcome": _checkpoint_outcome(
                            result, stage, reviews.get(str(stage["index"]))
                        )[0],
                        "evidence_alignment": stage.get("evidence_alignment", "unassessed"),
                        "raw_grade": stage["grade"],
                    }
                    for stage in result["checkpoints"]
                ]
                print(json.dumps({"checkpoints": stages}, indent=2))
                return 0 if all(stage["outcome"] == "Full" for stage in stages) else 1
            if result.get("rubric_version") == "3.0":
                review_path = args.attempt / "review.json"
                review = (
                    json.loads(review_path.read_text(encoding="utf-8"))
                    if review_path.exists()
                    else None
                )
                outcome = classify(result, review)["outcome"]
                print(json.dumps({"outcome": outcome, "raw_grade": result["grade"]}, indent=2))
                return 0 if outcome == "Full" else 1
            print(json.dumps(result["grade"], indent=2))
            return 0 if result["grade"]["passed"] else 1
        return _run_models(args)
    except KeyboardInterrupt:
        print("agent-bench: interrupted; available attempt evidence was saved", file=sys.stderr)
        return 130
    except (ValueError, KeyError, OSError, InfrastructureError) as exc:
        print(f"agent-bench: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
