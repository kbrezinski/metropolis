#!/usr/bin/env python3
"""Start or stop the built Metropolis lab in a workable order.

Routers and switches come up before the nodes that route through them,
publishers before subscribers, and polling clients last, so nothing exhausts its
retries against a target that is still booting. Stopping is the reverse.

Examples::

    run.py lab_lifecycle --status
    run.py lab_lifecycle --start
    run.py lab_lifecycle --start --no-wait
    run.py lab_lifecycle --stop
"""

from __future__ import annotations

import argparse
import sys

from _cli import (
    add_server_arguments,
    connect,
    image_by_node,
    load_plan,
    type_by_node,
)
from lifecycle import (
    DEFAULT_READY_TIMEOUT,
    LifecycleResult,
    node_status,
    start_all,
    startup_order,
    stop_all,
    tier_for,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    add_server_arguments(parser)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--start", action="store_true", help="Start every node")
    group.add_argument("--stop", action="store_true", help="Stop every node")
    group.add_argument("--status", action="store_true", help="Report node status")
    group.add_argument(
        "--order",
        action="store_true",
        help="Print the startup order without changing anything",
    )
    parser.add_argument(
        "--no-wait", action="store_true", help="Do not wait for nodes to report started"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_READY_TIMEOUT,
        help="Seconds to wait for a node to start",
    )
    parser.add_argument(
        "--dataset",
        default=None,
        help="Dataset directory, when it is not the default one",
    )
    args = parser.parse_args()

    topology, inventory = load_plan()
    kinds = type_by_node(topology)
    images = image_by_node(inventory)

    if args.order:
        names = startup_order(
            [{"name": node.name} for node in topology.nodes], kinds, images
        )
        for name in names:
            print(f"  {tier_for(kinds.get(name, 'device'), images.get(name))}  {name}")
        return 0

    client = connect(args)
    project = client.project_by_name(args.project)
    if project is None:
        print(f"error: no project named {args.project} on this server", file=sys.stderr)
        return 2

    if args.status:
        for name, status in sorted(node_status(client, project.project_id).items()):
            print(f"  {status:<10} {name}")
        return 0

    if args.start:
        result: LifecycleResult = start_all(
            client,
            project.project_id,
            kinds,
            images,
            wait=not args.no_wait,
            timeout=args.timeout,
        )
        print(f"{len(result.started)} started")
        for failure in result.failed:
            print(f"  failed: {failure}", file=sys.stderr)
        return 1 if result.failed else 0

    result = stop_all(client, project.project_id, kinds, images)
    print(f"{len(result.stopped)} stopped")
    for failure in result.failed:
        print(f"  failed: {failure}", file=sys.stderr)
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
