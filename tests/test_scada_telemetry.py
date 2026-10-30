"""Selected SCADA exports cannot delay polling or leak full controller payloads."""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEVICES = ROOT / "testbeds/metropolis/devices"
sys.path.insert(0, str(DEVICES / "runtime"))
import telemetry_export as exports  # noqa: E402


def cell(heartbeat=1, **extra):
    return {
        "cell": "custom-cell",
        "timestamp": "2026-10-30T01:02:03Z",
        "quality": "good",
        "alarms": [],
        "heartbeat": heartbeat,
        **extra,
    }


def test_export_selects_status_fields_and_utc_source_without_mutating_input():
    exporter = exports.TelemetryExporter("SCADA-X", "https://192.0.2.1/v1/status", None)
    payload = cell(controller="192.0.2.55", password="synthetic-secret", setpoint=50)
    exporter.submit([payload])
    payload["alarms"].append("later")
    snapshot = exporter.pending.get_nowait()
    assert snapshot["source"] == "SCADA-X"
    assert (
        datetime.fromisoformat(snapshot["timestamp"]).utcoffset().total_seconds() == 0
    )
    assert snapshot["cells"] == [cell()]
    assert payload["password"] == "synthetic-secret"


def test_slow_failed_delivery_keeps_latest_snapshot_and_recovers(monkeypatch, caplog):
    entered = threading.Event()
    release = threading.Event()
    delivered = threading.Event()
    sent = []

    def post(url, payload, context, *, timeout):
        assert timeout == 2
        sent.append(payload["cells"][0]["heartbeat"])
        if len(sent) == 1:
            entered.set()
            assert release.wait(2)
            raise OSError("secret response body")
        delivered.set()

    monkeypatch.setattr(exports, "post_snapshot", post)
    exporter = exports.TelemetryExporter("SCADA-X", "https://192.0.2.1/v1/status", None)
    caplog.set_level(logging.INFO, logger="metropolis.telemetry_export")
    exporter.start()
    try:
        exporter.submit([cell(1)])
        assert entered.wait(1)
        # The sender is blocked: polling can submit repeatedly, with bounded memory.
        for value in range(2, 100):
            exporter.submit([cell(value)])
        assert exporter.pending.qsize() == 1
        release.set()
        assert delivered.wait(2)
    finally:
        release.set()
        exporter.close()
    assert not exporter.worker.is_alive()
    assert sent == [1, 99]
    assert "delivery failed source=SCADA-X error=OSError" in caplog.text
    assert "delivery recovered source=SCADA-X" in caplog.text
    assert "secret response body" not in caplog.text
    exporter.submit([cell(100)])
    assert exporter.pending.empty()


def test_export_is_optional_and_explicit_tls_configuration_fails_early(monkeypatch):
    def invalid_context(env):
        raise FileNotFoundError("missing CA")

    monkeypatch.setattr(exports, "client_context", invalid_context)
    assert exports.TelemetryExporter.from_environment("SCADA-X", {}) is None
    with pytest.raises(FileNotFoundError, match="missing CA"):
        exports.TelemetryExporter.from_environment(
            "SCADA-X", {"TELEMETRY_RELAY_URL": "https://192.0.2.1/v1/status"}
        )
    for url in ("http://192.0.2.1/v1/status", "https://user:secret@host/v1/status"):
        with pytest.raises(ValueError, match="HTTPS"):
            exports.TelemetryExporter("SCADA-X", url, None)


def test_poll_loop_exports_each_outage_cycle_and_recovery_and_closes(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "telemetry_scada_loop", DEVICES / "operations/scada/scada_collector.py"
    )
    assert spec and spec.loader
    scada = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = scada
    spec.loader.exec_module(scada)
    events = []
    outgoing = []

    class Exporter:
        def start(self):
            events.append("started")

        def submit(self, snapshot):
            events.append(snapshot)

        def close(self):
            events.append("closed")

    class Publisher:
        def __init__(self, *args, **kwargs):
            pass

        def username_pw_set(self, *args):
            pass

        def reconnect_delay_set(self, **kwargs):
            pass

        def connect(self, *args, **kwargs):
            pass

        def loop_start(self):
            pass

        def publish(self, topic, payload, **kwargs):
            outgoing.append(json.loads(payload))

        def loop_stop(self):
            pass

        def disconnect(self):
            events.append("disconnected")

    monkeypatch.setattr(
        scada,
        "TelemetryExporter",
        SimpleNamespace(from_environment=lambda name: Exporter()),
    )
    monkeypatch.setattr(scada.mqtt, "Client", Publisher)
    monkeypatch.setattr(scada, "apply_to", lambda publisher: None)
    monkeypatch.setenv("DEVICE_NAME", "SCADA-X")
    monkeypatch.setenv("MQTT_USERNAME", "test")
    monkeypatch.setenv("MQTT_PASSWORD", "synthetic")
    monkeypatch.delenv("DNP3_TARGETS_B64", raising=False)
    monkeypatch.setenv(
        "SCADA_TARGETS",
        json.dumps(
            [
                {
                    "id": "custom-cell",
                    "host": "192.0.2.55",
                    "profile": "intake",
                    "topic": "x/status",
                }
            ]
        ),
    )
    polls = []
    previous_term = scada.signal.getsignal(scada.signal.SIGTERM)

    def poll(target, **kwargs):
        polls.append(target.cell)
        if len(polls) < 3:
            raise ConnectionError("offline")
        return cell()

    def sleep(interval):
        if len(polls) == 3:
            scada.signal.getsignal(scada.signal.SIGTERM)(scada.signal.SIGTERM, None)

    monkeypatch.setattr(scada, "poll_target", poll)
    monkeypatch.setattr(scada.time, "sleep", sleep)
    scada.main()
    snapshots = [event for event in events if isinstance(event, list)]
    assert [snapshot[0]["quality"] for snapshot in snapshots] == [
        "unavailable",
        "unavailable",
        "good",
    ]
    assert snapshots[0][0]["alarms"] == ["controller_unreachable"]
    assert snapshots[2][0]["transition"] == "poll_recovered"
    assert [record["quality"] for record in outgoing] == ["unavailable", "good"]
    assert events[0] == "started"
    assert events[-2:] == ["closed", "disconnected"]
    assert scada.signal.getsignal(scada.signal.SIGTERM) == previous_term
