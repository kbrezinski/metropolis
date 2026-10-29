#!/usr/bin/env python3
"""Run one inert Merlin experiment through the actual simulated agent node.

The agent must already be running in the lab. This driver uses the control
console, waits for that agent to check in, transfers an inert text marker to it,
then verifies its allowlisted command result.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from _c2 import Console
from _common import Run, address_of, cli, host_of, parser, positive
from run_merlin import http

INERT_SOURCE = "/opt/metropolis/inert-hping3.txt"
DEFAULT_INERT_DESTINATION = "/tmp/metropolis-merlin/hping3.marker"
DEFAULT_CA_FILE = Path(__file__).resolve().parents[3] / "certs/merlin/ca.crt"


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
    options.add_argument(
        "--https-port",
        "--http-port",
        dest="agent_port",
        type=int,
        help="CNC HTTPS agent-channel port",
    )
    options.add_argument(
        "--ca-file",
        type=Path,
        default=DEFAULT_CA_FILE,
        help="Lab CA certificate used to verify the Merlin CNC",
    )
    options.add_argument("--wait-seconds", type=positive, default=90.0)
    options.add_argument("--poll-interval", type=positive, default=1.0)
    options.add_argument(
        "--command",
        default=None,
        help="Allowlisted command after the inert transfer; no shell is run",
    )
    args = options.parse_args()
    if args.wait_seconds > 600 or args.poll_interval > 30:
        raise ValueError(
            "wait-seconds must be at most 600 and poll-interval at most 30"
        )
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
    inert_destination = agent.get("environment", {}).get(
        "MERLIN_MARKER_PATH", DEFAULT_INERT_DESTINATION
    )
    args.command = args.command or f"chmod 775 {inert_destination}"
    if not args.command.strip() or "\n" in args.command or "\r" in args.command:
        raise ValueError("command must be one nonempty line")
    agent_port = int(
        args.agent_port
        or os.getenv("METROPOLIS_MERLIN_HTTP_PORT")
        or cnc.get("environment", {}).get("MERLIN_HTTP_PORT", 8443)
    )
    if not 1 <= agent_port <= 65535:
        raise ValueError("Merlin HTTPS port is out of range")

    with Run(
        args,
        "merlin_scenario",
        cnc=f"{cnc_host}:{cnc_port}",
        https=f"{cnc_host}:{agent_port}",
        agent=agent_host,
        agent_node=agent["name"],
        transport="certificate-verified HTTPS on the agent channel",
        wait_seconds=args.wait_seconds,
    ) as run:
        if args.dry_run:
            return 0
        if not args.ca_file.is_file():
            raise ValueError(f"Merlin CA certificate is missing: {args.ca_file}")

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
                    agent_port,
                    "GET",
                    f"/status/{agent_id}",
                    b"",
                    args.timeout,
                    args.ca_file,
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

            transfer_command = f"upload {INERT_SOURCE} {inert_destination}"
            transfer_reply = console.command(transfer_command, sentinel="")
            if not any("queued inert transfer" in line for line in transfer_reply):
                raise RuntimeError("CNC did not queue the inert transfer")
            run.emit("tool_transfer_queued", agent_id=agent_id)
            transfer_result = wait_for(
                lambda: result_after(
                    transfer_command, latest_sequence(baseline["results"])
                ),
                args.wait_seconds,
                args.poll_interval,
            )
            if transfer_result is None or not transfer_result["ok"]:
                raise RuntimeError("Agent did not receive the inert marker")
            after_transfer = status()
            new_transfers = [
                item
                for item in after_transfer["transfers"]
                if item["sequence"] > latest_sequence(baseline["transfers"])
            ]
            if not any(
                item["bytes"] > 0 and item["peer_address"] == agent_host
                for item in new_transfers
            ):
                raise RuntimeError("CNC has no nonempty transfer to the selected agent")
            run.emit("tool_transfer", agent_id=agent_id, transfers=new_transfers)

            queue(args.command)
            result = wait_for(
                lambda: result_after(
                    args.command, latest_sequence(after_transfer["results"])
                ),
                args.wait_seconds,
                args.poll_interval,
            )
            if result is None:
                raise RuntimeError("Agent did not report a result for the command")
            run.emit("agent_command_result", command=args.command, ok=result["ok"])
            if not result["ok"]:
                raise RuntimeError("Agent refused or failed the simulated command")

        run.emit(
            "summary",
            agent_id=agent_id,
            command_reported=True,
            transfer_verified=True,
            simulated=True,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(cli(main))
