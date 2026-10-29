"""Focused checks for the residual analyzer's Modbus register contract."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "testbeds/metropolis/devices/field/analyzers/analyzer.py"
SPEC = importlib.util.spec_from_file_location("metropolis_residual_analyzer", PATH)
assert SPEC and SPEC.loader
analyzer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = analyzer
SPEC.loader.exec_module(analyzer)


def test_chlorine_registers_and_heartbeat_are_independent():
    context = analyzer.make_context("chlorine")
    assert context[0].getValues(3, 0, 2) == [10, 0]

    analyzer.update_registers(context, 1, "chlorine")
    assert context[0].getValues(3, 0, 2) == [10, 1]

    analyzer.update_registers(context, 45, "chlorine")
    assert context[0].getValues(3, 0, 2) == [12, 45]
