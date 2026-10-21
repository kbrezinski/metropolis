"""Offline integration of the Merlin scenario with the actual inert agent model."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ATTACKS = ROOT / "scripts/attacks"
MERLIN = ROOT / "testbeds/metropolis/devices/attack/merlin"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def await_port(port):
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"Port {port} did not open")


def records(result):
    return [
        json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
    ]


def test_real_agent_reports_command_and_upload(tmp_path):
    control_port = free_port()
    http_port = free_port()
    inventory = tmp_path / "initial_devices.yaml"
    inventory.write_text(
        yaml.safe_dump(
            {
                "testbed_id": "loopback",
                "devices": [
                    {
                        "name": "CNC",
                        "address": "127.0.0.1/8",
                        "protocols": ["merlin-control"],
                        "environment": {
                            "MERLIN_CONTROL_PORT": str(control_port),
                            "MERLIN_HTTP_PORT": str(http_port),
                        },
                    },
                    {
                        "name": "AGENT",
                        "address": "127.0.0.1/8",
                        "protocols": ["merlin-agent"],
                        "environment": {},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["METROPOLIS_LAB_ACK"] = "yes"
    cnc = subprocess.Popen(
        [sys.executable, str(MERLIN / "cnc/start_merlin_cnc.py")],
        env={
            **env,
            "MERLIN_BIND": "127.0.0.1",
            "MERLIN_CONTROL_PORT": str(control_port),
            "MERLIN_HTTP_PORT": str(http_port),
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    agent = None
    try:
        await_port(control_port)
        await_port(http_port)
        agent = subprocess.Popen(
            [sys.executable, str(MERLIN / "agent/start_merlin_agent.py")],
            env={
                **env,
                "MERLIN_CNC_URL": f"http://127.0.0.1:{http_port}",
                "AGENT_SLEEP": "1",
            },
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(2):
            result = subprocess.run(
                [
                    sys.executable,
                    str(ATTACKS / "run.py"),
                    "merlin_scenario",
                    "--inventory",
                    str(inventory),
                    "--wait-seconds",
                    "10",
                    "--poll-interval",
                    "0.1",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert any(
                row["event"] == "agent_command_result" and row["ok"]
                for row in records(result)
            )
            assert any(row["event"] == "agent_upload" for row in records(result))
            assert records(result)[-2]["upload_verified"] is True
    finally:
        if agent is not None:
            agent.terminate()
            agent.wait(timeout=5)
        cnc.terminate()
        cnc.wait(timeout=5)


def test_dry_run_resolves_default_agent_without_contacting_lab():
    env = os.environ.copy()
    env.pop("METROPOLIS_LAB_ACK", None)
    result = subprocess.run(
        [sys.executable, str(ATTACKS / "run.py"), "merlin_scenario", "--dry-run"],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert records(result)[0]["agent"] == "10.99.10.21"
