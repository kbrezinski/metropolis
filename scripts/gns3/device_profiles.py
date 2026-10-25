"""Opt-in, reversible Docker resource limits, separate from resource monitoring."""

from __future__ import annotations

import json
import math
from pathlib import Path
from urllib.parse import urlsplit

from device_logs import container_id
from resource_metrics import RemoteDockerClient, _direct_docker_client

MIN_MEMORY = 6 * 1024 * 1024
CPU_PERIOD = 100_000
LIMITS = {
    "cpu_period": "CpuPeriod",
    "cpu_quota": "CpuQuota",
    "nano_cpus": "NanoCpus",
    "mem_limit": "Memory",
    "memswap_limit": "MemorySwap",
}
INFO_FIELDS = ("OSType", "MemTotal", "MemoryLimit", "CpuCfsPeriod", "CpuCfsQuota")


def _inspected(engine, cid: str) -> dict:
    value = engine.inspect_container(cid)
    config = value.get("HostConfig") or {}
    return {
        "Id": value.get("Id"),
        "HostConfig": {
            key: config.get(key, 0) for key in (*LIMITS.values(), "MemoryReservation")
        },
    }


def _update(engine, cid: str, limits: dict) -> dict:
    """Use the Engine API directly; SDK update_container omits zero fields."""
    if not limits or set(limits) - set(LIMITS.values()):
        raise ValueError("unsupported device resource update")
    if any(type(value) is not int for value in limits.values()):
        raise ValueError("resource updates require integer values")
    response = engine._post_json(
        engine._url("/containers/{0}/update", cid), data=limits
    )
    value = engine._result(response, True)
    return {"Warnings": value.get("Warnings") or []}


def _profile_worker(connection, base_url: str, factory=None) -> None:
    """Bound SSH setup and updates in a dedicated, terminable process."""
    engine = None
    try:
        if factory is None:
            import paramiko

            config_path = Path.home() / ".ssh" / "config"
            if config_path.is_file():
                config = paramiko.SSHConfig()
                with config_path.open(encoding="utf-8") as source:
                    config.parse(source)
                if config.lookup(urlsplit(base_url).hostname).get("proxycommand"):
                    raise ValueError("SSH ProxyCommand is unsupported for profiles")
        engine = (factory or _direct_docker_client)(base_url)
        while True:
            operation, cid = connection.recv()
            if operation == "close":
                connection.send((True, None))
                break
            try:
                request = json.loads(operation)
                action = request.get("action")
                if action == "inspect":
                    result = _inspected(engine, cid)
                elif action == "info":
                    info = engine.info()
                    result = {key: info.get(key) for key in INFO_FIELDS}
                elif action == "update":
                    result = _update(engine, cid, request["limits"])
                else:
                    raise ValueError("unsupported profile operation")
                connection.send((True, result))
            except Exception as exc:  # noqa: BLE001 - never relay inspect secrets
                connection.send((False, type(exc).__name__))
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:  # noqa: BLE001
        try:
            connection.send((False, type(exc).__name__))
        except (EOFError, BrokenPipeError, OSError):
            pass
    finally:
        if engine is not None:
            engine.close()
        connection.close()


class ProfileDockerClient:
    """Resource-only Engine adapter with bounded SSH operations."""

    def __init__(self, base_url: str):
        self.remote = base_url.startswith("ssh://")
        self.client = (
            RemoteDockerClient(base_url, worker_target=_profile_worker)
            if self.remote
            else _direct_docker_client(base_url)
        )

    @property
    def closed(self) -> bool:
        return getattr(self.client, "closed", False)

    def _request(self, action: str, cid=None, **fields):
        return self.client._request(json.dumps({"action": action, **fields}), cid)

    def inspect_container(self, cid: str) -> dict:
        return (
            self._request("inspect", cid)
            if self.remote
            else _inspected(self.client, cid)
        )

    def info(self) -> dict:
        if self.remote:
            return self._request("info")
        value = self.client.info()
        return {key: value.get(key) for key in INFO_FIELDS}

    def update_limits(self, cid: str, limits: dict) -> dict:
        return (
            self._request("update", cid, limits=limits)
            if self.remote
            else _update(self.client, cid, limits)
        )

    def close(self) -> None:
        self.client.close()


