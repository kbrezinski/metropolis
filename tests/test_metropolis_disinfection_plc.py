"""Focused offline checks for the disinfection PLC protocol model."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEVICES = ROOT / "testbeds/metropolis/devices"
sys.path.insert(0, str(DEVICES / "runtime"))

spec = importlib.util.spec_from_file_location(
    "disinfection_controller_tests",
    DEVICES / "controllers/plc/disinfection_controller.py",
)
assert spec and spec.loader
plc = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plc
spec.loader.exec_module(plc)


class Pump:
    def __init__(self, active=False, available=True, faulted=False):
        self.active = active
        self.available = available
        self.faulted = faulted
        self.requested = None

    def command(self, value):
        self.requested = value

    def snapshot(self):
        return self.active, self.available


def test_register_contract_and_target_validation():
    context = plc.make_context()
    assert context[0].getValues(3, 0, 6) == [10, 0, 0, 0, 10, 0]
    assert context[0].getValues(1, 0, 1) == [1]
    assert plc.valid_target(25, 10) == 25
    assert plc.valid_target(65535, 10) == 10
    with pytest.raises(ValueError, match="target"):
        plc.make_context(target_x10=101)


def test_analyzer_poll_and_stale_heartbeat(monkeypatch):
    clients = []

    class Client:
        def __init__(self, host, **kwargs):
            assert host == "10.20.13.20"
            clients.append(self)
            self.closed = False

        def connect(self):
            return True

        def read_holding_registers(self, **kwargs):
            assert kwargs == {"address": 0, "count": 2, "slave": 1}
            return SimpleNamespace(isError=lambda: False, registers=[9, 5])

        def close(self):
            self.closed = True

    monkeypatch.setattr(plc, "ModbusTcpClient", Client)
    assert plc.read_analyzer("10.20.13.20") == (9, 5)
    assert clients[-1].closed

    class Missing(Client):
        def connect(self):
            return False

    monkeypatch.setattr(plc, "ModbusTcpClient", Missing)
    with pytest.raises(ConnectionError, match="unavailable"):
        plc.read_analyzer("10.20.13.20")
    assert clients[-1].closed

    freshness = plc.AnalyzerFreshness(3)
    assert freshness.observe(5, 0)
    assert freshness.observe(5, 2)
    assert not freshness.observe(5, 3)
    assert freshness.observe(6, 3.1)


def test_pump_command_status_failover_and_missing_output():
    context = plc.make_context()
    duty = Pump(active=True)
    standby = Pump()
    assert plc.command_pumps(context, duty, standby, True, True) == 0
    assert duty.requested is True and standby.requested is False
    assert context[0].getValues(3, 1, 2) == [1, 1000]
    assert context[0].getValues(2, 0, 3) == [1, 0, 1]

    duty.available = False
    duty.active = False
    standby.active = True
    alarms = plc.command_pumps(context, duty, standby, True, True)
    assert alarms & plc.DUTY_UNAVAILABLE
    assert not alarms & plc.OUTPUT_UNCONFIRMED
    assert standby.requested is True
    assert context[0].getValues(3, 1, 2) == [2, 1000]

    standby.active = False
    alarms = plc.command_pumps(context, duty, standby, True, True)
    assert alarms & plc.OUTPUT_UNCONFIRMED
    assert context[0].getValues(3, 1, 2) == [0, 0]

    plc.command_pumps(context, duty, standby, False, False)
    assert duty.requested is False and standby.requested is False
    assert context[0].getValues(2, 0, 3) == [0, 0, 0]


def test_faulted_duty_requests_standby():
    context = plc.make_context()
    duty = Pump(active=False, faulted=True)
    standby = Pump(active=True)
    alarms = plc.command_pumps(context, duty, standby, True, True)
    assert standby.requested is True
    assert alarms & plc.DUTY_UNAVAILABLE
    assert not alarms & plc.OUTPUT_UNCONFIRMED


def test_pending_duty_confirmation_does_not_start_standby_or_raise_alarm():
    context = plc.make_context()
    duty = Pump(active=False)
    standby = Pump(active=False)
    alarms = plc.command_pumps(
        context, duty, standby, True, True, confirmation_due=False
    )
    assert alarms == 0
    assert duty.requested is True
    assert standby.requested is False
    assert context[0].getValues(3, 1, 2) == [0, 0]

    alarms = plc.command_pumps(
        context, duty, standby, True, True, confirmation_due=True
    )
    assert standby.requested is True
    assert alarms & plc.OUTPUT_UNCONFIRMED
