"""Prompt conditions stay opt-in and never share a report denominator."""

import csv
import json

from agent_bench import cli
from agent_bench.reporting import report


def _save_attempt(root, name, *, condition, outcome, refusal=False, format_followed=None):
    folder = root / name
    folder.mkdir(parents=True)
    attempt = {
        "evaluation_mode": "command-generation",
        "rubric_version": "2.0",
        "task_id": "ping",
        "task_family": "benign-command",
        "task_version": "1.0",
        "task_hash": "same-contract",
        "suite_hash": "same-suite",
        "prompt_condition": condition,
        "prompt_condition_version": "1.0",
        "prompt_condition_sha256": f"template-{condition}",
        "effective_prompt_sha256": f"prompt-{condition}",
        "model": {"alias": "demo", "id": "model", "endpoint": "local", "reasoning": None},
        "agent_engine": {"name": "stirrup", "version": "0.2.1", "config": {}},
        "budget": {"rounds": 30, "seconds": 600},
        "grade": {
            "passed": outcome == "Full",
            "outcome": outcome,
            "format_followed": format_followed,
        },
        "stop_reason": "provider_refusal" if refusal else "complete",
        "provider_refusal": refusal,
    }
    (folder / "attempt.json").write_text(json.dumps(attempt), encoding="utf-8")


def test_condition_reports_separate_denominators_and_format_observation(tmp_path):
    root = tmp_path / "attempts"
    _save_attempt(root, "base-full", condition="baseline", outcome="Full")
    _save_attempt(root, "base-refusal", condition="baseline", outcome="Failure", refusal=True)
    _save_attempt(root, "authority-1", condition="system-mode", outcome="Full")
    _save_attempt(root, "authority-2", condition="system-mode", outcome="Full")
    _save_attempt(
        root, "format-plain", condition="output-format", outcome="Full", format_followed=False
    )
    _save_attempt(
        root, "format-encoded", condition="output-format", outcome="Limited", format_followed=True
    )

    summaries = {row["prompt_condition"]: row for row in report(root, tmp_path / "out")}
    assert set(summaries) == {"baseline", "system-mode", "output-format"}
    assert (summaries["baseline"]["full"], summaries["baseline"]["refusals"]) == (1, 1)
    assert summaries["system-mode"]["full"] == 2
    assert summaries["system-mode"]["full_delta_pp"] == 50
    assert summaries["system-mode"]["refusal_delta_pp"] == -50
    assert summaries["output-format"]["format_followed_count"] == 1
    assert summaries["output-format"]["format_missed_count"] == 1
    assert summaries["output-format"]["full_delta_pp"] == 0
    with (tmp_path / "out/attempts.csv").open(newline="", encoding="utf-8") as stream:
        exported = list(csv.DictReader(stream))
    assert {row["prompt_condition"] for row in exported} == set(summaries)
    assert (
        next(row for row in exported if row["prompt_condition"] == "output-format")[
            "prompt_condition_sha256"
        ]
        == "template-output-format"
    )
    assert next(row for row in exported if row["prompt_condition"] == "output-format")[
        "format_followed"
    ] in {"True", "False"}


def test_run_selects_variant_path_without_changing_baseline_path(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "load_models", lambda _: {"demo": {}})
    monkeypatch.setattr(cli, "make_provider", lambda _: object())
    monkeypatch.setattr(cli, "suite_hash", lambda _: "suite")
    monkeypatch.setattr(cli, "report", lambda *_: [])

    def record(*args, **kwargs):
        calls.append((args[3], kwargs["prompt_condition"]))
        return {"stop_reason": "complete", "grade": {"outcome": "Full"}}

    monkeypatch.setattr(cli, "run_attempt", record)
    output = tmp_path / "run"
    assert (
        cli.main(
            [
                "run",
                "--config",
                str(tmp_path / "model.toml"),
                "--model",
                "demo",
                "--task",
                "ping",
                "--repeats",
                "1",
                "--prompt-condition",
                "baseline",
                "--prompt-condition",
                "system-mode",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert calls == [
        (output / "demo/ping/repeat-001", "baseline"),
        (output / "demo/ping/condition-04/repeat-001", "system-mode"),
    ]
