#!/usr/bin/env python3
"""Generate lab TLS certificates for MQTT, synthetic Merlin, or telemetry.

The Metropolis lab needs a CA and a server certificate for TLS services.
Generating them here means the lab does not depend on a public certificate for a
hostname that only exists inside GNS3.

Certificates are lab material, not source: they are written to a directory that
is ignored by Git. Regenerate them whenever you like; every device trusts the CA
that is present, so rotating it means updating both ends.

The functions are importable, because the broker image generates its own
certificates at startup if none were supplied. For Merlin, provide a selected
dataset so the certificate covers that dataset's C&C address. Generate a
separate bundle before building the Merlin images.
Telemetry also uses the selected dataset, with distinct writer and reader
client certificates in one shared bundle for its three images.

Usage::

    uv run python scripts/gen_lab_certificates.py --out-dir certs
    uv run python scripts/gen_lab_certificates.py --service merlin --dataset DATASET
    uv run python scripts/gen_lab_certificates.py --service telemetry --dataset DATASET
    uv run python scripts/gen_lab_certificates.py --help
"""

from __future__ import annotations

import argparse
import datetime
import ipaddress
from pathlib import Path

import yaml

# The names and addresses the certificate must be valid for. A broker is reached
# by address inside the lab and by name from a host, so both are covered. The
# GNS3 node name is what the clients in this lab actually connect to.
DEFAULT_NAMES = [
    "localhost",
    "broker.lab.metropolis.test",
    "metropolis-mqtt-broker-01",
    "MET-MQTT-BROKER-01",
    "mqtt-broker",
]
DEFAULT_ADDRESSES = [
    "127.0.0.1",
    "10.20.23.10",
]

CA_VALID_DAYS = 3650
CERT_VALID_DAYS = 825


