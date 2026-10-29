"""Run one RDP session per declared client on its GNS3 compute."""

from __future__ import annotations

import copy
import json
import math
import threading
import time
from collections import deque
from ipaddress import ip_interface

from device_logs import container_id, utc_now
from resource_metrics import RemoteDockerClient, _direct_docker_client

CLIENT_HELPER = "/opt/metropolis/rdp_client.py"
UDP_HELPER = "/opt/metropolis/rdp_udp.py"
CLIENT_FAILURE_REASONS = {
    "credential_configuration",
    "invalid_duration",
    "rdp_disconnected",
    "interrupted",
    "authentication_not_observed",
    "udp_unanswered",
    "OSError",
    "BrokenPipeError",
    "FileNotFoundError",
}


def _shortest_path(graph, start, targets, kinds, blocked):
    """Find a unique shortest declared path without crossing another device."""
    pending = deque([start])
    depth = {start: 0}
    paths = {start: []}
    counts = {start: 1}
    while pending:
        node = pending.popleft()
        for neighbor, link_id, kind in graph.get(node, []):
            if kind not in kinds or neighbor in blocked:
                continue
            next_depth = depth[node] + 1
            if neighbor not in depth:
                depth[neighbor] = next_depth
                paths[neighbor] = [*paths[node], link_id]
                counts[neighbor] = counts[node]
                pending.append(neighbor)
            elif depth[neighbor] == next_depth:
                counts[neighbor] = min(2, counts[neighbor] + counts[node])
    found = [name for name in targets if name in depth]
    if not found:
        raise ValueError("no declared RDP path")
    closest = min(depth[name] for name in found)
    found = [name for name in found if depth[name] == closest]
    if sum(counts[name] for name in found) != 1:
        raise ValueError("ambiguous declared RDP path")
    return found[0], paths[found[0]]


def declared_rdp(inventory: dict, amplified: list[str]) -> list[dict]:
    """Resolve clients and requested modes without a fixed dataset or node name."""
    devices = inventory.get("devices", [])
    names = [item["name"] for item in devices]
    if len(names) != len(set(names)):
        raise ValueError("RDP inventory contains duplicate node names")
    servers = {
        item["name"]: item
        for item in devices
        if "rdp-udp-synthetic" in item.get("protocols", [])
        and "rdp" in item.get("protocols", [])
    }
    if len(amplified) != len(set(amplified)):
        raise ValueError("an RDP UDP amplifier may be selected only once")
    for name in amplified:
        if name not in servers:
            raise ValueError(f"{name}: not a declared RDP UDP server")
    sessions = []
    for item in devices:
        target_name = item.get("environment", {}).get("RDP_TARGET_NODE")
        if not target_name:
            continue
        target = servers.get(target_name)
        if target is None:
            raise ValueError(f"{item['name']}: unknown RDP server {target_name}")
        sessions.append(
            {
                "source": item["name"],
                "source_address": str(ip_interface(item["address"]).ip),
                "destination": target_name,
                "destination_address": str(ip_interface(target["address"]).ip),
            }
        )
    return sessions


