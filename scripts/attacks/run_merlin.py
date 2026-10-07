#!/usr/bin/env python3
"""Play out a Merlin C2 session against the synthetic control node.

Use this to generate command-and-control traffic in one run. The console starts
an HTTPS listener, an agent checks in, the console lists and selects it, a
command is queued, and the agent polls it and uploads an artefact.

Nothing here is malware and nothing is executed. The agent simulates a fixed
allow-list of commands and refuses the rest, and the console records what it
queues without running it, so the dialog is capturable while the testbed stays
inert.
"""

from __future__ import annotations

import json
import os
import socket

from _c2 import Console
from _common import Run, address_of, cli, host_of, ipv4, parser, positive


def http(
    host: str, port: int, method: str, path: str, body: bytes, timeout: float
) -> dict:
    """Minimal HTTP exchange against the CNC agent channel."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        head = (
            f"{method} {path} HTTP/1.0\r\nHost: {host}\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        ).encode()
        sock.sendall(head + body)
        chunks = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    _, _, payload = b"".join(chunks).partition(b"\r\n\r\n")
    if not payload.strip():
        return {}
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {"raw": payload.decode("utf-8", errors="replace")}


def main() -> int:
    options = parser(__doc__, "MET-MERLIN-CNC-01")
    options.add_argument("--cnc-node", default="MET-MERLIN-CNC-01")
    options.add_argument("--agent-node", default="MET-MERLIN-AGENT-01")
    options.add_argument("--listener-scheme", default="https")
    options.add_argument("--max-agents", type=int, default=4)
    options.add_argument("--interval", type=positive, default=1.0)
    options.add_argument(
        "--command",
        default="chmod 775 /opt/metropolis",
        help="command queued for the agent (simulated, never executed)",
    )
    options.add_argument(
        "--upload-bytes",
        type=int,
        default=64,
        help="size of the synthetic artefact the agent uploads",
    )
    args = options.parse_args()

    if not 1 <= args.max_agents <= 32:
        raise ValueError("max-agents must be 1..32")
    if not 1 <= args.upload_bytes <= 4096:
        raise ValueError("upload-bytes must be 1..4096")

    _, cnc_host, cnc_port = address_of(
        args, args.cnc_node, "merlin-control", "METROPOLIS_MERLIN_CNC_HOST"
    )
    _, agent_host = host_of(
        args, args.agent_node, "merlin-agent", "METROPOLIS_MERLIN_AGENT_HOST"
    )
    http_port = int(os.getenv("METROPOLIS_MERLIN_HTTP_PORT") or args.port or 8443)
    if not 1 <= http_port <= 65535:
        raise ValueError("Merlin HTTP port is out of range")
    ipv4(cnc_host)
    ipv4(agent_host)

    with Run(
        args,
        "merlin_choreography",
        cnc=f"{cnc_host}:{cnc_port}",
        http=f"{cnc_host}:{http_port}",
        agent=agent_host,
        listener=args.listener_scheme,
    ) as run:
        if args.dry_run:
            return 0

        # Gotham's console sequence, in the same order.
        with Console(cnc_host, cnc_port, args.timeout) as console:
            run.emit("cnc_banner", banner=console.read_line())
            run.emit("listeners", reply=console.command("listeners", sentinel=""))
            console.command("use https", sentinel="")
            console.command("set Interface 0.0.0.0", sentinel="")
            run.emit("listener_start", reply=console.command("start", sentinel=""))
            run.emit("listener_info", reply=console.command("info", sentinel=""))

            # Agent check-in over the HTTP channel.
            checkin = http(
                cnc_host,
                http_port,
                "POST",
                "/checkin",
                json.dumps(
                    {"address": agent_host, "arch": "linux-x64-synthetic"}
                ).encode(),
                args.timeout,
            )
            agent_id = checkin.get("agent_id")
            run.emit("agent_checkin", agent_id=agent_id, address=agent_host)
            if not agent_id:
                raise RuntimeError("Agent did not receive an id from the CNC")

            # Gotham lists agents via the console and reads the uuid back.
            rows = [line for line in console.command("agent list") if line != "END"]
            run.emit("agent_list", count=len(rows), rows=rows[: args.max_agents])

            console.command(f"agent interact {agent_id}", sentinel="")
            run.emit(
                "run_queued", reply=console.command(f"run {args.command}", sentinel="")
            )
            console.command("back", sentinel="")

        # The agent collects its command and answers with an upload.
        pending = http(
            cnc_host, http_port, "GET", f"/commands/{agent_id}", b"", args.timeout
        )
        run.emit("agent_poll", commands=pending.get("commands", []))

        artefact = f"metropolis-merlin-artefact {agent_id}\n".encode()[
            : args.upload_bytes
        ]
        stored = http(
            cnc_host, http_port, "POST", f"/upload/{agent_id}", artefact, args.timeout
        )
        run.emit(
            "agent_upload", bytes=len(artefact), stored=stored.get("stored", False)
        )

        run.emit(
            "summary",
            listener=args.listener_scheme,
            agents=1,
            commands_queued=1,
            upload_bytes=len(artefact),
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(cli(main))
