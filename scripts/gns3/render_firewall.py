#!/usr/bin/env python3
"""Validate and render the dataset's VyOS firewall policy without GNS3."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _cli import DATASET, load_documents
from firewall_policy import compile_policy, parse_attack_exception


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument(
        "--attack-allow",
        action="append",
        default=[],
        metavar="SOURCE-NET:DEST-NET:PROTOCOL:PORT",
        help="Show a temporary attack-lab network allowance; repeat for multiple flows",
    )
    parser.add_argument(
        "--check", action="store_true", help="Print a summary instead of VyOS commands"
    )
    args = parser.parse_args()
    try:
        _, inventory, infrastructure, _ = load_documents(args.dataset)
        exceptions = [parse_attack_exception(item) for item in args.attack_allow]
        commands, report = compile_policy(
            args.dataset, infrastructure, inventory, exceptions
        )
    except (ValueError, KeyError, FileNotFoundError) as exc:
        parser.error(str(exc))
    if args.check:
        print(
            json.dumps(
                {
                    "baseline_flows": len(report["baseline_flows"]),
                    "attack_exceptions": len(report["attack_exceptions"]),
                    "local_subnet_flows": report["local_subnet_flows"],
                    "ipsec_tunnels": report.get("ipsec_tunnels", []),
                    "routers": {name: len(lines) for name, lines in commands.items()},
                },
                indent=2,
            )
        )
    else:
        print(json.dumps({"flows": report, "router_commands": commands}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
