#!/usr/bin/env python3
"""Plan, check, or explicitly apply a dataset's container resource baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _cli import DATASET, add_server_arguments, connect, load_documents
from baseline_config import BASELINE_FILE, load_baseline
from collect_device_logs import docker_host_mapping
from resource_baseline import configure_baseline


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_server_arguments(parser)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--plan", action="store_true", help="Validate and list targets offline"
    )
    modes.add_argument(
        "--check", action="store_true", help="Read-only drift check (default)"
    )
    modes.add_argument(
        "--apply",
        action="store_true",
        help="Persist the selected limits on existing containers",
    )
    parser.add_argument(
        "--docker-host", action="append", default=[], metavar="COMPUTE_ID=URI"
    )
    args = parser.parse_args()
    try:
        hosts = docker_host_mapping(args.docker_host)
        _, inventory, infrastructure, _ = load_documents(args.dataset)
        path = args.dataset / BASELINE_FILE
        baseline = load_baseline(path, inventory, infrastructure)
    except (ValueError, OSError) as exc:
        parser.error(f"baseline: {exc}")
    if args.plan:
        print(json.dumps({"mode": "plan", **baseline}, indent=2))
        return 0
    client = connect(args)
    project = client.project_by_name(args.project)
    if project is None:
        print(f"error: no GNS3 project named {args.project}", file=sys.stderr)
        return 2
    try:
        report = configure_baseline(
            client, project.project_id, baseline, hosts, apply=args.apply
        )
    except KeyboardInterrupt:
        print(
            "Baseline setup interrupted; run --check to inspect current settings.",
            file=sys.stderr,
        )
        return 130
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "matched" else 1


if __name__ == "__main__":
    raise SystemExit(main())
