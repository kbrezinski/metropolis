"""Run-scoped enforcement, durable rollback, and observation of selected profiles."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

from capture import resolve_links
from device_logs import container_id, utc_now
from device_profiles import DeviceProfileBackend
from link_bandwidth import LinkBandwidthBackend
from link_identity import server_key

SWITCH_IMAGE = "metropolis/openvswitch:dev"


def _identity(node: dict) -> dict:
    return {
        "node_id": node["node_id"],
        "name": node["name"],
        "compute_id": node.get("compute_id", "local"),
        "node_type": node.get("node_type"),
        "status": node.get("status"),
        "properties": {
            "container_id": container_id(node),
            "image": node.get("properties", {}).get("image"),
        },
    }


def _filters(original: dict, settings: dict) -> dict:
    result = copy.deepcopy(original)
    if "delay_ms" in settings or "jitter_ms" in settings:
        baseline = original.get("delay", [0, 0])
        latency = settings.get("delay_ms", baseline[0])
        jitter = settings.get("jitter_ms", baseline[1] if len(baseline) > 1 else 0)
        if latency or jitter:
            result["delay"] = [latency, jitter]
        else:
            result.pop("delay", None)
    if "loss_percent" in settings:
        if settings["loss_percent"]:
            result["packet_loss"] = [settings["loss_percent"]]
        else:
            result.pop("packet_loss", None)
    return result


class ProfileSession:
    """Preflight all targets before writes, then restore every attempted change."""

    def __init__(
        self,
        client,
        project_id,
        topology,
        profile,
        run_dir,
        run_id,
        docker_hosts,
        *,
        device_backend_factory=DeviceProfileBackend,
        bandwidth_backend_factory=LinkBandwidthBackend,
    ):
        self.client, self.project_id, self.topology = client, project_id, topology
        self.profile, self.run_dir = profile, Path(run_dir)
        self.device_backend = device_backend_factory(docker_hosts)
        self.bandwidth_backend = bandwidth_backend_factory(docker_hosts, run_id)
        self.result = {
            "enabled": True,
            "name": profile["name"],
            "path": profile.get("path", ""),
            "sha256": profile.get("sha256")
            or hashlib.sha256(
                json.dumps(
                    {k: v for k, v in profile.items() if k not in {"path", "sha256"}},
                    sort_keys=True,
                    allow_nan=False,
                ).encode()
            ).hexdigest(),
            "state_path": "profiles.json",
            "status": "planned",
            "configuration_verified": False,
            "restored": False,
            "devices": [],
            "links": [],
            "failures": [],
        }
        self.state = {
            "schema_version": "1.0",
            "run_id": run_id,
            "project_id": project_id,
            "profile": self.result,
        }
        if hasattr(client, "server"):
            self.state["server"] = {
                "host": client.server.host,
                "port": client.server.port,
            }
        self._write()

    def _write(self):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.run_dir / "profiles.json.tmp"
        temporary.write_text(
            json.dumps(self.state, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        temporary.replace(self.run_dir / "profiles.json")

    def _failure(self, source, exc):
        failure = {"source": source, "reason": str(exc)}
        if failure not in self.result["failures"]:
            self.result["failures"].append(failure)
        self.result["status"] = "failed"
        self._checkpoint()

    def _checkpoint(self):
        """Checkpoint errors must never prevent rollback of remaining targets."""
        try:
            self._write()
        except OSError as exc:
            failure = {
                "source": "checkpoint",
                "reason": f"profile checkpoint failed: {type(exc).__name__}",
            }
            if failure not in self.result["failures"]:
                self.result["failures"].append(failure)
            self.result["status"] = "failed"

    def prepare(self):
        """Snapshot resource settings and port-aware link matches without writes."""
        try:
            records = self.client.project_nodes(self.project_id)
            names = {}
            for node in records:
                names.setdefault(node["name"], []).append(node)
            for selected in self.profile.get("devices", []):
                matches = names.get(selected["node"], [])
                if len(matches) != 1 or matches[0].get("node_type") != "docker":
                    raise ValueError(f"{selected['node']}: expected one Docker node")
                node = matches[0]
                if node.get("status") != "started" or not container_id(node):
                    raise ValueError(f"{selected['node']}: container is not started")
                snapshot = self.device_backend.inspect(node)
                settings = {k: v for k, v in selected.items() if k != "node"}
                prepared = self.device_backend.prepare(snapshot, settings)
                if prepared:
                    snapshot["managed_fields"] = list(prepared)
                self.result["devices"].append(
                    {
                        "node": _identity(node),
                        "settings": settings,
                        "snapshot": snapshot,
                        "attempted": False,
                        "verified": False,
                        "restored": False,
                    }
                )
                self._write()
            selected_ids = [item["link"] for item in self.profile.get("links", [])]
            resolution = (
                resolve_links(self.client, self.project_id, self.topology, selected_ids)
                if selected_ids
                else None
            )
            physical_links = set()
            for selected in self.profile.get("links", []):
                name = selected["link"]
                if name not in resolution.resolved:
                    raise ValueError(
                        f"{name}: {resolution.status.get(name, {}).get('status', 'missing')}"
                    )
                if resolution.resolved[name] in physical_links:
                    raise ValueError(
                        "profile declares the same physical link more than once"
                    )
                physical_links.add(resolution.resolved[name])
                raw = self.client.link(self.project_id, resolution.resolved[name])
                entry = {
                    "link": name,
                    "gns3_link_id": resolution.resolved[name],
                    "endpoints": raw["nodes"],
                    "original_filters": copy.deepcopy(raw.get("filters") or {}),
                    "settings": {k: v for k, v in selected.items() if k != "link"},
                    "filters_attempted": False,
                    "bandwidth_attempted": False,
                    "verified": False,
                    "restored": False,
                }
                entry["selected_filters"] = _filters(
                    entry["original_filters"], selected
                )
                if "bandwidth_kbps" in selected:
                    switches = [
                        (node, end["adapter_number"])
                        for end in raw["nodes"]
                        for node in records
                        if node["node_id"] == end["node_id"]
                        and node.get("node_type") == "docker"
                        and node.get("properties", {}).get("image") == SWITCH_IMAGE
                    ]
                    if not switches:
                        raise ValueError(
                            f"{name}: bandwidth requires a repo-owned Open vSwitch endpoint"
                        )
                    switch, adapter = sorted(
                        switches, key=lambda item: item[0]["node_id"]
                    )[0]
                    entry["bandwidth_node"] = _identity(switch)
                    entry["adapter"] = adapter
                    entry["bandwidth_snapshot"] = self.bandwidth_backend.inspect(
                        switch, adapter
                    )
                self.result["links"].append(entry)
                self._write()
        except (Exception, KeyboardInterrupt) as exc:
            self._failure("preflight", exc)
            raise

    def apply(self):
        try:
            for entry in self.result["devices"]:
                if (
                    self.device_backend.inspect(entry["node"])["limits"]
                    != entry["snapshot"]["limits"]
                ):
                    raise RuntimeError("device limits changed after preflight")
                entry["attempted"] = True
                self._write()
                entry["applied"] = self.device_backend.apply(
                    entry["node"], entry["settings"]
                )
                entry["verified"] = True
                self._write()
            for entry in self.result["links"]:
                current = self.client.link(self.project_id, entry["gns3_link_id"])
                if (
                    server_key(current or {})
                    != server_key({"nodes": entry["endpoints"]})
                    or (current.get("filters") or {}) != entry["original_filters"]
                ):
                    raise RuntimeError(f"{entry['link']}: link changed after preflight")
                if entry["selected_filters"] != entry["original_filters"]:
                    entry["filters_attempted"] = True
                    self._write()
                    self.client.set_link_filters(
                        self.project_id,
                        entry["gns3_link_id"],
                        entry["selected_filters"],
                    )
                    actual = self.client.link(self.project_id, entry["gns3_link_id"])
                    if (actual.get("filters") or {}) != entry["selected_filters"]:
                        raise RuntimeError(
                            f"{entry['link']}: GNS3 filter readback mismatch"
                        )
                if "bandwidth_node" in entry:
                    entry["bandwidth_attempted"] = True
                    self._write()
                    entry["bandwidth_applied"] = self.bandwidth_backend.apply(
                        entry["bandwidth_node"],
                        entry["adapter"],
                        entry["settings"]["bandwidth_kbps"],
                    )
                entry["verified"] = True
                self._write()
            self.result.update(status="applied", configuration_verified=True)
            self.state["applied_at_utc"] = utc_now()
            self._write()
        except (Exception, KeyboardInterrupt) as exc:
            self._failure("apply", exc)
            raise

    def restore(self):
        """Attempt every rollback even when an apply or another rollback failed."""
        self.state["restoring_at_utc"] = utc_now()
        self._checkpoint()
        restored = True
        for entry in reversed(self.result["links"]):
            if not entry["filters_attempted"] and not entry["bandwidth_attempted"]:
                entry["restored"] = True
                continue
            ok = True
            try:
                current = self.client.link(self.project_id, entry["gns3_link_id"])
                if server_key(current or {}) != server_key(
                    {"nodes": entry["endpoints"]}
                ):
                    raise RuntimeError(
                        "link endpoints changed; original link cannot be restored"
                    )
                if entry["bandwidth_attempted"]:
                    entry["bandwidth_restored_values"] = self.bandwidth_backend.restore(
                        entry["bandwidth_node"],
                        entry["adapter"],
                        entry["bandwidth_snapshot"],
                    )
            except (Exception, KeyboardInterrupt) as exc:
                ok = False
                self._failure(entry["link"], exc)
            try:
                if entry["filters_attempted"]:
                    if server_key(
                        self.client.link(self.project_id, entry["gns3_link_id"]) or {}
                    ) != server_key({"nodes": entry["endpoints"]}):
                        raise RuntimeError(
                            "link endpoints changed; filter restoration refused"
                        )
                    self.client.set_link_filters(
                        self.project_id,
                        entry["gns3_link_id"],
                        entry["original_filters"],
                    )
                    if (
                        self.client.link(self.project_id, entry["gns3_link_id"]).get(
                            "filters"
                        )
                        or {}
                    ) != entry["original_filters"]:
                        raise RuntimeError("original filter readback mismatch")
            except (Exception, KeyboardInterrupt) as exc:
                ok = False
                self._failure(entry["link"], exc)
            entry["restored"] = ok
            restored &= ok
            self._checkpoint()
        for entry in reversed(self.result["devices"]):
            if not entry["attempted"]:
                continue
            try:
                node = self.client.node(self.project_id, entry["node"]["node_id"])
                if (
                    not node
                    or container_id(node) != container_id(entry["node"])
                    or node.get("compute_id", "local") != entry["node"]["compute_id"]
                ):
                    raise RuntimeError(
                        "container changed; refusing to restore a different instance"
                    )
                entry["restored_values"] = self.device_backend.restore(
                    node, entry["snapshot"]
                )
                entry["restored"] = True
            except (Exception, KeyboardInterrupt) as exc:
                restored = False
                self._failure(entry["node"]["name"], exc)
            self._checkpoint()
        self.result["restored"] = restored
        self.result["status"] = "failed" if self.result["failures"] else "restored"
        self.state["restored_at_utc"] = utc_now()
        self._checkpoint()

    def observe(self, metrics):
        """Record observed values and gaps; configuration readback is not a load test."""
        observations = {
            "achieved_verified": False,
            "explanation": "Settings were checked separately. Passive samples do not prove CPU capacity, link capacity, one-way delay, jitter, or packet loss under offered load.",
            "devices": {},
            "links": {},
            "computes": {},
        }
        start = self.state.get("applied_at_utc")
        stop = self.state.get("restoring_at_utc")
        observations["applied_at_utc"] = start
        observations["restoring_at_utc"] = stop
        observations["compute_pressure_threshold_percent"] = 90
        selected_nodes = {entry["node"]["node_id"] for entry in self.result["devices"]}
        selected_links = {entry["link"] for entry in self.result["links"]}
        for key in selected_nodes:
            observations["devices"][key] = {
                "status": "unavailable",
                "peak_cpu_percent": None,
                "peak_memory_bytes": None,
            }
        for key in selected_links:
            observations["links"][key] = {"status": "not_captured"}

        def in_window(value):
            return start is not None and datetime.fromisoformat(
                start.replace("Z", "+00:00")
            ) <= datetime.fromisoformat(
                value.replace("Z", "+00:00")
            ) <= datetime.fromisoformat((stop or utc_now()).replace("Z", "+00:00"))

        def rows(filename):
            path = self.run_dir / "metrics" / filename
            if not path.is_file():
                return
            with path.open(encoding="utf-8") as source:
                for line in source:
                    try:
                        value = json.loads(line)
                        if in_window(value["timestamp"]):
                            yield value
                    except (ValueError, TypeError, KeyError, AttributeError):
                        continue

        def peak(target, key, value):
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
            ):
                target[key] = max(target.get(key) or 0, value)

        for value in rows("containers.jsonl"):
            if value.get("node_id") not in selected_nodes:
                continue
            result = observations["devices"][value["node_id"]]
            result["status"] = "observed"
            peak(result, "peak_cpu_percent", value.get("cpu_percent"))
            peak(result, "peak_memory_bytes", value.get("memory_usage_bytes"))
        for value in rows("computes.jsonl"):
            if not value.get("compute_id"):
                continue
            result = observations["computes"].setdefault(
                value["compute_id"],
                {"peak_cpu_percent": None, "peak_memory_percent": None},
            )
            peak(result, "peak_cpu_percent", value.get("cpu_percent"))
            peak(result, "peak_memory_percent", value.get("memory_percent"))
            result["possible_pressure"] = any(
                v is not None and v >= 90
                for v in (result["peak_cpu_percent"], result["peak_memory_percent"])
            )
        summary = self.run_dir / "metrics/summary.json"
        if metrics and summary.is_file():
            data = json.loads(summary.read_text(encoding="utf-8"))
            for key, value in data.get("traffic", {}).get("links", {}).items():
                if key not in selected_links:
                    continue
                observations["links"][key] = {
                    field: value[field]
                    for field in (
                        "mean_bits_per_second",
                        "peak_bits_per_second",
                        "packets",
                        "bytes",
                    )
                    if field in value
                }
                observations["links"][key]["status"] = value.get(
                    "status", "unavailable"
                )
        self.result["observations"] = observations
        self._write()

    def close(self):
        try:
            self.device_backend.close()
        finally:
            self.bandwidth_backend.close()

    @classmethod
    def recover(
        cls,
        client,
        state_path,
        docker_hosts,
        *,
        device_backend_factory=DeviceProfileBackend,
        bandwidth_backend_factory=LinkBandwidthBackend,
    ):
        """Retry restoration from a run's durable state without applying a profile."""
        state_path = Path(state_path).resolve()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("schema_version") != "1.0" or not isinstance(
            state.get("profile"), dict
        ):
            raise ValueError("not a supported profile recovery state")
        if state_path.name != "profiles.json":
            raise ValueError("recovery state must be a run's profiles.json")
        if state.get("server") and state["server"] != {
            "host": client.server.host,
            "port": client.server.port,
        }:
            raise ValueError(
                "recovery server differs from the recorded controller; supply its --host and --port"
            )
        self = cls.__new__(cls)
        self.client, self.project_id = client, state["project_id"]
        self.run_dir = state_path.parent
        self.state, self.result = state, state["profile"]
        self.device_backend = device_backend_factory(docker_hosts)
        self.bandwidth_backend = bandwidth_backend_factory(
            docker_hosts, state["run_id"]
        )
        try:
            self.restore()
        finally:
            self.close()
        manifest_path = self.run_dir / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("run_id") != state["run_id"]:
                raise ValueError("run manifest identity differs from recovery state")
            manifest["profiles"] = copy.deepcopy(self.result)
            temporary = self.run_dir / "manifest.json.tmp"
            temporary.write_text(
                json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8"
            )
            temporary.replace(manifest_path)
        return self.result
