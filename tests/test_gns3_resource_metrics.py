"""Resource monitoring with fake engines, dynamic nodes, and durable samples."""

from __future__ import annotations

import json
import sys
import threading
import time
from types import SimpleNamespace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

GNS3 = Path(__file__).resolve().parents[1] / "scripts" / "gns3"
sys.path.insert(0, str(GNS3))

import resource_metrics  # noqa: E402
from resource_metrics import (  # noqa: E402
    RemoteDockerClient,
    ResourceCollector,
    cpu_reading,
    memory_readings,
)
from _transport import Server  # noqa: E402
from client import Gns3Client  # noqa: E402


def node(
    *, name="Controller", cid="first", compute="remote", kind="docker", status="started"
):
    return {
        "node_id": name.lower(),
        "name": name,
        "node_type": kind,
        "status": status,
        "compute_id": compute,
        "properties": {"container_id": cid} if cid else {},
    }


def counters(total=1000, system=10000):
    return {
        "cpu_stats": {
            "cpu_usage": {"total_usage": total},
            "system_cpu_usage": system,
            "online_cpus": 4,
        },
        "memory_stats": {"usage": 1024, "limit": 4096, "stats": {"inactive_file": 256}},
    }


class FakeClient:
    def __init__(self, nodes=None):
        self.nodes = [node()] if nodes is None else nodes
        self.compute_values = {
            "compute_id": "remote",
            "name": "Remote compute",
            "cpu_usage_percent": 35,
            "memory_usage_percent": 40,
        }

    def project_nodes(self, project_id):
        assert project_id == "project"
        return self.nodes

    def computes(self):
        return [self.compute_values]


class FakeEngine:
    def __init__(self):
        self.calls = []
        self.count = {}
        self.closed = False

    def info(self):
        return {"NCPU": 4, "MemTotal": 8192, "IgnoredSecret": "DO_NOT_SAVE"}

    def inspect_container(self, cid):
        self.calls.append(("inspect", cid))
        return {
            "HostConfig": {
                "Memory": 4096,
                "CpuQuota": -1,
                "CpusetCpus": "0-3",
                "Env": "DO_NOT_SAVE",
            },
            "Config": {"Env": ["PASSWORD=DO_NOT_SAVE"]},
            "State": {"OOMKilled": False},
        }

    def stats(self, cid, **kwargs):
        assert kwargs == {"stream": False, "one_shot": True}
        self.calls.append(("stats", cid))
        count = self.count.get(cid, 0)
        self.count[cid] = count + 1
        return counters(total=1000 + count * 500, system=10000 + count * 1000)

    def close(self):
        self.closed = True


def fake_remote_factory(_uri):
    return FakeEngine()


def responsive_worker(connection, uri):
    resource_metrics._remote_docker_worker(connection, uri, factory=fake_remote_factory)


def unresponsive_worker(connection, _uri):
    connection.recv()
    time.sleep(60)


def crashed_worker(connection, _uri):
    connection.close()
    raise SystemExit(7)


def collector(tmp_path, *, client=None, engine=None, **kwargs):
    engine = engine or FakeEngine()
    return ResourceCollector(
        client or FakeClient(),
        "project",
        tmp_path,
        "run-test",
        {"remote": "ssh://gns3@compute.example"},
        docker_client_factory=lambda _uri: engine,
        **kwargs,
    ), engine


def samples(resource, key):
    return [
        json.loads(line)
        for line in (resource.run_dir / resource.result[key]).read_text().splitlines()
    ]


