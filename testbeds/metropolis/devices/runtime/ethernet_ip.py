"""Small EtherNet/IP explicit-message implementation for lab actuators.

Implements RegisterSession and SendRRData with CIP Get/Set Attribute Single.
Class 0x64 is a lab-specific object, not a vendor drive profile or cyclic I/O.
"""

from __future__ import annotations

import socket
import struct

PORT = 44818
REGISTER_SESSION = 0x65
SEND_RR_DATA = 0x6F
CLASS = 0x64
PATH = bytes((0x20, CLASS, 0x24, 1, 0x30, 1))
HEADER = struct.Struct("<HHII8sI")


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
