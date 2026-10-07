"""Shared command-line plumbing for the GNS3 builders.

Loading the topology, resolving the server, and reporting failures the same way
in every command lives here, so each command's own script only describes what it
does.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

from client import Gns3Client
from config import ENV_HOST, ENV_PASSWORD, ENV_PORT, ENV_USER, resolve_server
from templates import APPLIANCES, TEMPLATES
from topology import Topology, build_plan, load

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "testbeds/metropolis/datasets/water_treatment_v1"
DEFAULT_PROJECT = "metropolis_water_treatment_v1"


def load_documents(dataset: Path = DATASET) -> tuple[dict, dict, dict, dict]:
    """The four declared topology documents."""
    address_plan, inventory, infrastructure = load(dataset)
    link_plan = yaml.safe_load((dataset / "topology/links.yaml").read_text("utf-8"))
    return address_plan, inventory, infrastructure, link_plan


def template_name_by_image() -> dict[str, str]:
    """Map each Docker image to the GNS3 template name registered for it."""
    return {item.image: item.name for item in TEMPLATES}


def image_by_node(inventory: dict) -> dict[str, str]:
    """Map each inventoried device name to the image it is built from."""
    return {device["name"]: device["image"] for device in inventory["devices"]}


def type_by_node(topology: Topology) -> dict[str, str]:
    """Map each planned node name to its kind."""
    return {node.name: node.kind for node in topology.nodes}


def add_server_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="GNS3 project name")
    parser.add_argument("--host", help=f"Override {ENV_HOST}")
    parser.add_argument("--port", type=int, help=f"Override {ENV_PORT}")
    parser.add_argument("--username", help=f"Override {ENV_USER}")
    parser.add_argument("--password", help=f"Override {ENV_PASSWORD}")


def add_appliance_arguments(parser: argparse.ArgumentParser) -> None:
    """Let the appliance template names match whatever the server has.

    The VyOS and Ethernet switch template names depend on what has been imported
    into a given GNS3 install, so they are overridable rather than assumed.
    """
    parser.add_argument(
        "--router-template",
        default=APPLIANCES["router"],
        help="Name of the VyOS template on the server",
    )
    parser.add_argument(
        "--switch-template",
        default=APPLIANCES["switch"],
        help="Name of the Ethernet switch template on the server",
    )
    parser.add_argument(
        "--compute-id",
        default="local",
        help="Compute that hosts the nodes and captures",
    )


def connect(args) -> Gns3Client:
    """Build a client from the arguments and the environment, or exit."""
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
        raise SystemExit(f"error: {exc}") from None
    client = Gns3Client(server)
    if not client.reachable():
        raise SystemExit(f"error: no GNS3 server answered at {server.base_url}")
    return client


def load_plan(
    dataset: Path = DATASET,
    *,
    router_template: str | None = None,
    switch_template: str | None = None,
) -> tuple[Topology, dict]:
    """Resolve the declared topology into nodes and links.

    The appliance template names are only used to look templates up on the
    server, so they can be overridden for a GNS3 install that names them
    differently.
    """
    _, inventory, infrastructure, link_plan = load_documents(dataset)
    overrides = {}
    if router_template:
        overrides["router_template"] = router_template
    if switch_template:
        overrides["switch_template"] = switch_template
    topology = build_plan(
        inventory, infrastructure, link_plan, template_name_by_image(), **overrides
    )
    return topology, inventory


def report_skipped(skipped: list[str]) -> None:
    for item in skipped:
        print(f"  skipped: {item}", file=sys.stderr)
