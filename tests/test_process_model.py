"""Checks the PLC register model and loopback controller/sensor behavior."""

from __future__ import annotations

import importlib.util
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLC = ROOT / "testbeds/metropolis/devices/controllers/plc"
SENSORS = ROOT / "testbeds/metropolis/devices/field/sensors"


def load(name: str, path: Path):
    """Load a module by path, registering it so dataclasses resolve.

    The sensor imports its CoAP sibling by name, so its own directory has to be
    importable first.
    """
    directory = str(path.parent)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    return module


@pytest.fixture(scope="module")
def model():
    return load("process_model", PLC / "process_model.py")


def test_the_registers_are_the_state(model):
    """The registers are the rounded state, which is what a client reads."""
    state = model.ProcessState(level=650.0, flow=120.0, quality=950.0)
    assert state.registers() == [650, 120, 950]
    # Fractional state rounds into the register rather than being lost.
    assert (
        model.ProcessState(level=650.4, flow=120.0, quality=950.0).registers()[0] == 650
    )
    assert (
        model.ProcessState(level=650.6, flow=120.0, quality=950.0).registers()[0] == 651
    )


# -- coupling with the sensor -------------------------------------------------


def free_port() -> int:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


@pytest.fixture
def controller():
    """A real controller on loopback, as the test_attack_tools suite starts it."""
    pytest.importorskip("pymodbus")
    port = free_port()
    env = {
        **__import__("os").environ,
        "MODBUS_HOST": "127.0.0.1",
        "MODBUS_PORT": str(port),
    }
    process = subprocess.Popen(
        [sys.executable, str(PLC / "modbus_controller.py")],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    from pymodbus.client import ModbusTcpClient

    client = ModbusTcpClient("127.0.0.1", port=port, timeout=1, retries=0)
    for _ in range(50):
        if client.connect():
            break
        if process.poll() is not None:
            pytest.fail("Controller startup failed")
        time.sleep(0.1)
    yield client, port
    client.close()
    process.terminate()
    process.wait(timeout=5)


def test_controller_registers_are_live(controller):
    """The registers must change on their own, which they did not before."""
    client, _ = controller
    first = client.read_holding_registers(0, count=3, slave=1)
    assert not first.isError()
    time.sleep(2.0)
    second = client.read_holding_registers(0, count=3, slave=1)
    assert not second.isError()
    # Flow tracks the pump, which the control client cycles; at least one of the
    # three values should have moved within two seconds.
    assert first.registers != second.registers


def test_controller_heartbeat_still_counts_seconds(controller):
    client, _ = controller
    first = client.read_holding_registers(3, count=1, slave=1).registers[0]
    time.sleep(2.2)
    second = client.read_holding_registers(3, count=1, slave=1).registers[0]
    assert 1 <= second - first <= 4


def test_the_sensor_reports_every_measurement(controller):
    """One reading per CoAP resource, all from the same PLC sample."""
    _, port = controller
    reader = load("mqtt_sensor_multi", SENSORS / "mqtt_sensor.py").ProcessReader(
        host="127.0.0.1", port=port, unit_id=1, divisor=10
    )
    values = reader()

    assert set(values) == {
        "level",
        "flow",
        "quality",
        "pressure",
        "temperature",
        "turbidity",
        "pump",
    }
    assert all(isinstance(value, (int, float)) for value in values.values())


def test_the_sensor_keeps_its_last_reading_when_the_plc_is_gone(controller):
    client, port = controller
    reader = load("mqtt_sensor_keep", SENSORS / "mqtt_sensor.py").ProcessReader(
        host="127.0.0.1", port=port, unit_id=1, divisor=10
    )
    good = reader()

    # Point the same reader at a port with nothing on it.
    reader.port = free_port()
    assert reader() == good
