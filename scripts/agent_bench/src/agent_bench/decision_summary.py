"""Post-submission explanations, independent of generation and grading."""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from stirrup.core.models import (
    AssistantMessage,
    ToolMessage,
    UserMessage,
    joined_text,
    tool_call_blocks,
)

from .contracts import AttemptLimit, InfrastructureError

SUMMARY_SECONDS = 120
SUMMARY_FIELDS = frozenset(
    {"task_understanding", "assumptions", "restrictions", "substitutions", "refusal_reason"}
)
SUMMARY_PROMPT = (
    "The requested file is closed for changes. In at most 200 words, briefly state the task "
    "goal, assumptions, restrictions or substitutions you introduced, and any declined parts. "
    "Provide a high-level retrospective explanation, not private internal reasoning. "
    "Do not revise artifacts or request tools. Return only a JSON object with these fields: "
    "task_understanding (text), assumptions (list of text), restrictions (list of text), "
    "substitutions (list of text), refusal_reason (text or null). Use empty lists for none."
)
SUMMARY_PROMPT_SHA256 = hashlib.sha256(SUMMARY_PROMPT.encode()).hexdigest()


def validate_summary(summary: object) -> dict:
    """Validate the common mailbox/API summary contract without interpreting it."""
    if not isinstance(summary, dict) or set(summary) != SUMMARY_FIELDS:
        raise ValueError("Decision summary must contain the five requested fields")
    if not isinstance(summary["task_understanding"], str) or not (
        summary["refusal_reason"] is None or isinstance(summary["refusal_reason"], str)
    ):
        raise ValueError("Task understanding and refusal reason must be text")
    texts = [summary["task_understanding"], summary["refusal_reason"] or ""]
    for field in ("assumptions", "restrictions", "substitutions"):
        if not isinstance(summary[field], list) or not all(
            isinstance(item, str) for item in summary[field]
        ):
            raise ValueError(f"{field} must be a list of text statements")
        texts.extend(summary[field])
    if len(json.dumps(summary, ensure_ascii=False)) > 3000:
        raise ValueError("Decision summary exceeds 3000 characters")
    if sum(len(text.split()) for text in texts) > 200:
        raise ValueError("Decision summary exceeds 200 words")
    return summary


def api_summary_enabled(provider) -> bool:
    config = provider.config
    return config.get("provider", "openai-compatible") in {
        "openrouter",
        "openai-compatible",
    } and config.get("endpoint", "").startswith(("http://", "https://"))


def _write_summary(directory: Path, record: dict, secret: str = "") -> dict:
    encoded = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    if secret:
        encoded = encoded.replace(secret, "[REDACTED]")
    path = directory / "decision-summary.json"
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(path)
    except OSError:
        # A missing summary must not change the frozen task's outcome.
        record = json.loads(encoded)
        record.update(status="unavailable", reason="Could not save the decision summary")
        return record
    return json.loads(encoded)


def save_unavailable_summary(directory: Path, reason: str, *, secret: str = "") -> dict:
    return _write_summary(
        directory,
        {
            "schema_version": "1.0",
            "kind": "participant-stated retrospective explanation",
            "status": "unavailable",
            "summary": None,
            "reason": reason,
            "collected_before_grading": True,
            "prompt_sha256": SUMMARY_PROMPT_SHA256,
            "usage": {},
            "finished_utc": datetime.now(timezone.utc).isoformat(),
        },
        secret,
    )


def _valid_continuation(history) -> bool:
    pending = set()
    for message in history:
        if isinstance(message, AssistantMessage):
            if pending:
                return False
            pending.update(call.tool_call_id for call in tool_call_blocks(message.blocks))
        elif isinstance(message, ToolMessage):
            pending.discard(message.tool_call_id)
    return bool(history) and not pending


def collect_api_summary(
    provider,
    engine,
    submission: Path,
    *,
    artifact_path: str,
    submission_sha256: dict,
    checkpoints: dict | None = None,
    seconds: float = SUMMARY_SECONDS,
) -> dict:
    """Ask the same model once, after freeze, with no tools or grading feedback."""
    started = time.monotonic()
    record = {
        "schema_version": "1.0",
        "kind": "participant-stated retrospective explanation",
        "status": "unavailable",
        "summary": None,
        "reason": None,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "collected_before_grading": True,
        "prompt_sha256": SUMMARY_PROMPT_SHA256,
        "submission_sha256": submission_sha256,
        "usage": {},
        "provider_response": None,
        "raw_response": None,
    }
    if checkpoints is not None:
        record["checkpoint_sha256"] = {
            key: value["sha256"] if value else None for key, value in checkpoints.items()
        }
    requested = False
    try:
        if not _valid_continuation(engine.history):
            record["reason"] = "Generation did not leave a complete continuation context"
        elif getattr(provider, "fatal_error", None):
            record["reason"] = "Generation provider configuration is unavailable"
        else:
            artifact = submission / artifact_path
            frozen = {
                "frozen_artifact_path": artifact_path,
                "frozen_artifact": (
                    artifact.read_text(encoding="utf-8")
                    if artifact_path in submission_sha256
                    else None
                ),
                "submission_sha256": submission_sha256,
            }
            if checkpoints is not None:
                frozen["frozen_checkpoints"] = checkpoints
            request = SUMMARY_PROMPT + "\n" + json.dumps(frozen, ensure_ascii=False)
            record["request_sha256"] = hashlib.sha256(request.encode()).hexdigest()
            requested = True

            async def request_summary():
                try:
                    return await asyncio.wait_for(
                        provider.generate([*engine.history, UserMessage(content=request)], {}),
                        timeout=seconds,
                    )
                finally:
                    await provider.aclose()

            reply = asyncio.run(request_summary())
            record["usage"] = reply.metadata.get("usage", {})
            record["provider_response"] = reply.metadata.get("provider_response")
            record["raw_response"] = joined_text(reply.blocks)
            if reply.metadata.get("provider_refusal"):
                record["reason"] = "The model or provider declined the summary request"
            elif tool_call_blocks(reply.blocks):
                record["status"], record["reason"] = "invalid", "Summary requested a tool"
            else:
                record["summary"] = validate_summary(json.loads(record["raw_response"]))
                record["status"] = "recorded"
    except TimeoutError:
        record["status"], record["reason"] = "timeout", "Summary exceeded its separate budget"
    except (AttemptLimit, InfrastructureError) as error:
        record["reason"] = str(error)
    except (OSError, ValueError, TypeError, UnicodeError) as error:
        record["status"], record["reason"] = "invalid", str(error)
    finally:
        if getattr(provider, "fatal_error", None):
            record["model_error"] = provider.fatal_error
        if requested and not record["usage"]:
            record["usage"] = getattr(provider, "last_usage", {})
        if requested and record["provider_response"] is None:
            record["provider_response"] = getattr(provider, "last_response_metadata", None)
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        record["finished_utc"] = datetime.now(timezone.utc).isoformat()
        record = _write_summary(submission.parent, record, getattr(provider, "secret", ""))
    return record
