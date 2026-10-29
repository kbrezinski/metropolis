"""Command-line entry point for an installed, repository-independent benchmark."""

import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import sys

from .contracts import InfrastructureError
from .providers import load_models, make_provider
from .reporting import report
from .runner import grade_attempt, run_attempt, suite_hash
from .tasks import get_task, tasks


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Industrial command-generation benchmark (no commands are executed)"
    )
    commands = root.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="List versioned task contracts")
    listing.add_argument("--json", action="store_true")
    listing.add_argument("--task", help="Show one task, including its public prompt")
    run = commands.add_parser("run", help="Generate artifacts and grade independent attempts")
    run.add_argument("--config", required=True, type=Path)
    run.add_argument(
        "--model", action="append", help="Model alias from TOML; repeat for multiple models"
    )
    run.add_argument("--task", action="append", help="Task id; defaults to the complete pilot")
    run.add_argument("--output", required=True, type=Path, help="A new run directory")
    run.add_argument("--repeats", type=int, default=3)
    run.add_argument("--rounds", type=int, default=30)
    run.add_argument("--seconds", type=float, default=600)
    grading = commands.add_parser("grade", help="Statically regrade a saved command submission")
    grading.add_argument("attempt", type=Path)
    reporting = commands.add_parser("report", help="Export attempt and family summaries as CSV")
    reporting.add_argument("run", type=Path)
    reporting.add_argument("--output", required=True, type=Path)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "list":
            selected = [get_task(args.task)] if args.task else tasks()
            if args.json:
                print(
                    json.dumps(
                        [{**asdict(task), "hash": task.digest} for task in selected], indent=2
                    )
                )
            else:
                for task in selected:
                    print(f"{task.id:28} {task.family:16} v{task.version}")
                    if args.task:
                        print(task.prompt)
            return 0
        if args.command == "report":
            summary = report(args.run, args.output)
            print(f"Saved attempt and summary CSVs to {args.output} ({len(summary)} groups)")
            return 0
        if args.command == "grade":
            result = grade_attempt(args.attempt)
            print(json.dumps(result["grade"], indent=2))
            return 0 if result["grade"]["passed"] else 1
        if (
            args.repeats < 1
            or args.rounds < 1
            or not math.isfinite(args.seconds)
            or args.seconds <= 0
        ):
            raise ValueError("Repetitions and attempt budgets must be positive")
        if args.output.exists():
            raise ValueError("Choose a new --output directory; existing runs are never overwritten")
        catalog = tasks()
        selected = [get_task(task_id) for task_id in args.task] if args.task else catalog
        if len({task.id for task in selected}) != len(selected):
            raise ValueError("A task was selected more than once")
        models = load_models(args.config)
        aliases = args.model or list(models)
        if len(set(aliases)) != len(aliases) or any(alias not in models for alias in aliases):
            raise ValueError("Select distinct model aliases present in the configuration")
        providers = {alias: make_provider(models[alias]) for alias in aliases}
        catalog_hash = suite_hash(catalog)
        args.output.mkdir(parents=True)
        for alias in aliases:
            for task in selected:
                for repeat in range(1, args.repeats + 1):
                    directory = args.output / alias / task.id / f"repeat-{repeat:03}"
                    print(f"{alias} / {task.id} / {repeat}", flush=True)
                    result = run_attempt(
                        task,
                        models[alias],
                        providers[alias],
                        directory,
                        repeat,
                        catalog_hash,
                        args.rounds,
                        args.seconds,
                    )
                    print(
                        f"  {result['stop_reason']}; grade: {result['grade']['outcome']}",
                        flush=True,
                    )
        report(args.output, args.output / "reports")
        return 0
    except KeyboardInterrupt:
        print("agent-bench: interrupted; available attempt evidence was saved", file=sys.stderr)
        return 130
    except (ValueError, KeyError, OSError, InfrastructureError) as exc:
        print(f"agent-bench: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
