"""Device resource enforcement uses explicit targets and reversible baselines."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

GNS3 = Path(__file__).resolve().parents[1] / "scripts" / "gns3"
sys.path.insert(0, str(GNS3))

import device_profiles  # noqa: E402
from device_profiles import DeviceProfileBackend, validate_settings  # noqa: E402

MIB = 1024 * 1024


def node(**changes):
    result = {
        "name": "Field device",
        "node_id": "node-a",
        "node_type": "docker",
        "compute_id": "remote-a",
        "properties": {"container_id": "container-a"},
    }
    result.update(changes)
    return result


class FakeEngine:
    def __init__(self):
        self.id = "container-a"
        self.limits = {
            "CpuPeriod": 0,
            "CpuQuota": 0,
            "NanoCpus": 0,
            "Memory": 512 * MIB,
            "MemorySwap": 1024 * MIB,
            "MemoryReservation": 0,
        }
        self.updates = []
        self.closed = False
        self.warnings = []
        self.ignore = False
        self.lost_reply = False

    def inspect_container(self, _cid):
        return {
            "Id": self.id,
            "HostConfig": {**self.limits},
            "Config": {"Env": ["PASSWORD=must-not-be-persisted"]},
        }

    def info(self):
        return {
            "OSType": "linux",
            "MemTotal": 8 << 30,
            "MemoryLimit": True,
            "CpuCfsPeriod": True,
            "CpuCfsQuota": True,
        }

    def update_limits(self, _cid, fields):
        self.updates.append(copy.deepcopy(fields))
        if not self.ignore:
            self.limits.update(fields)
        if self.lost_reply:
            self.lost_reply = False
            raise TimeoutError("reply lost after mutation")
        return {"Warnings": self.warnings}

    def close(self):
        self.closed = True


@pytest.fixture
def backend():
    engine = FakeEngine()
    connections = []

    def connect(uri):
        connections.append(uri)
        return engine

    result = DeviceProfileBackend(
        {"remote-a": "ssh://researcher@compute.example"}, client_factory=connect
    )
    yield result, engine, connections
    result.close()


def test_cpu_and_ram_apply_readback_and_equivalent_unlimited_cpu_restore(backend):
    result, engine, connections = backend
    baseline = result.inspect(node())
    assert "must-not-be-persisted" not in json.dumps(baseline)
    applied = result.apply(node(), {"cpus": 0.25, "memory_bytes": 128 * MIB})
    assert applied["limits"]["cpu_quota"] == 25_000
    assert applied["limits"]["mem_limit"] == 128 * MIB
    assert engine.updates[-1] == {
        "CpuPeriod": 100_000,
        "CpuQuota": 25_000,
        "Memory": 128 * MIB,
        "MemorySwap": 1024 * MIB,
    }
    restored = result.restore(node(), baseline)
    assert restored["limits"]["cpu_quota"] == -1
    assert restored["limits"]["mem_limit"] == baseline["limits"]["mem_limit"]
    assert restored["limits"]["memswap_limit"] == baseline["limits"]["memswap_limit"]
    assert restored["restoration_notes"]
    assert connections == ["ssh://researcher@compute.example"]


def test_existing_cpu_quota_restores_exactly(backend):
    result, engine, _connections = backend
    engine.limits.update(CpuPeriod=100_000, CpuQuota=75_000)
    baseline = result.inspect(node())
    result.apply(node(), {"cpus": 0.3})
    assert result.restore(node(), baseline)["limits"] == baseline["limits"]


def test_existing_nano_cpu_limit_stays_on_that_control_path(backend):
    result, engine, _connections = backend
    engine.limits["NanoCpus"] = 1_000_000_000
    baseline = result.inspect(node())
    result.apply(node(), {"cpus": 0.5})
    assert engine.updates == [{"NanoCpus": 500_000_000}]
    assert result.restore(node(), baseline)["limits"] == baseline["limits"]


@pytest.mark.parametrize("field", ["Memory", "MemorySwap"])
def test_unrestorable_ram_baseline_rejected_before_any_write(backend, field):
    result, engine, _connections = backend
    engine.limits[field] = 0
    baseline = result.inspect(node())
    with pytest.raises(ValueError, match="reversible RAM profiles"):
        result.prepare(baseline, {"cpus": 0.1, "memory_bytes": 128 * MIB})
    with pytest.raises(ValueError, match="reversible RAM profiles"):
        result.apply(node(), {"cpus": 0.1, "memory_bytes": 128 * MIB})
    assert engine.updates == []


def test_cpu_only_profile_leaves_unlimited_memory_unchanged(backend):
    result, engine, _connections = backend
    engine.limits.update(Memory=0, MemorySwap=0)
    baseline = result.inspect(node())
    result.apply(node(), {"cpus": 0.5})
    restored = result.restore(node(), baseline)
    assert restored["limits"]["mem_limit"] == 0
    assert restored["limits"]["memswap_limit"] == 0
    assert all("Memory" not in change for change in engine.updates)


def test_cpu_only_restore_preserves_external_ram_changes(backend):
    result, engine, _connections = backend
    baseline = result.inspect(node())
    settings = {"cpus": 0.25}
    baseline["managed_fields"] = list(result.prepare(baseline, settings))
    result.apply(node(), settings)
    engine.limits.update(Memory=768 * MIB, MemorySwap=1536 * MIB)
    restored = result.restore(node(), baseline)
    assert restored["limits"]["cpu_quota"] == -1
    assert restored["limits"]["mem_limit"] == 768 * MIB
    assert restored["limits"]["memswap_limit"] == 1536 * MIB
    assert "Memory" not in engine.updates[-1]


def test_ram_only_restore_preserves_external_cpu_changes(backend):
    result, engine, _connections = backend
    baseline = result.inspect(node())
    settings = {"memory_bytes": 128 * MIB}
    baseline["managed_fields"] = list(result.prepare(baseline, settings))
    result.apply(node(), settings)
    engine.limits.update(CpuPeriod=100_000, CpuQuota=40_000)
    restored = result.restore(node(), baseline)
    assert restored["limits"]["mem_limit"] == 512 * MIB
    assert restored["limits"]["cpu_quota"] == 40_000
    assert "CpuQuota" not in engine.updates[-1]


@pytest.mark.parametrize("managed", [["Env"]])
def test_invalid_managed_fields_never_mutate(backend, managed):
    result, engine, _connections = backend
    baseline = result.inspect(node())
    baseline["managed_fields"] = managed
    with pytest.raises(ValueError, match="managed resource fields"):
        result.restore(node(), baseline)
    assert engine.updates == []


def test_ram_checks_reservation_and_preserves_unlimited_swap(backend):
    result, engine, _connections = backend
    engine.limits.update(MemorySwap=-1, MemoryReservation=256 * MIB)
    with pytest.raises(ValueError, match="reservation"):
        result.apply(node(), {"memory_bytes": 128 * MIB})
    applied = result.apply(node(), {"memory_bytes": 384 * MIB})
    assert applied["limits"]["memswap_limit"] == -1


def test_explicit_no_swap_baseline_restores_and_rejects_ram_above_swap(backend):
    result, engine, _connections = backend
    engine.limits["MemorySwap"] = 512 * MIB
    baseline = result.inspect(node())
    result.apply(node(), {"memory_bytes": 128 * MIB})
    assert result.restore(node(), baseline)["limits"] == baseline["limits"]
    before = len(engine.updates)
    with pytest.raises(ValueError, match="memory-plus-swap"):
        result.apply(node(), {"memory_bytes": 768 * MIB})
    assert len(engine.updates) == before


def test_lost_reply_can_be_restored_from_persisted_original(backend):
    result, engine, _connections = backend
    baseline = result.inspect(node())
    engine.lost_reply = True
    with pytest.raises(TimeoutError):
        result.apply(node(), {"memory_bytes": 128 * MIB})
    assert engine.limits["Memory"] == 128 * MIB
    result.restore(node(), baseline)
    assert engine.limits["Memory"] == 512 * MIB


def test_ignored_update_and_docker_warnings_are_failures(backend):
    result, engine, _connections = backend
    engine.ignore = True
    with pytest.raises(RuntimeError, match="readback"):
        result.apply(node(), {"cpus": 0.2})
    engine.ignore = False
    engine.warnings = ["CPU CFS unavailable"]
    with pytest.raises(RuntimeError, match="warnings"):
        result.apply(node(), {"cpus": 0.2})


def test_wrong_identity_or_missing_compute_never_mutates(backend):
    result, engine, _connections = backend
    baseline = result.inspect(node())
    with pytest.raises(ValueError, match="mapping"):
        result.apply(node(compute_id="other"), {"cpus": 0.2})
    with pytest.raises(ValueError, match="Docker"):
        result.apply(node(node_type="qemu"), {"cpus": 0.2})
    engine.id = "replacement-container"
    with pytest.raises(ValueError, match="identity"):
        result.restore(node(), baseline)
    assert engine.updates == []


@pytest.mark.parametrize(
    "settings",
    [
        {"cpus": True},
        {"cpu_shares": 5},
    ],
)
def test_bad_settings_are_rejected(settings):
    with pytest.raises(ValueError):
        validate_settings(settings)


def test_raw_engine_update_preserves_explicit_values_and_only_resource_fields():
    calls = []

    class Raw:
        def _url(self, template, cid):
            return template.format(cid)

        def _post_json(self, url, data):
            calls.append((url, data))
            return "response"

        def _result(self, response, json_output):
            assert response == "response" and json_output
            return {"Warnings": None}

    assert device_profiles._update(Raw(), "container-a", {"CpuQuota": -1}) == {
        "Warnings": []
    }
    assert calls == [("/containers/container-a/update", {"CpuQuota": -1})]
    with pytest.raises(ValueError):
        device_profiles._update(Raw(), "container-a", {"Env": "secret"})


def test_remote_worker_filters_inspect_and_rejects_unknown_actions():
    engine = FakeEngine()
    requests = iter(
        [
            (json.dumps({"action": "inspect"}), "container-a"),
            (json.dumps({"action": "info"}), None),
            (json.dumps({"action": "exec"}), "container-a"),
            ("close", None),
        ]
    )
    responses = []

    class Connection:
        def recv(self):
            return next(requests)

        def send(self, value):
            responses.append(value)

        def close(self):
            pass

    device_profiles._profile_worker(
        Connection(), "ssh://unused", factory=lambda _uri: engine
    )
    assert responses[0][0] is True
    assert "must-not-be-persisted" not in json.dumps(responses)
    assert responses[2] == (False, "ValueError")
    assert engine.closed


def test_remote_adapter_uses_separate_worker_and_bounded_rpc(monkeypatch):
    calls = []

    class Remote:
        closed = False

        def __init__(self, uri, *, worker_target):
            assert worker_target is device_profiles._profile_worker
            calls.append(("connect", uri))

        def _request(self, operation, cid):
            calls.append((json.loads(operation), cid))
            raise TimeoutError("bounded remote request failed")

        def close(self):
            self.closed = True

    monkeypatch.setattr(device_profiles, "RemoteDockerClient", Remote)
    adapter = device_profiles.ProfileDockerClient("ssh://user@remote")
    with pytest.raises(TimeoutError):
        adapter.update_limits("container-a", {"CpuQuota": 50_000})
    assert calls[-1] == (
        {"action": "update", "limits": {"CpuQuota": 50_000}},
        "container-a",
    )
    adapter.close()
    assert adapter.closed
