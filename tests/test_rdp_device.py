"""Reusable RDP lab device behavior, independent of the reference topology."""

from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEVICES = ROOT / "testbeds/metropolis/devices/operations"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_synthetic_udp_replies_and_expired_amplification_lease(tmp_path):
    udp = load("rdp_udp_test", DEVICES / "rdp_server/rdp_udp.py")
    state = tmp_path / "udp-mode.json"
    assert udp.NORMAL_REPLY == b"MRDP-ACK" + bytes(8)
    assert udp.current_mode(state) == "benign"
    assert udp.response(bytes(16), "10.8.1.2", "benign") == udp.NORMAL_REPLY
    assert udp.response(bytes(16), "10.8.1.2", "benign", 123)[8:] == (123).to_bytes(
        8, "big"
    )
    assert udp.response(bytes(16), "8.8.8.8", "amplify") is None
    assert udp.response(bytes(15), "10.8.1.2", "amplify") is None

    assert udp.set_mode("amplify", 60, state) == {"mode": "amplify", "verified": True}
    assert len(udp.response(bytes(16), "10.8.1.2", udp.current_mode(state))) == 512
    assert udp.current_mode(state, now=time.time() + 61) == "benign"
    assert "expires_utc_epoch" in json.loads(state.read_text(encoding="utf-8"))
    state.write_text(
        json.dumps({"mode": "amplify", "expires_monotonic": 999999999}),
        encoding="utf-8",
    )
    assert udp.current_mode(state) == "benign"
    state.write_text(
        json.dumps({"mode": "amplify", "expires_utc_epoch": float("nan")}),
        encoding="utf-8",
    )
    assert udp.current_mode(state) == "benign"
    assert udp.set_mode("benign", None, state) == {"mode": "benign", "verified": True}


def test_synthetic_udp_wire_stays_small_until_explicitly_armed(tmp_path):
    udp = load("rdp_udp_wire_test", DEVICES / "rdp_server/rdp_udp.py")
    state = tmp_path / "udp-mode.json"
    stop = threading.Event()
    ready = threading.Event()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
        tcp.bind(("127.0.0.1", 0))
        tcp.listen()
        port = tcp.getsockname()[1]
        assert udp.check_ready(tcp_port=port, udp_port=port) == {
            "tcp_ready": True,
            "udp_ready": False,
        }
        worker = threading.Thread(
            target=udp.serve, args=("127.0.0.1", port, state, stop, ready), daemon=True
        )
        worker.start()
        try:
            assert ready.wait(2)
            assert udp.check_ready(tcp_port=port, udp_port=port) == {
                "tcp_ready": True,
                "udp_ready": True,
            }
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.settimeout(2)
                client.sendto(bytes(16), ("127.0.0.1", port))
                assert len(client.recv(1024)) == 16
                udp.set_mode("amplify", 60, state)
                client.sendto(bytes(16), ("127.0.0.1", port))
                assert len(client.recv(1024)) == 512
        finally:
            stop.set()
            worker.join(timeout=2)
        assert not worker.is_alive()
        assert udp.check_ready(tcp_port=port, udp_port=port) == {
            "tcp_ready": True,
            "udp_ready": False,
        }


@pytest.mark.parametrize(("udp_ready", "exit_code"), [(False, 1), (True, 0)])
def test_rdp_readiness_command_reports_both_services(
    monkeypatch, capsys, udp_ready, exit_code
):
    udp = load("rdp_udp_ready_test", DEVICES / "rdp_server/rdp_udp.py")
    monkeypatch.setattr(sys, "argv", ["rdp_udp.py", "ready"])
    monkeypatch.setattr(
        udp,
        "check_ready",
        lambda **_kwargs: {"tcp_ready": True, "udp_ready": udp_ready},
    )
    assert udp.main() == exit_code
    assert json.loads(capsys.readouterr().out) == {
        "tcp_ready": True,
        "udp_ready": udp_ready,
    }