def test_durable_samples_remote_attribution_limits_and_schema(tmp_path):
    urls = []
    ticks = iter(range(1000))
    resource, engine = collector(tmp_path, clock=lambda: next(ticks))
    resource.factory = lambda uri: urls.append(uri) or engine
    try:
        resource.sample_once()
        first = samples(resource, "container_samples_path")[0]
        assert first["cpu_percent"] is None
        assert first["elapsed_seconds"] is None
        assert first["memory_usage_bytes"] == 1024
        assert first["memory_working_set_bytes"] == 768
        assert first["memory_limit_bytes"] == 4096
        assert first["configured_limits"] == {
            "Memory": 4096,
            "CpuQuota": -1,
            "CpusetCpus": "0-3",
        }
        assert first["run_id"] == "run-test"
        assert first["source"] == "Controller"
        assert first["compute_id"] == "remote"
        assert all(uri == "ssh://gns3@compute.example" for uri in urls)
        assert "DO_NOT_SAVE" not in (tmp_path / "metrics/containers.jsonl").read_text()
        resource.sample_once()
        second = samples(resource, "container_samples_path")[1]
        assert second["cpu_percent"] == 200
        assert second["elapsed_seconds"] > 0
        compute = samples(resource, "compute_samples_path")[0]
        assert compute["cpu_count"] == 4
        assert compute["memory_total_bytes"] == 8192
        assert compute["cpu_percent"] == 35
        schema = json.loads(
            (GNS3.parents[1] / "schemas/resource-metric.schema.json").read_text()
        )
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for record in [first, second, compute]:
            validator.validate(record)
            assert record["timestamp"].endswith("Z")
    finally:
        resource.close()
    assert engine.closed
    assert resource.snapshot()["status"] == "complete"
    assert resource.result["sources"]["controller"]["status"] == "complete"


@pytest.mark.parametrize("field", ["total_inactive_file", "cache"])
def test_memory_accounts_for_cgroup_cache_formats(field):
    assert memory_readings(
        {"memory_stats": {"usage": 1024, "limit": 2048, "stats": {field: 300}}}
    ) == (1024, 724, 2048)
    assert memory_readings({}) == (None, None, None)


def test_cpu_initial_reset_missing_and_legacy_core_counts():
    assert cpu_reading(counters(), None)[0] is None
    assert cpu_reading(counters(5, 10100), (1000, 10000))[0] is None
    assert cpu_reading(counters(1500, 10000), (1000, 10000))[0] is None
    assert cpu_reading({}, (1000, 10000))[0] is None
    value = counters(1500, 11000)
    value["cpu_stats"].pop("online_cpus")
    value["cpu_stats"]["cpu_usage"]["percpu_usage"] = [1, 2]
    assert cpu_reading(value, (1000, 10000))[0] == 100


def test_container_replacement_restarts_cpu_window(tmp_path):
    client = FakeClient()
    resource, _ = collector(tmp_path, client=client)
    try:
        resource.sample_once()
        resource.sample_once()
        client.nodes[0]["properties"]["container_id"] = "replacement"
        resource.sample_once()
    finally:
        resource.close()
    records = samples(resource, "container_samples_path")
    assert [record["cpu_percent"] for record in records] == [None, 200, None]
    assert records[-1]["container_id"] == "replacement"
    assert resource.result["sources"]["controller"]["container_ids"] == [
        "first",
        "replacement",
    ]


@pytest.mark.parametrize("change", ["disappeared"])
def test_sampled_node_loss_remains_a_failure_after_recovery(tmp_path, change):
    client = FakeClient()
    original = client.nodes[0]
    resource, _ = collector(tmp_path, client=client)
    try:
        resource.sample_once()
        resource.sample_once()
        client.nodes = []
        resource.sample_once()
        assert resource.result["sources"]["controller"]["status"] == "unavailable"
        assert any(change in item["reason"] for item in resource.result["failures"])
        original["status"] = "started"
        client.nodes = [original]
        resource.sample_once()
    finally:
        resource.close()
    assert resource.result["sources"]["controller"]["samples"] == 3
    assert resource.result["status"] == "failed"


