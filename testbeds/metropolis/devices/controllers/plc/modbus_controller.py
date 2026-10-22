"""Modbus TCP process controller model for the Metropolis lab.

The holding registers are not static. A small coupled process model drives
level, flow, and quality from the pump coil and from each other, so telemetry
moves the way a plant's does and the sensor's readings agree with the
registers. See ``process_model.py`` for the model and its bounds.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
import sys

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import StartTcpServer

# The model is a sibling module; the image runs this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runtime"))
from process_model import ProcessState, update  # noqa: E402
from ethernet_ip import get_status, set_command  # noqa: E402


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("metropolis.controller")

# Function codes used with the datastore.
COILS = 1
HOLDING = 3

# How often the process advances. Short enough that a polling client sees the
# level change rather than sampling the same value twice.
TICK_SECONDS = 0.1

# The datastore blocks start at 1, and PyModbus addresses them 1:1 with the
# documented register map: a client reading register 0 reads datastore address
# 0. The helpers below take the documented register number so the mapping lives
# in one place rather than at every call site.
DATASTORE_OFFSET = 0


def make_context(values: list[int]) -> ModbusServerContext:
    device = ModbusSlaveContext(
        di=ModbusSequentialDataBlock(1, [0] * 16),
        co=ModbusSequentialDataBlock(1, [0] * 16),
        hr=ModbusSequentialDataBlock(1, values + [0] * 12),
        ir=ModbusSequentialDataBlock(1, [0] * 16),
    )
    return ModbusServerContext(slaves=device, single=True)


def read_holding(context: ModbusServerContext, address: int, count: int) -> list[int]:
    """Read holding registers by the documented register number."""
    return context[0].getValues(HOLDING, address + DATASTORE_OFFSET, count)


def write_holding(
    context: ModbusServerContext, address: int, values: list[int]
) -> None:
    """Write holding registers by the documented register number."""
    context[0].setValues(HOLDING, address + DATASTORE_OFFSET, values)


def pump_is_on(context: ModbusServerContext) -> bool:
    """The pump-run coil, at documented coil address 0."""
    return bool(context[0].getValues(COILS, 0 + DATASTORE_OFFSET, 1)[0])


def poll_actuators(
    context: ModbusServerContext, duty: str, standby: str, valve: str
) -> bool:
    """Command duty drive, fail over on lost response, and return actual flow state."""
    requested = pump_is_on(context)
    try:
        duty_running = set_command(duty, requested) and get_status(duty)
        duty_ok = not requested or duty_running
    except (OSError, ValueError, ConnectionError) as exc:
        log.warning("Duty drive unavailable: %s", exc)
        duty_running, duty_ok = False, False
    try:
        standby_running = set_command(
            standby, requested and not duty_ok
        ) and get_status(standby)
    except (OSError, ValueError, ConnectionError) as exc:
        log.warning("Standby drive unavailable: %s", exc)
        standby_running = False
    valve_request = bool(context[0].getValues(COILS, 1, 1)[0])
    try:
        valve_open = set_command(valve, valve_request) and get_status(valve)
    except (OSError, ValueError, ConnectionError) as exc:
        log.warning("Valve controller unavailable: %s", exc)
        valve_open = False
    # Discrete inputs expose confirmed duty, standby, and valve status to SCADA.
    context[0].setValues(
        2, 0, [int(duty_running), int(standby_running), int(valve_open)]
    )
    return (duty_running or standby_running) and valve_open


def main() -> None:
    name = os.getenv("DEVICE_NAME", "MET-PLC-INTAKE-01")
    role = os.getenv("DEVICE_ROLE", "PLC")
    host = os.getenv("MODBUS_HOST", "0.0.0.0")
    port = int(os.getenv("MODBUS_PORT", "502"))
    # PROCESS_* settings are already register values in tenths: 650 is 65.0%.
    level_tenths = int(round(float(os.getenv("PROCESS_LEVEL", "650"))))
    flow_tenths = int(round(float(os.getenv("PROCESS_FLOW", "120"))))
    quality_tenths = int(round(float(os.getenv("PROCESS_QUALITY", "950"))))
    values = [level_tenths, flow_tenths, quality_tenths, 0]

    context = make_context(values)
    duty = os.getenv("EIP_DUTY_HOST")
    standby = os.getenv("EIP_STANDBY_HOST")
    valve = os.getenv("EIP_VALVE_HOST")
    networked = bool(duty and standby and valve)
    if networked:
        # An intake valve starts open; coil 1 remains the operator command.
        context[0].setValues(COILS, 1, [1])
    state = ProcessState(
        level=float(level_tenths),
        flow=float(flow_tenths),
        quality=float(quality_tenths),
    )

    def clock() -> None:
        """Advance the process, then the heartbeat.

        The process model owns registers 0 to 2 and overwrites whatever an
        external writer put there, the way a plant responds to a manipulated
        reading rather than accepting it. Register 3 is the controller's own
        counter and increments once per second, as the register map documents.
        """
        nonlocal state
        count = 0
        since_heartbeat = 0.0
        last_published = state.registers()
        actual_pump = False
        poll_elapsed = 1.0
        while True:
            time.sleep(TICK_SECONDS)
            if networked:
                poll_elapsed += TICK_SECONDS
                if poll_elapsed >= 1.0:
                    actual_pump = poll_actuators(context, duty, standby, valve)
                    poll_elapsed = 0.0
            # Preserve fractional motion between ticks. Only reload rounded
            # register values when an outside writer has changed them.
            current = read_holding(context, 0, 3)
            if current != last_published:
                state = ProcessState(
                    level=float(current[0]),
                    flow=float(current[1]),
                    quality=float(current[2]),
                )
            state = update(
                state, TICK_SECONDS, actual_pump if networked else pump_is_on(context)
            )
            last_published = state.registers()
            write_holding(context, 0, last_published)
            since_heartbeat += TICK_SECONDS
            if since_heartbeat >= 1.0:
                since_heartbeat -= 1.0
                count = (count + 1) % 65536
                write_holding(context, 3, [count])

    threading.Thread(target=clock, daemon=True).start()
    log.info("Starting %s model %s on %s:%s", role, name, host, port)
    StartTcpServer(context, address=(host, port))


if __name__ == "__main__":
    main()