@pytest.mark.parametrize(
    ("final_marker", "expected_status"), [(1, "complete"), (0, "failed")]
)
def test_client_keeps_password_private_and_checks_desktop_session(
    monkeypatch, final_marker, expected_status
):
    client = load("rdp_client_test", DEVICES / "rdp_client/rdp_client.py")
    monkeypatch.setenv("RDP_USERNAME", "lab_admin")
    monkeypatch.setenv("RDP_PASSWORD", "lab-secret")
    monkeypatch.setenv("NODE_HOSTNAME", "CLIENT-EXAMPLE")
    seen = {}

    class Input:
        def write(self, data):
            seen["stdin"] = data

        def close(self):
            pass

    class Process:
        pid = 1234
        stdin = Input()

        def poll(self):
            return None

    def spawn(argv, **kwargs):
        seen["argv"] = argv
        seen["env"] = kwargs["env"]
        return Process()

    monkeypatch.setattr(client.subprocess, "Popen", spawn)
    monkeypatch.setattr(client, "_stop_process", lambda _process: None)
    sample_count = 0

    def sample(_host):
        nonlocal sample_count
        sample_count += 1
        marker = 0 if sample_count == 1 else final_marker
        return b"MRDP-ACK" + marker.to_bytes(8, "big")

    monkeypatch.setattr(client, "sample_udp", sample)
    monkeypatch.setattr(
        client,
        "probe_udp",
        lambda _host, _stop: {
            "udp_sent": 1,
            "udp_received": 1,
            "udp_response_bytes": 16,
        },
    )
    result = client.run_session("10.8.1.9", 0.25)
    assert result["status"] == expected_status
    assert result["tcp_success"] is (expected_status == "complete")
    if expected_status == "failed":
        assert result["reason"] == "authentication_not_observed"
    assert result["source"] == "CLIENT-EXAMPLE"
    assert result["target_host"] == "10.8.1.9"
    assert "lab-secret" in seen["stdin"]
    assert "/cert:ignore" in seen["stdin"]
    assert "lab-secret" not in str(seen["argv"])
    assert "RDP_PASSWORD" not in seen["env"]
    assert "lab-secret" not in str(result)


def test_client_interrupt_stops_freerdp_and_reports_failure(monkeypatch):
    client = load("rdp_client_interrupt_test", DEVICES / "rdp_client/rdp_client.py")
    monkeypatch.setenv("RDP_USERNAME", "lab_admin")
    monkeypatch.setenv("RDP_PASSWORD", "lab-secret")
    stopped = []
    handlers = {}

    class Process:
        pid = 1234
        stdin = type(
            "Input",
            (),
            {"write": lambda _self, _value: None, "close": lambda _self: None},
        )()

        def poll(self):
            return None

    monkeypatch.setattr(client.subprocess, "Popen", lambda *_args, **_kwargs: Process())
    monkeypatch.setattr(client, "sample_udp", lambda _host: b"MRDP-ACK" + bytes(8))
    monkeypatch.setattr(
        client, "_stop_process", lambda process: stopped.append(process)
    )
    monkeypatch.setattr(client.signal, "getsignal", lambda signum: f"old-{signum}")
    monkeypatch.setattr(
        client.signal,
        "signal",
        lambda signum, handler: handlers.__setitem__(signum, handler),
    )

    def interrupt(_seconds):
        handlers[client.signal.SIGTERM](client.signal.SIGTERM, None)

    monkeypatch.setattr(client.time, "sleep", interrupt)
    result = client.run_session("10.8.1.9", 30)
    assert result["status"] == "failed"
    assert result["reason"] == "interrupted"
    assert len(stopped) == 1
    assert handlers[client.signal.SIGTERM] == f"old-{client.signal.SIGTERM}"


