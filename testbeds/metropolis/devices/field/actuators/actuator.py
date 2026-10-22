"""Networked pump-drive or valve-controller with cyclic EtherNet/IP I/O."""

from __future__ import annotations

import json
import os
import socket
import socketserver
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runtime"))

from ethernet_ip import (
    ASSEMBLY_PATH,
    CLASS,
    FORWARD_CLOSE,
    FORWARD_OPEN,
    IO_PORT,
    MANAGER_PATH,
    OPEN_DATA,
    REGISTER_SESSION,
    SEND_RR_DATA,
    cip_from_rr,
    io_packet,
    parse_io_packet,
    receive_packet,
    rr_from_cip,
    send_packet,
)


class State:
    def __init__(self, role: str, faulted: bool = False) -> None:
        if role not in {"pump_drive", "valve_controller"}:
            raise ValueError(f"Unsupported actuator role: {role}")
        self.role = role
        self.faulted = faulted
        self.active = role == "valve_controller" and not faulted
        self.lock = threading.Lock()
        self.connections: dict[int, dict] = {}

    def handle(self, cip: bytes, peer: str = "127.0.0.1") -> bytes:
        service = cip[0] if cip else 0
        if service == FORWARD_OPEN:
            if cip[1:6] != bytes((2,)) + MANAGER_PATH or len(cip) < 6 + OPEN_DATA.size:
                return bytes((service | 0x80, 0, 0x13, 0))
            fields = OPEN_DATA.unpack_from(cip, 6)
            output_id, input_id, serial, rpi_us = (
                fields[2],
                fields[3],
                fields[4],
                fields[8],
            )
            if (
                not 20_000 <= rpi_us <= 5_000_000
                or fields[10] != rpi_us
                or fields[9:12:2] != (0x4207, 0x4208)
                or fields[13] != len(ASSEMBLY_PATH) // 2
                or cip[6 + OPEN_DATA.size :] != ASSEMBLY_PATH
            ):
                return bytes((service | 0x80, 0, 0x13, 0))
            with self.lock:
                self.connections[output_id] = {
                    "input_id": input_id,
                    "serial": serial,
                    "peer": peer,
                    "rpi": rpi_us / 1_000_000,
                    "last": time.monotonic(),
                    "port": None,
                    "next_send": 0.0,
                    "sequence": 0,
                }
            return bytes((service | 0x80, 0, 0, 0)) + struct.pack(
                "<IIHHIIIBB",
                output_id,
                input_id,
                serial,
                0x1337,
                0x4D455452,
                rpi_us,
                rpi_us,
                0,
                0,
            )
        if service == FORWARD_CLOSE:
            if cip[1:6] != bytes((2,)) + MANAGER_PATH or len(cip) < 10:
                return bytes((service | 0x80, 0, 0x13, 0))
            serial = struct.unpack_from("<H", cip, 8)[0]
            with self.lock:
                matched = any(
                    item["serial"] == serial and item["peer"] == peer
                    for item in self.connections.values()
                )
                self.connections = {
                    key: item
                    for key, item in self.connections.items()
                    if item["serial"] != serial or item["peer"] != peer
                }
                if matched:
                    self.active = False
            return bytes((service | 0x80, 0, 0 if matched else 0x01, 0))
        valid_path = len(cip) >= 8 and cip[1:8] == bytes(
            (3, 0x20, CLASS, 0x24, 1, 0x30, 1)
        )
        status = 0 if valid_path and service in (0x0E, 0x10) else 0x08
        if status == 0 and service == 0x10 and len(cip) != 9:
            status = 0x13
        with self.lock:
            if status == 0 and service == 0x10:
                new_active = bool(cip[8]) and not self.faulted
                changed = self.active != new_active
                self.active = new_active
                if changed:
                    print(
                        json.dumps(
                            {
                                "event": "actuator_state_changed",
                                "role": self.role,
                                "active": self.active,
                            }
                        ),
                        flush=True,
                    )
            active = self.active
        return bytes((service | 0x80, 0, status, 0, int(active)))

    def consume_io(self, packet: bytes, peer: str, port: int) -> None:
        connection_id, sequence, data = parse_io_packet(packet)
        if len(data) != 1:
            raise ValueError("Invalid lab actuator output assembly")
        with self.lock:
            connection = self.connections.get(connection_id)
            if connection is None or connection["peer"] != peer:
                raise ValueError("Unknown I/O connection")
            if sequence <= connection.get("last_sequence", -1):
                raise ValueError("Stale I/O sequence")
            connection["last_sequence"] = sequence
            connection["last"] = time.monotonic()
            connection["port"] = port
            changed = self.active != (bool(data[0]) and not self.faulted)
            self.active = bool(data[0]) and not self.faulted
            active = self.active
        if changed:
            print(
                json.dumps(
                    {
                        "event": "actuator_state_changed",
                        "role": self.role,
                        "active": active,
                    }
                ),
                flush=True,
            )

    def due_io(self) -> list[tuple[bytes, tuple[str, int]]]:
        now = time.monotonic()
        outgoing = []
        timed_out = False
        with self.lock:
            for connection_id, connection in list(self.connections.items()):
                if now - connection["last"] > max(4 * connection["rpi"], 0.5):
                    del self.connections[connection_id]
                    self.active = False
                    timed_out = True
                    continue
                if connection["port"] is None or now < connection["next_send"]:
                    continue
                connection["sequence"] += 1
                connection["next_send"] = now + connection["rpi"]
                data = bytes((int(self.active), int(self.faulted)))
                outgoing.append(
                    (
                        io_packet(connection["input_id"], connection["sequence"], data),
                        (connection["peer"], connection["port"]),
                    )
                )
        if timed_out:
            print(
                json.dumps({"event": "actuator_io_timeout", "role": self.role}),
                flush=True,
            )
        return outgoing