def _exec(engine, cid: str, request: dict) -> dict:
    inspected = engine.inspect_container(cid)
    if (
        inspected.get("Id") != cid
        or inspected.get("Config", {}).get("Image") != request["image"]
    ):
        raise ValueError("RDP node uses a stale or unexpected Docker image")
    command = request["command"]
    if command[:2] not in (
        ["python3", UDP_HELPER],
        ["python3", CLIENT_HELPER],
    ):
        raise ValueError("unsupported RDP helper command")
    process = engine.exec_create(cid, cmd=command, stdout=True, stderr=True)
    output = engine.exec_start(process["Id"])
    if not isinstance(output, bytes) or len(output) > 64 * 1024:
        raise ValueError("invalid RDP helper output")
    exit_code = engine.exec_inspect(process["Id"]).get("ExitCode")
    try:
        result = json.loads(output.decode("utf-8").splitlines()[-1])
    except (ValueError, UnicodeDecodeError, IndexError) as exc:
        raise RuntimeError(
            "RDP helper returned no valid result; rebuild the image"
        ) from exc
    if not isinstance(result, dict):
        raise ValueError("invalid RDP helper result")
    if command[1] == UDP_HELPER and command[2] == "ready":
        return {
            "tcp_ready": exit_code == 0 and result.get("tcp_ready") is True,
            "udp_ready": exit_code == 0 and result.get("udp_ready") is True,
        }
    if command[1] == UDP_HELPER:
        mode = result.get("mode")
        return {
            "mode": mode
            if isinstance(mode, str) and mode in {"benign", "amplify"}
            else None,
            "verified": exit_code == 0 and result.get("verified") is True,
        }
    reason = result.get("reason")
    if not isinstance(reason, str) or reason not in CLIENT_FAILURE_REASONS:
        reason = (
            "helper_exit_nonzero" if exit_code != 0 else "session_or_udp_incomplete"
        )
    return {
        "status": result.get("status") if exit_code == 0 else "failed",
        "source": result.get("source"),
        "target_host": result.get("target_host"),
        "tcp_success": result.get("tcp_success") is True,
        "udp_sent": result.get("udp_sent"),
        "udp_received": result.get("udp_received"),
        "udp_response_bytes": result.get("udp_response_bytes"),
        "reason": reason,
    }


def _worker(connection, base_url: str):
    engine = None
    try:
        engine = _direct_docker_client(base_url)
        engine.timeout = 45
        while True:
            operation, cid = connection.recv()
            if operation == "close":
                connection.send((True, None))
                return
            try:
                request = json.loads(operation)
                connection.send((True, _exec(engine, cid, request)))
            except Exception as exc:  # noqa: BLE001 - omit Docker output and environment
                safe = (
                    str(exc)
                    if isinstance(exc, (ValueError, RuntimeError))
                    else type(exc).__name__
                )
                connection.send((False, safe))
    except (EOFError, BrokenPipeError):
        pass
    finally:
        if engine is not None:
            engine.close()
        connection.close()


def execute(
    node: dict, hosts: dict[str, str], command: list[str], image: str, timeout: float
) -> dict:
    compute = node.get("compute_id", "local")
    if compute not in hosts:
        raise ValueError(f"no Docker host mapping for compute {compute}")
    cid = container_id(node)
    if node.get("node_type") != "docker" or node.get("status") != "started" or not cid:
        raise ValueError("RDP node is not a started Docker container")
    if node.get("properties", {}).get("image") != image:
        raise ValueError("GNS3 RDP node image differs from the selected dataset")
    engine = RemoteDockerClient(hosts[compute], timeout=timeout, worker_target=_worker)
    try:
        return engine._request(
            json.dumps({"command": command, "image": image}), cid, timeout=timeout
        )
    finally:
        engine.close()


