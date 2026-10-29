"""Real Stirrup loop with canned responses and a substituted Docker boundary."""

import asyncio
import json

import pytest
from stirrup.core.models import AssistantMessage, ToolCall

from agent_bench.contracts import AttemptLimit, Grade, InfrastructureError, Task
from agent_bench.providers import CannedProvider
from agent_bench import runner


class Workspace:
    """Never executes code; actual container tests are separately opt-in."""

    image_id = "sha256:fixture-image"
    commands = []

    def __init__(self, *a, **kw):
        self.files = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def write_file(self, path, content, timeout=30):
        self.files[path] = content

    def read_file(self, path, timeout=30):
        return self.files[path]

    def execute(self, command, timeout):
        self.commands.append(command)
        return {"exit_code": 0, "stdout": "fixture boundary", "stderr": "", "timed_out": False}

    def collect(self, destination):
        destination.mkdir()
        for name, content in self.files.items():
            (destination / name).write_text(content)

    def load(self, source):
        self.files = {p.name: p.read_text() for p in source.iterdir()}


def call(name, **arguments):
    return {"name": name, "arguments": arguments}


@pytest.fixture
def run_case(tmp_path, monkeypatch):
    import agent_bench.grading

    Workspace.commands.clear()
    monkeypatch.setattr(runner, "Sandbox", Workspace)
    monkeypatch.setattr(agent_bench.grading, "grade", lambda *a: Grade(True))

    def run(replies, task=None, change_provider=None, **budgets):
        task = task or Task("example", "support", "Write answer.py")
        cassette = tmp_path / "cassette.json"
        cassette.write_text(json.dumps({"tasks": {task.id: replies}}))
        provider = CannedProvider({"cassette": str(cassette)})
        if change_provider:
            change_provider(provider)
        return runner.run_attempt(
            task, {}, provider, tmp_path / "run", "fixture", 1, "suite", **budgets
        )

    return run


def events(tmp_path):
    return [json.loads(line) for line in (tmp_path / "run/trace.jsonl").read_text().splitlines()]


def test_real_stirrup_edits_reads_checks_and_finishes_with_redacted_evidence(tmp_path, run_case):
    result = run_case(
        [
            {
                "tool_calls": [
                    call("write_file", path="answer.py", content="credential-secret"),
                    call("read_file", path="answer.py"),
                    call("run_command", command="local-check"),
                ],
                "usage": {"completion_tokens": 5},
            },
            {"content": "Finished credential-secret", "usage": {"completion_tokens": 3}},
        ],
        change_provider=lambda provider: setattr(provider, "secret", "credential-secret"),
    )
    assert result["rounds_used"] == 2
    assert result["tool_calls"] == 4
    assert result["usage"]["completion_tokens"] == 8
    assert result["stop_reason"] == "complete"
    assert (tmp_path / "run/submission/answer.py").read_text() == "[REDACTED]"
    assert Workspace.commands == ["local-check"]
    trace = events(tmp_path)
    context = trace[0]["messages"]
    assert runner.SYSTEM_PROMPT in context[0]["content"]
    assert "benchmark" not in context[0]["content"].lower()
    assert context[1]["content"] == "Write answer.py"
    requests = [event for event in trace if event["event"] == "model_request"]
    assert {tool["name"] for tool in requests[0]["tools"]} == {
        "read_file",
        "write_file",
        "run_command",
        "finish",
    }
    results = [event for event in trace if event["event"] == "tool_result"]
    assert [event["name"] for event in results] == [
        "write_file",
        "read_file",
        "run_command",
        "finish",
    ]
    assert json.loads(results[1]["result"]) == {"content": "[REDACTED]"}
    assert all(event["duration_seconds"] >= 0 for event in results)
    assert result["agent_engine"]["name"] == "stirrup"
    assert result["agent_engine"]["version"] == "0.2.1"
    assert "credential-secret" not in json.dumps(trace)
    assert result["final_response"] == "Finished [REDACTED]"


def test_late_response_cannot_dispatch_tools(tmp_path, run_case):
    async def late(*args):
        try:
            await asyncio.sleep(0.15)
        except asyncio.CancelledError:
            pass
        return AssistantMessage(
            blocks=[
                ToolCall(
                    name="run_command", arguments='{"command":"late-command"}', tool_call_id="late"
                )
            ]
        )

    result = run_case([], change_provider=lambda p: setattr(p, "generate", late), seconds=0.02)
    assert result["stop_reason"] == "time_limit"
    assert not result["grade"]["passed"]
    assert Workspace.commands == []


def test_round_limit_exports_submission(tmp_path, run_case):
    result = run_case(
        [{"tool_calls": [call("write_file", path="answer.py", content="pass")]}], rounds=1
    )
    assert result["stop_reason"] == "round_limit"
    assert not result["grade"]["passed"]
    assert (tmp_path / "run/submission/answer.py").exists()


def test_refusal_blocks_attached_tools(run_case):
    result = run_case(
        [
            {
                "content": "Declined",
                "provider_refusal": True,
                "tool_calls": [call("run_command", command="must-not-run")],
            }
        ]
    )
    assert result["stop_reason"] == "provider_refusal"
    assert result["provider_refusal"]
    assert result["tool_calls"] == 0
    assert Workspace.commands == []


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


def test_invalid_calls_return_feedback_and_allow_revision(tmp_path, run_case):
    result = run_case(
        [
            {"tool_calls": [{"name": "write_file", "arguments": "not-json"}, call("unavailable")]},
            {"tool_calls": [call("write_file", path="answer.py", content="repaired")]},
            {"content": "Finished"},
        ]
    )
    tools = [event for event in events(tmp_path) if event["event"] == "tool_result"]
    assert [event["success"] for event in tools] == [False, False, True, True]
    assert (tmp_path / "run/submission/answer.py").read_text() == "repaired"
    assert result["stop_reason"] == "complete"


def test_grading_outage_can_be_regraded(tmp_path, monkeypatch, run_case):
    import agent_bench.grading
    from agent_bench.tasks import get_task

    def unavailable(*args):
        raise InfrastructureError("grader daemon unavailable")

    monkeypatch.setattr(agent_bench.grading, "grade", unavailable)
    result = run_case([{"content": "Submitted"}], task=get_task("inventory-resolve"))
    assert result["stop_reason"] == "grading_error"
    assert result["generation_stop_reason"] == "complete"
    monkeypatch.setattr(agent_bench.grading, "grade", lambda *a: Grade(False))
    result = runner.grade_attempt(tmp_path / "run")
    assert result["stop_reason"] == "complete"
    assert result["grade"]["infrastructure_error"] is None
    history = [json.loads(p.read_text()) for p in (tmp_path / "run/grade-history").glob("*.json")]
    assert history[0]["previous_provenance"]["suite_hash"] == "suite"
    assert history[0]["new_provenance"]["suite_hash"] == result["grading_suite_hash"]


def test_interrupt_preserves_cancelled_evidence(tmp_path, monkeypatch, run_case):
    class StopWorkspace(Workspace):
        def execute(self, command, timeout):
            raise KeyboardInterrupt

    monkeypatch.setattr(runner, "Sandbox", StopWorkspace)
    with pytest.raises(KeyboardInterrupt):
        run_case([{"tool_calls": [call("run_command", command="check")]}])
    saved = json.loads((tmp_path / "run/attempt.json").read_text())
    assert saved["stop_reason"] == "interrupted"
    assert "cancelled" in saved["grade"]["infrastructure_error"]
