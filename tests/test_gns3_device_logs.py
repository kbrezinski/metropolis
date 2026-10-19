"""Focused tests for GNS3 Docker log attribution and durable run output."""

from __future__ import annotations

import io
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

GNS3 = Path(__file__).resolve().parents[1] / "scripts" / "gns3"
sys.path.insert(0, str(GNS3))

from collect_device_logs import docker_host_mapping  # noqa: E402
import device_logs  # noqa: E402
from device_logs import DeviceLogCollector, normalize_line, redact  # noqa: E402


class FakeClient:
    def __init__(self, nodes):
        self.nodes = nodes

    def project_nodes(self, project_id):
        assert project_id == "project-1"
        return self.nodes


class FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes, *, exit_code=None):
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = exit_code
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode


def node(*, compute="remote-1", status="started", container="container-123"):
    return {
        "node_id": "node-12345678",
        "name": "MET-SCADA-01",
        "node_type": "docker",
        "status": status,
        "compute_id": compute,
        "properties": {"container_id": container} if container else {},
    }


def test_json_and_plain_text_preserve_device_fields_and_collector_time():
    structured = normalize_line(
        '{"timestamp":"2026-10-20T03:15:12Z","level":"info",'
        '"event":"poll_completed","message":"Read intake", "duration_ms":18}',
        run_id="run-1",
        source="MET-SCADA-01",
        component="scada",
        stream="stdout",
    )
    plain = normalize_line(
        "unparseable {",
        run_id="run-1",
        source="MET-SCADA-01",
        component="scada",
        stream="stderr",
    )
    assert structured["attributes"]["duration_ms"] == 18
    assert structured["device_timestamp"] == "2026-10-20T03:15:12Z"
    assert structured["event"] == "poll_completed"
    assert structured["level"] == "INFO"
    assert plain["message"] == plain["raw"] == "unparseable {"
    assert plain["stream"] == "stderr"
    assert structured["run_id"] == plain["run_id"] == "run-1"
    for record in (structured, plain):
        assert datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")).tzinfo


def test_redaction_preserves_json_and_hides_pem_material():
    safe, changed, _ = redact('{"password":"secret", "message":"ready"}')
    assert changed and json.loads(safe)["password"] == "[REDACTED]"
    first, _, inside = redact("-----BEGIN PRIVATE KEY-----")
    middle, _, inside = redact("secret key bytes", in_private_key=inside)
    last, _, inside = redact("-----END PRIVATE KEY-----", in_private_key=inside)
    assert first == middle == last == "[REDACTED PRIVATE KEY]"
    assert not inside


def test_incremental_streaming_and_remote_compute_attribution(tmp_path):
    payload = (
        b'2026-10-20T03:15:12.482Z {"event":"ready","message":"started"}\n'
        b"2026-10-20T03:15:13Z malformed {\n"
        b"2026-10-20T03:15:14Z     continuation line\n"
    )
    process = FakeProcess(payload, b"2026-10-20T03:15:15Z warning\n")
    commands = []

    def start(command, **kwargs):
        commands.append(command)
        return process

    collector = DeviceLogCollector(
        FakeClient([node()]),
        "project-1",
        tmp_path / "run-1",
        "run-1",
        {"remote-1": "ssh://gns3@compute.example"},
        popen=start,
    )
    collector.discover()
    for thread in collector.threads:
        thread.join(timeout=2)
    events_path = tmp_path / "run-1/logs/devices.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert len(events) == 4  # Already durable while collection is still running.
    assert all(event["source"] == "MET-SCADA-01" for event in events)
    assert {event["stream"] for event in events} == {"stdout", "stderr"}
    assert any(
        event.get("device_timestamp") == "2026-10-20T03:15:12.482Z" for event in events
    )
    assert any(event["message"] == "malformed {" for event in events)
    assert any(event["message"] == "    continuation line" for event in events)
    assert commands[0][1:3] == ["--host", "ssh://gns3@compute.example"]
    assert commands[0][-1] == "container-123"
    manifest = collector.run(0.01, poll_interval=0.001)
    assert process.terminated
    assert manifest["status"] == "complete"
    assert manifest["logs"]["sources"]["MET-SCADA-01"]["lines"] == 4
    raw_path = (
        tmp_path / "run-1" / manifest["logs"]["sources"]["MET-SCADA-01"]["raw_path"]
    )
    assert b"malformed {\n" in raw_path.read_bytes()
    assert not (tmp_path / "run-1/timeline.jsonl").read_bytes()
    schemas = Path(__file__).resolve().parents[1] / "schemas"
    for schema_name, values in (
        ("device-log-event.schema.json", events),
        ("experiment-run.schema.json", [manifest]),
    ):
        schema = json.loads((schemas / schema_name).read_text())
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for value in values:
            validator.validate(value)


