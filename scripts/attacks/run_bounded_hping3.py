#!/usr/bin/env python3
"""Send a capped amount of load at one lab service.

Use this to generate availability-experiment traffic, so a capture shows what
load against a chosen listener looks like. Connect mode makes TCP connection
attempts and suits the PLC, MQTT broker, HMI and SSH listeners. UDP mode sends
fixed-size datagrams and suits the CoAP sensor, DNS and NTP.

Every run stops at --max-packets or --duration, whichever comes first. Those
are ceilings rather than quotas: the tool never catches up after a slow probe,
so a slow target sends fewer packets than you asked for.

Targets must be private lab addresses, and the default target is an
inventoried node, so a run cannot be pointed at a public host by accident.
This sends no raw or spoofed packets and is not a flood. Pair it with a
before, during and after service measurement before claiming anything about
availability.

Mirrors the bounded load Gotham produced by running an uploaded hping3 binary
through its C2 console.
"""

from __future__ import annotations

import socket
import time

from _common import Run, address_of, cli, ipv4, parser, positive, schedule

# Hard ceilings. These are deliberately modest: a lab availability experiment
# needs a repeatable, bounded stimulus, not a genuine flood.
MAX_PACKET_CEILING = 200_000
MAX_RATE = 2000.0
MAX_DURATION = 600.0
DEFAULT_PAYLOAD = b"metropolis-bounded-probe"


def connect_probe(host: str, port: int, timeout: float) -> str:
    """One TCP connect attempt against a lab listener."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "open"
    except socket.timeout:
        return "timeout"
    except ConnectionRefusedError:
        return "refused"
    except OSError as exc:
        return f"error:{exc.errno}"


def udp_probe(host: str, port: int, timeout: float, size: int) -> str:
    """Send one fixed-size datagram; a reply means the listener answered."""
    payload = (DEFAULT_PAYLOAD * (size // len(DEFAULT_PAYLOAD) + 1))[:size]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.sendto(payload, (host, port))
        except OSError as exc:
            return f"error:{exc.errno}"
        try:
            sock.recvfrom(2048)
            return "answered"
        except socket.timeout:
            return "no-reply"


def main() -> int:
    options = parser(__doc__, "MET-PLC-INTAKE-01")
    options.add_argument("--mode", choices=["connect", "udp"], default="connect")
    options.add_argument("--rate", type=positive, default=20)
    options.add_argument("--duration", type=positive, default=10)
    options.add_argument("--max-packets", type=int, default=1000)
    options.add_argument("--payload-size", type=int, default=64)
    args = options.parse_args()

    if args.max_packets > MAX_PACKET_CEILING:
        raise ValueError(f"max-packets must not exceed {MAX_PACKET_CEILING}")
    if args.rate > MAX_RATE:
        raise ValueError(f"rate must not exceed {MAX_RATE}/s")
    if args.duration > MAX_DURATION:
        raise ValueError(f"duration must not exceed {MAX_DURATION}s")
    if not 1 <= args.payload_size <= 1024:
        raise ValueError("payload-size must be 1..1024 bytes")

    # TCP load targets an OT listener; UDP load targets the CoAP sensor, so the
    # target node changes with the mode unless the caller named one.
    if args.mode == "connect":
        protocol, prefix, port_key, default_node, default_port = (
            "modbus-tcp",
            "PLC",
            "MODBUS_PORT",
            "MET-PLC-INTAKE-01",
            502,
        )
    else:
        protocol, prefix, port_key, default_node, default_port = (
            "coap",
            "COAP",
            "COAP_PORT",
            "MET-SENSOR-INTAKE-01",
            5683,
        )
    node = args.node if args.node != "MET-PLC-INTAKE-01" else default_node
    _, host, port = address_of(
        args,
        node,
        protocol,
        f"METROPOLIS_{prefix}_HOST",
        port_key,
        default_port,
    )
    ipv4(host)

    probe = (
        (lambda: connect_probe(host, port, args.timeout))
        if args.mode == "connect"
        else (lambda: udp_probe(host, port, args.timeout, args.payload_size))
    )
    with Run(
        args,
        "bounded_probe",
        host=host,
        port=port,
        mode=args.mode,
        rate=args.rate,
        duration=args.duration,
        max_packets=args.max_packets,
        payload_size=args.payload_size if args.mode == "udp" else None,
    ) as run:
        if args.dry_run:
            return 0
        outcomes: dict[str, int] = {}
        sent = 0
        started = time.monotonic()
        for _ in schedule(args.rate, args.duration, args.max_packets):
            result = probe()
            outcomes[result] = outcomes.get(result, 0) + 1
            sent += 1
        elapsed = time.monotonic() - started
        run.emit(
            "summary",
            packets=sent,
            elapsed_seconds=round(elapsed, 3),
            achieved_rate=round(sent / elapsed, 1) if elapsed else None,
            outcomes=outcomes,
            note=(
                "Bounded stimulus only; pair it with a before/during/after "
                "service measurement to say anything about availability"
            ),
        )
        return 0 if sent else 1


if __name__ == "__main__":
    raise SystemExit(cli(main))
