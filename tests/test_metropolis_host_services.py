"""Exercise the HMI over real loopback HTTP without requiring GNS3."""

import base64
import http.client
import importlib.util
import json
import threading
from pathlib import Path

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
    assert request("POST", "/api/setpoint", json.dumps({"value": 42}))[0] == 202
    assert state.changed.is_set()
    assert state.command_payload()["value"] == 42
    assert state.snapshot()["last_publish_queued_at"] is None


@pytest.mark.parametrize("value", [True, "42", -1, 101, float("nan"), None])
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


@pytest.mark.parametrize("password", ["", "one\ntwo", "one\rtwo", "one\x00two"])
def test_ssh_rejects_invalid_password_input(password):
    with pytest.raises(ValueError):
        ssh.validate_password(password)


def test_ssh_password_accepts_literal_symbols():
    assert ssh.validate_password("example:$()!:") == "example:$()!:"