def test_xrdp_requires_credentials_and_tls(tmp_path, monkeypatch):
    server = load("rdp_server_test", DEVICES / "rdp_server/start_rdp.py")
    config = tmp_path / "xrdp.ini"
    config.write_text(
        "security_layer=negotiate\n#require_credentials=true\nautorun=\n"
        "certificate=\nkey_file=\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "XRDP_CONFIG", config)
    server.configure_xrdp(tmp_path / "cert.pem", tmp_path / "key.pem")
    text = config.read_text(encoding="utf-8")
    assert "security_layer=tls" in text
    assert "require_credentials=true" in text
    assert "autorun=Xorg" in text
    assert f"certificate={tmp_path / 'cert.pem'}" in text


@pytest.fixture
def supervisor(monkeypatch, tmp_path):
    server = load("rdp_supervisor_test", DEVICES / "rdp_server/start_rdp.py")
    monkeypatch.delenv("TELEMETRY_ENABLED", raising=False)
    monkeypatch.setattr(server, "setup_account", lambda: None)
    monkeypatch.setattr(
        server,
        "setup_certificate",
        lambda: (tmp_path / "rdp-cert.pem", tmp_path / "rdp-key.pem"),
    )
    monkeypatch.setattr(server, "configure_xrdp", lambda *_args: None)
    monkeypatch.setattr(server, "Path", lambda _value: tmp_path)
    handlers = {}
    previous = {
        server.signal.SIGTERM: "previous-term",
        server.signal.SIGINT: "previous-int",
    }

    def register(signum, handler):
        old = handlers.get(signum, previous[signum])
        handlers[signum] = handler
        return old

    monkeypatch.setattr(server.signal, "signal", register)
    return server, handlers, previous


class SupervisedProcess:
    def __init__(self, command, *, exit_code=None, stubborn=False):
        self.command = command
        self.returncode = exit_code
        self.stubborn = stubborn
        self.terminated = self.killed = self.waited = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if not self.stubborn:
            self.returncode = 0

    def wait(self, timeout=None):
        if self.stubborn and not self.killed:
            raise subprocess.TimeoutExpired(self.command, timeout)
        self.waited = True
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


@pytest.mark.parametrize("relay_enabled", [False, True])
def test_rdp_supervisor_stops_all_selected_services_on_signal(
    supervisor, monkeypatch, relay_enabled
):
    server, handlers, previous = supervisor
    if relay_enabled:
        monkeypatch.setenv("TELEMETRY_ENABLED", "true")
    children = []

    def start(command):
        process = SupervisedProcess(command, stubborn=len(children) == 1)
        children.append(process)
        return process

    monkeypatch.setattr(server.subprocess, "Popen", start)
    monkeypatch.setattr(
        server.time,
        "sleep",
        lambda _seconds: handlers[server.signal.SIGTERM](server.signal.SIGTERM, None),
    )
    assert server.main() == 0
    assert len(children) == (4 if relay_enabled else 3)
    assert (
        any("/opt/metropolis/relay.py" in child.command for child in children)
        is relay_enabled
    )
    assert all(child.terminated and child.waited for child in children)
    assert children[1].killed
    assert handlers == previous


@pytest.mark.parametrize(
    ("exited_service", "exit_code"),
    [("/opt/metropolis/relay.py", 1), ("/usr/sbin/xrdp", 0)],
)
def test_rdp_supervisor_reports_unexpected_exit_and_cleans_siblings(
    supervisor, monkeypatch, capsys, exited_service, exit_code
):
    server, handlers, previous = supervisor
    monkeypatch.setenv("TELEMETRY_ENABLED", "true")
    children = []

    def start(command):
        # A clean child exit also means the requested long-running service is lost.
        process = SupervisedProcess(
            command, exit_code=exit_code if exited_service in command else None
        )
        children.append(process)
        return process

    monkeypatch.setattr(server.subprocess, "Popen", start)
    assert server.main() == 1
    assert len(children) == 4
    assert all(child.waited for child in children)
    assert all(
        child.terminated for child in children if exited_service not in child.command
    )
    failed = next(
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if '"event": "rdp_service_failed"' in line
    )
    assert failed["service"] == (
        "telemetry-relay" if exited_service.endswith("relay.py") else "xrdp"
    )
    assert failed["exit_code"] == exit_code
    assert handlers == previous


def test_relay_spawn_failure_cleans_existing_rdp_services(
    supervisor, monkeypatch, capsys
):
    server, handlers, previous = supervisor
    monkeypatch.setenv("TELEMETRY_ENABLED", "true")
    children = []

    def start(command):
        if "/opt/metropolis/relay.py" in command:
            raise OSError("relay could not start")
        child = SupervisedProcess(command)
        children.append(child)
        return child

    monkeypatch.setattr(server.subprocess, "Popen", start)
    assert server.main() == 1
    assert len(children) == 3
    assert all(child.terminated and child.waited for child in children)
    assert json.loads(capsys.readouterr().out)["error"] == "OSError"
    assert handlers == previous


def test_invalid_relay_selection_fails_before_starting_rdp(
    supervisor, monkeypatch, capsys
):
    server, handlers, previous = supervisor
    monkeypatch.setenv("TELEMETRY_ENABLED", "perhaps")
    monkeypatch.setattr(
        server.subprocess,
        "Popen",
        lambda *_args: pytest.fail("invalid config started a service"),
    )
    assert server.main() == 1
    assert json.loads(capsys.readouterr().out)["error"] == "ValueError"
    assert handlers == previous
