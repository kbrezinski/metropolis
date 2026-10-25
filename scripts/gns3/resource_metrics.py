"""Read-only container and compute resource samples for experiment runs."""

from __future__ import annotations

import copy
import json
import math
import multiprocessing
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from urllib.parse import urlsplit

from _transport import Transport
from client import Gns3Client
from device_logs import container_id, utc_now

LIMIT_FIELDS = (
    "Memory",
    "MemorySwap",
    "MemoryReservation",
    "NanoCpus",
    "CpuPeriod",
    "CpuQuota",
    "CpusetCpus",
    "CpusetMems",
)


def _direct_docker_client(base_url: str):
    """Use Paramiko for SSH so killing a worker also closes its SSH connection."""
    try:
        import docker
    except ImportError as exc:
        raise RuntimeError("Docker SDK unavailable; run uv sync --group dev") from exc
    return docker.APIClient(
        base_url=base_url,
        version="auto",
        timeout=3,
        use_ssh_client=False,
    )


def _remote_result(engine, operation: str, cid: str | None):
    """Only allow resource operations and return fields needed by the collector."""
    if operation == "info":
        info = engine.info()
        return {key: info.get(key) for key in ("NCPU", "MemTotal")}
    if operation == "inspect_container":
        inspected = engine.inspect_container(cid)
        config = inspected.get("HostConfig") or {}
        return {
            "HostConfig": {key: config[key] for key in LIMIT_FIELDS if key in config},
            "State": {"OOMKilled": (inspected.get("State") or {}).get("OOMKilled")},
        }
    if operation == "stats":
        stats = engine.stats(cid, stream=False, one_shot=True)
        cpu = stats.get("cpu_stats") or {}
        usage = cpu.get("cpu_usage") or {}
        memory = stats.get("memory_stats") or {}
        details = memory.get("stats") or {}
        return {
            "cpu_stats": {
                "cpu_usage": {
                    key: usage.get(key) for key in ("total_usage", "percpu_usage")
                },
                "system_cpu_usage": cpu.get("system_cpu_usage"),
                "online_cpus": cpu.get("online_cpus"),
            },
            "memory_stats": {
                "usage": memory.get("usage"),
                "limit": memory.get("limit"),
                "stats": {
                    key: details[key]
                    for key in ("total_inactive_file", "inactive_file", "cache")
                    if key in details
                },
            },
        }
    raise ValueError("unsupported Docker metric operation")


def _remote_docker_worker(connection, base_url: str, factory=None):
    """Keep blocking SSH setup and socket reads inside a terminable process."""
    engine = None
    try:
        # SDK SSH reads the user's config and known hosts. ProxyCommand could
        # start an external process surviving forced worker shutdown, so reject
        # it rather than weaken bounded cleanup or host-key verification.
        if factory is None:
            import paramiko

            config_path = Path.home() / ".ssh" / "config"
            if config_path.is_file():
                config = paramiko.SSHConfig()
                with config_path.open(encoding="utf-8") as source:
                    config.parse(source)
                if config.lookup(urlsplit(base_url).hostname).get("proxycommand"):
                    raise ValueError(
                        "SSH ProxyCommand is unsupported for resource metrics"
                    )
        engine = (factory or _direct_docker_client)(base_url)
        while True:
            operation, cid = connection.recv()
            if operation == "close":
                engine.close()
                connection.send((True, None))
                break
            try:
                connection.send((True, _remote_result(engine, operation, cid)))
            except Exception as exc:  # noqa: BLE001 - do not send remote payloads or credentials
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


class RemoteDockerClient:
    """Small read-only RPC proxy with a hard deadline around remote SDK calls."""

    def __init__(
        self, base_url: str, *, timeout: float = 5, worker_target=None, context=None
    ):
        self.timeout = timeout
        self.closed = False
        context = context or multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(
            target=worker_target or _remote_docker_worker,
            args=(child, base_url),
            daemon=True,
        )
        try:
            self.process.start()
        except BaseException:
            self.connection.close()
            child.close()
            raise
        child.close()

    def _terminate(self):
        self.closed = True
        self.connection.close()
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=0.5)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=0.5)

    def _request(self, operation: str, cid: str | None = None, *, timeout=None):
        if self.closed:
            raise RuntimeError("remote Docker metrics worker is closed")
        if cid is not None and (not isinstance(cid, str) or len(cid) > 256):
            raise ValueError("invalid Docker container ID")
        try:
            self.connection.send((operation, cid))
            if not self.connection.poll(self.timeout if timeout is None else timeout):
                raise TimeoutError("remote Docker metrics request timed out")
            ok, result = self.connection.recv()
            if not ok:
                raise RuntimeError(f"remote Docker metrics unavailable ({result})")
            return result
        except BaseException:
            self._terminate()
            raise

    def info(self):
        return self._request("info")

    def inspect_container(self, cid):
        return self._request("inspect_container", cid)

    def stats(self, cid, *, stream=False, one_shot=True):
        if stream or not one_shot:
            raise ValueError("resource metrics permit only one-shot stats")
        return self._request("stats", cid)

    def close(self):
        if self.closed:
            return
        try:
            self._request("close", timeout=0.25)
        except (OSError, EOFError, RuntimeError, TimeoutError):
            pass
        finally:
            if not self.closed:
                self._terminate()


