"""Start and stop a built topology in a workable order.

GNS3 starts nodes when asked, in whatever order it is asked. This module adds
the order the lab actually needs and the waiting that makes it useful:

* routers and switches before anything that routes through them;
* publishers before subscribers, so a broker or controller is listening when its
  clients connect;
* polling clients last, so they do not exhaust their retries while their target
  is still booting.

Stopping is the reverse, which keeps consumers alive until producers are gone.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from client import Gns3Client

# Startup tiers. A lower tier starts first. Anything unlisted is a device.
TIER_SWITCH = 0
TIER_ROUTER = 1
TIER_SERVER = 2
TIER_PRODUCER = 3
TIER_CLIENT = 4

INFRASTRUCTURE_TIERS = {"switch": TIER_SWITCH, "router": TIER_ROUTER}

# Roles that something else depends on being up first, keyed by the image the
# node was created from.
SERVER_IMAGES = ("metropolis/controller:dev", "metropolis/mqtt-broker:dev")
PRODUCER_IMAGES = (
    "metropolis/mqtt-sensor:dev",
    "metropolis/hmi:dev",
    "metropolis/dns:dev",
    "metropolis/ntp:dev",
    "metropolis/legacy-gateway:dev",
    "metropolis/engineering:dev",
)

# How long to allow for a node to report started. Docker nodes are quick; a
# VyOS appliance boots an image and is slower.
DEFAULT_READY_TIMEOUT = 120.0
POLL_INTERVAL = 1.0


def tier_for(kind: str, image: str | None) -> int:
    """The startup tier for a node, from its kind and image."""
    if kind in INFRASTRUCTURE_TIERS:
        return INFRASTRUCTURE_TIERS[kind]
    if image in SERVER_IMAGES:
        return TIER_SERVER
    if image in PRODUCER_IMAGES:
        return TIER_PRODUCER
    return TIER_CLIENT


def startup_order(
    nodes: list[dict], kind_by_name: dict[str, str], image_by_name: dict[str, str]
) -> list[str]:
    """Node names ordered by tier, then by name for a stable sequence.

    ``nodes`` is the server's node list, which is the authority on what exists;
    the mappings come from the declared plan.
    """

    def key(node: dict) -> tuple[int, str]:
        name = node["name"]
        return tier_for(kind_by_name.get(name, "device"), image_by_name.get(name)), name

    return [node["name"] for node in sorted(nodes, key=key)]


@dataclass
class LifecycleResult:
    """What a start or stop did."""

    started: list[str] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def wait_until_running(
    client: Gns3Client,
    project_id: str,
    names: list[str],
    *,
    timeout: float = DEFAULT_READY_TIMEOUT,
    poll: float = POLL_INTERVAL,
    sleep=time.sleep,
    clock=time.monotonic,
) -> tuple[list[str], list[str]]:
    """Wait for every named node to report started.

    Returns (running, missing). Waiting is bounded: a node that never comes up
    is reported rather than waited on forever.
    """
    deadline = clock() + timeout
    pending = list(names)
    running: list[str] = []
    while pending and clock() < deadline:
        by_name = {node["name"]: node for node in client.project_nodes(project_id)}
        still: list[str] = []
        for name in pending:
            node = by_name.get(name)
            if node is not None and node.get("status") == "started":
                running.append(name)
            else:
                still.append(name)
        pending = still
        if pending:
            sleep(poll)
    return running, pending


def start_all(
    client: Gns3Client,
    project_id: str,
    kind_by_name: dict[str, str],
    image_by_name: dict[str, str],
    *,
    wait: bool = True,
    timeout: float = DEFAULT_READY_TIMEOUT,
) -> LifecycleResult:
    """Start every node in the project, in dependency order."""
    result = LifecycleResult()
    nodes = client.project_nodes(project_id)
    by_name = {node["name"]: node for node in nodes}

    for name in startup_order(nodes, kind_by_name, image_by_name):
        node = by_name[name]
        if node.get("status") == "started":
            result.started.append(name)
            continue
        try:
            client.start_node(project_id, node["node_id"])
            result.started.append(name)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            result.failed.append(f"{name}: {exc}")

    if wait:
        running, missing = wait_until_running(
            client, project_id, result.started, timeout=timeout
        )
        result.failed.extend(f"{name}: did not report started" for name in missing)
        result.started = running
    return result


def stop_all(
    client: Gns3Client,
    project_id: str,
    kind_by_name: dict[str, str],
    image_by_name: dict[str, str],
) -> LifecycleResult:
    """Stop every node, in the reverse of the startup order."""
    result = LifecycleResult()
    nodes = client.project_nodes(project_id)
    by_name = {node["name"]: node for node in nodes}

    for name in reversed(startup_order(nodes, kind_by_name, image_by_name)):
        node = by_name[name]
        try:
            client.stop_node(project_id, node["node_id"])
            result.stopped.append(name)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            result.failed.append(f"{name}: {exc}")
    return result


def node_status(client: Gns3Client, project_id: str) -> dict[str, str]:
    """Every node's status, by name."""
    return {
        node["name"]: node.get("status", "unknown")
        for node in client.project_nodes(project_id)
    }
