"""Networked dosing analyzer exposing changing pH and turbidity over Modbus TCP.

Holding registers 0, 1, and 2 are pH x 100, turbidity x 10, and a
one-second heartbeat. The waveform supplies plausible repeated reads and
value changes; it is not a chemical process simulation.
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
log = logging.getLogger("metropolis.analyzer")


def sample(elapsed: float, *, period: float = 90.0) -> tuple[int, int]:
    """Return bounded pH and turbidity readings in register units."""
    phase = 2 * math.pi * elapsed / period
    ph = round(720 + 45 * math.sin(phase))
    turbidity = round(50 + 12 * math.sin(phase / 2 + 0.7))
    return ph, turbidity


def make_context() -> ModbusServerContext:
    ph, turbidity = sample(0)
    device = ModbusSlaveContext(
        hr=ModbusSequentialDataBlock(1, [ph, turbidity, 0] + [0] * 13),
        co=ModbusSequentialDataBlock(1, [0] * 16),
        di=ModbusSequentialDataBlock(1, [0] * 16),
        ir=ModbusSequentialDataBlock(1, [0] * 16),
    )
    return ModbusServerContext(slaves=device, single=True)


def update_registers(context: ModbusServerContext, elapsed: float) -> None:
    ph, turbidity = sample(elapsed)
    context[0].setValues(3, 0, [ph, turbidity, int(elapsed) % 65536])


def main() -> None:
    host = os.getenv("MODBUS_HOST", "0.0.0.0")
    port = int(os.getenv("MODBUS_PORT", "502"))
    interval = float(os.getenv("ANALYZER_INTERVAL", "1"))
    if interval <= 0:
        raise ValueError("ANALYZER_INTERVAL must be positive")
    context = make_context()
    started = time.monotonic()

    def produce() -> None:
        while True:
            time.sleep(interval)
            update_registers(context, time.monotonic() - started)

    threading.Thread(target=produce, daemon=True).start()
    log.info("Networked dosing analyzer listening on %s:%s", host, port)
    StartTcpServer(context, address=(host, port))


if __name__ == "__main__":
    main()
