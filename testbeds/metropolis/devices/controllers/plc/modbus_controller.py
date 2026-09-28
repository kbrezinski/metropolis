"""Simple Modbus TCP process controller model for the Metropolis lab."""

from __future__ import annotations

import logging
import os
import threading
import time

from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext, ModbusSlaveContext
from pymodbus.server import StartTcpServer


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("metropolis.controller")


def main() -> None:
    name = os.getenv("DEVICE_NAME", "MET-PLC-INTAKE-01")
    role = os.getenv("DEVICE_ROLE", "PLC")
    host = os.getenv("MODBUS_HOST", "0.0.0.0")
    port = int(os.getenv("MODBUS_PORT", "502"))
    values = [
        int(float(os.getenv("PROCESS_LEVEL", "650"))),
        int(float(os.getenv("PROCESS_FLOW", "120"))),
        int(float(os.getenv("PROCESS_QUALITY", "950"))),
        0,
    ]

    device = ModbusSlaveContext(
        di=ModbusSequentialDataBlock(0, [0] * 16),
        co=ModbusSequentialDataBlock(0, [0] * 16),
        hr=ModbusSequentialDataBlock(0, values + [0] * 12),
        ir=ModbusSequentialDataBlock(0, [0] * 16),
        zero_mode=True,
    )
    context = ModbusServerContext(slaves=device, single=True)

    def heartbeat() -> None:
        count = 0
        while True:
            count = (count + 1) % 65536
            context[0].setValues(3, 3, [count])
            time.sleep(1)

    threading.Thread(target=heartbeat, daemon=True).start()
    log.info("Starting %s model %s on %s:%s", role, name, host, port)
    StartTcpServer(context, address=(host, port))


if __name__ == "__main__":
    main()
