#!/usr/bin/env python3
"""Inventory lab listeners with bounded TCP and protocol-aware UDP probes.

Every selected host/port is probed once by default. Extra passes generate
repeatable reconnaissance traffic and show whether a service changes over time.
An unanswered UDP probe is inconclusive; it is never called closed.
"""

from __future__ import annotations

import ipaddress
import secrets
import socket
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from _common import Run, cli, inventory, ipv4, parser, positive, schedule
from coap_amplification_attack import build_get, valid_response
from network_recon_scan import TCP_PORTS, UDP_PORTS

MAX_HOSTS = 256
MAX_PROBE_CEILING = 200_000
MAX_RATE = 2000.0
MAX_DURATION = 900.0
MAX_PORTS = 32
UDP_SERVICE = {53: "dns", 69: "tftp", 123: "ntp", 5683: "coap"}


def parse_ports(value: str) -> tuple[int, ...]:
    """Accept a short, concrete port list for this experiment."""
    try:
        ports = tuple(int(part) for part in value.split(","))
    except ValueError as exc:
        raise ValueError("Ports must be comma-separated numbers") from exc
    if not 1 <= len(ports) <= MAX_PORTS or any(not 1 <= p <= 65535 for p in ports):
        raise ValueError(f"Select 1–{MAX_PORTS} ports in the range 1–65535")
    if len(set(ports)) != len(ports):
        raise ValueError("Port lists must not contain duplicates")
    return ports


def tcp_probe(host: str, port: int, timeout: float) -> str:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "open"
    except ConnectionRefusedError:
        return "closed"
    except (socket.timeout, TimeoutError):
        return "no-response"
    except OSError:
        return "error"


def udp_request(kind: str) -> tuple[bytes, bytes]:
    """Return (request, correlation token) for one listening protocol."""
    if kind == "dns":
        token = secrets.token_bytes(2)
        question = b"\x0aplc-intake\x0ametropolis\x04test\x00\x00\x01\x00\x01"
        return token + b"\x01\x00\x00\x01" + b"\x00" * 6 + question, token
    if kind == "ntp":
        return b"\x23" + b"\x00" * 47, b""
    if kind == "coap":
        token = secrets.token_bytes(4)
        return build_get("/.well-known/core", token, secrets.randbelow(65536)), token
    if kind == "tftp":
        return b"\x00\x01metropolis-discovery\x00octet\x00", b""
    return b"metropolis-discovery", b""


def udp_reply_matches(kind: str, reply: bytes, token: bytes) -> bool:
    if kind == "dns":
        return len(reply) >= 12 and reply[:2] == token and bool(reply[2] & 0x80)
    if kind == "ntp":
        return len(reply) >= 48 and reply[0] & 7 == 4
    if kind == "coap":
        return valid_response(reply, token)
    if kind == "tftp":
        return len(reply) >= 4 and reply[:2] in {b"\x00\x03", b"\x00\x05"}
    return bool(reply)


def udp_probe(host: str, port: int, timeout: float, kind: str) -> str:
    request, token = udp_request(kind)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            # TFTP normally replies from a new transfer port. Other protocols
            # answer from the destination port and use a connected UDP socket.
            if kind == "tftp":
                sock.sendto(request, (host, port))
            else:
                sock.connect((host, port))
                sock.send(request)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                sock.settimeout(max(0.001, deadline - time.monotonic()))
                if kind == "tftp":
                    reply, peer = sock.recvfrom(2048)
                    if peer[0] != host:
                        continue
                else:
                    reply = sock.recv(2048)
                if udp_reply_matches(kind, reply, token):
                    return "open"
        except ConnectionRefusedError:
            return "closed"
        except (socket.timeout, TimeoutError):
            return "no-response"
        except OSError:
            return "error"
    return "no-response"


def resolve_hosts(args) -> list[str]:
    if sum(bool(item) for item in (args.host, args.cidr, args.node != "all")) > 1:
        raise ValueError("Select only one of --host, --cidr, or --node")
    if args.host:
        return [ipv4(args.host)]
    if args.cidr:
        subnet = ipaddress.IPv4Network(args.cidr, strict=True)
        if not subnet.is_private or subnet.num_addresses > MAX_HOSTS:
            raise ValueError(
                f"Discovery ranges must be private and at most {MAX_HOSTS} addresses"
            )
        return [ipv4(str(address)) for address in subnet.hosts()]
    records = inventory(args.inventory)
    if args.node != "all":
        records = [item for item in records if item["name"] == args.node]
        if not records:
            raise ValueError(f"{args.node} is not in the inventory")
    return sorted({ipv4(item["address"].split("/")[0]) for item in records})


