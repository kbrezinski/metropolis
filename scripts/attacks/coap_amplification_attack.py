"""Measure CoAP response expansion or generate bounded lab reflection traffic."""

from __future__ import annotations

import secrets
import re
import socket
import struct
import time
from collections import Counter

from _common import Run, cli, endpoint, ipv4, limits, parser, positive, schedule

PROFILES = {
    "custom": (5, 10, 100),
    "baseline": (2, 30, 60),
    "burst": (25, 10, 250),
    "sustained": (5, 120, 600),
}
MIXED_RESOURCES = (
    "/.well-known/core", "/status", "/level", "/flow", "/quality",
    "/pressure", "/temperature", "/turbidity", "/pump", "/missing",
)


def nibble(value: int):
    if value < 13:
        return value, b""
    if value < 269:
        return 13, bytes([value - 13])
    if value <= 65804:
        return 14, struct.pack("!H", value - 269)
    raise ValueError("CoAP option is too long")


def build_get(path: str, token: bytes, message_id: int, confirmable: bool = False) -> bytes:
    if not 1 <= len(token) <= 8 or not path.startswith("/"):
        raise ValueError("Use an absolute resource path and a 1–8 byte token")
    options = bytearray()
    previous = 0
    for segment in (part.encode() for part in path.split("/") if part):
        delta, delta_ext = nibble(11 - previous)
        length, length_ext = nibble(len(segment))
        options.extend(
            bytes([(delta << 4) | length]) + delta_ext + length_ext + segment
        )
        previous = 11
    return (
        bytes([(0x40 if confirmable else 0x50) | len(token), 1])
        + struct.pack("!H", message_id) + token + options
    )


def valid_response(data: bytes, token: bytes) -> bool:
    return (
        len(data) >= 4 + len(token)
        and data[0] >> 6 == 1
        and data[0] & 15 == len(token)
        and data[1] >> 5 == 2
        and data[4 : 4 + len(token)] == token
    )


def response_code(data: bytes, token: bytes, message_id: int, confirmable: bool) -> int | None:
    """Match this sensor's piggybacked ACK or NON reply to one request."""
    if len(data) < 4 + len(token) or data[0] >> 6 != 1:
        return None
    if data[0] & 15 != len(token) or data[4 : 4 + len(token)] != token:
        return None
    if confirmable:
        if (data[0] >> 4) & 3 != 2 or int.from_bytes(data[2:4], "big") != message_id:
            return None
    elif (data[0] >> 4) & 3 != 1:
        return None
    return data[1] if data[1] >> 5 in (2, 4, 5) else None


def settings(args) -> None:
    if args.profile != "custom" and any(
        value is not None for value in (args.rate, args.duration, args.max_messages)
    ):
        raise ValueError("Use --profile or explicit --rate/--duration/--max-messages")
    rate, duration, maximum = PROFILES[args.profile]
    args.rate = rate if args.rate is None else args.rate
    args.duration = duration if args.duration is None else args.duration
    args.max_messages = maximum if args.max_messages is None else args.max_messages
    limits(args)
    if args.experiment_id is None:
        if args.mode == "spoof":
            raise ValueError("Spoof mode needs --experiment-id to correlate receiver logs")
        args.experiment_id = secrets.token_hex(2)
    if not re.fullmatch(r"[0-9a-fA-F]{4}", args.experiment_id):
        raise ValueError("experiment-id must be four hexadecimal characters")
    args.experiment_id = args.experiment_id.lower()


def request_choice(args, index: int) -> tuple[str, bool]:
    path = args.resource or MIXED_RESOURCES[index % len(MIXED_RESOURCES)]
    confirmable = args.message_type == "con" or (
        args.message_type == "mixed" and index % 2 == 0
    )
    return path, confirmable


