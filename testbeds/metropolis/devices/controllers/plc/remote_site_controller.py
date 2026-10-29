"""Networked remote-site RTU for reservoir and raw-water GNS3 sites.

The RTU polls a local Modbus level instrument and exchanges cyclic
EtherNet/IP I/O with a local pump drive. Its six holding registers expose
level x10 percent, confirmed pump state, instrument readiness, heartbeat,
requested pump state, and the shared alarm mask to central SCADA. Coil 0 is
the pump request, initially on. These values model traffic and status, not a
hydraulic process.
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
    alarm_changes,
)
from ethernet_ip import CyclicActuator


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("metropolis.remote_rtu")


def make_context(*, initial_level_x10: int = 650) -> ModbusServerContext:
    if not 0 <= initial_level_x10 <= 1000:
        raise ValueError("Initial level must be within 0..1000")
    device = ModbusSlaveContext(
        hr=ModbusSequentialDataBlock(1, [initial_level_x10, 0, 0, 0, 1, 0] + [0] * 10),
        co=ModbusSequentialDataBlock(1, [1] + [0] * 15),
        di=ModbusSequentialDataBlock(1, [0] * 16),
        ir=ModbusSequentialDataBlock(1, [0] * 16),
    )
    return ModbusServerContext(slaves=device, single=True)


def read_instrument(host: str, *, port: int = 502) -> tuple[int, int]:
    """Read level and heartbeat from the Ethernet-connected instrument."""
    client = ModbusTcpClient(host, port=port, timeout=0.6, retries=0)
    try:
        try:
            if not client.connect():
                raise ConnectionError(f"Level instrument {host}:{port} unavailable")
            response = client.read_holding_registers(address=0, count=2, slave=1)
        except ModbusException as exc:
            raise ConnectionError(
                f"Level instrument {host}:{port} poll failed"
            ) from exc
        if response is None or response.isError() or len(response.registers) != 2:
            raise ValueError("Level instrument returned invalid Modbus registers")
        level_x10, heartbeat = response.registers
        if not 0 <= level_x10 <= 1000:
            raise ValueError("Level instrument reading outside 0..1000")
        return level_x10, heartbeat
    finally:
        client.close()


@dataclass
class InstrumentFreshness:
    stale_after: float
    heartbeat: int | None = None
    changed_at: float | None = None

    def observe(self, heartbeat: int, now: float) -> bool:
        if self.heartbeat != heartbeat or self.changed_at is None:
            self.heartbeat = heartbeat
            self.changed_at = now
        return now - self.changed_at < self.stale_after


def publish_status(
    context: ModbusServerContext,
    pump: CyclicActuator,
    *,
    instrument_ready: bool,
    instrument_alarm: int,
    confirmation_due: bool,
) -> int:
    """Publish confirmed local I/O status and return the current alarm mask."""
    requested = bool(context[0].getValues(1, 0, 1)[0])
    pump.command(requested)
    active, available = pump.snapshot()
    faulted = bool(getattr(pump, "faulted", False))
    running = active and available and not faulted
    alarms = instrument_alarm
    if faulted or (confirmation_due and (not available or (requested and not running))):
        alarms |= DUTY_UNAVAILABLE
    if requested and not running and confirmation_due:
        alarms |= OUTPUT_UNCONFIRMED
    context[0].setValues(
        3,
        1,
        [int(running), int(instrument_ready)],
    )
    context[0].setValues(3, 4, [int(requested), alarms])
    context[0].setValues(2, 0, [int(running), int(instrument_ready)])
    return alarms


def main() -> None:
    name = os.getenv("NODE_HOSTNAME", "MET-RTU-REMOTE-01")
    site_role = os.getenv("REMOTE_SITE_ROLE", "")
    if site_role not in {"reservoir", "raw_water"}:
        raise ValueError("REMOTE_SITE_ROLE must be reservoir or raw_water")
    instrument_host = os.environ["INSTRUMENT_HOST"]
    pump_host = os.environ["EIP_PUMP_HOST"]
    instrument_port = int(os.getenv("INSTRUMENT_PORT", "502"))
    poll_interval = float(os.getenv("LEVEL_POLL_INTERVAL", "1"))
    stale_after = float(os.getenv("INSTRUMENT_STALE_SECONDS", "3"))
    rpi_ms = int(os.getenv("EIP_RPI_MS", "100"))
    confirm_seconds = float(os.getenv("PUMP_CONFIRM_SECONDS", "3"))
    if poll_interval <= 0 or stale_after <= poll_interval or confirm_seconds <= 0:
        raise ValueError(
            "Remote-site polling, freshness, and confirmation times must be positive"
        )

    pump = CyclicActuator(
        pump_host,
        rpi_ms=rpi_ms,
        io_port=int(os.getenv("EIP_IO_PORT", "2222")),
    )
    context = make_context()
    freshness = InstrumentFreshness(stale_after)
    stop = threading.Event()
    dnp3 = None
    dnp3_setting = os.getenv("DNP3_ENABLED", "false").strip().lower()
    if dnp3_setting not in {"0", "false", "no", "1", "true", "yes"}:
        raise ValueError("DNP3_ENABLED must be true or false")
    if dnp3_setting in {"1", "true", "yes"}:
        from dnp3_outstation import AlarmOutstation

        dnp3 = AlarmOutstation(
            name,
            host=os.getenv("DNP3_HOST", "0.0.0.0"),
            port=int(os.getenv("DNP3_PORT", "20000")),
        )
        dnp3.start()
    pump.command(True)
    pump.start()

    def control() -> None:
        last_poll = -poll_interval
        last_heartbeat = time.monotonic()
        heartbeat = 0
        requested_before = True
        requested_since = last_heartbeat
        instrument_ready = False
        instrument_alarm = 0
        previous_alarms = 0
        instrument_failure_reported = False
        while not stop.wait(0.1):
            now = time.monotonic()
            if now - last_poll >= poll_interval:
                last_poll = now
                try:
                    level_x10, instrument_heartbeat = read_instrument(
                        instrument_host, port=instrument_port
                    )
                    instrument_ready = freshness.observe(instrument_heartbeat, now)
                    instrument_alarm = 0 if instrument_ready else INSTRUMENT_STALE
                    context[0].setValues(3, 0, [level_x10])
                    if instrument_failure_reported:
                        log.info("%s level instrument polling recovered", name)
                    instrument_failure_reported = False
                except (OSError, ValueError, ConnectionError) as exc:
                    instrument_ready = False
                    instrument_alarm = INSTRUMENT_UNAVAILABLE
                    if not instrument_failure_reported:
                        log.warning("%s level instrument unavailable: %s", name, exc)
                        instrument_failure_reported = True

            requested = bool(context[0].getValues(1, 0, 1)[0])
            if requested != requested_before:
                requested_before = requested
                requested_since = now
            alarms = publish_status(
                context,
                pump,
                instrument_ready=instrument_ready,
                instrument_alarm=instrument_alarm,
                confirmation_due=now - requested_since >= confirm_seconds,
            )
            if dnp3 is not None:
                dnp3.publish(alarms)
            if alarms != previous_alarms:
                raised, cleared = alarm_changes(previous_alarms, alarms)
                for alarm in raised:
                    log.warning("%s alarm_raised=%s", name, alarm)
                for alarm in cleared:
                    log.info("%s alarm_cleared=%s", name, alarm)
                previous_alarms = alarms
            if now - last_heartbeat >= 1.0:
                heartbeat = (heartbeat + 1) % 65536
                context[0].setValues(3, 3, [heartbeat])
                last_heartbeat = now

    threading.Thread(target=control, daemon=True).start()
    host = os.getenv("MODBUS_HOST", "0.0.0.0")
    port = int(os.getenv("MODBUS_PORT", "502"))
    log.info("Starting %s remote RTU %s on %s:%s", site_role, name, host, port)
    try:
        StartTcpServer(context, address=(host, port))
    finally:
        stop.set()
        pump.close()
        if dnp3 is not None:
            dnp3.close()


if __name__ == "__main__":
    main()
