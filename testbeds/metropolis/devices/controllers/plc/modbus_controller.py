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
from process_model import ProcessState, update  # noqa: E402


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


def main() -> None:
    name = os.getenv("DEVICE_NAME", "MET-PLC-INTAKE-01")
    role = os.getenv("DEVICE_ROLE", "PLC")
    host = os.getenv("MODBUS_HOST", "0.0.0.0")
    port = int(os.getenv("MODBUS_PORT", "502"))
    # The PROCESS_* settings are stated in engineering units, matching the
    # register map's "x10" column: 650 is 65.0 percent. Registers hold tenths,
    # so they are converted here rather than seeded with the raw setting.
    level_tenths = int(round(float(os.getenv("PROCESS_LEVEL", "650")) * 10))
    flow_tenths = int(round(float(os.getenv("PROCESS_FLOW", "120")) * 10))
    quality_tenths = int(round(float(os.getenv("PROCESS_QUALITY", "950")) * 10))
    values = [level_tenths, flow_tenths, quality_tenths, 0]

    context = make_context(values)
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
        while True:
            time.sleep(TICK_SECONDS)
            # Feed the process the current registers so an outside write moves
            # it, then let it evolve from there.
            current = read_holding(context, 0, 3)
            state = ProcessState(
                level=float(current[0]),
                flow=float(current[1]),
                quality=float(current[2]),
            )
            state = update(state, TICK_SECONDS, pump_is_on(context))
            write_holding(context, 0, state.registers())
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
