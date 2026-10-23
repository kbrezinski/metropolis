"""Offline dosing analyzer, PLC, SCADA, and GNS3 planning checks."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEVICES = ROOT / "testbeds/metropolis/devices"
sys.path.insert(0, str(DEVICES / "runtime"))
sys.path.insert(0, str(ROOT / "scripts/gns3"))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


analyzer = load("dosing_analyzer", DEVICES / "field/analyzers/analyzer.py")
dosing = load("dosing_controller", DEVICES / "controllers/plc/dosing_controller.py")
scada = load("dosing_scada", DEVICES / "operations/scada/scada_collector.py")
hmi = load("dosing_hmi", DEVICES / "operations/hmi/hmi_web.py")


class Drive:
    def __init__(self, active: bool, healthy: bool = True):
        self.active = active
        self.healthy = healthy
        self.requested = None

    def command(self, value: bool) -> None:
        self.requested = value

    def snapshot(self) -> tuple[bool, bool]:
        return self.active, self.healthy


def test_analyzer_registers_change_and_stay_in_range():
    context = analyzer.make_context()
    first = context[0].getValues(3, 0, 3)
    assert first == [720, 58, 0]
    analyzer.update_registers(context, 22.5)
    later = context[0].getValues(3, 0, 3)
    assert first != later
    assert 675 <= later[0] <= 765
    assert 38 <= later[1] <= 62
    assert later[2] == 22


def test_dosing_hysteresis_and_drive_failover():
    assert dosing.dosing_demand(690, False)
    assert dosing.dosing_demand(720, True)
    assert not dosing.dosing_demand(720, False)
    assert not dosing.dosing_demand(740, True)
    context = dosing.make_context()
    duty = Drive(False, False)
    standby = Drive(True)
    assert dosing.update_drives(context, duty, standby, True, True)
    assert duty.requested is True and standby.requested is True
    assert context[0].getValues(2, 0, 3) == [0, 1, 1]
    assert not dosing.update_drives(context, duty, standby, True, False)
    assert duty.requested is False and standby.requested is False
    assert context[0].getValues(2, 0, 3) == [0, 1, 0]


def test_dosing_plc_reads_modbus_analyzer_and_rejects_failure(monkeypatch):
    class Client:
        def __init__(self, host, **kwargs):
            assert host == "10.20.11.20"
            self.closed = False

        def connect(self):
            return True

        def read_holding_registers(self, **kwargs):
            assert kwargs == {"address": 0, "count": 3, "slave": 1}
            return SimpleNamespace(isError=lambda: False, registers=[695, 48, 7])

        def close(self):
            self.closed = True

    monkeypatch.setattr(dosing, "ModbusTcpClient", Client)
    assert dosing.read_analyzer("10.20.11.20") == (695, 48, 7)

    class Missing(Client):
        def connect(self):
            return False

    monkeypatch.setattr(dosing, "ModbusTcpClient", Missing)
    with pytest.raises(ConnectionError, match="unavailable"):
        dosing.read_analyzer("10.20.11.20")


def test_scada_configures_distinct_cell_targets_and_decodes_telemetry():
    targets = scada.configured_targets(
        json.dumps(
            [
                {
                    "id": "intake",
                    "host": "192.0.2.10",
                    "profile": "intake",
                    "topic": "demo/intake/status",
                },
                {
                    "id": "dosing",
                    "host": "192.0.2.11",
                    "profile": "dosing",
                    "topic": "demo/dosing/status",
                },
            ]
        ),
        legacy_host="unused",
        legacy_topic="unused",
    )
    assert [(item.cell, item.host) for item in targets] == [
        ("intake", "192.0.2.10"),
        ("dosing", "192.0.2.11"),
    ]
    assert scada.decode_registers(targets[0], [650, 120, 950, 8])["level_percent"] == 65
    dose = scada.decode_registers(targets[1], [695, 48, 650, 9])
    assert dose["ph"] == 6.95
    assert dose["turbidity_ntu"] == 4.8
    assert dose["dose_output_percent"] == 65
    assert targets[0].topic != targets[1].topic


def test_scada_rejects_bad_target_config_and_keeps_single_target_compatibility():
    assert scada.configured_targets(
        None, legacy_host="10.20.10.10", legacy_topic="intake"
    ) == [scada.Target("intake", "10.20.10.10", "intake", "intake")]
    with pytest.raises(ValueError, match="unique"):
        scada.configured_targets(
            json.dumps(
                [
                    {"id": "dosing", "host": "a", "profile": "dosing", "topic": "a"},
                    {"id": "dosing", "host": "b", "profile": "dosing", "topic": "b"},
                ]
            ),
            legacy_host="unused",
            legacy_topic="unused",
        )


def test_dosing_hmi_uses_cell_specific_command():
    state = hmi.HmiState(
        "MET-HMI-DOSING-01",
        65,
        panel_title="Dosing operator panel",
        setpoint_label="Requested dose setpoint",
        setpoint_command="dose_setpoint",
    )
    state.set_setpoint(65)
    assert state.command_payload()["command"] == "dose_setpoint"
    assert state.snapshot()["panel_title"] == "Dosing operator panel"
