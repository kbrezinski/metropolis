"""Checks for the lab certificate authority and the sensor's TLS settings.

The certificates are used for the first time here as well as generated: a real
TLS handshake runs over loopback, so "the files exist" is not mistaken for "TLS
works".
"""

from __future__ import annotations

import importlib.util
import socket
import ssl
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SENSORS = ROOT / "testbeds/metropolis/devices/field/sensors"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load(name: str, path: Path):
    directory = str(path.parent)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    return module


@pytest.fixture(scope="module")
def certificates(tmp_path_factory):
    module = load("gen_lab_certificates", SCRIPTS / "gen_lab_certificates.py")
    out = tmp_path_factory.mktemp("certs")
    written = module.generate(
        out, ["localhost", "MET-MQTT-BROKER-01"], ["127.0.0.1", "10.20.23.10"]
    )
    return module, written


# -- certificates -------------------------------------------------------------


def test_generate_writes_every_file_needed(certificates):
    _, written = certificates
    # The CA, the server certificate, and its key: nothing else is needed now
    # that the DTLS pre-shared key file is gone.
    assert set(written) == {"ca", "server", "key"}
    for label, path in written.items():
        assert path.is_file(), f"{label} was not written"
        assert path.stat().st_size > 0


def test_the_server_certificate_loads_into_a_tls_context(certificates):
    _, written = certificates
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(written["server"], written["key"])


def test_a_real_handshake_succeeds_and_validates_the_hostname(certificates):
    """The pair must actually agree, not merely parse."""
    _, written = certificates
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(written["server"], written["key"])

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve():
        connection, _ = listener.accept()
        with server_context.wrap_socket(connection, server_side=True) as session:
            session.recv(16)
            session.sendall(b"ok")

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()

    client_context = ssl.create_default_context(cafile=str(written["ca"]))
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with client_context.wrap_socket(raw, server_hostname="localhost") as session:
            session.sendall(b"hi")
            assert session.recv(16) == b"ok"
    worker.join(timeout=5)
    listener.close()


def test_the_certificate_covers_the_addresses_clients_use(certificates):
    """A device reaching the broker by address must not fail verification."""
    module, written = certificates
    assert "10.20.23.10" in module.DEFAULT_ADDRESSES
    assert "MET-MQTT-BROKER-01" in module.DEFAULT_NAMES


def test_the_certificate_names_exactly_what_was_requested(certificates):
    """The SAN list is the contract, so it is read rather than assumed."""
    from cryptography import x509

    module, written = certificates
    certificate = x509.load_pem_x509_certificate(written["server"].read_bytes())
    san = certificate.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value

    assert set(san.get_values_for_type(x509.DNSName)) == {
        "localhost",
        "MET-MQTT-BROKER-01",
    }
    assert {str(item) for item in san.get_values_for_type(x509.IPAddress)} == {
        "127.0.0.1",
        "10.20.23.10",
    }


def test_the_ca_is_a_ca(certificates):
    """A leaf signed by something that cannot sign certificates is useless."""
    from cryptography import x509

    _, written = certificates
    ca = x509.load_pem_x509_certificate(written["ca"].read_bytes())
    constraints = ca.extensions.get_extension_for_class(x509.BasicConstraints).value

    assert constraints.ca is True


# -- sensor TLS settings ------------------------------------------------------


def sensor_module():
    return load("mqtt_sensor_tls", SENSORS / "mqtt_sensor.py")


def test_tls_is_off_unless_asked_for(monkeypatch):
    module = sensor_module()
    monkeypatch.setattr(module.os, "environ", {})
    assert module.tls_from_environment() is None


def test_tls_settings_come_from_the_environment(certificates, monkeypatch):
    module = sensor_module()
    _, written = certificates
    monkeypatch.setattr(
        module.os,
        "environ",
        {
            "TLS": "true",
            "TLS_CA_FILE": str(written["ca"]),
            "TLS_INSECURE": "true",
        },
    )
    config = module.tls_from_environment()

    assert config is not None
    assert config.ca_file == str(written["ca"])
    assert config.insecure is True
    assert config.paho_tls_args() == {
        "ca_certs": str(written["ca"]),
        "insecure": True,
    }


def test_a_missing_ca_file_is_refused(monkeypatch):
    """Starting with TLS on and no CA would fail at connect time instead."""
    module = sensor_module()
    monkeypatch.setattr(
        module.os, "environ", {"TLS": "true", "TLS_CA_FILE": "/nope/missing.crt"}
    )
    with pytest.raises(SystemExit) as caught:
        module.tls_from_environment()
    assert "does not exist" in str(caught.value)


def test_applying_tls_configures_verification(certificates):
    module = sensor_module()
    _, written = certificates
    calls: list[dict] = []

    class FakeClient:
        def tls_set(self, **kwargs):
            calls.append(kwargs)

        def tls_insecure_set(self, value):
            calls.append({"insecure": value})

    config = module.TlsConfig(ca_file=str(written["ca"]), insecure=True)
    config.apply(FakeClient())

    assert calls[0]["ca_certs"] == str(written["ca"])
    assert calls[1] == {"insecure": True}


def test_insecure_is_not_the_default(certificates):
    module = sensor_module()
    _, written = certificates
    assert module.TlsConfig(ca_file=str(written["ca"])).insecure is False
