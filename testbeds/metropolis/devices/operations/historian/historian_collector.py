"""MQTT historian subscriber; writes received events to container stdout."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(message)s")
log = logging.getLogger("metropolis.historian")


def main() -> None:
    name = os.getenv("DEVICE_NAME", "MET-HISTORIAN-01")
    broker = os.getenv("MQTT_HOST", "10.20.23.10")
    port = int(os.getenv("MQTT_PORT", "1883"))
    topic = os.getenv("MQTT_TOPIC", "metropolis/#")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=name)

    def on_connect(client, userdata, flags, reason_code, properties) -> None:
        if reason_code == 0:
            client.subscribe(topic, qos=0)
            log.info('{"event":"connected","device":"%s","subscription":"%s"}', name, topic)
        else:
            log.warning("MQTT connection rejected: %s", reason_code)

    def on_message(client, userdata, message) -> None:
        log.info(
            '{"received_at":"%s","topic":"%s","payload":%s}',
            datetime.now(timezone.utc).isoformat(),
            message.topic,
            message.payload.decode("utf-8", errors="replace"),
        )

    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    while True:
        try:
            client.connect(broker, port, keepalive=30)
            break
        except OSError as exc:
            log.warning("Broker not ready (%s); retrying in 5 seconds", exc)
            time.sleep(5)
    log.info("%s subscribing to %s at %s:%s", name, topic, broker, port)
    client.loop_forever()


if __name__ == "__main__":
    main()
