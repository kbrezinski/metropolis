"""Poll Modbus cells and bridge HMI requests to acknowledged PLC writes."""

from __future__ import annotations

import json
import logging
import math
import os
import queue
import re
import signal
import threading
import time
from collections import OrderedDict
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

import paho.mqtt.client as mqtt
from pymodbus.client import ModbusTcpClient


import sys
from pathlib import Path

# The shared TLS settings sit beside the entrypoint, which every image copies
# to /opt/metropolis.
sys.path.insert(0, str(Path("/opt/metropolis")))
from tls_config import TlsConfig, apply_to  # noqa: E402,F401
from alarms import ALARM_NAMES  # noqa: E402
from telemetry_export import TelemetryExporter  # noqa: E402
from opcua_runtime import OpcUaServerWorker  # noqa: E402


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("metropolis.scada")


@dataclass(frozen=True)
class Target:
    cell: str
    host: str
    profile: str
    topic: str


COMMANDS = {
    "intake": ("level_setpoint", "percent", 0.0, 100.0),
    "dosing": ("dose_setpoint", "percent", 0.0, 100.0),
    "filtration": ("backwash_threshold", "kPa", 10.0, 100.0),
    "disinfection": ("residual_setpoint", "mg/L", 0.0, 10.0),
}
READ_ONLY_PROFILES = frozenset({"reservoir", "raw_water"})
SUPPORTED_PROFILES = COMMANDS.keys() | READ_ONLY_PROFILES
SETPOINT_REGISTER = 4
COMMAND_MAX_AGE_SECONDS = 15


class ControllerHeartbeatMonitor:
    """Mark readable PLC data stale when its heartbeat stops advancing."""

    def __init__(self, stale_after: float) -> None:
        if stale_after <= 0:
            raise ValueError("PLC_STALE_SECONDS must be positive")
        self.stale_after = stale_after
        self.last_change: dict[str, tuple[int, float]] = {}

    def observe(self, cell: str, heartbeat: int, now: float) -> bool:
        previous = self.last_change.get(cell)
        if previous is None or previous[0] != heartbeat:
            self.last_change[cell] = heartbeat, now
            return True
        return now - previous[1] < self.stale_after

    def forget(self, cell: str) -> None:
        self.last_change.pop(cell, None)


def configured_targets(
    value: str | None, *, legacy_host: str, legacy_topic: str
) -> list[Target]:
    """Read cell targets from node configuration, retaining old single-PLC use."""
    if not value:
        return [Target("intake", legacy_host, "intake", legacy_topic)]
    records = json.loads(value)
    if not isinstance(records, list) or not records:
        raise ValueError("SCADA_TARGETS must be a nonempty JSON array")
    targets = []
    for item in records:
        if not isinstance(item, dict) or set(item) != {
            "id",
            "host",
            "profile",
            "topic",
        }:
            raise ValueError("Each SCADA target needs id, host, profile, and topic")
        if any(not isinstance(item[key], str) or not item[key] for key in item):
            raise ValueError("SCADA target fields must be nonempty strings")
        if item["profile"] not in SUPPORTED_PROFILES:
            raise ValueError(f"Unsupported SCADA profile: {item['profile']}")
        targets.append(Target(item["id"], item["host"], item["profile"], item["topic"]))
    if len({item.cell for item in targets}) != len(targets):
        raise ValueError("SCADA target IDs must be unique")
    return targets


