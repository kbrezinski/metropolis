"""Dosing PLC: Modbus analyzer polling and cyclic duty/standby drive I/O.

PLC holding registers 0..3 expose pH x 100, turbidity x 10, commanded
dose output x 10, and a heartbeat. Discrete inputs 0..2 report confirmed
duty drive, standby drive, and analyzer availability. Holding register 4 is a
writable dose setpoint x 10; register 5 is the alarm bitmask. Coil 0 enables
dosing.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from pymodbus.client import ModbusTcpClient
from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.exceptions import ModbusException
from pymodbus.server import StartTcpServer

from ethernet_ip import CyclicActuator
from alarms import (
    DUTY_UNAVAILABLE,
    INSTRUMENT_STALE,
    INSTRUMENT_UNAVAILABLE,
    OUTPUT_UNCONFIRMED,
    STANDBY_UNAVAILABLE,
    alarm_changes,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("metropolis.dosing_plc")


def make_context(*, setpoint_x10: int = 650) -> ModbusServerContext:
    if not 0 <= setpoint_x10 <= 1000:
        raise ValueError("Dosing setpoint must be within 0..1000")
    device = ModbusSlaveContext(
        hr=ModbusSequentialDataBlock(1, [720, 50, 0, 0, setpoint_x10, 0] + [0] * 10),
        co=ModbusSequentialDataBlock(1, [1] + [0] * 15),
        di=ModbusSequentialDataBlock(1, [0] * 16),
        ir=ModbusSequentialDataBlock(1, [0] * 16),
    )
    return ModbusServerContext(slaves=device, single=True)


class AnalyzerHeartbeatMonitor:
    """Reject a readable analyzer whose heartbeat has stopped advancing."""

    def __init__(self, stale_seconds: float = 4.0) -> None:
        if stale_seconds <= 0:
            raise ValueError("ANALYZER_STALE_SECONDS must be positive")
        self.stale_seconds = stale_seconds
        self.last_heartbeat: int | None = None
        self.last_change: float | None = None

    def observe(self, heartbeat: int, now: float) -> bool:
        if heartbeat != self.last_heartbeat or self.last_change is None:
            self.last_heartbeat = heartbeat
            self.last_change = now
        return now - self.last_change < self.stale_seconds


def read_analyzer(host: str, *, port: int = 502) -> tuple[int, int, int]:
    """Read the analyzer's pH, turbidity, and heartbeat via Modbus TCP."""
    client = ModbusTcpClient(host, port=port, timeout=0.6, retries=0)
    try:
        if not client.connect():
            raise ConnectionError(f"Analyzer {host}:{port} unavailable")
        response = client.read_holding_registers(address=0, count=3, slave=1)
        if response.isError() or len(response.registers) != 3:
            raise ValueError("Analyzer returned invalid Modbus registers")
        ph, turbidity, heartbeat = response.registers
        if not 0 <= ph <= 1400 or not 0 <= turbidity <= 10000:
            raise ValueError("Analyzer readings outside register range")
        return ph, turbidity, heartbeat
    finally:
        client.close()


def dosing_demand(ph: int, previous: bool, *, low: int = 700, high: int = 735) -> bool:
    """Use hysteresis so small analyzer changes do not chatter the drives."""
    if not 0 <= low < high <= 1400:
        raise ValueError("Dosing pH thresholds must be ordered within 0..1400")
    return ph < high if previous else ph < low


def update_drives(
    context: ModbusServerContext,
    duty: CyclicActuator,
    standby: CyclicActuator,
    demand: bool,
    analyzer_ok: bool,
    analyzer_alarm: int = 0,
) -> bool:
    """Command both I/O connections and publish confirmed status."""
    enabled = bool(context[0].getValues(1, 0, 1)[0])
    requested = demand and analyzer_ok and enabled
    duty.command(requested)
    duty_running, duty_healthy = duty.snapshot()
    duty_running = duty_running and duty_healthy
    standby_requested = requested and not duty_running
    standby.command(standby_requested)
    standby_running, standby_healthy = standby.snapshot()
    standby_running = standby_running and standby_healthy
    context[0].setValues(
        2, 0, [int(duty_running), int(standby_running), int(analyzer_ok)]
    )
    alarm_mask = analyzer_alarm
    if not analyzer_ok and not analyzer_alarm:
        alarm_mask |= INSTRUMENT_UNAVAILABLE
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
    if requested and not (duty_running or standby_running):
        alarm_mask |= OUTPUT_UNCONFIRMED
    previous = context[0].getValues(3, 5, 1)[0]
    if alarm_mask != previous:
        context[0].setValues(3, 5, [alarm_mask])
        raised, cleared = alarm_changes(previous, alarm_mask)
        for name in raised:
            log.warning("alarm_raised=%s", name)
        for name in cleared:
            log.info("alarm_cleared=%s", name)
    return requested and (duty_running or standby_running)


