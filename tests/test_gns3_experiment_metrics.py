"""Run-level resource cleanup, reporting, and CLI behavior without live services."""

from __future__ import annotations

import json
import hashlib
import itertools
import struct
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, FormatChecker

GNS3 = Path(__file__).resolve().parents[1] / "scripts" / "gns3"
sys.path.insert(0, str(GNS3))

import experiment  # noqa: E402
import experiment_run  # noqa: E402
from capture import CaptureResult, LinkResolution  # noqa: E402
from device_logs import utc_now  # noqa: E402
from metrics_report import finalize_metrics  # noqa: E402
from resource_metrics import ResourceCollector  # noqa: E402


@pytest.fixture
def run_fixture(tmp_path, monkeypatch):
    events = []
    ready = threading.Event()

    class Logs:
        def __init__(self, _client, _project, run_dir, _run_id, _hosts, **_kwargs):
            self.run_dir = run_dir
            run_dir.mkdir(parents=True)
            self.manifest = {
                "schema_version": "1.0",
                "run_id": _run_id,
                "project_id": _project,
                "started_at_utc": utc_now(),
                "status": "running",
                "logs": {"normalized_path": "logs/devices.jsonl", "sources": {}},
                "failures": [],
            }

        def run(self, _duration, *, stop_event):
            stop_event.wait(5)
            events.append("logs_stopped")
            self.manifest["status"] = "complete"

        def _write_manifest(self):
            (self.run_dir / "manifest.json").write_text(json.dumps(self.manifest))

    class Resources:
        def __init__(self, _client, _project, _dir, _id, hosts, *, interval):
            events.append(("resources_created", hosts, interval))
            self.result = {
                "status": "running",
                "container_samples_path": "metrics/containers.jsonl",
                "compute_samples_path": "metrics/computes.jsonl",
                "sources": {},
                "computes": {},
                "failures": [],
            }

        def run(self, stop_event):
            events.append("resources_started")
            ready.set()
            stop_event.wait(5)
            events.append("resources_stopped")
            self.result["status"] = "complete"

        def snapshot(self):
            return self.result.copy()

    resolution = LinkResolution(
        resolved={"link-a": "physical-a"},
        status={"link-a": {"status": "built"}},
        declared_ids={"link-a"},
    )
    monkeypatch.setattr(experiment, "resolve_links", lambda *_args: resolution)
    monkeypatch.setattr(experiment, "coverage_report", lambda *_args: {})

    def capture(
        _client, _project, _resolution, chosen, _duration, output, *_args, **kw
    ):
        events.append("capture")
        assert chosen == ["link-a"]
        directory = output / kw["run_id"]
        directory.mkdir(parents=True)
        manifest = directory / "manifest.json"
        manifest.write_text(
            json.dumps(
                {"captures": {}, "link_status": resolution.status, "failures": []}
            )
        )
        return CaptureResult(manifest_path=manifest)

    def report(run_dir, run_id, resources, capture_manifest, capture_path, *, interval):
        assert "resources_stopped" in events or resources["failures"]
        assert "logs_stopped" in events
        assert resources["status"] in {"complete", "failed"}
        assert run_id == "run_001"
        events.append("report")
        return {
            "enabled": True,
            "status": "failed" if resources["failures"] else "complete",
            "sample_interval_seconds": interval,
            "resource": resources,
            "failures": resources["failures"],
        }

    def run(**overrides):
        gns3_client = overrides.pop("client", object())
        options = {
            "collector_factory": Logs,
            "resource_collector_factory": Resources,
            "capture_fn": capture,
            "metrics_report_fn": report,
        }
        options.update(overrides)
        return experiment.run_experiment(
            gns3_client,
            SimpleNamespace(project_id="project-a", status="opened"),
            SimpleNamespace(nodes=[]),
            {"devices": []},
            {"routers": []},
            {"links": [{"id": "link-a"}]},
            tmp_path,
            "run_001",
            0.01,
            {"compute-a": "ssh://user@compute.example"},
            **options,
        )

    return SimpleNamespace(
        run=run, events=events, ready=ready, path=tmp_path / "runs/run_001"
    )


