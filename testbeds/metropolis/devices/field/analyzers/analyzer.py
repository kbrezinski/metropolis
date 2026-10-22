"""Networked dosing or chlorine analyzer over Modbus TCP.

The dosing role exposes pH x 100, turbidity x 10, and a heartbeat in holding
registers 0..2. The chlorine role exposes residual x 10 mg/L and a heartbeat
in registers 0..1. Values change independently of the actuators; neither role
models the physical process.
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


def sample_chlorine(elapsed: float) -> int:
    """Return a small changing residual value in tenths of mg/L."""
    return round(10 + 2 * math.sin(2 * math.pi * elapsed / 180))


def registers(role: str, elapsed: float) -> list[int]:
    heartbeat = int(elapsed) % 65536
    if role == "dosing":
        ph, turbidity = sample(elapsed)
        return [ph, turbidity, heartbeat]
    if role == "chlorine":
        return [sample_chlorine(elapsed), heartbeat]
    raise ValueError(f"Unsupported analyzer role: {role}")


def make_context(role: str = "dosing") -> ModbusServerContext:
    initial = registers(role, 0)
    device = ModbusSlaveContext(
        hr=ModbusSequentialDataBlock(1, initial + [0] * (16 - len(initial))),
        co=ModbusSequentialDataBlock(1, [0] * 16),
        di=ModbusSequentialDataBlock(1, [0] * 16),
        ir=ModbusSequentialDataBlock(1, [0] * 16),
    )
    return ModbusServerContext(slaves=device, single=True)


def update_registers(
    context: ModbusServerContext, elapsed: float, role: str = "dosing"
) -> None:
    context[0].setValues(3, 0, registers(role, elapsed))


def main() -> None:
    role = os.getenv("ANALYZER_ROLE", "dosing")
    host = os.getenv("MODBUS_HOST", "0.0.0.0")
    port = int(os.getenv("MODBUS_PORT", "502"))
    interval = float(os.getenv("ANALYZER_INTERVAL", "1"))
    if interval <= 0:
        raise ValueError("ANALYZER_INTERVAL must be positive")
    context = make_context(role)
    started = time.monotonic()

    def produce() -> None:
        while True:
            time.sleep(interval)
            update_registers(context, time.monotonic() - started, role)

    threading.Thread(target=produce, daemon=True).start()
    log.info("Networked %s analyzer listening on %s:%s", role, host, port)
    StartTcpServer(context, address=(host, port))


if __name__ == "__main__":
    main()
