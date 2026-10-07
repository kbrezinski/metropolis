"""Create the Metropolis project, its nodes, and its links in GNS3.

Reads the declared topology (see ``topology.py``) and makes the server match it.
Idempotent: a node or link that already exists is reused, so a rerun after a
partial failure resumes rather than duplicating.

Links are what make switch ports concrete. The switch specifications list port
roles and leave the GNS3 port numbers as a ``TODO``; this build derives them and
writes the mapping out, so the specs can be filled in from something that ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from client import Gns3Client, Node
from topology import Topology, environment_string


@dataclass
class BuildResult:
    """What a build created versus reused."""

    project: str
    created_nodes: list[str] = field(default_factory=list)
    reused_nodes: list[str] = field(default_factory=list)
    created_links: list[str] = field(default_factory=list)
    reused_links: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _link_key(first: dict, second: dict) -> frozenset:
    """Identify a link by the two node ids it joins.

    The graph is a tree of point-to-point links, so a pair of nodes is enough;
    this is stable regardless of which end GNS3 reports first.
    """
    return frozenset((first["node_id"], second["node_id"]))


def existing_link_keys(links: list[dict]) -> set[frozenset]:
    keys = set()
    for link in links:
        nodes = link.get("nodes", [])
        if len(nodes) == 2:
            keys.add(_link_key(nodes[0], nodes[1]))
    return keys


def ensure_nodes(
    client: Gns3Client,
    project_id: str,
    topology: Topology,
    template_ids: dict[str, str],
    compute_id: str = "local",
) -> tuple[dict[str, Node], BuildResult]:
    """Create the nodes that are missing and return them by name."""
    result = BuildResult(project=project_id)
    existing = {node["name"]: node for node in client.project_nodes(project_id)}
    nodes: dict[str, Node] = {}

    for planned in topology.nodes:
        found = existing.get(planned.name)
        if found is not None:
            nodes[planned.name] = Node.from_api(found)
            result.reused_nodes.append(planned.name)
            continue
        template_id = template_ids.get(planned.template_name)
        if template_id is None:
            result.skipped.append(
                f"{planned.name}: no template named {planned.template_name!r}"
            )
            continue
        created = client.create_node(
            project_id,
            template_id,
            name=planned.name,
            x=planned.x,
            y=planned.y,
            compute_id=compute_id,
        )
        nodes[planned.name] = created
        result.created_nodes.append(planned.name)
        if planned.environment:
            client.set_node_environment(
                project_id, created.node_id, environment_string(planned.environment)
            )
    return nodes, result


def ensure_links(
    client: Gns3Client,
    project_id: str,
    topology: Topology,
    nodes: dict[str, Node],
    result: BuildResult,
) -> None:
    """Create the links that are missing."""
    present = existing_link_keys(client.project_links(project_id))

    for planned in topology.links:
        first = nodes.get(planned.first.node)
        second = nodes.get(planned.second.node)
        if first is None or second is None:
            result.skipped.append(f"{planned.link_id}: an endpoint node is absent")
            continue
        key = frozenset((first.node_id, second.node_id))
        if key in present:
            result.reused_links.append(planned.link_id)
            continue
        client.create_link(
            project_id,
            (first.node_id, planned.first.adapter, planned.first.port),
            (second.node_id, planned.second.adapter, planned.second.port),
        )
        present.add(key)
        result.created_links.append(planned.link_id)


def build(
    client: Gns3Client,
    project_name: str,
    topology: Topology,
    templates: dict[str, str],
    *,
    prune_missing: bool = False,
    compute_id: str = "local",
) -> BuildResult:
    """Make the server match the declared topology.

    ``templates`` maps a template name to its id; it comes from the same
    registration the template CLI performs.
    """
    project, _ = client.ensure_project(project_name)
    nodes, result = ensure_nodes(
        client, project.project_id, topology, templates, compute_id
    )
    result.skipped.extend(topology.skipped)
    ensure_links(client, project.project_id, topology, nodes, result)

    if prune_missing:
        planned = {node.name for node in topology.nodes}
        for existing in client.project_nodes(project.project_id):
            if existing["name"] not in planned:
                client.delete_node(project.project_id, existing["node_id"])
                result.skipped.append(f"deleted {existing['name']} (not in the plan)")
    return result


def port_map_yaml(topology: Topology, project_name: str) -> str:
    """Render the derived switch port numbers for the switch specifications."""
    return yaml.safe_dump(
        {
            "project": project_name,
            "note": (
                "Derived by scripts/gns3/build_topology.py from the port role order "
                "in each switch specification. Record these in the specs."
            ),
            "switches": {
                switch: {
                    role: adapter
                    for role, adapter in sorted(roles.items(), key=lambda item: item[1])
                }
                for switch, roles in sorted(topology.port_map.items())
            },
        },
        sort_keys=True,
    )
