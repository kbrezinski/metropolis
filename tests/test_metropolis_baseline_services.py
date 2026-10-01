"""Check the routine Modbus cycle and time packet parsing without GNS3."""

from __future__ import annotations

import importlib.util
import socket
import struct
import sys
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
