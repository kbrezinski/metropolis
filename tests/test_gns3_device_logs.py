"""Focused tests for GNS3 Docker log attribution and durable run output."""

from __future__ import annotations

import io
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

GNS3 = Path(__file__).resolve().parents[1] / "scripts" / "gns3"
sys.path.insert(0, str(GNS3))

from collect_device_logs import docker_host_mapping  # noqa: E402
import device_logs  # noqa: E402
from device_logs import DeviceLogCollector, normalize_line, redact  # noqa: E402

NTP_SERVICE = (
    Path(__file__).resolve().parents[1] / "testbeds/metropolis/devices/services/ntp"
)
sys.path.insert(0, str(NTP_SERVICE))
from syslog_receiver import parse_syslog  # noqa: E402
import start_services as ntp_services  # noqa: E402


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


def test_ssh_auth_outcomes_and_syslog_source_attribution():
    failed = normalize_line(
        "Failed password for invalid user guest from 10.20.21.30 port 40123 ssh2",
        run_id="run-1",
        source="MET-ENGINEERING-01",
        component="engineering",
        stream="stderr",
    )
    assert failed["event"] == "ssh_auth_failed"
    assert failed["attributes"]["peer_ip"] == "10.20.21.30"
    assert failed["attributes"]["user"] == "guest"
    assert "password" not in failed["attributes"]
    router = parse_syslog(
        b"<86>Oct 20 03:15:12 MET-OT-CORE sshd[10]: Accepted publickey for vyos",
        "10.20.30.1",
    )
    event = normalize_line(
        json.dumps(router),
        run_id="run-1",
        source="MET-NTP-01",
        component="ntp",
        stream="stdout",
    )
    assert event["source"] == "MET-OT-CORE"
    assert event["stream"] == "syslog"
    assert event["component"] == "router"
    assert event["attributes"]["peer_ip"] == "10.20.30.1"
    assert event["relay_timestamp"]


def test_redaction_preserves_json_and_hides_pem_material():
    safe, changed, _ = redact('{"password":"secret", "message":"ready"}')
    assert changed and json.loads(safe)["password"] == "[REDACTED]"
    first, _, inside = redact("-----BEGIN PRIVATE KEY-----")
    middle, _, inside = redact("secret key bytes", in_private_key=inside)
    last, _, inside = redact("-----END PRIVATE KEY-----", in_private_key=inside)
    assert first == middle == last == "[REDACTED PRIVATE KEY]"
    assert not inside


@pytest.mark.parametrize("quote", ["", "'", '"'])
def test_ipsec_psk_redaction_preserves_cli_and_structured_log_fields(quote):
    synthetic_key = "lab-only-key-" + "A1_+/=-" * 5
    command = (
        "set vpn ipsec site-to-site peer 192.0.2.2 authentication "
        f"pre-shared-secret {quote}{synthetic_key}{quote}"
    )
    safe, changed, _ = redact(command)
    assert changed
    assert synthetic_key not in safe
    assert safe.endswith(f"pre-shared-secret {quote}[REDACTED]{quote}")
    payload = {
        "event": "configuration_changed",
        "message": command,
        "outcome": "success",
    }
    structured, changed, _ = redact(json.dumps(payload))
    assert changed
    decoded = json.loads(structured)
    assert decoded == {**payload, "message": safe}
    relay = parse_syslog(
        f"<86>Oct 20 03:15:12 ROUTER configuration: {command}".encode(),
        "192.0.2.1",
    )
    assert synthetic_key not in json.dumps(relay)
    assert relay["source"] == "ROUTER"
    assert relay["message"].endswith(safe)
    structured_relay = parse_syslog(
        f"<86>Oct 20 03:15:12 ROUTER {json.dumps(payload)}".encode(), "192.0.2.1"
    )
    assert json.loads(structured_relay["message"]) == decoded
    assert synthetic_key not in structured_relay["raw_syslog"]
    auth_line = "Failed password for invalid user guest from 192.0.2.3 port 40123 ssh2"
    assert redact(auth_line) == (auth_line, False, False)


