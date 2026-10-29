"""Resolve declared links to GNS3 ports and preserve verified PCAP captures."""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from client import Gns3Client
from link_identity import planned_key, related_links, server_key
from topology import Topology

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LinkResolution:
    """One physical link match, or an explicit reason it cannot be captured."""

    resolved: dict[str, str] = field(default_factory=dict)
    status: dict[str, dict] = field(default_factory=dict)
    declared_ids: set[str] = field(default_factory=set)

    @property
    def capturable(self) -> list[str]:
        return sorted(self.resolved)


@dataclass
class CaptureResult:
    started: list[str] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    manifest_path: Path | None = None
    interrupted: bool = False


def resolve_links(
    client: Gns3Client,
    project_id: str,
    topology: Topology,
    declared_ids: list[str],
) -> LinkResolution:
    """Match by node ID, adapter number, and port number at both ends."""
    nodes = client.project_nodes(project_id)
    server_links = client.project_links(project_id)
    node_ids: dict[str, str] = {}
    duplicate_names: set[str] = set()
    for node in nodes:
        name = node["name"]
        if name in node_ids:
            duplicate_names.add(name)
        else:
            node_ids[name] = node["node_id"]
    planned = {link.link_id: link for link in topology.links}
    valid_keys = {
        planned_key(link, node_ids)
        for link in topology.links
        if link.first.node in node_ids and link.second.node in node_ids
    }
    result = LinkResolution()
    result.declared_ids = set(declared_ids)
    for declared_id in declared_ids:
        link = planned.get(declared_id)
        if link is None:
            result.status[declared_id] = {
                "status": "unplanned",
                "reason": "declaration could not be converted to a GNS3 link",
            }
            continue
        ends = {link.first.node, link.second.node}
        if ends & duplicate_names:
            result.status[declared_id] = {
                "status": "ambiguous",
                "reason": "duplicate GNS3 node name",
            }
            continue
        if not ends <= node_ids.keys():
            result.status[declared_id] = {
                "status": "missing",
                "reason": "endpoint node is absent from GNS3",
            }
            continue
        identity = planned_key(link, node_ids)
        matches = [item for item in server_links if server_key(item) == identity]
        if len(matches) > 1:
            result.status[declared_id] = {
                "status": "ambiguous",
                "gns3_link_ids": [item["link_id"] for item in matches],
                "reason": "multiple physical links use the declared ports",
            }
        elif matches:
            found = matches[0]
            if found.get("suspend") or found.get("capturing"):
                result.status[declared_id] = {
                    "status": "not_capturable",
                    "gns3_link_ids": [found["link_id"]],
                    "reason": "link is suspended or already capturing",
                }
            else:
                result.resolved[declared_id] = found["link_id"]
                result.status[declared_id] = {
                    "status": "built",
                    "gns3_link_ids": [found["link_id"]],
                }
        else:
            conflicts = [
                item
                for item in related_links(identity, server_links)
                if server_key(item) not in valid_keys
            ]
            result.status[declared_id] = (
                {
                    "status": "miswired",
                    "gns3_link_ids": [item["link_id"] for item in conflicts],
                    "reason": "endpoint port or peer differs from the declaration",
                }
                if conflicts
                else {"status": "missing", "reason": "no physical GNS3 link"}
            )
    matched_ids = set(result.resolved.values())
    disputed_ids = {
        item_id
        for details in result.status.values()
        if details["status"] in {"ambiguous", "miswired"}
        for item_id in details.get("gns3_link_ids", [])
    }
    for item in server_links:
        link_id = item["link_id"]
        if link_id in matched_ids:
            continue
        synthetic_id = f"gns3_{link_id}"
        if item.get("suspend") or item.get("capturing"):
            result.status[synthetic_id] = {
                "status": "not_capturable",
                "gns3_link_ids": [link_id],
                "reason": "physical link is suspended or already capturing",
            }
        else:
            result.resolved[synthetic_id] = link_id
            result.status[synthetic_id] = {
                "status": "undeclared_built",
                "gns3_link_ids": [link_id],
                "reason": (
                    "physical link has no unique declared mapping"
                    if link_id in disputed_ids
                    else "physical GNS3 link is absent from links.yaml"
                ),
            }
    return result