@pytest.mark.parametrize("failure", [None, KeyboardInterrupt])
def test_metrics_start_before_lab_and_stop_after_success_error_or_interrupt(
    run_fixture, monkeypatch, failure
):
    state = run_fixture

    def start(*_args, **_kwargs):
        assert state.ready.wait(1)
        assert "resources_started" in state.events
        state.events.append("lab_started")
        if failure:
            raise failure("startup interrupted")
        return SimpleNamespace(started=[], failed=[])

    monkeypatch.setattr(experiment, "start_all", start)
    manifest = state.run(start_lab=True)
    assert "resources_stopped" in state.events
    assert "logs_stopped" in state.events
    assert "report" in state.events
    assert state.events.index("resources_stopped") < state.events.index("report")
    assert manifest["metrics"]["enabled"] is True
    assert manifest["status"] == (
        "interrupted"
        if failure is KeyboardInterrupt
        else "failed"
        if failure
        else "complete"
    )
    assert json.loads((state.path / "manifest.json").read_text()) == manifest


def test_disabling_metrics_skips_collector_and_report(run_fixture):
    def unwanted(*_args, **_kwargs):
        raise AssertionError("disabled metrics invoked")

    manifest = run_fixture.run(
        metrics_enabled=False,
        resource_collector_factory=unwanted,
        metrics_report_fn=unwanted,
    )
    assert manifest["status"] == "complete"
    assert manifest["metrics"] == {
        "enabled": False,
        "status": "disabled",
        "sample_interval_seconds": 5.0,
    }


def test_resource_setup_failure_still_captures_and_is_reported(run_fixture):
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("metrics unavailable")

    manifest = run_fixture.run(resource_collector_factory=unavailable)
    assert "capture" in run_fixture.events
    assert manifest["status"] == "failed"
    assert manifest["metrics"]["status"] == "failed"
    assert any("metrics unavailable" in item["reason"] for item in manifest["failures"])


def test_report_failure_preserves_capture_metadata_and_stops_collectors(run_fixture):
    def broken_report(*_args, **_kwargs):
        assert "resources_stopped" in run_fixture.events
        raise RuntimeError("chart writer failed")

    manifest = run_fixture.run(metrics_report_fn=broken_report)
    assert manifest["capture"]["manifest_path"]
    assert manifest["status"] == "failed"
    assert manifest["metrics"]["resource"]["status"] == "complete"
    assert any("chart writer failed" in item["reason"] for item in manifest["failures"])


@pytest.mark.parametrize("failure", [None, KeyboardInterrupt])
def test_selected_profile_restored_before_collectors_stop(run_fixture, failure):
    events = run_fixture.events

    class Profile:
        def __init__(self, *_args):
            self.result = {
                "enabled": True,
                "name": "example",
                "status": "planned",
                "restored": False,
                "failures": [],
            }

        def prepare(self):
            events.append("profile_prepared")

        def apply(self):
            events.append("profile_applied")
            if failure:
                raise failure("partial profile apply")

        def restore(self):
            events.append("profile_restored")
            self.result["restored"] = True

        def close(self):
            events.append("profile_closed")

        def observe(self, _metrics):
            events.append("profile_observed")

    manifest = run_fixture.run(
        profile={"name": "example"}, profile_session_factory=Profile
    )
    assert manifest["profiles"]["restored"]
    assert events.index("profile_restored") < events.index("resources_stopped")
    assert events.index("profile_closed") < events.index("logs_stopped")
    assert events.index("profile_observed") > events.index("report")
    assert manifest["status"] == (
        "interrupted"
        if failure is KeyboardInterrupt
        else "failed"
        if failure
        else "complete"
    )


def test_default_run_does_not_construct_profile_enforcement(run_fixture):
    def unwanted(*_args):
        pytest.fail("default run tried to enforce a profile")

    manifest = run_fixture.run(profile_session_factory=unwanted)
    assert "profiles" not in manifest


