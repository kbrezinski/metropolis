"""Measure CoAP response expansion or generate bounded lab reflection traffic."""

from __future__ import annotations

import secrets
import socket
import struct
import time

from _common import Run, cli, endpoint, ipv4, limits, parser, positive, schedule


def nibble(value: int):
    if value < 13:
        return value, b""
    if value < 269:
        return 13, bytes([value - 13])
    if value <= 65804:
        return 14, struct.pack("!H", value - 269)
    raise ValueError("CoAP option is too long")


def build_get(path: str, token: bytes, message_id: int) -> bytes:
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
        bytes([0x50 | len(token), 1]) + struct.pack("!H", message_id) + token + options
    )


def valid_response(data: bytes, token: bytes) -> bool:
    return (
        len(data) >= 4 + len(token)
        and data[0] >> 6 == 1
        and data[0] & 15 == len(token)
        and data[1] >> 5 == 2
        and data[4 : 4 + len(token)] == token
    )


def main():
    options = parser(__doc__, "MET-SENSOR-INTAKE-01")
    options.add_argument("--mode", choices=["measure", "spoof"], default="measure")
    options.add_argument("--resource", default="/.well-known/core")
    options.add_argument("--rate", type=positive, default=5)
    options.add_argument("--duration", type=positive, default=10)
    options.add_argument("--max-messages", type=int, default=100)
    options.add_argument("--spoof-src", help="Separate private lab receiver address")
    options.add_argument("--receiver-port", type=int, default=56830)
    args = options.parse_args()
    _, host, port = endpoint(args, "coap", "COAP", 5683)
    limits(args)
    build_get(args.resource, b"test", 1)
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
        resource=args.resource,
        rate=args.rate,
        duration=args.duration,
        receiver=args.spoof_src,
        receiver_port=args.receiver_port,
    ) as run:
        if args.dry_run:
            return 0
        sent = received = request_bytes = response_bytes = matched_request_bytes = 0
        if args.mode == "spoof":
            from scapy.all import IP, UDP, Raw, send

            for _ in schedule(args.rate, args.duration, args.max_messages):
                payload = build_get(
                    args.resource, secrets.token_bytes(4), secrets.randbelow(65536)
                )
                packet = (
                    IP(src=args.spoof_src, dst=host)
                    / UDP(sport=args.receiver_port, dport=port)
                    / Raw(payload)
                )
                send(packet, verbose=False)
                sent += 1
                request_bytes += len(payload)
            run.emit(
                "summary",
                sent=sent,
                request_bytes=request_bytes,
                evidence="Requests emitted; capture responses at the receiver",
            )
            return 0
        deadline = time.monotonic() + args.duration
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for _ in schedule(args.rate, args.duration, args.max_messages):
                token = secrets.token_bytes(4)
                payload = build_get(args.resource, token, secrets.randbelow(65536))
                sock.sendto(payload, (host, port))
                sent += 1
                request_bytes += len(payload)
                response_deadline = min(deadline, time.monotonic() + args.timeout)
                while time.monotonic() < response_deadline:
                    sock.settimeout(max(0.001, response_deadline - time.monotonic()))
                    try:
                        response, peer = sock.recvfrom(65535)
                    except socket.timeout:
                        break
                    if peer == (host, port) and valid_response(response, token):
                        received += 1
                        response_bytes += len(response)
                        matched_request_bytes += len(payload)
                        break
        run.emit(
            "summary",
            sent=sent,
            received=received,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
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
