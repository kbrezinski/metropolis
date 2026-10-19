"""Loopback checks for the bounded availability probe and discovery sweep.

Both drivers generate hostile-shaped traffic, so these tests focus on the
limits that keep a lab experiment bounded, plus direct loopback checks that the
probe helpers really observe a live listener.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

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


@pytest.mark.parametrize(
    ("mode", "host", "port"),
    [
        ("udp-raw", "10.20.10.20", 5683),
        ("udp-vse", "10.20.10.20", 5683),
        ("tcp-syn", "10.20.10.10", 502),
        ("tcp-ack", "10.20.10.10", 502),
        ("tcp-ack-psh", "10.20.10.10", 502),
    ],
)
def test_raw_profiles_resolve_inventoried_targets(mode, host, port):
    result = invoke("run_bounded_hping3.py", "--mode", mode, "--dry-run", ack=False)
    assert result.returncode == 0, result.stdout + result.stderr
    start = records(result)[0]
    assert (start["host"], start["port"]) == (host, port)


def test_raw_packet_shapes_and_rotating_tcp_ports():
    from scapy.layers.inet import IP, TCP, UDP
    from scapy.packet import Raw

    driver = load_driver("raw_hping_driver", "run_bounded_hping3.py")
    syn = driver.raw_packet("tcp-syn", "10.20.10.10", 502, 1, 64)
    ack = driver.raw_packet("tcp-ack", "10.20.10.10", 502, 2, 64)
    psh = driver.raw_packet("tcp-ack-psh", "10.20.10.10", 502, 3, 64)
    vse = driver.raw_packet("udp-vse", "10.20.10.20", 5683, 0, 64)
    assert syn[IP].dst == "10.20.10.10"
    assert (syn[TCP].flags, syn[TCP].dport) == ("S", 503)
    assert (ack[TCP].flags, ack[TCP].dport) == ("A", 504)
    assert (psh[TCP].flags, psh[TCP].dport) == ("PA", 505)
    assert len(bytes(psh[Raw].load)) == 64
    assert vse[UDP].dport == 5683
    assert bytes(vse[Raw].load) == driver.VSE_QUERY


def test_raw_flood_remains_bounded_and_inventory_scoped():
    tool = "run_bounded_hping3.py"
    assert invoke(tool, "--mode", "tcp-syn", "--max-packets", "3001").returncode == 2
    assert invoke(tool, "--mode", "tcp-syn", "--duration", "31").returncode == 2
    assert invoke(tool, "--mode", "tcp-syn", "--rate", "101").returncode == 2
    assert invoke(tool, "--mode", "connect", "--flood").returncode == 2
    assert invoke(tool, "--mode", "tcp-syn", "--max-packets", "0").returncode == 2
    assert invoke(tool, "--mode", "tcp-syn", "--host", "10.20.10.99").returncode == 2
    flood = invoke(tool, "--mode", "tcp-syn", "--flood", "--dry-run", ack=False)
    assert flood.returncode == 0, flood.stdout + flood.stderr
    assert records(flood)[0]["flood"] is True


def test_raw_flood_stops_after_the_packet_ceiling(monkeypatch, capsys):
    driver = load_driver("raw_flood_driver", "run_bounded_hping3.py")
    sent = []
    monkeypatch.setattr(driver, "raw_packet", lambda *args: args)
    monkeypatch.setattr(driver, "send_raw", sent.append)
    monkeypatch.setenv("METROPOLIS_LAB_ACK", "yes")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_bounded_hping3.py",
            "--mode",
            "tcp-syn",
            "--flood",
            "--duration",
            "1",
            "--max-packets",
            "3",
        ],
    )
    assert driver.main() == 0
    summary = next(
        item
        for item in (json.loads(line) for line in capsys.readouterr().out.splitlines())
        if item["event"] == "summary"
    )
    assert len(sent) == summary["packets"] == 3


def test_raw_flood_stops_at_duration(monkeypatch, capsys):
    driver = load_driver("raw_flood_time_driver", "run_bounded_hping3.py")
    sent = []
    ticks = iter((0.0, 0.1, 1.1, 1.2))
    monkeypatch.setattr(driver, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    monkeypatch.setattr(driver, "raw_packet", lambda *args: args)
    monkeypatch.setattr(driver, "send_raw", sent.append)
    monkeypatch.setenv("METROPOLIS_LAB_ACK", "yes")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_bounded_hping3.py",
            "--mode",
            "tcp-syn",
            "--flood",
            "--duration",
            "1",
            "--max-packets",
            "100",
        ],
    )
    assert driver.main() == 0
    summary = next(
        item
        for item in (json.loads(line) for line in capsys.readouterr().out.splitlines())
        if item["event"] == "summary"
    )
    assert len(sent) == summary["packets"] == 1


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
            "--dry-run",
            ack=False,
        )
    )[0]
    assert start["protocol"] == "both"
    assert "10.20.10.10" in start["hosts"]
    assert "10.99.10.10" in start["hosts"]
    assert len(start["hosts"]) <= 256
    assert "tcp/502" in start["ports"]
    assert "tcp/8883" in start["ports"]
    assert "tcp/48101" in start["ports"]
    assert "udp/69" in start["ports"]
    assert "udp/5683" in start["ports"]
    assert start["planned"] == len(start["hosts"]) * len(start["ports"])


def test_discovery_probe_helpers_observe_a_live_listener(tcp_listener):
    """TCP opens are reported; a closed reserved port is not."""
    port, _ = tcp_listener
    discovery = load_driver("discovery_driver", "run_iot_discovery_simulation.py")
    assert discovery.tcp_probe("127.0.0.1", port, 2.0) == "open"
    assert discovery.tcp_probe("127.0.0.1", 1, 0.5) in {"closed", "no-response"}


def test_discovery_reports_complete_and_partial_coverage(tcp_listener):
    port, accepted = tcp_listener
    arguments = (
        "--host",
        "127.0.0.1",
        "--protocol",
        "tcp",
        "--tcp-ports",
        str(port),
        "--passes",
        "2",
        "--rate",
        "100",
        "--workers",
        "2",
    )
    complete = invoke("run_iot_discovery_simulation.py", *arguments)
    assert complete.returncode == 0, complete.stdout + complete.stderr
    summary = next(row for row in records(complete) if row["event"] == "summary")
    assert summary["complete"] is True
    assert (summary["planned"], summary["attempted"], summary["untested"]) == (2, 2, 0)
    assert summary["live_services"] == {"127.0.0.1": [f"tcp/{port}"]}
    assert len(accepted) >= 2

    partial = invoke("run_iot_discovery_simulation.py", *arguments, "--max-probes", "1")
    assert partial.returncode == 1
    summary = next(row for row in records(partial) if row["event"] == "summary")
    assert summary["complete"] is False
    assert (summary["planned"], summary["attempted"], summary["untested"]) == (2, 1, 1)


def test_discovery_coap_probe_uses_real_sensor_response():
    sensor_path = ROOT / "testbeds/metropolis/devices/field/sensors/coap_sensor.py"
    sensor = load_driver("coap_for_discovery", sensor_path)
    discovery = load_driver("coap_discovery_driver", "run_iot_discovery_simulation.py")

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            request, sock = self.request
            response = sensor.handle_get(request, "MET-SENSOR-01", "level", 65.0)
            if response:
                sock.sendto(response, self.client_address)

    class Server(socketserver.ThreadingUDPServer):
        daemon_threads = True

    server = Server(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        assert (
            discovery.udp_probe("127.0.0.1", server.server_address[1], 1, "coap")
            == "open"
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_discovery_accepts_tftp_transfer_port_reply():
    discovery = load_driver("tftp_discovery_driver", "run_iot_discovery_simulation.py")

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            request, _ = self.request
            if request.startswith(b"\x00\x01"):
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as transfer:
                    transfer.sendto(b"\x00\x05\x00\x01missing\x00", self.client_address)

    class Server(socketserver.ThreadingUDPServer):
        daemon_threads = True

    server = Server(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        assert (
            discovery.udp_probe("127.0.0.1", server.server_address[1], 1, "tftp")
            == "open"
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize("kind", ["dns", "ntp"])
def test_discovery_recognizes_dns_and_ntp_replies(kind):
    discovery = load_driver(
        f"{kind}_discovery_driver", "run_iot_discovery_simulation.py"
    )

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            request, sock = self.request
            reply = (
                request[:2] + b"\x81\x80" + request[4:]
                if kind == "dns"
                else b"\x24" + b"\x00" * 47
            )
            sock.sendto(reply, self.client_address)

    class Server(socketserver.ThreadingUDPServer):
        daemon_threads = True

    server = Server(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        assert (
            discovery.udp_probe("127.0.0.1", server.server_address[1], 1, kind)
            == "open"
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


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
    assert (
        invoke(
            "run_iot_discovery_simulation.py", "--max-probes", "0", "--dry-run"
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
