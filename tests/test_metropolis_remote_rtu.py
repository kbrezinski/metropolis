"""Offline checks for the two remote-site RTU roles."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pymodbus.exceptions import ModbusException

ROOT = Path(__file__).resolve().parents[1]
DEVICES = ROOT / "testbeds/metropolis/devices"
sys.path.insert(0, str(DEVICES / "runtime"))

spec = importlib.util.spec_from_file_location(
    "remote_site_controller_tests",
    DEVICES / "controllers/plc/remote_site_controller.py",
)
assert spec and spec.loader
rtu = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = rtu
spec.loader.exec_module(rtu)


class Pump:
    def __init__(
        self, active: bool = False, available: bool = True, faulted: bool = False
    ) -> None:
        self.active = active
        self.available = available
        self.faulted = faulted
        self.requested: bool | None = None

    def command(self, value: bool) -> None:
        self.requested = value

    def snapshot(self) -> tuple[bool, bool]:
        return self.active, self.available


def test_remote_rtu_register_contract():
    context = rtu.make_context(initial_level_x10=420)
    assert context[0].getValues(3, 0, 6) == [420, 0, 0, 0, 1, 0]
    assert context[0].getValues(1, 0, 1) == [1]
    with pytest.raises(ValueError, match="Initial level"):
        rtu.make_context(initial_level_x10=1001)


def test_networked_level_poll_closes_client_and_rejects_bad_data(monkeypatch):
    clients = []

    class Client:
        def __init__(self, host, **kwargs):
            assert host == "level-device"
            assert kwargs["port"] == 502
            clients.append(self)
            self.closed = False

        def connect(self):
            return True

        def read_holding_registers(self, **kwargs):
            assert kwargs == {"address": 0, "count": 2, "slave": 1}
            return SimpleNamespace(isError=lambda: False, registers=[550, 4])

        def close(self):
            self.closed = True

    monkeypatch.setattr(rtu, "ModbusTcpClient", Client)
    assert rtu.read_instrument("level-device") == (550, 4)
    assert clients[-1].closed

    class Missing(Client):
        def connect(self):
            return False

    monkeypatch.setattr(rtu, "ModbusTcpClient", Missing)
    with pytest.raises(ConnectionError, match="unavailable"):
        rtu.read_instrument("level-device")
    assert clients[-1].closed

    class Broken(Client):
        def read_holding_registers(self, **kwargs):
            raise ModbusException("timed out")

    monkeypatch.setattr(rtu, "ModbusTcpClient", Broken)
    with pytest.raises(ConnectionError, match="poll failed"):
        rtu.read_instrument("level-device")
    assert clients[-1].closed

    class Invalid(Client):
        def read_holding_registers(self, **kwargs):
            return SimpleNamespace(isError=lambda: False, registers=[1100, 4])

    monkeypatch.setattr(rtu, "ModbusTcpClient", Invalid)
    with pytest.raises(ValueError, match="outside"):
        rtu.read_instrument("level-device")
    assert clients[-1].closed


def test_stale_instrument_heartbeat_recovers():
    freshness = rtu.InstrumentFreshness(stale_after=3)
    assert freshness.observe(4, 0)
    assert freshness.observe(4, 2)
    assert not freshness.observe(4, 3)
    assert freshness.observe(5, 3.1)


def test_pump_status_and_alarms_allow_startup_confirmation():
    context = rtu.make_context()
    pump = Pump(active=False, available=False)
    assert (
        rtu.publish_status(
            context,
            pump,
            instrument_ready=True,
            instrument_alarm=0,
            confirmation_due=False,
        )
        == 0
    )
    assert pump.requested is True
    assert context[0].getValues(3, 1, 2) == [0, 1]

    alarms = rtu.publish_status(
        context,
        pump,
        instrument_ready=False,
        instrument_alarm=rtu.INSTRUMENT_STALE,
        confirmation_due=True,
    )
    assert alarms == (
        rtu.INSTRUMENT_STALE | rtu.DUTY_UNAVAILABLE | rtu.OUTPUT_UNCONFIRMED
    )
    assert context[0].getValues(3, 4, 2) == [1, alarms]

    pump.available = True
    pump.active = True
    assert (
        rtu.publish_status(
            context,
            pump,
            instrument_ready=True,
            instrument_alarm=0,
            confirmation_due=True,
        )
        == 0
    )
    assert context[0].getValues(3, 1, 2) == [1, 1]

    pump.faulted = True
    alarms = rtu.publish_status(
        context,
        pump,
        instrument_ready=True,
        instrument_alarm=rtu.INSTRUMENT_UNAVAILABLE,
        confirmation_due=True,
    )
    assert alarms & rtu.DUTY_UNAVAILABLE
    assert alarms & rtu.OUTPUT_UNCONFIRMED
    assert alarms & rtu.INSTRUMENT_UNAVAILABLE

    context[0].setValues(1, 0, [0])
    pump.faulted = False
    pump.active = False
    alarms = rtu.publish_status(
        context,
        pump,
        instrument_ready=True,
        instrument_alarm=0,
        confirmation_due=True,
    )
    assert alarms == 0
    assert pump.requested is False
    assert context[0].getValues(3, 4, 1) == [0]
