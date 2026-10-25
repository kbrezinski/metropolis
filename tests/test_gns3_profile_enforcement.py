"""Profile transactions use real port mapping and rollback every attempted write."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, FormatChecker

GNS3 = Path(__file__).resolve().parents[1] / "scripts/gns3"
sys.path.insert(0, str(GNS3))

from profile_enforcement import ProfileSession  # noqa: E402
from topology import PlannedLink, PlannedPort  # noqa: E402


@pytest.fixture
def profile_lab(tmp_path):
    events = []
    nodes = [
        {
            "node_id": "device-id",
            "name": "PLC",
            "node_type": "docker",
            "status": "started",
            "compute_id": "remote",
            "properties": {"container_id": "cid-device"},
        },
        {
            "node_id": "switch-id",
            "name": "Switch",
            "node_type": "docker",
            "status": "started",
            "compute_id": "remote",
            "properties": {
                "container_id": "cid-switch",
                "image": "metropolis/openvswitch:dev",
            },
        },
    ]
    endpoints = [
        {"node_id": "device-id", "adapter_number": 0, "port_number": 0},
        {"node_id": "switch-id", "adapter_number": 2, "port_number": 0},
    ]
    actual_link = {
        "link_id": "gns3-real-id",
        "nodes": endpoints,
        "filters": {"frequency_drop": [99]},
    }

    class Client:
        def project_nodes(self, _project):
            return nodes

        def project_links(self, _project):
            return [actual_link]

        def link(self, _project, link_id):
            assert link_id == "gns3-real-id"
            return copy.deepcopy(actual_link)

        def set_link_filters(self, _project, link_id, filters):
            assert link_id == "gns3-real-id"
            events.append(("filters", filters.copy()))
            actual_link["filters"] = copy.deepcopy(filters)

        def node(self, _project, node_id):
            return next((node for node in nodes if node["node_id"] == node_id), None)

    class Devices:
        def __init__(self, _hosts):
            self.fail_apply = False
            self.fail_restore = False

        def inspect(self, node):
            return {"node_id": node["node_id"], "limits": {"mem_limit": 512 << 20}}

        def prepare(self, snapshot, settings):
            if settings.get("memory_bytes", 0) and snapshot["limits"]["mem_limit"] == 0:
                raise ValueError("finite baseline required")

        def apply(self, node, settings):
            saved = json.loads((tmp_path / "profiles.json").read_text())
            assert saved["profile"]["devices"][0]["attempted"]
            events.append(("device_apply", node["node_id"], settings))
            if self.fail_apply:
                raise RuntimeError("write succeeded but reply lost")
            return {"verified": True}

        def restore(self, node, _snapshot):
            events.append(("device_restore", node["node_id"]))
            if self.fail_restore:
                raise RuntimeError("device unavailable")
            return {"restoration_notes": ["baseline restored"]}

        def close(self):
            events.append("device_closed")

    class Bandwidth:
        def __init__(self, _hosts, _run):
            self.fail_restore = False

        def inspect(self, node, adapter):
            assert (node["node_id"], adapter) == ("switch-id", 2)
            return {"interface": "eth2", "qos": None}

        def apply(self, node, adapter, rate):
            assert node["status"] == "started"
            assert rate == 100
            events.append(("bandwidth_apply", adapter))
            return {"verified": True}

        def restore(self, _node, adapter, _snapshot):
            events.append(("bandwidth_restore", adapter))
            if self.fail_restore:
                raise RuntimeError("bandwidth restore failed")
            return {"verified": True}

        def close(self):
            events.append("bandwidth_closed")

    client = Client()
    profile = {
        "name": "constrained",
        "path": "selected.yaml",
        "sha256": "a" * 64,
        "devices": [{"node": "PLC", "cpus": 0.25, "memory_bytes": 128 << 20}],
        "links": [
            {
                "link": "declared-link",
                "bandwidth_kbps": 100,
                "delay_ms": 20,
                "jitter_ms": 2,
                "loss_percent": 1,
            }
        ],
    }
    topology = SimpleNamespace(
        links=[
            PlannedLink(
                "declared-link",
                "access",
                PlannedPort("PLC", 0),
                PlannedPort("Switch", 2),
            )
        ]
    )

    def session():
        return ProfileSession(
            client,
            "project",
            topology,
            profile,
            tmp_path,
            "run-a",
            {"remote": "ssh://user@host"},
            device_backend_factory=Devices,
            bandwidth_backend_factory=Bandwidth,
        )

    return SimpleNamespace(
        create=session,
        events=events,
        nodes=nodes,
        link=actual_link,
        client=client,
        profile=profile,
        devices=Devices,
        bandwidth=Bandwidth,
        path=tmp_path,
    )


def test_preflight_snapshots_all_targets_before_changes_and_restores_baseline(
    profile_lab,
):
    lab = profile_lab
    session = lab.create()
    session.prepare()
    assert lab.events == []
    assert session.result["links"][0]["gns3_link_id"] == "gns3-real-id"
    session.apply()
    assert lab.link["filters"] == {
        "frequency_drop": [99],
        "delay": [20, 2],
        "packet_loss": [1],
    }
    session.restore()
    session.close()
    assert lab.link["filters"] == {"frequency_drop": [99]}
    assert session.result["restored"]
    assert session.result["status"] == "restored"
    assert session.result["devices"][0]["restored_values"]["restoration_notes"]
    assert ("bandwidth_restore", 2) in lab.events
    assert ("device_restore", "device-id") in lab.events
    assert "bandwidth_closed" in lab.events
    schema = json.loads(
        (GNS3.parents[1] / "schemas/experiment-run.schema.json").read_text()
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
        {
            "schema_version": "1.0",
            "run_id": "run-a",
            "project_id": "project",
            "started_at_utc": "2026-10-26T01:00:00Z",
            "status": "complete",
            "logs": {"normalized_path": "logs/devices.jsonl", "sources": {}},
            "failures": [],
            "profiles": session.result,
        }
    )


def test_partial_device_apply_is_in_rollback_set(profile_lab):
    session = profile_lab.create()
    session.prepare()
    session.device_backend.fail_apply = True
    with pytest.raises(RuntimeError, match="reply lost"):
        session.apply()
    session.restore()
    session.close()
    assert ("device_restore", "device-id") in profile_lab.events
    assert session.result["restored"]
    assert session.result["status"] == "failed"


def test_bad_final_target_prevents_all_profile_writes(profile_lab):
    profile_lab.nodes[1]["properties"]["image"] = "foreign/switch"
    session = profile_lab.create()
    with pytest.raises(ValueError, match="Open vSwitch"):
        session.prepare()
    session.restore()
    session.close()
    assert not any(isinstance(item, tuple) for item in profile_lab.events)


def test_wrong_adapter_is_not_an_equivalent_link(profile_lab):
    profile_lab.link["nodes"][1]["adapter_number"] = 3
    session = profile_lab.create()
    with pytest.raises(ValueError, match="miswired"):
        session.prepare()
    session.close()
    assert not any(isinstance(item, tuple) for item in profile_lab.events)


def test_checkpoint_failure_does_not_prevent_any_rollback(profile_lab):
    session = profile_lab.create()
    session.prepare()
    session.apply()

    def disk_full():
        raise OSError("disk full")

    session._write = disk_full
    session.restore()
    session.close()
    assert profile_lab.link["filters"] == {"frequency_drop": [99]}
    assert ("bandwidth_restore", 2) in profile_lab.events
    assert ("device_restore", "device-id") in profile_lab.events
    assert session.result["status"] == "failed"
    assert any(item["source"] == "checkpoint" for item in session.result["failures"])


def test_failed_bandwidth_restore_does_not_skip_filter_or_device_restore(profile_lab):
    session = profile_lab.create()
    session.prepare()
    session.apply()
    session.bandwidth_backend.fail_restore = True
    session.restore()
    session.close()
    assert profile_lab.link["filters"] == {"frequency_drop": [99]}
    assert ("device_restore", "device-id") in profile_lab.events
    assert session.result["restored"] is False


def test_replaced_container_is_not_modified_by_cleanup(profile_lab):
    session = profile_lab.create()
    session.prepare()
    session.apply()
    profile_lab.nodes[0]["properties"]["container_id"] = "replacement"
    session.restore()
    session.close()
    assert ("device_restore", "device-id") not in profile_lab.events
    assert session.result["restored"] is False


def test_recovery_retries_original_baseline_without_applying_again(profile_lab):
    session = profile_lab.create()
    session.prepare()
    session.apply()
    session.close()
    profile_lab.events.clear()
    result = ProfileSession.recover(
        profile_lab.client,
        profile_lab.path / "profiles.json",
        {"remote": "ssh://user@host"},
        device_backend_factory=profile_lab.devices,
        bandwidth_backend_factory=profile_lab.bandwidth,
    )
    assert result["restored"]
    assert not any(
        isinstance(item, tuple) and item[0].endswith("apply")
        for item in profile_lab.events
    )


def test_observations_do_not_claim_achieved_limits_from_passive_samples(profile_lab):
    session = profile_lab.create()
    session.prepare()
    session.apply()
    session.restore()
    session.observe(None)
    session.close()
    assert session.result["observations"]["achieved_verified"] is False


def test_pressure_observations_use_only_active_profile_window(profile_lab):
    session = profile_lab.create()
    session.prepare()
    session.apply()
    session.restore()
    session.state["applied_at_utc"] = "2026-10-26T01:00:00Z"
    session.state["restoring_at_utc"] = "2026-10-26T01:00:10Z"
    folder = profile_lab.path / "metrics"
    folder.mkdir()
    (folder / "computes.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "timestamp": timestamp,
                    "compute_id": "remote",
                    "cpu_percent": cpu,
                    "memory_percent": 20,
                }
            )
            for timestamp, cpu in [
                ("2026-10-26T00:59:00Z", 99),
                ("2026-10-26T01:00:05Z", 10),
            ]
        )
    )
    session.observe(None)
    session.close()
    assert session.result["observations"]["computes"]["remote"] == {
        "peak_cpu_percent": 10,
        "peak_memory_percent": 20,
        "possible_pressure": False,
    }
