"""Offline checks for the optional Metasploit MQTT adapter."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ATTACKS = ROOT / "scripts/attacks"
sys.path.insert(0, str(ATTACKS))
from mqtt import mqtt_bruteforce_attack as attack  # noqa: E402


@pytest.fixture
def broker_inventory(tmp_path):
    inventory = tmp_path / "initial_devices.yaml"
    inventory.write_text(
        yaml.safe_dump(
            {
                "testbed_id": "fixture",
                "devices": [
                    {
                        "name": "BROKER-A",
                        "address": "10.77.2.10/24",
                        "protocols": ["mqtt"],
                        "environment": {
                            "MQTT_OPERATOR_USERNAME": "admin",
                            "MQTT_OPERATOR_PASSWORD": "test-secret",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return inventory


@pytest.mark.parametrize(
    "console,status,code",
    [
        (
            "[+] MQTT Login Successful: admin/secret\n[*] Auxiliary module execution completed",
            "credential_accepted",
            0,
        ),
        (
            "[+] Does not require authentication\n[*] Auxiliary module execution completed",
            "anonymous_access",
            0,
        ),
        ("[*] Auxiliary module execution completed", "no_credential_accepted", 1),
        ("[-] MQTT LOGIN FAILED: admin/secret", "execution_error", 2),
        (
            "[-] Auxiliary failed: scanner error\n[*] Auxiliary module execution completed",
            "execution_error",
            2,
        ),
    ],
)
def test_metasploit_resource_is_bounded_and_console_secrets_are_hidden(
    monkeypatch, console, status, code
):
    events = []
    seen_paths = []

    def fake_run(command, **kwargs):
        assert command[1:] == ["-q", "-n", "-r", "-"]
        resource = kwargs["input"]
        assert "use auxiliary/scanner/mqtt/connect" in resource
        assert "set RHOSTS 10.77.2.10" in resource
        assert "set RPORT 1883" in resource
        assert "set USERNAME admin" in resource
        assert "set USER_AS_PASS false" in resource
        assert "set STOP_ON_SUCCESS true" in resource
        assert "set SSL false" in resource
        assert "set READ_TIMEOUT 3" in resource
        assert "secret" not in resource
        line = next(
            line for line in resource.splitlines() if line.startswith("set PASS_FILE ")
        )
        path = Path(line.removeprefix('set PASS_FILE "').removesuffix('"'))
        seen_paths.append(path)
        assert path.read_text(encoding="utf-8") == "wrong\nsecret\n"
        if os.name == "posix":
            assert path.stat().st_mode & 0o077 == 0
        return subprocess.CompletedProcess(command, 0, console, "password=secret")

    monkeypatch.setattr(attack.shutil, "which", lambda _: "/usr/bin/msfconsole")
    monkeypatch.setattr(attack.subprocess, "run", fake_run)
    args = SimpleNamespace(
        tls=False,
        tls_ca=None,
        tls_insecure=False,
        timeout=3,
        metasploit_speed=3,
        metasploit_timeout=180,
    )
    run = SimpleNamespace(emit=lambda event, **fields: events.append((event, fields)))
    assert (
        attack.metasploit_scan(
            args, run, "10.77.2.10", 1883, "admin", ["wrong", "secret"]
        )
        == code
    )
    assert events == [
        ("summary", {"engine": "metasploit", "module": attack.MODULE, "status": status})
    ]
    assert "secret" not in str(events)
    assert not seen_paths[0].exists()


def test_metasploit_dry_run_needs_no_binary_or_lab_ack(broker_inventory):
    env = os.environ.copy()
    env.pop("METROPOLIS_LAB_ACK", None)
    result = subprocess.run(
        [
            sys.executable,
            str(ATTACKS / "run.py"),
            "mqtt_bruteforce_attack",
            "--inventory",
            str(broker_inventory),
            "--engine",
            "metasploit",
            "--dry-run",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"engine": "metasploit"' in result.stdout
    assert '"attempts": 25' in result.stdout
    assert "LabOnly_MQTT_2026" not in result.stdout


def test_default_candidates_have_failures_then_lab_success(tmp_path):
    args = SimpleNamespace(engine="metasploit", wordlist=None, max_attempts=25)
    candidates = attack.password_candidates(args, "LabOnly_MQTT_2026")
    assert len(candidates) == len(set(candidates)) == 25
    assert candidates[-1] == "LabOnly_MQTT_2026"
    assert all(value != candidates[-1] for value in candidates[:-1])

    args.max_attempts = 10
    assert attack.password_candidates(args, "LabOnly_MQTT_2026") == candidates[:9] + [
        candidates[-1]
    ]

    args.engine = "paho"
    assert attack.password_candidates(args, "LabOnly_MQTT_2026") == [
        "incorrect-lab-password",
        "LabOnly_MQTT_2026",
    ]

    wordlist = tmp_path / "custom.txt"
    wordlist.write_text("guess1\nguess2\n", encoding="utf-8")
    args.engine = "metasploit"
    args.wordlist = wordlist
    assert attack.password_candidates(args, "LabOnly_MQTT_2026") == ["guess1", "guess2"]


def test_metasploit_timeout_is_recorded_without_console_text(monkeypatch):
    events = []

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1, output="admin/secret")

    monkeypatch.setattr(attack.shutil, "which", lambda _: "/usr/bin/msfconsole")
    monkeypatch.setattr(attack.subprocess, "run", timeout)
    args = SimpleNamespace(
        tls=False,
        tls_ca=None,
        tls_insecure=False,
        timeout=3,
        metasploit_speed=3,
        metasploit_timeout=1,
    )
    run = SimpleNamespace(emit=lambda event, **fields: events.append((event, fields)))
    assert (
        attack.metasploit_scan(args, run, "10.77.2.10", 1883, "admin", ["secret"]) == 2
    )
    assert events[0][1]["status"] == "timeout"
    assert "secret" not in str(events)