@pytest.mark.parametrize(
    ("nodes", "hosts", "expected"),
    [
        ([node()], {}, "no Docker host mapping"),
        ([node(status="stopped")], {"remote-1": "ssh://host"}, "did not start"),
        ([node(container=None)], {"remote-1": "ssh://host"}, "no container ID"),
    ],
)
def test_incomplete_sources_and_no_logs_are_failures(tmp_path, nodes, hosts, expected):
    collector = DeviceLogCollector(
        FakeClient(nodes),
        "project-1",
        tmp_path / "run",
        "run",
        hosts,
    )
    manifest = collector.run(0.01, poll_interval=0.001)
    assert manifest["status"] == "failed"
    assert any(expected in failure["reason"] for failure in manifest["failures"])
    assert (
        json.loads((tmp_path / "run/manifest.json").read_text())["status"] == "failed"
    )


def test_docker_process_failure_and_empty_output_are_reported(tmp_path):
    process = FakeProcess(b"", b"Docker daemon unavailable\n", exit_code=1)
    collector = DeviceLogCollector(
        FakeClient([node()]),
        "project-1",
        tmp_path / "run",
        "run",
        {"remote-1": "ssh://host"},
        popen=lambda *_args, **_kwargs: process,
    )
    manifest = collector.run(0.01, poll_interval=0.001)
    assert manifest["status"] == "failed"
    assert any(
        "Docker log process ended" in failure["reason"]
        for failure in manifest["failures"]
    )
    assert not manifest["logs"]["sources"]["MET-SCADA-01"]["complete"]


def test_remote_mapping_rejects_passwords_and_tcp():
    assert (
        docker_host_mapping(["remote-1=ssh://user@host"])["remote-1"]
        == "ssh://user@host"
    )
    with pytest.raises(ValueError):
        docker_host_mapping(["remote-1=ssh://user:password@host"])
    with pytest.raises(ValueError):
        docker_host_mapping(["remote-1=tcp://host:2375"])


def test_interruption_stops_followers_and_records_incomplete_run(tmp_path, monkeypatch):
    process = FakeProcess(b"2026-10-20T03:15:12Z ready\n", b"")
    collector = DeviceLogCollector(
        FakeClient([node()]),
        "project-1",
        tmp_path / "run",
        "run",
        {"remote-1": "ssh://host"},
        popen=lambda *_args, **_kwargs: process,
    )

    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(device_logs.time, "sleep", interrupt)
    manifest = collector.run(1)
    assert manifest["status"] == "interrupted"
    assert process.terminated
    assert any(failure["reason"] == "interrupted" for failure in manifest["failures"])


def test_existing_run_is_never_reused(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    with pytest.raises(FileExistsError):
        DeviceLogCollector(FakeClient([node()]), "project-1", run, "run", {})


def test_gns3_discovery_failure_is_preserved_in_manifest(tmp_path):
    class BrokenClient:
        def project_nodes(self, project_id):
            raise OSError("controller disconnected")

    collector = DeviceLogCollector(
        BrokenClient(), "project-1", tmp_path / "run", "run", {}
    )
    manifest = collector.run(0.01, poll_interval=0.005)
    assert manifest["status"] == "failed"
    assert any("controller disconnected" in failure["reason"] for failure in manifest["failures"])
    assert json.loads((tmp_path / "run/manifest.json").read_text())["failures"]
