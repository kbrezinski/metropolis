"""Checks for the shared device TLS settings and how the clients use them.

TLS is switched on by environment, so these read the settings the same way a
container would and confirm each device actually applies them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEVICES = ROOT / "testbeds/metropolis/devices"
RUNTIME = DEVICES / "runtime"
ATTACKS = ROOT / "scripts/attacks"

if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))
if str(ATTACKS) not in sys.path:
    sys.path.insert(0, str(ATTACKS))


@pytest.fixture(scope="module")
def tls_module():
    import tls_config

    return tls_config


@pytest.fixture(scope="module")
def ca_file(tmp_path_factory):
    """A real file to stand in for a CA, since existence is checked."""
    path = tmp_path_factory.mktemp("ca") / "ca.crt"
    path.write_text("-----BEGIN CERTIFICATE-----\nlab\n", encoding="utf-8")
    return path


# -- shared settings ----------------------------------------------------------


def test_a_missing_ca_is_refused_rather_than_ignored(tls_module):
    """Failing here beats failing at connect time with a TLS error."""
    with pytest.raises(SystemExit) as caught:
        tls_module.from_environment({"TLS": "true", "TLS_CA_FILE": "/nope/ca.crt"})
    assert "does not exist" in str(caught.value)


def test_insecure_is_opt_in_only(tls_module, ca_file):
    plain = tls_module.from_environment({"TLS": "true", "TLS_CA_FILE": str(ca_file)})
    assert plain.insecure is False
    relaxed = tls_module.from_environment(
        {"TLS": "true", "TLS_CA_FILE": str(ca_file), "TLS_INSECURE": "true"}
    )
    assert relaxed.insecure is True


def test_apply_to_reads_the_environment(tls_module, ca_file):
    class FakeClient:
        def __init__(self):
            self.configured = False

        def tls_set(self, **kwargs):
            self.configured = True

    client = FakeClient()
    assert tls_module.apply_to(client, {"TLS": "true", "TLS_CA_FILE": str(ca_file)})
    assert client.configured is True

    untouched = FakeClient()
    assert tls_module.apply_to(untouched, {}) is None
    assert untouched.configured is False


# -- the attack toolkit -------------------------------------------------------


def test_the_broker_connection_accepts_and_applies_tls(monkeypatch):
    """The toolkit must be able to attack an encrypted broker, not just a plain one."""
    import _mqtt
    import paho.mqtt.client as paho

    applied = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def username_pw_set(self, *args):
            pass

        def tls_set(self, **kwargs):
            applied.update(kwargs)

        def tls_insecure_set(self, value):
            applied["insecure"] = value

    monkeypatch.setattr(paho, "Client", FakeClient)
    settings = _mqtt.TlsConfig(ca_file="/certs/ca.crt", insecure=True)

    connection = _mqtt.BrokerConnection(
        "10.20.23.10", 8883, "lab_operator", "secret", 3.0, settings
    )

    assert applied["ca_certs"] == "/certs/ca.crt"
    assert applied["insecure"] is True
    assert connection.status == "timeout", "no connection is attempted yet"
