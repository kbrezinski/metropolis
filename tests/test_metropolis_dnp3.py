"""Loopback DNP3 alarm exchange and selected-dataset peer settings."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
from dnp3.master import DefaultSOEHandler, Master, MasterConfig, MasterTcpRunner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "testbeds/metropolis/devices/runtime"))
sys.path.insert(0, str(ROOT / "testbeds/metropolis/devices/controllers/plc"))
sys.path.insert(0, str(ROOT / "testbeds/metropolis/devices/operations/scada"))

from alarms import DUTY_UNAVAILABLE, INSTRUMENT_UNAVAILABLE  # noqa: E402
from dnp3_master import Dnp3Target, configured_targets, start_master  # noqa: E402
from dnp3_outstation import AlarmOutstation  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(predicate, timeout: float = 3) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_dnp3_peer_settings_are_dynamic_and_validated():
    records = [
        {"name": "remote-a", "host": "192.0.2.25", "port": 20000},
        {"name": "remote-b", "host": "192.0.2.26", "port": 20001},
    ]
    encoded = base64.b64encode(json.dumps(records).encode()).decode()
    assert configured_targets(encoded) == [
        Dnp3Target("remote-a", "192.0.2.25", 20000),
        Dnp3Target("remote-b", "192.0.2.26", 20001),
    ]
    assert configured_targets(None) == []
    with pytest.raises(ValueError, match="must encode JSON"):
        configured_targets("not-base64")
    with pytest.raises(ValueError, match="unique"):
        configured_targets(
            base64.b64encode(json.dumps(records[:1] * 2).encode()).decode()
        )


def test_dnp3_outstation_reports_alarm_changes_over_tcp():
    port = free_port()
    outstation = AlarmOutstation("remote-a", host="127.0.0.1", port=port)
    outstation.start()

    async def exchange() -> None:
        handler = DefaultSOEHandler()
        master = Master(
            handler=handler, config=MasterConfig(address=2, outstation_address=1)
        )
        for _ in range(30):
            try:
                async with MasterTcpRunner(
                    master=master, host="127.0.0.1", port=port, response_timeout=1
                ) as runner:
                    await runner.integrity_poll()
                    assert handler.binary_inputs[0].value is False
                    outstation.publish(INSTRUMENT_UNAVAILABLE | DUTY_UNAVAILABLE)
                    await asyncio.sleep(0.25)
                    await runner.class_poll(class_1=True, class_2=False, class_3=False)
                    assert handler.binary_inputs[0].value is True
                    assert handler.binary_inputs[2].value is True
                    outstation.publish(0)
                    await asyncio.sleep(0.25)
                    await runner.class_poll(class_1=True, class_2=False, class_3=False)
                    assert handler.binary_inputs[0].value is False
                    assert handler.binary_inputs[2].value is False
                    return
            except OSError:
                await asyncio.sleep(0.1)
        pytest.fail("DNP3 outstation did not accept a loopback connection")

    try:
        asyncio.run(exchange())
    finally:
        outstation.close()


def test_dnp3_master_recovers_when_outstation_starts_late(caplog, monkeypatch):
    import dnp3_master

    caplog.set_level(logging.DEBUG, logger="metropolis.dnp3.master")
    monkeypatch.setattr(dnp3_master, "RETRY_SECONDS", 0.1)
    monkeypatch.setattr(dnp3_master, "EVENT_POLL_SECONDS", 0.1)
    monkeypatch.setattr(dnp3_master, "INTEGRITY_POLL_SECONDS", 0.3)
    port = free_port()
    stop = threading.Event()
    worker = start_master([Dnp3Target("remote-b", "127.0.0.1", port)], stop)
    assert worker is not None
    outstation = AlarmOutstation("remote-b", host="127.0.0.1", port=port)
    try:
        assert wait_for(lambda: "polling unavailable" in caplog.text)
        outstation.start()
        assert wait_for(lambda: "DNP3 integrity poll completed" in caplog.text)
        assert wait_for(lambda: "DNP3 polling recovered" in caplog.text)
        outstation.publish(INSTRUMENT_UNAVAILABLE)
        assert wait_for(
            lambda: "DNP3 alarm_raised=instrument_unavailable" in caplog.text
        )
        assert wait_for(lambda: "DNP3 class-1 poll completed" in caplog.text)
        assert wait_for(lambda: caplog.text.count("DNP3 integrity poll completed") >= 2)
    finally:
        stop.set()
        worker.join(timeout=4)
        outstation.close()
    assert not worker.is_alive()


def test_dnp3_listener_failure_is_reported(caplog):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        outstation = AlarmOutstation("blocked-rtu", host="127.0.0.1", port=port)
        try:
            outstation.start()
            assert wait_for(lambda: "DNP3 listener failed" in caplog.text)
        finally:
            outstation.close()
