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


def play_choreography(
    run,
    *,
    cnc_host: str,
    cnc_port: int,
    listener_host: str,
    listener_port: int,
    loader_host: str,
    loader_port: int,
    bot_targets: list[tuple[str, int]],
    max_bots: int,
    interval: float,
    command: str,
    payload_path: str,
    timeout: float,
) -> int:
    """Generate the inert control sequence for the supplied simulated identities."""
    with Console(cnc_host, cnc_port, timeout) as console:
        run.emit("cnc_banner", banner=console.read_line())
        run.emit("cnc_registry_initial", bots=console.command("bots"))

    with socket.create_connection((listener_host, listener_port), timeout=timeout):
        run.emit("listener_reachable", host=listener_host, port=listener_port)

    new_bot_ids = []
    for index, (address, target_port) in enumerate(bot_targets):
        acknowledgement = bot_checkin(
            cnc_host, cnc_port, address, "linux-x64-synthetic", timeout
        )
        if not acknowledgement.startswith("ACK "):
            raise RuntimeError(f"Control service rejected simulated bot {index + 1}")
        bot_id = acknowledgement.removeprefix("ACK ").strip()
        if not bot_id:
            raise RuntimeError("Control service returned an empty bot ID")
        new_bot_ids.append(bot_id)
        run.emit("bot_checkin", bot=index + 1, address=address, reply=acknowledgement)
        report = {
            "bot_id": bot_id,
            "vulnerable": f"{address}:{target_port}",
            "method": "synthetic-choreography",
        }
        with socket.create_connection(
            (listener_host, listener_port), timeout=timeout
        ) as sock:
            sock.sendall((json.dumps(report) + "\n").encode())
        run.emit("bot_report", bot=index + 1, address=address)
        if index + 1 < len(bot_targets):
            time.sleep(interval)

    with Console(cnc_host, cnc_port, timeout) as console:
        console.read_line()
        registry = [line for line in console.command("bots") if line != "END"]
        registered = set()
        for line in registry:
            try:
                registered.add(json.loads(line)["bot_id"])
            except (json.JSONDecodeError, KeyError):
                continue
        run.emit("cnc_registry_final", count=len(registered))
        missing = set(new_bot_ids) - registered
        if missing:
            raise RuntimeError(
                "New simulated bots were absent from the control registry"
            )
        dispatched = 0
        for bot_id in new_bot_ids[:max_bots]:
            console.command(f"use {bot_id}", sentinel="")
            console.command(f"run {command}", sentinel="")
            console.command("back", sentinel="")
            dispatched += 1
    run.emit("commands_dispatched", count=dispatched)

    body = fetch_stub(loader_host, loader_port, payload_path, timeout)
    inert = body.lstrip().startswith(STUB_MARKER)
    run.emit("payload_delivery", bytes=len(body), inert_stub=inert)
    if not inert:
        raise RuntimeError("Loader did not return the expected inert stub")
    return dispatched


def main() -> int:
    options = parser(__doc__, "MET-MIRAI-CNC-01")
    options.add_argument(
        "--cnc-node", help="Mirai control node (auto-selected by role)"
    )
    options.add_argument(
        "--listener-node", help="Mirai report listener (auto-selected by role)"
    )
    options.add_argument(
        "--loader-node", help="HTTP payload loader (auto-selected by role)"
    )
    options.add_argument("--listener-host", help="Override the listener IPv4 address")
    options.add_argument("--loader-host", help="Override the loader IPv4 address")
    options.add_argument("--listener-port", type=int)
    options.add_argument("--loader-port", type=int)
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

    cnc, cnc_host, cnc_port = address_of(
        args,
        args.cnc_node,
        "mirai-cnc",
        "METROPOLIS_MIRAI_CNC_HOST",
        "MIRAI_CNC_PORT",
        23,
        use_common_overrides=True,
    )
    _, listener_host, listener_port = address_of(
        args,
        args.listener_node,
        "mirai-report",
        "METROPOLIS_MIRAI_LISTENER_HOST",
        "MIRAI_REPORT_PORT",
        48101,
        exclude={cnc["name"]},
        host_override=args.listener_host,
        port_override=args.listener_port,
        use_args_node=False,
        selection_option="--listener-node",
    )
    _, loader_host, loader_port = address_of(
        args,
        args.loader_node,
        "payload-delivery",
        "METROPOLIS_MIRAI_LOADER_HOST",
        "LOADER_PORT",
        80,
        env_equals=("LOADER_KIND", "wget"),
        host_override=args.loader_host,
        port_override=args.loader_port,
        use_args_node=False,
        selection_option="--loader-node",
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
        dispatched = play_choreography(
            run,
            cnc_host=cnc_host,
            cnc_port=cnc_port,
            listener_host=listener_host,
            listener_port=listener_port,
            loader_host=loader_host,
            loader_port=loader_port,
            bot_targets=[(f"10.99.10.{100 + index}", 23) for index in range(args.bots)],
            max_bots=args.max_bots,
            interval=args.interval,
            command=args.command,
            payload_path=args.payload_path,
            timeout=args.timeout,
        )

        run.emit(
            "summary",
            bots_checked_in=args.bots,
            commands_dispatched=dispatched,
            payload_inert=True,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(cli(main))
