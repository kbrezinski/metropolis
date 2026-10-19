#!/usr/bin/env python3
"""Check the Metropolis lab setup for a researcher supplied inert Merlin agent."""

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
    parser = options(__doc__, "--external-agent", "--start-inert-agent")
    args = parser.parse_args()
    cnc = device(args, "MET-MERLIN-CNC-01", "merlin-control")
    example = require_inventory_name(args, "MET-MERLIN-AGENT-01")
    source = "user_supplied" if args.external_agent else "repository_example"
    with Run(
        args,
        "gotham_merlin_setup",
        agent_source=source,
        project=args.project,
        control=f"{host(cnc)}:{cnc['environment']['MERLIN_CONTROL_PORT']}",
        agent_url=f"http://{host(cnc)}:{cnc['environment']['MERLIN_HTTP_PORT']}",
        example_agent=example["name"],
    ) as run:
        if args.dry_run:
            return 0
        client, project_id, nodes = live_nodes(args.project)
        require_running(nodes, cnc["name"])
        if args.start_inert_agent:
            if example["name"] not in nodes:
                raise ValueError(f"GNS3 node {example['name']} is missing")
            if nodes[example["name"]].get("status") != "started":
                client.start_node(project_id, nodes[example["name"]]["node_id"])
            run.emit("example_started", node=example["name"])
        else:
            run.emit("external_ready", note="Start your own inert agent on a lab node")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli(main))
