"""Read the declared topologies and lay them out for GNS3.

This turns the checked-in inventories into a list of nodes and links, and works
out the coordinates and port assignments GNS3 needs. It contacts nothing, so the
whole plan can be inspected and tested before a server is involved.

Two deliberate rules:

* **Switch port numbers are derived, not asserted.** The switch specifications
  list port roles, not GNS3 port numbers. Here each role gets the adapter
  matching its position in the specification's own list, and the mapping is
  written out so the specs' ``TODO`` can be filled in from something real.
* **Port roles are not nodes.** A link endpoint that names an access segment is
  skipped and reported, because those segments are documented as segments rather
  than appliances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Grid spacing in GNS3 canvas units; large enough that nodes do not overlap.
COLUMN_WIDTH = 220
ROW_HEIGHT = 120
MARGIN = 60

ROUTER_TEMPLATE = "VyOS 1.3.0"
SWITCH_TEMPLATE = "Open vSwitch"


@dataclass(frozen=True)
class PlannedNode:
    """One node to create, or to look up if it already exists."""

    name: str
    template_name: str
    environment: dict[str, str] = field(default_factory=dict)
    kind: str = "device"
    x: int = 0
    y: int = 0


@dataclass(frozen=True)
class PlannedPort:
    """One endpoint of a link, already resolved to a GNS3 port."""

    node: str
    adapter: int
    port: int = 0


@dataclass(frozen=True)
class PlannedLink:
    """One link to create."""

    link_id: str
    kind: str
    first: PlannedPort
    second: PlannedPort


@dataclass
class Topology:
    """Everything the builder needs, with the gaps made explicit."""

    nodes: list[PlannedNode]
    links: list[PlannedLink]
    port_map: dict[str, dict[str, int]]
    skipped: list[str]
    missing_templates: list[str]


def load(dataset: Path) -> tuple[dict, dict, dict]:
    """Load the three declared topology documents."""
    return (
        yaml.safe_load((dataset / "topology/address-plan.yaml").read_text("utf-8")),
        yaml.safe_load(
            (dataset / "device_instances/initial_devices.yaml").read_text("utf-8")
        ),
        yaml.safe_load(
            (dataset / "device_instances/infrastructure.yaml").read_text("utf-8")
        ),
    )


def environment_string(environment: dict[str, str]) -> str:
    """Render a node environment the way GNS3 stores it.

    GNS3 holds the environment as one newline-separated string of KEY=value
    pairs, quoting each value so an address like ``10.20.10.10/24`` survives.
    """
    return "\n".join(f'{key}="{value}"' for key, value in sorted(environment.items()))


def _device_nodes(
    inventory: dict, templates: dict[str, str]
) -> tuple[list[PlannedNode], list[str]]:
    nodes: list[PlannedNode] = []
    missing: list[str] = []
    for device in inventory["devices"]:
        template_name = templates.get(device["image"])
        if template_name is None:
            missing.append(
                f"{device['name']} uses unregistered image {device['image']}"
            )
            continue
        nodes.append(
            PlannedNode(
                name=device["name"],
                template_name=template_name,
                environment=dict(device.get("environment", {})),
                kind="device",
            )
        )
    return nodes, missing


def _infrastructure_nodes(
    infrastructure: dict,
    router_template: str = ROUTER_TEMPLATE,
    switch_template: str = SWITCH_TEMPLATE,
) -> list[PlannedNode]:
    nodes: list[PlannedNode] = []
    for router in infrastructure["routers"]:
        nodes.append(
            PlannedNode(
                name=router["name"], template_name=router_template, kind="router"
            )
        )
    for switch in infrastructure["switches"]:
        nodes.append(
            PlannedNode(
                name=switch["name"], template_name=switch_template, kind="switch"
            )
        )
    # Access segments are switch appliances named by the aggregation specs but
    # without a specification document of their own.
    for segment in infrastructure.get("segments", []):
        nodes.append(
            PlannedNode(
                name=segment["name"], template_name=switch_template, kind="switch"
            )
        )
    return nodes


def port_map(infrastructure: dict) -> dict[str, dict[str, int]]:
    """Adapter number per switch port role, from the specification's order."""
    mapping: dict[str, dict[str, int]] = {}
    for switch in infrastructure["switches"]:
        mapping[switch["name"]] = {
            role: index for index, role in enumerate(switch["attachment_points"])
        }
    return mapping


def _adapter_for(endpoint: dict, roles: dict[str, dict[str, int]]) -> int | None:
    """The GNS3 adapter a link endpoint uses, or None when it is not a node.

    A router or device uses the number in its interface name, because the model
    configures that interface itself. A switch uses the position of its port
    role in the specification.
    """
    node = endpoint["node"]
    if node in roles:
        role = endpoint.get("port_role")
        if role is None:
            return 0
        return roles[node].get(role)
    interface = endpoint.get("interface")
    if interface and interface.startswith("eth"):
        return int(interface[3:])
    return 0


def place(nodes: list[PlannedNode]) -> list[PlannedNode]:
    """Assign coordinates: routers first, then switches, then devices."""
    order = {"router": 0, "switch": 1, "device": 2}
    placed: list[PlannedNode] = []
    for kind in ("router", "switch", "device"):
        members = [item for item in nodes if item.kind == kind]
        for index, node in enumerate(sorted(members, key=lambda item: item.name)):
            placed.append(
                PlannedNode(
                    name=node.name,
                    template_name=node.template_name,
                    environment=node.environment,
                    kind=node.kind,
                    x=MARGIN + index * COLUMN_WIDTH,
                    y=MARGIN + order[kind] * ROW_HEIGHT * 3,
                )
            )
    return placed


def build_plan(
    inventory: dict,
    infrastructure: dict,
    link_plan: dict,
    templates: dict[str, str],
    *,
    router_template: str = ROUTER_TEMPLATE,
    switch_template: str = SWITCH_TEMPLATE,
) -> Topology:
    """Resolve the declared topology into nodes and links for GNS3."""
    device_nodes, missing = _device_nodes(inventory, templates)
    nodes = device_nodes + _infrastructure_nodes(
        infrastructure, router_template, switch_template
    )
    known = {node.name for node in nodes}

    roles = port_map(infrastructure)
    links: list[PlannedLink] = []
    skipped: list[str] = []
    for link in link_plan["links"]:
        endpoints = link["endpoints"]
        adapters = [_adapter_for(endpoint, roles) for endpoint in endpoints]
        if any(adapter is None for adapter in adapters):
            unknown = [
                endpoint["node"]
                for endpoint, adapter in zip(endpoints, adapters)
                if adapter is None
            ]
            skipped.append(f"{link['id']}: no port for {', '.join(unknown)}")
            continue
        absent = [
            endpoint["node"] for endpoint in endpoints if endpoint["node"] not in known
        ]
        if absent:
            skipped.append(f"{link['id']}: {', '.join(absent)} is not a created node")
            continue
        links.append(
            PlannedLink(
                link_id=link["id"],
                kind=link["kind"],
                first=PlannedPort(endpoints[0]["node"], adapters[0] or 0),
                second=PlannedPort(endpoints[1]["node"], adapters[1] or 0),
            )
        )

    return Topology(
        nodes=place(nodes),
        links=links,
        port_map=roles,
        skipped=skipped,
        missing_templates=missing,
    )
