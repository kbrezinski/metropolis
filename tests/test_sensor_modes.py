"""Checks for the sensor's publish modes and the reachability probe node.

The publish mode changes the traffic shape a capture sees, so both are pinned
here: a persistent session on one hand, and per-message connections on the
other. The publisher is built directly rather than through ``main`` because
``main`` runs forever and swallows KeyboardInterrupt.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SENSORS = ROOT / "testbeds/metropolis/devices/field/sensors"
PROBE = ROOT / "testbeds/metropolis/devices/field/reachability_probe"

GNS3 = ROOT / "scripts/gns3"
if str(GNS3) not in sys.path:
    sys.path.insert(0, str(GNS3))


def load_sensor():
    directory = str(SENSORS)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    spec = importlib.util.spec_from_file_location(
        "mqtt_sensor_modes", SENSORS / "mqtt_sensor.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["mqtt_sensor_modes"] = module
    spec.loader.exec_module(module)
    return module


def build(module, mode: str):
    return module.build_publisher(
        broker="127.0.0.1",
        port=1883,
        topic="metropolis/plant/intake/telemetry",
        client_id="MET-SENSOR-INTAKE-01",
        username="lab_device",
        password="LabOnly_Device_2026",
        mode=mode,
    )


# -- publish modes ------------------------------------------------------------


def test_per_message_mode_publishes_with_one_shot_connections(monkeypatch):
    """Gotham's t2 shape: connect, publish, disconnect for every message."""
    module = load_sensor()
    sent: list[dict] = []
    monkeypatch.setattr(module.publish, "single", lambda **kwargs: sent.append(kwargs))

    publisher = build(module, "per-message")
    status = publisher.send('{"value": 65.0}')

    assert status == module.mqtt.MQTT_ERR_SUCCESS
    assert len(sent) == 1
    assert sent[0]["hostname"] == "127.0.0.1"
    assert sent[0]["topic"] == "metropolis/plant/intake/telemetry"
    assert sent[0]["auth"]["username"] == "lab_device"
    # No session is held, so there is nothing to close.
    assert publisher.client is None


def test_per_message_mode_does_not_build_a_client(monkeypatch):
    module = load_sensor()
    monkeypatch.setattr(module.publish, "single", lambda **kwargs: None)
    monkeypatch.setattr(
        module.mqtt,
        "Client",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no session in this mode")
        ),
    )
    publisher = build(module, "per-message")
    assert publisher.client is None


def test_per_message_mode_survives_a_broker_that_is_down(monkeypatch):
    module = load_sensor()

    def refuse(**kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(module.publish, "single", refuse)
    publisher = build(module, "per-message")
    assert publisher.send("{}") == module.mqtt.MQTT_ERR_NO_CONN


def test_persistent_mode_reuses_one_session(monkeypatch):
    module = load_sensor()
    published: list[tuple] = []
    lifecycle: list[str] = []

    class FakeResult:
        rc = 0

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def username_pw_set(self, *args):
            pass

        def reconnect_delay_set(self, **kwargs):
            pass

        def connect(self, *args, **kwargs):
            lifecycle.append("connect")
            return 0

        def loop_start(self):
            lifecycle.append("loop_start")

        def loop_stop(self):
            lifecycle.append("loop_stop")

        def disconnect(self):
            lifecycle.append("disconnect")

        def publish(self, topic, payload, **kwargs):
            published.append((topic, payload))
            return FakeResult()

    monkeypatch.setattr(module.mqtt, "Client", FakeClient)
    publisher = build(module, "persistent")

    assert publisher.send("{}") == 0
    assert publisher.send("{}") == 0
    assert len(published) == 2
    # One connection for two messages is the whole point of this mode.
    assert lifecycle.count("connect") == 1

    publisher.close()
    assert lifecycle[-2:] == ["loop_stop", "disconnect"]


def test_persistent_mode_retries_until_the_broker_answers(monkeypatch):
    module = load_sensor()
    attempts = {"count": 0}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def username_pw_set(self, *args):
            pass

        def reconnect_delay_set(self, **kwargs):
            pass

        def connect(self, *args, **kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise OSError("broker not up yet")

        def loop_start(self):
            pass

    monkeypatch.setattr(module.mqtt, "Client", FakeClient)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    publisher = build(module, "persistent")
    assert attempts["count"] == 2
    assert publisher.client is not None


def test_an_unknown_publish_mode_is_refused(monkeypatch):
    module = load_sensor()
    with pytest.raises(SystemExit) as caught:
        build(module, "sometimes")
    assert "PUBLISH_MODE" in str(caught.value)


def test_persistent_is_the_default_mode():
    """An existing deployment that sets nothing keeps the old behaviour."""
    source = (SENSORS / "mqtt_sensor.py").read_text(encoding="utf-8")
    assert 'os.getenv("PUBLISH_MODE", PERSISTENT)' in source
    assert 'PERSISTENT = "persistent"' in source


# -- reachability probe -------------------------------------------------------


def test_the_probe_runs_no_service():
    """It is a target, not a generator: no application is started."""
    dockerfile = (PROBE / "Dockerfile").read_text(encoding="utf-8")
    assert 'CMD ["sleep", "infinity"]' in dockerfile
    assert "network-entrypoint.sh" in dockerfile


def test_the_probe_is_registered_and_inventoried():
    import yaml

    from templates import TEMPLATES

    registered = {item.image for item in TEMPLATES}
    assert "metropolis/reachability-probe:dev" in registered

    inventory = yaml.safe_load(
        (
            ROOT
            / "testbeds/metropolis/datasets/water_treatment_v1"
            / "device_instances/initial_devices.yaml"
        ).read_text(encoding="utf-8")
    )
    probe = next(
        device
        for device in inventory["devices"]
        if device["name"] == "MET-DEBUG-DMZ-01"
    )
    assert probe["image"] == "metropolis/reachability-probe:dev"
    # The node's address is applied from its environment, so they must agree.
    assert probe["environment"]["NODE_IP"] == probe["address"]
    assert probe["environment"]["NODE_GATEWAY"] == probe["gateway"]
