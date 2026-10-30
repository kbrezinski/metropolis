"""Exercise the status relay over real loopback HTTPS with distinct roles."""

from __future__ import annotations

import importlib.util
import json
import ssl
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from http.client import HTTPSConnection
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[1]
DEVICES = ROOT / "testbeds/metropolis/devices"
sys.path.insert(0, str(DEVICES / "runtime"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


relay = load("telemetry_relay_test", DEVICES / "services/telemetry_relay/relay.py")
consumer = load(
    "telemetry_consumer_test", DEVICES / "operations/enterprise_client/consumer.py"
)
http = load("telemetry_http_test", DEVICES / "runtime/telemetry_http.py")
certificates = load(
    "telemetry_certificates_test", ROOT / "scripts/gen_lab_certificates.py"
)


@pytest.fixture(scope="module")
def tls(tmp_path_factory):
    directory = tmp_path_factory.mktemp("relay-certs")
    ca_key, ca_cert = certificates.build_ca("Wire test CA")
    certificates.write_pem(directory / "ca.crt", ca_cert)

    def save(name, key, cert):
        certificates.write_pem(directory / f"{name}.crt", cert)
        (directory / f"{name}.key").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )

    save(
        "server",
        *certificates.build_server_certificate(
            ca_key, ca_cert, ["relay.example.test"], ["127.0.0.1"]
        ),
    )
    for name in ("writer", "reader", "outsider"):
        save(name, *certificates.build_client_certificate(ca_key, ca_cert, name))
    wrong_key, wrong_ca = certificates.build_ca("Unrelated CA")
    certificates.write_pem(directory / "wrong-ca.crt", wrong_ca)
    save(
        "untrusted-client",
        *certificates.build_client_certificate(wrong_key, wrong_ca, "writer"),
    )

    def env(name):
        return {
            "TELEMETRY_CA_FILE": str(directory / "ca.crt"),
            "TELEMETRY_CERT_FILE": str(directory / f"{name}.crt"),
            "TELEMETRY_KEY_FILE": str(directory / f"{name}.key"),
        }

    return directory, env


@contextmanager
def serving(tls):
    _, env = tls
    server = relay.make_server(
        "127.0.0.1",
        0,
        {
            **env("server"),
            "TELEMETRY_WRITER_NODE": "writer",
            "TELEMETRY_READER_NODE": "reader",
            "NODE_HOSTNAME": "relay",
        },
    )
    worker = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    worker.start()
    try:
        yield server, f"https://127.0.0.1:{server.server_port}/v1/status"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=4)
        assert not worker.is_alive()


def snapshot(heartbeat=9):
    return {
        "source": "writer",
        "timestamp": "2026-01-02T03:04:05Z",
        "cells": [
            {
                "cell": "custom-cell",
                "timestamp": "2026-01-02T03:04:05+00:00",
                "quality": "degraded",
                "alarms": ["drive_fault"],
                "heartbeat": heartbeat,
            }
        ],
    }


def test_https_roles_and_latest_snapshot_are_enforced(tls, monkeypatch):
    _, env = tls
    writer = http.client_context(env("writer"))
    reader = http.client_context(env("reader"))
    outsider = http.client_context(env("outsider"))
    # Host proxy settings must not divert this lab exchange.
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    with serving(tls) as (_, url):
        with pytest.raises(HTTPError) as caught:
            http.read_snapshot(url, reader)
        assert caught.value.code == 503
        for context, operation in (
            (reader, lambda c: http.post_snapshot(url, snapshot(), c)),
            (writer, lambda c: http.read_snapshot(url, c)),
            (outsider, lambda c: http.read_snapshot(url, c)),
        ):
            with pytest.raises(HTTPError) as denied:
                operation(context)
            assert denied.value.code == 403
        accepted = http.post_snapshot(url, snapshot(), writer)
        assert accepted["status"] == "accepted"
        first = http.read_snapshot(url, reader)
        assert first == {**snapshot(), "received_at": accepted["received_at"]}
        assert (
            datetime.fromisoformat(first["received_at"].replace("Z", "+00:00"))
            .utcoffset()
            .total_seconds()
            == 0
        )
        http.post_snapshot(url, snapshot(42), writer)
        assert http.read_snapshot(url, reader)["cells"][0]["heartbeat"] == 42