def test_ipsec_psks_are_redacted_before_raw_and_normalized_writes(tmp_path):
    synthetic_key = "lab-only-key-" + "B2_+/=-" * 5
    command = (
        "set vpn ipsec site-to-site peer 192.0.2.2 authentication "
        f'pre-shared-secret "{synthetic_key}"'
    )
    record = {
        "_metropolis_syslog": True,
        "source": "ROUTER",
        "event": "configuration_changed",
        "message": command,
        "raw_syslog": command,
    }
    process = FakeProcess((json.dumps(record) + "\n").encode(), b"")
    collector = DeviceLogCollector(
        FakeClient([node()]),
        "project-1",
        tmp_path / "run",
        "run",
        {"remote-1": "ssh://host"},
        popen=lambda *_args, **_kwargs: process,
    )
    manifest = collector.run(0.01, poll_interval=0.001)
    raw_path = (
        collector.run_dir / manifest["logs"]["sources"]["MET-SCADA-01"]["raw_path"]
    )
    for path in [
        raw_path,
        collector.run_dir / "logs/devices.jsonl",
        collector.run_dir / "logs/syslog.jsonl",
    ]:
        retained = path.read_text()
        assert synthetic_key not in retained
        assert "[REDACTED]" in retained
    normalized = json.loads((collector.run_dir / "logs/devices.jsonl").read_text())
    assert normalized["event"] == "configuration_changed"
    assert normalized["source"] == "ROUTER"
    assert normalized["redacted"] is True
    assert normalized["attributes"]["raw_syslog"].endswith('"[REDACTED]"')


