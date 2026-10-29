"""One inert MQTT topic-permission probe from the HMI's own container."""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from tls_config import apply_to


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def probe(allowed_topic: str, cross_topic: str, client_id: str, run_id: str) -> dict:
    """Send invalid-command JSON so accidental delivery cannot actuate a controller."""
    configured_topic = os.environ.get("MQTT_TOPIC")
    if not configured_topic or configured_topic != allowed_topic:
        raise ValueError("allowed topic differs from this HMI's configured topic")
    if (
        cross_topic == allowed_topic
        or any(char in cross_topic for char in ("+", "#", "\x00"))
        or not cross_topic
    ):
        raise ValueError("cross-cell topic must be distinct and concrete")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("invalid run ID")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", client_id):
        raise ValueError("invalid client ID")
    username = os.environ.get("MQTT_USERNAME")
    password = os.environ.get("MQTT_PASSWORD")
    if not username or not password:
        raise ValueError("MQTT credentials are missing")

    connected = threading.Event()
    accepted = {"value": False}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    client.username_pw_set(username, password)
    apply_to(client)

    def on_connect(_client, _userdata, _flags, reason_code, _properties):
        accepted["value"] = reason_code == 0
        connected.set()

    client.on_connect = on_connect
    result = {
        "status": "failed",
        "run_id": run_id,
        "client_id": client_id,
        "source": os.environ.get("NODE_HOSTNAME", "unknown"),
        "allowed_topic": allowed_topic,
        "cross_topic": cross_topic,
        "connected": False,
        "attempts": [],
    }
    try:
        client.connect(
            os.environ["MQTT_HOST"], int(os.environ.get("MQTT_PORT", "1883")), 10
        )
        client.loop_start()
        if not connected.wait(8) or not accepted["value"]:
            raise RuntimeError("MQTT connection was not accepted")
        result["connected"] = True
        for label, topic in (("own", allowed_topic), ("cross", cross_topic)):
            timestamp = utc_now()
            payload = json.dumps(
                {
                    "acl_probe": True,
                    "run_id": run_id,
                    "client_id": client_id,
                    "kind": label,
                },
                separators=(",", ":"),
            )
            published = client.publish(topic, payload, qos=1, retain=False)
            if published.rc != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError("MQTT publish could not be queued")
            published.wait_for_publish(timeout=5)
            result["attempts"].append(
                {
                    "kind": label,
                    "topic": topic,
                    "timestamp": timestamp,
                    "acknowledged": published.is_published(),
                }
            )
            if not published.is_published():
                raise TimeoutError("MQTT publish acknowledgement timed out")
            # Keep the broker log entries in a stable order for later evidence checks.
            time.sleep(0.1)
        result["status"] = "sent"
    except Exception as exc:  # noqa: BLE001 - never echo credentials or broker output
        result["reason"] = type(exc).__name__
    finally:
        client.disconnect()
        client.loop_stop()
        result["stopped_at_utc"] = utc_now()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allowed-topic", required=True)
    parser.add_argument("--cross-topic", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        result = probe(
            args.allowed_topic, args.cross_topic, args.client_id, args.run_id
        )
    except Exception as exc:  # noqa: BLE001 - only a safe error category leaves the container
        result = {
            "status": "failed",
            "reason": type(exc).__name__,
            "stopped_at_utc": utc_now(),
        }
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["status"] == "sent" else 1


if __name__ == "__main__":
    raise SystemExit(main())
