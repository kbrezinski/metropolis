"""Certificate identity follows selected OPC UA peers, independently of lab names."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gen_lab_certificates as certificates


def test_opcua_bundle_uses_inventory_uris_addresses_and_distinct_role_keys(tmp_path):
    dataset = tmp_path / "custom-v8"
    inventory = dataset / "device_instances" / "initial_devices.yaml"
    inventory.parent.mkdir(parents=True)
    devices = [
        {
            "name": "control-service",
            "address": "192.0.2.99/24",
            "protocols": ["opcua-server"],
            "environment": {
                "OPCUA_ENABLED": "true",
                "OPCUA_CLIENT_NODE": "archive-service",
            },
        },
        {
            "name": "archive-service",
            "address": "198.51.100.24/24",
            "protocols": ["opcua-client"],
            "environment": {
                "OPCUA_ENABLED": "true",
                "OPCUA_SERVER_NODE": "control-service",
            },
        },
    ]
    inventory.write_text(yaml.safe_dump({"devices": devices}), encoding="utf-8")
    identities = certificates.opcua_identities(dataset)
    bundle = certificates.generate_opcua(tmp_path / "bundle", identities)
    ca = x509.load_pem_x509_certificate(bundle["ca"].read_bytes())
    expected = (
        ("server", "control-service", "192.0.2.99", ExtendedKeyUsageOID.SERVER_AUTH),
        ("client", "archive-service", "198.51.100.24", ExtendedKeyUsageOID.CLIENT_AUTH),
    )
    for role, name, address, purpose in expected:
        cert = x509.load_pem_x509_certificate(bundle[role].read_bytes())
        cert.verify_directly_issued_by(ca)
        assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == name
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        assert san.get_values_for_type(x509.UniformResourceIdentifier) == [
            f"urn:metropolis:opcua:{name}"
        ]
        assert [str(value) for value in san.get_values_for_type(x509.IPAddress)] == [
            address
        ]
        assert list(
            cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        ) == [purpose]
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        assert usage.digital_signature and usage.content_commitment
        assert usage.key_encipherment and usage.data_encipherment
        key = serialization.load_pem_private_key(
            bundle[f"{role}_key"].read_bytes(), password=None
        )
        assert key.public_key().public_numbers() == cert.public_key().public_numbers()
    assert bundle["server_key"].read_bytes() != bundle["client_key"].read_bytes()
    assert not (tmp_path / "bundle" / "ca.key").exists()
