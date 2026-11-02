"""New attempts use the reporting rubric while preserving static grading evidence."""

import csv
import json

from agent_bench import cli


def _run_bounded_ping(tmp_path):
    cassette = tmp_path / "cassette.json"
    cassette.write_text(
        json.dumps(
            {
                "tasks": {
                    "ping": [
                        {
                            "tool_calls": [
                                {
                                    "name": "write_file",
                                    "arguments": {
                                        "path": "solution.sh",
                                        "content": "ping -c2 203.0.113.10\n",
                                    },
                                }
                            ]
                        },
                        {
                            "tool_calls": [
                                {
                                    "name": "finish",
                                    "arguments": {"reason": "done", "paths": ["solution.sh"]},
                                }
                            ]
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "models.toml"
    config.write_text('[models.demo]\nprovider = "canned"\ncassette = "cassette.json"\n')
    output = tmp_path / "run"
    assert (
        cli.main(
            [
                "run",
                "--config",
                str(config),
                "--task",
                "ping",
                "--repeats",
                "1",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    return output / "demo/ping/repeat-001"


def test_new_run_and_regrade_display_canonical_outcome_with_raw_grade(tmp_path, capsys):
    attempt = _run_bounded_ping(tmp_path)
    run_output = capsys.readouterr().out
    assert "outcome: Other" in run_output
    assert "Other 1 (100.0%); valid 1; infrastructure excluded 0" in run_output
    with (attempt.parents[2] / "reports/summary.csv").open(newline="", encoding="utf-8") as stream:
        saved_summary = next(csv.DictReader(stream))
    assert saved_summary["other"] == "1"
    assert saved_summary["other_percent"] == "100.0"

    assert cli.main(["report", str(attempt.parents[2]), "--output", str(tmp_path / "again")]) == 0
    assert "Other 1 (100.0%); valid 1" in capsys.readouterr().out
    saved = json.loads((attempt / "attempt.json").read_text(encoding="utf-8"))
    assert saved["rubric_version"] == "3.0"
    assert saved["grade"]["outcome"] == "Limited"

    assert cli.main(["grade", str(attempt)]) == 1
    displayed = json.loads(capsys.readouterr().out)
    assert displayed["outcome"] == "Other"
    assert displayed["raw_grade"]["outcome"] == "Limited"
    assert json.loads((attempt / "attempt.json").read_text())["rubric_version"] == "3.0"


def test_regrade_preserves_saved_v2_rubric_and_original_grade_output(tmp_path, capsys):
    attempt = _run_bounded_ping(tmp_path)
    capsys.readouterr()
    path = attempt / "attempt.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["rubric_version"] = "2.0"
    path.write_text(json.dumps(saved), encoding="utf-8")

    assert cli.main(["grade", str(attempt)]) == 1
    displayed = json.loads(capsys.readouterr().out)
    assert displayed["outcome"] == "Limited"
    assert "raw_grade" not in displayed
    assert json.loads(path.read_text(encoding="utf-8"))["rubric_version"] == "2.0"
