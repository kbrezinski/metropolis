#!/usr/bin/env python3
"""Create the Metropolis project, nodes, and links in GNS3 from the declared topology.

Reads the address plan, device inventory, infrastructure inventory, and link
plan, and makes the server match them. Safe to rerun: existing nodes and links
are reused, so a build that stopped partway resumes.

Examples::

    run.py build_topology --plan                # show what would be created
    run.py build_topology --plan --write-ports  # also print the derived switch ports
    run.py build_topology                       # create it
    run.py build_topology --prune-missing       # also delete nodes not in the plan
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _cli import (
    add_appliance_arguments,
    add_server_arguments,
    connect,
    load_plan,
    report_skipped,
)
from builder import build, port_map_yaml
from client import Gns3Client
from templates import TEMPLATES


def template_ids(client: Gns3Client) -> dict[str, str]:
    """Template name to id, for every template the plan needs."""
    return {item["name"]: item["template_id"] for item in client.templates()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    add_server_arguments(parser)
    add_appliance_arguments(parser)
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Print the plan and exit without contacting GNS3",
    )
    parser.add_argument(
        "--prune-missing",
        action="store_true",
        help="Delete project nodes the plan does not contain",
    )
    parser.add_argument(
        "--write-ports",
        type=Path,
        help="Write the derived switch port map to this path",
    )
    args = parser.parse_args()

    topology, inventory = load_plan(
        router_template=args.router_template, switch_template=args.switch_template
    )

    if args.plan:
        print(f"{len(topology.nodes)} nodes, {len(topology.links)} links")
        for kind in ("router", "switch", "device"):
            names = [node.name for node in topology.nodes if node.kind == kind]
            print(f"  {kind}: {len(names)}")
        by_template: dict[str, int] = {}
        for node in topology.nodes:
            by_template[node.template_name] = by_template.get(node.template_name, 0) + 1
        for name, count in sorted(by_template.items()):
            print(f"    {count:>3}  {name}")
        report_skipped(topology.skipped)
        if args.write_ports:
            args.write_ports.write_text(port_map_yaml(topology, args.project), "utf-8")
            print(f"port map written to {args.write_ports}")
        return 0

    client = connect(args)

    missing = [
        node.name
        for node in topology.nodes
        if node.template_name not in {item.name for item in TEMPLATES}
        and node.kind == "device"
    ]
    if missing:
        for name in missing:
            print(f"error: {name} has no registered template", file=sys.stderr)
        return 2

    result = build(
        client,
        args.project,
        topology,
        template_ids(client),
        prune_missing=args.prune_missing,
        compute_id=args.compute_id,
    )
    print(f"project {args.project}")
    print(
        f"  {len(result.created_nodes)} nodes created, {len(result.reused_nodes)} reused"
    )
    print(
        f"  {len(result.created_links)} links created, {len(result.reused_links)} reused"
    )
    report_skipped(result.skipped)

    if args.write_ports:
        args.write_ports.write_text(port_map_yaml(topology, args.project), "utf-8")
        print(f"port map written to {args.write_ports}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