def test_optional_baseline_check_is_read_only_and_does_not_block_capture(
    run_fixture,
):
    baseline = {
        "path": "baseline.yaml",
        "sha256": "a" * 64,
        "targets": [],
        "unsupported": [],
    }

    def check(_client, _project, selected, _hosts, *, apply):
        assert apply is False
        assert selected is baseline
        run_fixture.events.append("baseline_checked")
        return {
            "path": selected["path"],
            "sha256": selected["sha256"],
            "mode": "check",
            "status": "failed",
            "nodes": {},
            "unsupported": [],
            "failures": [],
        }

    manifest = run_fixture.run(baseline=baseline, baseline_check_fn=check)
    assert manifest["status"] == "complete"
    assert manifest["resource_baseline"]["status"] == "failed"
    schema = json.loads(
        (GNS3.parents[1] / "schemas/experiment-run.schema.json").read_text()
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    assert run_fixture.events.index("baseline_checked") < run_fixture.events.index(
        "capture"
    )


def test_cli_rejects_unknown_profile_targets_before_connecting(tmp_path, monkeypatch):
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        'schema_version: "1.0"\nname: missing\ndevices:\n  - node: absent\n    cpus: 0.25\n'
    )
    monkeypatch.setattr(sys, "argv", ["experiment_run", "--profile", str(profile)])
    monkeypatch.setattr(
        experiment_run,
        "load_documents",
        lambda _path: ({}, {"devices": []}, {}, {"links": []}),
    )
    monkeypatch.setattr(
        experiment_run,
        "connect",
        lambda _args: pytest.fail("connected before validation"),
    )
    with pytest.raises(SystemExit) as exc:
        experiment_run.main()
    assert exc.value.code == 2


@pytest.mark.parametrize("value", ["0"])
def test_cli_rejects_invalid_metric_intervals_before_connecting(
    monkeypatch, capsys, value
):
    monkeypatch.setattr(sys, "argv", ["experiment_run", "--metrics-interval", value])
    monkeypatch.setattr(
        experiment_run, "connect", lambda _args: pytest.fail("unexpected connection")
    )
    with pytest.raises(SystemExit) as exc:
        experiment_run.main()
    assert exc.value.code == 2
    assert "--metrics-interval must be positive and finite" in capsys.readouterr().err


@pytest.mark.parametrize("disabled,baseline_present", [(False, False)])
def test_cli_passes_metric_settings_to_run(
    tmp_path, monkeypatch, disabled, baseline_present
):
    dataset = tmp_path / "dataset"
    for relative in (
        "topology/address-plan.yaml",
        "topology/links.yaml",
        "topology/firewall-policy.yaml",
        "device_instances/initial_devices.yaml",
        "device_instances/infrastructure.yaml",
    ):
        target = dataset / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
    if baseline_present:
        (dataset / "topology/resource-baseline.yaml").write_text(
            'schema_version: "1.0"\ndefaults:\n'
            "  cpus: 1\n  memory_bytes: 536870912\n"
            "  memory_swap_bytes: 536870912\n"
        )
    args = [
        "experiment_run",
        "--dataset",
        str(dataset),
        "--run-id",
        "cli-run",
        "--metrics-interval",
        "2.5",
    ]
    if disabled:
        args.append("--no-metrics")
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(
        experiment_run,
        "load_documents",
        lambda _path: ({"testbed_id": "custom"}, {}, {}, {"links": []}),
    )
    monkeypatch.setattr(
        experiment_run,
        "compile_policy",
        lambda *_args: ({}, {"attack_exceptions": []}),
    )
    project = SimpleNamespace(project_id="project-a", name="custom-project")
    monkeypatch.setattr(
        experiment_run,
        "connect",
        lambda _args: SimpleNamespace(project_by_name=lambda _name: project),
    )
    monkeypatch.setattr(experiment_run, "build_plan", lambda *_args: object())

    def run(*_args, **kwargs):
        assert kwargs["metrics_enabled"] is (not disabled)
        assert kwargs["metrics_interval"] == 2.5
        assert (kwargs["baseline"] is not None) is baseline_present
        if baseline_present:
            assert kwargs["baseline"]["path"] == str(
                (dataset / "topology/resource-baseline.yaml").resolve()
            )
            assert len(kwargs["baseline"]["sha256"]) == 64
        return {"status": "complete", "captures": [], "failures": []}

    monkeypatch.setattr(experiment_run, "run_experiment", run)
    assert experiment_run.main() == 0


