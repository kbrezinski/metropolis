#!/usr/bin/env python3
<<<<<<< HEAD
"""Send bounded service probes or raw hping3-style packets inside the lab.
=======
"""Generate bounded packet patterns based on Gotham's hping3 recipes.
>>>>>>> 5e23f21557b30ec53f0429a17cc6561c89992a71

Raw modes model Gotham's generic UDP, Source Engine query, TCP SYN, TCP ACK,
and TCP ACK+PSH patterns. They require raw-socket privileges. The older
TCP-connect and UDP probe modes remain available for comparison.

Every run stops at --max-packets or --duration, whichever comes first. Those
are ceilings rather than quotas: the tool never catches up after a slow probe,
so a slow target sends fewer packets than you asked for.

<<<<<<< HEAD
The raw profiles model Gotham's UDP, UDP VSE, TCP SYN, TCP ACK, and TCP ACK+PSH
packet shapes. TCP profiles advance the destination port on every packet.
``--flood`` removes the pacing delay but still stops at the packet and time
ceilings. Raw modes need privileges to send IP packets. They do not spoof a
source address or establish that a service was disrupted; confirm their shape
and count in a capture.
=======
Raw modes are restricted to the selected node's inventoried address. No source
spoofing is used. The low ceilings yield the packet patterns in a capture, not
Gotham's unbounded --flood volume or a service-exhaustion test. The --flood
switch removes inter-packet delays while retaining the count and time limits.
Measure availability separately before making a DoS claim.

