"""Opt-in real Docker acceptance checks; never substituted with mocked wire proof."""

import os

import pytest

from agent_bench.grading import grade
from agent_bench.runtime import Sandbox
from agent_bench.tasks import get_task, tasks
from reference_solutions import PACKETS_STDLIB, reference_files

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_BENCH_DOCKER_TESTS") != "1",
    reason="Set AGENT_BENCH_DOCKER_TESTS=1 with the fixture image built and Docker available",
)
IMAGE = os.environ.get("AGENT_BENCH_IMAGE", "industrial-agent-bench:0.1.0")


@pytest.mark.parametrize("task_id", [task.id for task in tasks()])
def test_protected_docker_grader_accepts_reference_artifacts(task_id):
    task = get_task(task_id)
    with Sandbox(IMAGE, raw_network=task.raw_network) as sandbox:
        for name, content in {**task.starter_files, **reference_files(task_id)}.items():
            sandbox.write_file(name, content)
        result = grade(task, sandbox)
        assert result.passed, result.to_dict()


def test_wire_equivalent_substitution_fails_hping3_requirement_only():
    for task_id, expected in [("packets-any", True), ("packets-hping3", False)]:
        task = get_task(task_id)
        with Sandbox(IMAGE, raw_network=True) as sandbox:
            sandbox.write_file("solution.py", PACKETS_STDLIB)
            result = grade(task, sandbox)
            assert result.passed is expected, result.to_dict()
            if not expected:
                assert [c["name"] for c in result.checks if not c["passed"]] == [
                    "required-hping3-executable"
                ]


def test_student_written_observation_file_is_not_trusted():
    task = get_task("toy-reproduce")
    source = """import argparse,json,pathlib
p=argparse.ArgumentParser();p.add_argument('--url');p.add_argument('--output');a=p.parse_args()
pathlib.Path('/tmp/observations.json').write_text('[{"raised":"IndexError"}]')
pathlib.Path(a.output).write_text(json.dumps({'error':'IndexError'}))
"""
    with Sandbox(IMAGE) as sandbox:
        sandbox.write_file("solution.py", source)
        assert not grade(task, sandbox).passed


def test_hping3_help_then_python_packets_is_not_hping3_fulfilment():
    hybrid = (
        "import subprocess\nsubprocess.run(['/usr/sbin/hping3','--help'],capture_output=True)\n"
        + PACKETS_STDLIB
    )
    with Sandbox(IMAGE, raw_network=True) as sandbox:
        sandbox.write_file("solution.py", hybrid)
        result = grade(get_task("packets-hping3"), sandbox)
        assert not result.passed
        assert [c["name"] for c in result.checks if not c["passed"]] == [
            "required-hping3-executable"
        ]
