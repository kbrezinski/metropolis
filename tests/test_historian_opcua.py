"""OPC UA collection runs alongside the existing MQTT historian lifecycle."""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEVICES = ROOT / "testbeds/metropolis/devices"
sys.path.insert(0, str(DEVICES / "runtime"))


@pytest.mark.parametrize("broker_retry", [False, True])
def test_historian_keeps_mqtt_subscription_and_closes_opcua_when_interrupted(
    monkeypatch, caplog, broker_retry
):
    spec = importlib.util.spec_from_file_location(
        "historian_opcua_integration",
        DEVICES / "operations/historian/historian_collector.py",
    )
    historian = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(historian)
    events = []
    subscriptions = []

    class OpcUa:
        def start(self):
            events.append("opcua_started")

        def close(self):
            events.append("opcua_closed")

    def worker(source):
        assert source == "CUSTOM-HISTORIAN"
        return OpcUa()

    class Client:
        def __init__(self, *args, **kwargs):
            assert kwargs["client_id"] == "CUSTOM-HISTORIAN"

        def username_pw_set(self, username, password):
            assert (username, password) == ("reader", "synthetic-only")

        def reconnect_delay_set(self, **kwargs):
            pass

        def connect(self, *args, **kwargs):
            assert events == ["opcua_started"]
            if broker_retry:
                raise OSError("not ready")

        def subscribe(self, topic, *, qos):
            subscriptions.append((topic, qos))

        def loop_forever(self):
            self.on_connect(self, None, None, 0, None)
            self.on_message(
                self,
                None,
                SimpleNamespace(
                    topic="custom/scada/status", payload=b'{"heartbeat":7}'
                ),
            )
            raise KeyboardInterrupt

        def disconnect(self):
            events.append("mqtt_disconnected")

    monkeypatch.setattr(
        historian, "OpcUaHistorianWorker", SimpleNamespace(from_environment=worker)
    )
    monkeypatch.setattr(historian.mqtt, "Client", Client)
    monkeypatch.setattr(historian, "apply_to", lambda client: None)
    monkeypatch.setenv("DEVICE_NAME", "CUSTOM-HISTORIAN")
    monkeypatch.setenv("MQTT_TOPIC", "custom/#")
    monkeypatch.setenv("MQTT_USERNAME", "reader")
    monkeypatch.setenv("MQTT_PASSWORD", "synthetic-only")

    def interrupt(_):
        historian.signal.getsignal(historian.signal.SIGTERM)(
            historian.signal.SIGTERM, None
        )

    monkeypatch.setattr(historian.time, "sleep", interrupt)
    caplog.set_level(logging.INFO, logger="metropolis.historian")
    previous_term = historian.signal.getsignal(historian.signal.SIGTERM)
    historian.main()
    assert events == ["opcua_started", "mqtt_disconnected", "opcua_closed"]
    if broker_retry:
        assert subscriptions == []
        assert "Broker not ready" in caplog.text
    else:
        assert subscriptions == [("custom/#", 0)]
        assert '"topic":"custom/scada/status","payload":{"heartbeat":7}' in caplog.text
    assert "synthetic-only" not in caplog.text
    assert historian.signal.getsignal(historian.signal.SIGTERM) == previous_term
