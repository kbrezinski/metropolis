"""TLS settings shared by every Metropolis device that talks MQTT.

One implementation, because the sensor, SCADA, the HMI, and the historian all
need the same rules: read the settings from the environment, refuse to start on
a half-configured TLS setup, and otherwise leave the connection alone.

TLS is off unless ``TLS=true``, so a plaintext lab behaves exactly as it did
before this existed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

TRUE_VALUES = {"1", "true", "yes", "on"}


def enabled(value: str | None) -> bool:
    """True when an environment value asks for something to be switched on."""
    return (value or "").strip().lower() in TRUE_VALUES


@dataclass
class TlsConfig:
    """How to reach a broker over TLS.

    ``insecure`` keeps the encryption but stops checking the certificate's
    hostname, which is useful when a device reaches the broker by an address the
    certificate does not name. It is a lab convenience, not a safe default.
    """

    ca_file: str | None = None
    insecure: bool = False
    client_cert: str | None = None
    client_key: str | None = None

    def apply(self, client) -> None:
        """Configure a paho client to use TLS."""
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


def from_environment(env: dict[str, str] | None = None) -> TlsConfig | None:
    """Read the TLS settings, or None when TLS is not switched on.

    A CA path that does not exist is an error rather than a warning: starting
    with TLS on and no trust anchor fails later, at connect time, where it is
    harder to diagnose.
    """
    source = os.environ if env is None else env
    if not enabled(source.get("TLS")):
        return None
    ca_file = source.get("TLS_CA_FILE") or None
    if ca_file and not Path(ca_file).is_file():
        raise SystemExit(f"TLS is enabled but the CA file {ca_file} does not exist")
    return TlsConfig(
        ca_file=ca_file,
        insecure=enabled(source.get("TLS_INSECURE")),
        client_cert=source.get("TLS_CLIENT_CERT") or None,
        client_key=source.get("TLS_CLIENT_KEY") or None,
    )


def apply_to(client, env: dict[str, str] | None = None) -> TlsConfig | None:
    """Configure a paho client from the environment, if TLS is on."""
    config = from_environment(env)
    if config is not None:
        config.apply(client)
    return config
