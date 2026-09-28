"""Poll a Modbus TCP controller and publish its snapshot over MQTT."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from pymodbus.client import ModbusTcpClient


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("metropolis.scada")


def main() -> None:
    name = os.getenv("DEVICE_NAME", "MET-SCADA-01")
    plc_host = os.getenv("PLC_HOST", "10.20.10.10")
    modbus_port = int(os.getenv("MODBUS_PORT", "502"))
    unit_id = int(os.getenv("MODBUS_UNIT_ID", "1"))
    mqtt_host = os.getenv("MQTT_HOST", "10.20.23.10")
    mqtt_port = int(os.getenv("MQTT_PORT", "1883"))
    topic = os.getenv("MQTT_TOPIC", "metropolis/scada/intake/status")
    interval = max(1.0, float(os.getenv("POLL_INTERVAL", "5")))

    publisher = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{name}-mqtt")
    publisher.reconnect_delay_set(min_delay=1, max_delay=30)
    while True:
        try:
            publisher.connect(mqtt_host, mqtt_port, keepalive=30)
            break
        except OSError as exc:
            log.warning("MQTT broker not ready (%s); retrying in 5 seconds", exc)
            time.sleep(5)
    publisher.loop_start()
    log.info("%s polling %s:%s and publishing to %s", name, plc_host, modbus_port, topic)

    try:
        while True:
            client = ModbusTcpClient(plc_host, port=modbus_port, timeout=3, retries=0)
            try:
                if not client.connect():
                    raise ConnectionError(f"Unable to connect to controller {plc_host}:{modbus_port}")
                response = client.read_holding_registers(address=0, count=4, slave=unit_id)
                if response.isError():
                    raise RuntimeError(f"Controller returned Modbus error: {response}")
                registers = response.registers
                payload = {
                    "device": name,
                    "controller": plc_host,
                    "level_percent": registers[0] / 10,
                    "flow_rate": registers[1] / 10,
                    "quality_percent": registers[2] / 10,
                    "heartbeat": registers[3],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                publisher.publish(topic, json.dumps(payload), qos=0, retain=False)
                log.info("Polled controller heartbeat=%s", registers[3])
            except Exception as exc:  # Keep the emulated SCADA node alive during outages.
                log.warning("Poll failed: %s", exc)
            finally:
                client.close()
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        publisher.loop_stop()
        publisher.disconnect()


if __name__ == "__main__":
    main()