class RdpRun:
    """Capture-scoped RDP clients and reversible synthetic UDP mode."""

    def __init__(
        self,
        client,
        project_id: str,
        inventory: dict,
        hosts: dict[str, str],
        duration: float,
        amplified: list[str],
        *,
        routers: list[str] | None = None,
        readiness_timeout: float = 20,
        executor=execute,
    ):
        self.client, self.project_id, self.inventory = client, project_id, inventory
        self.hosts, self.duration, self.amplified = hosts, duration, list(amplified)
        self.routers = set(routers or [])
        self.readiness_timeout = readiness_timeout
        self.executor = executor
        self.sessions = declared_rdp(inventory, amplified)
        self.devices = {item["name"]: item for item in inventory.get("devices", [])}
        self.nodes: dict[str, dict] = {}
        self.threads: list[threading.Thread] = []
        self.attempted_modes: list[str] = []
        self.unavailable_servers: set[str] = set()
        self.lock = threading.Lock()
        used_servers = {item["destination"] for item in self.sessions}
        self.result = {
            "status": "planned" if self.enabled else "not_configured",
            "sessions": [{**item, "status": "planned"} for item in self.sessions],
            "udp_modes": {
                name: {
                    "status": "planned",
                    "requested_mode": "amplify" if name in amplified else "benign",
                }
                for name in used_servers | set(amplified)
            },
            "readiness": {},
            "failures": [],
        }

    @property
    def enabled(self) -> bool:
        return bool(self.sessions or self.amplified)

    def snapshot(self) -> dict:
        with self.lock:
            return copy.deepcopy(self.result)

    def _failure(self, source: str, reason: str):
        with self.lock:
            self.result["failures"].append({"source": source, "reason": reason})
            self.result["status"] = "failed"

    def prepare(self):
        if not self.enabled:
            return
        records: dict[str, list[dict]] = {}
        for node in self.client.project_nodes(self.project_id):
            records.setdefault(node["name"], []).append(node)
        for name in {
            *(item["source"] for item in self.sessions),
            *(item["destination"] for item in self.sessions),
            *self.amplified,
        }:
            matches = records.get(name, [])
            if len(matches) != 1:
                self._failure(name, "expected one built GNS3 node")
                continue
            node = matches[0]
            image = self.devices[name]["image"]
            if (
                node.get("node_type") != "docker"
                or node.get("status") != "started"
                or not container_id(node)
            ):
                self._failure(name, "Docker node is not started")
            elif node.get("properties", {}).get("image") != image:
                self._failure(
                    name, "built node image differs from inventory; rebuild it"
                )
            elif node.get("compute_id", "local") not in self.hosts:
                self._failure(
                    name,
                    f"no Docker host mapping for compute {node.get('compute_id', 'local')}",
                )
            else:
                self.nodes[name] = node
        for name in {
            *(
                item["destination"]
                for item in self.sessions
                if item["source"] in self.nodes
            ),
            *self.amplified,
        }:
            if name in self.nodes:
                self._wait_ready(name)

    def _wait_ready(self, name: str):
        deadline = time.monotonic() + self.readiness_timeout
        last_error = "service unavailable"
        while True:
            try:
                state = self.executor(
                    self.nodes[name],
                    self.hosts,
                    ["python3", UDP_HELPER, "ready"],
                    self.devices[name]["image"],
                    5,
                )
                ready = (
                    state.get("tcp_ready") is True and state.get("udp_ready") is True
                )
                self.result["readiness"][name] = {
                    "tcp_ready": state.get("tcp_ready") is True,
                    "udp_ready": state.get("udp_ready") is True,
                    "checked_at_utc": utc_now(),
                }
                if ready:
                    return
            except Exception as exc:  # noqa: BLE001 - retry bounded startup
                last_error = type(exc).__name__
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.nodes.pop(name, None)
                self._failure(name, f"RDP service readiness failed: {last_error}")
                return
            time.sleep(min(0.5, remaining))

    def set_capture_points(
        self, link_plan: dict, selected: list[str], resolved: dict[str, str]
    ):
        """Resolve declared endpoint and routed links for each RDP path."""
        physical = set(selected) & set(resolved)
        graph: dict[str, list[tuple[str, str, str]]] = {}
        for link in link_plan.get("links", []):
            endpoints = link.get("endpoints", [])
            if len(endpoints) != 2:
                continue
            first, second = (endpoint["node"] for endpoint in endpoints)
            graph.setdefault(first, []).append((second, link["id"], link["kind"]))
            graph.setdefault(second, []).append((first, link["id"], link["kind"]))
        intermediate_devices = set(self.devices)
        for entry in self.result["sessions"]:
            source = entry["source"]
            destination = entry["destination"]
            try:
                if (
                    ip_interface(self.devices[source]["address"]).network
                    == ip_interface(self.devices[destination]["address"]).network
                ):
                    _, source_links = _shortest_path(
                        graph,
                        source,
                        {destination},
                        {"access", "trunk"},
                        intermediate_devices - {source, destination},
                    )
                    route_links, destination_links = [], []
                else:
                    if not self.routers:
                        raise ValueError("no declared RDP router path")
                    source_router, source_links = _shortest_path(
                        graph,
                        source,
                        self.routers,
                        {"access", "trunk"},
                        intermediate_devices - {source},
                    )
                    destination_router, destination_links = _shortest_path(
                        graph,
                        destination,
                        self.routers,
                        {"access", "trunk"},
                        intermediate_devices - {destination},
                    )
                    if source_router == destination_router:
                        route_links = []
                    else:
                        _, route_links = _shortest_path(
                            graph,
                            source_router,
                            {destination_router},
                            {"transit", "underlay"},
                            intermediate_devices,
                        )
                entry["capture_path_status"] = "resolved"
            except ValueError as exc:
                source_links, route_links, destination_links = [], [], []
                entry["capture_path_status"] = (
                    "ambiguous" if "ambiguous" in str(exc) else "missing"
                )
                self._failure(source, str(exc))
            entry["capture_links"] = {
                "source": source_links,
                "routed": route_links,
                "destination": destination_links,
            }
            required = set(source_links + route_links + destination_links)
            entry["capture_selected"] = bool(required and required <= physical)
            entry["capture_started"] = False
            entry["capture_saved"] = False
            if (
                entry["capture_path_status"] == "resolved"
                and not entry["capture_selected"]
            ):
                self._failure(
                    source,
                    "RDP declared path is not selected for capture",
                )

    def mark_capture_started(self, started: list[str]):
        started_ids = set(started)
        for entry in self.result["sessions"]:
            links = entry.get("capture_links", {})
            required = set(
                links.get("source", [])
                + links.get("routed", [])
                + links.get("destination", [])
            )
            entry["capture_started"] = bool(required and required <= started_ids)
            if entry["capture_selected"] and not entry["capture_started"]:
                self._failure(entry["source"], "RDP path capture did not start")

    def mark_capture_saved(self, saved: dict[str, str]):
        saved_ids = set(saved)
        for entry in self.result["sessions"]:
            links = entry.get("capture_links", {})
            required = set(
                links.get("source", [])
                + links.get("routed", [])
                + links.get("destination", [])
            )
            entry["capture_saved"] = bool(required and required <= saved_ids)
            if entry.get("capture_started") and not entry["capture_saved"]:
                self._failure(entry["source"], "RDP path PCAP was not saved")

    def _session(self, entry: dict):
        source = entry["source"]
        command = [
            "python3",
            CLIENT_HELPER,
            "--target-host",
            entry["destination_address"],
            "--duration",
            str(min(30.0, self.duration)),
        ]
        with self.lock:
            entry["started_at_utc"] = utc_now()
            entry["status"] = "running"
        try:
            result = self.executor(
                self.nodes[source],
                self.hosts,
                command,
                self.devices[source]["image"],
                45,
            )
            good = (
                result.get("status") == "complete"
                and result.get("source") == source
                and result.get("target_host") == entry["destination_address"]
                and result.get("tcp_success") is True
                and type(result.get("udp_sent")) is int
                and result["udp_sent"] > 0
                and type(result.get("udp_received")) is int
                and result["udp_received"] > 0
            )
            with self.lock:
                entry.update(
                    {
                        "status": "complete" if good else "failed",
                        "tcp_success": result.get("tcp_success") is True,
                        "udp_sent": result.get("udp_sent")
                        if type(result.get("udp_sent")) is int
                        else 0,
                        "udp_received": result.get("udp_received")
                        if type(result.get("udp_received")) is int
                        else 0,
                        "udp_response_bytes": result.get("udp_response_bytes")
                        if type(result.get("udp_response_bytes")) is int
                        else 0,
                    }
                )
            if not good:
                reason = result.get("reason")
                if not isinstance(
                    reason, str
                ) or reason not in CLIENT_FAILURE_REASONS | {
                    "helper_exit_nonzero",
                    "session_or_udp_incomplete",
                }:
                    reason = "session_or_udp_incomplete"
                with self.lock:
                    entry["reason"] = reason
                self._failure(source, f"RDP session or UDP probe failed: {reason}")
        except Exception as exc:  # noqa: BLE001 - keep other sessions and captures running
            with self.lock:
                entry["status"] = "failed"
            self._failure(source, f"RDP session failed: {type(exc).__name__}")
        finally:
            with self.lock:
                entry["stopped_at_utc"] = utc_now()

    def start(self):
        if not self.enabled:
            return
        with self.lock:
            self.result["started_at_utc"] = utc_now()
            if self.result["status"] == "planned":
                self.result["status"] = "running"
        lease = math.ceil(self.duration + 60)
        baseline_targets = {
            entry["destination"]
            for entry in self.result["sessions"]
            if entry["source"] in self.nodes and entry["destination"] in self.nodes
        } - set(self.amplified)
        for name in sorted(baseline_targets):
            self.attempted_modes.append(name)
            try:
                value = self.executor(
                    self.nodes[name],
                    self.hosts,
                    ["python3", UDP_HELPER, "set-mode", "--mode", "benign"],
                    self.devices[name]["image"],
                    15,
                )
                if value.get("verified") is not True or value.get("mode") != "benign":
                    raise RuntimeError("benign mode was not verified")
                self.result["udp_modes"][name].update(
                    {"status": "benign_verified", "started_at_utc": utc_now()}
                )
                self.attempted_modes.remove(name)
            except Exception as exc:  # noqa: BLE001 - do not run against uncertain mode
                self.result["udp_modes"][name]["status"] = "failed"
                self.unavailable_servers.add(name)
                self._failure(
                    name, f"RDP UDP benign reset failed: {type(exc).__name__}"
                )
        for name in self.amplified:
            if name not in self.nodes:
                self.result["udp_modes"][name]["status"] = "failed"
                continue
            self.attempted_modes.append(name)
            try:
                value = self.executor(
                    self.nodes[name],
                    self.hosts,
                    [
                        "python3",
                        UDP_HELPER,
                        "set-mode",
                        "--mode",
                        "amplify",
                        "--lease-seconds",
                        str(lease),
                    ],
                    self.devices[name]["image"],
                    15,
                )
                if value.get("verified") is not True or value.get("mode") != "amplify":
                    raise RuntimeError("amplifying mode was not verified")
                self.result["udp_modes"][name].update(
                    {
                        "status": "applied",
                        "started_at_utc": utc_now(),
                        "lease_seconds": lease,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - restoration still required after uncertain write
                self.result["udp_modes"][name]["status"] = "failed"
                self.unavailable_servers.add(name)
                self._failure(
                    name, f"RDP UDP mode activation failed: {type(exc).__name__}"
                )
        for entry in self.result["sessions"]:
            if (
                entry["source"] not in self.nodes
                or entry["destination"] not in self.nodes
                or entry["destination"] in self.unavailable_servers
            ):
                entry["status"] = "failed"
                entry["reason"] = "server_or_udp_mode_unavailable"
                continue
            worker = threading.Thread(
                target=self._session,
                args=(entry,),
                name=f"rdp-{entry['source']}",
                daemon=True,
            )
            self.threads.append(worker)
            worker.start()

    def finish(self):
        if not self.enabled:
            return
        if self.result["status"] == "planned":
            self.result["status"] = "skipped"
        for entry in self.result["sessions"]:
            if entry["status"] == "planned":
                entry["status"] = "skipped"
        for mode in self.result["udp_modes"].values():
            if mode["status"] == "planned":
                mode["status"] = "skipped"
        for worker in self.threads:
            try:
                worker.join(timeout=45)
            except KeyboardInterrupt:
                self._failure(worker.name, "RDP session cleanup interrupted")
            if worker.is_alive():
                self._failure(
                    worker.name, "RDP session did not finish before cleanup deadline"
                )
        for name in self.attempted_modes:
            try:
                value = self.executor(
                    self.nodes[name],
                    self.hosts,
                    ["python3", UDP_HELPER, "set-mode", "--mode", "benign"],
                    self.devices[name]["image"],
                    15,
                )
                if value.get("verified") is not True or value.get("mode") != "benign":
                    raise RuntimeError("benign mode was not verified")
                self.result["udp_modes"][name].update(
                    {"status": "restored", "stopped_at_utc": utc_now()}
                )
            except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - restore other nodes
                self.result["udp_modes"][name]["status"] = "failed"
                self._failure(
                    name,
                    f"RDP UDP mode restoration failed: {type(exc).__name__}",
                )
        with self.lock:
            self.result["stopped_at_utc"] = utc_now()
            if self.result["status"] == "running":
                self.result["status"] = "complete"
