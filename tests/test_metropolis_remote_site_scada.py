"""Read-only SCADA behavior for networked remote sites."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/gns3"))
sys.path.insert(0, str(ROOT / "testbeds/metropolis/devices/runtime"))

spec = importlib.util.spec_from_file_location(
    "remote_site_scada",
    ROOT / "testbeds/metropolis/devices/operations/scada/scada_collector.py",
)
assert spec and spec.loader

scada = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = scada
spec.loader.exec_module(scada)


def test_scada_remote_targets_are_polled_but_never_accept_hmi_commands(monkeypatch):
    targets = scada.configured_targets(
        json.dumps(
            [
                {
                    "id": "reservoir",
                    "host": "192.0.2.10",
                    "profile": "reservoir",
                    "topic": "demo/reservoir/status",
                },
                {
                    "id": "raw-water",
                    "host": "192.0.2.11",
                    "profile": "raw_water",
                    "topic": "demo/raw-water/status",
                },
                {
                    "id": "intake",
                    "host": "192.0.2.12",
                    "profile": "intake",
                    "topic": "demo/intake/status",
                },
            ]
        ),
        legacy_host="unused",
        legacy_topic="unused",
    )
    remote = {
        target.cell: target
        for target in targets
        if target.profile in scada.READ_ONLY_PROFILES
    }
    assert remote == {
        "reservoir": scada.Target(
            "reservoir", "192.0.2.10", "reservoir", "demo/reservoir/status"
        ),
        "raw-water": scada.Target(
            "raw-water", "192.0.2.11", "raw_water", "demo/raw-water/status"
        ),
    }
    bridge = scada.CommandBridge(
        targets,
        SimpleNamespace(publish=lambda *args, **kwargs: None),
        port=502,
        unit_id=1,
    )
    assert scada.command_topic(targets[-1]) in bridge.targets
    for target in remote.values():
        assert scada.command_topic(target) not in bridge.targets
        assert bridge.handle(scada.command_topic(target), b"{}") is None
        with pytest.raises(ValueError, match="does not accept commands"):
            scada.validate_command(
                target, {}, now=scada.datetime.now(scada.timezone.utc)
            )

    class Client:
        def __init__(self, host, **kwargs):
            assert host == "192.0.2.10"
            assert kwargs == {"port": 502, "timeout": 3, "retries": 0}
            self.closed = False

        def connect(self):
            return True

        def read_holding_registers(self, **kwargs):
            assert kwargs == {"address": 0, "count": 6, "slave": 1}
            return SimpleNamespace(
                isError=lambda: False, registers=[642, 1, 1, 7, 1, 0]
            )

        def close(self):
            self.closed = True

    monkeypatch.setattr(scada, "ModbusTcpClient", Client)
    payload = scada.poll_target(remote["reservoir"], port=502, unit_id=1)
    assert payload["reservoir_level_percent"] == 64.2
    assert payload["booster_pump_running"] is True
    assert payload["instrument_ready"] is True
    assert payload["pump_requested"] is True
    assert payload["quality"] == "good"
    assert "setpoint" not in payload


def test_raw_water_decode_uses_site_specific_fields():
    target = scada.Target("raw-water", "192.0.2.11", "raw_water", "status")
    with pytest.raises(ValueError, match="fewer than six"):
        scada.decode_registers(target, [455, 0, 0, 8])
    payload = scada.decode_registers(target, [455, 0, 0, 8, 1, 16])
    assert payload["wet_well_level_percent"] == 45.5
    assert payload["lift_pump_running"] is False
    assert payload["instrument_ready"] is False
    assert payload["pump_requested"] is True
    assert payload["alarm_mask"] == 16
    assert payload["quality"] == "degraded"
    assert "setpoint" not in payload


def test_remote_site_startup_order_starts_instruments_before_rtus_and_scada():
    from lifecycle import TIER_ACTUATOR, TIER_CLIENT, TIER_SERVER, tier_for

    assert tier_for("device", "metropolis/level-instrument:dev") == TIER_ACTUATOR
    assert tier_for("device", "metropolis/actuator:dev") == TIER_ACTUATOR
    assert tier_for("device", "metropolis/controller:dev") == TIER_SERVER
    assert tier_for("device", "metropolis/scada:dev") == TIER_CLIENT
