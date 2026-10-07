"""Small CoAP GET service with CoRE discovery and per-measurement resources.

Supports URI routing and CON/NON requests, not a complete CoAP stack.
See the device guide for protocol limits.

Besides the CoRE discovery resource and ``/status``, one resource is served per
measurement. That is what a real CoAP device exposes, and it is also what makes
a CoAP request pattern interesting: a polling client makes a separate round trip
per resource, so the traffic has structure a single endpoint cannot show.
"""

from __future__ import annotations

import json
import logging
import secrets
import socket
import struct
from typing import NamedTuple


log = logging.getLogger("metropolis.coap")


class Resource(NamedTuple):
    """One CoAP resource: its path, the reading behind it, and what it means.

    A plain tuple rather than a dataclass, so this module loads under the simple
    file-based importers the tests use.
    """

    path: str
    key: str
    title: str


# Served in this order, and advertised by discovery in the same order.
RESOURCES: tuple[Resource, ...] = (
    Resource("level", "level", "process level"),
    Resource("flow", "flow", "flow rate"),
    Resource("quality", "quality", "water quality"),
    Resource("pressure", "pressure", "line pressure"),
    Resource("temperature", "temperature", "water temperature"),
    Resource("turbidity", "turbidity", "turbidity"),
    Resource("pump", "pump", "pump run state"),
)

DISCOVERY = b'</status>;rt="metropolis.sensor";ct=50' + b"".join(
    f',</{item.path}>;rt="metropolis.measurement";ct=0'.encode() for item in RESOURCES
)


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


def current_value(value) -> float:
    """Resolve a sensor value that may be a fixed number or a live callable.

    A callable lets the status resource report the process reading rather than a
    value captured at startup.
    """
    return value() if callable(value) else value


def readings(value) -> dict[str, float] | None:
    """The full measurement set, when the provider can supply one.

    A provider returning a mapping gives one resource per measurement. A
    provider returning a single number still works: the sensor's own reading is
    served under its own name and nothing else is available.
    """
    resolved = current_value(value)
    return resolved if isinstance(resolved, dict) else None


def measurement(value, resource: Resource) -> float | None:
    """The reading behind one resource, or None when it is not available."""
    resolved = current_value(value)
    if isinstance(resolved, dict):
        return resolved.get(resource.key)
    # A single reading is only served under its own resource name.
    return resolved if resource.path == resource.key else None


def _body_for_status(device_name: str, sensor_type: str, value) -> bytes:
    resolved = current_value(value)
    if isinstance(resolved, dict):
        reading = resolved.get(sensor_type, next(iter(resolved.values()), None))
    else:
        reading = resolved
    return json.dumps(
        {"device": device_name, "sensor": sensor_type, "value": reading},
        separators=(",", ":"),
    ).encode()


def handle_get(
    request: bytes, device_name: str, sensor_type: str, value
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
        body = _body_for_status(device_name, sensor_type, value)
        content_format = 50  # application/json
    else:
        resource = next(
            (item for item in RESOURCES if item.path.encode() == path), None
        )
        if resource is None:
            return respond(132)  # 4.04 Not Found
        reading = measurement(value, resource)
        if reading is None:
            return respond(132)  # 4.04 Not Found: the sensor does not report this
        # A bare scalar per resource, which is what these devices return.
        body = f"{reading:g}".encode()
        content_format = 0  # text/plain;charset=utf-8
    if accepted and int.from_bytes(accepted[0], "big") != content_format:
        return respond(134)  # 4.06 Not Acceptable
    return respond(69, body, content_format)  # 2.05 Content


def serve(host: str, port: int, device_name: str, sensor_type: str, value) -> None:
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
