"""Paho v2 connection handling: broker rejection is distinct from timeout."""

from __future__ import annotations

import secrets
import sys
import threading
from pathlib import Path

# The toolkit reuses the same TLS settings the device models use, so a client
# here and a device there verify a broker identically.
_RUNTIME = Path(__file__).resolve().parents[2] / "testbeds/metropolis/devices/runtime"
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))
from tls_config import TlsConfig  # noqa: E402


class BrokerConnection:
    def __init__(
        self,
        host,
        port,
        username,
        password,
        timeout,
        tls_config: TlsConfig | None = None,
    ):
        import paho.mqtt.client as mqtt

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"metropolis-{secrets.token_hex(6)}",
            reconnect_on_failure=False,
        )
        self.client.connect_timeout = timeout
        self.client.username_pw_set(username, password)
        if tls_config is not None:
            tls_config.apply(self.client)
        self.host, self.port, self.timeout = host, port, timeout
        self.ready = threading.Event()
        self.reason = None
        self.status = "timeout"

        def connected(client, userdata, flags, reason_code, properties):
            self.reason = reason_code.value
            self.status = "accepted" if reason_code == 0 else "rejected"
            self.ready.set()

        def failed(client, userdata):
            self.status = "connection_failed"
            self.ready.set()

        self.client.on_connect = connected
        self.client.on_connect_fail = failed

    def __enter__(self):
        try:
            self.client.connect_async(self.host, self.port, keepalive=30)
            self.client.loop_start()
            self.ready.wait(self.timeout)
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        self.client.disconnect()
        self.client.loop_stop()

    def publish(self, topic, payload, retain, timeout):
        if self.status != "accepted" or not self.client.is_connected():
            raise ConnectionError(f"MQTT connection unavailable: {self.status}")
        result = self.client.publish(topic, payload, qos=1, retain=retain)
        if result.rc != 0:
            raise RuntimeError(f"MQTT publish failed: {result.rc}")
        result.wait_for_publish(timeout=timeout)
        if not result.is_published():
            raise TimeoutError("No MQTT PUBACK within timeout")