def probe(host: str, transport: str, port: int, timeout: float, udp_style: str) -> str:
    if transport == "tcp":
        return tcp_probe(host, port, timeout)
    kind = UDP_SERVICE.get(port, "generic") if udp_style == "service" else "generic"
    return udp_probe(host, port, timeout, kind)


def main() -> int:
    options = parser(__doc__, "all")
    options.add_argument("--cidr", help="Private IPv4 range of at most 256 addresses")
    options.add_argument("--protocol", choices=["tcp", "udp", "both"], default="both")
    options.add_argument("--tcp-ports", default=",".join(map(str, TCP_PORTS)))
    options.add_argument("--udp-ports", default=",".join(map(str, UDP_PORTS)))
    options.add_argument(
        "--udp-style", choices=["service", "generic"], default="service"
    )
    options.add_argument("--passes", type=int, default=1)
    options.add_argument("--workers", type=int, default=16)
    options.add_argument("--rate", type=positive, default=50)
    options.add_argument(
        "--duration", type=positive, help="Optional launch-time limit in seconds"
    )
    options.add_argument("--max-probes", type=int, default=4096)
    args = options.parse_args()

    if args.port is not None:
        raise ValueError("Use --tcp-ports or --udp-ports instead of --port")
    if not 1 <= args.max_probes <= MAX_PROBE_CEILING:
        raise ValueError(f"max-probes must be 1–{MAX_PROBE_CEILING}")
    if not 1 <= args.passes <= 5 or not 1 <= args.workers <= 32:
        raise ValueError("passes must be 1–5 and workers must be 1–32")
    if args.rate > MAX_RATE or (
        args.duration is not None and args.duration > MAX_DURATION
    ):
        raise ValueError(
            f"rate must be <= {MAX_RATE}/s and duration <= {MAX_DURATION}s"
        )

    hosts = resolve_hosts(args)
    tcp_ports = parse_ports(args.tcp_ports) if args.protocol in {"tcp", "both"} else ()
    udp_ports = parse_ports(args.udp_ports) if args.protocol in {"udp", "both"} else ()
    ports = [("tcp", p) for p in tcp_ports] + [("udp", p) for p in udp_ports]
    tasks = [
        (pass_number, host, transport, port)
        for pass_number in range(1, args.passes + 1)
        for host in hosts
        for transport, port in ports
    ]
    if not tasks:
        raise ValueError("Select at least one host and one port")

    with Run(
        args,
        "iot_discovery",
        protocol=args.protocol,
        hosts=hosts,
        ports=[f"{transport}/{port}" for transport, port in ports],
        planned=len(tasks),
        passes=args.passes,
        workers=args.workers,
        rate=args.rate,
        duration=args.duration,
        max_probes=args.max_probes,
        udp_style=args.udp_style,
    ) as run:
        if args.dry_run:
            return 0

        counts: dict[str, int] = {}
        live: dict[str, set[str]] = {}
        inconclusive: dict[str, set[str]] = {}
        attempted = 0

        def record(future, task) -> None:
            nonlocal attempted
            pass_number, host, transport, port = task
            status = future.result()
            attempted += 1
            counts[status] = counts.get(status, 0) + 1
            label = f"{transport}/{port}"
            if status == "open":
                live.setdefault(host, set()).add(label)
                inconclusive.get(host, set()).discard(label)
            elif status in {"no-response", "error"} and label not in live.get(
                host, set()
            ):
                inconclusive.setdefault(host, set()).add(label)
            run.emit(
                "probe",
                pass_number=pass_number,
                host=host,
                port=port,
                transport=transport,
                status=status,
            )

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            pending = {}
            for index in schedule(
                args.rate,
                args.duration if args.duration is not None else float("inf"),
                min(len(tasks), args.max_probes),
            ):
                if len(pending) >= args.workers * 2:
                    done, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        record(future, pending.pop(future))
                task = tasks[index]
                pending[
                    executor.submit(probe, *task[1:], args.timeout, args.udp_style)
                ] = task
            for future, task in list(pending.items()):
                record(future, task)

        complete = attempted == len(tasks)
        run.emit(
            "summary",
            complete=complete,
            planned=len(tasks),
            attempted=attempted,
            untested=len(tasks) - attempted,
            status_counts=counts,
            live_services={host: sorted(items) for host, items in sorted(live.items())},
            inconclusive_services={
                host: sorted(items) for host, items in sorted(inconclusive.items())
            },
        )
        return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(cli(main))
