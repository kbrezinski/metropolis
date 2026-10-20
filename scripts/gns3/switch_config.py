"""Apply declared VLAN settings to the Metropolis-owned Docker switches."""

from __future__ import annotations

import json
import subprocess
import time

from topology import Topology

SWITCH_IMAGE = "metropolis/openvswitch:dev"


def switch_payload(topology: Topology, name: str) -> dict:
    """Cabled adapters only; unlisted spare ports never enter the bridge."""
    ports = {
        str(number): setting
        for number, setting in topology.switch_ports.get(name, {}).items()
    }
    if name not in topology.switch_ports:
        uplinks = [
            link
            for link in topology.links
            if name in {link.first.node, link.second.node} and link.kind == "trunk"
        ]
        if len(uplinks) > 1:
            raise ValueError(f"{name} has multiple tagged uplinks")
        access_vlan = uplinks[0].vlans[0] if uplinks else None
        if uplinks and len(uplinks[0].vlans) != 1:
            raise ValueError(f"{name} segment uplink must carry one VLAN")
        for link in topology.links:
            for endpoint in (link.first, link.second):
                if endpoint.node == name:
                    ports[str(endpoint.adapter)] = {
                        "mode": "trunk"
                        if link in uplinks
                        else "access"
                        if access_vlan
                        else "untagged",
                        "vlans": list(link.vlans)
                        if link in uplinks
                        else [access_vlan]
                        if access_vlan
                        else [],
                        "link_id": link.link_id,
                    }
    return {"schema_version": 1, "ports": ports}


def expected_state(payload: dict) -> dict:
    states = {}
    for number, setting in payload["ports"].items():
        mode, vlans = setting["mode"], setting["vlans"]
        states[f"eth{number}"] = (
            {"tag": None, "trunks": sorted(vlans), "vlan_mode": "trunk"}
            if mode == "trunk"
            else {
                "tag": vlans[0] if mode == "access" else 1,
                "trunks": [],
                "vlan_mode": "access",
            }
        )
    return states


def configure_switches(
    client,
    project_id: str,
    topology: Topology,
    docker_hosts: dict[str, str],
    *,
    execute=subprocess.run,
    timeout: float = 120,
) -> dict[str, dict]:
    """Start each switch, apply its plan, and trust only verified image output."""
    results = {}
    for planned in topology.nodes:
        if planned.kind != "switch":
            continue
        name = planned.name
        try:
            record = next(
                (
                    item
                    for item in client.project_nodes(project_id)
                    if item["name"] == name
                ),
                None,
            )
            if record is None:
                raise RuntimeError("node was not created")
            if (
                record.get("node_type") != "docker"
                or record.get("properties", {}).get("image") != SWITCH_IMAGE
            ):
                raise RuntimeError(
                    "node is not the Metropolis Open vSwitch Docker image"
                )
            host = docker_hosts.get(record.get("compute_id", "local"))
            if not host:
                raise RuntimeError(
                    f"no Docker host for compute {record.get('compute_id', 'local')}"
                )
            if record.get("status") != "started":
                client.start_node(project_id, record["node_id"])
            deadline = time.monotonic() + timeout
            while True:
                record = client.node(project_id, record["node_id"])
                if (
                    record
                    and record.get("status") == "started"
                    and (
                        record.get("properties", {}).get("container_id")
                        or record.get("container_id")
                    )
                ):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("switch container did not start")
                time.sleep(0.2)
            container_id = (
                record.get("properties", {}).get("container_id")
                or record["container_id"]
            )
            payload = switch_payload(topology, name)
            completed = execute(
                [
                    "docker",
                    "--host",
                    host,
                    "exec",
                    "-i",
                    str(container_id),
                    "python3",
                    "/opt/metropolis/switch.py",
                    "apply",
                ],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=timeout,
            )
            if completed.returncode != 0:
                try:
                    error = json.loads(completed.stdout).get("error")
                except (ValueError, AttributeError):
                    error = None
                raise RuntimeError(
                    error
                    or completed.stderr.strip()
                    or completed.stdout.strip()
                    or "switch apply failed"
                )
            response = json.loads(completed.stdout)
            if response.get("verified") is not True or response.get(
                "ports"
            ) != expected_state(payload):
                raise RuntimeError("switch did not verify every declared port")
            results[name] = {
                "status": "unchanged" if not response.get("changed") else "configured",
                "ports": response["ports"],
            }
        except Exception as exc:  # noqa: BLE001 - continue with the remaining switches
            results[name] = {"status": "failed", "reason": str(exc)}
    return results
