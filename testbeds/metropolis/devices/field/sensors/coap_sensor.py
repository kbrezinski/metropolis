"""Small CoAP GET service with CoRE discovery and sensor status resources.

Supports URI routing and CON/NON requests, not a complete CoAP stack.
See the device guide for protocol limits.
"""

from __future__ import annotations

import json
import logging
import secrets
import socket
import struct


log = logging.getLogger("metropolis.coap")
DISCOVERY = b'</status>;rt="metropolis.sensor";ct=50'


def _option_value(nibble: int, data: bytes, offset: int) -> tuple[int, int]:
    if nibble < 13:
        return nibble, offset
    size = {13: 1, 14: 2}.get(nibble)
    if size is None or offset + size > len(data):
        raise ValueError("Invalid or truncated CoAP option")
    value = int.from_bytes(data[offset : offset + size], "big")
    return value + (13 if nibble == 13 else 269), offset + size


def _options(data: bytes, offset: int) -> list[tuple[int, bytes]]:
    number = 0
    options = []
    while offset < len(data):
        header = data[offset]
        offset += 1
        if header == 0xFF:
            if offset == len(data):
                raise ValueError("Empty CoAP payload marker")
            break
        delta, offset = _option_value(header >> 4, data, offset)
        length, offset = _option_value(header & 15, data, offset)
        if offset + length > len(data):
            raise ValueError("Truncated CoAP option value")
        number += delta
        options.append((number, data[offset : offset + length]))
        offset += length
    return options


def handle_get(
    request: bytes, device_name: str, sensor_type: str, value: float
) -> bytes | None:
    if len(request) < 4:
        return None
    first, code = request[0], request[1]
    version = first >> 6
    request_type = (first >> 4) & 0x03
    token_length = first & 0x0F
    if (
        version != 1
        or request_type not in (0, 1)
        or token_length > 8
        or len(request) < 4 + token_length
        or not 1 <= code <= 31
    ):
        return None

    token = request[4 : 4 + token_length]
    message_id = struct.unpack("!H", request[2:4])[0]
    response_type = 2 if request_type == 0 else 1
    # NON responses have a server-assigned ID; ACKs echo the request ID.
    if request_type == 1:
        message_id = secrets.randbelow(65536)

    def respond(status: int, body: bytes = b"", content_format: int = 50) -> bytes:
        header = struct.pack(
            "!BBH", 0x40 | (response_type << 4) | token_length, status, message_id
        )
        if not body:
            return header + token
        return header + token + bytes((0xC1, content_format, 0xFF)) + body

    if code != 1:
        return respond(133)  # 4.05 Method Not Allowed
    try:
        options = _options(request, 4 + token_length)
    except ValueError:
        return None
    # Uri-Host/Port are accepted for this single-host service. Unsupported
    # critical options (including query filtering/proxying) get 4.02.
    if any(number % 2 and number not in {3, 7, 11, 17} for number, _ in options):
        return respond(130)
    path = b"/".join(value for number, value in options if number == 11)
    accepted = [value for number, value in options if number == 17]
    if len(accepted) > 1 or any(len(value) > 2 for value in accepted):
        return respond(130)
    if path == b".well-known/core":
        body, content_format = DISCOVERY, 40  # application/link-format
    elif path in (b"", b"status"):
        body = json.dumps(
            {"device": device_name, "sensor": sensor_type, "value": value},
            separators=(",", ":"),
        ).encode()
        content_format = 50  # application/json
    else:
        return respond(132)  # 4.04 Not Found
    if accepted and int.from_bytes(accepted[0], "big") != content_format:
        return respond(134)  # 4.06 Not Acceptable
    return respond(69, body, content_format)  # 2.05 Content


def serve(
    host: str, port: int, device_name: str, sensor_type: str, value: float
) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((host, port))
        log.info("CoAP status endpoint listening on %s:%s", host, port)
        while True:
            request, peer = sock.recvfrom(1024)
            response = handle_get(request, device_name, sensor_type, value)
            if response is not None:
                sock.sendto(response, peer)
            else:
                log.warning(
                    "Ignored malformed or unsupported CoAP message from %s", peer[0]
                )