def docker_client(base_url: str):
    """Create a bounded connection; only resource reads are exposed remotely."""
    return (
        RemoteDockerClient(base_url)
        if base_url.startswith("ssh://")
        else _direct_docker_client(base_url)
    )


def number(value, *, maximum: float | None = None):
    """Keep absent, invalid, and negative readings distinct from zero usage."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value if maximum is None or value <= maximum else None


def cpu_reading(stats: dict, previous: tuple | None) -> tuple[float | None, tuple]:
    """Calculate Docker CPU percent, where 100 percent denotes one CPU core."""
    current = stats.get("cpu_stats") or {}
    total = number((current.get("cpu_usage") or {}).get("total_usage"))
    system = number(current.get("system_cpu_usage"))
    cpus = number(current.get("online_cpus"))
    if not cpus:
        cpus = len((current.get("cpu_usage") or {}).get("percpu_usage") or [])
    value = None
    if previous and total is not None and system is not None and cpus:
        old_total, old_system = previous
        if old_total is not None and old_system is not None:
            delta, system_delta = total - old_total, system - old_system
            if delta >= 0 and system_delta > 0:
                value = delta / system_delta * cpus * 100
    return value, (total, system)


def memory_readings(stats: dict) -> tuple[int | None, int | None, int | None]:
    memory = stats.get("memory_stats") or {}
    usage, limit = number(memory.get("usage")), number(memory.get("limit"))
    details = memory.get("stats") or {}
    cache = next(
        (
            number(details[key])
            for key in ("total_inactive_file", "inactive_file", "cache")
            if number(details.get(key)) is not None
        ),
        0,
    )
    working = max(0, usage - cache) if usage is not None else None
    return usage, working, limit


class ResourceCollector:
    """Discover nodes dynamically and flush samples without modifying devices."""

    def __init__(
        self,
        client,
        project_id: str,
        run_dir: Path,
        run_id: str,
        docker_hosts: dict[str, str],
        *,
        interval: float = 5.0,
        docker_client_factory=None,
        clock=time.monotonic,
        now=utc_now,
        max_workers: int = 8,
    ):
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("metrics interval must be finite and positive")
        # Use shorter monitoring requests without changing capture/log timeouts.
        self.client = (
            Gns3Client(client.server, Transport(client.server, timeout=3))
            if isinstance(client, Gns3Client) and type(client.transport) is Transport
            else client
        )
        self.project_id = project_id
        self.run_dir, self.run_id = Path(run_dir), run_id
        self.docker_hosts = dict(docker_hosts)
        self.interval, self.clock, self.now = interval, clock, now
        self.factory = docker_client_factory or docker_client
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=max_workers)
        self.engines = {}
        self.capacity = {}
        self.previous = {}
        self.valid_cpu_samples = {}
        self.valid_measurements = 0
        self.closed = False
        self.result = {
            "status": "running",
            "started_at_utc": now(),
            "container_samples_path": "metrics/containers.jsonl",
            "compute_samples_path": "metrics/computes.jsonl",
            "sources": {},
            "computes": {},
            "failures": [],
        }
        (self.run_dir / "metrics").mkdir(parents=True, exist_ok=True)
        for key in ("container_samples_path", "compute_samples_path"):
            (self.run_dir / self.result[key]).touch()

    def snapshot(self) -> dict:
        with self.lock:
            return copy.deepcopy(self.result)

    def _failure(self, source: str, reason: str) -> None:
        failure = {"source": source, "reason": reason}
        if failure not in self.result["failures"]:
            self.result["failures"].append(failure)

    def _write(self, key: str, record: dict) -> None:
        with (self.run_dir / self.result[key]).open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, allow_nan=False) + "\n")
            output.flush()

    def _engine(self, compute_id: str):
        key = (threading.get_ident(), compute_id)
        with self.lock:
            engine = self.engines.get(key)
            if engine is not None and getattr(engine, "closed", False):
                self.engines.pop(key, None)
                engine = None
        if engine is None:
            engine = self.factory(self.docker_hosts[compute_id])
            with self.lock:
                self.engines[key] = engine
        return engine

    def _sample_container(self, node: dict) -> tuple[dict, dict, float]:
        engine = self._engine(node.get("compute_id", "local"))
        cid = container_id(node)
        inspected = engine.inspect_container(cid)
        stats = engine.stats(cid, stream=False, one_shot=True)
        # Only resource settings are copied; inspect also contains credentials.
        config = inspected.get("HostConfig") or {}
        limits = {
            key: config[key]
            for key in LIMIT_FIELDS
            if key in config
            and (config[key] is None or isinstance(config[key], (int, float, str)))
            and not isinstance(config[key], bool)
        }
        usage, working, limit = memory_readings(stats)
        record = {
            "schema_version": "1.0",
            "timestamp": self.now(),
            "run_id": self.run_id,
            "source": node["name"],
            "node_id": node["node_id"],
            "compute_id": node.get("compute_id", "local"),
            "container_id": cid,
            "cpu_percent": None,
            "elapsed_seconds": None,
            "memory_usage_bytes": usage,
            "memory_working_set_bytes": working,
            "memory_limit_bytes": limit,
            "configured_limits": limits,
            "oom_killed": (inspected.get("State") or {}).get("OOMKilled"),
        }
        if not isinstance(record["oom_killed"], bool):
            record["oom_killed"] = None
        return record, stats, self.clock()

    def _computes(
        self, compute_ids: set[str], stop_event: threading.Event | None = None
    ) -> None:
        try:
            computes = {item["compute_id"]: item for item in self.client.computes()}
        except Exception as exc:  # noqa: BLE001 - retain other metric sources
            computes = {}
            self._failure(
                "computes", f"compute statistics unavailable ({type(exc).__name__})"
            )
        for compute_id in sorted(compute_ids):
            if stop_event is not None and stop_event.is_set():
                break
            value = computes.get(compute_id, {})
            connected = value.get("connected") is not False
            target = self.result["computes"].setdefault(
                compute_id,
                {
                    "compute_id": compute_id,
                    "name": value.get("name", compute_id),
                    "samples": 0,
                    "status": "pending",
                },
            )
            if compute_id not in self.capacity:
                self.capacity[compute_id] = {}
                if compute_id in self.docker_hosts:
                    try:
                        info = self._engine(compute_id).info()
                        self.capacity[compute_id] = {
                            "cpu_count": number(info.get("NCPU")),
                            "memory_total_bytes": number(info.get("MemTotal")),
                        }
                    except Exception:  # noqa: BLE001 - usage can still come from GNS3
                        pass
            record = {
                "schema_version": "1.0",
                "timestamp": self.now(),
                "run_id": self.run_id,
                "compute_id": compute_id,
                "source": value.get("name", compute_id),
                "cpu_percent": number(value.get("cpu_usage_percent"), maximum=100)
                if connected
                else None,
                "memory_percent": number(value.get("memory_usage_percent"), maximum=100)
                if connected
                else None,
                "cpu_count": self.capacity[compute_id].get("cpu_count"),
                "memory_total_bytes": self.capacity[compute_id].get(
                    "memory_total_bytes"
                ),
            }
            self._write("compute_samples_path", record)
            target["samples"] += 1
            if (
                record["cpu_percent"] is not None
                or record["memory_percent"] is not None
            ):
                self.valid_measurements += 1
            if record["cpu_percent"] is None or record["memory_percent"] is None:
                target.update(
                    status="unavailable",
                    reason="compute CPU or memory reading unavailable"
                    if connected
                    else "compute is disconnected; cached readings excluded",
                )
                self._failure(compute_id, target["reason"])
            else:
                target["status"] = "collecting"
                target.pop("reason", None)

    def sample_once(self, stop_event: threading.Event | None = None) -> None:
        """One bounded sampling pass; a single failed source preserves its peers."""
        if self.closed:
            raise RuntimeError("resource collector is closed")
        try:
            nodes = self.client.project_nodes(self.project_id)
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self._failure(
                    "discovery", f"node discovery unavailable ({type(exc).__name__})"
                )
            return
        with self.lock:
            self._computes(
                {node.get("compute_id", "local") for node in nodes}, stop_event
            )
            if stop_event is not None and stop_event.is_set():
                return
            present = {node["node_id"] for node in nodes}
            for node_id, source in self.result["sources"].items():
                if source["samples"] and node_id not in present:
                    source.update(
                        status="unavailable",
                        reason="previously sampled node disappeared during collection",
                    )
                    self._failure(node_id, source["reason"])
            pending = {}
            for node in nodes:
                node_id = node["node_id"]
                source = self.result["sources"].setdefault(
                    node_id,
                    {
                        "name": node["name"],
                        "node_id": node_id,
                        "node_type": node.get("node_type", "unknown"),
                        "compute_id": node.get("compute_id", "local"),
                        "container_ids": [],
                        "samples": 0,
                        "status": "pending",
                    },
                )
                source.update(
                    name=node["name"], compute_id=node.get("compute_id", "local")
                )
                if node.get("node_type") != "docker":
                    source.update(
                        status="unsupported",
                        reason="per-node metrics require a Docker container",
                    )
                    continue
                cid = container_id(node)
                if cid and cid not in source["container_ids"]:
                    source["container_ids"].append(cid)
                if node.get("status") != "started" or not cid:
                    source["reason"] = "node is not started or has no container ID"
                    if source["samples"]:
                        source["status"] = "unavailable"
                        self._failure(
                            node_id,
                            "previously sampled node stopped or lost its container ID",
                        )
                    continue
                if source["compute_id"] not in self.docker_hosts:
                    source.update(
                        status="unavailable",
                        reason="no Docker host mapping for compute",
                    )
                    self._failure(node_id, source["reason"])
                    continue
                pending[self.pool.submit(self._sample_container, node)] = source
        while pending:
            if stop_event is not None and stop_event.is_set():
                for future in pending:
                    future.cancel()
                break
            done, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in done:
                source = pending.pop(future)
                with self.lock:
                    try:
                        record, stats, sampled_at = future.result()
                        key = (
                            record["node_id"],
                            record["compute_id"],
                            record["container_id"],
                        )
                        old = self.previous.get(key)
                        cpu, counters = cpu_reading(stats, old[0] if old else None)
                        record["cpu_percent"] = cpu
                        if cpu is not None:
                            self.valid_cpu_samples[record["node_id"]] = (
                                self.valid_cpu_samples.get(record["node_id"], 0) + 1
                            )
                        elapsed = sampled_at - old[1] if old else None
                        record["elapsed_seconds"] = (
                            elapsed if elapsed is not None and elapsed > 0 else None
                        )
                        self.previous[key] = (counters, sampled_at)
                        self._write("container_samples_path", record)
                        if cpu is not None or record["memory_usage_bytes"] is not None:
                            self.valid_measurements += 1
                        source["samples"] += 1
                        source["status"] = "collecting"
                        source.pop("reason", None)
                        if record["memory_usage_bytes"] is None:
                            source.update(
                                status="unavailable",
                                reason="container memory reading unavailable",
                            )
                            self._failure(source["node_id"], source["reason"])
                        if any(value is None for value in counters):
                            source.update(
                                status="unavailable",
                                reason="container CPU counters unavailable",
                            )
                            self._failure(source["node_id"], source["reason"])
                    except Exception as exc:  # noqa: BLE001 - isolate source and never persist API payloads
                        reason = f"Docker statistics unavailable ({type(exc).__name__})"
                        if isinstance(exc, RuntimeError) and str(exc).startswith(
                            "Docker SDK unavailable"
                        ):
                            reason = str(exc)
                        source.update(status="unavailable", reason=reason)
                        self._failure(source["node_id"], reason)

    def close(self) -> None:
        """Stop pending work and retain completed samples and source coverage."""
        if self.closed:
            return
        self.pool.shutdown(wait=True, cancel_futures=True)
        with self.lock:
            for engine in self.engines.values():
                try:
                    engine.close()
                except Exception as exc:  # noqa: BLE001
                    self._failure(
                        "collector",
                        f"Docker metric connection close failed ({type(exc).__name__})",
                    )
            self.closed = True
            for key, source in self.result["sources"].items():
                if source["status"] == "unsupported":
                    continue
                if not source["samples"]:
                    source["status"] = "unavailable"
                    self._failure(
                        key, source.get("reason", "no container samples captured")
                    )
                elif source["status"] == "collecting":
                    source["status"] = "complete"
                if source["samples"] >= 2 and not self.valid_cpu_samples.get(key):
                    source.update(
                        status="unavailable",
                        reason="no valid container CPU intervals captured",
                    )
                    self._failure(key, source["reason"])
            for source in self.result["computes"].values():
                if source["status"] == "collecting":
                    source["status"] = "complete"
            if not self.result["sources"]:
                self._failure(
                    "collector", "no nodes discovered for resource monitoring"
                )
            if not self.valid_measurements:
                self._failure("collector", "no resource measurements captured")
            self.result["status"] = "failed" if self.result["failures"] else "complete"
            self.result["stopped_at_utc"] = self.now()

    def run(self, stop_event: threading.Event) -> dict:
        """Sample immediately and honor the experiment stop event on every pass."""
        try:
            while not stop_event.is_set():
                started = self.clock()
                self.sample_once(stop_event)
                stop_event.wait(max(0, self.interval - (self.clock() - started)))
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - durable partial output
            with self.lock:
                self._failure(
                    "collector", f"resource collector stopped ({type(exc).__name__})"
                )
        finally:
            self.close()
        return self.snapshot()
