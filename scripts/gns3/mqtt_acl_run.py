"""Capture-scoped, HMI-origin MQTT topic-permission check."""

from __future__ import annotations

import copy
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from ipaddress import ip_interface

from device_logs import container_id, utc_now
from resource_metrics import RemoteDockerClient, _direct_docker_client

HELPER = "/opt/metropolis/mqtt_acl_probe.py"


def _is_utc(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.utcoffset() == timezone.utc.utcoffset(None)


def declared_probe(inventory: dict, source: str, target: str) -> dict:
    """Select two configured HMI roles and the broker without fixed node names."""
    devices = inventory.get("devices", [])
    by_name = {device["name"]: device for device in devices}
    if len(by_name) != len(devices):
        raise ValueError("MQTT inventory has duplicate node names")
    if source == target:
        raise ValueError("source and target HMIs must differ")
    for name in (source, target):
        if (
            name not in by_name
            or by_name[name].get("environment", {}).get("MQTT_ACCESS_ROLE") != "hmi"
        ):
            raise ValueError(f"{name}: not an inventoried MQTT HMI")
    brokers = [
        device
        for device in devices
        if "MQTT_OPERATOR_USERNAME" in device.get("environment", {})
    ]
    if len(brokers) != 1:
        raise ValueError("expected one inventoried MQTT broker")
    broker = brokers[0]
    broker_address = str(ip_interface(broker["address"]).ip)
    source_env = by_name[source]["environment"]
    target_env = by_name[target]["environment"]
    for name, env in ((source, source_env), (target, target_env)):
        if env.get("MQTT_HOST") != broker_address:
            raise ValueError(f"{name}: MQTT broker address differs from inventory")
        if not env.get("MQTT_USERNAME") or not env.get("MQTT_PASSWORD"):
            raise ValueError(f"{name}: MQTT account is missing")
    if source_env["MQTT_USERNAME"] == target_env["MQTT_USERNAME"]:
        raise ValueError("HMI accounts must be distinct")
    own_topic, cross_topic = source_env.get("MQTT_TOPIC"), target_env.get("MQTT_TOPIC")
    if (
        not own_topic
        or not cross_topic
        or own_topic == cross_topic
        or any(char in own_topic + cross_topic for char in ("+", "#", "\x00"))
    ):
        raise ValueError("HMI command topics must be distinct and concrete")
    source_cell = source_env.get("HMI_CELL_ID")
    target_cell = target_env.get("HMI_CELL_ID")
    if (
        not isinstance(source_cell, str)
        or not source_cell
        or not isinstance(target_cell, str)
        or not target_cell
        or source_cell == target_cell
    ):
        raise ValueError("source and target must identify distinct HMI cells")
    cells = {source_cell, target_cell}
    scada = []
    for device in devices:
        env = device.get("environment", {})
        if env.get("MQTT_ACCESS_ROLE") != "scada":
            continue
        try:
            targets = json.loads(env.get("SCADA_TARGETS", "[]"))
        except ValueError:
            continue
        subscribed_cells = {
            item.get("id")
            for item in targets
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        if cells <= subscribed_cells:
            scada.append(device["name"])
    if len(scada) != 1:
        raise ValueError("expected one SCADA subscriber for both HMI cells")
    return {
        "source": source,
        "target": target,
        "broker": broker["name"],
        "scada": scada[0],
        "source_address": str(ip_interface(by_name[source]["address"]).ip),
        "broker_address": broker_address,
        "own_topic": own_topic,
        "cross_topic": cross_topic,
    }


def _broker_evidence(logs: bytes | str, client_id: str, own: str, cross: str) -> dict:
    """Return only authorization observations, never raw broker log lines."""
    if isinstance(logs, bytes):
        logs = logs.decode("utf-8", errors="replace")
    evidence = {
        "own_received": False,
        "own_denied": False,
        "cross_received": False,
        "cross_denied": False,
    }
    marker = f"from {client_id} "
    for line in logs.splitlines():
        if marker not in line:
            continue
        if f"'{own}'" in line:
            kind = "own"
        elif f"'{cross}'" in line:
            kind = "cross"
        else:
            continue
        if "Denied PUBLISH" in line:
            evidence[f"{kind}_denied"] = True
        elif "Received PUBLISH" in line:
            evidence[f"{kind}_received"] = True
    return evidence


def _scada_evidence(
    logs: bytes | str, run_id: str, client_id: str, own: str, cross: str
) -> dict:
    """Find the inert marker that the SCADA command bridge logged on delivery."""
    if isinstance(logs, bytes):
        logs = logs.decode("utf-8", errors="replace")
    evidence = {"own_received": False, "cross_received": False}
    for line in logs.splitlines():
        marker = "MQTT_ACL_PROBE "
        if marker not in line:
            continue
        try:
            event = json.loads(line.split(marker, 1)[1])
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or (
            event.get("run_id") != run_id or event.get("client_id") != client_id
        ):
            continue
        if event.get("kind") == "own" and event.get("topic") == own:
            evidence["own_received"] = True
        elif event.get("kind") == "cross" and event.get("topic") == cross:
            evidence["cross_received"] = True
    return evidence


def _remote_operation(engine, cid: str, request: dict) -> dict:
    inspected = engine.inspect_container(cid)
    if (
        inspected.get("Id") != cid
        or inspected.get("Config", {}).get("Image") != request["image"]
    ):
        raise ValueError("MQTT node uses a stale or unexpected Docker image")
    if request["kind"] == "probe":
        args = request["args"]
        if not all(
            isinstance(args.get(key), str) and args[key]
            for key in ("own_topic", "cross_topic", "client_id", "run_id")
        ):
            raise ValueError("invalid MQTT probe arguments")
        command = [
            "python3",
            HELPER,
            "--allowed-topic",
            args["own_topic"],
            "--cross-topic",
            args["cross_topic"],
            "--client-id",
            args["client_id"],
            "--run-id",
            args["run_id"],
        ]
        process = engine.exec_create(cid, cmd=command, stdout=True, stderr=True)
        output = engine.exec_start(process["Id"])
        if not isinstance(output, bytes) or len(output) > 64 * 1024:
            raise ValueError("invalid MQTT probe output")
        try:
            result = json.loads(output.decode("utf-8").splitlines()[-1])
        except (UnicodeError, ValueError, IndexError) as exc:
            raise RuntimeError(
                "MQTT probe returned no valid result; rebuild HMI"
            ) from exc
        if not isinstance(result, dict):
            raise ValueError("invalid MQTT probe result")
        if engine.exec_inspect(process["Id"]).get("ExitCode") != 0:
            reason = result.get("reason")
            return {
                "status": "failed",
                "reason": reason
                if reason in {"ValueError", "RuntimeError", "TimeoutError", "OSError"}
                else "helper_failed",
            }
        return {
            "status": result.get("status"),
            "client_id": result.get("client_id"),
            "source": result.get("source"),
            "run_id": result.get("run_id"),
            "connected": result.get("connected") is True,
            "attempts": result.get("attempts"),
            "stopped_at_utc": result.get("stopped_at_utc"),
        }
    if request["kind"] == "evidence":
        args = request["args"]
        client_id = args.get("client_id")
        if not isinstance(client_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", client_id
        ):
            raise ValueError("invalid MQTT probe client ID")
        logs = engine.logs(cid, tail=4000, timestamps=False)
        if not isinstance(logs, (bytes, str)) or len(logs) > 2 * 1024 * 1024:
            raise ValueError("broker evidence log is unavailable or too large")
        return _broker_evidence(logs, client_id, args["own_topic"], args["cross_topic"])
    if request["kind"] == "scada_evidence":
        args = request["args"]
        if not all(
            isinstance(args.get(key), str) and args[key]
            for key in ("run_id", "client_id", "own_topic", "cross_topic")
        ):
            raise ValueError("invalid SCADA evidence arguments")
        logs = engine.logs(cid, tail=4000, timestamps=False)
        if not isinstance(logs, (bytes, str)) or len(logs) > 2 * 1024 * 1024:
            raise ValueError("SCADA evidence log is unavailable or too large")
        return _scada_evidence(
            logs,
            args["run_id"],
            args["client_id"],
            args["own_topic"],
            args["cross_topic"],
        )
    raise ValueError("unsupported MQTT probe operation")


def _worker(connection, base_url: str):
    engine = None
    try:
        engine = _direct_docker_client(base_url)
        engine.timeout = 25
        while True:
            operation, cid = connection.recv()
            if operation == "close":
                connection.send((True, None))
                return
            try:
                connection.send(
                    (True, _remote_operation(engine, cid, json.loads(operation)))
                )
            except Exception as exc:  # noqa: BLE001 - no Docker output or secrets cross the boundary
                connection.send((False, type(exc).__name__))
    except (EOFError, BrokenPipeError):
        pass
    finally:
        if engine is not None:
            engine.close()
        connection.close()


def execute(
    node: dict, hosts: dict[str, str], image: str, kind: str, args: dict
) -> dict:
    """Run a fixed helper or read bounded broker evidence on the hosting compute."""
    compute = node.get("compute_id", "local")
    if compute not in hosts:
        raise ValueError(f"no Docker host mapping for compute {compute}")
    cid = container_id(node)
    if node.get("node_type") != "docker" or node.get("status") != "started" or not cid:
        raise ValueError("MQTT node is not a started Docker container")
    if node.get("properties", {}).get("image") != image:
        raise ValueError("GNS3 MQTT node image differs from selected inventory")
    engine = RemoteDockerClient(hosts[compute], timeout=25, worker_target=_worker)
    try:
        return engine._request(
            json.dumps({"image": image, "kind": kind, "args": args}),
            cid,
            timeout=25,
        )
    finally:
        engine.close()


class MqttAclRun:
    """One optional, capture-scoped allowed/denied publish pair."""

    def __init__(
        self,
        client,
        project_id: str,
        inventory: dict,
        hosts: dict[str, str],
        run_id: str,
        source: str,
        target: str,
        *,
        executor=execute,
    ):
        self.client, self.project_id, self.hosts = client, project_id, hosts
        self.run_id, self.executor = run_id, executor
        self.devices = {device["name"]: device for device in inventory["devices"]}
        self.declared = declared_probe(inventory, source, target)
        self.client_id = f"aclprobe-{run_id[:24]}-{uuid.uuid4().hex[:10]}"
        self.nodes: dict[str, dict] = {}
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.result = {
            **self.declared,
            "scenario_id": "mqtt.acl_cross_cell.v1",
            "status": "planned",
            "client_id": self.client_id,
            "attempts": [],
            "broker_evidence": {},
            "subscriber_evidence": {},
            "downstream_delivery": "unverified",
            "capture_links": {},
            "capture_selected": False,
            "capture_started": False,
            "capture_saved": False,
            "failures": [],
        }

    def snapshot(self) -> dict:
        with self.lock:
            return copy.deepcopy(self.result)

    def _failure(self, source: str, reason: str) -> None:
        with self.lock:
            self.result["failures"].append({"source": source, "reason": reason})
            self.result["status"] = "failed"

    def prepare(self) -> None:
        records: dict[str, list[dict]] = {}
        for node in self.client.project_nodes(self.project_id):
            records.setdefault(node["name"], []).append(node)
        for name in (
            self.declared["source"],
            self.declared["broker"],
            self.declared["scada"],
        ):
            matches = records.get(name, [])
            if len(matches) != 1:
                self._failure(name, "expected one built GNS3 node")
                continue
            node = matches[0]
            if (
                node.get("node_type") != "docker"
                or node.get("status") != "started"
                or not container_id(node)
            ):
                self._failure(name, "Docker node is not started")
            elif node.get("properties", {}).get("image") != self.devices[name]["image"]:
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

    def set_capture_points(
        self, link_plan: dict, selected: list[str], resolved: dict[str, str]
    ) -> None:
        """Require source, broker, and SCADA attachment captures."""
        links = link_plan.get("links", [])
        names = {
            "source": self.declared["source"],
            "broker": self.declared["broker"],
            "target_hmi": self.declared["target"],
            "scada": self.declared["scada"],
        }
        capture_links = {}
        for role, name in names.items():
            capture_links[role] = [
                link["id"]
                for link in links
                if name is not None
                and link.get("kind") == "access"
                and any(
                    endpoint.get("node") == name
                    for endpoint in link.get("endpoints", [])
                )
            ]
        physical = set(selected) & set(resolved)
        required = set(
            capture_links["source"] + capture_links["broker"] + capture_links["scada"]
        )
        with self.lock:
            self.result["capture_links"] = capture_links
            self.result["capture_selected"] = bool(required and required <= physical)
            self.result["downstream_capture_selected"] = bool(
                capture_links["scada"] and set(capture_links["scada"]) <= physical
            )
            self.result["target_hmi_capture_selected"] = bool(
                capture_links["target_hmi"]
                and set(capture_links["target_hmi"]) <= physical
            )
        if any(len(capture_links[role]) != 1 for role in ("source", "broker", "scada")):
            self._failure(
                "capture",
                "source, broker, or SCADA attachment link is missing or ambiguous",
            )
        elif not self.result["capture_selected"]:
            self._failure(
                "capture",
                "source, broker, or SCADA attachment link is unavailable for capture",
            )

    def mark_capture_started(self, started: list[str]) -> None:
        links = self.result["capture_links"]
        required = set(
            links.get("source", []) + links.get("broker", []) + links.get("scada", [])
        )
        with self.lock:
            self.result["capture_started"] = bool(required and required <= set(started))
        if self.result["capture_selected"] and not self.result["capture_started"]:
            self._failure(
                "capture", "source, broker, or SCADA attachment capture did not start"
            )

    def _run_probe(self) -> None:
        source = self.declared["source"]
        try:
            result = self.executor(
                self.nodes[source],
                self.hosts,
                self.devices[source]["image"],
                "probe",
                {
                    "own_topic": self.declared["own_topic"],
                    "cross_topic": self.declared["cross_topic"],
                    "client_id": self.client_id,
                    "run_id": self.run_id,
                },
            )
            expected_topics = {
                "own": self.declared["own_topic"],
                "cross": self.declared["cross_topic"],
            }
            attempts = result.get("attempts")
            valid = (
                result.get("status") == "sent"
                and result.get("client_id") == self.client_id
                and result.get("source") == source
                and result.get("run_id") == self.run_id
                and result.get("connected") is True
                and isinstance(attempts, list)
                and len(attempts) == 2
                and all(
                    isinstance(item, dict)
                    and item.get("kind") in expected_topics
                    and item.get("topic") == expected_topics[item["kind"]]
                    and item.get("acknowledged") is True
                    and _is_utc(item.get("timestamp"))
                    for item in attempts
                )
                and {item["kind"] for item in attempts} == {"own", "cross"}
            )
            if not valid:
                raise RuntimeError("HMI probe did not confirm both publish attempts")
            with self.lock:
                self.result["attempts"] = [
                    {
                        "kind": item["kind"],
                        "topic": item["topic"],
                        "timestamp": item["timestamp"],
                        "acknowledged": True,
                    }
                    for item in attempts
                ]
                self.result["probe_stopped_at_utc"] = result.get("stopped_at_utc")
        except Exception as exc:  # noqa: BLE001 - retain partial capture and safe failure
            self._failure(source, f"HMI probe failed: {type(exc).__name__}")

    def start(self) -> None:
        if self.result["status"] == "failed" or not self.result["capture_started"]:
            self._failure("probe", "MQTT ACL probe prerequisites were not met")
            return
        with self.lock:
            self.result["status"] = "running"
            self.result["started_at_utc"] = utc_now()
        self.thread = threading.Thread(
            target=self._run_probe, name="mqtt-acl-probe", daemon=True
        )
        self.thread.start()

    def finish(self) -> None:
        if self.thread is None:
            if self.result["status"] == "planned":
                with self.lock:
                    self.result["status"] = "skipped"
            return
        self.thread.join(timeout=27)
        if self.thread.is_alive():
            self._failure("probe", "HMI probe did not finish before cleanup deadline")
        if self.result["attempts"]:
            broker = self.declared["broker"]
            try:
                observed = self.executor(
                    self.nodes[broker],
                    self.hosts,
                    self.devices[broker]["image"],
                    "evidence",
                    {
                        "own_topic": self.declared["own_topic"],
                        "cross_topic": self.declared["cross_topic"],
                        "client_id": self.client_id,
                    },
                )
                evidence = {
                    key: observed.get(key) is True
                    for key in (
                        "own_received",
                        "own_denied",
                        "cross_received",
                        "cross_denied",
                    )
                }
                with self.lock:
                    self.result["broker_evidence"] = evidence
                if (
                    evidence.get("own_received") is not True
                    or evidence.get("own_denied") is True
                    or evidence.get("cross_denied") is not True
                    or evidence.get("cross_received") is True
                ):
                    self._failure(
                        "broker",
                        "MQTT broker did not verify own-topic allow and cross-cell deny",
                    )
            except Exception as exc:  # noqa: BLE001 - do not claim ACL result without broker evidence
                self._failure(
                    "broker", f"broker evidence unavailable: {type(exc).__name__}"
                )
            scada = self.declared["scada"]
            try:
                observed = self.executor(
                    self.nodes[scada],
                    self.hosts,
                    self.devices[scada]["image"],
                    "scada_evidence",
                    {
                        "run_id": self.run_id,
                        "client_id": self.client_id,
                        "own_topic": self.declared["own_topic"],
                        "cross_topic": self.declared["cross_topic"],
                    },
                )
                evidence = {
                    "own_received": observed.get("own_received") is True,
                    "cross_received": observed.get("cross_received") is True,
                }
                with self.lock:
                    self.result["subscriber_evidence"] = evidence
                    if (
                        self.result["broker_evidence"].get("cross_denied") is True
                        and evidence["own_received"]
                        and not evidence["cross_received"]
                    ):
                        self.result["downstream_delivery"] = "blocked_by_broker"
                if not evidence["own_received"] or evidence["cross_received"]:
                    self._failure(
                        "scada",
                        "SCADA did not verify own-topic delivery and cross-topic isolation",
                    )
            except Exception as exc:  # noqa: BLE001 - never infer subscriber delivery
                self._failure(
                    "scada", f"SCADA evidence unavailable: {type(exc).__name__}"
                )
        with self.lock:
            self.result["stopped_at_utc"] = utc_now()
            if self.result["status"] == "running":
                self.result["status"] = "pending_capture"

    def mark_capture_saved(self, files: dict[str, str]) -> None:
        links = self.result["capture_links"]
        required = set(
            links.get("source", []) + links.get("broker", []) + links.get("scada", [])
        )
        with self.lock:
            self.result["capture_saved"] = bool(required and required <= set(files))
            self.result["downstream_capture_saved"] = bool(
                links.get("scada") and set(links["scada"]) <= set(files)
            )
            if (
                self.result["status"] == "pending_capture"
                and self.result["capture_saved"]
            ):
                self.result["status"] = "complete"
        if self.result["capture_started"] and not self.result["capture_saved"]:
            self._failure(
                "capture", "source, broker, or SCADA attachment PCAP was not saved"
            )
