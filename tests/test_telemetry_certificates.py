"""Selected-inventory identities and separate TLS credentials for the data path."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml
from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gen_lab_certificates as certificates


def inventory(tmp_path):
    devices = [
        {
            "name": "relay-custom",
            "address": "192.0.2.33/24",
            "protocols": ["telemetry-relay"],
            "environment": {
                "TELEMETRY_WRITER_NODE": "publisher-custom",
                "TELEMETRY_READER_NODE": "consumer-custom",
            },
        },
    ]
    devices.extend(
        {
            "name": name,
            "environment": {"TELEMETRY_RELAY_NODE": "relay-custom"},
        }
        for name in ("publisher-custom", "consumer-custom")
    )
    directory = tmp_path / "renamed-v9" / "device_instances"
    directory.mkdir(parents=True)
    path = directory / "initial_devices.yaml"
    path.write_text(yaml.safe_dump({"devices": devices}), encoding="utf-8")
    return directory.parent, path, devices


def test_certificate_roles_and_server_san_follow_selected_inventory(tmp_path):
    dataset, _, _ = inventory(tmp_path)
    identities = certificates.telemetry_identities(dataset)
    written = certificates.generate_telemetry(tmp_path / "bundle", identities)
    server = x509.load_pem_x509_certificate(written["server"].read_bytes())
    san = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == ["relay-custom"]
    assert [str(address) for address in san.get_values_for_type(x509.IPAddress)] == [
        "192.0.2.33"
    ]
    for role, subject in (
        ("writer", "publisher-custom"),
        ("reader", "consumer-custom"),
    ):
        certificate = x509.load_pem_x509_certificate(written[role].read_bytes())
        assert (
            certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
            == subject
        )
        usage = certificate.extensions.get_extension_for_class(
            x509.ExtendedKeyUsage
        ).value
        assert list(usage) == [ExtendedKeyUsageOID.CLIENT_AUTH]
    assert (
        len(
            {
                written[f"{role}_key"].read_bytes()
                for role in ("server", "writer", "reader")
            }
        )
        == 3
    )
    assert not (tmp_path / "bundle" / "ca.key").exists()


@pytest.mark.parametrize("defect", ["missing", "same-role", "wrong-relay"])
def test_bad_role_references_cannot_produce_a_misleading_bundle(tmp_path, defect):
    dataset, path, devices = inventory(tmp_path)
    if defect == "missing":
        devices[0]["environment"]["TELEMETRY_WRITER_NODE"] = "absent"
    elif defect == "same-role":
        devices[0]["environment"]["TELEMETRY_READER_NODE"] = "publisher-custom"
    else:
        devices[1]["environment"]["TELEMETRY_RELAY_NODE"] = "another-relay"
    path.write_text(yaml.safe_dump({"devices": devices}), encoding="utf-8")
    with pytest.raises(ValueError):
        certificates.telemetry_identities(dataset)