def build_ca(common_name: str):
    """A self-signed CA certificate and its key."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=CA_VALID_DAYS))
        # A CA needs these two, or nothing will accept a certificate it signs.
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return key, certificate


def build_server_certificate(ca_key, ca_certificate, names, addresses):
    """A server certificate for the given names and addresses, signed by the CA."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])])
    san = x509.SubjectAlternativeName(
        [x509.DNSName(name) for name in names]
        + [x509.IPAddress(ipaddress.ip_address(item)) for item in addresses]
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=CERT_VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(san, critical=False)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return key, certificate


def write_pem(path: Path, certificate) -> None:
    """Write only the public certificate; the private key has its own file."""
    from cryptography.hazmat.primitives import serialization

    path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


def build_client_certificate(ca_key, ca_certificate, common_name: str):
    """Issue a distinct, client-only identity for a telemetry role."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(ca_certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=CERT_VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return key, certificate


def telemetry_identities(dataset: Path) -> dict[str, str]:
    """Resolve relay and client certificate identities from selected inventory."""
    inventory_path = dataset / "device_instances" / "initial_devices.yaml"
    inventory = yaml.safe_load(inventory_path.read_text(encoding="utf-8"))
    devices = {device["name"]: device for device in inventory.get("devices", [])}
    relays = [
        device
        for device in devices.values()
        if "telemetry-relay" in device.get("protocols", [])
    ]
    if len(relays) != 1:
        raise ValueError(
            f"Expected one telemetry relay in {inventory_path}; found {len(relays)}"
        )
    relay = relays[0]
    environment = relay.get("environment", {})
    names = {"relay": relay["name"]}
    for role, setting in (
        ("writer", "TELEMETRY_WRITER_NODE"),
        ("reader", "TELEMETRY_READER_NODE"),
    ):
        name = environment.get(setting)
        if not name or name not in devices:
            raise ValueError(
                f"{relay['name']}: {setting} must name an inventoried node"
            )
        if (
            devices[name].get("environment", {}).get("TELEMETRY_RELAY_NODE")
            != relay["name"]
        ):
            raise ValueError(
                f"{name}: TELEMETRY_RELAY_NODE must select {relay['name']}"
            )
        names[role] = name
    if len(set(names.values())) != 3:
        raise ValueError("Telemetry relay, writer, and reader must be distinct nodes")
    names["address"] = str(ipaddress.ip_interface(relay["address"]).ip)
    return names


def generate_telemetry(out_dir: Path, identities: dict[str, str]) -> dict[str, Path]:
    """Generate a shared CA and three role-specific keys; never write the CA key."""
    from cryptography.hazmat.primitives import serialization

    out_dir.mkdir(parents=True, exist_ok=True)
    ca_key, ca_certificate = build_ca("Metropolis Telemetry CA")
    ca_path = out_dir / "ca.crt"
    write_pem(ca_path, ca_certificate)
    keys = {
        "server": build_server_certificate(
            ca_key, ca_certificate, [identities["relay"]], [identities["address"]]
        ),
        "writer": build_client_certificate(
            ca_key, ca_certificate, identities["writer"]
        ),
        "reader": build_client_certificate(
            ca_key, ca_certificate, identities["reader"]
        ),
    }
    written = {"ca": ca_path}
    for role, (key, certificate) in keys.items():
        cert_path = out_dir / f"{role}.crt"
        key_path = out_dir / f"{role}.key"
        write_pem(cert_path, certificate)
        key_path.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        key_path.chmod(0o600)
        written[role] = cert_path
        written[f"{role}_key"] = key_path
    return written


def generate(out_dir: Path, names: list[str], addresses: list[str]) -> dict[str, Path]:
    """Write the CA, server certificate, and server private key."""
    from cryptography.hazmat.primitives import serialization

    out_dir.mkdir(parents=True, exist_ok=True)
    ca_key, ca_certificate = build_ca("Metropolis Lab CA")
    server_key, server_certificate = build_server_certificate(
        ca_key, ca_certificate, names, addresses
    )

    ca_path = out_dir / "ca.crt"
    ca_path.write_bytes(ca_certificate.public_bytes(serialization.Encoding.PEM))
    write_pem(out_dir / "server.crt", server_certificate)
    key_path = out_dir / "server.key"
    key_path.write_bytes(
        server_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    return {
        "ca": ca_path,
        "server": out_dir / "server.crt",
        "key": out_dir / "server.key",
    }


def merlin_identity(dataset: Path) -> tuple[list[str], list[str]]:
    """Find the one Merlin control endpoint in a selected dataset."""
    inventory_path = dataset / "device_instances" / "initial_devices.yaml"
    inventory = yaml.safe_load(inventory_path.read_text(encoding="utf-8"))
    matches = [
        device
        for device in inventory.get("devices", [])
        if "merlin-control" in device.get("protocols", [])
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one Merlin control node in {inventory_path}; found {len(matches)}"
        )
    device = matches[0]
    address = str(ipaddress.ip_interface(device["address"]).ip)
    return [device["name"]], [address]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--service",
        choices=("mqtt", "merlin", "telemetry"),
        default="mqtt",
        help="Certificate identity to generate (default: mqtt)",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        help="Selected dataset directory (required for merlin/telemetry)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="Output directory (default: certs or certs/<service>)",
    )
    parser.add_argument(
        "--name",
        action="append",
        dest="names",
        help="Extra DNS name the certificate must be valid for",
    )
    parser.add_argument(
        "--address",
        action="append",
        dest="addresses",
        help="Extra IP address the certificate must be valid for",
    )
    args = parser.parse_args()

    if args.service == "telemetry":
        if args.dataset is None:
            parser.error("--dataset is required for --service telemetry")
        if args.names or args.addresses:
            parser.error(
                "Telemetry identities come from --dataset; --name/--address are unsupported"
            )
        written = generate_telemetry(
            args.out_dir or Path("certs/telemetry"), telemetry_identities(args.dataset)
        )
        for label, path in written.items():
            print(f"  {label}: {path}")
        print(
            "Rebuild DMZ RDP/relay, SCADA, and enterprise client with this same bundle."
        )
        return 0

    if args.service == "merlin":
        if args.dataset is None:
            parser.error("--dataset is required for --service merlin")
        names, addresses = merlin_identity(args.dataset)
        out_dir = args.out_dir or Path("certs/merlin")
    else:
        names, addresses = DEFAULT_NAMES.copy(), DEFAULT_ADDRESSES.copy()
        out_dir = args.out_dir or Path("certs")
    names += args.names or []
    addresses += args.addresses or []
    written = generate(out_dir, names, addresses)

    for label, path in written.items():
        print(f"  {label}: {path}")
    print(f"valid for: {', '.join(names)} and {', '.join(addresses)}")
    if args.service == "mqtt":
        print("Load ca.crt into broker clients that should use MQTT TLS.")
    else:
        print("Rebuild both Merlin images with this bundle on their hosting compute.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
