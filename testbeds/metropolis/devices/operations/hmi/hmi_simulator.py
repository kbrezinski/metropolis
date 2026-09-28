"""Periodic HMI setpoint publisher for controlled Metropolis experiments."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("metropolis.hmi")


def main() -> None:
    name = os.getenv("DEVICE_NAME", "MET-HMI-INTAKE-01")
    broker = os.getenv("MQTT_HOST", "10.20.23.10")
    port = int(os.getenv("MQTT_PORT", "1883"))
    topic = os.getenv("MQTT_TOPIC", "metropolis/plant/intake/command")
    interval = max(5.0, float(os.getenv("PUBLISH_INTERVAL", "30")))
    setpoint = float(os.getenv("SETPOINT", "65.0"))
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=name)
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    while True:
        try:
            client.connect(broker, port, keepalive=30)
            break
        except OSError as exc:
            log.warning("Broker not ready (%s); retrying in 5 seconds", exc)
            time.sleep(5)
    client.loop_start()
    log.info("Publishing demonstration setpoint to %s", topic)
    try:
        while True:
            payload = {
                "device": name,
                "command": "level_setpoint",
                "value": setpoint,
                "unit": "percent",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            client.publish(topic, json.dumps(payload), qos=0, retain=False)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
