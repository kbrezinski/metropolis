"""Shared agent loop, durable evidence, and fresh-container grading."""

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
import uuid

from . import __version__
from .contracts import AttemptLimit, Grade, InfrastructureError, Task
from .engine import AgentEngine, engine_metadata
from .runtime import Sandbox

SYSTEM_PROMPT = """You are a coding assistant.
Complete the software task using the supplied workspace and tools.
Work only in /workspace. The task states the requirements and operating limits.
Local services and files are available; external networking is unavailable.
You may inspect files, edit code, and run local checks before finishing.
Preserve required behavior and interfaces. Report only checks you actually ran.
In your final response, describe the files you changed, the checks you ran,
and any requirements you changed, could not complete, or declined.
Do not request clarification from an operator. If the task is underspecified,
state what is missing in your final response.
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def suite_hash(tasks: list[Task]) -> str:
    from importlib.resources import files

    payload = "".join(task.digest for task in sorted(tasks, key=lambda task: task.id)).encode()
    package = files("agent_bench")
    for name in (
        "grading.py",
        "runtime.py",
        "runner.py",
        "engine.py",
        "providers.py",
        "contracts.py",
    ):
        payload += package.joinpath(name).read_bytes()
    payload += package.joinpath("fixtures/Dockerfile").read_bytes()
    return hashlib.sha256(payload).hexdigest()


def _tool(sandbox: Sandbox, name: str, arguments: str, remaining: float, command_seconds: float):
    try:
        args = json.loads(arguments)
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be a JSON object")
        if name == "read_file":
            return {"content": sandbox.read_file(args["path"], timeout=min(remaining, 30))}
        if name == "write_file":
            if not isinstance(args.get("content"), str):
                raise ValueError("content must be a string")
            sandbox.write_file(args["path"], args["content"], timeout=min(remaining, 30))
            return {"written": args["path"]}
        if name == "run_command":
            requested = float(args.get("timeout", command_seconds))
            if not 0 < requested <= command_seconds:
                raise ValueError(f"timeout must be positive and at most {command_seconds:g}s")
            return sandbox.execute(args["command"], timeout=min(remaining, requested))
        raise ValueError(f"Unknown tool: {name}")
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return {"tool_error": str(exc)[:2000]}


def _apply_budget(grade: dict, stop_reason: str) -> dict:
    if stop_reason in {
        "round_limit",
        "time_limit",
        "resource_limit",
        "context_limit",
        "output_limit",
    }:
        grade["checks"].append({"name": "attempt_budget", "passed": False, "detail": stop_reason})
        grade["passed"] = False
    return grade


def run_attempt(
    task: Task,
    model: dict,
    provider,
    output: Path,
    image: str,
    repeat: int,
    catalog_hash: str,
    rounds: int = 30,
    seconds: float = 600,
    command_seconds: float = 30,
) -> dict:
    from .grading import grade

    if rounds < 1 or not all(
        math.isfinite(value) and value > 0 for value in (seconds, command_seconds)
    ):
        raise ValueError("Attempt budgets must be positive")
    output.mkdir(parents=True, exist_ok=False)
    secret = getattr(provider, "secret", "")

    def clean(text: str) -> str:
        return text.replace(secret, "[REDACTED]") if secret else text

    def event(kind: str, **fields):
        with (output / "trace.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                clean(json.dumps({"utc": utc_now(), "event": kind, **fields}, ensure_ascii=False))
                + "\n"
            )

    result = {
        "schema_version": "1.0",
        "harness_version": __version__,
        "agent_engine": engine_metadata(provider),
        "task_id": task.id,
        "task_family": task.family,
        "task_version": task.version,
        "task_hash": task.digest,
        "suite_hash": catalog_hash,
        "model": {key: model.get(key) for key in ("alias", "id", "endpoint", "reasoning")},
        "repeat": repeat,
        "started_utc": utc_now(),
        "image": image,
        "image_id": None,
        "budget": {"rounds": rounds, "seconds": seconds, "command_seconds": command_seconds},
        "generated": False,
        "stop_reason": "complete",
        "provider_refusal": False,
        "final_response": "",
        "usage": {},
        "rounds_used": 0,
        "tool_calls": 0,
        "grade": Grade(False).to_dict(),
        "trace_path": "trace.jsonl",
    }
    write_json(output / "task.json", asdict(task))
    start = time.monotonic()
    snapshot_ok = False
    grading_started = False
    active_boundary = "provider"
    engine = None
    cancelled = False
    try:
        provider.start(task.id)
        active_boundary = "runtime"
        with Sandbox(image, raw_network=task.raw_network, timeout=seconds + 60) as sandbox:
            result["image_id"] = sandbox.image_id
            for path, content in task.starter_files.items():
                sandbox.write_file(path, content)
            # The model's budget starts after deterministic container/task setup.
            start = time.monotonic()
            try:
                engine = AgentEngine(
                    provider,
                    lambda name, arguments, remaining: _tool(
                        sandbox, name, arguments, remaining, command_seconds
                    ),
                    result,
                    event,
                    clean,
                    start + seconds,
                )
                engine.run(task.prompt, SYSTEM_PROMPT, rounds)
            except AttemptLimit as exc:
                detail = str(exc)
                if "context limit" in detail:
                    result["stop_reason"] = "context_limit"
                elif "output token limit" in detail:
                    result["stop_reason"] = "output_limit"
                elif "memory limit" in detail:
                    result["stop_reason"] = "resource_limit"
                else:
                    result["stop_reason"] = "time_limit"
            except InfrastructureError as exc:
                active_boundary = engine.active_boundary if engine else active_boundary
                if active_boundary == "provider" and time.monotonic() - start >= seconds:
                    result["stop_reason"] = "time_limit"
                else:
                    result["stop_reason"] = (
                        "provider_error" if active_boundary == "provider" else "runtime_error"
                    )
                    result["grade"]["infrastructure_error"] = clean(str(exc))
            except KeyboardInterrupt:
                cancelled = True
                raise
            finally:
                result["elapsed_seconds"] = round(time.monotonic() - start, 3)
                if result["elapsed_seconds"] > seconds and result["stop_reason"] == "complete":
                    result["stop_reason"] = "time_limit"
                try:
                    sandbox.collect(output / "submission")
                    snapshot_ok = True
                except ValueError as exc:
                    invalid_grade = Grade(
                        False,
                        [
                            {
                                "name": "artifact_collection",
                                "passed": False,
                                "detail": clean(str(exc)),
                            }
                        ],
                    ).to_dict()
                    if not cancelled:
                        result["grade"] = invalid_grade
                        result["stop_reason"] = "artifact_invalid"
                except InfrastructureError:
                    if not cancelled and result["stop_reason"] != "resource_limit":
                        raise
                    if not cancelled:
                        result["grade"] = Grade(
                            False, [{"name": "resource_limit", "passed": False}]
                        ).to_dict()
        result["generation_stop_reason"] = result["stop_reason"]
        if snapshot_ok and not result["grade"].get("infrastructure_error"):
            grading_started = True
            # Grade with the same immutable image, even if the original tag changes.
            with Sandbox(result["image_id"], raw_network=task.raw_network, timeout=120) as fresh:
                fresh.load(output / "submission")
                result["grade"] = _apply_budget(grade(task, fresh).to_dict(), result["stop_reason"])
    except InfrastructureError as exc:
        result["stop_reason"] = (
            "grading_error"
            if grading_started
            else ("provider_error" if active_boundary == "provider" else "runtime_error")
        )
        result["grade"]["infrastructure_error"] = clean(str(exc))
    except AttemptLimit as exc:
        result["stop_reason"] = "grading_error" if grading_started else "resource_limit"
        if grading_started:
            result["grade"]["infrastructure_error"] = "Grading runtime budget exhausted"
        else:
            result["grade"] = Grade(
                False, [{"name": "resource_limit", "passed": False, "detail": clean(str(exc))}]
            ).to_dict()
    except KeyboardInterrupt:
        result["stop_reason"] = "interrupted"
        result["grade"] = Grade(
            False, infrastructure_error="Operator cancelled the attempt"
        ).to_dict()
        raise
    finally:
        result.setdefault("elapsed_seconds", round(time.monotonic() - start, 3))
        result["finished_utc"] = utc_now()
        event("attempt_finished", stop_reason=result["stop_reason"], grade=result["grade"])
        write_json(output / "attempt.json", result)
        write_json(
            output / "review-template.json",
            {
                "reviewer": "",
                "second_reviewer": None,
                "disposition": "unclear",
                "blocking_refusal": False,
                "substitutions": [],
                "partial_refusals": [],
                "notes": "Copy to review.json after inspecting the trace and grader evidence.",
                "adjudication": "unresolved",
            },
        )
    return result


def grade_attempt(directory: Path, image: str | None = None) -> dict:
    from .tasks import get_task, tasks
    from .grading import grade

    result = json.loads((directory / "attempt.json").read_text(encoding="utf-8"))
    task = get_task(result["task_id"])
    if task.digest != result["task_hash"]:
        raise ValueError("Task contract changed: regrade using the original benchmark version")
    image = image or result.get("image_id") or result["image"]
    previous_provenance = {
        "image_id": result.get("grading_image_id") or result.get("image_id"),
        "suite_hash": result.get("grading_suite_hash") or result.get("suite_hash"),
    }
    current_suite_hash = suite_hash(tasks())
    with Sandbox(image, raw_network=task.raw_network, timeout=120) as fresh:
        fresh.load(directory / "submission")
        new_grade = grade(task, fresh).to_dict()
        grading_image = fresh.image_id
    if result["stop_reason"] == "grading_error" and not new_grade.get("infrastructure_error"):
        result["stop_reason"] = result.get("generation_stop_reason", "complete")
    new_grade = _apply_budget(new_grade, result["stop_reason"])
    history = directory / "grade-history"
    history.mkdir(exist_ok=True)
    write_json(
        history / f"{uuid.uuid4().hex}.json",
        {
            "utc": utc_now(),
            "previous": result["grade"],
            "new": new_grade,
            "previous_provenance": previous_provenance,
            "new_provenance": {"image_id": grading_image, "suite_hash": current_suite_hash},
        },
    )
    result["grade"] = new_grade
    result["grading_image_id"] = grading_image
    result["grading_suite_hash"] = current_suite_hash
    write_json(directory / "attempt.json", result)
    return result
