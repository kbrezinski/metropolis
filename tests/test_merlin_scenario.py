"""Offline integration of the Merlin scenario with the actual inert agent model."""

from __future__ import annotations

import json
import importlib.util
import os
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import pytest
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


def test_merlin_certificate_uses_selected_dataset_identity(tmp_path):
    dataset = tmp_path / "custom_dataset"
    inventory = dataset / "device_instances" / "initial_devices.yaml"
    inventory.parent.mkdir(parents=True)
    inventory.write_text(
        yaml.safe_dump(
            {
                "devices": [
                    {
                        "name": "LAB-CONTROL",
                        "address": "10.77.4.20/24",
                        "protocols": ["merlin-control"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location(
        "merlin_identity_test", ROOT / "scripts/gen_lab_certificates.py"
    )
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    names, addresses = generator.merlin_identity(dataset)
    assert names == ["LAB-CONTROL"]
    assert addresses == ["10.77.4.20"]
    files = generator.generate(tmp_path / "certs", names, addresses)
    assert b"PRIVATE KEY" not in files["server"].read_bytes()
    assert b"PRIVATE KEY" in files["key"].read_bytes()


def test_agent_attribution_rejects_claimed_address_with_other_peer():
    if str(ATTACKS) not in sys.path:
        sys.path.insert(0, str(ATTACKS))
    if str(ATTACKS / "merlin") not in sys.path:
        sys.path.insert(0, str(ATTACKS / "merlin"))
    spec = importlib.util.spec_from_file_location(
        "merlin_attribution_test", ATTACKS / "merlin/merlin_scenario.py"
    )
    scenario = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scenario)

    class Console:
        def command(self, _):
            return [
                json.dumps(
                    {
                        "agent_id": "fake",
                        "address": "10.77.4.21",
                        "peer_address": "10.77.4.99",
                    }
                ),
                "END",
            ]

    assert scenario.matching_agent(Console(), "10.77.4.21") is None


def test_real_agent_reports_command_and_transfer(tmp_path):
    control_port = free_port()
    http_port = free_port()
    marker_path = tmp_path / "agent" / "hping3.marker"
    cert_module = ROOT / "scripts/gen_lab_certificates.py"
    spec = importlib.util.spec_from_file_location(
        "merlin_test_certificates", cert_module
    )
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    certs = generator.generate(tmp_path / "certs", ["localhost"], ["127.0.0.1"])
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
                        "environment": {
                            "MERLIN_CNC_URL": f"https://127.0.0.1:{http_port}",
                            "MERLIN_MARKER_PATH": str(marker_path),
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["METROPOLIS_LAB_ACK"] = "yes"
    agent = subprocess.Popen(
        [sys.executable, str(MERLIN / "agent/start_merlin_agent.py")],
        env={
            **env,
            "MERLIN_CNC_URL": f"https://127.0.0.1:{http_port}",
            "MERLIN_CA_FILE": str(certs["ca"]),
            "MERLIN_MARKER_PATH": str(marker_path),
            "AGENT_SLEEP": "1",
            "AGENT_STARTUP_TIMEOUT": "20",
            "AGENT_UPLOAD_ONCE": "false",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.2)
    cnc = subprocess.Popen(
        [sys.executable, str(MERLIN / "cnc/start_merlin_cnc.py")],
        env={
            **env,
            "MERLIN_BIND": "127.0.0.1",
            "MERLIN_CONTROL_PORT": str(control_port),
            "MERLIN_HTTP_PORT": str(http_port),
            "MERLIN_TLS_CERT_FILE": str(certs["server"]),
            "MERLIN_TLS_KEY_FILE": str(certs["key"]),
            "MERLIN_MARKER_PATH": str(marker_path),
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        await_port(control_port)
        await_port(http_port)
        helper_spec = importlib.util.spec_from_file_location(
            "merlin_test_http", ATTACKS / "merlin/run_merlin.py"
        )
        helper = importlib.util.module_from_spec(helper_spec)
        helper_spec.loader.exec_module(helper)
        other_ca = generator.generate(
            tmp_path / "other-certs", ["localhost"], ["127.0.0.1"]
        )["ca"]
        with pytest.raises(ssl.SSLCertVerificationError):
            helper.http(
                "127.0.0.1", http_port, "GET", "/status/unknown", b"", 3, other_ca
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
                    "--ca-file",
                    str(certs["ca"]),
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
            assert any(
                row["event"] == "agent_checkin_observed"
                and row["peer_address"] == "127.0.0.1"
                for row in records(result)
            )
            assert any(row["event"] == "tool_transfer" for row in records(result))
            assert records(result)[-2]["transfer_verified"] is True
            assert marker_path.read_bytes() == b"metropolis-inert-hping3-marker\n"
    finally:
        agent.terminate()
        agent.wait(timeout=5)
        cnc.terminate()
        cnc.wait(timeout=5)