def main():
    options = parser(__doc__, "MET-SENSOR-INTAKE-01")
    options.add_argument("--mode", choices=["measure", "spoof"], default="measure")
    options.add_argument("--resource", help="Use one resource instead of the mixed resource sequence")
    options.add_argument("--message-type", choices=["con", "non", "mixed"], default="mixed")
    options.add_argument("--profile", choices=tuple(PROFILES), default="custom")
    options.add_argument("--rate", type=positive)
    options.add_argument("--duration", type=positive)
    options.add_argument("--max-messages", type=int)
    options.add_argument("--experiment-id", help="Four hex digits shared by sender, sensor and receiver")
    options.add_argument("--spoof-src", help="Separate private lab receiver address")
    options.add_argument("--receiver-port", type=int, default=56830)
    args = options.parse_args()
    _, host, port = endpoint(args, "coap", "COAP", 5683)
    settings(args)
    for path in ((args.resource,) if args.resource else MIXED_RESOURCES):
        build_get(path, b"test", 1)
    if not 1 <= args.receiver_port <= 65535:
        raise ValueError("Invalid receiver port")
    if args.mode == "spoof":
        if not args.spoof_src or ipv4(args.spoof_src) == host:
            raise ValueError(
                "Spoof mode needs a separate private lab --spoof-src receiver"
            )
    with Run(
        args,
        "coap",
        host=host,
        port=port,
        mode=args.mode,
        resources=[args.resource] if args.resource else list(MIXED_RESOURCES),
        message_type=args.message_type,
        profile=args.profile,
        experiment_id=args.experiment_id,
        rate=args.rate,
        duration=args.duration,
        receiver=args.spoof_src,
        receiver_port=args.receiver_port,
    ) as run:
        if args.dry_run:
            return 0
        sent = received = request_bytes = response_bytes = matched_request_bytes = 0
        resources_sent: Counter[str] = Counter()
        response_codes: Counter[str] = Counter()
        if args.mode == "spoof":
            from scapy.all import IP, UDP, Raw, send

            for index in schedule(args.rate, args.duration, args.max_messages):
                path, confirmable = request_choice(args, index)
                payload = build_get(
                    path,
                    bytes.fromhex(args.experiment_id) + secrets.token_bytes(2),
                    secrets.randbelow(65536),
                    confirmable,
                )
                packet = (
                    IP(src=args.spoof_src, dst=host)
                    / UDP(sport=args.receiver_port, dport=port)
                    / Raw(payload)
                )
                send(packet, verbose=False)
                sent += 1
                request_bytes += len(payload)
                resources_sent[path] += 1
            run.emit(
                "summary",
                sent=sent,
                request_bytes=request_bytes,
                resources_sent=dict(resources_sent),
                experiment_id=args.experiment_id,
                evidence="Requests emitted; capture responses at the receiver",
            )
            return 0 if sent else 1
        deadline = time.monotonic() + args.duration
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for index in schedule(args.rate, args.duration, args.max_messages):
                path, confirmable = request_choice(args, index)
                token = bytes.fromhex(args.experiment_id) + secrets.token_bytes(2)
                message_id = secrets.randbelow(65536)
                payload = build_get(path, token, message_id, confirmable)
                sock.sendto(payload, (host, port))
                sent += 1
                request_bytes += len(payload)
                resources_sent[path] += 1
                response_deadline = min(deadline, time.monotonic() + args.timeout)
                while time.monotonic() < response_deadline:
                    sock.settimeout(max(0.001, response_deadline - time.monotonic()))
                    try:
                        response, peer = sock.recvfrom(65535)
                    except socket.timeout:
                        break
                    code = response_code(response, token, message_id, confirmable)
                    if peer == (host, port) and code is not None:
                        received += 1
                        response_bytes += len(response)
                        matched_request_bytes += len(payload)
                        response_codes[f"{code >> 5}.{code & 31:02d}"] += 1
                        break
        run.emit(
            "summary",
            sent=sent,
            received=received,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
            resources_sent=dict(resources_sent),
            response_codes=dict(response_codes),
            experiment_id=args.experiment_id,
            matched_payload_ratio=(
                response_bytes / matched_request_bytes if received else None
            ),
            matched_ipv4_udp_ratio=(
                (response_bytes + 28 * received)
                / (matched_request_bytes + 28 * received)
                if received
                else None
            ),
        )
        return 0 if received else 1


if __name__ == "__main__":
    raise SystemExit(cli(main))
