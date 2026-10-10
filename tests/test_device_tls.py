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


def test_tls_is_off_by_default(tls_module):
    assert tls_module.from_environment({}) is None


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_tls_switches_on_for_the_usual_spellings(tls_module, value, ca_file):
    config = tls_module.from_environment({"TLS": value, "TLS_CA_FILE": str(ca_file)})
    assert config is not None
    assert config.ca_file == str(ca_file)


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_other_values_leave_tls_off(tls_module, value):
    assert tls_module.from_environment({"TLS": value}) is None


def test_a_missing_ca_is_refused_rather_than_ignored(tls_module):
    """Failing here beats failing at connect time with a TLS error."""
    with pytest.raises(SystemExit) as caught:
        tls_module.from_environment({"TLS": "true", "TLS_CA_FILE": "/nope/ca.crt"})
    assert "does not exist" in str(caught.value)


def test_the_ca_may_be_omitted_to_use_the_system_store(tls_module):
    config = tls_module.from_environment({"TLS": "true"})
    assert config is not None
    assert config.ca_file is None


def test_insecure_is_opt_in_only(tls_module, ca_file):
    plain = tls_module.from_environment({"TLS": "true", "TLS_CA_FILE": str(ca_file)})
    assert plain.insecure is False
    relaxed = tls_module.from_environment(
        {"TLS": "true", "TLS_CA_FILE": str(ca_file), "TLS_INSECURE": "true"}
    )
    assert relaxed.insecure is True


def test_client_certificates_are_passed_through(tls_module, ca_file):
    config = tls_module.from_environment(
        {
            "TLS": "true",
            "TLS_CA_FILE": str(ca_file),
            "TLS_CLIENT_CERT": "/certs/client.crt",
            "TLS_CLIENT_KEY": "/certs/client.key",
        }
    )
    assert config.client_cert == "/certs/client.crt"
    assert config.client_key == "/certs/client.key"


def test_apply_configures_a_paho_client(tls_module, ca_file):
    calls = []

    class FakeClient:
        def tls_set(self, **kwargs):
            calls.append(("tls_set", kwargs))

        def tls_insecure_set(self, value):
            calls.append(("insecure", value))

    config = tls_module.TlsConfig(ca_file=str(ca_file), insecure=True)
    config.apply(FakeClient())

    assert calls[0][0] == "tls_set"
    assert calls[0][1]["ca_certs"] == str(ca_file)
    assert calls[1] == ("insecure", True)


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


# -- the device models --------------------------------------------------------


MQTT_CLIENTS = {
    "scada": DEVICES / "operations/scada/scada_collector.py",
    "hmi": DEVICES / "operations/hmi/hmi_simulator.py",
    "historian": DEVICES / "operations/historian/historian_collector.py",
}


@pytest.mark.parametrize("name", sorted(MQTT_CLIENTS))
def test_each_mqtt_client_applies_the_shared_tls_settings(name):
    """Every device that talks MQTT must be able to encrypt, not just the sensor."""
    source = MQTT_CLIENTS[name].read_text(encoding="utf-8")
    assert "from tls_config import" in source, f"{name} does not import the settings"
    assert "apply_to(" in source, f"{name} never applies the settings"


@pytest.mark.parametrize("name", sorted(MQTT_CLIENTS))
def test_each_image_carries_the_settings_module(name):
    """Importing a module the image does not contain fails at container start."""
    dockerfile = MQTT_CLIENTS[name].parent / "Dockerfile"
    assert "tls_config.py /opt/metropolis/tls_config.py" in dockerfile.read_text(
        encoding="utf-8"
    )


def test_the_sensor_uses_the_same_module():
    source = (DEVICES / "field/sensors/mqtt_sensor.py").read_text(encoding="utf-8")
    assert "from tls_config import" in source
    # It must not carry its own copy, or the two would drift.
    assert "class TlsConfig" not in source


def test_the_sensor_image_carries_the_settings_module():
    dockerfile = DEVICES / "field/sensors/Dockerfile"
    assert "tls_config.py" in dockerfile.read_text(encoding="utf-8")


# -- the attack toolkit -------------------------------------------------------


def test_the_toolkit_exposes_tls_flags():
    source = (ATTACKS / "_common.py").read_text(encoding="utf-8")
    assert "--tls-ca" in source
    assert "tls_from_args" in source


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


def test_a_plain_broker_connection_is_left_unencrypted(monkeypatch):
    import _mqtt
    import paho.mqtt.client as paho

    touched = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def username_pw_set(self, *args):
            pass

        def tls_set(self, **kwargs):
            touched.append("tls_set")

    monkeypatch.setattr(paho, "Client", FakeClient)
    _mqtt.BrokerConnection("10.20.23.10", 1883, "lab_operator", "secret", 3.0)

    assert touched == [], "TLS must not be configured unless it was asked for"
