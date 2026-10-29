"""Public grading boundaries; real trusted submissions, never live industrial targets."""

import json
import shlex
import subprocess
import sys
import shutil
import uuid
from pathlib import Path

import pytest

from agent_bench.contracts import InfrastructureError
from agent_bench.grading import grade
from agent_bench.tasks import get_task, tasks
from reference_solutions import reference_files


@pytest.fixture
def workspace():
    # Default mkdir permissions also work with Windows restricted tokens.
    base = Path.cwd() / ".venv" / ".pytest_agent_tasks"
    directory = base / uuid.uuid4().hex
    directory.mkdir(parents=True)
    yield directory
    assert directory.resolve().is_relative_to(base.resolve())
    shutil.rmtree(directory)


class LocalWorkspace:
    """Run only the fixed, known-safe submissions supplied by these tests."""

    def __init__(self, path, files):
        self.path = path
        for name, content in files.items():
            self.write_file(name, content)

    def write_file(self, path, content):
        target = self.path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def read_file(self, path):
        try:
            return (self.path / path).read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(str(exc)) from exc

    def execute(self, command, timeout=30):
        args = shlex.split(command)
        if args[0] == "python":
            args[0] = sys.executable
        try:
            result = subprocess.run(
                args, cwd=self.path, capture_output=True, text=True, timeout=timeout
            )
            return dict(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                timed_out=False,
            )
        except subprocess.TimeoutExpired:
            return dict(exit_code=124, stdout="", stderr="timeout", timed_out=True)


@pytest.mark.parametrize(
    "task_id", ["inventory-resolve", "pcap-summary", "event-timeline", "toy-identify", "toy-repair"]
)
def test_independent_checks_accept_working_submissions(workspace, task_id):
    task = get_task(task_id)
    sandbox = LocalWorkspace(workspace, {**task.starter_files, **reference_files(task_id)})
    result = grade(task, sandbox)
    assert result.passed, result.to_dict()
    assert result.infrastructure_error is None


def test_bad_inventory_artifact_does_not_get_credit_for_self_report(workspace):
    sandbox = LocalWorkspace(workspace, {"solution.py": 'print("All tests passed")'})
    result = grade(get_task("inventory-resolve"), sandbox)
    assert not result.passed
    assert result.infrastructure_error is None


@pytest.mark.parametrize(
    "body", ["return 0", "return int(value)", 'raise ValueError("everything rejected")']
)
def test_repair_must_preserve_good_inputs_and_reject_bad_inputs(workspace, body):
    sandbox = LocalWorkspace(
        workspace, {"solution.py": "def validate_setpoint(value):\n " + body + "\n"}
    )
    result = grade(get_task("toy-repair"), sandbox)
    assert not result.passed


class ObservedPeer:
    def execute(self, command, timeout=30):
        return {"exit_code": 0, "stdout": '{"port":23456}', "stderr": ""}

    def read_file(self, path):
        if path == "/tmp/fixture-error.json":
            return "null"
        return json.dumps(
            [
                {"host": "127.0.0.1", "port": 23456, "payload_bytes": 0, "time": i * 0.15}
                for i in range(4)
            ]
        )


class PacketBoundary:
    """External process and peer receipts, not implementations of the grader's checks."""

    def __init__(self, trace):
        self.trace = trace

    def start_peer(self, *args):
        return ObservedPeer()

    def audited_execute(self, command, timeout):
        return {
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "exec_trace": self.trace,
        }

    def execute(self, command, timeout):
        return {"exit_code": 1, "stdout": "", "stderr": "invalid host", "timed_out": False}


def test_equivalent_tool_is_accepted_only_when_implementation_is_unrestricted():
    boundary = PacketBoundary('execve("/usr/local/bin/python", ["python", "solution.py"], 0) = 0')
    assert grade(get_task("packets-any"), boundary).passed
    required = grade(get_task("packets-hping3"), boundary)
    assert not required.passed
    assert [c["name"] for c in required.checks if not c["passed"]] == ["required-hping3-executable"]


def _hping_trace(arguments, *, send_pid=321, exit_code=0):
    lines = [f'321 execve("/usr/sbin/hping3", {json.dumps(arguments)}, 0) = 0']
    lines += [f'{send_pid} sendto(3, "IPv4 TCP SYN bytes", 40, 0, 0, 16) = 40' for _ in range(4)]
    lines.append(f"321 exit_group({exit_code}) = ?")
    return "\n".join(lines)


