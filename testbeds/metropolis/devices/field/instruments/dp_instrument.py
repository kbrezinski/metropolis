"""Networked filter differential-pressure instrument on Modbus TCP.

Holding register 0 is pressure drop across the filter in tenths of a kPa;
register 1 is a one-second heartbeat. The sawtooth is independent of PLC
commands: it creates repeated traffic and backwash triggers, not hydraulics.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import StartTcpServer

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("metropolis.dp_instrument")


def sample(
    elapsed: float,
    *,
    minimum: int = 250,
    maximum: int = 800,
    period: float = 120.0,
) -> int:
    """Return a rising pressure drop that resets at each synthetic cycle."""
    if minimum < 0 or maximum <= minimum or period <= 0:
        raise ValueError("DP range and cycle period must be positive and ordered")
    return round(minimum + (maximum - minimum) * ((elapsed % period) / period))


def make_context(initial_pressure: int = 250) -> ModbusServerContext:
    device = ModbusSlaveContext(
        hr=ModbusSequentialDataBlock(1, [initial_pressure, 0] + [0] * 14),
        co=ModbusSequentialDataBlock(1, [0] * 16),
        di=ModbusSequentialDataBlock(1, [0] * 16),
        ir=ModbusSequentialDataBlock(1, [0] * 16),
    )
    return ModbusServerContext(slaves=device, single=True)


def update_registers(
    context: ModbusServerContext,
    elapsed: float,
    *,
    minimum: int = 250,
    maximum: int = 800,
    period: float = 120.0,
) -> None:
    context[0].setValues(
        3,
        0,
        [
            sample(elapsed, minimum=minimum, maximum=maximum, period=period),
            int(elapsed) % 65536,
        ],
    )


def main() -> None:
    host = os.getenv("MODBUS_HOST", "0.0.0.0")
    port = int(os.getenv("MODBUS_PORT", "502"))
    interval = float(os.getenv("DP_UPDATE_INTERVAL", "1"))
    minimum = int(os.getenv("DP_MIN_X10", "250"))
    maximum = int(os.getenv("DP_MAX_X10", "800"))
    period = float(os.getenv("DP_CYCLE_SECONDS", "120"))
    if interval <= 0:
        raise ValueError("DP_UPDATE_INTERVAL must be positive")
    sample(0, minimum=minimum, maximum=maximum, period=period)
    context = make_context(minimum)
    started = time.monotonic()

    def produce() -> None:
        while True:
            time.sleep(interval)
            update_registers(
                context,
                time.monotonic() - started,
                minimum=minimum,
                maximum=maximum,
                period=period,
            )

    threading.Thread(target=produce, daemon=True).start()
    log.info("Differential-pressure instrument listening on %s:%s", host, port)
    StartTcpServer(context, address=(host, port))


if __name__ == "__main__":
    main()
