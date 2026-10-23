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
        out, ["localhost", "broker.example.test"], ["127.0.0.1", "192.0.2.10"]
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


def test_the_certificate_names_exactly_what_was_requested(certificates):
    """The SAN list is the contract, so it is read rather than assumed."""
    from cryptography import x509

    _, written = certificates
    certificate = x509.load_pem_x509_certificate(written["server"].read_bytes())
    san = certificate.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value

    assert set(san.get_values_for_type(x509.DNSName)) == {
        "localhost",
        "broker.example.test",
    }
    assert {str(item) for item in san.get_values_for_type(x509.IPAddress)} == {
        "127.0.0.1",
        "192.0.2.10",
    }


def test_the_ca_is_a_ca(certificates):
    """A leaf signed by something that cannot sign certificates is useless."""
    from cryptography import x509

    _, written = certificates
    ca = x509.load_pem_x509_certificate(written["ca"].read_bytes())
    constraints = ca.extensions.get_extension_for_class(x509.BasicConstraints).value

    assert constraints.ca is True


# -- sensor TLS integration ---------------------------------------------------


def sensor_module():
    return load("mqtt_sensor_tls", SENSORS / "mqtt_sensor.py")


def test_sensor_uses_shared_tls_configuration():
    import tls_config

    module = sensor_module()
    assert module.tls_from_environment is tls_config.from_environment
    assert module.TlsConfig is tls_config.TlsConfig
