"""Wire-level and failover checks for the intake actuator simulation."""

from __future__ import annotations

import importlib.util
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "testbeds/metropolis/devices/runtime"
ACTUATORS = ROOT / "testbeds/metropolis/devices/field/actuators"
PLC = ROOT / "testbeds/metropolis/devices/controllers/plc"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(ACTUATORS))
sys.path.insert(0, str(PLC))

from actuator import Server, State, serve_io  # noqa: E402
from ethernet_ip import (  # noqa: E402
    CyclicActuator,
    get_status,
    io_packet,
    parse_io_packet,
    set_command,
)


def controller_module():
    spec = importlib.util.spec_from_file_location(
        "intake_controller", PLC / "modbus_controller.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actuator_cip_diagnostics_roundtrip() -> None:
    with Server(("127.0.0.1", 0), State("pump_drive")) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        port = server.server_address[1]
        assert get_status("127.0.0.1", port) is False
        assert set_command("127.0.0.1", True, port) is True
        assert get_status("127.0.0.1", port) is True
        assert set_command("127.0.0.1", False, port) is False
        server.shutdown()
        worker.join()


def test_io_packet_has_sequenced_cpf_items_and_rejects_malformed() -> None:
    packet = io_packet(0x12345678, 7, b"\x01")
    assert packet[:6] == b"\x02\x00\x02\x80\x08\x00"
    assert parse_io_packet(packet) == (0x12345678, 7, b"\x01")
    with pytest.raises(ValueError):
        parse_io_packet(packet[:-1])
    with pytest.raises(ValueError):
        parse_io_packet(b"bad")


def test_valve_starts_open() -> None:
    assert State("valve_controller").active is True


def test_faulted_drive_rejects_run_command() -> None:
    state = State("pump_drive", faulted=True)
    assert state.handle(bytes((0x10, 3, 0x20, 0x64, 0x24, 1, 0x30, 1, 1)))[4] == 0


def test_cyclic_io_uses_udp_and_closes_to_safe_state() -> None:
    state = State("pump_drive")
    cycles = []
    consume = state.consume_io

    def count_packet(packet, peer, port):
        consume(packet, peer, port)
        cycles.append(time.monotonic())

    state.consume_io = count_packet
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        io_port = probe.getsockname()[1]
    stop = threading.Event()
    with Server(("127.0.0.1", 0), state) as server:
        tcp_worker = threading.Thread(target=server.serve_forever, daemon=True)
        io_worker = threading.Thread(
            target=serve_io, args=(state, "127.0.0.1", io_port, stop), daemon=True
        )
        tcp_worker.start()
        io_worker.start()
        client = CyclicActuator(
            "127.0.0.1", rpi_ms=50, tcp_port=server.server_address[1], io_port=io_port
        )
        client.command(True)
        client.start()
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and client.snapshot() != (True, True):
                time.sleep(0.02)
            assert client.snapshot() == (True, True)
            assert state.active
            time.sleep(0.18)
            assert len(cycles) >= 3
            client.command(False)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and client.snapshot()[0]:
                time.sleep(0.02)
            assert client.snapshot() == (False, True)
        finally:
            client.close()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and state.active:
                time.sleep(0.02)
            assert not state.active
            stop.set()
            server.shutdown()
            tcp_worker.join()
            io_worker.join()


def test_lost_cyclic_updates_stop_actuator() -> None:
    state = State("pump_drive")
    state.connections[42] = {
        "input_id": 43,
        "serial": 1,
        "peer": "127.0.0.1",
        "rpi": 0.05,
        "last": time.monotonic(),
        "port": 12345,
        "next_send": 0,
        "sequence": 0,
    }
    state.consume_io(io_packet(42, 1, b"\x01"), "127.0.0.1", 12345)
    assert state.active
    state.connections[42]["last"] -= 1
    assert state.due_io() == []
    assert not state.active
    assert not state.connections


class StubActuator:
    def __init__(self, active: bool, healthy: bool = True) -> None:
        self.active = active
        self.healthy = healthy
        self.requested: bool | None = None

    def command(self, value: bool) -> None:
        self.requested = value

    def snapshot(self) -> tuple[bool, bool]:
        return self.active, self.healthy


@pytest.mark.parametrize("duty_status", [(False, False), (False, True)])
def test_plc_fails_over_to_standby(duty_status) -> None:
    module = controller_module()
    context = module.make_context([650, 120, 950, 0])
    context[0].setValues(module.COILS, 0, [1, 1])
    duty = StubActuator(*duty_status)
    standby = StubActuator(True)
    valve = StubActuator(True)
    assert module.update_actuators(context, duty, standby, valve) is True
    assert duty.requested is True
    assert standby.requested is True
    assert valve.requested is True
    assert context[0].getValues(2, 0, 3) == [0, 1, 1]


def test_plc_keeps_standby_idle_when_duty_runs() -> None:
    module = controller_module()
    context = module.make_context([650, 120, 950, 0])
    context[0].setValues(module.COILS, 0, [1, 1])
    duty, standby, valve = StubActuator(True), StubActuator(False), StubActuator(True)
    assert module.update_actuators(context, duty, standby, valve) is True
    assert standby.requested is False
    assert context[0].getValues(2, 0, 3) == [1, 0, 1]