def validate_settings(settings: dict) -> None:
    if not isinstance(settings, dict) or not settings:
        raise ValueError("a device profile requires CPU or RAM settings")
    if set(settings) - {"cpus", "memory_bytes"}:
        raise ValueError("unsupported device profile setting")
    if "cpus" in settings:
        cpus = settings["cpus"]
        if (
            isinstance(cpus, bool)
            or not isinstance(cpus, (int, float))
            or not math.isfinite(cpus)
            or cpus < 0.01
        ):
            raise ValueError("cpus must be finite and at least 0.01")
    if "memory_bytes" in settings and (
        type(settings["memory_bytes"]) is not int
        or settings["memory_bytes"] < MIN_MEMORY
    ):
        raise ValueError("memory_bytes must be an integer of at least 6 MiB")


class DeviceProfileBackend:
    """Inspect, apply, verify, and restore only selected containers' limits."""

    def __init__(self, docker_hosts: dict, *, client_factory=None):
        self.hosts = dict(docker_hosts)
        self.factory = client_factory or ProfileDockerClient
        self.engines = {}

    def _target(self, node: dict):
        if node.get("node_type") != "docker":
            raise ValueError("device resource profiles require a Docker node")
        cid = container_id(node)
        if not cid:
            raise ValueError("device profile target has no container ID")
        compute = node.get("compute_id", "local")
        if compute not in self.hosts:
            raise ValueError(f"no Docker host mapping for compute {compute}")
        engine = self.engines.get(compute)
        if engine is None or getattr(engine, "closed", False):
            engine = self.factory(self.hosts[compute])
            self.engines[compute] = engine
        return engine, cid, compute

    def inspect(self, node: dict) -> dict:
        engine, cid, compute = self._target(node)
        inspected = engine.inspect_container(cid)
        actual_id = inspected.get("Id")
        if not isinstance(actual_id, str) or not (
            actual_id == cid or (len(cid) >= 12 and actual_id.startswith(cid))
        ):
            raise ValueError("Docker container identity differs from the GNS3 node")
        config = inspected.get("HostConfig") or {}
        limits = {key: config.get(field, 0) for key, field in LIMITS.items()}
        if any(type(value) is not int for value in limits.values()):
            raise ValueError("Docker returned invalid resource limits")
        info = engine.info()
        if info.get("OSType") not in (None, "linux"):
            raise ValueError("resource profiles require Linux Docker containers")
        return {
            "container_id": actual_id,
            "compute_id": compute,
            "node_id": node["node_id"],
            "limits": limits,
            "memory_reservation": config.get("MemoryReservation", 0),
            "capabilities": {
                key: info.get(key)
                for key in ("MemoryLimit", "CpuCfsPeriod", "CpuCfsQuota")
            },
        }

    @staticmethod
    def prepare(snapshot: dict, settings: dict) -> dict:
        """Validate reversibility before performing any mutation."""
        validate_settings(settings)
        limits, caps = snapshot["limits"], snapshot.get("capabilities", {})
        fields = {}
        if "cpus" in settings:
            if caps.get("CpuCfsPeriod") is False or caps.get("CpuCfsQuota") is False:
                raise ValueError("the Docker compute cannot enforce CPU quota limits")
            if limits["nano_cpus"] > 0:
                fields["NanoCpus"] = round(settings["cpus"] * 1_000_000_000)
            else:
                fields.update(
                    CpuPeriod=CPU_PERIOD, CpuQuota=round(settings["cpus"] * CPU_PERIOD)
                )
        if "memory_bytes" in settings:
            if caps.get("MemoryLimit") is False:
                raise ValueError("the Docker compute cannot enforce memory limits")
            if limits["mem_limit"] <= 0 or limits["memswap_limit"] == 0:
                raise ValueError(
                    "reversible RAM profiles require an existing finite memory limit "
                    "and an explicit memory-plus-swap limit; unlimited RAM cannot be "
                    "restored reliably without recreating the container"
                )
            memory = settings["memory_bytes"]
            if memory < snapshot.get("memory_reservation", 0):
                raise ValueError("profile RAM is below the existing memory reservation")
            swap = limits["memswap_limit"]
            if swap > 0 and memory > swap:
                raise ValueError(
                    "profile RAM exceeds the existing memory-plus-swap limit"
                )
            fields["Memory"] = memory
            fields["MemorySwap"] = swap
        return fields

    def _write_and_verify(self, node: dict, fields: dict) -> dict:
        engine, cid, _compute = self._target(node)
        result = engine.update_limits(cid, fields)
        if result.get("Warnings"):
            raise RuntimeError(
                "Docker reported warnings while applying resource limits"
            )
        actual = self.inspect(node)
        expected = {key: value for key, value in LIMITS.items() if value in fields}
        if any(
            actual["limits"][key] != fields[field] for key, field in expected.items()
        ):
            raise RuntimeError(
                "Docker resource limit readback did not match the request"
            )
        return actual

    def apply(self, node: dict, settings: dict) -> dict:
        snapshot = self.inspect(node)
        return self._write_and_verify(node, self.prepare(snapshot, settings))

    def restore(self, node: dict, snapshot: dict) -> dict:
        managed = snapshot.get("managed_fields")
        if managed is None:
            managed = set(LIMITS.values())
        elif (
            not isinstance(managed, list)
            or not managed
            or any(not isinstance(field, str) for field in managed)
            or set(managed) - set(LIMITS.values())
        ):
            raise ValueError("invalid managed resource fields in profile snapshot")
        else:
            managed = set(managed)
        current = self.inspect(node)
        if any(
            current[key] != snapshot.get(key)
            for key in ("container_id", "compute_id", "node_id")
        ):
            raise ValueError(
                "refusing to restore resource limits onto a different node"
            )
        original, actual = snapshot["limits"], current["limits"]
        fields, notes = {}, []
        if managed & {"CpuPeriod", "CpuQuota", "NanoCpus"} and any(
            original[key] != actual[key]
            for key in ("cpu_period", "cpu_quota", "nano_cpus")
        ):
            if original["nano_cpus"] > 0:
                fields["NanoCpus"] = original["nano_cpus"]
            else:
                fields["CpuPeriod"] = original["cpu_period"] or CPU_PERIOD
                fields["CpuQuota"] = (
                    original["cpu_quota"] if original["cpu_quota"] > 0 else -1
                )
                if original["cpu_period"] == 0 or original["cpu_quota"] == 0:
                    notes.append(
                        "Unlimited CPU restored with an equivalent quota=-1; Docker's original zero-valued metadata may differ."
                    )
        if managed & {"Memory", "MemorySwap"} and any(
            original[key] != actual[key] for key in ("mem_limit", "memswap_limit")
        ):
            if original["mem_limit"] <= 0 or original["memswap_limit"] == 0:
                raise ValueError(
                    "cannot restore an unlimited or implicit RAM baseline safely"
                )
            fields.update(
                Memory=original["mem_limit"], MemorySwap=original["memswap_limit"]
            )
        restored = self._write_and_verify(node, fields) if fields else current
        if notes:
            restored["restoration_notes"] = notes
        return restored

    def close(self) -> None:
        failures = []
        for compute, engine in self.engines.items():
            try:
                engine.close()
            except Exception as exc:  # noqa: BLE001 - attempt every compute
                failures.append(f"{compute}: {type(exc).__name__}")
        self.engines.clear()
        if failures:
            raise RuntimeError(
                "could not close profile connections: " + ", ".join(failures)
            )
