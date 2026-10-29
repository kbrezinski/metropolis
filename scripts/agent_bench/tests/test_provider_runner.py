"""Real Stirrup orchestration over bounded, file-only authoring tools."""

import asyncio
import json

import pytest
from stirrup.core.models import AssistantMessage, ToolCall

from agent_bench.contracts import AttemptLimit, Grade, InfrastructureError, Task
from agent_bench.providers import CannedProvider
from agent_bench import runner, runtime


def call(name, **arguments):
    return {"name": name, "arguments": arguments}


@pytest.fixture
def run_case(tmp_path, monkeypatch):
    import agent_bench.grading

    # These tests own orchestration/provider behavior; semantic grading has its
    # own independent command fixtures and is exercised by the complete pilot.
    monkeypatch.setattr(agent_bench.grading, "grade", lambda *args: Grade(True))

    def run(replies, task=None, change_provider=None, **budgets):
        task = task or Task("example", "benign-command", "Write solution.sh")
        cassette = tmp_path / "cassette.json"
        cassette.write_text(json.dumps({"tasks": {task.id: replies}}))
        provider = CannedProvider({"cassette": str(cassette)})
        if change_provider:
            change_provider(provider)
        return runner.run_attempt(task, {}, provider, tmp_path / "run", 1, "suite", **budgets)

    return run


def events(tmp_path):
    return [json.loads(line) for line in (tmp_path / "run/trace.jsonl").read_text().splitlines()]


def test_stirrup_authors_without_execution_and_preserves_redacted_evidence(tmp_path, run_case):
    marker = tmp_path / "must-not-exist"
    source = f"touch '{marker.as_posix()}'\n# credential-secret\n"
    result = run_case(
        [
            {
                "tool_calls": [
                    call("write_file", path="solution.sh", content=source),
                    call("read_file", path="solution.sh"),
                ],
                "usage": {"completion_tokens": 5},
            },
            {"content": "Finished credential-secret", "usage": {"completion_tokens": 3}},
        ],
        change_provider=lambda provider: setattr(provider, "secret", "credential-secret"),
    )
    assert result["rounds_used"] == 2
    assert result["tool_calls"] == 3
    assert result["usage"]["completion_tokens"] == 8
    assert result["stop_reason"] == "complete"
    assert (tmp_path / "run/submission/solution.sh").read_text() == source.replace(
        "credential-secret", "[REDACTED]"
    )
    assert not marker.exists()
    trace = events(tmp_path)
    context = trace[0]["messages"]
    assert runner.SYSTEM_PROMPT in context[0]["content"]
    assert "benchmark" not in context[0]["content"].lower()
    assert context[1]["content"] == "Write solution.sh"
    request = next(event for event in trace if event["event"] == "model_request")
    assert {tool["name"] for tool in request["tools"]} == {"read_file", "write_file", "finish"}
    results = [event for event in trace if event["event"] == "tool_result"]
    assert [event["name"] for event in results] == ["write_file", "read_file", "finish"]
    assert json.loads(results[1]["result"]) == {
        "content": source.replace("credential-secret", "[REDACTED]")
    }
    assert all(event["duration_seconds"] >= 0 for event in results)
    assert result["agent_engine"]["name"] == "stirrup"
    assert result["agent_engine"]["version"] == "0.2.1"
    assert "credential-secret" not in json.dumps(trace)
    assert result["final_response"] == "Finished [REDACTED]"


def test_late_response_cannot_write_artifacts(tmp_path, run_case):
    async def late(*args):
        try:
            await asyncio.sleep(0.15)
        except asyncio.CancelledError:
            # Windows may deliver a short asyncio timeout slightly early.
            # Model a response that really arrives after the wall-clock budget.
            await asyncio.sleep(0.05)
        return AssistantMessage(
            blocks=[
                ToolCall(
                    name="write_file",
                    arguments='{"path":"late.sh","content":"late"}',
                    tool_call_id="late",
                )
            ]
        )

    result = run_case(
        [], change_provider=lambda provider: setattr(provider, "generate", late), seconds=0.02
    )
    assert result["stop_reason"] == "time_limit"
    assert not result["grade"]["passed"]
    assert not (tmp_path / "run/submission/late.sh").exists()


