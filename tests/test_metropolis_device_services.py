"""Checks for Metropolis' MQTT and CoAP device configuration/services."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
COAP_SPEC = importlib.util.spec_from_file_location(
    "coap_sensor",
    ROOT
    / "testbeds"
    / "metropolis"
    / "devices"
    / "field"
    / "sensors"
    / "coap_sensor.py",
)
assert COAP_SPEC and COAP_SPEC.loader
coap_sensor = importlib.util.module_from_spec(COAP_SPEC)
COAP_SPEC.loader.exec_module(coap_sensor)


def test_mosquitto_requires_authentication_and_generates_password_file() -> None:
    config = (
        ROOT / "testbeds/metropolis/devices/services/mqtt_broker/mosquitto.conf"
    ).read_text(encoding="utf-8")
    entrypoint = (
        ROOT / "testbeds/metropolis/devices/services/mqtt_broker/broker-entrypoint.sh"
    ).read_text(encoding="utf-8")
    assert "allow_anonymous false" in config
    assert "password_file /mosquitto/data/passwordfile" in config
    assert "mosquitto_passwd -b -c" in entrypoint
    assert 'mosquitto_passwd -b "$password_file"' in entrypoint


def test_coap_sensor_answers_get_with_its_configured_status() -> None:
    # CoAP v1 confirmable GET, message ID 7, four-byte token "abcd".
    request = bytes((0x44, 1, 0, 7)) + b"abcd"
    response = coap_sensor.handle_get(request, "MET-SENSOR-INTAKE-01", "level", 65.0)
    assert response is not None
    assert response[:4] == bytes((0x64, 69, 0, 7))
    assert response[4:8] == b"abcd"
    assert b"MET-SENSOR-INTAKE-01" in response


def answer(request: bytes) -> bytes | None:
    return coap_sensor.handle_get(request, "MET-SENSOR-INTAKE-01", "level", 65.0)


def test_coap_discovery_advertises_a_real_json_resource() -> None:
    response = answer(b"\x40\x01\x12\x34\xbb.well-known\x04core")
    assert response is not None
    assert response[:7] == b"\x60\x45\x12\x34\xc1\x28\xff"
    # Discovery advertises the status resource plus one per measurement.
    discovery = response[7:]
    assert discovery.startswith(b'</status>;rt="metropolis.sensor";ct=50')
    assert b"</level>" in discovery
    assert b"</pressure>" in discovery
    status = answer(b"\x40\x01\x12\x35\xb6status")
    assert status is not None
    assert status[:7] == b"\x60\x45\x12\x35\xc1\x32\xff"
    assert json.loads(status[7:]) == {
        "device": "MET-SENSOR-INTAKE-01",
        "sensor": "level",
        "value": 65.0,
    }


@pytest.mark.parametrize(
    ("packet", "response_code"),
    [
        (b"\x40\x01\x00\x07\xb7missing", 132),  # Unknown path
        (b"\x40\x02\x00\x07\xb6status", 133),  # POST
        (b"\x40\x01\x00\x07\xb6status\x61\x28", 134),  # Wrong Accept
        (b"\x40\x01\x00\x07\xb6status\x41q", 130),  # Query unsupported
        (b"\x40\x01\x00\x07\xbd\x00abcdefghijklm", 132),  # Extended length
    ],
)
def test_coap_rejects_unsupported_requests(packet, response_code) -> None:
    response = answer(packet)
    assert response is not None
    assert response[1] == response_code


@pytest.mark.parametrize(
    "packet",
    [
        b"",
        b"\x40\x01\x00",
        b"\x40\x01\x00\x07\xb6x",
        b"\x40\x01\x00\x07\xbd",
        b"\x40\x01\x00\x07\xff",
        b"\x40\x01\x00\x07\xf0",
        b"\x60\x01\x00\x07",
    ],
)
def test_coap_ignores_malformed_packets_and_ack_messages(packet) -> None:
    assert answer(packet) is None


def test_coap_non_response_preserves_token_with_a_server_message_id(
    monkeypatch,
) -> None:
    monkeypatch.setattr(coap_sensor.secrets, "randbelow", lambda _: 99)
    response = answer(b"\x51\x01\x00\x07t\xb6status")
    assert response is not None
    assert response[:5] == b"\x51\x45\x00\x63t"