def decode_registers(target: Target, registers: list[int]) -> dict:
    if len(registers) < 4:
        raise ValueError("Controller returned fewer than four registers")
    if target.profile in READ_ONLY_PROFILES and len(registers) < 6:
        raise ValueError("Remote RTU returned fewer than six registers")
    payload = {
        "cell": target.cell,
        "controller": target.host,
        "heartbeat": registers[3],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if len(registers) >= 6:
        payload.update(
            alarm_mask=registers[5],
            alarms=[label for bit, label in ALARM_NAMES.items() if registers[5] & bit],
            quality="degraded" if registers[5] else "good",
        )
        if target.profile in COMMANDS:
            payload["setpoint"] = registers[4] / 10
    if target.profile in READ_ONLY_PROFILES:
        level_field, pump_field = (
            ("reservoir_level_percent", "booster_pump_running")
            if target.profile == "reservoir"
            else ("wet_well_level_percent", "lift_pump_running")
        )
        payload.update(
            {
                level_field: registers[0] / 10,
                pump_field: bool(registers[1]),
                "instrument_ready": bool(registers[2]),
                "pump_requested": bool(registers[4]),
            }
        )
    elif target.profile == "disinfection":
        payload.update(
            residual_mg_l=registers[0] / 10,
            pump_status=registers[1],
            dose_output_percent=registers[2] / 10,
        )
    elif target.profile == "dosing":
        payload.update(
            ph=registers[0] / 100,
            turbidity_ntu=registers[1] / 10,
            dose_output_percent=registers[2] / 10,
        )
    elif target.profile == "filtration":
        payload.update(
            differential_pressure_kpa=registers[0] / 10,
            backwash_phase=registers[1],
            confirmed_outputs=registers[2],
        )
    else:
        payload.update(
            level_percent=registers[0] / 10,
            flow_rate=registers[1] / 10,
            quality_percent=registers[2] / 10,
        )
    return payload


def poll_target(target: Target, *, port: int, unit_id: int) -> dict:
    client = ModbusTcpClient(target.host, port=port, timeout=3, retries=0)
    try:
        if not client.connect():
            raise ConnectionError(
                f"Unable to connect to controller {target.host}:{port}"
            )
        response = client.read_holding_registers(address=0, count=6, slave=unit_id)
        if response.isError() or len(response.registers) < 6:
            raise RuntimeError(f"Controller returned Modbus error: {response}")
        return decode_registers(target, response.registers)
    finally:
        client.close()


def command_topic(target: Target) -> str:
    return f"metropolis/plant/{target.cell}/command"


def ack_topic(target: Target) -> str:
    return f"metropolis/scada/{target.cell}/ack"


def validate_command(
    target: Target,
    payload: dict,
    *,
    now: datetime,
    max_age: float = COMMAND_MAX_AGE_SECONDS,
) -> int:
    """Return the Modbus register value for a fresh, cell-specific request."""
    if max_age <= 0:
        raise ValueError("COMMAND_MAX_AGE_SECONDS must be positive")
    if target.profile not in COMMANDS:
        raise ValueError("This SCADA target does not accept commands")
    expected_command, expected_unit, minimum, maximum = COMMANDS[target.profile]
    if payload.get("cell") != target.cell:
        raise ValueError("command cell does not match the MQTT topic")
    if not isinstance(payload.get("device"), str) or not payload["device"]:
        raise ValueError("command device is required")
    if payload.get("command") != expected_command:
        raise ValueError("command is not supported by this controller")
    if payload.get("unit") != expected_unit:
        raise ValueError("command unit does not match this controller")
    value = payload.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("command value must be numeric")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"command value must be between {minimum:g} and {maximum:g}")
    scaled = value * 10
    if abs(scaled - round(scaled)) > 1e-6:
        raise ValueError("command value must use increments of 0.1")
    timestamp_text = payload.get("timestamp")
    if not isinstance(timestamp_text, str):
        raise ValueError("command timestamp is required")
    try:
        sent_at = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("command timestamp is invalid") from exc
    if sent_at.tzinfo is None:
        raise ValueError("command timestamp must include a timezone")
    age = (now - sent_at.astimezone(timezone.utc)).total_seconds()
    if age > max_age or age < -5:
        raise ValueError("command timestamp is stale or in the future")
    return round(scaled)


def apply_setpoint(
    target: Target, register_value: int, *, port: int, unit_id: int
) -> None:
    """Require a successful PLC Modbus write and matching controller readback."""
    client = ModbusTcpClient(target.host, port=port, timeout=3, retries=0)
    try:
        if not client.connect():
            raise ConnectionError(f"controller {target.host}:{port} unavailable")
        written = client.write_register(
            address=SETPOINT_REGISTER, value=register_value, slave=unit_id
        )
        if written.isError():
            raise RuntimeError("controller rejected Modbus setpoint write")
        readback = client.read_holding_registers(
            address=SETPOINT_REGISTER, count=1, slave=unit_id
        )
        if (
            readback.isError()
            or len(readback.registers) != 1
            or readback.registers[0] != register_value
        ):
            raise RuntimeError("controller setpoint readback differs from request")
    finally:
        client.close()