def dose_output_x10(context: ModbusServerContext, running: bool) -> int:
    """Apply the writable operator setpoint only when a drive confirms running."""
    if not running:
        return 0
    return min(context[0].getValues(3, 4, 1)[0], 1000)


def main() -> None:
    name = os.getenv("NODE_HOSTNAME", "MET-PLC-DOSING-01")
    analyzer_host = os.environ["ANALYZER_HOST"]
    duty_host = os.environ["EIP_DUTY_HOST"]
    standby_host = os.environ["EIP_STANDBY_HOST"]
    interval = float(os.getenv("ANALYZER_POLL_INTERVAL", "1"))
    if interval <= 0:
        raise ValueError("ANALYZER_POLL_INTERVAL must be positive")
    monitor = AnalyzerHeartbeatMonitor(float(os.getenv("ANALYZER_STALE_SECONDS", "4")))
    low = int(os.getenv("DOSING_PH_LOW", "700"))
    high = int(os.getenv("DOSING_PH_HIGH", "735"))
    dosing_demand(720, False, low=low, high=high)
    rpi_ms = int(os.getenv("EIP_RPI_MS", "100"))
    io_port = int(os.getenv("EIP_IO_PORT", "2222"))
    duty = CyclicActuator(duty_host, rpi_ms=rpi_ms, io_port=io_port)
    standby = CyclicActuator(standby_host, rpi_ms=rpi_ms, io_port=io_port)
    context = make_context(setpoint_x10=int(os.getenv("DOSING_SETPOINT_X10", "650")))
    stop = threading.Event()
    for drive in (duty, standby):
        drive.start()

    def control() -> None:
        ph, turbidity = 720, 50
        demand = False
        analyzer_ok = False
        analyzer_alarm = INSTRUMENT_UNAVAILABLE
        last_poll = -interval
        last_heartbeat = time.monotonic()
        heartbeat = 0
        reported_failure = False
        while not stop.wait(0.1):
            now = time.monotonic()
            if now - last_poll >= interval:
                last_poll = now
                try:
                    ph, turbidity, instrument_heartbeat = read_analyzer(analyzer_host)
                    analyzer_ok = monitor.observe(instrument_heartbeat, now)
                    analyzer_alarm = 0 if analyzer_ok else INSTRUMENT_STALE
                    demand = (
                        dosing_demand(ph, demand, low=low, high=high)
                        if analyzer_ok
                        else False
                    )
                    reported_failure = False
                except (OSError, ValueError, ModbusException) as exc:
                    analyzer_ok = False
                    analyzer_alarm = INSTRUMENT_UNAVAILABLE
                    demand = False
                    if not reported_failure:
                        log.warning("Analyzer unavailable: %s", exc)
                        reported_failure = True
            running = update_drives(
                context, duty, standby, demand, analyzer_ok, analyzer_alarm
            )
            while now - last_heartbeat >= 1:
                last_heartbeat += 1
                heartbeat = (heartbeat + 1) % 65536
            dose_output = dose_output_x10(context, running)
            context[0].setValues(3, 0, [ph, turbidity, dose_output, heartbeat])

    worker = threading.Thread(target=control, daemon=True)
    worker.start()
    host = os.getenv("MODBUS_HOST", "0.0.0.0")
    port = int(os.getenv("MODBUS_PORT", "502"))
    log.info("Dosing PLC %s listening on %s:%s", name, host, port)
    try:
        StartTcpServer(context, address=(host, port))
    finally:
        stop.set()
        worker.join(timeout=2)
        duty.close()
        standby.close()


if __name__ == "__main__":
    main()
