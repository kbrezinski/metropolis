#!/usr/bin/env python3
"""Send bounded service probes or raw hping3-style packets inside the lab.

Use this to generate availability-experiment traffic, so a capture shows what
load against a chosen listener looks like. Connect mode makes TCP connection
attempts and suits the PLC, MQTT broker, HMI and SSH listeners. UDP mode sends
fixed-size datagrams and suits the CoAP sensor, DNS and NTP.

Every run stops at --max-packets or --duration, whichever comes first. Those
are ceilings rather than quotas: the tool never catches up after a slow probe,
so a slow target sends fewer packets than you asked for.

The raw profiles model Gotham's UDP, UDP VSE, TCP SYN, TCP ACK, and TCP ACK+PSH
packet shapes. TCP profiles advance the destination port on every packet.
``--flood`` removes the pacing delay but still stops at the packet and time
ceilings. Raw modes need privileges to send IP packets. They do not spoof a
source address or establish that a service was disrupted; confirm their shape
and count in a capture.
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
RAW_MAX_PACKETS = 3_000
RAW_MAX_RATE = 100.0
RAW_MAX_DURATION = 30.0
RAW_MODES = frozenset({"udp-raw", "udp-vse", "tcp-syn", "tcp-ack", "tcp-ack-psh"})
TCP_RAW_MODES = frozenset({"tcp-syn", "tcp-ack", "tcp-ack-psh"})
DEFAULT_PAYLOAD = b"metropolis-bounded-probe"
VSE_QUERY = b"\xff\xff\xff\xffTSource Engine Query\x00"


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


def raw_packet(mode: str, host: str, port: int, index: int, size: int):
    """Build one unspoofed IP packet with the selected profile."""
    from scapy.all import IP, TCP, UDP, Raw

    if mode in TCP_RAW_MODES:
        flags = {"tcp-syn": "S", "tcp-ack": "A", "tcp-ack-psh": "PA"}[mode]
        destination = (port - 1 + index) % 65535 + 1
        packet = IP(dst=host) / TCP(
            sport=40000 + index % 20000,
            dport=destination,
            flags=flags,
            seq=index + 1,
        )
        if mode == "tcp-ack-psh":
            packet /= Raw(
                load=(DEFAULT_PAYLOAD * (size // len(DEFAULT_PAYLOAD) + 1))[:size]
            )
        return packet
    payload = (
        VSE_QUERY
        if mode == "udp-vse"
        else (DEFAULT_PAYLOAD * (size // len(DEFAULT_PAYLOAD) + 1))[:size]
    )
    return (
        IP(dst=host) / UDP(sport=40000 + index % 20000, dport=port) / Raw(load=payload)
    )


def send_raw(packet) -> None:
    """Send a layer-three packet without waiting for a response."""
    from scapy.all import send

    send(packet, verbose=False)


def main() -> int:
    options = parser(__doc__, "MET-PLC-INTAKE-01")
    options.add_argument(
        "--mode", choices=["connect", "udp", *sorted(RAW_MODES)], default="connect"
    )
    options.add_argument("--rate", type=positive, default=20)
    options.add_argument("--duration", type=positive, default=10)
    options.add_argument("--max-packets", type=int, default=1000)
    options.add_argument("--payload-size", type=int, default=64)
    options.add_argument(
        "--flood",
        action="store_true",
        help="Send raw packets without pacing; packet and duration limits still apply",
    )
    args = options.parse_args()

    if not 1 <= args.max_packets <= MAX_PACKET_CEILING:
        raise ValueError(f"max-packets must not exceed {MAX_PACKET_CEILING}")
    if args.rate > MAX_RATE:
        raise ValueError(f"rate must not exceed {MAX_RATE}/s")
    if args.duration > MAX_DURATION:
        raise ValueError(f"duration must not exceed {MAX_DURATION}s")
    if not 1 <= args.payload_size <= 1024:
        raise ValueError("payload-size must be 1..1024 bytes")
    if args.flood and args.mode not in RAW_MODES:
        raise ValueError("--flood requires a raw packet mode")
    if args.mode in RAW_MODES:
        if args.max_packets > RAW_MAX_PACKETS:
            raise ValueError(f"raw modes allow at most {RAW_MAX_PACKETS} packets")
        if args.duration > RAW_MAX_DURATION:
            raise ValueError(f"raw modes allow at most {RAW_MAX_DURATION}s")
        if not args.flood and args.rate > RAW_MAX_RATE:
            raise ValueError(f"raw modes allow at most {RAW_MAX_RATE} packets/s")

    # TCP load targets an OT listener; UDP load targets the CoAP sensor, so the
    # target node changes with the mode unless the caller named one.
    if args.mode == "connect" or args.mode in TCP_RAW_MODES:
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
    record, host, port = address_of(
        args,
        node,
        protocol,
        f"METROPOLIS_{prefix}_HOST",
        port_key,
        default_port,
    )
    ipv4(host)
    if args.mode in RAW_MODES and host != record["address"].split("/", 1)[0]:
        raise ValueError("raw packet modes must target the inventoried node address")

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
        rate=None if args.flood else args.rate,
        flood=args.flood,
        duration=args.duration,
        max_packets=args.max_packets,
        payload_size=args.payload_size if args.mode in {"udp", "udp-raw"} else None,
        destination_ports="incrementing" if args.mode in TCP_RAW_MODES else "fixed",
    ) as run:
        if args.dry_run:
            return 0
        outcomes: dict[str, int] = {}
        sent = 0
        started = time.monotonic()
        if args.mode in RAW_MODES:
            deadline = started + args.duration
            indexes = (
                range(args.max_packets)
                if args.flood
                else schedule(args.rate, args.duration, args.max_packets)
            )
            for index in indexes:
                if time.monotonic() >= deadline:
                    break
                send_raw(raw_packet(args.mode, host, port, index, args.payload_size))
                sent += 1
        else:
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