class CommandBridge:
    """Consume HMI commands once and publish correlated PLC readback results."""

    def __init__(
        self,
        targets: list[Target],
        publisher: mqtt.Client,
        *,
        port: int,
        unit_id: int,
        command_max_age: float = COMMAND_MAX_AGE_SECONDS,
    ) -> None:
        if command_max_age <= 0:
            raise ValueError("COMMAND_MAX_AGE_SECONDS must be positive")
        self.targets = {
            command_topic(target): target
            for target in targets
            if target.profile in COMMANDS
        }
        self.publisher = publisher
        self.port = port
        self.unit_id = unit_id
        self.command_max_age = command_max_age
        self.seen: OrderedDict[tuple[str, str], dict] = OrderedDict()

    def _publish(self, target: Target, acknowledgement: dict) -> None:
        result = self.publisher.publish(
            ack_topic(target), json.dumps(acknowledgement), qos=1, retain=False
        )
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("PLC acknowledgement could not be queued for %s", target.cell)

    def handle(self, topic: str, raw: bytes) -> dict | None:
        target = self.targets.get(topic)
        if target is None or len(raw) > 4096:
            return None
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            log.warning("Ignored invalid HMI command JSON on %s", topic)
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("acl_probe") is True:
            run_id = payload.get("run_id")
            client_id = payload.get("client_id")
            kind = payload.get("kind")
            if (
                isinstance(run_id, str)
                and re.fullmatch(r"[A-Za-z0-9_-]+", run_id)
                and isinstance(client_id, str)
                and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", client_id)
                and isinstance(kind, str)
                and kind in {"own", "cross"}
            ):
                log.info(
                    "MQTT_ACL_PROBE %s",
                    json.dumps(
                        {
                            "run_id": run_id,
                            "client_id": client_id,
                            "kind": kind,
                            "topic": topic,
                        },
                        separators=(",", ":"),
                    ),
                )
            else:
                log.warning("Ignored invalid MQTT ACL probe on %s", topic)
            return None
        try:
            request_id = str(UUID(str(payload.get("request_id"))))
        except (TypeError, ValueError, AttributeError):
            log.warning("Ignored HMI command without a valid request ID")
            return None
        key = (target.cell, request_id)
        if key in self.seen:
            acknowledgement = self.seen[key]
            self._publish(target, acknowledgement)
            return acknowledgement

        acknowledgement = {
            "request_id": request_id,
            "cell": target.cell,
            "controller": target.host,
            "value": payload.get("value"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            register_value = validate_command(
                target,
                payload,
                now=datetime.now(timezone.utc),
                max_age=self.command_max_age,
            )
        except ValueError as exc:
            acknowledgement.update(status="rejected", reason=str(exc))
        else:
            try:
                apply_setpoint(
                    target, register_value, port=self.port, unit_id=self.unit_id
                )
                acknowledgement["status"] = "stored"
                acknowledgement["value"] = register_value / 10
            except Exception as exc:  # Keep the bridge alive after a Modbus failure.
                acknowledgement.update(status="failed", reason=str(exc))
        self.seen[key] = acknowledgement
        if len(self.seen) > 1024:
            self.seen.popitem(last=False)
        self._publish(target, acknowledgement)
        log.info(
            "HMI command %s for %s: %s",
            request_id,
            target.cell,
            acknowledgement["status"],
        )
        return acknowledgement


def _collect(cleanup: ExitStack) -> None:
    name = os.getenv("DEVICE_NAME", "MET-SCADA-01")
    exporter = TelemetryExporter.from_environment(name)
    targets = configured_targets(
        os.getenv("SCADA_TARGETS"),
        legacy_host=os.getenv("PLC_HOST", "10.20.10.10"),
        legacy_topic=os.getenv("MQTT_TOPIC", "metropolis/scada/intake/status"),
    )
    modbus_port = int(os.getenv("MODBUS_PORT", "502"))
    unit_id = int(os.getenv("MODBUS_UNIT_ID", "1"))
    mqtt_host = os.getenv("MQTT_HOST", "10.20.23.10")
    mqtt_port = int(os.getenv("MQTT_PORT", "1883"))
    interval = max(1.0, float(os.getenv("POLL_INTERVAL", "5")))
    heartbeat_monitor = ControllerHeartbeatMonitor(
        float(os.getenv("PLC_STALE_SECONDS", "15"))
    )
    opcua = OpcUaServerWorker.from_environment(
        name, [decode_registers(target, [0] * 6) for target in targets]
    )
    if opcua is not None:
        cleanup.callback(opcua.close)
        opcua.start()
    stop_dnp3 = threading.Event()
    dnp3_thread = None
    if dnp3_encoded := os.getenv("DNP3_TARGETS_B64"):
        from dnp3_master import configured_targets as configured_dnp3_targets
        from dnp3_master import start_master

        dnp3_targets = configured_dnp3_targets(dnp3_encoded)
        dnp3_thread = start_master(dnp3_targets, stop_dnp3)
        cleanup.callback(dnp3_thread.join, timeout=4)
        cleanup.callback(stop_dnp3.set)

    publisher = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{name}-mqtt")
    cleanup.callback(publisher.disconnect)
    cleanup.callback(publisher.loop_stop)
    publisher.username_pw_set(
        os.environ["MQTT_USERNAME"],
        os.environ["MQTT_PASSWORD"],
    )
    apply_to(publisher)
    publisher.reconnect_delay_set(min_delay=1, max_delay=30)
    bridge = CommandBridge(
        targets,
        publisher,
        port=modbus_port,
        unit_id=unit_id,
        command_max_age=float(os.getenv("COMMAND_MAX_AGE_SECONDS", "15")),
    )
    commands: queue.Queue[tuple[str, bytes]] = queue.Queue(maxsize=64)
    stop_commands = threading.Event()

    def on_connect(client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            log.warning("MQTT connection rejected: %s", reason_code)
            return
        for topic in bridge.targets:
            client.subscribe(topic, qos=1)

    def on_message(client, userdata, message) -> None:
        if message.topic not in bridge.targets or len(message.payload) > 4096:
            return
        try:
            commands.put_nowait((message.topic, bytes(message.payload)))
        except queue.Full:
            log.warning(
                "HMI command queue full; request on %s was dropped", message.topic
            )

    def process_commands() -> None:
        while not stop_commands.is_set():
            try:
                topic, raw = commands.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                bridge.handle(topic, raw)
            except Exception:
                log.exception("HMI command bridge failed")
            finally:
                commands.task_done()

    publisher.on_connect = on_connect
    publisher.on_message = on_message
    while True:
        try:
            publisher.connect(mqtt_host, mqtt_port, keepalive=30)
            break
        except OSError as exc:
            log.warning("MQTT broker not ready (%s); retrying in 5 seconds", exc)
            time.sleep(5)
    publisher.loop_start()
    command_worker = threading.Thread(target=process_commands, daemon=True)
    command_worker.start()
    cleanup.callback(command_worker.join, timeout=2)
    cleanup.callback(stop_commands.set)
    log.info("%s polling %s Modbus cells", name, len(targets))
    poll_ok: dict[str, bool] = {}
    poll_stale: dict[str, bool] = {}

    if exporter is not None:
        cleanup.callback(exporter.close)
        exporter.start()

    try:
        while True:
            statuses = []
            for target in targets:
                try:
                    payload = poll_target(target, port=modbus_port, unit_id=unit_id)
                    payload["device"] = name
                    fresh = heartbeat_monitor.observe(
                        target.cell, payload["heartbeat"], time.monotonic()
                    )
                    if not fresh:
                        payload["quality"] = "stale"
                        payload.setdefault("alarms", []).append("controller_stale")
                        if not poll_stale.get(target.cell):
                            log.warning("PLC heartbeat stopped for %s", target.cell)
                    elif poll_stale.get(target.cell):
                        log.info("PLC heartbeat resumed for %s", target.cell)
                    poll_stale[target.cell] = not fresh
                    if poll_ok.get(target.cell) is False:
                        payload["transition"] = "poll_recovered"
                        log.info("Modbus polling recovered for %s", target.cell)
                    publisher.publish(
                        target.topic, json.dumps(payload), qos=0, retain=False
                    )
                    poll_ok[target.cell] = True
                    log.info(
                        "Polled %s heartbeat=%s", target.cell, payload["heartbeat"]
                    )
                    statuses.append(payload)
                except Exception as exc:  # Keep other cells polling during an outage.
                    unavailable = {
                        "device": name,
                        "cell": target.cell,
                        "controller": target.host,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "quality": "unavailable",
                        "alarms": ["controller_unreachable"],
                        "transition": "poll_lost",
                    }
                    statuses.append(unavailable)
                    if poll_ok.get(target.cell) is not False:
                        publisher.publish(
                            target.topic,
                            json.dumps(unavailable),
                            qos=0,
                            retain=False,
                        )
                        log.warning("Poll failed for %s: %s", target.cell, exc)
                    poll_ok[target.cell] = False
                    poll_stale[target.cell] = False
                    heartbeat_monitor.forget(target.cell)
                if opcua is not None:
                    try:
                        opcua.submit(statuses[-1])
                    except Exception as exc:
                        log.warning(
                            "OPC UA update failed cell=%s error=%s",
                            target.cell,
                            type(exc).__name__,
                        )
            if exporter is not None:
                exporter.submit(statuses)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass


def main() -> None:
    def terminate(signum, frame) -> None:
        raise KeyboardInterrupt

    previous_term = signal.signal(signal.SIGTERM, terminate)
    try:
        with ExitStack() as cleanup:
            _collect(cleanup)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous_term)


if __name__ == "__main__":
    main()