def test_real_collector_and_reports_form_a_schema_valid_run(run_fixture):
    """Exercise the full run pipeline with actual files and fake remote APIs."""
    container = {
        "node_id": "device-a",
        "name": "Controller A",
        "node_type": "docker",
        "compute_id": "compute-a",
        "status": "started",
        "properties": {"container_id": "container-a"},
    }
    router = {
        **container,
        "node_id": "router-a",
        "name": "Router A",
        "node_type": "qemu",
        "properties": {},
    }
    client = SimpleNamespace(
        project_nodes=lambda _project: [container, router],
        computes=lambda: [
            {
                "compute_id": "compute-a",
                "name": "Compute A",
                "connected": True,
                "cpu_usage_percent": 30,
                "memory_usage_percent": 40,
            }
        ],
    )
    counter = itertools.count(1)

    class Engine:
        def info(self):
            return {"NCPU": 4, "MemTotal": 8 << 30}

        def inspect_container(self, _cid):
            return {"HostConfig": {"Memory": 0}, "State": {"OOMKilled": False}}

        def stats(self, _cid, **_kwargs):
            sample = next(counter)
            return {
                "cpu_stats": {
                    "cpu_usage": {"total_usage": sample * 10},
                    "system_cpu_usage": sample * 100,
                    "online_cpus": 4,
                },
                "memory_stats": {
                    "usage": 300,
                    "limit": 4096,
                    "stats": {"inactive_file": 50},
                },
            }

        def close(self):
            pass

    resources = []

    def factory(*args, **kwargs):
        collector = ResourceCollector(
            *args, **kwargs, docker_client_factory=lambda _uri: Engine()
        )
        resources.append(collector)
        return collector

    def capture(*args, **kwargs):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            source = resources[0].snapshot()["sources"].get("device-a", {})
            if source.get("samples", 0) >= 2:
                break
            time.sleep(0.01)
        assert source.get("samples", 0) >= 2
        directory = args[5] / kwargs["run_id"]
        directory.mkdir(parents=True)
        pcap = directory / "link-a.pcap"
        second = int(time.time())
        payload = struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
        payload += struct.pack("<IIII", second, 500_000, 4, 64) + b"data"
        pcap.write_bytes(payload)

        def timestamp(value):
            return datetime.fromtimestamp(value, timezone.utc).isoformat()

        manifest = directory / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "run_id": kwargs["run_id"],
                    "link_status": {"link-a": {"status": "built"}},
                    "captures": {
                        "link-a": {
                            "declared_link_id": "link-a",
                            "gns3_link_id": "physical-a",
                            "local_file": str(pcap),
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "started_at_utc": timestamp(second),
                            "stopped_at_utc": timestamp(second + 1),
                        }
                    },
                }
            )
        )
        return CaptureResult(manifest_path=manifest, files={"link-a": str(pcap)})

    manifest = run_fixture.run(
        client=client,
        resource_collector_factory=factory,
        metrics_report_fn=finalize_metrics,
        metrics_interval=0.01,
        capture_fn=capture,
    )
    assert manifest["status"] == "complete"
    assert resources[0].closed
    metrics = manifest["metrics"]
    assert metrics["resource"]["sources"]["router-a"]["status"] == "unsupported"
    assert metrics["traffic"]["links"]["link-a"]["bytes"] == 64
    assert len(metrics["charts"]) == 3
    assert all((run_fixture.path / file).is_file() for file in metrics["charts"])
    schemas = Path(__file__).resolve().parents[1] / "schemas"
    validator = Draft202012Validator(
        json.loads((schemas / "experiment-run.schema.json").read_text()),
        format_checker=FormatChecker(),
    )
    validator.validate(manifest)
    sample_validator = Draft202012Validator(
        json.loads((schemas / "resource-metric.schema.json").read_text()),
        format_checker=FormatChecker(),
    )
    for filename in ("containers.jsonl", "computes.jsonl"):
        for line in (run_fixture.path / "metrics" / filename).read_text().splitlines():
            sample_validator.validate(json.loads(line))
