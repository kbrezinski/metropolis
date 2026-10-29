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
import shlex
import subprocess

import yaml

from client import Gns3Client, Node
from link_identity import planned_key, related_links, server_key
from switch_config import configure_switches
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
    switch_configuration: dict[str, dict] = field(default_factory=dict)


def existing_link_keys(links: list[dict]) -> set[tuple]:
    return {identity for link in links if (identity := server_key(link)) is not None}


def protocol_environment_drift(planned, existing: dict) -> bool:
    """Detect protocol settings that an existing GNS3 node would keep stale."""
    expected = {
        key: str(value)
        for key, value in planned.environment.items()
        if key.startswith(("MQTT_", "DNP3_", "COAP_"))
    }
    if not expected:
        return False
    raw = existing.get("properties", {}).get("environment", "")
    actual = {}
    try:
        for line in raw.splitlines():
            parsed = shlex.split(line)
            if len(parsed) != 1 or "=" not in parsed[0]:
                return True
            key, value = parsed[0].split("=", 1)
            actual[key] = value
    except ValueError:
        return True
    return any(actual.get(key) != value for key, value in expected.items())


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
            if protocol_environment_drift(planned, found):
                result.skipped.append(
                    f"{planned.name}: stale MQTT/DNP3/CoAP environment; refresh or recreate the GNS3 node"
                )
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
    present = client.project_links(project_id)
    node_ids = {name: node.node_id for name, node in nodes.items()}
    planned_keys = {
        planned_key(link, node_ids)
        for link in topology.links
        if link.first.node in node_ids and link.second.node in node_ids
    }

    for planned in topology.links:
        first = nodes.get(planned.first.node)
        second = nodes.get(planned.second.node)
        if first is None or second is None:
            result.skipped.append(f"{planned.link_id}: an endpoint node is absent")
            continue
        identity = planned_key(planned, node_ids)
        exact = [link for link in present if server_key(link) == identity]
        if len(exact) > 1:
            result.skipped.append(f"{planned.link_id}: ambiguous duplicate GNS3 links")
            continue
        if exact:
            result.reused_links.append(planned.link_id)
            continue
        conflicts = [
            link
            for link in related_links(identity, present)
            if server_key(link) not in planned_keys
        ]
        if conflicts:
            result.skipped.append(
                f"{planned.link_id}: a GNS3 link uses the wrong ports or peer"
            )
            continue
        created = client.create_link(
            project_id,
            (first.node_id, planned.first.adapter, planned.first.port),
            (second.node_id, planned.second.adapter, planned.second.port),
        )
        present.append(
            created
            if server_key(created) is not None
            else {
                "nodes": [
                    {
                        "node_id": first.node_id,
                        "adapter_number": planned.first.adapter,
                        "port_number": planned.first.port,
                    },
                    {
                        "node_id": second.node_id,
                        "adapter_number": planned.second.adapter,
                        "port_number": planned.second.port,
                    },
                ]
            }
        )
        result.created_links.append(planned.link_id)


def build(
    client: Gns3Client,
    project_name: str,
    topology: Topology,
    templates: dict[str, str],
    *,
    prune_missing: bool = False,
    compute_id: str = "local",
    docker_hosts: dict[str, str] | None = None,
    switch_execute=subprocess.run,
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
    result.switch_configuration = configure_switches(
        client, project.project_id, topology, docker_hosts or {}, execute=switch_execute
    )
    unbuilt = {
        link.link_id
        for link in topology.links
        if any(message.startswith(f"{link.link_id}:") for message in result.skipped)
    }
    for link in topology.links:
        if link.link_id not in unbuilt:
            continue
        for endpoint in (link.first, link.second):
            if endpoint.node in result.switch_configuration:
                result.switch_configuration[endpoint.node] = {
                    "status": "failed",
                    "reason": f"declared link {link.link_id} was not built",
                }

    if prune_missing:
        planned = {node.name for node in topology.nodes}
        for existing in client.project_nodes(project.project_id):
            if existing["name"] not in planned:
                client.delete_node(project.project_id, existing["node_id"])
                result.skipped.append(f"deleted {existing['name']} (not in the plan)")
    return result


def port_map_yaml(topology: Topology, project_name: str) -> str:
    """Render the derived switch port numbers as YAML."""
    return yaml.safe_dump(
        {
            "project": project_name,
            "note": (
                "Derived by scripts/gns3/build_topology.py from the port role order "
                "in each switch's attachment_points. "
                "testbeds/metropolis/switch/README.md shows the same mapping."
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