def serve_io(
    state: State,
    host: str,
    port: int,
    stop: threading.Event,
    bound_socket: socket.socket | None = None,
) -> None:
    """Consume output assemblies and produce status assemblies every RPI."""
    with bound_socket or socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        if bound_socket is None:
            sock.bind((host, port))
        sock.settimeout(0.01)
        while not stop.is_set():
            try:
                packet, address = sock.recvfrom(2048)
                state.consume_io(packet, address[0], address[1])
            except socket.timeout:
                pass
            except ValueError:
                pass  # A malformed or unassociated datagram must not change state.
            except OSError:
                if not stop.is_set():
                    time.sleep(0.05)
            for packet, address in state.due_io():
                try:
                    sock.sendto(packet, address)
                except OSError:
                    pass  # The watchdog will stop the actuator if updates cease.


class Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        session = 0
        try:
            while True:
                command, incoming_session, payload = receive_packet(self.request)
                if command == REGISTER_SESSION and payload == struct.pack("<HH", 1, 0):
                    session = 1
                    send_packet(self.request, command, session, payload)
                elif (
                    command == SEND_RR_DATA and session and incoming_session == session
                ):
                    answer = self.server.state.handle(
                        cip_from_rr(payload), self.client_address[0]
                    )
                    send_packet(self.request, command, session, rr_from_cip(answer))
                else:
                    break
        except (ConnectionError, OSError, ValueError):
            return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: State) -> None:
        super().__init__(address, Handler)
        self.state = state


def main() -> None:
    state = State(
        os.getenv("ACTUATOR_ROLE", "pump_drive"),
        os.getenv("ACTUATOR_FAULTED", "false").lower() == "true",
    )
    host = os.getenv("EIP_HOST", "0.0.0.0")
    port = int(os.getenv("EIP_PORT", "44818"))
    io_port = int(os.getenv("EIP_IO_PORT", str(IO_PORT)))
    stop = threading.Event()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as io_socket:
        io_socket.bind((host, io_port))
        worker = threading.Thread(
            target=serve_io, args=(state, host, io_port, stop, io_socket), daemon=True
        )
        with Server((host, port), state) as server:
            worker.start()
            print(
                json.dumps(
                    {
                        "event": "actuator_started",
                        "role": state.role,
                        "tcp_port": port,
                        "udp_port": io_port,
                    }
                ),
                flush=True,
            )
            try:
                server.serve_forever()
            finally:
                stop.set()
                worker.join(timeout=1)


if __name__ == "__main__":
    main()
