#!/usr/bin/env python3
"""Run one inert Merlin experiment through the actual simulated agent node.

The agent must already be running in the lab. This driver uses the control
console, waits for that agent to check in, queues a simulated command and an
artefact request, then verifies the agent's result reports and upload.
"""

from __future__ import annotations

import json
import os
import time

from _c2 import Console
from _common import Run, address_of, cli, host_of, parser, positive
from run_merlin import http


def matching_agent(console: Console, address: str) -> dict | None:
    for line in console.command("agent list"):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("address") == address and row.get("peer_address") == address:
            return row
    return None


def wait_for(check, seconds: float, interval: float):
    deadline = time.monotonic() + seconds
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() >= deadline:
            return None
        time.sleep(min(interval, max(0, deadline - time.monotonic())))


def latest_sequence(rows: list[dict]) -> int:
    return max((item["sequence"] for item in rows), default=0)


def main() -> int:
    options = parser(__doc__, "MET-MERLIN-CNC-01")
    options.add_argument("--cnc-node", help="Merlin control node")
    options.add_argument("--agent-node", help="Running Merlin agent node")
    options.add_argument("--agent-host", help="Override the agent IPv4 address")
    options.add_argument("--http-port", type=int, help="CNC agent-channel port")
    options.add_argument("--wait-seconds", type=positive, default=90.0)
    options.add_argument("--poll-interval", type=positive, default=1.0)
    options.add_argument(
        "--command",
        default="chmod 775 /opt/metropolis",
        help="Command simulated by the agent; no shell is run",
    )
    args = options.parse_args()
    if args.wait_seconds > 600 or args.poll_interval > 30:
        raise ValueError(
            "wait-seconds must be at most 600 and poll-interval at most 30"
        )
    if not args.command.strip() or "\n" in args.command or "\r" in args.command:
        raise ValueError("command must be one nonempty line")

    cnc, cnc_host, cnc_port = address_of(
        args,
        args.cnc_node,
        "merlin-control",
        "METROPOLIS_MERLIN_CNC_HOST",
        "MERLIN_CONTROL_PORT",
        23,
        use_common_overrides=True,
    )
    agent, agent_host = host_of(
        args,
        args.agent_node,
        "merlin-agent",
        "METROPOLIS_MERLIN_AGENT_HOST",
        exclude={cnc["name"]},
        host_override=args.agent_host,
        use_args_node=False,
        selection_option="--agent-node",
    )
    http_port = int(
        args.http_port
        or os.getenv("METROPOLIS_MERLIN_HTTP_PORT")
        or cnc.get("environment", {}).get("MERLIN_HTTP_PORT", 8443)
    )
    if not 1 <= http_port <= 65535:
        raise ValueError("Merlin HTTP port is out of range")

    with Run(
        args,
        "merlin_scenario",
        cnc=f"{cnc_host}:{cnc_port}",
        http=f"{cnc_host}:{http_port}",
        agent=agent_host,
        agent_node=agent["name"],
        transport="plain HTTP on the agent channel",
        wait_seconds=args.wait_seconds,
    ) as run:
        if args.dry_run:
            return 0

        with Console(cnc_host, cnc_port, args.timeout) as console:
            run.emit("cnc_banner", banner=console.read_line())
            console.command("use https", sentinel="")
            console.command("set Interface 0.0.0.0", sentinel="")
            run.emit("listener_start", reply=console.command("start", sentinel=""))

            row = wait_for(
                lambda: matching_agent(console, agent_host),
                args.wait_seconds,
                args.poll_interval,
            )
            if row is None:
                raise RuntimeError(
                    f"No check-in from running agent {agent['name']} at {agent_host}; "
                    "verify it is started and uses the updated image"
                )
            agent_id = row["agent_id"]
            run.emit(
                "agent_checkin_observed",
                agent_id=agent_id,
                address=agent_host,
                peer_address=row["peer_address"],
            )

            def status():
                return http(
                    cnc_host,
                    http_port,
                    "GET",
                    f"/status/{agent_id}",
                    b"",
                    args.timeout,
                )

            baseline = status()
            if baseline.get("agent_id") != agent_id:
                raise RuntimeError("CNC did not expose the selected agent's status")
            selected = console.command(f"agent interact {agent_id}", sentinel="")
            if not any("interacting with" in line for line in selected):
                raise RuntimeError("CNC did not select the expected agent")

            def queue(command: str):
                reply = console.command(f"run {command}", sentinel="")
                if not any("queued for" in line for line in reply):
                    raise RuntimeError(f"CNC did not queue {command!r}")
                run.emit("command_queued", command=command, agent_id=agent_id)

            def result_after(command: str, previous_sequence: int):
                current = status()
                for result in current.get("results", []):
                    if (
                        result.get("sequence", 0) > previous_sequence
                        and result.get("command") == command
                    ):
                        return result
                return None

            queue(args.command)
            result = wait_for(
                lambda: result_after(
                    args.command, latest_sequence(baseline["results"])
                ),
                args.wait_seconds,
                args.poll_interval,
            )
            if result is None:
                raise RuntimeError("Agent did not report a result for the command")
            run.emit("agent_command_result", command=args.command, ok=result["ok"])
            if not result["ok"]:
                raise RuntimeError("Agent refused or failed the simulated command")

            before_upload = status()
            upload_command = "upload metropolis-scenario"
            queue(upload_command)
            upload_result = wait_for(
                lambda: result_after(
                    upload_command, latest_sequence(before_upload["results"])
                ),
                args.wait_seconds,
                args.poll_interval,
            )
            if upload_result is None or not upload_result["ok"]:
                raise RuntimeError("Agent did not complete the synthetic upload")
            after_upload = status()
            previous_upload = latest_sequence(before_upload["uploads"])
            new_uploads = [
                item
                for item in after_upload["uploads"]
                if item["sequence"] > previous_upload
            ]
            if not new_uploads or not any(item["bytes"] > 0 for item in new_uploads):
                raise RuntimeError("CNC has no new nonempty upload from the agent")
            run.emit("agent_upload", agent_id=agent_id, uploads=new_uploads)

        run.emit(
            "summary",
            agent_id=agent_id,
            command_reported=True,
            upload_verified=True,
            simulated=True,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(cli(main))
