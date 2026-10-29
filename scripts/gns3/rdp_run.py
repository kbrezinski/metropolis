"""Run one RDP session per declared client on its GNS3 compute."""

from __future__ import annotations

import copy
import json
import math
import threading
from ipaddress import ip_interface

from device_logs import container_id, utc_now
from resource_metrics import RemoteDockerClient, _direct_docker_client

SERVER_IMAGE = "metropolis/rdp-server:dev"
CLIENT_HELPER = "/opt/metropolis/rdp_client.py"
UDP_HELPER = "/opt/metropolis/rdp_udp.py"


def declared_rdp(inventory: dict, amplified: list[str]) -> list[dict]:
    """Resolve clients and requested modes without a fixed dataset or node name."""
    devices = inventory.get("devices", [])
    names = [item["name"] for item in devices]
    if len(names) != len(set(names)):
        raise ValueError("RDP inventory contains duplicate node names")
    by_name = {item["name"]: item for item in devices}
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
                "destination": target_name,
                "destination_address": str(ip_interface(target["address"]).ip),
            }
        )
    return sessions


def _exec(engine, cid: str, request: dict) -> dict:
    inspected = engine.inspect_container(cid)
    if inspected.get("Id") != cid or inspected.get("Config", {}).get("Image") != request["image"]:
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
    if engine.exec_inspect(process["Id"]).get("ExitCode") != 0:
        raise RuntimeError("RDP helper exited unsuccessfully")
    try:
        result = json.loads(output.decode("utf-8").splitlines()[-1])
    except (ValueError, UnicodeDecodeError, IndexError) as exc:
        raise RuntimeError("RDP helper returned no valid result; rebuild the image") from exc
    if not isinstance(result, dict):
        raise ValueError("invalid RDP helper result")
    return result


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
                safe = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
                connection.send((False, safe))
    except (EOFError, BrokenPipeError):
        pass
    finally:
        if engine is not None:
            engine.close()
        connection.close()


def execute(node: dict, hosts: dict[str, str], command: list[str], image: str, timeout: float) -> dict:
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
        return engine._request(json.dumps({"command": command, "image": image}), cid, timeout=timeout)
    finally:
        engine.close()


class RdpRun:
    """Capture-scoped RDP clients and reversible synthetic UDP mode."""

    def __init__(self, client, project_id: str, inventory: dict, hosts: dict[str, str], duration: float, amplified: list[str], *, executor=execute):
        self.client, self.project_id, self.inventory = client, project_id, inventory
        self.hosts, self.duration, self.amplified = hosts, duration, list(amplified)
        self.executor = executor
        self.sessions = declared_rdp(inventory, amplified)
        self.devices = {item["name"]: item for item in inventory.get("devices", [])}
        self.nodes: dict[str, dict] = {}
        self.threads: list[threading.Thread] = []
        self.attempted_modes: list[str] = []
        self.lock = threading.Lock()
        self.result = {
            "status": "planned" if self.enabled else "not_configured",
            "sessions": [{**item, "status": "planned"} for item in self.sessions],
            "udp_modes": {name: {"status": "planned"} for name in amplified},
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
        for name in {*(item["source"] for item in self.sessions), *(item["destination"] for item in self.sessions), *self.amplified}:
            matches = records.get(name, [])
            if len(matches) != 1:
                self._failure(name, "expected one built GNS3 node")
                continue
            node = matches[0]
            image = self.devices[name]["image"]
            if node.get("node_type") != "docker" or node.get("status") != "started" or not container_id(node):
                self._failure(name, "Docker node is not started")
            elif node.get("properties", {}).get("image") != image:
                self._failure(name, "built node image differs from inventory; rebuild it")
            elif node.get("compute_id", "local") not in self.hosts:
                self._failure(name, f"no Docker host mapping for compute {node.get('compute_id', 'local')}")
            else:
                self.nodes[name] = node

    def _session(self, entry: dict):
        source = entry["source"]
        command = ["python3", CLIENT_HELPER, "--target-host", entry["destination_address"], "--duration", str(min(30.0, self.duration))]
        with self.lock:
            entry["started_at_utc"] = utc_now()
            entry["status"] = "running"
        try:
            result = self.executor(self.nodes[source], self.hosts, command, self.devices[source]["image"], max(45.0, self.duration + 15))
            good = (result.get("status") == "complete" and result.get("tcp_success") is True and type(result.get("udp_sent")) is int and result["udp_sent"] > 0 and type(result.get("udp_received")) is int and result["udp_received"] > 0)
            with self.lock:
                entry.update({"status": "complete" if good else "failed", "tcp_success": result.get("tcp_success") is True, "udp_sent": result.get("udp_sent") if type(result.get("udp_sent")) is int else 0, "udp_received": result.get("udp_received") if type(result.get("udp_received")) is int else 0})
            if not good:
                self._failure(source, "RDP session or UDP probe did not complete")
        except Exception as exc:  # noqa: BLE001 - keep other sessions and captures running
            with self.lock:
                entry["status"] = "failed"
            self._failure(source, f"RDP session failed: {type(exc).__name__}: {exc}")
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
        for name in self.amplified:
            if name not in self.nodes:
                self.result["udp_modes"][name]["status"] = "failed"
                continue
            self.attempted_modes.append(name)
            try:
                value = self.executor(self.nodes[name], self.hosts, ["python3", UDP_HELPER, "set-mode", "--mode", "amplify", "--lease-seconds", str(lease)], SERVER_IMAGE, 15)
                if value.get("verified") is not True or value.get("mode") != "amplify":
                    raise RuntimeError("amplifying mode was not verified")
                self.result["udp_modes"][name].update({"status": "applied", "started_at_utc": utc_now(), "lease_seconds": lease})
            except Exception as exc:  # noqa: BLE001 - restoration still required after uncertain write
                self.result["udp_modes"][name]["status"] = "failed"
                self._failure(name, f"RDP UDP mode activation failed: {type(exc).__name__}: {exc}")
        for entry in self.result["sessions"]:
            if entry["source"] not in self.nodes or entry["destination"] not in self.nodes:
                entry["status"] = "failed"
                continue
            worker = threading.Thread(target=self._session, args=(entry,), name=f"rdp-{entry['source']}", daemon=True)
            self.threads.append(worker)
            worker.start()

    def finish(self):
        if not self.enabled:
            return
        for name in self.attempted_modes:
            try:
                value = self.executor(self.nodes[name], self.hosts, ["python3", UDP_HELPER, "set-mode", "--mode", "benign"], SERVER_IMAGE, 15)
                if value.get("verified") is not True or value.get("mode") != "benign":
                    raise RuntimeError("benign mode was not verified")
                self.result["udp_modes"][name].update({"status": "restored", "stopped_at_utc": utc_now()})
            except Exception as exc:  # noqa: BLE001 - continue restoring remaining nodes
                self.result["udp_modes"][name]["status"] = "failed"
                self._failure(name, f"RDP UDP mode restoration failed: {type(exc).__name__}: {exc}")
        for worker in self.threads:
            worker.join(timeout=45)
            if worker.is_alive():
                self._failure(worker.name, "RDP session did not finish before cleanup deadline")
        with self.lock:
            self.result["stopped_at_utc"] = utc_now()
            if self.result["status"] == "running":
                self.result["status"] = "complete"
