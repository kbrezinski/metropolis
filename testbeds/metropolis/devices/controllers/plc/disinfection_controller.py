"""Disinfection PLC with networked residual measurement and cyclic pump I/O.

Holding registers 0..5 expose residual chlorine x10 mg/L, confirmed pump
status bits, output on/off as 0 or 1000, heartbeat, writable residual target
x10 mg/L, and the shared alarm bitmask. Coil 0 enables the pump command;
discrete inputs 0..2 report duty, standby, and analyzer availability. The
reading and command are a traffic model, not a chemical process simulation.
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

from alarms import (
    DUTY_UNAVAILABLE,
    INSTRUMENT_STALE,
    INSTRUMENT_UNAVAILABLE,
    OUTPUT_UNCONFIRMED,
    STANDBY_UNAVAILABLE,
    alarm_changes,
)
from ethernet_ip import CyclicActuator

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("metropolis.disinfection_plc")

TARGET_MAX_X10 = 100


def make_context(*, target_x10: int = 10) -> ModbusServerContext:
    if not 0 <= target_x10 <= TARGET_MAX_X10:
        raise ValueError("Disinfection target must be within 0..100")
    device = ModbusSlaveContext(
        hr=ModbusSequentialDataBlock(1, [10, 0, 0, 0, target_x10, 0] + [0] * 10),
        co=ModbusSequentialDataBlock(1, [1] + [0] * 15),
        di=ModbusSequentialDataBlock(1, [0] * 16),
        ir=ModbusSequentialDataBlock(1, [0] * 16),
    )
    return ModbusServerContext(slaves=device, single=True)


class AnalyzerFreshness:
    """Treat repeated analyzer heartbeats as stale after the configured time."""

    def __init__(self, stale_after: float) -> None:
        if stale_after <= 0:
            raise ValueError("ANALYZER_STALE_SECONDS must be positive")
        self.stale_after = stale_after
        self.heartbeat: int | None = None
        self.changed_at: float | None = None

    def observe(self, heartbeat: int, now: float) -> bool:
        if heartbeat != self.heartbeat or self.changed_at is None:
            self.heartbeat = heartbeat
            self.changed_at = now
        return now - self.changed_at < self.stale_after


def read_analyzer(host: str, *, port: int = 502) -> tuple[int, int]:
    """Read residual x10 mg/L and heartbeat over the local Modbus link."""
    client = ModbusTcpClient(host, port=port, timeout=0.6, retries=0)
    try:
        try:
            if not client.connect():
                raise ConnectionError(f"Residual analyzer {host}:{port} unavailable")
            response = client.read_holding_registers(address=0, count=2, slave=1)
        except ModbusException as exc:
            raise ConnectionError(
                f"Residual analyzer {host}:{port} poll failed"
            ) from exc
        if response is None or response.isError() or len(response.registers) != 2:
            raise ValueError("Residual analyzer returned invalid Modbus registers")
        residual, heartbeat = response.registers
        if not 0 <= residual <= 1000:
            raise ValueError("Residual analyzer reading outside register range")
        return residual, heartbeat
    finally:
        client.close()


def valid_target(requested: int, previous: int) -> int:
    """Ignore an out-of-range direct Modbus write to the target register."""
    return requested if 0 <= requested <= TARGET_MAX_X10 else previous


def command_pumps(
    context: ModbusServerContext,
    duty: CyclicActuator,
    standby: CyclicActuator,
    requested: bool,
    analyzer_ready: bool,
    *,
    confirmation_due: bool = True,
) -> int:
    """Send on/off I/O and expose confirmed pump status and alarms."""
    duty.command(requested)
    duty_running, duty_available = duty.snapshot()
    duty_faulted = bool(getattr(duty, "faulted", False))
    duty_running = duty_running and duty_available and not duty_faulted

    standby_requested = requested and (
        not duty_available or duty_faulted or (confirmation_due and not duty_running)
    )
    standby.command(standby_requested)
    standby_running, standby_available = standby.snapshot()
    standby_faulted = bool(getattr(standby, "faulted", False))
    standby_running = standby_running and standby_available and not standby_faulted

    status = int(duty_running) | (int(standby_running) << 1)
    context[0].setValues(
        2, 0, [int(duty_running), int(standby_running), int(analyzer_ready)]
    )
    context[0].setValues(3, 1, [status, 1000 if requested and status else 0])

    alarms = 0
    if (
        not duty_available
        or duty_faulted
        or (confirmation_due and requested and not duty_running)
    ):
        alarms |= DUTY_UNAVAILABLE
    if (
        not standby_available
        or standby_faulted
        or (confirmation_due and standby_requested and not standby_running)
    ):
        alarms |= STANDBY_UNAVAILABLE
    if confirmation_due and requested and not status:
        alarms |= OUTPUT_UNCONFIRMED
    return alarms


def main() -> None:
    name = os.getenv("NODE_HOSTNAME", "MET-PLC-DISINFECTION-01")
    analyzer_host = os.environ["ANALYZER_HOST"]
    duty_host = os.environ["EIP_DUTY_HOST"]
    standby_host = os.environ["EIP_STANDBY_HOST"]
    poll_interval = float(os.getenv("ANALYZER_POLL_INTERVAL", "1"))
    if poll_interval <= 0:
        raise ValueError("ANALYZER_POLL_INTERVAL must be positive")
    freshness = AnalyzerFreshness(float(os.getenv("ANALYZER_STALE_SECONDS", "4")))
    rpi_ms = int(os.getenv("EIP_RPI_MS", "100"))
    io_port = int(os.getenv("EIP_IO_PORT", "2222"))
    duty = CyclicActuator(duty_host, rpi_ms=rpi_ms, io_port=io_port)
    standby = CyclicActuator(standby_host, rpi_ms=rpi_ms, io_port=io_port)
    context = make_context(target_x10=int(os.getenv("RESIDUAL_TARGET_X10", "10")))
    stop = threading.Event()
    for pump in (duty, standby):
        pump.start()

    def control() -> None:
        residual = 10
        target = context[0].getValues(3, 4, 1)[0]
        analyzer_ready = False
        analyzer_alarm = INSTRUMENT_UNAVAILABLE
        last_poll = -poll_interval
        last_heartbeat = time.monotonic()
        heartbeat = 0
        previous_alarms = 0
        reported_failure = False
        invalid_target_reported = False
        requested_since: float | None = None

        while not stop.wait(0.1):
            now = time.monotonic()
            if now - last_poll >= poll_interval:
                last_poll = now
                try:
                    residual, analyzer_heartbeat = read_analyzer(analyzer_host)
                    analyzer_ready = freshness.observe(analyzer_heartbeat, now)
                    analyzer_alarm = 0 if analyzer_ready else INSTRUMENT_STALE
                    reported_failure = False
                except (OSError, ValueError, ModbusException) as exc:
                    analyzer_ready = False
                    analyzer_alarm = INSTRUMENT_UNAVAILABLE
                    if not reported_failure:
                        log.warning("Residual analyzer unavailable: %s", exc)
                        reported_failure = True

            requested_target = context[0].getValues(3, 4, 1)[0]
            safe_target = valid_target(requested_target, target)
            if safe_target != requested_target:
                context[0].setValues(3, 4, [safe_target])
                if not invalid_target_reported:
                    log.warning("Rejected out-of-range residual target")
                    invalid_target_reported = True
            else:
                invalid_target_reported = False
            target = safe_target
            enabled = bool(context[0].getValues(1, 0, 1)[0])
            requested = enabled and analyzer_ready and residual < target
            if requested and requested_since is None:
                requested_since = now
            elif not requested:
                requested_since = None
            pump_alarms = command_pumps(
                context,
                duty,
                standby,
                requested,
                analyzer_ready,
                confirmation_due=requested_since is not None
                and now - requested_since >= max(0.5, 2 * rpi_ms / 1000),
            )
            alarms = analyzer_alarm | pump_alarms
            if alarms != previous_alarms:
                raised, cleared = alarm_changes(previous_alarms, alarms)
                for alarm in raised:
                    log.warning("alarm_raised=%s", alarm)
                for alarm in cleared:
                    log.info("alarm_cleared=%s", alarm)
                previous_alarms = alarms

            while now - last_heartbeat >= 1:
                last_heartbeat += 1
                heartbeat = (heartbeat + 1) % 65536
            context[0].setValues(3, 0, [residual])
            context[0].setValues(3, 3, [heartbeat])
            context[0].setValues(3, 5, [alarms])

    worker = threading.Thread(target=control, daemon=True)
    worker.start()
    host = os.getenv("MODBUS_HOST", "0.0.0.0")
    port = int(os.getenv("MODBUS_PORT", "502"))
    log.info("Disinfection PLC %s listening on %s:%s", name, host, port)
    try:
        StartTcpServer(context, address=(host, port))
    finally:
        stop.set()
        worker.join(timeout=2)
        duty.close()
        standby.close()


if __name__ == "__main__":
    main()