def test_incremental_streaming_and_remote_compute_attribution(tmp_path):
    payload = (
        b'2026-10-20T03:15:12.482Z {"event":"ready","message":"started"}\n'
        b"2026-10-20T03:15:13Z malformed {\n"
        b"2026-10-20T03:15:14Z     continuation line\n"
    )
    process = FakeProcess(payload, b"2026-10-20T03:15:15Z warning\n")
    commands = []
    tunnels = [
        {
            "link_id": "custom-site-vpn",
            "endpoints": [
                {
                    "node": "edge-a",
                    "interface": "vti7",
                    "underlay_interface": "eth2",
                    "underlay_address": "192.0.2.1",
                },
                {
                    "node": "site-b",
                    "interface": "vti9",
                    "underlay_interface": "eth3",
                    "underlay_address": "192.0.2.2",
                },
            ],
        }
    ]

    def start(command, **kwargs):
        commands.append(command)
        return process

    collector = DeviceLogCollector(
        FakeClient([node()]),
        "project-1",
        tmp_path / "run-1",
        "run-1",
        {"remote-1": "ssh://gns3@compute.example"},
        metadata={"ipsec_tunnels": tunnels},
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
    assert manifest["ipsec_tunnels"] == tunnels
    saved_manifest = json.loads((collector.run_dir / "manifest.json").read_text())
    assert saved_manifest["ipsec_tunnels"] == tunnels
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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file sharing semantics")
def test_manifest_replace_recovers_from_transient_reader_lock(tmp_path, monkeypatch):
    collector = DeviceLogCollector(
        FakeClient([]), "project-1", tmp_path / "run", "run", {}
    )
    manifest_path = collector.run_dir / "manifest.json"
    temporary = collector.run_dir / "manifest.json.tmp"
    original_replace = Path.replace
    denied = threading.Event()
    errors = []

    def observe_replace(source, target):
        try:
            return original_replace(source, target)
        except PermissionError as exc:
            if source == temporary and exc.winerror in (5, 32):
                denied.set()
            raise

    monkeypatch.setattr(Path, "replace", observe_replace)
    collector.manifest["status"] = "complete"

    def write_manifest():
        try:
            collector._write_manifest()
        except Exception as exc:  # noqa: BLE001 - capture worker failure for assertion
            errors.append(exc)

    with manifest_path.open("rb"):
        worker = threading.Thread(target=write_manifest, daemon=True)
        worker.start()
        observed = denied.wait(timeout=1)
    worker.join(timeout=2)
    assert observed and not worker.is_alive()
    assert not errors
    assert json.loads(manifest_path.read_text())["status"] == "complete"

    collector.manifest["status"] = "failed"
    previous = manifest_path.read_bytes()
    with manifest_path.open("rb"):
        started = time.monotonic()
        with pytest.raises(PermissionError) as failure:
            collector._write_manifest()
        assert time.monotonic() - started < 1
        assert failure.value.winerror in (5, 32)
        assert manifest_path.read_bytes() == previous
    collector._write_manifest()
    assert json.loads(manifest_path.read_text())["status"] == "failed"


def test_gns3_discovery_failure_is_preserved_in_manifest(tmp_path):
    class BrokenClient:
        def project_nodes(self, project_id):
            raise OSError("controller disconnected")

    collector = DeviceLogCollector(
        BrokenClient(), "project-1", tmp_path / "run", "run", {}
    )
    manifest = collector.run(0.01, poll_interval=0.005)
    assert manifest["status"] == "failed"
    assert any(
        "controller disconnected" in failure["reason"]
        for failure in manifest["failures"]
    )
    assert json.loads((tmp_path / "run/manifest.json").read_text())["failures"]


def test_router_syslog_is_saved_separately_and_missing_routers_fail(tmp_path):
    payload = (
        json.dumps(
            parse_syslog(
                b"<30>Oct 20 03:15:12 MET-OT-CORE interface eth0 up", "10.20.30.1"
            )
        ).encode()
        + b"\n"
    )
    process = FakeProcess(payload, b"")
    collector = DeviceLogCollector(
        FakeClient([node()]),
        "project-1",
        tmp_path / "run",
        "run",
        {"remote-1": "ssh://host"},
        expected_syslog_sources=["MET-OT-CORE", "MET-PLANT"],
        popen=lambda *_args, **_kwargs: process,
    )
    manifest = collector.run(0.01, poll_interval=0.001)
    syslog_lines = (tmp_path / "run/logs/syslog.jsonl").read_text().splitlines()
    assert len(syslog_lines) == 1
    assert json.loads(syslog_lines[0])["source"] == "MET-OT-CORE"
    assert manifest["syslog"]["sources"]["MET-OT-CORE"] == 1
    assert manifest["syslog"]["missing_sources"] == ["MET-PLANT"]
    assert manifest["status"] == "failed"


def test_switch_syslog_attribution_transport_and_failure_reporting(tmp_path):
    switch = {**node(), "name": "sw-dmz"}
    payload = (
        b'2026-10-20T03:15:13Z {"_metropolis_switch_syslog":true,'
        b'"timestamp":"2026-10-20T03:15:12.482Z","source":"spoofed",'
        b'"event":"ovs_log","level":"WARN","message":"port down"}\n'
        b"2026-10-20T03:15:14Z plain simulator output\n"
    )
    collector = DeviceLogCollector(
        FakeClient([switch]),
        "project-1",
        tmp_path / "run",
        "run",
        {"remote-1": "ssh://host"},
        expected_switch_syslog_sources=["sw-dmz", "sw-plant-aggregation"],
        popen=lambda *_args, **_kwargs: FakeProcess(payload, b""),
    )
    manifest = collector.run(0.01, poll_interval=0.001)
    events = [
        json.loads(line)
        for line in (tmp_path / "run/logs/devices.jsonl").read_text().splitlines()
    ]
    assert events[0]["source"] == "sw-dmz"
    assert events[0]["component"] == "switch"
    assert events[0]["stream"] == "syslog"
    assert events[0]["device_timestamp"] == "2026-10-20T03:15:12.482Z"
    assert events[0]["docker_timestamp"] == "2026-10-20T03:15:13Z"
    assert events[1]["stream"] == "stdout"
    assert manifest["syslog"]["transports"] == {"sw-dmz": "docker_stdout_bridge"}
    assert manifest["syslog"]["missing_sources"] == ["sw-plant-aggregation"]
    assert any(f["reason"] == "no switch syslog captured" for f in manifest["failures"])
    assert len((tmp_path / "run/logs/syslog.jsonl").read_text().splitlines()) == 1


def test_switch_daemon_log_error_marks_source_incomplete(tmp_path):
    switch = {**node(), "name": "sw-dmz"}
    payload = b'{"_metropolis_switch_syslog":true,"event":"ovs_log_unavailable","message":"missing ovsdb-server.log"}\n'
    collector = DeviceLogCollector(
        FakeClient([switch]),
        "project-1",
        tmp_path / "run",
        "run",
        {"remote-1": "ssh://host"},
        expected_switch_syslog_sources=["sw-dmz"],
        popen=lambda *_args, **_kwargs: FakeProcess(payload, b""),
    )
    manifest = collector.run(0.01, poll_interval=0.001)
    assert manifest["status"] == "failed"
    assert manifest["logs"]["sources"]["sw-dmz"]["complete"] is False
    assert any("ovs_log_unavailable" in f["reason"] for f in manifest["failures"])


def test_switch_marker_from_non_switch_stays_container_output(tmp_path):
    payload = b'{"_metropolis_switch_syslog":true,"event":"ovs_log","message":"claimed switch event"}\n'
    collector = DeviceLogCollector(
        FakeClient([node()]),
        "project-1",
        tmp_path / "run",
        "run",
        {"remote-1": "ssh://host"},
        expected_switch_syslog_sources=["sw-dmz"],
        popen=lambda *_args, **_kwargs: FakeProcess(payload, b""),
    )
    manifest = collector.run(0.01, poll_interval=0.001)
    event = json.loads(
        (tmp_path / "run/logs/devices.jsonl").read_text().splitlines()[0]
    )
    assert event["stream"] == "stdout"
    assert event["source"] == "MET-SCADA-01"
    assert manifest["syslog"]["sources"] == {}
    assert manifest["syslog"]["missing_sources"] == ["sw-dmz"]


def test_ntp_supervisor_cleans_up_when_syslog_cannot_start(monkeypatch, capsys):
    process = FakeProcess(b"", b"")
    calls = 0

    def start(_command):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("syslog unavailable")
        return process

    monkeypatch.setattr(ntp_services.subprocess, "Popen", start)
    monkeypatch.setattr(ntp_services.signal, "signal", lambda *_args: None)
    assert ntp_services.main() == 1
    assert process.terminated
    events = [
        json.loads(line)["event"] for line in capsys.readouterr().out.splitlines()
    ]
    assert "service_start_failed" in events
    assert "services_stopped" in events


def test_intentionally_silent_probe_is_reported_without_forcing_failure(tmp_path):
    probe = {
        **node(),
        "node_id": "probe-12345678",
        "name": "MET-DEBUG-DMZ-01",
        "properties": {"container_id": "probe-container"},
    }
    processes = [FakeProcess(b"ready\n", b""), FakeProcess(b"", b"")]
    collector = DeviceLogCollector(
        FakeClient([node(), probe]),
        "project-1",
        tmp_path / "run",
        "run",
        {"remote-1": "ssh://host"},
        silent_sources=[probe["name"]],
        popen=lambda *_args, **_kwargs: processes.pop(0),
    )
    manifest = collector.run(0.01, poll_interval=0.001)
    assert manifest["status"] == "complete"
    assert manifest["logs"]["intentionally_silent_sources"] == [probe["name"]]
    assert manifest["logs"]["sources"][probe["name"]]["lines"] == 0
    assert manifest["logs"]["sources"][probe["name"]]["complete"] is False