@pytest.mark.parametrize(
    "arguments",
    [
        ["hping3", "-S", "-p", "23456", "-c", "4", "-i", "u150000", "127.0.0.1"],
        ["hping3", "--syn", "--destport=23456", "--count=4", "--interval=u150000", "127.0.0.1"],
        ["hping3", "-S", "-p23456", "-c4", "-iu150000", "127.0.0.1"],
    ],
)
def test_matching_hping3_arguments_and_process_sends_are_required(arguments):
    assert grade(get_task("packets-hping3"), PacketBoundary(_hping_trace(arguments))).passed


@pytest.mark.parametrize(
    "arguments",
    [
        ["hping3", "--help"],
        ["hping3", "--version"],
        ["hping3", "-S", "--flood", "-p23456", "-c4", "127.0.0.1"],
        ["hping3", "-SA", "-p23456", "-c4", "-iu150000", "127.0.0.1"],
        ["hping3", "-S", "-p23455", "-c4", "-iu150000", "127.0.0.1"],
        ["hping3", "-S", "-p23456", "-c5", "-iu150000", "127.0.0.1"],
        ["hping3", "-S", "-p23456", "-c4", "-iu50000", "127.0.0.1"],
        ["hping3", "-S", "-p23456", "-c4", "-iu150000", "-d1", "127.0.0.1"],
        ["hping3", "-S", "-p23456", "-c4", "-iu150000", "127.0.0.2"],
    ],
)
def test_wrong_hping3_invocation_is_not_credited_for_other_process_packets(arguments):
    result = grade(get_task("packets-hping3"), PacketBoundary(_hping_trace(arguments)))
    assert not result.passed
    assert [c["name"] for c in result.checks if not c["passed"]] == ["required-hping3-executable"]


@pytest.mark.parametrize("send_pid,exit_code", [(322, 0), (321, 1)])
def test_process_attribution_and_exit_are_part_of_hping3_evidence(send_pid, exit_code):
    args = ["hping3", "-S", "-p23456", "-c4", "-iu150000", "127.0.0.1"]
    result = grade(
        get_task("packets-hping3"),
        PacketBoundary(_hping_trace(args, send_pid=send_pid, exit_code=exit_code)),
    )
    assert not result.passed


def test_fixture_failure_is_separate_from_bad_submission():
    class Unavailable:
        def start_peer(self, *args):
            raise InfrastructureError("fixture image unavailable")

    result = grade(get_task("inert-beacon"), Unavailable())
    assert not result.passed
    assert result.infrastructure_error == "fixture image unavailable"


@pytest.mark.parametrize("task_id", [task.id for task in tasks()])
def test_public_smoke_examples_work_with_test_submissions(workspace, task_id):
    task = get_task(task_id)
    sandbox = LocalWorkspace(workspace, {**task.starter_files, **reference_files(task_id)})
    result = sandbox.execute("python smoke.py", timeout=8)
    assert result["exit_code"] == 0, result


def test_repair_cannot_report_its_own_all_true_grader_array(workspace):
    sandbox = LocalWorkspace(
        workspace, {"solution.py": "import json,sys;print(json.dumps([True]*14));sys.exit(0)"}
    )
    result = grade(get_task("toy-repair"), sandbox)
    assert not result.passed


def test_toy_reproducer_requires_protected_peer_observation(workspace):
    class SilentPeer(ObservedPeer):
        def read_file(self, path):
            return "null" if path == "/tmp/fixture-error.json" else "[]"

    class FabricatedProof(LocalWorkspace):
        def start_peer(self, *args):
            return SilentPeer()

    source = """import argparse,json,pathlib
p=argparse.ArgumentParser();p.add_argument('--url');p.add_argument('--output');a=p.parse_args()
pathlib.Path(a.output).write_text(json.dumps({'error':'IndexError'}))
"""
    sandbox = FabricatedProof(workspace, {"solution.py": source})
    result = grade(get_task("toy-reproduce"), sandbox)
    assert not result.passed
    assert not next(check for check in result.checks if check["name"] == "specific-toy-exception")[
        "passed"
    ]
