"""Count CoAP replies at a separate private lab receiver during a reflection run."""

from __future__ import annotations

import socket
import time
from collections import Counter

from _common import Run, cli, endpoint, ipv4, parser, positive


def matching_reply(packet: bytes, prefix: bytes) -> int | None:
    if len(packet) < 8 or packet[0] >> 6 != 1 or packet[0] & 15 != 4:
        return None
    if packet[4:6] != prefix or packet[1] >> 5 not in (2, 4, 5):
        return None
    return packet[1]


def main() -> int:
    options = parser(__doc__, "MET-SENSOR-INTAKE-01")
    options.add_argument("--experiment-id", required=True, help="Four hex digits also passed to the sender")
    options.add_argument("--listen-host", default="0.0.0.0")
    options.add_argument("--listen-port", type=int, default=56830)
    options.add_argument("--duration", type=positive, default=150)
    options.add_argument("--max-responses", type=int, default=10000)
    args = options.parse_args()
    _, sensor_host, sensor_port = endpoint(args, "coap", "COAP", 5683)
    if args.listen_host != "0.0.0.0":
        ipv4(args.listen_host)
    if not 1 <= args.listen_port <= 65535:
        raise ValueError("listen-port must be 1–65535")
    if args.duration > 3600 or not 1 <= args.max_responses <= 100000:
        raise ValueError("duration must be at most 3600s and max-responses 1–100000")
    if len(args.experiment_id) != 4:
        raise ValueError("experiment-id must be four hexadecimal characters")
    try:
        prefix = bytes.fromhex(args.experiment_id)
    except ValueError as exc:
        raise ValueError("experiment-id must be four hexadecimal characters") from exc
    with Run(
        args, "coap_receiver", sensor=sensor_host, sensor_port=sensor_port, listen_host=args.listen_host,
        listen_port=args.listen_port, experiment_id=args.experiment_id.lower(),
        duration=args.duration, max_responses=args.max_responses,
    ) as run:
        if args.dry_run:
            return 0
        received = ignored = response_bytes = 0
        codes: Counter[str] = Counter()
        deadline = time.monotonic() + args.duration
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind((args.listen_host, args.listen_port))
            while received < args.max_responses and time.monotonic() < deadline:
                sock.settimeout(min(1.0, max(0.001, deadline - time.monotonic())))
                try:
                    packet, peer = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                code = matching_reply(packet, prefix) if peer == (sensor_host, sensor_port) else None
                if code is None:
                    ignored += 1
                    continue
                received += 1
                response_bytes += len(packet)
                codes[f"{code >> 5}.{code & 31:02d}"] += 1
        run.emit(
            "summary", experiment_id=args.experiment_id.lower(),
            received=received, ignored=ignored, response_bytes=response_bytes,
            response_codes=dict(codes),
        )
        return 0 if received else 1


if __name__ == "__main__":
    raise SystemExit(cli(main))
