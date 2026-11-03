"""Offline checks for the networked filtration cell and its build plan."""

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


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


filtration = load(
    "filtration_controller", DEVICES / "controllers/plc/filtration_controller.py"
)


class Actuator:
    def __init__(
        self, active: bool = False, available: bool = True, faulted: bool = False
    ):
        self.active = active
        self.available = available
        self.faulted = faulted
        self.requested: bool | None = None

    def command(self, value: bool) -> None:
        self.requested = value

    def snapshot(self) -> tuple[bool, bool]:
        return self.active, self.available


def test_instrument_poll_and_heartbeat_staleness(monkeypatch):
    clients = []

    class Client:
        def __init__(self, host, **kwargs):
            assert host == "10.20.12.20"
            clients.append(self)
            self.closed = False

        def connect(self):
            return True

        def read_holding_registers(self, **kwargs):
            assert kwargs == {"address": 0, "count": 2, "slave": 1}
            return SimpleNamespace(isError=lambda: False, registers=[665, 10])

        def close(self):
            self.closed = True

    monkeypatch.setattr(filtration, "ModbusTcpClient", Client)
    assert filtration.read_instrument("10.20.12.20") == (665, 10)
    assert clients[-1].closed

    class Missing(Client):
        def connect(self):
            return False

    monkeypatch.setattr(filtration, "ModbusTcpClient", Missing)
    with pytest.raises(ConnectionError, match="unavailable"):
        filtration.read_instrument("10.20.12.20")
    assert clients[-1].closed

    class PollFault(Client):
        def read_holding_registers(self, **kwargs):
            raise ModbusException("timeout")

    monkeypatch.setattr(filtration, "ModbusTcpClient", PollFault)
    with pytest.raises(ConnectionError, match="poll failed"):
        filtration.read_instrument("10.20.12.20")
    assert clients[-1].closed

    monitor = filtration.InstrumentFreshness(stale_after=3)
    assert monitor.observe(10, 0)
    assert monitor.observe(10, 2)
    assert not monitor.observe(10, 3)
    assert monitor.observe(11, 3.1)


def test_backwash_uses_threshold_hysteresis_and_timed_rearm():
    cycle = filtration.BackwashCycle(duration=8, hysteresis=100)

    def step(pressure, now):
        return cycle.step(pressure, 650, now, enabled=True, instrument_ready=True)

    assert not step(649, 0)
    assert step(650, 1)
    assert cycle.phase == filtration.BACKWASHING
    assert step(700, 8)
    assert not step(700, 9)
    assert cycle.phase == filtration.WAITING_TO_REARM
    assert not step(600, 10)
    assert not step(550, 11)
    assert cycle.phase == filtration.IDLE
    assert step(650, 12)
    assert not cycle.step(700, 650, 13, enabled=True, instrument_ready=False)
    assert cycle.phase == filtration.WAITING_TO_REARM


def test_backwash_output_interlocks_and_fault_reporting():
    context = filtration.make_context()
    pump = Actuator()
    valve = Actuator()
    alarm = filtration.command_outputs(
        context, pump, valve, True, confirmation_due=True, instrument_ready=True
    )
    assert valve.requested is True
    assert pump.requested is False
    assert alarm & filtration.OUTPUT_UNCONFIRMED
    assert context[0].getValues(3, 2, 1) == [0]

    valve.active = True
    pump.active = True
    alarm = filtration.command_outputs(
        context, pump, valve, True, confirmation_due=True, instrument_ready=True
    )
    assert pump.requested is True
    assert alarm == 0
    assert context[0].getValues(2, 0, 3) == [1, 1, 1]
    assert context[0].getValues(3, 2, 1) == [3]

    valve.available = False
    pump.available = False
    alarm = filtration.command_outputs(
        context, pump, valve, True, confirmation_due=True, instrument_ready=False
    )
    assert alarm & filtration.VALVE_UNAVAILABLE
    assert alarm & filtration.DUTY_UNAVAILABLE
    assert pump.requested is False
    assert context[0].getValues(2, 0, 3) == [0, 0, 0]

    pump.available = True
    valve.available = True
    pump.faulted = True
    valve.faulted = True
    alarm = filtration.command_outputs(
        context, pump, valve, False, confirmation_due=False, instrument_ready=True
    )
    assert alarm & filtration.DUTY_UNAVAILABLE
    assert alarm & filtration.VALVE_UNAVAILABLE
    assert pump.requested is False
