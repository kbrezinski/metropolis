"""Port-aware identity for declared and GNS3 links."""

from __future__ import annotations

from typing import Iterable

from topology import PlannedLink

PortKey = tuple[str, int, int]
LinkKey = tuple[PortKey, PortKey]


def key(first: PortKey, second: PortKey) -> LinkKey:
    """A link key independent of endpoint order, including both port numbers."""
    return tuple(sorted((first, second)))  # type: ignore[return-value]


def server_key(link: dict) -> LinkKey | None:
    endpoints = link.get("nodes") or []
    if len(endpoints) != 2:
        return None
    try:
        ports = [
            (
                str(endpoint["node_id"]),
                int(endpoint["adapter_number"]),
                int(endpoint["port_number"]),
            )
            for endpoint in endpoints
        ]
    except (KeyError, TypeError, ValueError):
        return None
    return key(ports[0], ports[1])


def planned_key(link: PlannedLink, node_ids: dict[str, str]) -> LinkKey:
    return key(
        (node_ids[link.first.node], link.first.adapter, link.first.port),
        (node_ids[link.second.node], link.second.adapter, link.second.port),
    )


def related_links(expected: LinkKey, links: Iterable[dict]) -> list[dict]:
    """Links using either expected port or the same two nodes."""
    nodes = {expected[0][0], expected[1][0]}
    related = []
    for link in links:
        actual = server_key(link)
        if actual is None:
            continue
        if set(actual) & set(expected) or {actual[0][0], actual[1][0]} == nodes:
            related.append(link)
    return related
