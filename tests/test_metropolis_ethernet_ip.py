"""Wire-level checks for the intake actuator simulation."""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "testbeds/metropolis/devices/runtime"
ACTUATORS = ROOT / "testbeds/metropolis/devices/field/actuators"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(ACTUATORS))

from actuator import Server, State, serve_io  # noqa: E402
from ethernet_ip import (  # noqa: E402
    CyclicActuator,
    get_status,
    io_packet,
    parse_io_packet,
    set_command,
)


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
            state.faulted = True
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not client.faulted:
                time.sleep(0.02)
            assert client.faulted
            assert client.snapshot() == (False, True)
            state.faulted = False
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and client.faulted:
                time.sleep(0.02)
            assert not client.faulted
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
