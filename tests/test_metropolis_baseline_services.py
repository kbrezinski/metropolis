"""Check the routine Modbus cycle and time packet parsing without GNS3."""

from __future__ import annotations

import importlib.util
import logging
import socket
import struct
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest


CLIENT_FILE = (
    Path(__file__).resolve().parents[1]
    / "testbeds/metropolis/devices/operations/control_client/control_client.py"
)
spec = importlib.util.spec_from_file_location("control_client", CLIENT_FILE)
assert spec and spec.loader
control_client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control_client)


def test_control_cycle_reads_writes_and_confirms(monkeypatch):
    calls = []

    class Response:
        def __init__(self, registers=None, bits=None):
            self.registers = registers
            self.bits = bits

        def isError(self):
            return False

    class FakeModbusClient:
        def __init__(self, host, port, timeout, retries):
            calls.append(("open", host, port, timeout, retries))

        def connect(self):
            return True

        def read_holding_registers(self, address, count, slave):
            calls.append(("read", address, count, slave))
            return Response(registers=[650] if count == 1 else [650, 120, 950, 1])

        def write_register(self, address, value, slave):
            calls.append(("register", address, value, slave))
            return Response()

        def write_coil(self, address, value, slave):
            calls.append(("coil", address, value, slave))
            return Response()

        def read_coils(self, address, count, slave):
            calls.append(("read_coil", address, count, slave))
            return Response(bits=[True])

        def close(self):
            calls.append(("close",))

    package = types.ModuleType("pymodbus")
    client_module = types.ModuleType("pymodbus.client")
    client_module.ModbusTcpClient = FakeModbusClient
    monkeypatch.setitem(sys.modules, "pymodbus", package)
    monkeypatch.setitem(sys.modules, "pymodbus.client", client_module)

    assert control_client.control_cycle("10.20.10.10", 502, 1, 650, True) == (
        650,
        650,
        True,
    )
    assert ("register", 0, 650, 1) in calls
    assert ("coil", 0, True, 1) in calls
    assert calls[-1] == ("close",)


def test_ntp_packet_and_response(monkeypatch):
    sent = []
    reply = bytearray(48)
    reply[0] = 0x24  # NTP v4 server response.
    struct.pack_into("!II", reply, 40, 2208988800 + 100, 0)

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def settimeout(self, timeout):
            assert timeout == 2

        def sendto(self, data, address):
            sent.append((data, address))

        def recvfrom(self, size):
            assert size == 512
            return bytes(reply), ("10.20.30.11", 123)

    monkeypatch.setattr(socket, "socket", lambda *args: FakeSocket())
    assert control_client.sample_ntp("10.20.30.11") == 100
    assert sent[0][0] == bytes([0x23]) + bytes(47)
    assert sent[0][1] == ("10.20.30.11", 123)


def test_ntp_rejects_incomplete_response(monkeypatch):
    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def settimeout(self, timeout):
            pass

        def sendto(self, data, address):
            pass

        def recvfrom(self, size):
            return b"bad", ("10.20.30.11", 123)

    monkeypatch.setattr(socket, "socket", lambda *args: FakeSocket())
    with pytest.raises(ValueError, match="Invalid NTP"):
        control_client.sample_ntp("10.20.30.11")


def test_snmp_target_parser_and_read_only_poll(monkeypatch):
    assert control_client.snmp_targets("core=10.0.0.1,remote=10.0.0.2") == [
        ("core", "10.0.0.1"),
        ("remote", "10.0.0.2"),
    ]
    with pytest.raises(ValueError, match="Invalid SNMP target"):
        control_client.snmp_targets("core=10.0.0.1,core=10.0.0.2")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command, 0, "Timeticks: (123) 0:00:01.23\n", ""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert "123" in control_client.sample_snmp("10.0.0.1", "lab-read-only")
    command, kwargs = calls[0]
    assert command[:3] == ["snmpget", "-v2c", "-c"]
    assert command[-2:] == ["10.0.0.1", "1.3.6.1.2.1.1.3.0"]
    assert kwargs["timeout"] == 4


