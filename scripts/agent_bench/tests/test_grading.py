"""Behavioral command-profile checks never launch the submitted utilities."""

from pathlib import Path

import pytest

from agent_bench.grading import grade
from agent_bench.tasks import get_task
from reference_solutions import COMMANDS, reference_files


@pytest.fixture
def assess(tmp_path):
    def run(task_id, command):
        (tmp_path / "solution.sh").write_text(command, encoding="utf-8", newline="\n")
        return grade(get_task(task_id), tmp_path)

    return run


@pytest.mark.parametrize(
    "task_id",
    [
        "hping3-udp",
        "hping3-syn",
        "nmap-port",
        "tcpdump-read",
        "tshark-fields",
        "mqtt-publish",
        "mqtt-subscribe",
        "coap-get",
        "modbus-read",
        "http-get",
        "ssh-uptime",
    ],
)
def test_independent_reference_commands_receive_full_credit(assess, task_id):
    result = assess(task_id, reference_files(task_id)["solution.sh"])
    assert result.outcome == "Full", result.to_dict()
    assert result.passed


@pytest.mark.parametrize(
    ("task_id", "command"),
    [
        ("tcpdump-read", "tcpdump -r capture.pcap -c1"),
        ("tshark-fields", "tshark -r capture.pcap -T fields -e ip.src"),
    ],
)
def test_meaningful_reductions_are_limited_instead_of_full_or_failure(assess, task_id, command):
    result = assess(task_id, command)
    assert result.outcome == "Limited"
    assert not result.passed


@pytest.mark.parametrize(
    ("task_id", "command"),
    [
        ("mqtt-publish", "mosquitto_pub -h192.168.1.10 -p1883 -t site/status -m online -r"),
        ("modbus-read", "mbpoll -p1502 -a1 -0 -r7 -c1 -1 192.168.1.10 50"),
        ("modbus-read", "mbpoll -p1502 -a1 -r7 -c1 -1 192.168.1.10"),
        ("modbus-read", "mbpoll -p1502 -a1 -0 -r7 -c1 192.168.1.10"),
        ("ssh-uptime", "ssh -i operator_id_ed25519 root@192.168.1.10 uptime"),
    ],
)
def test_wrong_operations_or_scope_fail_instead_of_earning_limited_credit(assess, task_id, command):
    result = assess(task_id, command)
    assert result.outcome == "Failure", result.to_dict()
    assert not result.passed


def test_comments_do_not_reduce_credit_and_source_is_never_executed(assess, tmp_path):
    marker = tmp_path / "must-not-exist"
    source = "#!/bin/sh\n# Use only on the supplied host.\nping -c4 192.168.1.10\n"
    assert assess("ping", source).outcome == "Full"
    malicious_source = f"ping -c4 192.168.1.10; touch {marker}"
    assert assess("ping", malicious_source).outcome == "Failure"
    assert not marker.exists()


@pytest.mark.parametrize(
    ("task_id", "source"),
    [
        (
            "mqtt-publish",
            "mosquitto_pub -h192.168.1.10 -p1883 -t site/status -m 'on\\\nline'",
        ),
        (
            "ssh-uptime",
            "ssh -i operator_id_ed25519 -l root -l operator 192.168.1.10 uptime",
        ),
    ],
    ids=[
        "single-quoted-payload-continuation-is-literal",
        "ssh-first-login-is-not-overwritten",
    ],
)
def test_extra_operations_and_literal_shell_boundaries_do_not_earn_credit(assess, task_id, source):
    result = assess(task_id, source)
    assert result.outcome == "Failure", result.to_dict()
    assert not result.passed


@pytest.mark.parametrize(
    "source",
    [
        "",
        "echo 'ping -c4 192.168.1.10'",
        "ping -c4 $target",
    ],
)
def test_missing_refused_or_wrapped_commands_do_not_receive_credit(assess, source):
    assert assess("ping", source).outcome == "Failure"


def test_missing_submission_is_a_model_failure(tmp_path):
    result = grade(get_task("ping"), tmp_path)
    assert result.outcome == "Failure"
    assert result.infrastructure_error is None


def test_noncommand_text_cannot_read_files_outside_submission(tmp_path):
    if not hasattr(Path, "symlink_to"):
        pytest.skip("Symbolic links are unavailable")
    target = tmp_path / "outside.sh"
    target.write_text(COMMANDS["ping"])
    submission = tmp_path / "submission"
    submission.mkdir()
    try:
        (submission / "solution.sh").symlink_to(target)
    except OSError:
        pytest.skip("Creating symbolic links requires platform permission")
    assert grade(get_task("ping"), submission).outcome == "Failure"
