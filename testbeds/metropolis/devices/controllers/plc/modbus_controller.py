"""Modbus TCP process controller model for the Metropolis lab.

The holding registers are not static. A small coupled process model drives
level, flow, and quality from the pump coil and from each other, so telemetry
moves the way a plant's does and the sensor's readings agree with the
registers. Holding register 4 stores the operator setpoint and register 5
exposes communication and actuator alarms. See ``process_model.py`` for the
model and its bounds.
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
from ethernet_ip import CyclicActuator  # noqa: E402
from alarms import (  # noqa: E402
    DUTY_UNAVAILABLE,
    OUTPUT_UNCONFIRMED,
    STANDBY_UNAVAILABLE,
    VALVE_UNAVAILABLE,
    alarm_changes,
)


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


def make_context(values: list[int], *, setpoint_x10: int = 650) -> ModbusServerContext:
    if not 0 <= setpoint_x10 <= 1000:
        raise ValueError("Intake setpoint must be within 0..1000")
    device = ModbusSlaveContext(
        di=ModbusSequentialDataBlock(1, [0] * 16),
        co=ModbusSequentialDataBlock(1, [0] * 16),
        hr=ModbusSequentialDataBlock(1, values + [setpoint_x10, 0] + [0] * 10),
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


def update_actuators(
    context: ModbusServerContext,
    duty: CyclicActuator,
    standby: CyclicActuator,
    valve: CyclicActuator,
) -> bool:
    """Set next cyclic outputs and publish confirmed intake I/O states."""
    requested = pump_is_on(context)
    duty.command(requested)
    duty_running, duty_healthy = duty.snapshot()
    duty_running = duty_running and duty_healthy
    standby_requested = requested and not duty_running
    standby.command(standby_requested)
    standby_running, standby_healthy = standby.snapshot()
    standby_running = standby_running and standby_healthy
    valve_request = bool(context[0].getValues(COILS, 1, 1)[0])
    valve.command(valve_request)
    valve_open, valve_healthy = valve.snapshot()
    valve_open = valve_open and valve_healthy

    alarm_mask = 0
    if (
        not duty_healthy
        or getattr(duty, "faulted", False)
        or (requested and not duty_running)
    ):
        alarm_mask |= DUTY_UNAVAILABLE
    if (
        not standby_healthy
        or getattr(standby, "faulted", False)
        or (standby_requested and not standby_running)
    ):
        alarm_mask |= STANDBY_UNAVAILABLE
    if (
        not valve_healthy
        or getattr(valve, "faulted", False)
        or (valve_request and not valve_open)
    ):
        alarm_mask |= VALVE_UNAVAILABLE
    if (requested and not (duty_running or standby_running)) or (
        valve_request and not valve_open
    ):
        alarm_mask |= OUTPUT_UNCONFIRMED
    previous = read_holding(context, 5, 1)[0]
    if alarm_mask != previous:
        write_holding(context, 5, [alarm_mask])
        raised, cleared = alarm_changes(previous, alarm_mask)
        for name in raised:
            log.warning("alarm_raised=%s", name)
        for name in cleared:
            log.info("alarm_cleared=%s", name)
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

    context = make_context(
        values, setpoint_x10=int(os.getenv("INTAKE_SETPOINT_X10", "650"))
    )
    duty = os.getenv("EIP_DUTY_HOST")
    standby = os.getenv("EIP_STANDBY_HOST")
    valve = os.getenv("EIP_VALVE_HOST")
    networked = bool(duty and standby and valve)
    io_clients: tuple[CyclicActuator, CyclicActuator, CyclicActuator] | None = None
    if networked:
        # An intake valve starts open; coil 1 remains the operator command.
        context[0].setValues(COILS, 1, [1])
        rpi_ms = int(os.getenv("EIP_RPI_MS", "100"))
        io_port = int(os.getenv("EIP_IO_PORT", "2222"))
        io_clients = tuple(
            CyclicActuator(address, rpi_ms=rpi_ms, io_port=io_port)
            for address in (duty, standby, valve)
        )
        io_clients[2].command(True)
        for client in io_clients:
            client.start()
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
        while True:
            time.sleep(TICK_SECONDS)
            if io_clients is not None:
                actual_pump = update_actuators(context, *io_clients)
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
    try:
        StartTcpServer(context, address=(host, port))
    finally:
        if io_clients is not None:
            for client in io_clients:
                client.close()


if __name__ == "__main__":
    cell_role = os.getenv("CELL_ROLE")
    if cell_role == "dosing":
        from dosing_controller import main as dosing_main

        dosing_main()
    elif cell_role == "filtration":
        from filtration_controller import main as filtration_main

        filtration_main()
    elif cell_role == "disinfection":
        from disinfection_controller import main as disinfection_main

        disinfection_main()
    elif cell_role == "remote_site":
        from remote_site_controller import main as remote_site_main

        remote_site_main()
    else:
        main()