def coverage_report(
    resolution: LinkResolution,
    selected: list[str],
    link_plan: dict,
    inventory: dict,
    infrastructure: dict,
) -> dict:
    """Report whether selected points cover modeled local and routed paths."""
    device_names = {item["name"] for item in inventory["devices"]}
    router_names = {item["name"] for item in infrastructure["routers"]}
    physical = set(resolution.resolved)
    chosen = set(selected) & physical
    local: set[str] = set()
    routed: set[str] = set()
    linked_nodes: set[str] = set()
    for link in link_plan["links"]:
        link_id = link["id"]
        ends = {endpoint["node"] for endpoint in link["endpoints"]}
        if link_id in physical:
            linked_nodes.update(ends)
        if ends & device_names:
            local.add(link_id)
        if ends & router_names or link["kind"] in {"trunk", "transit", "underlay"}:
            routed.add(link_id)
    unresolved = {
        name: detail
        for name, detail in resolution.status.items()
        if detail["status"] != "built"
    }
    uncabled_devices = sorted(device_names - linked_nodes)
    uncabled_segments = sorted(
        {item["name"] for item in infrastructure.get("segments", [])} - linked_nodes
    )
    return {
        "declared_links": len(resolution.declared_ids),
        "built_capturable_links": len(physical),
        "selected_links": sorted(chosen),
        "undeclared_physical_links": sorted(
            name for name in resolution.status if name not in resolution.declared_ids
        ),
        "local_attachment_points": sorted(local),
        "routed_path_points": sorted(routed),
        "missing_local_points": sorted(local - chosen),
        "missing_routed_points": sorted(routed - chosen),
        "uncabled_devices": uncabled_devices,
        "uncabled_segments": uncabled_segments,
        "unresolved_links": unresolved,
        "all_built_points_selected": chosen == physical,
        "modeled_local_paths_selected": local <= chosen,
        "modeled_routed_paths_selected": routed <= chosen,
        "all_network_traffic_verified": False,
        "claim_limit": (
            "Selected links show potential capture coverage. Confirm local and "
            "routed flows in PCAPs; uncabled or undeclared paths cannot be covered."
        ),
    }


