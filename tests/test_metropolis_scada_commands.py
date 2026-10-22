"""SCADA command readback, replay protection, and network alarm payloads."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEVICES = ROOT / "testbeds/metropolis/devices"
sys.path.insert(0, str(DEVICES / "runtime"))
spec = importlib.util.spec_from_file_location(
    "scada_command_tests", DEVICES / "operations/scada/scada_collector.py"
)
assert spec and spec.loader
scada = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = scada
spec.loader.exec_module(scada)
hmi_spec = importlib.util.spec_from_file_location(
    "hmi_command_contract", DEVICES / "operations/hmi/hmi_web.py"
)
assert hmi_spec and hmi_spec.loader
hmi_web = importlib.util.module_from_spec(hmi_spec)
sys.modules[hmi_spec.name] = hmi_web
hmi_spec.loader.exec_module(hmi_web)


class Publisher:
    def __init__(self):
        self.messages = []

    def publish(self, topic, payload, *, qos, retain):
        self.messages.append((topic, json.loads(payload), qos, retain))
        return SimpleNamespace(rc=0)


def request(cell: str, command: str, value: float, *, unit: str = "percent") -> bytes:
    return json.dumps(
        {
            "request_id": str(uuid4()),
            "cell": cell,
            "device": f"MET-HMI-{cell.upper()}-01",
            "command": command,
            "value": value,
            "unit": unit,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    ).encode()


def test_command_is_written_once_and_acknowledged_only_after_plc_readback(monkeypatch):
    writes = []

    class Client:
        def __init__(self, host, *, port, timeout, retries):
            assert (host, port, timeout, retries) == ("10.20.11.10", 502, 3, 0)
            self.register = None

        def connect(self):
            return True

        def write_register(self, *, address, value, slave):
            writes.append((address, value, slave))
            self.register = value
            return SimpleNamespace(isError=lambda: False)

        def read_holding_registers(self, *, address, count, slave):
            assert (address, count, slave) == (4, 1, 1)
            return SimpleNamespace(isError=lambda: False, registers=[self.register])

        def close(self):
            pass

    monkeypatch.setattr(scada, "ModbusTcpClient", Client)
    target = scada.Target("dosing", "10.20.11.10", "dosing", "status")
    publisher = Publisher()
    bridge = scada.CommandBridge([target], publisher, port=502, unit_id=1)
    raw = request("dosing", "dose_setpoint", 64.5)
    first = bridge.handle(scada.command_topic(target), raw)
    assert first["status"] == "stored"
    assert first["value"] == 64.5
    assert writes == [(4, 645, 1)]
    assert publisher.messages[0][:2] == (scada.ack_topic(target), first)
    assert publisher.messages[0][2:] == (1, False)
    assert bridge.handle(scada.command_topic(target), raw) == first
    assert writes == [(4, 645, 1)]
    assert len(publisher.messages) == 2


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"cell": "intake"}, "cell"),
        ({"command": "level_setpoint"}, "not supported"),
        ({"unit": "kPa"}, "unit"),
        ({"value": 64.55}, "increments"),
        ({"timestamp": "2000-01-01T00:00:00Z"}, "stale"),
    ],
)
def test_bad_or_stale_command_is_rejected_without_modbus(monkeypatch, change, reason):
    monkeypatch.setattr(
        scada,
        "ModbusTcpClient",
        lambda *args, **kwargs: pytest.fail("invalid request reached Modbus"),
    )
    target = scada.Target("dosing", "10.20.11.10", "dosing", "status")
    bridge = scada.CommandBridge([target], Publisher(), port=502, unit_id=1)
    payload = json.loads(request("dosing", "dose_setpoint", 65))
    payload.update(change)
    result = bridge.handle(scada.command_topic(target), json.dumps(payload).encode())
    assert result["status"] == "rejected"
    assert reason in result["reason"]


def test_controller_mismatch_reports_failed_ack(monkeypatch):
    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def connect(self):
            return True

        def write_register(self, **kwargs):
            return SimpleNamespace(isError=lambda: False)

        def read_holding_registers(self, **kwargs):
            return SimpleNamespace(isError=lambda: False, registers=[500])

        def close(self):
            pass

    monkeypatch.setattr(scada, "ModbusTcpClient", Client)
    target = scada.Target("intake", "10.20.10.10", "intake", "status")
    bridge = scada.CommandBridge([target], Publisher(), port=502, unit_id=1)
    result = bridge.handle(
        scada.command_topic(target), request("intake", "level_setpoint", 60)
    )
    assert result["status"] == "failed"
    assert "readback" in result["reason"]


def test_hmi_request_reaches_scada_bridge_and_receives_plc_readback(monkeypatch):
    class Client:
        def __init__(self, *args, **kwargs):
            self.value = None

        def connect(self):
            return True

        def write_register(self, *, address, value, slave):
            assert (address, slave) == (4, 1)
            self.value = value
            return SimpleNamespace(isError=lambda: False)

        def read_holding_registers(self, *, address, count, slave):
            assert (address, count, slave) == (4, 1, 1)
            return SimpleNamespace(isError=lambda: False, registers=[self.value])

        def close(self):
            pass

    monkeypatch.setattr(scada, "ModbusTcpClient", Client)
    panel = hmi_web.HmiState(
        "MET-HMI-FILTRATION-01",
        65.0,
        cell="filtration",
        setpoint_command="backwash_threshold",
        setpoint_unit="kPa",
        setpoint_min=10.0,
    )
    request_id = panel.set_setpoint(62.5)
    pending = panel.take_pending_command()
    assert pending["request_id"] == request_id
    target = scada.Target("filtration", "10.20.12.10", "filtration", "status")
    bridge = scada.CommandBridge([target], Publisher(), port=502, unit_id=1)
    ack = bridge.handle(scada.command_topic(target), json.dumps(pending).encode())
    assert ack["status"] == "stored"
    assert panel.record_ack(ack)
    assert panel.snapshot()["command_status"] == "stored"


def test_filtration_profile_and_alarm_payloads():
    target = scada.Target("filtration", "10.20.12.10", "filtration", "status")
    assert (
        scada.validate_command(
            target,
            json.loads(request("filtration", "backwash_threshold", 62.5, unit="kPa")),
            now=datetime.now(timezone.utc),
        )
        == 625
    )
    with pytest.raises(ValueError, match="between 10 and 100"):
        scada.validate_command(
            target,
            json.loads(request("filtration", "backwash_threshold", 0, unit="kPa")),
            now=datetime.now(timezone.utc),
        )
    payload = scada.decode_registers(target, [680, 1, 3, 5, 650, 4])
    assert payload["differential_pressure_kpa"] == 68
    assert payload["backwash_phase"] == 1
    assert payload["confirmed_outputs"] == 3
    assert payload["quality"] == "degraded"
    assert payload["alarms"] == [scada.ALARM_NAMES[4]]


def test_future_timestamp_is_not_accepted():
    target = scada.Target("intake", "10.20.10.10", "intake", "status")
    payload = json.loads(request("intake", "level_setpoint", 65))
    payload["timestamp"] = (
        datetime.now(timezone.utc) + timedelta(minutes=1)
    ).isoformat()
    with pytest.raises(ValueError, match="future"):
        scada.validate_command(target, payload, now=datetime.now(timezone.utc))


def test_command_arriving_after_hmi_timeout_is_rejected():
    target = scada.Target("intake", "10.20.10.10", "intake", "status")
    payload = json.loads(request("intake", "level_setpoint", 65))
    payload["timestamp"] = (
        datetime.now(timezone.utc) - timedelta(seconds=16)
    ).isoformat()
    with pytest.raises(ValueError, match="stale"):
        scada.validate_command(target, payload, now=datetime.now(timezone.utc))


def test_plc_heartbeat_monitor_detects_frozen_data_and_recovers():
    monitor = scada.ControllerHeartbeatMonitor(stale_after=10)
    assert monitor.observe("dosing", 5, 0)
    assert monitor.observe("dosing", 5, 9)
    assert not monitor.observe("dosing", 5, 10)
    assert monitor.observe("dosing", 6, 11)
    assert monitor.observe("filtration", 5, 100)
    monitor.forget("dosing")
    assert monitor.observe("dosing", 6, 100)