def test_round_limit_exports_submission(tmp_path, run_case):
    result = run_case(
        [{"tool_calls": [call("write_file", path="solution.sh", content="ping 203.0.113.10")]}],
        rounds=1,
    )
    assert result["stop_reason"] == "round_limit"
    assert not result["grade"]["passed"]
    assert (tmp_path / "run/submission/solution.sh").exists()


def test_refusal_blocks_attached_file_tools(tmp_path, run_case):
    result = run_case(
        [
            {
                "content": "Declined",
                "provider_refusal": True,
                "tool_calls": [call("write_file", path="declined.sh", content="must-not-write")],
            }
        ]
    )
    assert result["stop_reason"] == "provider_refusal"
    assert result["provider_refusal"]
    assert result["tool_calls"] == 0
    assert not (tmp_path / "run/submission/declined.sh").exists()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AttemptLimit("Model context limit exhausted"), "context_limit"),
        (AttemptLimit("Model output token limit exhausted"), "output_limit"),
        (InfrastructureError("Provider unavailable"), "provider_error"),
    ],
)
def test_provider_limits_and_outages_retain_distinct_evidence(tmp_path, run_case, error, expected):
    def change(provider):
        async def fail(*args):
            provider.last_usage = {"completion_tokens": 9}
            raise error

        provider.generate = fail

    result = run_case([], change_provider=change)
    assert result["stop_reason"] == expected
    assert not result["grade"]["passed"]
    assert bool(result["grade"]["infrastructure_error"]) is (expected == "provider_error")
    assert result["usage"]["completion_tokens"] == 9
    assert any(event["event"] == "model_error" for event in events(tmp_path))


def test_invalid_and_execution_calls_return_feedback_without_running(tmp_path, run_case):
    marker = tmp_path / "forbidden-command"
    result = run_case(
        [
            {
                "tool_calls": [
                    {"name": "write_file", "arguments": "not-json"},
                    call("run_command", command=f"touch '{marker.as_posix()}'"),
                ]
            },
            {"tool_calls": [call("write_file", path="solution.sh", content="corrected")]},
            {"content": "Finished"},
        ]
    )
    tools = [event for event in events(tmp_path) if event["event"] == "tool_result"]
    assert [event["success"] for event in tools] == [False, False, True, True]
    assert (tmp_path / "run/submission/solution.sh").read_text() == "corrected"
    assert not marker.exists()
    assert result["stop_reason"] == "complete"


def test_grading_outage_can_be_regraded(tmp_path, monkeypatch, run_case):
    import agent_bench.grading
    from agent_bench.tasks import get_task

    def unavailable(*args):
        raise InfrastructureError("grader storage unavailable")

    monkeypatch.setattr(agent_bench.grading, "grade", unavailable)
    result = run_case([{"content": "Submitted"}], task=get_task("ping"))
    assert result["stop_reason"] == "grading_error"
    assert result["generation_stop_reason"] == "complete"
    monkeypatch.setattr(agent_bench.grading, "grade", lambda *args: Grade(False))
    result = runner.grade_attempt(tmp_path / "run")
    assert result["stop_reason"] == "complete"
    assert result["grade"]["infrastructure_error"] is None
    history = [
        json.loads(path.read_text()) for path in (tmp_path / "run/grade-history").glob("*.json")
    ]
    assert history[0]["previous_provenance"]["suite_hash"] == "suite"
    assert history[0]["new_provenance"]["suite_hash"] == result["grading_suite_hash"]


def test_interrupt_preserves_cancelled_evidence(tmp_path, monkeypatch, run_case):
    class StopWorkspace(runtime.Workspace):
        def write_file(self, path, content, timeout=None):
            raise KeyboardInterrupt

    monkeypatch.setattr(runner, "Workspace", StopWorkspace)
    with pytest.raises(KeyboardInterrupt):
        run_case([{"tool_calls": [call("write_file", path="solution.sh", content="interrupted")]}])
    saved = json.loads((tmp_path / "run/attempt.json").read_text())
    assert saved["stop_reason"] == "interrupted"
    assert "cancelled" in saved["grade"]["infrastructure_error"]
