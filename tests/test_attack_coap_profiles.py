"""Protocol and report checks for the expanded CoAP experiment."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
ATTACKS = ROOT / "scripts/attacks"
sys.path.insert(0, str(ATTACKS))
import coap_response_measurement as coap  # noqa: E402
import coap_experiment_report as report  # noqa: E402
import coap_reflection_receiver as receiver  # noqa: E402

SENSOR_PATH = ROOT / "testbeds/metropolis/devices/field/sensors/coap_sensor.py"
spec = importlib.util.spec_from_file_location("coap_sensor_profiles", SENSOR_PATH)
sensor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sensor)


@pytest.mark.parametrize("confirmable", [True, False])
def test_real_sensor_replies_to_both_message_types(confirmable):
    token = bytes.fromhex("beef1234")
    request = coap.build_get("/.well-known/core", token, 1234, confirmable)
    reply = sensor.handle_get(request, "MET-SENSOR-INTAKE-01", "level", 65)
    assert coap.response_code(reply, token, 1234, confirmable) == 69
    assert ((reply[0] >> 4) & 3) == (2 if confirmable else 1)
    assert receiver.matching_reply(reply, bytes.fromhex("beef")) == 69
    assert receiver.matching_reply(reply, bytes.fromhex("dead")) is None
    audit = sensor.audit_response(request, reply, ("10.20.30.20", 56830))
    assert audit["experiment_id"] == "beef"
    assert audit["resource"] == "/.well-known/core"
    assert audit["response_code"] == "2.05"
    assert audit["request_bytes"] == len(request)
    assert audit["response_bytes"] == len(reply)


def test_mixed_resources_include_success_and_not_found():
    args = SimpleNamespace(resource=None, message_type="mixed")
    readings = {item.key: 1 for item in sensor.RESOURCES}
    codes = []
    for index in range(10):
        path, confirmable = coap.request_choice(args, index)
        token = bytes.fromhex("beef1234")
        request = coap.build_get(path, token, index, confirmable)
        reply = sensor.handle_get(request, "MET-SENSOR-INTAKE-01", "level", readings)
        codes.append(coap.response_code(reply, token, index, confirmable))
    assert codes == [69] * 9 + [132]


def test_named_profile_and_spoof_id_validation():
    args = SimpleNamespace(profile="burst", rate=None, duration=None, max_messages=None, experiment_id="beef", mode="measure")
    coap.settings(args)
    assert (args.rate, args.duration, args.max_messages) == (25, 10, 250)
    args.mode = "spoof"
    args.experiment_id = None
    args.rate = args.duration = args.max_messages = None
    with pytest.raises(ValueError, match="experiment-id"):
        coap.settings(args)


def test_receiver_counts_real_loopback_reply(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        listen_port = reservation.getsockname()[1]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        sensor_port = reservation.getsockname()[1]
    output = tmp_path / "receiver.jsonl"
    env = {**os.environ, "METROPOLIS_LAB_ACK": "yes"}
    process = subprocess.Popen(
        [sys.executable, str(ATTACKS / "coap_reflection_receiver.py"),
         "--host", "127.0.0.1", "--port", str(sensor_port),
         "--listen-host", "127.0.0.1", "--listen-port", str(listen_port),
         "--experiment-id", "beef", "--duration", "0.8", "--output", str(output)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        time.sleep(0.2)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.bind(("127.0.0.1", sensor_port))
            request = coap.build_get("/status", bytes.fromhex("beef1234"), 7)
            reply = sensor.handle_get(request, "MET-SENSOR-INTAKE-01", "level", 65)
            sender.sendto(reply, ("127.0.0.1", listen_port))
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stdout + stderr
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        result = next(row for row in rows if row["event"] == "summary")
        assert result["received"] == 1
        assert result["response_bytes"] == len(reply)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_report_combines_three_observation_points(tmp_path):
    sender = tmp_path / "sender.jsonl"
    recipient = tmp_path / "receiver.jsonl"
    sensor_log = tmp_path / "sensor.log"
    sender.write_text(json.dumps({"tool": "coap", "event": "summary", "experiment_id": "beef", "sent": 2, "request_bytes": 40, "resources_sent": {"/status": 2}}) + "\n", encoding="utf-8")
    recipient.write_text(json.dumps({"tool": "coap_receiver", "event": "summary", "experiment_id": "beef", "received": 1, "response_bytes": 80, "response_codes": {"2.05": 1}}) + "\n", encoding="utf-8")
    sensor_log.write_text("INFO COAP_AUDIT " + json.dumps({"experiment_id": "beef", "request_bytes": 20, "response_bytes": 80, "resource": "/status", "response_code": "2.05"}) + "\n", encoding="utf-8")
    assert report.sensor_counts(sensor_log, "beef")["responses"] == 1
    output = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, str(ATTACKS / "coap_experiment_report.py"),
         "--sender", str(sender), "--receiver", str(recipient),
         "--sensor-log", str(sensor_log), "--output", str(output)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    combined = json.loads(output.read_text(encoding="utf-8"))
    assert (combined["sender"]["sent"], combined["sensor"]["responses"], combined["receiver"]["received"]) == (2, 1, 1)
    assert combined["observed_payload_ratio"] == 2
