"""Lab EtherNet/IP explicit setup and cyclic, unicast I/O primitives.

The I/O assembly is lab-specific; this is not a vendor drive profile or an
ODVA-conformance implementation. The UDP common packet format is wire-shaped
like EtherNet/IP Class 1 I/O so captures contain real cyclic traffic.
"""

from __future__ import annotations

import socket
import struct
import secrets
import threading
import time
import logging

PORT = 44818
IO_PORT = 2222
REGISTER_SESSION = 0x65
SEND_RR_DATA = 0x6F
CLASS = 0x64
PATH = bytes((0x20, CLASS, 0x24, 1, 0x30, 1))
MANAGER_PATH = bytes((0x20, 6, 0x24, 1))
ASSEMBLY_PATH = bytes((0x20, 4, 0x24, 1, 0x2C, 100, 0x2C, 101))
FORWARD_OPEN = 0x54
FORWARD_CLOSE = 0x4E
HEADER = struct.Struct("<HHII8sI")
OPEN_DATA = struct.Struct("<BBIIHHIB3xIHIHBB")
IO_PREFIX = struct.Struct("<HHHIIHHH")
log = logging.getLogger("metropolis.ethernet_ip")


def receive_exact(sock: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise ConnectionError("EtherNet/IP connection closed")
        data.extend(chunk)
    return bytes(data)


def receive_packet(sock: socket.socket) -> tuple[int, int, bytes]:
    header = receive_exact(sock, HEADER.size)
    command, length, session, _status, _context, _options = HEADER.unpack(header)
    if length > 4096:
        raise ValueError("EtherNet/IP payload too large")
    return command, session, receive_exact(sock, length)


def send_packet(
    sock: socket.socket, command: int, session: int, payload: bytes
) -> None:
    sock.sendall(HEADER.pack(command, len(payload), session, 0, b"\0" * 8, 0) + payload)


def cip_from_rr(payload: bytes) -> bytes:
    if len(payload) < 8:
        raise ValueError("Short SendRRData payload")
    _interface, _timeout, count = struct.unpack_from("<IHH", payload)
    offset = 8
    for _ in range(count):
        if len(payload) < offset + 4:
            raise ValueError("Short CPF item")
        kind, length = struct.unpack_from("<HH", payload, offset)
        offset += 4
        if len(payload) < offset + length:
            raise ValueError("Short CPF data")
        item = payload[offset : offset + length]
        offset += length
        if kind == 0xB2:
            return item
    raise ValueError("Missing unconnected CIP item")


def rr_from_cip(cip: bytes) -> bytes:
    return struct.pack("<IHHHHHH", 0, 0, 2, 0, 0, 0xB2, len(cip)) + cip


def exchange(
    host: str, service: int, value: bool | None = None, port: int = PORT
) -> bool:
    """Read or write the lab actuator's single Boolean command/status attribute."""
    with socket.create_connection((host, port), timeout=0.6) as sock:
        sock.settimeout(0.6)
        send_packet(sock, REGISTER_SESSION, 0, struct.pack("<HH", 1, 0))
        command, session, _ = receive_packet(sock)
        if command != REGISTER_SESSION or session == 0:
            raise ValueError("EtherNet/IP session registration failed")
        cip = bytes((service, 3)) + PATH
        if value is not None:
            cip += bytes((int(value),))
        send_packet(sock, SEND_RR_DATA, session, rr_from_cip(cip))
        command, response_session, payload = receive_packet(sock)
        if command != SEND_RR_DATA or response_session != session:
            raise ValueError("Unexpected EtherNet/IP response")
        response = cip_from_rr(payload)
        if len(response) < 5 or response[0] != service | 0x80 or response[2] != 0:
            raise ValueError("CIP command failed")
        return bool(response[4])


def get_status(host: str, port: int = PORT) -> bool:
    return exchange(host, 0x0E, port=port)


def set_command(host: str, value: bool, port: int = PORT) -> bool:
    return exchange(host, 0x10, value, port)


def io_packet(connection_id: int, sequence: int, data: bytes) -> bytes:
    """Encode sequenced-address and connected-data CPF items for UDP/2222."""
    if not 0 <= sequence <= 0xFFFFFFFF:
        raise ValueError("I/O sequence out of range")
    return (
        IO_PREFIX.pack(
            2,
            0x8002,
            8,
            connection_id,
            sequence,
            0x00B1,
            len(data) + 6,
            sequence & 0xFFFF,
        )
        + struct.pack("<I", 1)
        + data
    )


def parse_io_packet(packet: bytes) -> tuple[int, int, bytes]:
    if len(packet) < IO_PREFIX.size + 4:
        raise ValueError("Short I/O packet")
    (
        count,
        address_type,
        address_size,
        connection_id,
        sequence,
        data_type,
        size,
        count16,
    ) = IO_PREFIX.unpack_from(packet)
    if (
        count != 2
        or address_type != 0x8002
        or address_size != 8
        or data_type != 0x00B1
        or size != len(packet) - (IO_PREFIX.size - 2)
        or count16 != sequence & 0xFFFF
        or struct.unpack_from("<I", packet, IO_PREFIX.size)[0] != 1
    ):
        raise ValueError("Invalid I/O common packet format")
    return connection_id, sequence, packet[IO_PREFIX.size + 4 :]


def send_cip(host: str, cip: bytes, port: int = PORT) -> bytes:
    """Send one unconnected CIP request through a registered TCP session."""
    with socket.create_connection((host, port), timeout=0.6) as sock:
        sock.settimeout(0.6)
        send_packet(sock, REGISTER_SESSION, 0, struct.pack("<HH", 1, 0))
        command, session, _ = receive_packet(sock)
        if command != REGISTER_SESSION or session == 0:
            raise ValueError("EtherNet/IP session registration failed")
        send_packet(sock, SEND_RR_DATA, session, rr_from_cip(cip))
        command, response_session, payload = receive_packet(sock)
        if command != SEND_RR_DATA or response_session != session:
            raise ValueError("Unexpected EtherNet/IP response")
        return cip_from_rr(payload)


def open_io(host: str, rpi_ms: int, port: int = PORT) -> tuple[int, int, int]:
    """Open this lab's two one-byte I/O assemblies; return IDs and serial."""
    if not 20 <= rpi_ms <= 5000:
        raise ValueError("EIP_RPI_MS must be between 20 and 5000")
    output_id = secrets.randbelow(0x7FFFFFFF) + 1
    input_id = secrets.randbelow(0x7FFFFFFF) + 1
    serial = secrets.randbelow(0xFFFF) + 1
    rpi_us = rpi_ms * 1000
    data = (
        OPEN_DATA.pack(
            0x0A,
            10,
            output_id,
            input_id,
            serial,
            0x1337,
            0x4D455452,
            3,
            rpi_us,
            0x4207,
            rpi_us,
            0x4208,
            0xA3,
            len(ASSEMBLY_PATH) // 2,
        )
        + ASSEMBLY_PATH
    )
    answer = send_cip(host, bytes((FORWARD_OPEN, 2)) + MANAGER_PATH + data, port)
    if len(answer) < 12 or answer[:4] != bytes((FORWARD_OPEN | 0x80, 0, 0, 0)):
        raise ValueError("CIP Forward Open failed")
    returned_output, returned_input = struct.unpack_from("<II", answer, 4)
    if (returned_output, returned_input) != (output_id, input_id):
        raise ValueError("CIP Forward Open returned different connection IDs")
    return output_id, input_id, serial


def close_io(host: str, serial: int, port: int = PORT) -> None:
    data = struct.pack(
        "<BBHHIB", 0x0A, 10, serial, 0x1337, 0x4D455452, len(ASSEMBLY_PATH) // 2
    )
    answer = send_cip(
        host,
        bytes((FORWARD_CLOSE, 2)) + MANAGER_PATH + data + b"\0" + ASSEMBLY_PATH,
        port,
    )
    if answer[:4] != bytes((FORWARD_CLOSE | 0x80, 0, 0, 0)):
        raise ValueError("CIP Forward Close failed")


class CyclicActuator:
    """Keep a lab actuator's unicast I/O connection alive in a worker thread."""

    def __init__(
        self, host: str, rpi_ms: int = 100, tcp_port: int = PORT, io_port: int = IO_PORT
    ) -> None:
        if not 20 <= rpi_ms <= 5000:
            raise ValueError("EIP_RPI_MS must be between 20 and 5000")
        self.host = host
        self.rpi = rpi_ms / 1000
        self.tcp_port = tcp_port
        self.io_port = io_port
        self._lock = threading.Lock()
        self._command = False
        self._active = False
        self._available = False
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

    def start(self) -> None:
        if self._worker is None:
            self._worker = threading.Thread(target=self._run, daemon=True)
            self._worker.start()

    def command(self, value: bool) -> None:
        with self._lock:
            self._command = value

    def snapshot(self) -> tuple[bool, bool]:
        """Return (confirmed active, connection healthy)."""
        with self._lock:
            return self._active, self._available

    def close(self) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=2)

    def _run(self) -> None:
        reported_failure = False
        while not self._stop.is_set():
            serial = None
            try:
                output_id, input_id, serial = open_io(
                    self.host, round(self.rpi * 1000), self.tcp_port
                )
                log.info(
                    "Cyclic I/O connected to %s at %s ms RPI",
                    self.host,
                    round(self.rpi * 1000),
                )
                reported_failure = False
                target_ip = socket.gethostbyname(self.host)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(max(self.rpi * 2, 0.1))
                    last_input_sequence = -1
                    sequence = 0
                    while not self._stop.is_set():
                        started = time.monotonic()
                        with self._lock:
                            desired = self._command
                        sequence += 1
                        sock.sendto(
                            io_packet(output_id, sequence, bytes((int(desired),))),
                            (self.host, self.io_port),
                        )
                        while True:
                            reply, source = sock.recvfrom(2048)
                            connection, incoming, data = parse_io_packet(reply)
                            if (
                                source[0] != target_ip
                                or connection != input_id
                                or incoming <= last_input_sequence
                                or len(data) != 2
                            ):
                                continue
                            last_input_sequence = incoming
                            with self._lock:
                                self._active = bool(data[0]) and not bool(data[1])
                                self._available = True
                            break
                        self._stop.wait(max(0, self.rpi - (time.monotonic() - started)))
            except (OSError, ValueError, ConnectionError) as exc:
                if not reported_failure:
                    log.warning("Cyclic I/O unavailable for %s: %s", self.host, exc)
                    reported_failure = True
                with self._lock:
                    self._active = False
                    self._available = False
                self._stop.wait(1.0)
            finally:
                if serial is not None:
                    try:
                        close_io(self.host, serial, self.tcp_port)
                    except (OSError, ValueError, ConnectionError):
                        pass
