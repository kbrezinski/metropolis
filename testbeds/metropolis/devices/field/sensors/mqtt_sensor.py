"""MQTT telemetry publisher for a simulated OT sensor.

By default the sensor reports the process reading it reads from the PLC over
Modbus, so its published value and the PLC's registers agree and both move. Set
``PLC_HOST`` to the controller to enable that; without it the sensor publishes
the fixed ``SENSOR_VALUE``, which is useful for testing the sensor alone.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from coap_sensor import serve as serve_coap


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("metropolis.sensor")


class ProcessReader:
    """Reads one holding register from the PLC, holding the last good value.

    A failed read keeps the previous value rather than stopping the sensor: a
    sensor that loses its controller still has a last known reading, and the gap
    is visible in the repeated timestamps.
    """

    def __init__(
        self, host: str, port: int, unit_id: int, register: int, divisor: float
    ):
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.register = register
        self.divisor = divisor
        self.value: float | None = None

    def __call__(self) -> float:
        from pymodbus.client import ModbusTcpClient

        try:
            client = ModbusTcpClient(self.host, port=self.port, timeout=2, retries=0)
            try:
                if not client.connect():
                    raise ConnectionError("PLC connection failed")
                response = client.read_holding_registers(
                    address=self.register, count=1, slave=self.unit_id
                )
                if response.isError():
                    raise RuntimeError(f"Modbus error: {response}")
                raw = response.registers[0]
            finally:
                client.close()
        except Exception as exc:  # noqa: BLE001 - reported, then last value kept
            if self.value is None:
                raise
            log.warning("Keeping last reading; PLC read failed: %s", exc)
            return self.value
        self.value = round(raw / self.divisor, 1)
        return self.value


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
            register=int(os.getenv("SENSOR_REGISTER", "0")),
            divisor=float(os.getenv("SENSOR_DIVISOR", "10")),
        )
        log.info(
            "Reporting %s from %s holding register %s",
            sensor_type,
            plc_host,
            value.register,
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

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=name)
    username = os.getenv("MQTT_USERNAME", "lab_device")
    password = os.getenv("MQTT_PASSWORD", "LabOnly_Device_2026")
    client.username_pw_set(username, password)
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    while True:
        try:
            client.connect(broker, port, keepalive=30)
            break
        except OSError as exc:
            log.warning("Broker not ready (%s); retrying in 5 seconds", exc)
            time.sleep(5)
    client.loop_start()
    log.info("Publishing %s telemetry to %s:%s/%s", name, broker, port, topic)
    try:
        while True:
            try:
                reading = value() if callable(value) else value
            except Exception as exc:  # noqa: BLE001 - no reading yet, keep trying
                log.warning("No process reading available yet: %s", exc)
                time.sleep(interval)
                continue
            payload = {
                "device": name,
                "sensor": sensor_type,
                "value": reading,
                "unit": "percent"
                if sensor_type in {"level", "quality"}
                else "unit_per_second",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            result = client.publish(topic, json.dumps(payload), qos=0, retain=False)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                log.warning("MQTT publish returned status %s", result.rc)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
