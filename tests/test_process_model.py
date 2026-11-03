"""Checks loopback sensor fallback when its controller is unavailable."""

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


def test_the_sensor_keeps_its_last_reading_when_the_plc_is_gone(controller):
    client, port = controller
    reader = load("mqtt_sensor_keep", SENSORS / "mqtt_sensor.py").ProcessReader(
        host="127.0.0.1", port=port, unit_id=1, divisor=10
    )
    good = reader()

    # Point the same reader at a port with nothing on it.
    reader.port = free_port()
    assert reader() == good
