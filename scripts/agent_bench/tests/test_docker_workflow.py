"""Opt-in full runner/grade/report acceptance against real Linux containers."""

import csv
import json
import os
import shutil

import pytest

from agent_bench.cli import DEFAULT_IMAGE, main
from agent_bench.runtime import check_image
from agent_bench.tasks import tasks
from reference_solutions import reference_files


@pytest.mark.docker
def test_complete_canned_pilot_and_wrong_tool_pair(tmp_path):
    if os.environ.get("AGENT_BENCH_DOCKER_TESTS") != "1":
        pytest.skip("Set AGENT_BENCH_DOCKER_TESTS=1 to exercise actual containers")
    if not shutil.which("docker"):
        pytest.skip("Docker CLI is unavailable")
    image = os.environ.get("AGENT_BENCH_IMAGE", DEFAULT_IMAGE)
    check_image(image)  # With explicit opt-in, a missing image/daemon is a test failure.
    replies = {}
    for task in tasks():
        replies[task.id] = [
            {
                "tool_calls": [
                    {"name": "write_file", "arguments": {"path": path, "content": content}}
                    for path, content in reference_files(task.id).items()
                ]
            },
            {"content": "Submitted for independent grading."},
        ]
    cassette = tmp_path / "canned.json"
    cassette.write_text(json.dumps({"tasks": replies}))
    config = tmp_path / "canned.toml"
    config.write_text('[models.canned]\nprovider="canned"\ncassette="canned.json"\n')
    output = tmp_path / "run"
    assert (
        main(
            [
                "run",
                "--config",
                str(config),
                "--output",
                str(output),
                "--image",
                image,
                "--repeats",
                "1",
            ]
        )
        == 0
    )
    attempts = [json.loads(path.read_text()) for path in output.rglob("attempt.json")]
    assert len(attempts) == len(replies)
    assert all(attempt["grade"]["passed"] for attempt in attempts), attempts
    with (output / "reports/summary.csv").open(newline="") as stream:
        summaries = list(csv.DictReader(stream))
    assert sum(int(row["successes"]) for row in summaries) == len(replies)
    # Same correct wire behavior, but substitute a non-hping3 implementation.
    wrong = output / "canned/packets-hping3/repeat-001"
    for name, content in reference_files("packets-any").items():
        (wrong / "submission" / name).write_text(content)
    assert main(["grade", str(wrong)]) == 1
    result = json.loads((wrong / "attempt.json").read_text())
    checks = {check["name"]: check["passed"] for check in result["grade"]["checks"]}
    assert checks["wire-packet-contract"]
    assert not checks["required-hping3-executable"]
