#!/usr/bin/env python3
"""Capture packets on chosen links of the built Metropolis lab.

Capture points are links, named by the ids in ``links.yaml``, so a typo fails
here rather than producing an empty capture. GNS3 writes each capture next to
the project and this reports where the files landed, for recording alongside the
dataset.

Examples::

    run.py capture --list
    run.py capture --links link-core-plant attach-intake-plc --duration 60
"""

from __future__ import annotations

import argparse
import sys

from _cli import DATASET, add_server_arguments, connect, load_documents
from capture import capture_for, capture_points


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    add_server_arguments(parser)
    parser.add_argument(
        "--links", nargs="+", default=[], help="Link ids to capture, from links.yaml"
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=60.0,
        help="Seconds to capture before stopping",
    )
    parser.add_argument(
        "--list", action="store_true", help="List the declared capture points and exit"
    )
    args = parser.parse_args()

    _, _, _, link_plan = load_documents()
    declared = [link["id"] for link in link_plan["links"]]

    if args.list or not args.links:
        print(f"{len(declared)} capture points in {DATASET.name}:")
        for name in declared:
            print(f"  {name}")
        return 0

    unknown = [name for name in args.links if name not in declared]
    if unknown:
        for name in unknown:
            print(f"error: {name} is not a link in links.yaml", file=sys.stderr)
        return 2
    if args.duration <= 0:
        print("error: --duration must be positive", file=sys.stderr)
        return 2

    client = connect(args)
    project = client.project_by_name(args.project)
    if project is None:
        print(f"error: no project named {args.project} on this server", file=sys.stderr)
        return 2

    available = capture_points(client.project_links(project.project_id))
    if not available:
        print(
            "error: the project has no links; run build_topology first",
            file=sys.stderr,
        )
        return 2

    result = capture_for(client, project.project_id, args.links, args.duration)
    print(f"captured {len(result.started)} link(s) for {args.duration:g}s")
    for name, path in sorted(result.files.items()):
        print(f"  {name}: {path}")
    for failure in result.failed:
        print(f"  failed: {failure}", file=sys.stderr)
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
