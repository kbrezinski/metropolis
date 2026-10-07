#!/usr/bin/env python3
"""Play out a full Mirai infection lifecycle across the attack-lab nodes.

Use this to generate botnet-shaped traffic in one run. Each bot checks in with
the command console and reports to the scan listener, the console's registry is
read back, a command is dispatched to every enrolled bot, and the loader's
payload is fetched.

Nothing here is malware and nothing is executed. The loaders serve a text
marker, and the console records commands without running them, so a capture
shows the control conversation while the testbed stays inert.

Every step is recorded as UTC JSONL on stdout so tool actions line up with the
packets.
"""

from __future__ import annotations

import json
import socket
import time

from _c2 import Console
from _common import Run, address_of, cli, ipv4, parser, positive

# Marker served by the synthetic loader; proves the delivery path works while
# confirming the payload is inert text rather than a binary.
STUB_MARKER = b"metropolis-synthetic-payload-stub"


def fetch_stub(host: str, port: int, path: str, timeout: float) -> bytes:
    """Fetch the loader stub over HTTP and return the body."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(
            f"GET {path} HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode()
        )
        chunks = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    _, _, body = b"".join(chunks).partition(b"\r\n\r\n")
    return body


def bot_checkin(
    cnc_host: str, cnc_port: int, address: str, arch: str, timeout: float
) -> str:
    """Register one synthetic bot and return the CNC's acknowledgement."""
    with socket.create_connection((cnc_host, cnc_port), timeout=timeout) as sock:
        sock.sendall(f"CHECKIN {address} {arch}\n".encode())
        sock.settimeout(timeout)
        return sock.recv(256).decode("utf-8", errors="replace").strip()


def main() -> int:
    options = parser(__doc__, "MET-MIRAI-CNC-01")
    options.add_argument("--cnc-node", default="MET-MIRAI-CNC-01")
    options.add_argument("--listener-node", default="MET-MIRAI-LISTENER-01")
    options.add_argument("--loader-node", default="MET-MIRAI-LOADER-WGET-01")
    options.add_argument("--bots", type=int, default=3)
    options.add_argument("--max-bots", type=int, default=16)
    options.add_argument("--interval", type=positive, default=1.0)
    options.add_argument("--payload-path", default="/mirai.synthetic")
    options.add_argument(
        "--command",
        default="echo metropolis-synthetic-bot",
        help="command recorded against each enrolled bot (never executed)",
    )
    args = options.parse_args()

    if not 1 <= args.bots <= args.max_bots <= 64:
        raise ValueError("bots must be 1..max-bots and max-bots at most 64")

    _, cnc_host, cnc_port = address_of(
        args, args.cnc_node, "mirai-cnc", "METROPOLIS_MIRAI_CNC_HOST"
    )
    _, listener_host, listener_port = address_of(
        args, args.listener_node, "mirai-report", "METROPOLIS_MIRAI_LISTENER_HOST"
    )
    _, loader_host, loader_port = address_of(
        args, args.loader_node, "payload-delivery", "METROPOLIS_MIRAI_LOADER_HOST"
    )
    ipv4(cnc_host)
    ipv4(listener_host)
    ipv4(loader_host)

    with Run(
        args,
        "mirai_choreography",
        cnc=f"{cnc_host}:{cnc_port}",
        listener=f"{listener_host}:{listener_port}",
        loader=f"{loader_host}:{loader_port}",
        bots=args.bots,
    ) as run:
        if args.dry_run:
            return 0

        # 1. Reach the control console and the scan listener.
        with Console(cnc_host, cnc_port, args.timeout) as console:
            banner = console.read_line()
            run.emit("cnc_banner", banner=banner)
            enrolled = console.command("bots")
        run.emit("cnc_registry_initial", bots=enrolled)

        with socket.create_connection(
            (listener_host, listener_port), timeout=args.timeout
        ):
            run.emit("listener_reachable", host=listener_host, port=listener_port)

        # 2. Let each bot check in and report.
        for index in range(args.bots):
            address = f"10.99.10.{100 + index}"
            acknowledgement = bot_checkin(
                cnc_host, cnc_port, address, "linux-x64-synthetic", args.timeout
            )
            run.emit(
                "bot_checkin", bot=index + 1, address=address, reply=acknowledgement
            )
            report = {
                "bot_id": acknowledgement.split(" ", 1)[-1],
                "vulnerable": f"{address}:23",
                "method": "synthetic-choreography",
            }
            with socket.create_connection(
                (listener_host, listener_port), timeout=args.timeout
            ) as sock:
                sock.sendall((json.dumps(report) + "\n").encode())
            run.emit("bot_report", bot=index + 1, address=address)
            time.sleep(args.interval)

        # 3. Read the registry back, then dispatch a bounded command.
        with Console(cnc_host, cnc_port, args.timeout) as console:
            console.read_line()
            registry = [line for line in console.command("bots") if line != "END"]
            enrolled_ids = []
            for line in registry:
                try:
                    enrolled_ids.append(json.loads(line)["bot_id"])
                except (json.JSONDecodeError, KeyError):
                    continue
            run.emit("cnc_registry_final", count=len(enrolled_ids))
            dispatched = 0
            for bot_id in enrolled_ids[: args.max_bots]:
                console.command(f"use {bot_id}", sentinel="")
                console.command(f"run {args.command}", sentinel="")
                console.command("back", sentinel="")
                dispatched += 1
        run.emit("commands_dispatched", count=dispatched)

        # 4. Confirm the loader serves an inert stub, not an executable.
        body = fetch_stub(loader_host, loader_port, args.payload_path, args.timeout)
        inert = body.lstrip().startswith(STUB_MARKER)
        run.emit(
            "payload_delivery",
            node=args.loader_node,
            bytes=len(body),
            inert_stub=inert,
        )
        if not inert:
            raise RuntimeError("Loader did not return the expected inert stub")

        run.emit(
            "summary",
            bots_checked_in=args.bots,
            commands_dispatched=dispatched,
            payload_inert=True,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(cli(main))
