"""Read-only baseline drift checks and explicitly persistent setup with fake APIs."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

GNS3 = Path(__file__).resolve().parents[1] / "scripts/gns3"
sys.path.insert(0, str(GNS3))

from device_profiles import DeviceProfileBackend  # noqa: E402
from resource_baseline import configure_baseline, differences  # noqa: E402

MIB = 1024 * 1024
DESIRED = {"cpus": 0.5, "memory_bytes": 64 * MIB, "memory_swap_bytes": 128 * MIB}


@pytest.fixture
def lab():
    nodes = [
        {
            "name": name,
            "node_id": f"node-{name}",
            "compute_id": "remote",
            "node_type": "docker",
            "status": "started",
            "properties": {
                "image": "custom/controller:dev",
                "container_id": f"cid-{name}",
            },
        }
        for name in ("A", "B")
    ]
    limits = {
        node["properties"]["container_id"]: {
            "CpuPeriod": 0,
            "CpuQuota": 0,
            "NanoCpus": 0,
            "Memory": 0,
            "MemorySwap": 0,
            "MemoryReservation": 0,
        }
        for node in nodes
    }

    class Engine:
        def __init__(self):
            self.updates = []
            self.closed = False
            self.ignore = False

        def inspect_container(self, cid):
            return {"Id": cid, "HostConfig": limits[cid].copy()}

        def info(self):
            return {
                "OSType": "linux",
                "MemoryLimit": True,
                "CpuCfsPeriod": True,
                "CpuCfsQuota": True,
            }

        def update_limits(self, cid, fields):
            self.updates.append((cid, fields.copy()))
            if not self.ignore:
                limits[cid].update(fields)
            return {"Warnings": []}

        def close(self):
            self.closed = True

    engine = Engine()
    baseline = {
        "path": "selected/resource-baseline.yaml",
        "sha256": "a" * 64,
        "targets": [
            {
                "name": node["name"],
                "image": node["properties"]["image"],
                "settings": DESIRED.copy(),
            }
            for node in nodes
        ],
        "unsupported": [{"name": "Router", "reason": "QEMU is not a Docker node"}],
    }
    client = SimpleNamespace(project_nodes=lambda _project: nodes)

    def backend(hosts):
        return DeviceProfileBackend(hosts, client_factory=lambda _uri: engine)

    def run(**options):
        return configure_baseline(
            client,
            "project",
            baseline,
            {"remote": "ssh://operator@compute"},
            backend_factory=backend,
            **options,
        )

    return SimpleNamespace(
        nodes=nodes,
        limits=limits,
        engine=engine,
        baseline=baseline,
        client=client,
        backend=backend,
        run=run,
    )


def test_check_reports_unlimited_drift_without_writes(lab):
    report = lab.run()
    assert report["mode"] == "check"
    assert report["status"] == "drift"
    assert report["nodes"]["A"]["differences"] == {
        "cpus": {"expected": 0.5, "observed": None},
        "memory_bytes": {"expected": 64 * MIB, "observed": 0},
        "memory_swap_bytes": {"expected": 128 * MIB, "observed": 0},
    }
    assert report["unsupported"] == lab.baseline["unsupported"]
    assert lab.engine.updates == []
    assert lab.engine.closed


def test_explicit_apply_initializes_unlimited_ram_and_is_idempotent(lab):
    report = lab.run(apply=True)
    assert report["status"] == "matched"
    assert report["nodes"]["A"]["status"] == "applied"
    assert report["nodes"]["A"]["before"]["mem_limit"] == 0
    assert lab.engine.updates[0] == (
        "cid-A",
        {
            "CpuPeriod": 100_000,
            "CpuQuota": 50_000,
            "Memory": 64 * MIB,
            "MemorySwap": 128 * MIB,
        },
    )
    assert len(lab.engine.updates) == 2
    second = lab.run(apply=True)
    assert all(node["status"] == "matched" for node in second["nodes"].values())
    assert len(lab.engine.updates) == 2


def test_temporary_profile_restores_established_baseline(lab):
    assert lab.run(apply=True)["status"] == "matched"
    backend = lab.backend({"remote": "ssh://operator@compute"})
    try:
        snapshot = backend.inspect(lab.nodes[0])
        temporary = {"cpus": 0.1, "memory_bytes": 16 * MIB}
        snapshot["managed_fields"] = list(backend.prepare(snapshot, temporary))
        backend.apply(lab.nodes[0], temporary)
        backend.restore(lab.nodes[0], snapshot)
    finally:
        backend.close()
    assert lab.run()["status"] == "matched"


def test_check_accepts_equivalent_cpu_period_and_quantized_allowances():
    limits = {
        "nano_cpus": 0,
        "cpu_period": 200_000,
        "cpu_quota": 100_000,
        "mem_limit": 64 * MIB,
        "memswap_limit": 128 * MIB,
    }
    assert differences(limits, DESIRED) == {}
    limits.update(cpu_period=100_000, cpu_quota=33_333)
    assert differences(limits, {**DESIRED, "cpus": 0.333333}) == {}
    limits["nano_cpus"] = 500_000_000
    assert differences(limits, DESIRED) == {}


@pytest.mark.parametrize(
    "bad",
    [
        {"memory_swap_bytes": 16 * MIB},
    ],
)
def test_invalid_baseline_limits_fail_before_write(lab, bad):
    backend = lab.backend({"remote": "ssh://operator@compute"})
    try:
        snapshot = backend.inspect(lab.nodes[0])
        with pytest.raises(ValueError):
            backend.prepare_baseline(snapshot, {**DESIRED, **bad})
    finally:
        backend.close()
    assert not lab.engine.updates


def test_explicit_unlimited_swap_is_supported(lab):
    for target in lab.baseline["targets"]:
        target["settings"]["memory_swap_bytes"] = -1
    assert lab.run(apply=True)["status"] == "matched"
    assert lab.limits["cid-A"]["MemorySwap"] == -1


def test_reservation_and_unsupported_capability_are_not_overridden(lab):
    lab.limits["cid-A"]["MemoryReservation"] = 512 * MIB
    report = lab.run(apply=True)
    assert report["status"] == "failed"
    assert report["nodes"]["A"]["status"] == "unavailable"
    assert report["nodes"]["B"]["status"] == "applied"
    assert [cid for cid, _fields in lab.engine.updates] == ["cid-B"]


@pytest.mark.parametrize("problem", ["missing", "duplicate", "image"])
def test_invalid_runtime_identity_leaves_other_targets_available(lab, problem):
    if problem == "missing":
        lab.nodes.pop(0)
    elif problem == "duplicate":
        lab.nodes.append(copy.deepcopy(lab.nodes[0]))
    elif problem == "image":
        lab.nodes[0]["properties"]["image"] = "foreign/image"
    report = lab.run(apply=True)
    assert report["status"] == "failed"
    assert report["nodes"]["A"]["status"] == "unavailable"
    assert report["nodes"]["B"]["status"] == "applied"
    assert [cid for cid, _fields in lab.engine.updates] == ["cid-B"]


def test_readback_failures_are_reported_not_successful(lab):
    lab.engine.ignore = True
    report = lab.run(apply=True)
    assert report["status"] == "failed"
    assert report["nodes"]["A"]["status"] == "unavailable"
    assert report["nodes"]["A"]["observed"] is None
    assert report["failures"]
    assert lab.engine.closed


def test_interruption_propagates_after_closing_connections(lab):
    def interrupted(_cid):
        raise KeyboardInterrupt

    lab.engine.inspect_container = interrupted
    with pytest.raises(KeyboardInterrupt):
        lab.run(apply=True)
    assert lab.engine.closed


def test_no_docker_targets_is_not_success(lab):
    lab.baseline["targets"] = []
    assert lab.run()["status"] == "failed"
