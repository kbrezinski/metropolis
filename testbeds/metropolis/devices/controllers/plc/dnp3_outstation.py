"""Small DNP3 alarm outstation for a networked remote RTU.

Binary inputs 0..3 report instrument unavailable, instrument stale, pump
unavailable, and output unconfirmed. The database queues class-1 events when
one of those alarm states changes; process values remain on Modbus.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from dnp3.core.flags import BinaryQuality
from dnp3.database import BinaryInputConfig, Database, EventClass
from dnp3.outstation import Outstation, OutstationConfig, OutstationTcpRunner

from dnp3_points import ALARM_BITS


def alarm_database() -> Database:
    database = Database()
    for index in range(len(ALARM_BITS)):
        database.add_binary_input(
            index,
            BinaryInputConfig(event_class=EventClass.CLASS_1),
            quality=BinaryQuality.ONLINE,
        )
    return database


def update_alarms(database: Database, mask: int) -> None:
    for index, bit in enumerate(ALARM_BITS):
        database.update_binary_input(index, bool(mask & bit))


class AlarmOutstation:
    """Bridge the RTU control thread's alarm mask to a DNP3 TCP listener."""

    def __init__(self, name: str, *, host: str = "0.0.0.0", port: int = 20000):
        if not 1 <= port <= 65535:
            raise ValueError("DNP3_PORT must be within 1..65535")
        self.name, self.host, self.port = name, host, port
        self._mask = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.log = logging.getLogger("metropolis.dnp3.outstation")

    def publish(self, alarm_mask: int) -> None:
        with self._lock:
            self._mask = alarm_mask

    async def _serve(self) -> None:
        database = alarm_database()
        outstation = Outstation(database=database, config=OutstationConfig(address=1))
        runner = OutstationTcpRunner(
            outstation=outstation, host=self.host, port=self.port
        )
        serving = asyncio.create_task(runner.run())
        last_mask: int | None = None
        listening = False
        try:
            while not self._stop.is_set():
                if serving.done():
                    await serving
                    raise RuntimeError("DNP3 listener stopped unexpectedly")
                if runner.is_running and not listening:
                    self.log.info(
                        "%s DNP3 listening on %s:%s", self.name, self.host, self.port
                    )
                    listening = True
                with self._lock:
                    mask = self._mask
                if mask != last_mask:
                    update_alarms(database, mask)
                    last_mask = mask
                await asyncio.sleep(0.1)
        finally:
            await runner.stop()
            await serving

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                asyncio.run(self._serve())
            except Exception as exc:  # Keep Modbus running if DNP3 is unavailable.
                self.log.error(
                    "%s DNP3 listener failed: %s: %s",
                    self.name,
                    type(exc).__name__,
                    exc,
                )
            if not self._stop.is_set() and self._stop.wait(5):
                return

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("DNP3 outstation already started")
        self._thread = threading.Thread(
            target=self._run, name="dnp3-outstation", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