def test_initially_stopped_node_can_start_without_permanent_failure(tmp_path):
    client = FakeClient([node(status="stopped")])
    resource, _ = collector(tmp_path, client=client)
    resource.sample_once()
    client.nodes[0]["status"] = "started"
    resource.sample_once()
    resource.sample_once()
    resource.close()
    assert resource.result["status"] == "complete"
    assert resource.result["sources"]["controller"]["samples"] == 2


def test_partial_failures_preserve_healthy_sources_and_unsupported_nodes(tmp_path):
    class PartialEngine(FakeEngine):
        def stats(self, cid, **kwargs):
            if cid == "broken":
                raise TimeoutError("request payload PASSWORD=DO_NOT_SAVE")
            return super().stats(cid, **kwargs)

    nodes = [
        node(),
        node(name="Broken", cid="broken"),
        node(name="Router", kind="qemu"),
    ]
    resource, _ = collector(tmp_path, client=FakeClient(nodes), engine=PartialEngine())
    resource.sample_once()
    resource.close()
    result = resource.snapshot()
    assert result["status"] == "failed"
    assert result["sources"]["controller"]["samples"] == 1
    assert result["sources"]["broken"]["status"] == "unavailable"
    assert result["sources"]["router"]["status"] == "unsupported"
    assert len(result["failures"]) == 1
    assert "TimeoutError" in result["failures"][0]["reason"]
    assert "DO_NOT_SAVE" not in json.dumps(result)


def test_missing_host_mapping_stopped_node_and_compute_readings(tmp_path):
    client = FakeClient(
        [node(compute="unmapped"), node(name="Stopped", status="stopped")]
    )
    client.compute_values["cpu_usage_percent"] = None
    client.compute_values["memory_usage_percent"] = None
    resource, _ = collector(tmp_path, client=client)
    resource.sample_once()
    resource.close()
    assert resource.result["status"] == "failed"
    assert resource.result["sources"]["controller"]["samples"] == 0
    assert resource.result["sources"]["stopped"]["status"] == "unavailable"
    assert all(
        record["cpu_percent"] is None
        for record in samples(resource, "compute_samples_path")
    )
    assert any(
        "no Docker host mapping" in item["reason"]
        for item in resource.result["failures"]
    )


def test_memory_only_samples_do_not_imply_cpu_collection_succeeded(tmp_path):
    class MissingCpu(FakeEngine):
        def stats(self, _cid, **_kwargs):
            return {"memory_stats": {"usage": 1024, "limit": 4096}}

    resource, _ = collector(tmp_path, engine=MissingCpu())
    resource.sample_once()
    resource.sample_once()
    resource.close()
    assert resource.result["status"] == "failed"
    assert resource.result["sources"]["controller"]["status"] == "unavailable"
    assert all(
        record["cpu_percent"] is None
        for record in samples(resource, "container_samples_path")
    )
    assert any(
        "no valid container CPU intervals" in item["reason"]
        for item in resource.result["failures"]
    )


def test_disconnected_compute_excludes_cached_utilization(tmp_path):
    client = FakeClient([node(name="Router", kind="qemu")])
    client.compute_values["connected"] = False
    resource, _ = collector(tmp_path, client=client)
    resource.sample_once()
    resource.close()
    record = samples(resource, "compute_samples_path")[0]
    assert record["cpu_percent"] is record["memory_percent"] is None
    assert resource.result["status"] == "failed"
    assert any(
        "cached readings excluded" in item["reason"]
        for item in resource.result["failures"]
    )
    assert any(
        "no resource measurements captured" in item["reason"]
        for item in resource.result["failures"]
    )


def test_monitoring_timeout_does_not_change_shared_gns3_client(tmp_path):
    client = Gns3Client(Server("unused", 3080))
    resource, _ = collector(tmp_path, client=client)
    try:
        assert client.transport.timeout == 10
        assert resource.client is not client
        assert resource.client.transport.timeout == 3
        assert resource.client.server is client.server
    finally:
        resource.close()


