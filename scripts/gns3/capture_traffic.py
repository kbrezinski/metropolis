#!/usr/bin/env python3
"""Capture built Metropolis links into a versioned dataset with a run manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from _cli import (
    DATASET,
    add_server_arguments,
    connect,
    load_documents,
    template_name_by_image,
)
from capture import capture_for, coverage_report, resolve_links
from topology import build_plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    add_server_arguments(parser)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--links", nargs="+", help="Declared link IDs to capture")
    selection.add_argument(
        "--all-built", action="store_true", help="Capture every built, capturable link"
    )
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--list", action="store_true", help="List declared IDs offline")
    parser.add_argument(
        "--coverage", action="store_true", help="Report physical mapping and coverage"
    )
    args = parser.parse_args()

    dataset = args.dataset.resolve()
    address_plan, inventory, infrastructure, link_plan = load_documents(dataset)
    declared = [link["id"] for link in link_plan["links"]]
    logical = {link["id"] for link in link_plan["links"] if link["kind"] == "overlay"}
    if args.list or not (args.links or args.all_built or args.coverage):
        print(f"{len(declared)} declared links in {dataset.name}:")
        for name in declared:
            suffix = (
                " (logical overlay; not directly capturable)" if name in logical else ""
            )
            print(f"  {name}{suffix}")
        if not (args.links or args.all_built or args.coverage):
            return 0
    unknown = [name for name in args.links or [] if name not in declared]
    if unknown:
        for name in unknown:
            print(f"error: {name} is not declared in links.yaml", file=sys.stderr)
        return 2
    if selected_overlays := sorted(set(args.links or []) & logical):
        print(
            "error: logical overlays are not physical capture links: "
            + ", ".join(selected_overlays),
            file=sys.stderr,
        )
        return 2
    if args.duration <= 0:
        print("error: --duration must be positive", file=sys.stderr)
        return 2

    client = connect(args)
    project = client.project_by_name(args.project)
    if project is None:
        print(f"error: no project named {args.project} on this server", file=sys.stderr)
        return 2
    topology = build_plan(
        inventory, infrastructure, link_plan, template_name_by_image()
    )
    resolution = resolve_links(client, project.project_id, topology, declared)
    chosen = (
        resolution.capturable
        if args.all_built
        else list(dict.fromkeys(args.links or []))
    )
    coverage = coverage_report(resolution, chosen, link_plan, inventory, infrastructure)
    if args.coverage:
        print(
            json.dumps(
                {"link_status": resolution.status, "coverage": coverage}, indent=2
            )
        )
        if not (args.links or args.all_built):
            return 0
    if not chosen:
        print("error: no built, capturable links were selected", file=sys.stderr)
        return 2

    preflight = []
    if args.all_built:
        preflight = [
            f"{name}: {detail['status']}"
            for name, detail in resolution.status.items()
            if detail["status"] not in {"built", "undeclared_built", "logical_overlay"}
        ]
    result = capture_for(
        client,
        project.project_id,
        resolution,
        chosen,
        args.duration,
        dataset / "captures",
        {
            "testbed_id": link_plan["testbed_id"],
            "dataset_version": dataset.name,
            "dataset_path": str(dataset),
            "topology_schema_version": link_plan["schema_version"],
            "address_plan_schema_version": address_plan["schema_version"],
            "topology_document_sha256": {
                str(path.relative_to(dataset)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in (
                    dataset / "topology/address-plan.yaml",
                    dataset / "topology/links.yaml",
                    dataset / "device_instances/initial_devices.yaml",
                    dataset / "device_instances/infrastructure.yaml",
                )
            },
            "link_status": resolution.status,
            "coverage": coverage,
        },
        preflight_failures=preflight,
    )
    print(f"started {len(result.started)}, stopped {len(result.stopped)} capture(s)")
    for name, path in sorted(result.files.items()):
        print(f"  {name}: {path}")
    print(f"manifest: {result.manifest_path}")
    for failure in result.failed:
        print(f"  failed: {failure}", file=sys.stderr)
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
