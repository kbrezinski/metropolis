"""MQTT telemetry publisher for a simulated OT sensor.

By default the sensor reports the process reading it reads from the PLC over
Modbus, so its published value and the PLC's registers agree and both move. Set
``PLC_HOST`` to the controller to enable that; without it the sensor publishes
the fixed ``SENSOR_VALUE``, which is useful for testing the sensor alone.

Two publish modes are supported, because they look different on the wire:

``persistent``
    One long-lived session, which is what a well-behaved device looks like.
``per-message``
    A connection per message, which is what a device that cannot hold a session
    looks like: a repeated connect and disconnect cadence rather than one flow.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import paho.mqtt.client as mqtt
import paho.mqtt.publish as publish

from coap_sensor import serve as serve_coap


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("metropolis.sensor")

PER_MESSAGE = "per-message"
PERSISTENT = "persistent"


# Holding-register numbers, matching the controller's documented map.
LEVEL_REGISTER = 0
FLOW_REGISTER = 1
QUALITY_REGISTER = 2

# Derived measurements, so the CoAP resources have something to report that the
# PLC does not hold directly. Each is a plain function of the process state, so
# they move with it rather than being noise.
PRESSURE_BASE = 3.0
PRESSURE_PER_LEVEL = 0.02
TEMPERATURE_BASE = 18.0
TEMPERATURE_PER_QUALITY = 0.01
TURBIDITY_BASE = 1.0
TURBIDITY_PER_QUALITY = -0.005


class ProcessReader:
    """Reads the PLC's process registers and the pump coil.

    Returns every available measurement as a mapping, so one CoAP resource can
    be served per measurement, and the sensor's own MQTT value is taken from the
    same reading. A failed read keeps the previous values rather than stopping
    the sensor: a sensor that loses its controller still has a last known
    reading, and the gap is visible in the repeated timestamps.
    """

    def __init__(self, host: str, port: int, unit_id: int, divisor: float):
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.divisor = divisor
        self.values: dict[str, float] | None = None

    def __call__(self) -> dict[str, float]:
        from pymodbus.client import ModbusTcpClient

        try:
            client = ModbusTcpClient(self.host, port=self.port, timeout=2, retries=0)
            try:
                if not client.connect():
                    raise ConnectionError("PLC connection failed")
                response = client.read_holding_registers(
                    address=0, count=4, slave=self.unit_id
                )
                if response.isError():
                    raise RuntimeError(f"Modbus error: {response}")
                # The heartbeat is the fourth register; it is not a measurement.
                level, flow, quality = response.registers[:3]
                coil = client.read_coils(address=0, count=1, slave=self.unit_id)
                pump = bool(coil.bits[0]) if not coil.isError() else False
            finally:
                client.close()
        except Exception as exc:  # noqa: BLE001 - reported, then last values kept
            if self.values is None:
                raise
            log.warning("Keeping last readings; PLC read failed: %s", exc)
            return self.values

        scale = lambda raw: round(raw / self.divisor, 1)  # noqa: E731 - short and local
        level_pct = scale(level)
        quality_pct = scale(quality)
        self.values = {
            "level": level_pct,
            "flow": scale(flow),
            "quality": quality_pct,
            "pressure": round(PRESSURE_BASE + PRESSURE_PER_LEVEL * level_pct, 1),
            "temperature": round(
                TEMPERATURE_BASE + TEMPERATURE_PER_QUALITY * quality_pct, 1
            ),
            "turbidity": round(TURBIDITY_BASE + TURBIDITY_PER_QUALITY * quality_pct, 2),
            "pump": 1.0 if pump else 0.0,
        }
        return self.values


@dataclass
class TlsConfig:
    """How to reach the broker over TLS.

    ``insecure`` keeps the encryption but stops checking the certificate's
    hostname, which is useful when a device reaches the broker by an address the
    certificate does not name. It is a lab convenience, not a safe default.
    """

    ca_file: str | None = None
    insecure: bool = False
    client_cert: str | None = None
    client_key: str | None = None

    def apply(self, client) -> None:
        client.tls_set(
            ca_certs=self.ca_file,
            certfile=self.client_cert,
            keyfile=self.client_key,
        )
        if self.insecure:
            client.tls_insecure_set(True)

    def paho_tls_args(self) -> dict:
        """The same settings in the dictionary ``publish.single`` expects."""
        args: dict = {"ca_certs": self.ca_file}
        if self.insecure:
            args["insecure"] = True
        if self.client_cert:
            args["certfile"] = self.client_cert
        if self.client_key:
            args["keyfile"] = self.client_key
        return args


def tls_from_environment() -> TlsConfig | None:
    """Read the TLS settings, or None when TLS is not switched on."""
    if os.getenv("TLS", "").strip().lower() not in {"1", "true", "yes"}:
        return None
    ca_file = os.getenv("TLS_CA_FILE") or None
    if ca_file and not Path(ca_file).is_file():
        raise SystemExit(f"TLS is enabled but the CA file {ca_file} does not exist")
    return TlsConfig(
        ca_file=ca_file,
        insecure=os.getenv("TLS_INSECURE", "false").strip().lower()
        in {"1", "true", "yes"},
        client_cert=os.getenv("TLS_CLIENT_CERT") or None,
        client_key=os.getenv("TLS_CLIENT_KEY") or None,
    )


@dataclass
class Publisher:
    """Sends one telemetry message, in whichever mode was chosen."""

    mode: str
    send: Callable[[str], int]
    client: "mqtt.Client | None" = None

    def close(self) -> None:
        if self.client is not None:
            self.client.loop_stop()
            self.client.disconnect()


def build_publisher(
    *,
    broker: str,
    port: int,
    topic: str,
    client_id: str,
    username: str,
    password: str,
    mode: str,
    tls_config: "TlsConfig | None" = None,
) -> Publisher:
    """Build the publisher for a mode, connecting first if it holds a session.

    ``tls_config`` encrypts the connection. Both publish modes support it, so
    the encrypted and plaintext planes produce the same traffic shapes.
    """
    selected = mode.strip().lower()
    if selected not in {PERSISTENT, PER_MESSAGE}:
        raise SystemExit(
            f"PUBLISH_MODE must be {PERSISTENT!r} or {PER_MESSAGE!r}, not {mode!r}"
        )

    tls_args = tls_config.paho_tls_args() if tls_config else None

    if selected == PER_MESSAGE:
        auth = {"username": username, "password": password}

        def send_one_shot(document: str) -> int:
            try:
                publish.single(
                    topic=topic,
                    payload=document,
                    qos=0,
                    retain=False,
                    hostname=broker,
                    port=port,
                    client_id=f"{client_id}-oneshot",
                    auth=auth,
                    tls=tls_args,
                )
            except (OSError, ValueError) as exc:
                log.warning("One-shot publish failed: %s", exc)
                return mqtt.MQTT_ERR_NO_CONN
            return mqtt.MQTT_ERR_SUCCESS

        return Publisher(mode=selected, send=send_one_shot)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    client.username_pw_set(username, password)
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    if tls_config is not None:
        tls_config.apply(client)
    while True:
        try:
            client.connect(broker, port, keepalive=30)
            break
        except OSError as exc:
            log.warning("Broker not ready (%s); retrying in 5 seconds", exc)
            time.sleep(5)
    client.loop_start()

    def send_persistent(document: str) -> int:
        return client.publish(topic, document, qos=0, retain=False).rc

    return Publisher(mode=selected, send=send_persistent, client=client)


def main() -> None:
    name = os.getenv("DEVICE_NAME", "MET-SENSOR-INTAKE-01")
    broker = os.getenv("MQTT_HOST", "10.20.23.10")
    port = int(os.getenv("MQTT_PORT", "1883"))
    topic = os.getenv("MQTT_TOPIC", "metropolis/plant/intake/telemetry")
    interval = max(1.0, float(os.getenv("PUBLISH_INTERVAL", "5")))
    sensor_type = os.getenv("SENSOR_TYPE", "level")
    static_value = float(os.getenv("SENSOR_VALUE", "65.0"))

    # Reading the process is the point of the model, so it is the default when a
    # controller is configured. Registers hold tenths of a unit.
    plc_host = os.getenv("PLC_HOST", "").strip()
    if plc_host:
        value = ProcessReader(
            host=plc_host,
            port=int(os.getenv("MODBUS_PORT", "502")),
            unit_id=int(os.getenv("MODBUS_UNIT_ID", "1")),
            divisor=float(os.getenv("SENSOR_DIVISOR", "10")),
        )
        log.info(
            "Reporting %s from %s (level, flow, quality, pressure, temperature, "
            "turbidity, pump)",
            sensor_type,
            plc_host,
        )
    else:
        value = static_value
        log.info("No PLC_HOST configured; publishing the fixed value %s", static_value)

    if os.getenv("COAP_ENABLED", "true").lower() in {"1", "true", "yes"}:
        threading.Thread(
            target=serve_coap,
            args=(
                os.getenv("COAP_HOST", "0.0.0.0"),
                int(os.getenv("COAP_PORT", "5683")),
                name,
                sensor_type,
                value,
            ),
            daemon=True,
        ).start()

    publisher = build_publisher(
        broker=broker,
        port=port,
        topic=topic,
        client_id=name,
        username=os.getenv("MQTT_USERNAME", "lab_device"),
        password=os.getenv("MQTT_PASSWORD", "LabOnly_Device_2026"),
        mode=os.getenv("PUBLISH_MODE", PERSISTENT),
        tls_config=tls_from_environment(),
    )
    log.info(
        "Publishing %s telemetry to %s:%s/%s in %s mode",
        name,
        broker,
        port,
        topic,
        publisher.mode,
    )

    try:
        while True:
            try:
                resolved = value() if callable(value) else value
            except Exception as exc:  # noqa: BLE001 - no reading yet, keep trying
                log.warning("No process reading available yet: %s", exc)
                time.sleep(interval)
                continue
            # A reader supplies every measurement; this sensor publishes its own.
            if isinstance(resolved, dict):
                reading = resolved.get(sensor_type, next(iter(resolved.values()), None))
            else:
                reading = resolved
            payload = {
                "device": name,
                "sensor": sensor_type,
                "value": reading,
                "unit": "percent"
                if sensor_type in {"level", "quality"}
                else "unit_per_second",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            status = publisher.send(json.dumps(payload))
            if status != mqtt.MQTT_ERR_SUCCESS:
                log.warning("MQTT publish returned status %s", status)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        publisher.close()


if __name__ == "__main__":
    main()
