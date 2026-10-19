#!/usr/bin/env python3
"""Check the Metropolis lab setup for a researcher supplied inert Mirai bot."""

from __future__ import annotations

from _common import Run, cli
from _external_setup import (
    device,
    host,
    live_nodes,
    options,
    require_inventory_name,
    require_running,
)


def main() -> int:
    parser = options(__doc__, "--external-bot", "--start-inert-bot")
    args = parser.parse_args()
    cnc = device(args, "MET-MIRAI-CNC-01", "mirai-cnc")
    listener = device(args, "MET-MIRAI-LISTENER-01", "mirai-report")
    loader = device(args, "MET-MIRAI-LOADER-WGET-01", "payload-delivery")
    target = device(args, "MET-LEGACY-GATEWAY-01", "telnet")
    example = require_inventory_name(args, "MET-MIRAI-BOT-01")
    source = "user_supplied" if args.external_bot else "repository_example"
    with Run(
        args,
        "gotham_mirai_setup",
        agent_source=source,
        project=args.project,
        cnc=f"{host(cnc)}:{cnc['environment']['MIRAI_REPORT_PORT']}",
        report=f"{host(listener)}:{listener['environment']['MIRAI_REPORT_PORT']}",
        loader=f"http://{host(loader)}:{loader['environment']['LOADER_PORT']}",
        telnet_target=f"{host(target)}:23",
        telnet_user="root",
        credential_source="MET-LEGACY-GATEWAY-01 environment: LEGACY_ROOT_PASSWORD",
        example_bot=example["name"],
    ) as run:
        if args.dry_run:
            return 0
        client, project_id, nodes = live_nodes(args.project)
        require_running(
            nodes, cnc["name"], listener["name"], loader["name"], target["name"]
        )
        if args.start_inert_bot:
            if example["name"] not in nodes:
                raise ValueError(f"GNS3 node {example['name']} is missing")
            if nodes[example["name"]].get("status") != "started":
                client.start_node(project_id, nodes[example["name"]]["node_id"])
            run.emit("example_started", node=example["name"])
        else:
            run.emit("external_ready", note="Start your own inert bot on a lab node")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli(main))
