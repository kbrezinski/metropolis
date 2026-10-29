"""Focused checks for the remote-site networked level instrument."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "testbeds/metropolis/devices/field/level_instrument/level_instrument.py"
spec = importlib.util.spec_from_file_location("metropolis_level_instrument", SOURCE)
assert spec and spec.loader
instrument = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = instrument
spec.loader.exec_module(instrument)


def test_modbus_level_and_heartbeat_registers():
    context = instrument.make_context()
    assert context[0].getValues(3, 0, 2) == [400, 0]
    instrument.update_registers(context, 1)
    assert context[0].getValues(3, 0, 2) == [403, 1]
    instrument.update_registers(context, 2)
    assert context[0].getValues(3, 0, 2) == [407, 2]
    instrument.update_registers(context, 65537)
    assert context[0].getValues(3, 1, 1) == [1]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"minimum": -1},
        {"period": 0},
        {"elapsed": -1},
    ],
)
def test_invalid_level_settings_are_rejected(kwargs):
    settings = dict(kwargs)
    elapsed = settings.pop("elapsed", 0)
    with pytest.raises(ValueError, match="Level range"):
        instrument.sample(elapsed, **settings)
