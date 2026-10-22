"""Filtration PLC with networked pressure measurement and backwash I/O.

Holding registers 0..5 are differential pressure x10 kPa, backwash phase,
confirmed output flags, heartbeat, writable threshold x10 kPa, and alarm mask.
Coil 0 enables automatic backwash; discrete inputs 0..2 report confirmed pump,
valve, and pressure-instrument status. This is a traffic model, not hydraulics.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass

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
    VALVE_UNAVAILABLE,
    alarm_changes,
)
from ethernet_ip import CyclicActuator

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("metropolis.filtration_plc")

IDLE = 0
BACKWASHING = 1
WAITING_TO_REARM = 2
THRESHOLD_MIN = 100
THRESHOLD_MAX = 1000


def make_context() -> ModbusServerContext:
    device = ModbusSlaveContext(
        hr=ModbusSequentialDataBlock(1, [250, IDLE, 0, 0, 650, 0] + [0] * 10),
        co=ModbusSequentialDataBlock(1, [1] + [0] * 15),
        di=ModbusSequentialDataBlock(1, [0] * 16),
        ir=ModbusSequentialDataBlock(1, [0] * 16),
    )
    return ModbusServerContext(slaves=device, single=True)


def read_instrument(host: str, *, port: int = 502) -> tuple[int, int]:
    """Read pressure drop and heartbeat over the local Modbus TCP link."""
    client = ModbusTcpClient(host, port=port, timeout=0.6, retries=0)
    try:
        try:
            if not client.connect():
                raise ConnectionError(f"Pressure instrument {host}:{port} unavailable")
            response = client.read_holding_registers(address=0, count=2, slave=1)
        except ModbusException as exc:
            raise ConnectionError(
                f"Pressure instrument {host}:{port} poll failed"
            ) from exc
        if response is None or response.isError() or len(response.registers) != 2:
            raise ValueError("Pressure instrument returned invalid Modbus registers")
        pressure, heartbeat = response.registers
        if not 0 <= pressure <= 2000:
            raise ValueError("Pressure instrument reading outside expected range")
        return pressure, heartbeat
    finally:
        client.close()


@dataclass
class InstrumentFreshness:
    stale_after: float
    heartbeat: int | None = None
    changed_at: float | None = None

    def observe(self, heartbeat: int, now: float) -> bool:
        """Return false when successful polls repeat an old heartbeat too long."""
        if self.heartbeat != heartbeat or self.changed_at is None:
            self.heartbeat = heartbeat
            self.changed_at = now
        return now - self.changed_at < self.stale_after


@dataclass
class BackwashCycle:
    duration: float
    hysteresis: int
    phase: int = IDLE
    started_at: float | None = None

    def step(
        self,
        pressure: int,
        threshold: int,
        now: float,
        *,
        enabled: bool,
        instrument_ready: bool,
    ) -> bool:
        """Start one timed backwash per high-pressure excursion."""
        if not instrument_ready:
            self.phase = WAITING_TO_REARM
            return False
        if not enabled:
            if self.phase == BACKWASHING:
                self.phase = WAITING_TO_REARM
            return False
        if self.phase == BACKWASHING and now - self.started_at >= self.duration:
            self.phase = WAITING_TO_REARM
        if self.phase == WAITING_TO_REARM and pressure <= threshold - self.hysteresis:
            self.phase = IDLE
        if self.phase == IDLE and pressure >= threshold:
            self.phase = BACKWASHING
            self.started_at = now
        return self.phase == BACKWASHING


def command_outputs(
    context: ModbusServerContext,
    pump: CyclicActuator,
    valve: CyclicActuator,
    requested: bool,
    *,
    confirmation_due: bool,
    instrument_ready: bool,
) -> int:
    """Open the backwash valve before requesting pump operation."""
    valve.command(requested)
    valve_open, valve_available = valve.snapshot()
    valve_faulted = bool(getattr(valve, "faulted", False))
    valve_open = valve_open and valve_available and not valve_faulted
    pump.command(requested and valve_open)
    pump_running, pump_available = pump.snapshot()
    pump_faulted = bool(getattr(pump, "faulted", False))
    pump_running = pump_running and pump_available and not pump_faulted
    context[0].setValues(
        2, 0, [int(pump_running), int(valve_open), int(instrument_ready)]
    )
    context[0].setValues(3, 2, [int(pump_running) | (int(valve_open) << 1)])
    alarms = 0
    if not pump_available or pump_faulted:
        alarms |= DUTY_UNAVAILABLE
    if not valve_available or valve_faulted:
        alarms |= VALVE_UNAVAILABLE
    if requested and confirmation_due and not (valve_open and pump_running):
        alarms |= OUTPUT_UNCONFIRMED
    return alarms


def valid_threshold(requested: int, previous: int) -> int:
    """Reject invalid direct Modbus writes and retain the last safe value."""
    return requested if THRESHOLD_MIN <= requested <= THRESHOLD_MAX else previous


def main() -> None:
    name = os.getenv("NODE_HOSTNAME", "MET-PLC-FILTRATION-01")
    instrument_host = os.environ["INSTRUMENT_HOST"]
    pump_host = os.environ["EIP_PUMP_HOST"]
    valve_host = os.environ["EIP_VALVE_HOST"]
    poll_interval = float(os.getenv("DP_POLL_INTERVAL", "1"))
    stale_after = float(os.getenv("INSTRUMENT_STALE_SECONDS", "3"))
    duration = float(os.getenv("BACKWASH_SECONDS", "8"))
    hysteresis = int(os.getenv("BACKWASH_HYSTERESIS_X10", "100"))
    if poll_interval <= 0 or stale_after <= poll_interval or duration <= 0:
        raise ValueError(
            "Filtration polling, stale, and backwash times must be positive"
        )
    if hysteresis <= 0 or hysteresis > THRESHOLD_MIN:
        raise ValueError("BACKWASH_HYSTERESIS_X10 must be between 1 and 100")
    rpi_ms = int(os.getenv("EIP_RPI_MS", "100"))
    io_port = int(os.getenv("EIP_IO_PORT", "2222"))
    pump = CyclicActuator(pump_host, rpi_ms=rpi_ms, io_port=io_port)
    valve = CyclicActuator(valve_host, rpi_ms=rpi_ms, io_port=io_port)
    context = make_context()
    freshness = InstrumentFreshness(stale_after)
    cycle = BackwashCycle(duration, hysteresis)
    stop = threading.Event()
    for actuator in (pump, valve):
        actuator.start()

    def control() -> None:
        pressure = 250
        threshold = 650
        instrument_alarm = INSTRUMENT_UNAVAILABLE
        instrument_ready = False
        last_poll = -poll_interval
        last_heartbeat = time.monotonic()
        heartbeat = 0
        request_since: float | None = None
        previous_alarms = 0
        invalid_threshold_reported = False
        instrument_failure_reported = False
        while not stop.wait(0.1):
            now = time.monotonic()
            if now - last_poll >= poll_interval:
                last_poll = now
                try:
                    pressure, instrument_heartbeat = read_instrument(instrument_host)
                    instrument_ready = freshness.observe(instrument_heartbeat, now)
                    instrument_alarm = 0 if instrument_ready else INSTRUMENT_STALE
                    instrument_failure_reported = False
                except (OSError, ValueError, ConnectionError) as exc:
                    instrument_ready = False
                    instrument_alarm = INSTRUMENT_UNAVAILABLE
                    if not instrument_failure_reported:
                        log.warning("Pressure instrument unavailable: %s", exc)
                        instrument_failure_reported = True
            requested_threshold = context[0].getValues(3, 4, 1)[0]
            safe_threshold = valid_threshold(requested_threshold, threshold)
            if safe_threshold != requested_threshold:
                context[0].setValues(3, 4, [safe_threshold])
                if not invalid_threshold_reported:
                    log.warning("Rejected out-of-range backwash threshold")
                    invalid_threshold_reported = True
            else:
                invalid_threshold_reported = False
            threshold = safe_threshold
            enabled = bool(context[0].getValues(1, 0, 1)[0])
            requested = cycle.step(
                pressure,
                threshold,
                now,
                enabled=enabled,
                instrument_ready=instrument_ready,
            )
            if requested and request_since is None:
                request_since = now
            elif not requested:
                request_since = None
            actuator_alarms = command_outputs(
                context,
                pump,
                valve,
                requested,
                confirmation_due=(
                    request_since is not None and now - request_since >= 0.5
                ),
                instrument_ready=instrument_ready,
            )
            alarms = instrument_alarm | actuator_alarms
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
            context[0].setValues(3, 0, [pressure, cycle.phase])
            context[0].setValues(3, 3, [heartbeat])
            context[0].setValues(3, 5, [alarms])

    worker = threading.Thread(target=control, daemon=True)
    worker.start()
    host = os.getenv("MODBUS_HOST", "0.0.0.0")
    port = int(os.getenv("MODBUS_PORT", "502"))
    log.info("Filtration PLC %s listening on %s:%s", name, host, port)
    try:
        StartTcpServer(context, address=(host, port))
    finally:
        stop.set()
        worker.join(timeout=2)
        pump.close()
        valve.close()


if __name__ == "__main__":
    main()
