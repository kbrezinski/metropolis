"""Inventory and GNS3 checks shared by external bot/agent setup commands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import DEFAULT_INVENTORY, device_by_name, inventory

GNS3_DIR = Path(__file__).resolve().parents[1] / "gns3"
PROJECT = "metropolis_water_treatment_v1"


def options(description: str, external: str, inert: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--project", default=PROJECT, help="Existing GNS3 project name")
    parser.add_argument("--dry-run", action="store_true", help="Check inventory only")
    parser.add_argument("--output", type=Path, help="Also write UTC JSONL run events")
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument(external, action="store_true", help="Use your own lab node")
    choice.add_argument(
        inert, action="store_true", help="Start the inventoried example node"
    )
    return parser


def device(args, name: str, protocol: str) -> dict:
    return device_by_name(args, name, protocol)


def host(record: dict) -> str:
    return record["address"].split("/", 1)[0]


def live_nodes(project_name: str) -> tuple[object, str, dict[str, dict]]:
    # The GNS3 modules use flat sibling imports because they are script tools.
    if str(GNS3_DIR) not in sys.path:
        sys.path.insert(0, str(GNS3_DIR))
    from client import Gns3Client
    from config import resolve_server

    client = Gns3Client(resolve_server())
    project = client.project_by_name(project_name)
    if project is None:
        raise ValueError(f"GNS3 project {project_name!r} does not exist")
    return (
        client,
        project.project_id,
        {node["name"]: node for node in client.project_nodes(project.project_id)},
    )


def require_running(nodes: dict[str, dict], *names: str) -> None:
    for name in names:
        node = nodes.get(name)
        if node is None:
            raise ValueError(f"GNS3 node {name} is missing")
        if node.get("status") != "started":
            raise ValueError(f"GNS3 node {name} is not started")


def require_inventory_name(args, name: str) -> dict:
    record = next(
        (item for item in inventory(args.inventory) if item["name"] == name), None
    )
    if record is None:
        raise ValueError(f"{name} is not in the inventory")
    return record
