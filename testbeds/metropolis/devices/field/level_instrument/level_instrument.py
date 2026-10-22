"""Networked level instrument used at the reservoir and raw-water sites.

Modbus holding register 0 is level in tenths of a percent (0..1000), and
register 1 is a one-second heartbeat. The reading varies independently of
the pump; this model creates observable instrument traffic, not hydraulics.
"""

from __future__ import annotations

import logging
import math
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
log = logging.getLogger("metropolis.level_instrument")


def sample(
    elapsed: float,
    *,
    minimum: int = 400,
    maximum: int = 700,
    period: float = 180.0,
) -> int:
    """Return a slowly changing, bounded synthetic level."""
    if (
        not 0 <= minimum < maximum <= 1000
        or not math.isfinite(period)
        or period <= 0
        or not math.isfinite(elapsed)
        or elapsed < 0
    ):
        raise ValueError("Level range, cycle, and elapsed time must be valid")
    phase = (elapsed % period) / period
    triangle = 1 - abs(2 * phase - 1)
    return round(minimum + (maximum - minimum) * triangle)


def make_context(initial_level: int = 400) -> ModbusServerContext:
    if not 0 <= initial_level <= 1000:
        raise ValueError("Initial level must be within 0..1000")
    device = ModbusSlaveContext(
        hr=ModbusSequentialDataBlock(1, [initial_level, 0] + [0] * 14),
        co=ModbusSequentialDataBlock(1, [0] * 16),
        di=ModbusSequentialDataBlock(1, [0] * 16),
        ir=ModbusSequentialDataBlock(1, [0] * 16),
    )
    return ModbusServerContext(slaves=device, single=True)


def update_registers(
    context: ModbusServerContext,
    elapsed: float,
    *,
    minimum: int = 400,
    maximum: int = 700,
    period: float = 180.0,
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
    interval = float(os.getenv("LEVEL_UPDATE_INTERVAL", "1"))
    minimum = int(os.getenv("LEVEL_MIN_X10", "400"))
    maximum = int(os.getenv("LEVEL_MAX_X10", "700"))
    period = float(os.getenv("LEVEL_CYCLE_SECONDS", "180"))
    if not 0 < interval <= 1:
        raise ValueError("LEVEL_UPDATE_INTERVAL must be within (0, 1] seconds")
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
    log.info("Level instrument listening on %s:%s", host, port)
    StartTcpServer(context, address=(host, port))


if __name__ == "__main__":
    main()