def inspect_pcap(path: Path) -> tuple[int, str]:
    """Count complete classic-PCAP records and hash the downloaded file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        header = source.read(24)
        endian = {
            b"\xd4\xc3\xb2\xa1": "<",
            b"\xa1\xb2\xc3\xd4": ">",
            b"\x4d\x3c\xb2\xa1": "<",
            b"\xa1\xb2\x3c\x4d": ">",
        }.get(header[:4])
        if len(header) != 24 or endian is None:
            raise ValueError("file is not a complete classic PCAP")
        digest.update(header)
        packets = 0
        while record := source.read(16):
            if len(record) != 16:
                raise ValueError("truncated PCAP packet header")
            digest.update(record)
            _, _, included, original = struct.unpack(f"{endian}IIII", record)
            if not 0 < included <= original <= 16 << 20:
                raise ValueError("invalid PCAP packet length")
            remaining = included
            while remaining:
                block = source.read(min(remaining, 1 << 20))
                if not block:
                    raise ValueError("truncated PCAP packet")
                digest.update(block)
                remaining -= len(block)
            packets += 1
    if packets == 0:
        raise ValueError("PCAP contains no packets")
    return packets, digest.hexdigest()


def capture_for(
    client: Gns3Client,
    project_id: str,
    resolution: LinkResolution,
    selected: list[str],
    duration: float,
    output_dir: Path,
    metadata: dict,
    *,
    run_id: str | None = None,
    preflight_failures: list[str] | None = None,
    sleep=time.sleep,
    on_started=None,
) -> CaptureResult:
    """Stop every acknowledged start, retrieve files, and write a manifest."""
    if duration <= 0:
        raise ValueError("capture duration must be positive")
    run_id = run_id or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    if not SAFE_ID.fullmatch(run_id):
        raise ValueError("invalid capture run ID")
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    result = CaptureResult(failed=list(preflight_failures or []))
    entries: dict[str, dict] = {}
    started: dict[str, str] = {}
    run_started = utc_now()
    for name in dict.fromkeys(selected):
        if not SAFE_ID.fullmatch(name):
            result.failed.append(f"{name}: invalid declared link id")
            continue
        details = resolution.status.get(name, {"status": "unknown"})
        entries[name] = {
            "declared_link_id": name if name in resolution.declared_ids else None,
            "gns3_link_id": resolution.resolved.get(name),
            "resolution": details,
        }
        if name not in resolution.resolved:
            result.failed.append(f"{name}: {details['status']}")
    eligible = [name for name in entries if name in resolution.resolved]
    try:
        for name in eligible:
            link_id = resolution.resolved[name]
            try:
                record = client.start_capture(project_id, link_id) or {}
            except BaseException as exc:  # A lost reply or Ctrl-C may follow a start.
                result.interrupted = isinstance(exc, KeyboardInterrupt)
                result.failed.append(
                    f"{name}: capture start failed: {type(exc).__name__}: {exc}"
                )
                try:
                    current = client.link(project_id, link_id) or {}
                    if current.get("capturing"):
                        started[name] = link_id
                        result.started.append(name)
                        entries[name]["started_at_utc"] = utc_now()
                        entries[name]["server_capture_file_path"] = current.get(
                            "capture_file_path"
                        )
                except Exception as check_exc:  # noqa: BLE001
                    result.failed.append(
                        f"{name}: could not check capture state: {check_exc}"
                    )
                break
            started[name] = link_id
            result.started.append(name)
            entries[name]["started_at_utc"] = utc_now()
            entries[name]["server_capture_file_path"] = record.get("capture_file_path")
        if len(started) == len(eligible):
            if started:
                if on_started is not None:
                    on_started(result)
                sleep(duration)
    except (
        BaseException
    ) as exc:  # Ctrl-C or timer failure; always stop acknowledged starts
        result.interrupted = isinstance(exc, KeyboardInterrupt)
        result.failed.append(f"capture interrupted: {type(exc).__name__}: {exc}")
    finally:
        for name, link_id in started.items():
            try:
                record = client.stop_capture(project_id, link_id) or {}
                result.stopped.append(name)
                entries[name]["stopped_at_utc"] = utc_now()
                path = record.get("capture_file_path") or entries[name].get(
                    "server_capture_file_path"
                )
                if not path:
                    fallback = client.link(project_id, link_id) or {}
                    path = fallback.get("capture_file_path")
                if not path:
                    raise ValueError("GNS3 returned no capture_file_path")
                entries[name]["server_capture_file_path"] = path
            except Exception as exc:  # noqa: BLE001 - attempt every remaining stop
                result.failed.append(f"{name}: capture stop failed: {exc}")
        for name in result.stopped:
            if not entries[name].get("server_capture_file_path"):
                continue
            link_id = started[name]
            destination = run_dir / f"{name}.pcap"
            partial = run_dir / f"{name}.pcap.part"
            try:
                client.download_pcap(project_id, link_id, partial)
                if not partial.is_file():
                    raise ValueError("download did not create a PCAP file")
                packets, digest = inspect_pcap(partial)
                os.replace(partial, destination)
                entries[name].update(
                    {
                        "local_file": str(destination),
                        "sha256": digest,
                        "packet_count": packets,
                        "bytes": destination.stat().st_size,
                    }
                )
                result.files[name] = str(destination)
            except Exception as exc:  # noqa: BLE001 - record and preserve other files
                result.failed.append(f"{name}: PCAP retrieval failed: {exc}")
                partial.unlink(missing_ok=True)
        manifest = {
            **metadata,
            "run_id": run_id,
            "project_id": project_id,
            "run_started_at_utc": run_started,
            "run_stopped_at_utc": utc_now(),
            "duration_requested_seconds": duration,
            "captures": entries,
            "failures": result.failed,
        }
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        result.manifest_path = manifest_path
    return result
