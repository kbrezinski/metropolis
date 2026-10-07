#!/usr/bin/env python3
"""Find which lab hosts have the documented services listening.

Use this to generate reconnaissance traffic: a sweep of the inventory across
the port set the device models actually expose, so a capture contains the
discovery phase that would precede an attack.

The sweep stays deliberately narrow. Hosts default to the inventory rather than
an arbitrary range, an explicit --cidr must be a private network of at most 256
addresses, total probes are capped by --max-probes and --duration, and the port
set is the documented one rather than a full scan. It reports only that a port
answered, with no banner, version, or vulnerability claim, and it sends nothing
beyond a TCP connect or a single datagram.
"""

from __future__ import annotations

import ipaddress
import socket

from _common import Run, cli, inventory, ipv4, parser, positive, schedule

# Gotham's scanner covered the MQTT, CoAP, Telnet, and Modbus listeners.
# These are the Metropolis equivalents, split by transport.
TCP_PORTS = (22, 23, 53, 80, 443, 502, 1883, 8080, 8443)
UDP_PORTS = (53, 69, 123, 5683)
MAX_HOSTS = 256
MAX_PROBE_CEILING = 200_000
MAX_RATE = 2000.0
MAX_DURATION = 900.0
DEFAULT_PAYLOAD = b"metropolis-discovery"


def tcp_probe(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def udp_probe(host: str, port: int, timeout: float) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.sendto(DEFAULT_PAYLOAD, (host, port))
            sock.recvfrom(2048)
            return True
        except OSError:
            return False


def resolve_hosts(args) -> list[str]:
    """Hosts to sweep: an explicit private CIDR, or the inventoried addresses."""
    if args.cidr:
        subnet = ipaddress.IPv4Network(args.cidr, strict=True)
        if not subnet.is_private:
            raise ValueError("Discovery ranges must be private lab networks")
        hosts = [str(address) for address in subnet.hosts()]
        if len(hosts) > MAX_HOSTS:
            raise ValueError(
                f"Discovery ranges must contain at most {MAX_HOSTS} addresses"
            )
        return [ipv4(host) for host in hosts]
    records = inventory(args.inventory)
    if args.node != "all":
        records = [item for item in records if item["name"] == args.node]
        if not records:
            raise ValueError(f"{args.node} is not in the inventory")
    return sorted({ipv4(item["address"].split("/")[0]) for item in records})


def main() -> int:
    options = parser(__doc__, "all")
    options.add_argument(
        "--cidr", help="Optional private IPv4 range, maximum 256 addresses"
    )
    options.add_argument("--protocol", choices=["tcp", "udp", "both"], default="tcp")
    options.add_argument("--rate", type=positive, default=50)
    options.add_argument("--duration", type=positive, default=60)
    options.add_argument("--max-probes", type=int, default=2000)
    args = options.parse_args()

    if args.max_probes > MAX_PROBE_CEILING:
        raise ValueError(f"max-probes must not exceed {MAX_PROBE_CEILING}")
    if args.rate > MAX_RATE:
        raise ValueError(f"rate must not exceed {MAX_RATE}/s")
    if args.duration > MAX_DURATION:
        raise ValueError(f"duration must not exceed {MAX_DURATION}s")

    hosts = resolve_hosts(args)
    ports: list[tuple[str, int]] = []
    if args.protocol in {"tcp", "both"}:
        ports += [("tcp", port) for port in TCP_PORTS]
    if args.protocol in {"udp", "both"}:
        ports += [("udp", port) for port in UDP_PORTS]

    with Run(
        args,
        "iot_discovery",
        protocol=args.protocol,
        hosts=hosts,
        ports=[f"{transport}/{port}" for transport, port in ports],
        rate=args.rate,
        duration=args.duration,
        max_probes=args.max_probes,
    ) as run:
        if args.dry_run:
            return 0
        live: dict[str, list[str]] = {}
        probes = 0
        for index in schedule(args.rate, args.duration, args.max_probes):
            transport, port = ports[index % len(ports)]
            host = hosts[(index // len(ports)) % len(hosts)]
            found = (
                tcp_probe(host, port, args.timeout)
                if transport == "tcp"
                else udp_probe(host, port, args.timeout)
            )
            probes += 1
            if found:
                live.setdefault(host, []).append(f"{transport}/{port}")
                run.emit("listener", host=host, port=port, transport=transport)
        run.emit(
            "summary",
            probes=probes,
            hosts_reachable=len(live),
            live_services={
                host: sorted(set(items)) for host, items in sorted(live.items())
            },
            note="Connect/answer only; no service banner or version is claimed",
        )
        return 0 if probes else 1


if __name__ == "__main__":
    raise SystemExit(cli(main))
