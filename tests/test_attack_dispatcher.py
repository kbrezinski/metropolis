"""Checks for the single-entry-point dispatcher in scripts/attacks.

The dispatcher discovers tools from the directory and forwards arguments to
them, so these tests pin the discovery rules and the forwarding behaviour.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ATTACKS = ROOT / "scripts/attacks"
DISPATCHER = ATTACKS / "run.py"

# Helper modules carry protocol and inventory logic, never a CLI.
HELPERS = {"_common", "_mqtt", "_telnet", "_c2", "run"}

EXPECTED_TOOLS = {
    "coap_amplification_attack",
    "mirai_infection_sim",
    "modbus_manipulation_attack",
    "mqtt_bruteforce_attack",
    "mqtt_flood_attack",
    "network_recon_scan",
    "run_bounded_hping3",
    "run_iot_discovery_simulation",
    "run_merlin",
    "run_mirai_choreography",
    "ssh_bruteforce_attack",
}


def invoke(*args, ack=False):
    env = os.environ.copy()
    env.pop("METROPOLIS_LAB_ACK", None)
    for key in list(env):
        if key.startswith("METROPOLIS_"):
            env.pop(key)
    if ack:
        env["METROPOLIS_LAB_ACK"] = "yes"
    return subprocess.run(
        [sys.executable, str(DISPATCHER), *map(str, args)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def records(result):
    return [
        json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
    ]


def test_dispatcher_lists_every_tool_and_no_helpers():
    """Discovery finds the CLI modules and skips the shared helpers."""
    result = invoke("--help")
    assert result.returncode == 0
    listed = {
        line.strip()
        for line in result.stdout.splitlines()
        if line.startswith("  ") and line.strip() and " " not in line.strip()
    }
    assert listed == EXPECTED_TOOLS
    assert not (listed & HELPERS)
    # Every listed tool exists as a module.
    for name in listed:
        assert (ATTACKS / f"{name}.py").is_file()


def test_dispatcher_forwards_arguments_to_the_module():
    """A dispatched dry run behaves exactly like invoking the module directly."""
    result = invoke("mqtt_bruteforce_attack", "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    start = records(result)[0]
    assert start["tool"] == "mqtt_credentials"
    assert start["dry_run"] is True


def test_dispatcher_forwards_flagged_arguments():
    """Positional and flagged arguments reach the target module intact."""
    result = invoke("run_bounded_hping3", "--mode", "udp", "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    start = records(result)[0]
    assert start["mode"] == "udp"
    assert start["host"] == "10.20.10.20"


def test_dispatcher_rejects_unknown_tools_with_a_suggestion():
    """An unknown tool fails with exit 2 and a close-match suggestion."""
    result = invoke("mqtt_brute", "--dry-run")
    assert result.returncode == 2
    assert "unknown tool" in result.stderr
    assert "mqtt_bruteforce_attack" in result.stderr


def test_dispatcher_requires_a_tool_name():
    """No arguments prints usage on stderr and exits 2."""
    result = invoke()
    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_dispatcher_preserves_the_lab_acknowledgement_gate():
    """The gate lives in the tools, and dispatch must not bypass it."""
    assert invoke("mqtt_bruteforce_attack").returncode == 2
    with_ack = invoke("mqtt_bruteforce_attack", "--dry-run", ack=True)
    assert with_ack.returncode == 0


def test_dispatcher_passes_through_help():
    """'<tool> --help' reaches the module's own argument parser."""
    result = invoke("modbus_manipulation_attack", "--help")
    assert result.returncode == 0
    assert "--action" in result.stdout


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_every_tool_is_reachable_through_the_dispatcher(name):
    """Every discovered tool responds to --help through the dispatcher."""
    result = invoke(name, "--help")
    assert result.returncode == 0, f"{name}: {result.stdout}{result.stderr}"
