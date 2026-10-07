"""Checks for the PLC process model and the sensor's coupling to it.

The model is a pure function, so its behaviour is pinned here without a server.
The coupling check starts a real controller on loopback and reads it back,
because the point of the model is that the sensor's published value and the
PLC's registers agree.
"""

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


def step(model, state, seconds, pump_on):
    return model.update(state, seconds, pump_on)


def test_update_is_pure(model):
    """The same inputs must always give the same result."""
    start = model.initial(650, 120, 950)
    assert step(model, start, 1.0, True) == step(model, start, 1.0, True)


def test_the_pump_raises_the_level_and_the_flow_register(model):
    start = model.initial(650, 120, 950)
    # A short interval keeps the level inside its bounds, so this measures the
    # pump's effect rather than the clamp.
    with_pump = step(model, start, 1.0, True)
    without = step(model, start, 1.0, False)

    assert with_pump.level > start.level
    assert without.level < start.level
    assert with_pump.flow == model.FLOW_PUMP_ON
    assert without.flow == model.FLOW_PUMP_OFF


def test_the_level_settles_instead_of_draining_or_overflowing(model):
    """Left alone in one pump state, the level must reach a steady point."""
    state = model.initial(650, 120, 950)
    for _ in range(600):
        state = step(model, state, 1.0, False)
    off_settled = state.level

    state = model.initial(650, 120, 950)
    for _ in range(900):
        state = step(model, state, 1.0, True)
    on_settled = state.level

    assert model.LEVEL_MIN < off_settled < model.LEVEL_MAX
    assert off_settled < on_settled
    # A further second must no longer move the level appreciably.
    assert abs(step(model, state, 1.0, True).level - on_settled) <= 2


def test_values_stay_inside_the_documented_bounds(model):
    state = model.initial(1, 0, 0)
    for pump in (True, False):
        for _ in range(2000):
            state = step(model, state, 5.0, pump)
        assert model.LEVEL_MIN <= state.level <= model.LEVEL_MAX
        assert model.FLOW_MIN <= state.flow <= model.FLOW_MAX
        assert model.QUALITY_MIN <= state.quality <= model.QUALITY_MAX


def test_quality_moves_in_both_directions(model):
    """Quality must change, or a constant would be the detector's give-away."""
    state = model.initial(650, 120, 9500)
    rising = step(model, state, 60.0, True).quality
    falling = step(model, state, 60.0, False).quality
    assert rising > state.quality
    assert falling < state.quality


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


def test_the_process_advances_a_fraction_at_a_time(model):
    """A single tick moves less than one tenth, and must not be swallowed.

    Rounding the state every tick stalled the process at its starting value,
    which is why the state keeps fractions.
    """
    start = model.initial(650, 120, 950)
    after = step(model, start, 0.1, False)
    assert after.level != start.level
    assert start.level - after.level < 1.0


def test_an_outside_write_moves_the_process_and_is_steered_back(model):
    """A write is a real perturbation, not a value the model accepts."""
    written = model.initial(999, 120, 9500)
    after = step(model, written, 1.0, False)
    # The level falls from the manipulated value rather than staying put.
    assert after.level < written.level


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


def test_the_sensor_reads_the_same_value_the_controller_holds(controller):
    """The sensor's reading and the PLC register must agree, which is the point."""
    client, port = controller
    reader = load("mqtt_sensor", SENSORS / "mqtt_sensor.py").ProcessReader(
        host="127.0.0.1", port=port, unit_id=1, divisor=10
    )

    # Read the pair as closely as possible; the process ticks every 0.1s, so
    # compare against the register either side of the sensor's read.
    before = client.read_holding_registers(0, count=1, slave=1).registers[0]
    observed = reader()
    after = client.read_holding_registers(0, count=1, slave=1).registers[0]

    assert min(before, after) <= observed["level"] * 10 <= max(before, after)


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