def test_remote_worker_only_returns_whitelisted_metrics():
    remote = RemoteDockerClient(
        "ssh://gns3@compute.example", worker_target=responsive_worker
    )
    try:
        assert remote.info() == {"NCPU": 4, "MemTotal": 8192}
        inspected = remote.inspect_container("first")
        assert "Config" not in inspected
        assert "Env" not in inspected["HostConfig"]
        assert "DO_NOT_SAVE" not in json.dumps(inspected)
        value = remote.stats("first", stream=False, one_shot=True)
        assert memory_readings(value) == (1024, 768, 4096)
        with pytest.raises(ValueError, match="one-shot"):
            remote.stats("first", stream=True)
    finally:
        remote.close()
    assert remote.closed
    assert not remote.process.is_alive()


@pytest.mark.parametrize("target", [unresponsive_worker, crashed_worker])
def test_remote_hang_or_crash_has_bounded_cleanup(target):
    remote = RemoteDockerClient(
        "ssh://gns3@compute.example", timeout=0.4, worker_target=target
    )
    started = time.monotonic()
    try:
        with pytest.raises((TimeoutError, EOFError, BrokenPipeError, ConnectionError)):
            remote.info()
    finally:
        remote.close()
    assert time.monotonic() - started < 3
    assert remote.closed
    assert not remote.process.is_alive()


def test_direct_sdk_uses_paramiko_and_preserves_ssh_connection_settings(monkeypatch):
    calls = []
    expected = object()
    monkeypatch.setitem(
        sys.modules,
        "docker",
        SimpleNamespace(APIClient=lambda **kwargs: calls.append(kwargs) or expected),
    )
    uri = "ssh://researcher@gns3-alias:2222"
    assert resource_metrics._direct_docker_client(uri) is expected
    assert calls == [
        {"base_url": uri, "version": "auto", "timeout": 3, "use_ssh_client": False}
    ]


def test_closed_remote_session_is_replaced_after_transient_failure(tmp_path):
    failed = []

    class TransientEngine(FakeEngine):
        def stats(self, cid, **kwargs):
            if self.closed:
                raise ConnectionError("session is closed")
            if not failed:
                failed.append(True)
                self.closed = True
                raise TimeoutError
            return super().stats(cid, **kwargs)

    resource, _ = collector(tmp_path, max_workers=1)

    def factory(_uri):
        return TransientEngine()

    resource.factory = factory
    resource.sample_once()
    resource.sample_once()
    resource.close()
    assert resource.result["sources"]["controller"]["samples"] == 1
    assert resource.result["status"] == "failed"  # Preserve the earlier gap.
    assert any("TimeoutError" in item["reason"] for item in resource.result["failures"])


def test_run_stops_promptly_and_retains_incremental_data(tmp_path):
    resource, engine = collector(tmp_path, interval=30)
    written = threading.Event()
    original = resource._write

    def write(key, record):
        original(key, record)
        if key == "container_samples_path":
            written.set()

    resource._write = write
    stopped = threading.Event()
    thread = threading.Thread(target=resource.run, args=(stopped,))
    thread.start()
    try:
        assert written.wait(3)
        assert len(samples(resource, "container_samples_path")) == 1
    finally:
        stopped.set()
        thread.join(timeout=3)
    assert not thread.is_alive()
    assert engine.closed
    assert resource.result["status"] == "complete"


def test_interruption_closes_clients_and_reports_partial_failure(tmp_path):
    resource, _ = collector(tmp_path)

    def interrupted(_stop_event):
        raise KeyboardInterrupt

    resource.sample_once = interrupted
    resource.run(threading.Event())
    assert resource.closed
    assert resource.result["status"] == "failed"
    assert any(
        "KeyboardInterrupt" in item["reason"] for item in resource.result["failures"]
    )


@pytest.mark.parametrize("interval", [0])
def test_invalid_interval_rejected_before_collection(tmp_path, interval):
    with pytest.raises(ValueError, match="finite and positive"):
        collector(tmp_path, interval=interval)
