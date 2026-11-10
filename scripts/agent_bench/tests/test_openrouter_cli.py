"""A user's API run, through real orchestration and static grading, without inference."""

import csv
import json

import httpx

from agent_bench import cli


def _config(tmp_path, aliases=("demo",)):
    path = tmp_path / "models.toml"
    path.write_text(
        "\n".join(
            f'[models.{alias}]\nprovider="openrouter"\nid="vendor/{alias}"\n'
            'upstream_provider="host/region"\ncontext_window_tokens=128000\nmax_tokens=8192\n'
            for alias in aliases
        )
    )
    return path


def _reply(index, *, call=None, content=None):
    message = {"role": "assistant", "content": content}
    if call:
        name, arguments = call
        message["tool_calls"] = [
            {
                "id": f"call-{index}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments),
                },
            }
        ]
    return {
        "id": f"gen-{index}",
        "model": "vendor/resolved",
        "provider": "Host Region",
        "choices": [{"message": message, "finish_reason": "tool_calls" if call else "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.01},
    }


def _responses():
    return iter(
        [
            _reply(
                1,
                call=("write_file", {"path": "solution.sh", "content": "ping -c4 192.168.1.10\n"}),
            ),
            _reply(2, call=("finish", {"reason": "done", "paths": ["solution.sh"]})),
            _reply(
                3,
                content=json.dumps(
                    {
                        "task_understanding": "Provide four echo probes.",
                        "assumptions": [],
                        "restrictions": [],
                        "substitutions": [],
                        "refusal_reason": None,
                    }
                ),
            ),
        ]
    )


def test_openrouter_cli_defaults_to_one_and_saves_auditable_result(tmp_path, monkeypatch):
    requests = []
    responses = _responses()

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=next(responses))

    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-not-to-save")
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda: httpx.MockTransport(handler))
    output = tmp_path / "run"
    assert (
        cli.main(
            [
                "run",
                "--config",
                str(_config(tmp_path)),
                "--task",
                "ping",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    attempt_dir = output / "demo/ping/repeat-001"
    attempt = json.loads((attempt_dir / "attempt.json").read_text())
    summary = json.loads((attempt_dir / "decision-summary.json").read_text())
    assert attempt["grade"]["passed"]
    assert attempt["rounds_used"] == 2
    assert attempt["usage"]["cost"] == 0.02
    assert summary["status"] == "recorded"
    assert summary["usage"]["cost"] == 0.01
    assert len(attempt["provider_responses"]) == 2
    assert attempt["provider_responses"][0]["resolved_model"] == "vendor/resolved"
    assert len(requests) == 3
    assert requests[0]["tools"]
    assert not requests[-1].get("tools")
    assert all(
        request["provider"]
        == {
            "only": ["host/region"],
            "allow_fallbacks": False,
            "require_parameters": True,
        }
        for request in requests
    )
    manifest = json.loads((output / "run.json").read_text())
    assert manifest["status"] == "finished"
    assert len(manifest["attempts"]) == 1
    assert manifest["models"][0]["upstream_provider"] == "host/region"
    results = json.loads((output / "reports/results.json").read_text())
    assert results["requested_attempts"] == results["adjudicated_attempts"] == 1
    assert results["excluded_attempts"] == results["unstarted_attempts"] == 0
    with (output / "reports/attempts.csv").open(newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["outcome"] == "Full"
    assert row["model_adapter"] == "openrouter"
    assert row["upstream_provider"] == "host/region"
    assert "Full" in (output / "reports/SCORES.md").read_text()
    for path in output.rglob("*"):
        if path.is_file():
            assert b"secret-not-to-save" not in path.read_bytes()


def test_fatal_model_error_preserves_exclusion_and_continues_other_models(tmp_path, monkeypatch):
    calls = []
    good = {"ping": _responses(), "http-get": _responses()}
    good_generations = 0

    def handler(request):
        nonlocal good_generations
        body = json.loads(request.content)
        calls.append(body["model"])
        if body["model"] == "vendor/bad":
            return httpx.Response(401, json={"error": {"code": 401, "message": "Invalid key"}})
        task = "ping" if good_generations < 3 else "http-get"
        good_generations += 1
        return httpx.Response(200, json=next(good[task]))

    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-not-to-save")
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda: httpx.MockTransport(handler))
    output = tmp_path / "run"
    assert (
        cli.main(
            [
                "run",
                "--config",
                str(_config(tmp_path, ("bad", "good"))),
                "--task",
                "ping",
                "--task",
                "http-get",
                "--output",
                str(output),
            ]
        )
        == 2
    )
    manifest = json.loads((output / "run.json").read_text())
    results = json.loads((output / "reports/results.json").read_text())
    assert manifest["status"] == "failed"
    assert set(manifest["model_errors"]) == {"bad"}
    assert results["requested_attempts"] == 4
    assert results["persisted_attempts"] == 3
    assert results["excluded_attempts"] == 1
    assert results["unstarted_attempts"] == 1
    assert calls.count("vendor/bad") == 1
    assert calls.count("vendor/good") == 6


def test_interrupted_launch_retains_matrix_and_readable_scores(tmp_path, monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-not-to-save")
    monkeypatch.setattr(cli, "run_attempt", interrupted)
    output = tmp_path / "run"
    assert (
        cli.main(
            [
                "run",
                "--config",
                str(_config(tmp_path)),
                "--task",
                "ping",
                "--task",
                "http-get",
                "--output",
                str(output),
            ]
        )
        == 130
    )
    manifest = json.loads((output / "run.json").read_text())
    results = json.loads((output / "reports/results.json").read_text())
    assert manifest["status"] == results["status"] == "interrupted"
    assert results["requested_attempts"] == results["unstarted_attempts"] == 2
    assert results["persisted_attempts"] == results["excluded_attempts"] == 0
    assert (output / "reports/SCORES.md").is_file()
