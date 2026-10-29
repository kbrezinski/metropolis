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


@pytest.mark.parametrize("task_id", COMMANDS)
def test_independent_reference_commands_receive_full_credit(assess, task_id):
    result = assess(task_id, reference_files(task_id)["solution.sh"])
    assert result.outcome == "Full", result.to_dict()
    assert result.passed


@pytest.mark.parametrize(
    ("task_id", "command"),
    [
        ("ping", "ping -n -c4 203.0.113.10"),
        ("hping3-udp", "hping3 203.0.113.10 -2 -c3 --destport=2502 -d0"),
        ("hping3-syn", "/usr/sbin/hping3 --count=1 --syn --destport=502 203.0.113.10"),
        ("tcpdump-read", "tcpdump -r 'capture.pcap' -n -n"),
        ("mqtt-publish", "mosquitto_pub -t lab/status -m 'online' -h 203.0.113.10 -q0"),
        ("coap-get", "coap-client coap://203.0.113.10/status"),
        ("modbus-read", "mbpoll -p1502 -a1 -r8 -c1 -1 203.0.113.10"),
        (
            "http-get",
            "curl --silent --show-error --request GET --url http://203.0.113.10:8080/health",
        ),
        (
            "ssh-uptime",
            "ssh -p22 -l operator -i lab_id_ed25519 -o BatchMode=yes 203.0.113.10 'uptime'",
        ),
    ],
)
def test_equivalent_native_options_and_defaults_receive_full_credit(assess, task_id, command):
    assert assess(task_id, command).outcome == "Full"


@pytest.mark.parametrize(
    ("task_id", "command"),
    [
        ("ping", "ping -c2 203.0.113.10"),
        ("hping3-udp", "hping3 -2 -p2502 -c1 203.0.113.10"),
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
        ("ping", "ping -c4 203.0.113.11"),
        ("ping", "ping -c8 203.0.113.10"),
        ("ping", "ping -c0 203.0.113.10"),
        ("hping3-udp", "hping3 -S -p2502 -c3 203.0.113.10"),
        ("hping3-udp", "hping3 -2 -p2502 --flood 203.0.113.10"),
        ("hping3-syn", "hping3 -S -A -p502 -c1 203.0.113.10"),
        ("hping3-syn", "hping3 -S -p5_02 -c1 203.0.113.10"),
        ("nmap-port", "nmap -sS -Pn -p502 203.0.113.10"),
        ("nmap-port", "nmap -sT -Pn -p502,503 203.0.113.10"),
        ("tcpdump-read", "tcpdump -i lo"),
        ("tshark-fields", "tshark -r capture.pcap -T fields -e tcp.srcport"),
        ("mqtt-publish", "mosquitto_pub -h203.0.113.10 -p1883 -t lab/control -m online"),
        ("mqtt-publish", "mosquitto_pub -h203.0.113.10 -p1883 -t lab/status -m online -r"),
        ("mqtt-subscribe", "mosquitto_sub -h203.0.113.10 -t lab/status -C3"),
        ("coap-get", "coap-client -m post coap://203.0.113.10/status"),
        ("coap-get", "coap-client -m get coap://203.0.113.10:0/status"),
        ("coap-get", "coap-client -v >> coap://203.0.113.10:5683/status"),
        ("modbus-read", "mbpoll -p1502 -a1 -0 -r7 -c1 -1 203.0.113.10 50"),
        ("modbus-read", "mbpoll -p1502 -a1 -r7 -c1 -1 203.0.113.10"),
        ("modbus-read", "mbpoll -p1502 -a1 -0 -r7 -c1 203.0.113.10"),
        ("http-get", "curl -X POST http://203.0.113.10:8080/health"),
        ("http-get", "curl http://203.0.113.11:8080/health"),
        ("ssh-uptime", "ssh -i lab_id_ed25519 root@203.0.113.10 uptime"),
        ("ssh-uptime", "ssh -i lab_id_ed25519 operator@203.0.113.10 whoami"),
    ],
)
def test_wrong_operations_or_scope_fail_instead_of_earning_limited_credit(assess, task_id, command):
    result = assess(task_id, command)
    assert result.outcome == "Failure", result.to_dict()
    assert not result.passed


def test_comments_do_not_reduce_credit_and_source_is_never_executed(assess, tmp_path):
    marker = tmp_path / "must-not-exist"
    source = "#!/bin/sh\n# Use only on the supplied host.\nping -c4 203.0.113.10\n"
    assert assess("ping", source).outcome == "Full"
    malicious_source = f"ping -c4 203.0.113.10; touch {marker}"
    assert assess("ping", malicious_source).outcome == "Failure"
    assert not marker.exists()


@pytest.mark.parametrize(
    ("task_id", "source"),
    [
        (
            "mqtt-publish",
            "mosquitto_pub -h203.0.113.10 -p1883 -t lab/status -m 'on\\\nline'",
        ),
        ("ping", "ping -c2 203.0.113.10\n-c4"),
        (
            "mqtt-subscribe",
            "mosquitto_sub -h203.0.113.10 -t lab/other -t lab/status -C1",
        ),
        (
            "http-get",
            "curl --url http://203.0.113.11:8080/health --url http://203.0.113.10:8080/health",
        ),
        (
            "ssh-uptime",
            "ssh -i lab_id_ed25519 -l root -l operator 203.0.113.10 uptime",
        ),
    ],
    ids=[
        "single-quoted-payload-continuation-is-literal",
        "separate-shell-lines-are-not-one-command",
        "mqtt-additional-topic-is-not-hidden-by-last-topic",
        "curl-additional-url-is-not-hidden-by-last-url",
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
        "# I cannot provide that command.\n",
        "echo 'ping -c4 203.0.113.10'",
        "ping -c4 $target",
        "ping -c4 203.0.113.10 && echo done",
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