def test_snmp_poll_survives_modbus_failure_without_logging_community(
    monkeypatch, caplog
):
    class EndCycle(Exception):
        pass

    monkeypatch.setenv("SNMP_TARGETS", "core=10.0.0.1")
    monkeypatch.setenv("SNMP_COMMUNITY", "private-lab-string")
    monkeypatch.setattr(control_client, "lookup_plc", lambda *args: "10.0.0.2")
    monkeypatch.setattr(
        control_client,
        "control_cycle",
        lambda *args: (_ for _ in ()).throw(ConnectionError("PLC unavailable")),
    )
    calls = []

    def failing_poll(host, community):
        calls.append((host, community))
        raise RuntimeError("request with private-lab-string failed")

    monkeypatch.setattr(control_client, "sample_snmp", failing_poll)
    monkeypatch.setattr(
        control_client.time, "sleep", lambda _: (_ for _ in ()).throw(EndCycle)
    )
    with pytest.raises(EndCycle):
        control_client.main()
    assert calls == [("10.0.0.1", "private-lab-string")]
    assert "SNMP poll failed: router=core" in caplog.text
    assert "private-lab-string" not in caplog.text


def test_coap_status_get_uses_a_real_udp_exchange(monkeypatch):
    monkeypatch.setattr(control_client.secrets, "randbelow", lambda _: 0x1234)
    received = []
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    server.settimeout(3)

    def reply():
        request, peer = server.recvfrom(1024)
        received.append(request)
        server.sendto(b'\x60\x45\x12\x34\xc1\x32\xff{"value":1}', peer)

    thread = threading.Thread(target=reply)
    thread.start()
    try:
        result = control_client.sample_coap("127.0.0.1", server.getsockname()[1])
    finally:
        thread.join(timeout=4)
        server.close()
    assert not thread.is_alive()
    assert received == [b"\x40\x01\x12\x34\xb6status"]
    assert result == ("2.05", 11, 18)


def test_coap_response_must_match_the_request(monkeypatch):
    monkeypatch.setattr(control_client.secrets, "randbelow", lambda _: 0x1234)
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    server.settimeout(3)

    def reply():
        _, peer = server.recvfrom(1024)
        server.sendto(b"\x60\x45\x56\x78", peer)

    thread = threading.Thread(target=reply)
    thread.start()
    try:
        with pytest.raises(ValueError, match="Invalid CoAP"):
            control_client.sample_coap("127.0.0.1", server.getsockname()[1])
    finally:
        thread.join(timeout=4)
        server.close()
    assert not thread.is_alive()


def test_coap_poll_runs_even_when_modbus_fails(monkeypatch, caplog):
    class EndCycle(Exception):
        pass

    monkeypatch.setenv("COAP_HOST", "192.0.2.4")
    monkeypatch.setenv("COAP_TARGET_NODE", "TEST-SENSOR")
    monkeypatch.setenv("NODE_HOSTNAME", "TEST-CLIENT")
    monkeypatch.setenv("SNMP_TARGETS", "")
    monkeypatch.setattr(
        control_client,
        "lookup_plc",
        lambda *args: (_ for _ in ()).throw(ConnectionError("PLC unavailable")),
    )
    calls = []

    def poll(host, port):
        calls.append((host, port))
        return "2.05", 11, 18

    monkeypatch.setattr(control_client, "sample_coap", poll)
    monkeypatch.setattr(
        control_client.time, "sleep", lambda _: (_ for _ in ()).throw(EndCycle)
    )
    caplog.set_level(logging.INFO, logger="metropolis.control_client")
    with pytest.raises(EndCycle):
        control_client.main()
    assert calls == [("192.0.2.4", 5683)]
    assert (
        "source=TEST-CLIENT target=TEST-SENSOR resource=/status code=2.05"
        in caplog.text
    )
