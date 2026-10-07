#!/usr/bin/env python3
"""Register a GNS3 Docker template for every Metropolis device image.

Run this once against a GNS3 server after building the images, so nodes can be
created from them. It is safe to rerun: a template that already matches is left
alone, and one that has drifted is updated.

Examples::

    run.py register_templates --check          # compare the registry to the inventory
    run.py register_templates --dry-run        # show what would change
    run.py register_templates                  # apply

The server is taken from GNS3_SERVER_HOST, GNS3_SERVER_PORT, GNS3_SERVER_USERNAME
and GNS3_SERVER_PASSWORD when set, otherwise from the GNS3 client's own config.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

from client import Gns3Client
from config import ENV_HOST, ENV_PASSWORD, ENV_PORT, ENV_USER, resolve_server
from templates import APPLIANCES, TEMPLATES, register_templates

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY = (
    ROOT
    / "testbeds/metropolis/datasets/water_treatment_v1"
    / "device_instances/initial_devices.yaml"
)


def inventoried_images(inventory: Path) -> set[str]:
    """Every Docker image the device inventory refers to."""
    data = yaml.safe_load(inventory.read_text(encoding="utf-8"))
    return {device["image"] for device in data["devices"]}


def registry_problems(inventory: Path) -> list[str]:
    """Images the inventory needs but the registry does not describe."""
    registered = {item.image for item in TEMPLATES}
    missing = inventoried_images(inventory) - registered
    return [f"{image} is inventoried but has no template" for image in sorted(missing)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare the registry to the inventory and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without contacting GNS3",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Delete Metropolis templates the registry no longer lists",
    )
    parser.add_argument("--host", help=f"Override {ENV_HOST}")
    parser.add_argument("--port", type=int, help=f"Override {ENV_PORT}")
    parser.add_argument("--username", help=f"Override {ENV_USER}")
    parser.add_argument("--password", help=f"Override {ENV_PASSWORD}")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    problems = registry_problems(args.inventory)
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 2
    if args.check:
        print(
            f"{len(TEMPLATES)} templates cover every inventoried image "
            f"({len(inventoried_images(args.inventory))} images)"
        )
        print(f"appliances registered manually: {', '.join(APPLIANCES.values())}")
        return 0

    if args.dry_run:
        existing = {item.name for item in TEMPLATES}
        print(f"would ensure {len(existing)} templates:")
        for template in TEMPLATES:
            print(f"  {template.name}  <-  {template.image}")
        return 0

    overrides = {
        key: value
        for key, value in (
            (ENV_HOST, args.host),
            (ENV_PORT, str(args.port) if args.port is not None else None),
            (ENV_USER, args.username),
            (ENV_PASSWORD, args.password),
        )
        if value is not None
    }
    try:
        server = resolve_server(env={**os.environ, **overrides})
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    client = Gns3Client(server)
    if not client.reachable():
        print(f"error: no GNS3 server answered at {server.base_url}", file=sys.stderr)
        return 2

    print(f"GNS3 {client.version()} at {server.base_url}")
    result = register_templates(client, prune=args.prune)
    for label, names in (
        ("created", result.created),
        ("updated", result.updated),
        ("pruned", result.pruned),
    ):
        for name in names:
            print(f"  {label}: {name}")
    print(
        f"{len(result.created)} created, {len(result.updated)} updated, "
        f"{len(result.unchanged)} unchanged, {len(result.pruned)} pruned"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
