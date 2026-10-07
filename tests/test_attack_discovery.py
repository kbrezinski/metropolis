"""Loopback checks for the bounded availability probe and discovery sweep.

Both drivers generate hostile-shaped traffic, so these tests focus on the
limits that keep a lab experiment bounded, plus direct loopback checks that the
probe helpers really observe a live listener.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ATTACKS = ROOT / "scripts/attacks"


def invoke(name, *args, ack=True):
    env = os.environ.copy()
    env.pop("METROPOLIS_LAB_ACK", None)
    for key in list(env):
        if key.startswith("METROPOLIS_"):
            env.pop(key)
    if ack:
        env["METROPOLIS_LAB_ACK"] = "yes"
    return subprocess.run(
        [sys.executable, str(ATTACKS / name), *map(str, args)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def records(result):
    return [
        json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
    ]


def load_driver(name, filename):
    """Load a driver module, adding scripts/attacks so its helpers import."""
    inserted = str(ATTACKS)
    if inserted not in sys.path:
        sys.path.insert(0, inserted)
    spec = importlib.util.spec_from_file_location(name, ATTACKS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tcp_listener():
    """A real loopback listener the probe helpers can reach."""
    accepted = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            accepted.append(self.client_address)

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = Server(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield server.server_address[1], accepted
    server.shutdown()
    server.server_close()
    worker.join(timeout=2)


def test_bounded_probe_resolves_ot_nodes():
    """TCP load targets the PLC; UDP load targets the CoAP sensor."""
    connect = records(invoke("run_bounded_hping3.py", "--dry-run", ack=False))[0]
    assert connect["host"] == "10.20.10.10"
    assert connect["port"] == 502
    assert connect["mode"] == "connect"

    udp = records(
        invoke("run_bounded_hping3.py", "--mode", "udp", "--dry-run", ack=False)
    )[0]
    assert udp["host"] == "10.20.10.20"
    assert udp["port"] == 5683


def test_bounded_probe_refuses_unbounded_settings():
    """Every ceiling is enforced before any traffic is sent."""
    assert invoke("run_bounded_hping3.py", "--rate", "99999").returncode == 2
    assert invoke("run_bounded_hping3.py", "--duration", "99999").returncode == 2
    assert invoke("run_bounded_hping3.py", "--max-packets", "99999999").returncode == 2
    assert invoke("run_bounded_hping3.py", "--payload-size", "0").returncode == 2
    # A public target is refused by the private-address check.
    assert (
        invoke("run_bounded_hping3.py", "--host", "8.8.8.8", "--dry-run").returncode
        == 2
    )
    assert invoke("run_bounded_hping3.py", ack=False).returncode == 2


def test_bounded_probe_reaches_a_live_listener(tcp_listener):
    """The availability probe reports a real listening socket as open."""
    port, _ = tcp_listener
    probe = load_driver("bounded_probe_driver", "run_bounded_hping3.py")
    assert probe.connect_probe("127.0.0.1", port, 2.0) == "open"
    # Nothing listens on the reserved port below, so it must not report open.
    assert probe.connect_probe("127.0.0.1", 1, 0.5) != "open"


def test_bounded_probe_counts_stay_within_the_cap():
    """--max-packets is a ceiling: the run never exceeds it.

    The shared scheduler also declines to catch up after a slow probe, so a
    slow target legitimately yields fewer packets than the cap.
    """
    result = invoke(
        "run_bounded_hping3.py",
        "--host",
        "127.0.0.1",
        "--port",
        "1",
        "--rate",
        "50",
        "--duration",
        "1",
        "--max-packets",
        "4",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = next(item for item in records(result) if item["event"] == "summary")
    assert 1 <= summary["packets"] <= 4
    assert sum(summary["outcomes"].values()) == summary["packets"]


def test_discovery_defaults_to_inventory_and_documented_ports():
    """The sweep covers inventoried hosts and the documented listener set."""
    start = records(
        invoke(
            "run_iot_discovery_simulation.py",
            "--protocol",
            "both",
            "--dry-run",
            ack=False,
        )
    )[0]
    assert "10.20.10.10" in start["hosts"]
    assert "10.99.10.10" in start["hosts"]
    assert len(start["hosts"]) <= 256
    assert "tcp/502" in start["ports"]
    assert "udp/5683" in start["ports"]


def test_discovery_probe_helpers_observe_a_live_listener(tcp_listener):
    """TCP opens are reported; a closed reserved port is not."""
    port, _ = tcp_listener
    discovery = load_driver("discovery_driver", "run_iot_discovery_simulation.py")
    assert discovery.tcp_probe("127.0.0.1", port, 2.0) is True
    assert discovery.tcp_probe("127.0.0.1", 1, 0.5) is False


def test_discovery_refuses_public_and_oversized_ranges():
    """Explicit ranges must stay private and no larger than 256 addresses."""
    assert (
        invoke(
            "run_iot_discovery_simulation.py", "--cidr", "8.8.8.0/24", "--dry-run"
        ).returncode
        == 2
    )
    assert (
        invoke(
            "run_iot_discovery_simulation.py", "--cidr", "10.20.0.0/16", "--dry-run"
        ).returncode
        == 2
    )
    assert (
        invoke(
            "run_iot_discovery_simulation.py", "--max-probes", "99999999", "--dry-run"
        ).returncode
        == 2
    )
    assert invoke("run_iot_discovery_simulation.py", ack=False).returncode == 2


def test_discovery_node_selection_narrows_the_sweep():
    """--node narrows the sweep to one inventoried host."""
    start = records(
        invoke(
            "run_iot_discovery_simulation.py",
            "--node",
            "MET-PLC-INTAKE-01",
            "--dry-run",
            ack=False,
        )
    )[0]
    assert start["hosts"] == ["10.20.10.10"]


def test_discovery_rejects_an_unknown_node():
    result = invoke(
        "run_iot_discovery_simulation.py", "--node", "MET-NOPE-01", "--dry-run"
    )
    assert result.returncode == 2
    assert "not in the inventory" in result.stdout
