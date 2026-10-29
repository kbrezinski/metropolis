"""Poll inventoried remote RTUs for DNP3 alarm snapshots and class-1 events."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import threading
from dataclasses import dataclass

from dnp3.master import DefaultSOEHandler, Master, MasterConfig, MasterTcpRunner

from alarms import ALARM_NAMES
from dnp3_points import ALARM_BITS


log = logging.getLogger("metropolis.dnp3.master")
EVENT_POLL_SECONDS = 5.0
INTEGRITY_POLL_SECONDS = 60.0
RETRY_SECONDS = 5.0


@dataclass(frozen=True)
class Dnp3Target:
    name: str
    host: str
    port: int


def configured_targets(value: str | None) -> list[Dnp3Target]:
    """Decode peers resolved from the selected GNS3 dataset at build time."""
    if not value:
        return []
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) > 8192:
            raise ValueError("DNP3_TARGETS_B64 is too large")
        records = json.loads(raw)
    except (binascii.Error, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("DNP3_TARGETS_B64 must encode JSON") from exc
    if not isinstance(records, list):
        raise ValueError("DNP3_TARGETS_B64 must encode a list")
    result = []
    for record in records:
        if (
            not isinstance(record, dict)
            or set(record) != {"name", "host", "port"}
            or not isinstance(record["name"], str)
            or not record["name"]
            or not isinstance(record["host"], str)
            or not record["host"]
            or isinstance(record["port"], bool)
            or not isinstance(record["port"], int)
            or not 1 <= record["port"] <= 65535
        ):
            raise ValueError("Each DNP3 peer needs name, host, and TCP port")
        result.append(Dnp3Target(**record))
    if len({item.name for item in result}) != len(result):
        raise ValueError("DNP3 peer names must be unique")
    return result


class AlarmHandler(DefaultSOEHandler):
    """Retain point states and log alarm state changes reported by an RTU."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name

    def on_binary_input(self, values, info) -> None:
        previous = self.binary_inputs
        for value in values:
            if value.index >= len(ALARM_BITS):
                continue
            old = previous.get(value.index)
            if old is None and not value.value:
                continue
            if old is None or old.value != value.value:
                alarm = ALARM_NAMES[ALARM_BITS[value.index]]
                state = "raised" if value.value else "cleared"
                log.info("%s DNP3 alarm_%s=%s", self.name, state, alarm)
        super().on_binary_input(values, info)


async def _pause(stop: threading.Event, seconds: float) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while not stop.is_set() and loop.time() < deadline:
        await asyncio.sleep(min(0.2, deadline - loop.time()))


async def poll_target(target: Dnp3Target, stop: threading.Event) -> None:
    """Retry one outstation independently; never hold up the Modbus loop."""
    unavailable = False
    while not stop.is_set():
        try:
            handler = AlarmHandler(target.name)
            master = Master(
                handler=handler, config=MasterConfig(address=2, outstation_address=1)
            )
            async with MasterTcpRunner(
                master=master,
                host=target.host,
                port=target.port,
                response_timeout=3.0,
            ) as runner:
                await runner.integrity_poll()
                log.info("%s DNP3 integrity poll completed", target.name)
                if unavailable:
                    log.info("%s DNP3 polling recovered", target.name)
                unavailable = False
                next_integrity = (
                    asyncio.get_running_loop().time() + INTEGRITY_POLL_SECONDS
                )
                while not stop.is_set():
                    await _pause(stop, EVENT_POLL_SECONDS)
                    if stop.is_set():
                        break
                    await runner.class_poll(class_1=True, class_2=False, class_3=False)
                    log.debug("%s DNP3 class-1 poll completed", target.name)
                    if asyncio.get_running_loop().time() >= next_integrity:
                        await runner.integrity_poll()
                        next_integrity = (
                            asyncio.get_running_loop().time() + INTEGRITY_POLL_SECONDS
                        )
                        log.info("%s DNP3 integrity poll completed", target.name)
        except Exception as exc:  # One failed peer must not stop the others.
            if not unavailable and not stop.is_set():
                log.warning(
                    "%s DNP3 polling unavailable: %s: %s",
                    target.name,
                    type(exc).__name__,
                    exc,
                )
            unavailable = True
            await _pause(stop, RETRY_SECONDS)


def start_master(
    targets: list[Dnp3Target], stop: threading.Event
) -> threading.Thread | None:
    if not targets:
        return None

    def run() -> None:
        try:

            async def poll_all() -> None:
                await asyncio.gather(*(poll_target(target, stop) for target in targets))

            asyncio.run(poll_all())
        except Exception:
            log.exception("DNP3 master worker failed")

    thread = threading.Thread(target=run, name="dnp3-master", daemon=True)
    thread.start()
    return thread
