"""One-conversation OT episodes with frozen, independently graded checkpoints."""

import hashlib
import json
import math
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .contracts import AttemptLimit, Grade, InfrastructureError, Task
from .decision_summary import api_summary_enabled, collect_api_summary, save_unavailable_summary
from .engine import AgentEngine, engine_metadata
from .reporting import classify
from .runner import SYSTEM_PROMPT, _submission_manifest, suite_hash, utc_now, write_json
from .runtime import Workspace
from .staged_ot import evidence_alignment, grade_stage, stage_observation, staged_ot_tasks


def _observation_hash(observation: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(observation, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _checkpoint(index: int) -> dict:
    return {
        "index": index,
        "reached": False,
        "submitted": False,
        "outcome": "unreached",
        "evidence_alignment": "unassessed",
        "grade": None,
        "artifact_sha256": "",
        "observation_sha256": "",
        "elapsed_seconds": None,
        "rounds": None,
        "rounds_cumulative": None,
        "usage": {},
        "stop_reason": "unreached",
        "provider_refusal": False,
        "submission_path": f"checkpoints/{index:02}/submission",
    }


def _snapshot(
    workspace: Workspace, output: Path, checkpoint: dict, start: float, result: dict, previous: dict
) -> None:
    destination = output / checkpoint["submission_path"]
    destination.mkdir(parents=True, exist_ok=False)
    files = dict(workspace._files())
    source = files.get("solution.sh")
    if source is not None:
        data = source.read_bytes()
        (destination / "solution.sh").write_bytes(data)
        checkpoint["artifact_sha256"] = hashlib.sha256(data).hexdigest()
        checkpoint["submitted"] = True
    checkpoint["elapsed_seconds"] = round(time.monotonic() - start, 3)
    checkpoint["rounds_cumulative"] = result["rounds_used"]
    checkpoint["rounds"] = result["rounds_used"] - previous["rounds"]
    checkpoint["usage"] = {
        key: value - previous["usage"].get(key, 0) for key, value in result["usage"].items()
    }
    previous["rounds"] = result["rounds_used"]
    previous["usage"] = dict(result["usage"])


def _classify_checkpoint(checkpoint: dict) -> str:
    if not checkpoint["reached"]:
        return "unreached"
    return classify(
        {
            "evaluation_mode": "command-generation",
            "rubric_version": "3.0",
            "grade": checkpoint["grade"],
            "stop_reason": checkpoint["stop_reason"],
            "provider_refusal": checkpoint["provider_refusal"],
        }
    )["outcome"]


def _grade_checkpoints(task: Task, output: Path, result: dict) -> None:
    for checkpoint in result["checkpoints"]:
        if not checkpoint["reached"]:
            continue
        index = checkpoint["index"]
        submission = output / checkpoint["submission_path"]
        raw_grade = grade_stage(task, index, submission)
        checkpoint["grade"] = raw_grade.to_dict()
        checkpoint["outcome"] = _classify_checkpoint(checkpoint)
        checkpoint["evidence_alignment"] = evidence_alignment(task, index, raw_grade)


def run_staged_attempt(
    task: Task,
    model: dict,
    provider,
    output: Path,
    repeat: int,
    catalog_hash: str,
    rounds: int = 30,
    seconds: float = 600,
    after_freeze=None,
    *,
    prompt_condition: str = "baseline",
) -> dict:
    """Run one episode; later evidence is revealed only by submit_stage."""
    if task.parameters.get("suite_id") != "staged-ot":
        raise ValueError("run_staged_attempt requires a staged-ot task")
    if prompt_condition != "baseline":
        raise ValueError("Staged OT episodes support the baseline prompt only")
    if rounds < 1 or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Attempt budgets must be positive")
    stage_count = task.parameters["stage_count"]
    if type(stage_count) is not int or stage_count < 1:
        raise ValueError("Staged OT episodes require a positive checkpoint count")
    output.mkdir(parents=True, exist_ok=False)
    secret = getattr(provider, "secret", "")

    def clean(value: str) -> str:
        return value.replace(secret, "[REDACTED]") if secret else value

    def event(kind: str, **fields) -> None:
        with (output / "trace.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                clean(json.dumps({"utc": utc_now(), "event": kind, **fields}, ensure_ascii=False))
                + "\n"
            )

    result = {
        "schema_version": "3.0",
        "evaluation_mode": "staged-command-generation",
        "rubric_version": "3.0",
        "harness_version": __version__,
        "agent_engine": engine_metadata(provider, staged=True),
        "task_id": task.id,
        "suite_id": "staged-ot",
        "task_family": task.family,
        "scenario_id": task.parameters["scenario"],
        "arm": task.parameters["evidence_arm"],
        "task_version": task.version,
        "task_hash": task.digest,
        "stage_count": stage_count,
        "suite_hash": catalog_hash,
        "prompt_condition": "baseline",
        "effective_prompt_sha256": hashlib.sha256(task.prompt.encode("utf-8")).hexdigest(),
        "model": {
            key: model.get(key)
            for key in ("alias", "id", "endpoint", "reasoning", "provider", "upstream_provider")
        },
        "repeat": repeat,
        "started_utc": utc_now(),
        "budget": {"rounds": rounds, "seconds": seconds},
        "generated": False,
        "stop_reason": "complete",
        "provider_refusal": False,
        "final_response": "",
        "usage": {},
        "provider_responses": [],
        "rounds_used": 0,
        "tool_calls": 0,
        "grade": Grade(False).to_dict(),
        "checkpoints": [_checkpoint(index) for index in range(1, stage_count + 1)],
        "trace_path": "trace.jsonl",
    }
    write_json(output / "task.json", asdict(task))
    (output / "effective-prompt.txt").write_text(task.prompt, encoding="utf-8", newline="\n")
    start = time.monotonic()
    current = 1
    last_advance_round = 0
    previous = {"rounds": 0, "usage": {}}
    engine = None
    grading_started = False
    workspace = None

    def reveal(index: int) -> dict[str, str]:
        observation = stage_observation(task, index)
        for path, content in observation.items():
            workspace.write_file(path, content)
        checkpoint = result["checkpoints"][index - 1]
        checkpoint["reached"] = True
        checkpoint["observation_sha256"] = _observation_hash(observation)
        event("observation_revealed", index=index, sha256=checkpoint["observation_sha256"])
        return observation

    def freeze(index: int) -> None:
        checkpoint = result["checkpoints"][index - 1]
        if checkpoint["elapsed_seconds"] is not None:
            raise ValueError("Checkpoint already frozen")
        _snapshot(workspace, output, checkpoint, start, result, previous)
        event(
            "stage_frozen",
            index=index,
            artifact_sha256=checkpoint["artifact_sha256"],
            submitted=checkpoint["submitted"],
        )

    def dispatch(name: str, arguments: str, remaining: float) -> dict:
        nonlocal current, last_advance_round
        try:
            args = json.loads(arguments)
            if not isinstance(args, dict):
                raise TypeError("Tool arguments must be a JSON object")
            if name == "read_file":
                return {"content": workspace.read_file(args["path"], timeout=min(remaining, 30))}
            if name == "write_file":
                if args.get("path") != "solution.sh":
                    raise ValueError("Staged attempts may write only solution.sh")
                if result["rounds_used"] <= last_advance_round:
                    raise ValueError("Wait for the next model turn after an observation reveal")
                if not isinstance(args.get("content"), str):
                    raise ValueError("content must be a string")
                workspace.write_file("solution.sh", args["content"], timeout=min(remaining, 30))
                return {"written": "solution.sh"}
            if name == "submit_stage":
                if args:
                    raise ValueError("submit_stage takes no arguments")
                if current >= stage_count:
                    raise ValueError(f"Use finish after checkpoint {stage_count}")
                if result["rounds_used"] <= last_advance_round:
                    raise ValueError("Wait for the next model turn after an observation reveal")
                try:
                    freeze(current)
                    solution = workspace._target("solution.sh")
                    if solution.exists():
                        solution.unlink()
                    current += 1
                    last_advance_round = result["rounds_used"]
                    observation = reveal(current)
                except (OSError, ValueError) as exc:
                    raise InfrastructureError("Cannot freeze or reveal a checkpoint") from exc
                return {"recorded_observation": observation}
            raise ValueError(f"Unknown tool: {name}")
        except (ValueError, TypeError, KeyError, OSError) as exc:
            return {"tool_error": str(exc)[:2000]}

    try:
        provider.start(task.id)
        workspace = Workspace(output / "workspace")
        reveal(1)
        start = time.monotonic()
        engine = AgentEngine(provider, dispatch, result, event, clean, start + seconds, staged=True)
        try:
            engine.run(task.prompt, SYSTEM_PROMPT, rounds)
        except AttemptLimit as exc:
            detail = str(exc)
            result["stop_reason"] = (
                "context_limit"
                if "context limit" in detail
                else "output_limit"
                if "output token limit" in detail
                else "resource_limit"
                if "memory limit" in detail
                else "time_limit"
            )
        finally:
            result["elapsed_seconds"] = round(time.monotonic() - start, 3)
            if result["elapsed_seconds"] > seconds and result["stop_reason"] == "complete":
                result["stop_reason"] = "time_limit"
            if (
                workspace is not None
                and result["checkpoints"][current - 1]["reached"]
                and result["checkpoints"][current - 1]["elapsed_seconds"] is None
            ):
                freeze(current)
        result["generation_stop_reason"] = result["stop_reason"]
        active = result["checkpoints"][current - 1]
        active["stop_reason"] = result["stop_reason"]
        active["provider_refusal"] = result["provider_refusal"]
        for checkpoint in result["checkpoints"][: current - 1]:
            checkpoint["stop_reason"] = "complete"
        result["episode_status"] = (
            "complete"
            if current == stage_count and result["stop_reason"] == "complete"
            else "early_finish"
            if result["stop_reason"] == "complete"
            else "refusal_stop"
            if result["stop_reason"] == "provider_refusal"
            else "budget_stop"
        )
        final_submission = output / "submission"
        final_submission.mkdir()
        source = output / active["submission_path"] / "solution.sh"
        if source.exists():
            (final_submission / "solution.sh").write_bytes(source.read_bytes())
        if after_freeze is not None or api_summary_enabled(provider):
            engine.active_boundary = "runtime"
            frozen = {
                checkpoint["index"]: _submission_manifest(output / checkpoint["submission_path"])
                for checkpoint in result["checkpoints"]
                if checkpoint["reached"]
            }
            event("submission_frozen", submission_sha256=frozen)
            final_manifest = _submission_manifest(final_submission)
            if after_freeze is not None:
                after_freeze(final_submission)
            else:
                snapshots = {}
                for checkpoint in result["checkpoints"]:
                    artifact = output / checkpoint["submission_path"] / "solution.sh"
                    snapshots[str(checkpoint["index"])] = (
                        {
                            "content": artifact.read_text(encoding="utf-8"),
                            "sha256": checkpoint["artifact_sha256"],
                        }
                        if checkpoint["reached"] and artifact.is_file()
                        else None
                    )
                summary = collect_api_summary(
                    provider,
                    engine,
                    final_submission,
                    artifact_path="solution.sh",
                    submission_sha256=final_manifest,
                    checkpoints=snapshots,
                )
                result["decision_summary"] = {
                    "path": "decision-summary.json",
                    "status": summary["status"],
                    "usage": summary["usage"],
                }
                if summary.get("model_error"):
                    result["model_error"] = summary["model_error"]
            if _submission_manifest(final_submission) != final_manifest:
                raise InfrastructureError("Frozen final submission changed during collection")
            for checkpoint in result["checkpoints"]:
                if (
                    checkpoint["reached"]
                    and _submission_manifest(output / checkpoint["submission_path"])
                    != frozen[checkpoint["index"]]
                ):
                    raise InfrastructureError(
                        "Frozen checkpoint changed during post-submission collection"
                    )
            event("post_submission_collection_finished", submission_sha256=frozen)
        grading_started = True
        _grade_checkpoints(task, output, result)
    except InfrastructureError as exc:
        result["stop_reason"] = (
            "grading_error"
            if grading_started
            else "runtime_error"
            if engine and engine.active_boundary == "runtime"
            else "provider_error"
        )
        result["grade"] = Grade(False, infrastructure_error=clean(str(exc))).to_dict()
    except (OSError, ValueError) as exc:
        result["stop_reason"] = "grading_error" if grading_started else "runtime_error"
        result["grade"] = Grade(False, infrastructure_error=clean(str(exc))).to_dict()
    except KeyboardInterrupt:
        result["stop_reason"] = "interrupted"
        result["grade"] = Grade(
            False, infrastructure_error="Operator cancelled the attempt"
        ).to_dict()
        raise
    finally:
        if (
            api_summary_enabled(provider)
            and "decision_summary" not in result
            and not (output / "decision-summary.json").exists()
        ):
            summary = save_unavailable_summary(
                output, "Generation ended before summary collection", secret=secret
            )
            result["decision_summary"] = {
                "path": "decision-summary.json",
                "status": summary["status"],
                "usage": {},
            }
        result.setdefault("elapsed_seconds", round(time.monotonic() - start, 3))
        infrastructure_error = result["grade"].get("infrastructure_error")
        if infrastructure_error:
            for checkpoint in result["checkpoints"]:
                if checkpoint["reached"] and checkpoint["grade"] is None:
                    checkpoint["grade"] = Grade(
                        False, infrastructure_error=infrastructure_error
                    ).to_dict()
                    checkpoint["outcome"] = "Infrastructure Error"
                    checkpoint["stop_reason"] = result["stop_reason"]
        result["finished_utc"] = utc_now()
        event(
            "attempt_finished", stop_reason=result["stop_reason"], checkpoints=result["checkpoints"]
        )
        write_json(output / "attempt.json", result)
        write_json(
            output / "review-template.json",
            {
                "checkpoints": {
                    str(index): {
                        "reviewer": "",
                        "second_reviewer": None,
                        "disposition": "unclear",
                        "blocking_refusal": False,
                        "adjudication": "unresolved",
                        "notes": "",
                    }
                    for index in range(1, stage_count + 1)
                }
            },
        )
    return result


def grade_staged_attempt(directory: Path, result: dict) -> dict:
    if result.get("suite_id") != "staged-ot":
        raise ValueError("Saved attempt is not a staged-ot episode")
    task = next((task for task in staged_ot_tasks() if task.id == result["task_id"]), None)
    if task is None or task.digest != result["task_hash"]:
        raise ValueError("Task contract changed: use the original benchmark version")
    if result.get("prompt_condition") != "baseline":
        raise ValueError("Staged episodes support the baseline prompt only")
    checkpoints = result.get("checkpoints")
    stage_count = task.parameters["stage_count"]
    if result.get("stage_count", stage_count) != stage_count:
        raise ValueError("Saved checkpoint count changed since the episode was run")
    if not isinstance(checkpoints, list) or len(checkpoints) != stage_count:
        raise ValueError(f"Saved episode must have exactly {stage_count} checkpoints")
    for index, checkpoint in enumerate(checkpoints, 1):
        if (
            not isinstance(checkpoint, dict)
            or checkpoint.get("index") != index
            or checkpoint.get("submission_path") != f"checkpoints/{index:02}/submission"
        ):
            raise ValueError("Saved checkpoint index or submission path changed")
        if checkpoint.get("reached") and any(
            not previous.get("reached") for previous in checkpoints[: index - 1]
        ):
            raise ValueError("Saved checkpoint reach order is invalid")
    prompt = task.prompt.encode("utf-8")
    if (
        result.get("effective_prompt_sha256") != hashlib.sha256(prompt).hexdigest()
        or (directory / "effective-prompt.txt").read_bytes() != prompt
    ):
        raise ValueError("Saved prompt changed since the episode was run")
    for checkpoint in checkpoints:
        if not checkpoint["reached"]:
            continue
        expected_observation = _observation_hash(stage_observation(task, checkpoint["index"]))
        if checkpoint["observation_sha256"] != expected_observation:
            raise ValueError("Checkpoint observation changed: use the original benchmark version")
        submission = directory / checkpoint["submission_path"]
        frozen = _submission_manifest(submission)
        expected_files = {"solution.sh"} if checkpoint.get("submitted") else set()
        if set(frozen) != expected_files:
            raise ValueError("Frozen checkpoint file set changed since the episode was run")
        if frozen.get("solution.sh", "") != checkpoint["artifact_sha256"]:
            raise ValueError("Frozen checkpoint artifact changed since the episode was run")
    previous = [checkpoint.get("grade") for checkpoint in result["checkpoints"]]
    _grade_checkpoints(task, directory, result)
    if result["stop_reason"] == "grading_error" and not any(
        (checkpoint.get("grade") or {}).get("infrastructure_error")
        for checkpoint in result["checkpoints"]
        if checkpoint["reached"]
    ):
        result["stop_reason"] = result.get("generation_stop_reason", "complete")
        result["grade"] = Grade(False).to_dict()
    current_hash = suite_hash(staged_ot_tasks())
    history = directory / "grade-history"
    history.mkdir(exist_ok=True)
    write_json(
        history / f"{uuid.uuid4().hex}.json",
        {
            "utc": utc_now(),
            "previous": previous,
            "new": [checkpoint.get("grade") for checkpoint in result["checkpoints"]],
            "previous_provenance": {
                "suite_hash": result.get("grading_suite_hash") or result["suite_hash"]
            },
            "new_provenance": {"suite_hash": current_hash},
        },
    )
    result["grading_suite_hash"] = current_hash
    write_json(directory / "attempt.json", result)
    return result
