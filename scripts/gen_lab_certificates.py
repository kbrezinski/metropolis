#!/usr/bin/env python3
"""Generate the lab's own certificate authority and server certificate.

The Metropolis lab needs a CA and a server certificate so MQTT can run over TLS.
Generating them here means the lab does not depend on a public certificate for a
hostname that only exists inside GNS3.

Certificates are lab material, not source: they are written to a directory that
is ignored by Git. Regenerate them whenever you like; every device trusts the CA
that is present, so rotating it means updating both ends.

The functions are importable, because the broker image generates its own
certificates at startup if none were supplied. That keeps a TLS lab working
without anyone having to mount files.

Usage::

    uv run python scripts/gen_lab_certificates.py --out-dir certs
    uv run python scripts/gen_lab_certificates.py --help
"""

from __future__ import annotations

import argparse
import datetime
import ipaddress
from pathlib import Path

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


def write_pem(path: Path, key, certificate) -> None:
    """Write a certificate and its key as PEM, the form TLS expects."""
    from cryptography.hazmat.primitives import serialization

    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        + certificate.public_bytes(serialization.Encoding.PEM)
    )


def generate(out_dir: Path, names: list[str], addresses: list[str]) -> dict[str, Path]:
    """Write the CA, the server certificate and key, and a CoAP PSK file."""
    from cryptography.hazmat.primitives import serialization

    out_dir.mkdir(parents=True, exist_ok=True)
    ca_key, ca_certificate = build_ca("Metropolis Lab CA")
    server_key, server_certificate = build_server_certificate(
        ca_key, ca_certificate, names, addresses
    )

    ca_path = out_dir / "ca.crt"
    ca_path.write_bytes(ca_certificate.public_bytes(serialization.Encoding.PEM))
    write_pem(out_dir / "server.crt", server_key, server_certificate)
    (out_dir / "server.key").write_bytes(
        server_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return {
        "ca": ca_path,
        "server": out_dir / "server.crt",
        "key": out_dir / "server.key",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("certs"),
        help="Where to write the certificates (Git-ignored)",
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

    names = DEFAULT_NAMES + (args.names or [])
    addresses = DEFAULT_ADDRESSES + (args.addresses or [])
    written = generate(args.out_dir, names, addresses)

    for label, path in written.items():
        print(f"  {label}: {path}")
    print(f"valid for: {', '.join(names)} and {', '.join(addresses)}")
    print(
        "Load ca.crt into the broker and set TLS=true on the clients that should "
        "use it."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
