"""HTTP operator panel with MQTT telemetry and setpoint requests."""

from __future__ import annotations

import json
import logging
import os
import threading

import paho.mqtt.client as mqtt

from hmi_web import HmiState, create_server


import sys
from pathlib import Path

# The shared TLS settings sit beside the entrypoint, which every image copies
# to /opt/metropolis.
sys.path.insert(0, str(Path("/opt/metropolis")))
from tls_config import TlsConfig, apply_to  # noqa: E402,F401


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("metropolis.hmi")


def main() -> None:
    name = os.getenv("DEVICE_NAME", "MET-HMI-INTAKE-01")
    broker = os.getenv("MQTT_HOST", "10.20.23.10")
    port = int(os.getenv("MQTT_PORT", "1883"))
    topic = os.getenv("MQTT_TOPIC", "metropolis/plant/intake/command")
    interval = max(5.0, float(os.getenv("PUBLISH_INTERVAL", "30")))
    state = HmiState(name, float(os.getenv("SETPOINT", "65.0")))
    server = create_server(
        state,
        os.getenv("HMI_USERNAME", "operator"),
        os.getenv("HMI_PASSWORD", ""),
        os.getenv("HMI_HTTP_HOST", "0.0.0.0"),
        int(os.getenv("HMI_HTTP_PORT", "8080")),
    )
    telemetry_topic = os.getenv(
        "MQTT_TELEMETRY_TOPIC", "metropolis/scada/intake/status"
    )
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=name)
    client.username_pw_set(
        os.getenv("MQTT_USERNAME", "lab_device"),
        os.getenv("MQTT_PASSWORD", "LabOnly_Device_2026"),
    )
    apply_to(client)
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    def on_connect(client, userdata, flags, reason_code, properties):
        connected = reason_code == 0
        state.set_connected(connected)
        if connected:
            client.subscribe(telemetry_topic, qos=0)
            state.changed.set()
        else:
            log.warning("MQTT connection rejected: %s", reason_code)

    def on_disconnect(client, userdata, flags, reason_code, properties):
        state.set_connected(False)

    def on_message(client, userdata, message):
        if message.topic != telemetry_topic or len(message.payload) > 16384:
            return
        try:
            payload = json.loads(message.payload)
            if isinstance(payload, dict):
                state.record_telemetry(payload)
        except (ValueError, UnicodeError):
            log.warning("Ignored invalid telemetry JSON")

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    web_thread = threading.Thread(target=server.serve_forever, daemon=True)
    web_thread.start()
    log.info("HMI HTTP listening at %s:%s", *server.server_address)
    try:
        # The panel remains available while MQTT reconnects in the background.
        client.connect_async(broker, port, keepalive=30)
        client.loop_start()
        while True:
            state.changed.clear()
            payload = state.command_payload()
            result = client.publish(topic, json.dumps(payload), qos=0, retain=False)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                state.record_publish()
            else:
                log.warning("Setpoint was not queued: MQTT result %s", result.rc)
            state.changed.wait(interval)
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()
        client.loop_stop()
        server.shutdown()
        server.server_close()
        web_thread.join(timeout=5)


if __name__ == "__main__":
    main()
