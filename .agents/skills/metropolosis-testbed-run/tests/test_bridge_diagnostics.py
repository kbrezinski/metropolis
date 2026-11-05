"""Offline checks for unanswered Codex mailbox requests."""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest


BRIDGE_PATH = Path(__file__).parents[1] / "scripts" / "bridge_run.py"
SPEC = importlib.util.spec_from_file_location("blind_bridge", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


def _provider(tmp_path):
    output = tmp_path / "run"
    mailbox = output / "control" / "task" / "repeat-001"
    attempt = output / "codex-subagent" / "task" / "repeat-001"
    mailbox.mkdir(parents=True)
    attempt.mkdir(parents=True)
    provider = bridge.MailboxProvider(
        output, mailbox, "known-model", 1, set(), b"publisher", directory=attempt
    )
    provider.task_id = "task"
    provider.worker = {"agent_id": "worker-1"}
    return provider, output, attempt, mailbox


async def _wait_for(path):
    for _ in range(100):
        if path.exists():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Request was not published: {path}")


def test_unanswered_request_keeps_last_received_response_and_unknown_cause(tmp_path):
    provider, output, attempt, mailbox = _provider(tmp_path)

    async def exchange():
        first = asyncio.create_task(provider.generate([], {}))
        await _wait_for(mailbox / "request-001.json")
        request = bridge.read_json(mailbox / "request-001.json")
        assert "issued_utc" not in request
        (mailbox / "response-001.json").write_text(
            json.dumps(
                {
                    "attempt_nonce": provider.nonce,
                    "request_id": request["request_id"],
                    "content": "ready",
                    "tool_calls": [],
                    "provider_refusal": False,
                }
            ),
            encoding="utf-8",
        )
        await first
        second = asyncio.create_task(provider.generate([], {}))
        await _wait_for(mailbox / "request-002.json")
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second

    asyncio.run(exchange())
    diagnostic = bridge.read_json(attempt / "unanswered-request.json")
    assert (
        diagnostic["request_id"]
        == bridge.read_json(mailbox / "request-002.json")["request_id"]
    )
    assert diagnostic["round"] == 2
    assert diagnostic["response_present"] is False
    assert diagnostic["last_response_round"] == 1
    assert diagnostic["last_response_received_utc"]
    assert diagnostic["worker_id"] == "worker-1"
    assert diagnostic["participant_status"] == "unobserved"
    assert diagnostic["cause"] == "not determined"
    assert bridge.read_json(output / "active.json")["status"] == "unanswered"
    rows = bridge.unanswered_requests(
        [
            {
                "attempt_path": str(attempt / "attempt.json"),
                "task_id": "task",
                "repeat": "1",
            }
        ]
    )
    assert rows[0]["worker_observation"] is None
    assert "worker status unobserved" in bridge.unanswered_lines(rows)[1]
    assert "last received round 1 at " in bridge.unanswered_lines(rows)[1]


def test_coordinator_observation_is_separate_from_frozen_attempt(tmp_path):
    provider, output, attempt, mailbox = _provider(tmp_path)
    bridge.write_json(output / "run.json", {"tasks": ["task"]})
    frozen_attempt = b'{"outcome":"Other"}'
    (attempt / "attempt.json").write_bytes(frozen_attempt)
    bridge.write_json(
        attempt / "unanswered-request.json",
        {
            "worker_id": "worker-1",
            "request_id": "request-2",
            "round": 2,
            "wait_seconds": 600.0,
            "response_present": False,
            "last_response_round": 1,
        },
    )
    bridge.observe_worker(output, attempt, "failed", "worker stopped")
    observation = bridge.read_json(attempt / "worker-observation.json")
    assert observation["source"] == "coordinator-reported"
    assert observation["worker_id"] == "worker-1"
    assert observation["status"] == "failed"
    assert observation["detail"] == "worker stopped"
    assert (attempt / "attempt.json").read_bytes() == frozen_attempt
    rows = bridge.unanswered_requests(
        [
            {
                "attempt_path": str(attempt / "attempt.json"),
                "task_id": "task",
                "repeat": "1",
            }
        ]
    )
    assert rows[0]["worker_observation"] == observation
    assert "worker status failed" in bridge.unanswered_lines(rows)[1]
    assert (
        f"observed at {observation['observed_utc']}" in bridge.unanswered_lines(rows)[1]
    )
    with pytest.raises(ValueError, match="already recorded"):
        bridge.observe_worker(output, attempt, "running", None)
    with pytest.raises(ValueError, match="inside the selected blind run"):
        bridge.observe_worker(output, tmp_path / "elsewhere", "failed", None)
    assert mailbox.exists()