@pytest.mark.parametrize(
    "alter",
    [
        lambda p: p.update(source="spoofed-writer"),
        lambda p: p["cells"][0].update(password="must-not-be-retained"),
        lambda p: p["cells"][0].update(timestamp="2026-01-01T00:00:00"),
        lambda p: p["cells"].append(p["cells"][0].copy()),
        lambda p: p["cells"][0].update(quality=[]),
    ],
)
def test_malformed_updates_cannot_replace_accepted_status(tls, alter):
    _, env = tls
    writer, reader = (
        http.client_context(env("writer")),
        http.client_context(env("reader")),
    )
    with serving(tls) as (_, url):
        http.post_snapshot(url, snapshot(), writer)
        bad = snapshot(99)
        alter(bad)
        with pytest.raises(HTTPError) as invalid:
            http.post_snapshot(url, bad, writer)
        assert invalid.value.code == 400
        assert http.read_snapshot(url, reader)["cells"][0]["heartbeat"] == 9


def test_raw_json_size_and_endpoint_are_checked(tls):
    _, env = tls
    with serving(tls) as (server, url):
        connection = HTTPSConnection(
            "127.0.0.1",
            server.server_port,
            context=http.client_context(env("writer")),
            timeout=2,
        )
        try:
            for path, data, expected in (
                ("/v1/status", b"not-json", 400),
                ("/v1/status", b" " * 65537, 413),
                ("/controller/command", b"{}", 404),
            ):
                connection.request(
                    "POST",
                    path,
                    body=data,
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                assert response.status == expected
                response.read()
        finally:
            connection.close()


def test_tls_rejects_untrusted_peers_wrong_hostname_and_missing_client(tls):
    directory, env = tls
    bad_ca = http.client_context(
        {**env("reader"), "TELEMETRY_CA_FILE": str(directory / "wrong-ca.crt")}
    )
    untrusted_client = http.client_context(env("untrusted-client"))
    missing_client = ssl.create_default_context(cafile=str(directory / "ca.crt"))
    with serving(tls) as (server, url):
        for context in (bad_ca, missing_client, untrusted_client):
            # TLS 1.3 may report a peer rejection while sending the request or
            # as a connection close before the first HTTP response (Windows).
            with pytest.raises((URLError, OSError)):
                http.read_snapshot(url, context)
        with pytest.raises(URLError) as wrong_hostname:
            http.read_snapshot(
                f"https://localhost:{server.server_port}/v1/status",
                http.client_context(env("reader")),
            )
        assert isinstance(wrong_hostname.value.reason, ssl.SSLCertVerificationError)


def test_consumer_reports_empty_relay_then_recovers_and_stops(tls, monkeypatch, capsys):
    _, env = tls
    empty, received, stop = threading.Event(), threading.Event(), threading.Event()
    real_read = consumer.read_snapshot

    def observe(*args, **kwargs):
        try:
            result = real_read(*args, **kwargs)
        except HTTPError:
            empty.set()
            raise
        received.set()
        return result

    monkeypatch.setattr(consumer, "read_snapshot", observe)
    with serving(tls) as (_, url):
        settings = {
            **env("reader"),
            "TELEMETRY_RELAY_URL": url,
            "TELEMETRY_INTERVAL": "0.02",
            "NODE_HOSTNAME": "enterprise-consumer",
        }
        worker = threading.Thread(
            target=consumer.run, args=(stop, settings), daemon=True
        )
        worker.start()
        try:
            assert empty.wait(3)
            http.post_snapshot(url, snapshot(), http.client_context(env("writer")))
            assert received.wait(3)
        finally:
            stop.set()
            worker.join(timeout=4)
        assert not worker.is_alive()
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    own = [record for record in records if record["source"] == "enterprise-consumer"]
    assert {record["event"] for record in own} >= {
        "consumer_started",
        "snapshot_failed",
        "snapshot_received",
        "consumer_stopped",
    }
    success = next(record for record in own if record["event"] == "snapshot_received")
    assert (
        success["writer"],
        success["cell_count"],
        success["snapshot_timestamp"],
    ) == ("writer", 1, "2026-01-02T03:04:05Z")
    assert all(
        datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")).tzinfo
        == timezone.utc
        for record in own
    )


def test_client_refuses_plaintext_or_credential_urls_before_network(tls):
    _, env = tls
    context = http.client_context(env("reader"))
    for url in (
        "http://127.0.0.1/v1/status",
        "https://user:secret@example.test/v1/status",
        "https://example.test/v1/status?token=secret",
    ):
        with pytest.raises(ValueError, match="HTTPS with path"):
            http.read_snapshot(url, context)
