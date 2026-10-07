"""Offline CLI checks and protocol integration on loopback only."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "scripts/attacks"


def invoke(name, *args, ack=True):
    env = os.environ.copy()
    env.pop("METROPOLIS_LAB_ACK", None)
    for key in list(env):
        if key.startswith("METROPOLIS_"):
            env.pop(key)
    if ack:
        env["METROPOLIS_LAB_ACK"] = "yes"
    return subprocess.run(
        [sys.executable, str(TOOLS / name), *map(str, args)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


def records(result):
    return [
        json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
    ]


@pytest.mark.parametrize(
    "name,args",
    [
        ("coap_amplification_attack.py", []),
        ("mqtt_bruteforce_attack.py", []),
        ("mqtt_flood_attack.py", []),
        ("modbus_manipulation_attack.py", ["--action", "set-level"]),
        ("network_recon_scan.py", ["--protocol", "both"]),
        ("ssh_bruteforce_attack.py", []),
        ("mirai_infection_sim.py", []),
    ],
)
def test_dry_runs_resolve_inventory_without_ack(name, args):
    result = invoke(name, *args, "--dry-run", ack=False)
    assert result.returncode == 0, result.stdout + result.stderr
    start = records(result)[0]
    assert start["dry_run"] is True
    if name == "ssh_bruteforce_attack.py":
        assert start["host"] == "10.20.21.10"
    if name == "mirai_infection_sim.py":
        assert start["hosts"] == ["10.20.10.30"]
    if name == "network_recon_scan.py":
        ports = start["command"][start["command"].index("-p") + 1]
        assert "U:53,123,5683" in ports
    assert "LabOnly_" not in result.stdout


def test_missing_ack_and_invalid_settings_fail_before_network():
    assert invoke("mqtt_bruteforce_attack.py", ack=False).returncode == 2
    assert (
        invoke(
            "coap_amplification_attack.py", "--host", "8.8.8.8", "--dry-run"
        ).returncode
        == 2
    )
    assert (
        invoke("coap_amplification_attack.py", "--rate", "nan", "--dry-run").returncode
        == 2
    )
    assert (
        invoke(
            "coap_amplification_attack.py", "--mode", "spoof", "--dry-run"
        ).returncode
        == 2
    )
    assert (
        invoke(
            "network_recon_scan.py", "--cidr", "10.20.0.0/16", "--dry-run"
        ).returncode
        == 2
    )


def test_future_inventory_supplies_addresses_and_ports(tmp_path):
    import yaml

    path = (
        ROOT
        / "testbeds/metropolis/datasets/water_treatment_v1/device_instances/initial_devices.yaml"
    )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    sensor = next(
        item for item in data["devices"] if item["name"] == "MET-SENSOR-INTAKE-01"
    )
    sensor["address"] = "10.20.10.25/24"
    sensor["environment"]["COAP_PORT"] = "5684"
    alternate = tmp_path / "inventory.yaml"
    alternate.write_text(yaml.safe_dump(data), encoding="utf-8")
    result = invoke(
        "coap_amplification_attack.py", "--inventory", alternate, "--dry-run"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert records(result)[0]["host"] == "10.20.10.25"
    assert records(result)[0]["port"] == 5684


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_coap_uses_the_real_sensor_on_loopback():
    sensor = load_file(
        "sensor_for_attack_test",
        ROOT / "testbeds/metropolis/devices/field/sensors/coap_sensor.py",
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.1)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                packet, peer = sock.recvfrom(2048)
            except socket.timeout:
                continue
            reply = sensor.handle_get(packet, "MET-SENSOR-INTAKE-01", "level", 65)
            if reply:
                sock.sendto(reply, peer)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        result = invoke(
            "coap_amplification_attack.py",
            "--host",
            "127.0.0.1",
            "--port",
            sock.getsockname()[1],
            "--duration",
            "0.5",
            "--max-messages",
            "2",
            "--rate",
            "10",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        summary = next(item for item in records(result) if item["event"] == "summary")
        assert summary["received"] == summary["sent"] == 2
        assert summary["matched_payload_ratio"] > 0
    finally:
        stop.set()
        worker.join(timeout=2)
        sock.close()


@pytest.fixture
def tcp_peer():
    servers = []

    def start(handler):
        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        server = Server(("127.0.0.1", 0), handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        servers.append((server, worker))
        return server.server_address[1]

    yield start
    for server, worker in servers:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def read_exact(sock, size):
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


def mqtt_packet(sock):
    header = read_exact(sock, 1)[0]
    remaining = 0
    multiplier = 1
    while True:
        value = read_exact(sock, 1)[0]
        remaining += (value & 127) * multiplier
        if not value & 128:
            break
        multiplier *= 128
    return header, read_exact(sock, remaining)


def test_mqtt_rejections_and_pubacks(tcp_peer):
    pytest.importorskip("paho.mqtt.client")
    publications = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            try:
                _, body = mqtt_packet(self.request)
                accepted = b"LabOnly_MQTT_2026" in body
                self.request.sendall(b"\x20\x02\x00" + bytes([0 if accepted else 5]))
                if not accepted:
                    return
                while True:
                    header, body = mqtt_packet(self.request)
                    if header >> 4 == 14:
                        return
                    if header >> 4 == 3:
                        length = int.from_bytes(body[:2], "big")
                        publications.append((body[2 : 2 + length], bool(header & 1)))
                        self.request.sendall(
                            b"\x40\x02" + body[2 + length : 4 + length]
                        )
            except (EOFError, OSError):
                return

    port = tcp_peer(Handler)
    result = invoke(
        "mqtt_bruteforce_attack.py",
        "--host",
        "127.0.0.1",
        "--port",
        port,
        "--interval",
        "0.01",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    attempts = [item for item in records(result) if item["event"] == "attempt"]
    assert [item["status"] for item in attempts] == ["rejected", "accepted"]
    assert "LabOnly_MQTT_2026" not in result.stdout
    result = invoke(
        "mqtt_flood_attack.py",
        "--host",
        "127.0.0.1",
        "--port",
        port,
        "--mode",
        "retain",
        "--max-messages",
        "1",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert publications == [(b"metropolis/experiment/telemetry", True)]


def test_modbus_actual_controller_map_and_restoration():
    pytest.importorskip("pymodbus")
    from pymodbus.client import ModbusTcpClient

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    env = {**os.environ, "MODBUS_HOST": "127.0.0.1", "MODBUS_PORT": str(port)}
    process = subprocess.Popen(
        [
            sys.executable,
            str(
                ROOT
                / "testbeds/metropolis/devices/controllers/plc/modbus_controller.py"
            ),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client = ModbusTcpClient("127.0.0.1", port=port, timeout=1, retries=0)
    try:
        for _ in range(50):
            if client.connect():
                break
            if process.poll() is not None:
                pytest.fail("Controller startup failed")
            time.sleep(0.1)
        # The process model owns registers 0-2 and drives them from the pump, so
        # these are live operating values, not the seed the registers start at.
        first = client.read_holding_registers(0, count=3, slave=1)
        time.sleep(1.0)
        second = client.read_holding_registers(0, count=3, slave=1)
        assert first.registers != second.registers, first.registers
        assert 0 <= first.registers[0] <= 1000, first.registers
        before_level = first.registers[0]
        result = invoke(
            "modbus_manipulation_attack.py",
            "--host",
            "127.0.0.1",
            "--port",
            port,
            "--action",
            "set-level",
            "--value",
            "700",
            "--restore",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert any(item.get("observed") == 700 for item in records(result))
        # The write is a real perturbation now, so the process steers the level
        # back towards its baseline instead of holding the written value.
        after_level = client.read_holding_registers(0, count=1, slave=1).registers[0]
        assert after_level != 700
        assert abs(after_level - before_level) <= 400
        result = invoke(
            "modbus_manipulation_attack.py",
            "--host",
            "127.0.0.1",
            "--port",
            port,
            "--action",
            "force-pump",
            "--restore",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert client.read_coils(0, count=1, slave=1).bits[0] is False
    finally:
        client.close()
        process.terminate()
        process.wait(timeout=5)


def test_telnet_negotiation_and_challenge(tcp_peer):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            try:
                # Fragment a WILL ECHO sequence across TCP chunks.
                self.request.sendall(b"\xff\xfb")
                self.request.sendall(b"\x01login: ")
                assert read_exact(self.request, 3) == b"\xff\xfd\x01"
                reader = self.request.makefile("rb")
                assert reader.readline().strip() == b"root"
                self.request.sendall(b"Password: ")
                password = reader.readline().strip()
                if password != b"LabOnly_Legacy_2026":
                    self.request.sendall(b"Login incorrect\r\n")
                    return
                self.request.sendall(b"lab:~# ")
                command = reader.readline().strip()
                halves = re.findall(rb"'([a-z0-9_]+)'", command)
                token = b"".join(halves[-2:])
                self.request.sendall(command + b"\r\n" + token + b"\r\nlab:~# ")
            except (EOFError, OSError):
                return

    port = tcp_peer(Handler)
    result = invoke(
        "mirai_infection_sim.py",
        "--host",
        "127.0.0.1",
        "--port",
        port,
        "--interval",
        "0.01",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    attempts = [item for item in records(result) if item["event"] == "attempt"]
    assert attempts[0]["status"] == "rejected"
    assert attempts[1]["command_verified"] is True


def test_telnet_echo_alone_does_not_prove_execution(tcp_peer, tmp_path):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            reader = self.request.makefile("rb")
            self.request.sendall(b"login: ")
            reader.readline()
            self.request.sendall(b"Password: ")
            reader.readline()
            self.request.sendall(b"lab:~# ")
            # Echo the submitted command but never execute its printf.
            self.request.sendall(reader.readline())

    port = tcp_peer(Handler)
    wordlist = tmp_path / "passwords.txt"
    wordlist.write_text("LabOnly_Legacy_2026\n", encoding="utf-8")
    result = invoke(
        "mirai_infection_sim.py",
        "--host",
        "127.0.0.1",
        "--port",
        port,
        "--wordlist",
        wordlist,
        "--timeout",
        "0.5",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    summary = next(item for item in records(result) if item["event"] == "summary")
    assert summary["command_verified_hosts"] == 0


def test_ssh_rejection_and_fixed_command(tcp_peer):
    paramiko = pytest.importorskip("paramiko")
    host_key = paramiko.RSAKey.generate(2048)

    class Auth(paramiko.ServerInterface):
        def __init__(self):
            self.ready = threading.Event()
            self.command = None

        def check_auth_password(self, username, password):
            return (
                paramiko.AUTH_SUCCESSFUL
                if username == "engineer" and password == "LabOnly_Engineering_2026"
                else paramiko.AUTH_FAILED
            )

        def get_allowed_auths(self, username):
            return "password"

        def check_channel_request(self, kind, chanid):
            return (
                paramiko.OPEN_SUCCEEDED
                if kind == "session"
                else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
            )

        def check_channel_exec_request(self, channel, command):
            self.command = command
            self.ready.set()
            return True

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            # The tool makes two attempts in quick succession, and the first
            # connection is still tearing down while the second arrives. Give
            # the negotiation generous timeouts so this harness, rather than the
            # tool, absorbs that latency: when a negotiation loses its
            # connection mid-handshake the client reports no verified command,
            # which would fail the test even though the tool behaved correctly.
            transport = paramiko.Transport(self.request)
            auth = Auth()
            try:
                transport.add_server_key(host_key)
                transport.start_server(server=auth)
                channel = transport.accept(10)
                if channel is not None and auth.ready.wait(10):
                    token = re.findall(rb"'([a-z0-9_]+)'", auth.command)[-1]
                    channel.sendall(token + b"\n")
                    channel.send_exit_status(0)
                    channel.close()
                    # Let the client read the reply before the transport goes
                    # away. Closing immediately resets the connection while the
                    # client is still reading, which surfaces as a socket error
                    # rather than a verified command.
                    time.sleep(0.2)
            except (EOFError, OSError, paramiko.SSHException):
                pass
            finally:
                transport.close()

    port = tcp_peer(Handler)
    result = invoke(
        "ssh_bruteforce_attack.py",
        "--host",
        "127.0.0.1",
        "--port",
        port,
        "--interval",
        "0.01",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    attempts = [item for item in records(result) if item["event"] == "attempt"]
    assert attempts[0]["status"] == "rejected"
    assert attempts[1]["command_verified"] is True
