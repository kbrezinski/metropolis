"""Exercise the HMI over real loopback HTTP without requiring GNS3."""

import base64
import http.client
import importlib.util
import json
import threading
from pathlib import Path
from uuid import UUID

import pytest

MODELS = Path(__file__).resolve().parents[1] / "testbeds/metropolis/devices/operations"


def load_model(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hmi = load_model("hmi_web", MODELS / "hmi/hmi_web.py")
ssh = load_model("start_ssh", MODELS / "engineering_workstation/start_ssh.py")


@pytest.fixture
def endpoint():
    state = hmi.HmiState("TEST-HMI", 65)
    server = hmi.create_server(state, "operator", "test-password", "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, body=None, authorized=True, custom=True):
        headers = {"Content-Type": "application/json"}
        if authorized:
            headers["Authorization"] = (
                "Basic " + base64.b64encode(b"operator:test-password").decode()
            )
        if custom:
            headers["X-Metropolis-Request"] = "hmi"
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    yield state, request
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_login_and_live_status(endpoint):
    state, request = endpoint
    status, headers, _ = request("GET", "/api/status", authorized=False)
    assert status == 401
    assert headers["WWW-Authenticate"].startswith("Basic")
    state.record_telemetry({"level": 64})
    state.set_connected(True)
    status, _, body = request("GET", "/api/status")
    assert status == 200
    payload = json.loads(body)
    assert payload["telemetry"] == {"level": 64}
    assert payload["mqtt_connected"] is True
    assert b"test-password" not in body
    assert request("GET", "/")[0] == 200


def test_operator_setpoint_becomes_mqtt_payload(endpoint):
    state, request = endpoint
    assert state.command_payload() is None
    assert state.take_pending_command() is None
    status, _, body = request("POST", "/api/setpoint", json.dumps({"value": 42}))
    assert status == 202
    request_id = json.loads(body)["request_id"]
    UUID(request_id)
    assert state.changed.is_set()
    assert state.command_payload() == {
        "request_id": request_id,
        "cell": "intake",
        "device": "TEST-HMI",
        "command": "level_setpoint",
        "value": 42.0,
        "unit": "percent",
        "timestamp": state.snapshot()["command_requested_at"],
    }
    assert state.take_pending_command() == state.command_payload()
    assert state.take_pending_command() is None
    assert state.snapshot()["command_status"] == "pending"
    assert state.snapshot()["last_publish_queued_at"] is None


def test_hmi_rejects_a_second_request_while_controller_ack_is_pending(endpoint):
    state, request = endpoint
    assert request("POST", "/api/setpoint", '{"value":42}')[0] == 202
    first = state.snapshot()["request_id"]
    status, _, body = request("POST", "/api/setpoint", '{"value":43}')
    assert status == 409
    assert "previous controller command" in body.decode()
    assert state.snapshot()["request_id"] == first
    assert state.snapshot()["setpoint"] == 42


def test_ack_is_attributed_to_current_request_and_cell(endpoint):
    state, _ = endpoint
    request_id = state.set_setpoint(42)
    ack = {
        "request_id": request_id,
        "cell": "intake",
        "status": "stored",
        "value": 42,
        "controller": "10.20.10.10",
        "timestamp": hmi.utc_now(),
    }
    assert not state.record_ack({**ack, "cell": "dosing"})
    assert not state.record_ack({**ack, "request_id": "wrong"})
    assert not state.record_ack({**ack, "value": 41})
    assert not state.record_ack({**ack, "timestamp": "bad"})
    state.record_publish(request_id)
    assert state.snapshot()["last_publish_queued_at"] is not None
    assert state.record_ack(ack)
    assert state.snapshot()["command_status"] == "stored"
    assert not state.record_ack({**ack, "status": "failed"})


def test_stale_telemetry_command_timeout_and_late_ack(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(hmi.time, "monotonic", lambda: now[0])
    state = hmi.HmiState("TEST-HMI", 65, ack_timeout=2, telemetry_stale_seconds=5)
    assert state.snapshot()["telemetry_status"] == "unavailable"
    state.record_telemetry({"level": 65})
    assert state.snapshot()["telemetry_status"] == "fresh"
    request_id = state.set_setpoint(42)
    now[0] = 106.0
    snapshot = state.snapshot()
    assert snapshot["telemetry_status"] == "stale"
    assert snapshot["command_status"] == "timeout"
    assert state.take_pending_command() is None
    assert not state.record_ack(
        {
            "request_id": request_id,
            "cell": "intake",
            "status": "stored",
            "value": 42,
            "controller": "10.20.10.10",
            "timestamp": hmi.utc_now(),
        }
    )


@pytest.mark.parametrize(
    "value",
    [
        True,
        42.01,
    ],
)
def test_invalid_commands_do_not_change_state(endpoint, value):
    state, request = endpoint
    assert request("POST", "/api/setpoint", json.dumps({"value": value}))[0] == 400
    assert state.snapshot()["setpoint"] == 65


def test_command_request_guards(endpoint):
    state, request = endpoint
    assert request("POST", "/api/setpoint", '{"value":42}', authorized=False)[0] == 401
    assert request("POST", "/api/setpoint", '{"value":42}', custom=False)[0] == 403
    assert request("POST", "/api/setpoint", "x" * 1025)[0] == 413
    assert request("POST", "/api/setpoint", '{"value":42,"extra":1}')[0] == 400
    assert state.snapshot()["setpoint"] == 65


@pytest.mark.parametrize(
    "password",
    [
        "one\ntwo",
    ],
)
def test_ssh_rejects_invalid_password_input(password):
    with pytest.raises(ValueError):
        ssh.validate_password(password)