Gotham's recipe file lists these commands; its run_merlin.py uploads and makes
hping3 executable but does not invoke the flood recipes.
>>>>>>> 5e23f21557b30ec53f0429a17cc6561c89992a71
"""

from __future__ import annotations

import socket
import time
from os import urandom
from secrets import randbelow

from _common import Run, address_of, cli, inventory, ipv4, parser, positive, schedule

# Hard ceilings. These are deliberately modest: a lab availability experiment
# needs a repeatable, bounded stimulus, not a genuine flood.
<<<<<<< HEAD
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
=======
MAX_PACKET_CEILING = 3000
MAX_RATE = 100.0
MAX_DURATION = 30.0
DEFAULT_PAYLOAD = b"metropolis-bounded-probe"
SOURCE_QUERY = b"TSource Engine Query\x00"
RAW_MODES = {"syn", "udp-generic", "udp-vse", "ack", "stomp"}


def raw_packet(mode: str, host: str, port: int, index: int):
    """Build one Gotham-shaped packet without forging the source address."""
    from scapy.all import IP, TCP, UDP, Raw

    ip = IP(dst=host, tos=0, ttl=64, flags="DF" if mode in {"syn", "stomp"} else 0)
    source_port = 32768 + randbelow(28232)
    if mode == "udp-generic":
        return ip / UDP(sport=source_port, dport=port) / Raw(urandom(512))
    if mode == "udp-vse":
        return ip / UDP(sport=source_port, dport=27015) / Raw(SOURCE_QUERY)
    destination_port = (port + index) % 65536
    flags = {"syn": "S", "ack": "A", "stomp": "PA"}[mode]
    packet = ip / TCP(sport=source_port, dport=destination_port, flags=flags, ack=0)
    if mode in {"ack", "stomp"}:
        packet /= Raw(urandom(512 if mode == "ack" else 768))
    return packet


def send_raw_packet(mode: str, host: str, port: int, index: int) -> str:
    from scapy.all import send

    packet = raw_packet(mode, host, port, index)
    send(packet, verbose=False)
    return "sent"


def syn_probe(host: str, port: int) -> str:
    """Compatibility helper for a single SYN packet on a fixed port."""
    return send_raw_packet("syn", host, port, 0)


def flood_schedule(duration: float, maximum: int):
    """Emit packet indices without pacing, stopping at either hard ceiling."""
    deadline = time.monotonic() + duration
    for index in range(maximum):
        if time.monotonic() >= deadline:
            return
        yield index
>>>>>>> 5e23f21557b30ec53f0429a17cc6561c89992a71


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
<<<<<<< HEAD
        "--mode", choices=["connect", "udp", *sorted(RAW_MODES)], default="connect"
=======
        "--mode",
        choices=["syn", "udp-generic", "udp-vse", "ack", "stomp", "connect", "udp"],
        default="syn",
>>>>>>> 5e23f21557b30ec53f0429a17cc6561c89992a71
    )
    options.add_argument("--rate", type=positive, default=20)
    options.add_argument("--duration", type=positive, default=10)
    options.add_argument("--max-packets", type=int, default=1000)
    options.add_argument("--payload-size", type=int, default=64)
    options.add_argument(
        "--flood",
        action="store_true",
<<<<<<< HEAD
        help="Send raw packets without pacing; packet and duration limits still apply",
=======
        help="Send raw profile packets without pacing, still capped by count and time",
>>>>>>> 5e23f21557b30ec53f0429a17cc6561c89992a71
    )
    args = options.parse_args()

    if not 1 <= args.max_packets <= MAX_PACKET_CEILING:
<<<<<<< HEAD
        raise ValueError(f"max-packets must not exceed {MAX_PACKET_CEILING}")
=======
        raise ValueError(f"max-packets must be 1..{MAX_PACKET_CEILING}")
>>>>>>> 5e23f21557b30ec53f0429a17cc6561c89992a71
    if args.rate > MAX_RATE:
        raise ValueError(f"rate must not exceed {MAX_RATE}/s")
    if args.duration > MAX_DURATION:
        raise ValueError(f"duration must not exceed {MAX_DURATION}s")
    if not 1 <= args.payload_size <= 1024:
        raise ValueError("payload-size must be 1..1024 bytes")
    if args.flood and args.mode not in RAW_MODES:
<<<<<<< HEAD
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
=======
        raise ValueError("--flood is only available for the five raw packet modes")

    # Raw profiles use a single inventoried destination, independent of which
    # application protocols that node happens to expose.
    if args.mode in RAW_MODES:
        device = next(
            (item for item in inventory(args.inventory) if item["name"] == args.node),
            None,
        )
        if device is None:
            raise ValueError(f"{args.node} is not an inventoried node")
        host = ipv4(device["address"].split("/")[0])
        if args.host and ipv4(args.host) != host:
            raise ValueError(
                "Raw modes require the selected node's inventoried address"
            )
        port = (
            args.port
            if args.port is not None
            else (27015 if args.mode == "udp-vse" else 0)
        )
        if not 0 <= port <= 65535:
            raise ValueError("Port must be between 0 and 65535")
        if args.mode == "udp-vse" and port != 27015:
            raise ValueError("Gotham's Source Engine query uses UDP port 27015")
    elif args.mode == "connect":
>>>>>>> 5e23f21557b30ec53f0429a17cc6561c89992a71
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
<<<<<<< HEAD
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
=======
    if args.mode not in RAW_MODES:
        node = args.node if args.node != "MET-PLC-INTAKE-01" else default_node
        _, host, port = address_of(
            args,
            node,
            protocol,
            f"METROPOLIS_{prefix}_HOST",
            port_key,
            default_port,
        )
>>>>>>> 5e23f21557b30ec53f0429a17cc6561c89992a71

    with Run(
        args,
        "bounded_hping3_style",
        host=host,
        port=port,
        mode=args.mode,
        rate=None if args.flood else args.rate,
        flood=args.flood,
        duration=args.duration,
        max_packets=args.max_packets,
<<<<<<< HEAD
        payload_size=args.payload_size if args.mode in {"udp", "udp-raw"} else None,
        destination_ports="incrementing" if args.mode in TCP_RAW_MODES else "fixed",
=======
        payload_size=args.payload_size if args.mode == "udp" else None,
        destination_port_policy=(
            "incrementing" if args.mode in {"syn", "ack", "stomp"} else "fixed"
        ),
>>>>>>> 5e23f21557b30ec53f0429a17cc6561c89992a71
    ) as run:
        if args.dry_run:
            return 0
        outcomes: dict[str, int] = {}
        sent = 0
        started = time.monotonic()
<<<<<<< HEAD
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
=======
        indices = (
            flood_schedule(args.duration, args.max_packets)
            if args.flood
            else schedule(args.rate, args.duration, args.max_packets)
        )
        for index in indices:
            if args.mode in RAW_MODES:
                result = send_raw_packet(args.mode, host, port, index)
            elif args.mode == "connect":
                result = connect_probe(host, port, args.timeout)
            else:
                result = udp_probe(host, port, args.timeout, args.payload_size)
            outcomes[result] = outcomes.get(result, 0) + 1
            sent += 1
>>>>>>> 5e23f21557b30ec53f0429a17cc6561c89992a71
        elapsed = time.monotonic() - started
        run.emit(
            "summary",
            packets=sent,
            elapsed_seconds=round(elapsed, 3),
            achieved_rate=round(sent / elapsed, 1) if elapsed else None,
            outcomes=outcomes,
            note=(
                "Bounded packet stimulus only; measure the service before, during "
                "and after to evaluate availability"
            ),
        )
        return 0 if sent else 1


if __name__ == "__main__":
    raise SystemExit(cli(main))
