"""Setpoint, stale-instrument, and confirmed-output behavior of the PLCs."""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEVICES = ROOT / "testbeds/metropolis/devices"
sys.path.insert(0, str(DEVICES / "runtime"))
sys.path.insert(0, str(DEVICES / "controllers/plc"))

from alarms import (  # noqa: E402
    DUTY_UNAVAILABLE,
    INSTRUMENT_STALE,
    OUTPUT_UNCONFIRMED,
    STANDBY_UNAVAILABLE,
    VALVE_UNAVAILABLE,
)


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


intake = load("fault_intake", DEVICES / "controllers/plc/modbus_controller.py")
dosing = load("fault_dosing", DEVICES / "controllers/plc/dosing_controller.py")


class Drive:
    def __init__(self, active: bool, available: bool = True, *, faulted: bool = False):
        self.active = active
        self.available = available
        self.faulted = faulted
        self.requested: bool | None = None

    def command(self, value: bool) -> None:
        self.requested = value

    def snapshot(self) -> tuple[bool, bool]:
        return self.active, self.available


def test_intake_setpoint_readback_and_duty_alarm_recovery(caplog):
    context = intake.make_context([650, 120, 950, 0])
    assert intake.read_holding(context, 4, 2) == [650, 0]
    intake.write_holding(context, 4, [420])
    assert intake.read_holding(context, 4, 1) == [420]
    context[0].setValues(intake.COILS, 0, [1, 1])
    duty, standby, valve = Drive(False, False), Drive(True), Drive(True)

    with caplog.at_level(logging.INFO):
        assert intake.update_actuators(context, duty, standby, valve)
        assert intake.read_holding(context, 5, 1) == [DUTY_UNAVAILABLE]
        assert intake.update_actuators(context, duty, standby, valve)
        duty.active, duty.available = True, True
        standby.active = False
        assert intake.update_actuators(context, duty, standby, valve)

    assert intake.read_holding(context, 5, 1) == [0]
    assert (
        sum("alarm_raised=duty_unavailable" in item.message for item in caplog.records)
        == 1
    )
    assert (
        sum("alarm_cleared=duty_unavailable" in item.message for item in caplog.records)
        == 1
    )


def test_intake_unconfirmed_valve_is_visible_in_alarm_register():
    context = intake.make_context([650, 120, 950, 0])
    context[0].setValues(intake.COILS, 0, [1, 1])
    assert not intake.update_actuators(context, Drive(True), Drive(False), Drive(False))
    assert intake.read_holding(context, 5, 1) == [
        VALVE_UNAVAILABLE | OUTPUT_UNCONFIRMED
    ]


def test_frozen_analyzer_heartbeat_is_stale_and_recovers():
    monitor = dosing.AnalyzerHeartbeatMonitor(stale_seconds=4)
    assert monitor.observe(65535, now=0)
    assert monitor.observe(65535, now=3.9)
    assert not monitor.observe(65535, now=4.0)
    assert monitor.observe(0, now=4.1)  # Counter rollover is an update.
    assert monitor.observe(0, now=7.0)


def test_dosing_setpoint_and_alarm_mask_follow_confirmation():
    context = dosing.make_context()
    assert context[0].getValues(3, 4, 2) == [650, 0]
    context[0].setValues(3, 4, [375])
    duty, standby = Drive(True), Drive(False)
    assert dosing.update_drives(context, duty, standby, True, True)
    assert dosing.dose_output_x10(context, True) == 375
    assert context[0].getValues(3, 5, 1) == [0]

    duty.active = False
    assert not dosing.update_drives(
        context, duty, standby, True, False, INSTRUMENT_STALE
    )
    assert dosing.dose_output_x10(context, False) == 0
    assert context[0].getValues(3, 5, 1) == [INSTRUMENT_STALE]

    duty.available = False
    standby.available = False
    assert not dosing.update_drives(context, duty, standby, True, True)
    assert context[0].getValues(3, 5, 1) == [
        DUTY_UNAVAILABLE | STANDBY_UNAVAILABLE | OUTPUT_UNCONFIRMED
    ]
