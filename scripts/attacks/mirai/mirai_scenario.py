#!/usr/bin/env python3
"""Run Telnet access and the inert Mirai control sequence as one experiment.

A verified Telnet command permits a synthetic bot identity to be registered.
No bot is installed on the target, and the control command is never executed.
"""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

from _common import (
    Run,
    address_of,
    cli,
    inventory,
    ipv4,
    parser,
    passwords,
    positive,
)
from _telnet import Rejected, Session
from run_mirai_choreography import play_choreography


def telnet_targets(args) -> list[tuple[dict, str, int, str, list[str]]]:
    records = inventory(args.inventory)
    by_name = {item["name"]: item for item in records}
    names = args.target_node or [
        item["name"] for item in records if "telnet" in item.get("protocols", [])
    ]
    if not names or len(names) > args.max_bots or len(names) != len(set(names)):
        raise ValueError("Select 1..max-bots distinct Telnet target nodes")
    targets = []
    for name in names:
        item = by_name.get(name)
        if item is None or "telnet" not in item.get("protocols", []):
            raise ValueError(f"{name} is not an inventoried Telnet target")
        environment = item.get("environment", {})
        host = ipv4(item["address"].split("/")[0])
        port = int(environment.get("TELNET_PORT", 23))
        if not 1 <= port <= 65535:
            raise ValueError(f"{name} has an invalid TELNET_PORT")
        username = args.username or environment.get("TELNET_USERNAME", "root")
        good = os.getenv("METROPOLIS_TELNET_PASSWORD") or environment.get(
            "LEGACY_ROOT_PASSWORD"
        )
        if not good and not args.wordlist:
            raise ValueError(
                f"{name} has no credential fixture; provide --wordlist or "
                "METROPOLIS_TELNET_PASSWORD"
            )
        candidates = passwords(args.wordlist, good or "", args.max_attempts)
        targets.append((item, host, port, username, candidates))
    return targets


def verify_telnet(run, target, timeout: float, interval: float) -> bool:
    item, host, port, username, candidates = target
    for index, candidate in enumerate(candidates, start=1):
        session = None
        try:
            session = Session(host, port, timeout)
            session.login(username, candidate)
            verified = session.prove_command(
                "metropolis_" + secrets.token_hex(16), False
            )
            run.emit(
                "telnet_attempt",
                node=item["name"],
                host=host,
                attempt=index,
                status="accepted" if verified else "unverified",
                command_verified=verified,
            )
            return verified
        except Rejected:
            run.emit(
                "telnet_attempt",
                node=item["name"],
                host=host,
                attempt=index,
                status="rejected",
            )
        except (OSError, RuntimeError) as exc:
            run.emit(
                "telnet_attempt",
                node=item["name"],
                host=host,
                attempt=index,
                status="indeterminate",
                error=str(exc),
            )
        finally:
            if session is not None:
                session.close()
        if index < len(candidates):
            time.sleep(interval)
    return False


def main() -> int:
    options = parser(__doc__, "MET-MIRAI-CNC-01")
    options.add_argument("--target-node", action="append", help="Telnet target; repeat")
    options.add_argument("--username", help="Override each target's Telnet username")
    options.add_argument("--wordlist", type=Path)
    options.add_argument("--max-attempts", type=int, default=20)
    options.add_argument("--attempt-interval", type=positive, default=1.0)
    options.add_argument("--cnc-node", help="Mirai control node")
    options.add_argument("--listener-node", help="Mirai report listener")
    options.add_argument("--loader-node", help="HTTP payload loader")
    options.add_argument("--listener-host", help="Override listener IPv4 address")
    options.add_argument("--loader-host", help="Override loader IPv4 address")
    options.add_argument("--listener-port", type=int)
    options.add_argument("--loader-port", type=int)
    options.add_argument("--max-bots", type=int, default=16)
    options.add_argument("--interval", type=positive, default=1.0)
    options.add_argument("--payload-path", default="/mirai.synthetic")
    options.add_argument(
        "--command",
        default="echo metropolis-synthetic-bot",
        help="Command recorded by the inert control service; never executed",
    )
    args = options.parse_args()
    if not 1 <= args.max_bots <= 64:
        raise ValueError("max-bots must be 1..64")
    targets = telnet_targets(args)

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
    with Run(
        args,
        "mirai_scenario",
        targets=[item[0]["name"] for item in targets],
        cnc=f"{cnc_host}:{cnc_port}",
        listener=f"{listener_host}:{listener_port}",
        loader=f"{loader_host}:{loader_port}",
        attempts_per_target=[len(item[4]) for item in targets],
        transition="verified Telnet command permits simulated check-in; no infection",
    ) as run:
        if args.dry_run:
            return 0

        verified_targets = []
        for target in targets:
            if verify_telnet(run, target, args.timeout, args.attempt_interval):
                verified_targets.append((target[1], target[2]))
        run.emit(
            "telnet_stage_complete",
            targets=len(targets),
            verified=len(verified_targets),
        )
        if not verified_targets:
            run.emit("summary", outcome="no_verified_login", control_stage="skipped")
            return 1

        run.emit(
            "simulation_transition",
            source_hosts=[host for host, _ in verified_targets],
            note="No payload was installed and no target runs a bot",
        )
        dispatched = play_choreography(
            run,
            cnc_host=cnc_host,
            cnc_port=cnc_port,
            listener_host=listener_host,
            listener_port=listener_port,
            loader_host=loader_host,
            loader_port=loader_port,
            bot_targets=verified_targets,
            max_bots=args.max_bots,
            interval=args.interval,
            command=args.command,
            payload_path=args.payload_path,
            timeout=args.timeout,
        )
        run.emit(
            "summary",
            outcome="simulated_sequence_complete",
            telnet_verified=len(verified_targets),
            bots_checked_in=len(verified_targets),
            commands_dispatched=dispatched,
            payload_inert=True,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(cli(main))
