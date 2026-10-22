"""Wire-level and failover checks for the intake actuator simulation."""

from __future__ import annotations

import importlib.util
import socket
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "testbeds/metropolis/devices/runtime"
ACTUATORS = ROOT / "testbeds/metropolis/devices/field/actuators"
PLC = ROOT / "testbeds/metropolis/devices/controllers/plc"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(ACTUATORS))
sys.path.insert(0, str(PLC))

from actuator import Server, State  # noqa: E402
from ethernet_ip import get_status, set_command  # noqa: E402


def test_actuator_cip_roundtrip() -> None:
    with Server(("127.0.0.1", 0), State("pump_drive")) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        port = server.server_address[1]
        assert get_status("127.0.0.1", port) is False
        assert set_command("127.0.0.1", True, port) is True
        assert get_status("127.0.0.1", port) is True
        assert set_command("127.0.0.1", False, port) is False
        server.shutdown()
        worker.join()


def test_valve_starts_open() -> None:
    assert State("valve_controller").active is True


def test_faulted_drive_rejects_run_command() -> None:
    state = State("pump_drive", faulted=True)
    assert state.handle(bytes((0x10, 3, 0x20, 0x64, 0x24, 1, 0x30, 1, 1)))[4] == 0


def test_plc_fails_over_to_standby(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location(
        "intake_controller", PLC / "modbus_controller.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = module.make_context([650, 120, 950, 0])
    context[0].setValues(module.COILS, 0, [1, 1])
    commands = []

    def command(host, value):
        commands.append((host, value))
        if host == "duty":
            raise socket.timeout("drive unavailable")
        return value

    monkeypatch.setattr(module, "set_command", command)
    monkeypatch.setattr(module, "get_status", lambda host: True)
    assert module.poll_actuators(context, "duty", "standby", "valve") is True
    assert commands == [("duty", True), ("standby", True), ("valve", True)]
    assert context[0].getValues(2, 0, 3) == [0, 1, 1]


def test_plc_fails_over_when_duty_refuses_command(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location(
        "intake_controller_refusal", PLC / "modbus_controller.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = module.make_context([650, 120, 950, 0])
    context[0].setValues(module.COILS, 0, [1, 1])
    commands = []

    def command(host, value):
        commands.append((host, value))
        return False if host == "duty" else value

    monkeypatch.setattr(module, "set_command", command)
    monkeypatch.setattr(module, "get_status", lambda host: True)
    assert module.poll_actuators(context, "duty", "standby", "valve") is True
    assert commands[1] == ("standby", True)
