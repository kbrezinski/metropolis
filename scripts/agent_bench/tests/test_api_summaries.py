"""Frozen API submissions get a separate, tools-free retrospective call."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from stirrup.core.models import AssistantMessage, ToolCall, UserMessage

from agent_bench.contracts import Grade, Task
from agent_bench.decision_summary import collect_api_summary, validate_summary
from agent_bench.providers import CompatibleProvider
from agent_bench.runner import run_attempt, run_staged_attempt
from agent_bench.staged_ot import staged_ot_tasks


SUMMARY = {
    "task_understanding": "Write the requested file.",
    "assumptions": ["The supplied target is the requested target."],
    "restrictions": [],
    "substitutions": [],
    "refusal_reason": None,
}


def _reply(name, arguments, *, usage=1):
    return {
        "id": "response-" + name,
        "model": "version-a",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-" + name,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": usage, "cost": usage / 1000},
    }


def _provider(monkeypatch, handler):
    monkeypatch.setenv("SUMMARY_KEY", "summary-secret")
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda: httpx.MockTransport(handler))
    return CompatibleProvider(
        {
            "provider": "openai-compatible",
            "endpoint": "https://model.example/v1",
            "id": "version-a",
            "api_key_env": "SUMMARY_KEY",
            "max_tokens": 8192,
            "context_window_tokens": 128000,
        }
    )


def test_summary_after_freeze_before_grade_keeps_generation_budget_and_usage(tmp_path, monkeypatch):
    requests = []
    output = tmp_path / "run"

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                200,
                json=_reply(
                    "write_file",
                    {
                        "path": "solution.sh",
                        "content": "echo supplied\n",
                    },
                    usage=2,
                ),
            )
        if len(requests) == 2:
            return httpx.Response(
                200, json=_reply("finish", {"reason": "Done", "paths": []}, usage=3)
            )
        assert (output / "submission/solution.sh").read_text() == "echo supplied\n"
        assert not (output / "attempt.json").exists()
        return httpx.Response(
            200,
            json={
                "id": "summary-id",
                "model": "version-a",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": json.dumps(SUMMARY)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 11, "cost": 0.011},
            },
        )

    def grade(*_):
        summary = json.loads((output / "decision-summary.json").read_text())
        assert summary["status"] == "recorded"
        assert summary["collected_before_grading"]
        return Grade(True)

    monkeypatch.setattr("agent_bench.grading.grade", grade)
    provider = _provider(monkeypatch, handler)
    result = run_attempt(
        Task("api-summary", "benign-command", "Write solution.sh"),
        provider.config,
        provider,
        output,
        1,
        "suite",
        rounds=2,
    )
    summary = json.loads((output / "decision-summary.json").read_text())
    assert result["grade"]["passed"]
    assert result["rounds_used"] == 2
    assert result["usage"] == {"prompt_tokens": 2, "completion_tokens": 5, "cost": 0.005}
    assert summary["usage"] == {"prompt_tokens": 1, "completion_tokens": 11, "cost": 0.011}
    assert summary["summary"] == SUMMARY
    assert len(result["provider_responses"]) == 2
    assert "tools" not in requests[2]
    assert any(message["role"] == "tool" for message in requests[2]["messages"])
    assert "closed for changes" in json.dumps(requests[2]["messages"][-1]["content"])
    assert "summary-secret" not in (output / "trace.jsonl").read_text()
    assert "summary-secret" not in (output / "decision-summary.json").read_text()


@pytest.mark.parametrize(
    "summary_reply,status,fatal",
    [
        (
            {"choices": [{"message": {"role": "assistant", "content": "not JSON"}}]},
            "invalid",
            False,
        ),
        (
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "refusal": "No summary"},
                        "finish_reason": "content_filter",
                    }
                ]
            },
            "unavailable",
            False,
        ),
        ({"error": {"code": 503, "message": "Unavailable"}}, "unavailable", False),
        ({"error": {"code": 401, "message": "Authentication failed"}}, "unavailable", True),
    ],
)
def test_summary_failure_never_changes_task_grade_or_refusal(
    tmp_path, monkeypatch, summary_reply, status, fatal
):
    replies = iter(
        [
            _reply("write_file", {"path": "solution.sh", "content": "echo supplied\n"}),
            _reply("finish", {"reason": "Done", "paths": []}),
            summary_reply,
        ]
    )
    provider = _provider(monkeypatch, lambda _: httpx.Response(200, json=next(replies)))
    monkeypatch.setattr("agent_bench.grading.grade", lambda *_: Grade(True))
    result = run_attempt(
        Task("api-summary", "benign-command", "Write solution.sh"),
        provider.config,
        provider,
        tmp_path / "run",
        1,
        "suite",
    )
    summary = json.loads((tmp_path / "run/decision-summary.json").read_text())
    assert result["grade"]["passed"]
    assert result["stop_reason"] == "complete"
    assert not result["provider_refusal"]
    assert result["final_response"] == "Done"
    assert result["rounds_used"] == 2
    assert summary["status"] == status
    assert bool(result.get("model_error")) is fatal
    assert bool(summary.get("model_error")) is fatal


def test_summary_timeout_and_incomplete_context_are_explicit(tmp_path):
    submission = tmp_path / "submission"
    submission.mkdir()

    class Provider:
        secret = ""
        last_usage = {}

        async def generate(self, *_):
            await asyncio.sleep(1)

        async def aclose(self):
            pass

    engine = SimpleNamespace(history=[UserMessage(content="Task")])
    record = collect_api_summary(
        Provider(),
        engine,
        submission,
        artifact_path="solution.sh",
        submission_sha256={},
        seconds=0.001,
    )
    assert record["status"] == "timeout"
    engine.history.append(
        AssistantMessage(
            blocks=[
                ToolCall(
                    name="write_file",
                    arguments="{}",
                    tool_call_id="unfinished",
                )
            ]
        )
    )
    record = collect_api_summary(
        Provider(), engine, submission, artifact_path="solution.sh", submission_sha256={}
    )
    assert record["status"] == "unavailable"
    assert record["usage"] == {}


def test_summary_schema_rejects_word_budget_and_nontext_lists():
    with pytest.raises(ValueError, match="200 words"):
        validate_summary({**SUMMARY, "task_understanding": "word " * 201})
    with pytest.raises(ValueError, match="list of text"):
        validate_summary({**SUMMARY, "restrictions": [None]})


def test_staged_summary_includes_only_reached_frozen_snapshots(tmp_path, monkeypatch):
    task = staged_ot_tasks()[0]
    requests = []
    replies = iter(
        [
            _reply("write_file", {"path": "solution.sh", "content": "echo stage one\n"}),
            _reply("submit_stage", {}),
            _reply("finish", {"reason": "Stop here", "paths": []}),
            {"choices": [{"message": {"role": "assistant", "content": json.dumps(SUMMARY)}}]},
        ]
    )

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=next(replies))

    provider = _provider(monkeypatch, handler)
    result = run_staged_attempt(task, provider.config, provider, tmp_path / "run", 1, "suite")
    summary = json.loads((tmp_path / "run/decision-summary.json").read_text())
    assert result["episode_status"] == "early_finish"
    assert summary["status"] == "recorded"
    assert summary["checkpoint_sha256"]["1"]
    assert summary["checkpoint_sha256"]["2"] is None
    assert summary["checkpoint_sha256"]["3"] is None
    assert "frozen_checkpoints" in json.dumps(requests[-1]["messages"][-1]["content"])
