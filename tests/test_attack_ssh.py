"""Offline bounds and dry-run checks for the SSH credential experiment."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ATTACKS = ROOT / "scripts/attacks"
sys.path.insert(0, str(ATTACKS))
from ssh import ssh_bruteforce_attack as attack  # noqa: E402


def test_default_candidates_are_distinct_and_success_is_last(tmp_path):
    good = "LabOnly_Engineering_2026"
    candidates = attack.password_candidates(None, good, 25)
    assert len(candidates) == len(set(candidates)) == 25
    assert candidates[-1] == good
    assert good not in candidates[:-1]
    assert attack.password_candidates(None, good, 5) == candidates[:4] + [good]

    wordlist = tmp_path / "passwords.txt"
    wordlist.write_text("first\nsecond\n", encoding="utf-8")
    assert attack.password_candidates(wordlist, good, 25) == ["first", "second"]
    with pytest.raises(ValueError, match="max-attempts"):
        attack.password_candidates(None, good, 1001)


def test_dry_run_reports_limits_without_credential_or_lab_ack():
    env = os.environ.copy()
    env.pop("METROPOLIS_LAB_ACK", None)
    result = subprocess.run(
        [sys.executable, str(ATTACKS / "run.py"), "ssh_bruteforce_attack", "--dry-run"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"attempts": 25' in result.stdout
    assert '"rate": 2.0' in result.stdout
    assert '"duration": 120' in result.stdout
    assert '"post_login_commands": 3' in result.stdout
    assert "LabOnly_Engineering_2026" not in result.stdout


@pytest.mark.parametrize(
    "option,value",
    [("--rate", "101"), ("--duration", "601"), ("--max-attempts", "1001")],
)
def test_hard_ceiling_rejects_oversized_dry_run(option, value):
    result = subprocess.run(
        [
            sys.executable,
            str(ATTACKS / "run.py"),
            "ssh_bruteforce_attack",
            "--dry-run",
            option,
            value,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
