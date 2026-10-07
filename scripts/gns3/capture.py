"""Capture packets on chosen links of a built topology.

GNS3 captures on a link and writes a PCAP next to the project. This module
picks which links to capture, starts and stops them, and reports where the
files landed so a run can be recorded with its dataset.

Capture points are links, which is why they are named by the ``links.yaml`` ids
the validator already checks: a typo in a capture target fails here rather than
producing an empty PCAP.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from client import Gns3Client


@dataclass
class CaptureResult:
    """Per-link capture outcome."""

    started: list[str] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)


def capture_points(links: list[dict]) -> dict[str, dict]:
    """Map a link's declared id to the server's link record.

    The builder names links as it creates them; GNS3 may add its own label, so
    both are considered when matching.
    """
    points: dict[str, dict] = {}
    for link in links:
        for key in (link.get("link_id"), link.get("description")):
            if key:
                points[key] = link
    return points


def _node_names(link: dict) -> list[str]:
    return [
        endpoint.get("label") or endpoint.get("node_id", "?")
        for endpoint in link.get("nodes", [])
    ]


def resolve_links(
    client: Gns3Client, project_id: str, link_ids: list[str]
) -> tuple[dict[str, str], list[str]]:
    """Resolve declared link ids to server link ids.

    Returns (mapping, unknown). An unknown id is reported rather than silently
    skipping, because a capture that quietly covers nothing is worse than one
    that fails.
    """
    points = capture_points(client.project_links(project_id))
    resolved: dict[str, str] = {}
    unknown: list[str] = []
    for link_id in link_ids:
        found = points.get(link_id)
        if found is None:
            unknown.append(link_id)
        else:
            resolved[link_id] = found["link_id"]
    return resolved, unknown


def start_captures(
    client: Gns3Client, project_id: str, resolved: dict[str, str]
) -> CaptureResult:
    """Start a capture on each resolved link."""
    result = CaptureResult()
    for name, link_id in resolved.items():
        try:
            client.start_capture(project_id, link_id)
            result.started.append(name)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            result.failed.append(f"{name}: {exc}")
    return result


def stop_captures(
    client: Gns3Client, project_id: str, resolved: dict[str, str]
) -> CaptureResult:
    """Stop each capture and record the file the server reports.

    GNS3 returns the link record, whose ``capture_file_path`` is where the PCAP
    was written.
    """
    result = CaptureResult()
    for name, link_id in resolved.items():
        try:
            record = client.stop_capture(project_id, link_id)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            result.failed.append(f"{name}: {exc}")
            continue
        result.stopped.append(name)
        if not record:
            record = client.link(project_id, link_id)
        path = (record or {}).get("capture_file_path")
        if path:
            result.files[name] = path
    return result


def capture_for(
    client: Gns3Client,
    project_id: str,
    link_ids: list[str],
    duration: float,
    *,
    sleep=time.sleep,
) -> CaptureResult:
    """Capture every named link for ``duration`` seconds, then stop."""
    resolved, unknown = resolve_links(client, project_id, link_ids)
    result = start_captures(client, project_id, resolved)
    result.failed.extend(
        f"{link_id}: not a link in this project" for link_id in unknown
    )

    if result.started and duration > 0:
        sleep(duration)

    finished = stop_captures(client, project_id, resolved)
    result.stopped = finished.stopped
    result.failed.extend(finished.failed)
    result.files = finished.files
    return result
