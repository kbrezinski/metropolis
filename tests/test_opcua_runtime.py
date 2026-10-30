"""Real OPC TCP data access, certificate rejection, and historian recovery."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from asyncua import Client, ua
from asyncua.common.subscription import Subscription
from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256
from asyncua.crypto.uacrypto import CertProperties
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runtime = load(
    "opcua_runtime_test", ROOT / "testbeds/metropolis/devices/runtime/opcua_runtime.py"
)
certs = load("opcua_certificates_test", ROOT / "scripts/gen_lab_certificates.py")


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    directory = tmp_path_factory.mktemp("opcua-wire")
    identities = {
        "server": {
            "name": "custom-scada",
            "address": "127.0.0.1",
            "application_uri": "urn:test:scada",
        },
        "client": {
            "name": "custom-historian",
            "address": "127.0.0.1",
            "application_uri": "urn:test:historian",
        },
    }
    certs.generate_opcua(directory, identities)
    ca_key, ca_cert = certs.build_ca("Other lab")
    rogue_key, rogue_cert = certs.build_opcua_certificate(
        ca_key, ca_cert, "other-client", "127.0.0.1", "urn:test:historian", "client"
    )
    certs.write_pem(directory / "rogue.crt", rogue_cert)
    (directory / "rogue.key").write_bytes(
        rogue_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return directory


def environments(bundle, mode="SignAndEncrypt"):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    shared = {
        "OPCUA_ENABLED": "true",
        "OPCUA_SECURITY_MODE": mode,
        "OPCUA_PORT": str(port),
        "OPCUA_PEER_ADDRESS": "127.0.0.1",
        "OPCUA_PUBLISH_INTERVAL_MS": "100",
    }
    server = {
        **shared,
        "OPCUA_BIND": "127.0.0.1",
        "OPCUA_APPLICATION_URI": "urn:test:scada",
        "OPCUA_PEER_APPLICATION_URI": "urn:test:historian",
        "OPCUA_CERT_FILE": str(bundle / "server.crt"),
        "OPCUA_KEY_FILE": str(bundle / "server.key"),
        "OPCUA_PEER_CERT_FILE": str(bundle / "client.crt"),
    }
    client = {
        **shared,
        "OPCUA_ENDPOINT_URL": f"opc.tcp://127.0.0.1:{port}/metropolis/",
        "OPCUA_APPLICATION_URI": "urn:test:historian",
        "OPCUA_PEER_APPLICATION_URI": "urn:test:scada",
        "OPCUA_CERT_FILE": str(bundle / "client.crt"),
        "OPCUA_KEY_FILE": str(bundle / "client.key"),
        "OPCUA_PEER_CERT_FILE": str(bundle / "server.crt"),
    }
    return server, client


def payload(value=21.5, quality="good"):
    return {
        "cell": "north basin",
        "controller": "192.0.2.20",
        "device": "custom-scada",
        "heartbeat": 4,
        "level_percent": value,
        "setpoint": 50.0,
        "quality": quality,
        "alarm_mask": 0,
        "alarms": [],
        "timestamp": "2026-10-30T02:03:04.123456Z",
    }


async def client_for(env, **overrides):
    settings = {**env, **overrides}
    client = Client(settings["OPCUA_ENDPOINT_URL"], timeout=2)
    client.application_uri = settings["OPCUA_APPLICATION_URI"]
    if settings["OPCUA_SECURITY_MODE"] == "SignAndEncrypt":
        await client.set_security(
            SecurityPolicyBasic256Sha256,
            CertProperties(settings["OPCUA_CERT_FILE"], extension="pem"),
            CertProperties(settings["OPCUA_KEY_FILE"], extension="pem"),
            server_certificate=CertProperties(
                settings["OPCUA_PEER_CERT_FILE"], extension="pem"
            ),
        )
        await client.load_client_certificate(
            settings["OPCUA_CERT_FILE"], extension="pem"
        )
        await client.load_private_key(Path(settings["OPCUA_KEY_FILE"]), extension="pem")
    return client


@pytest.mark.parametrize("mode", ["SignAndEncrypt", "None"])
def test_subscriptions_read_only_data_source_time_and_outage_quality(bundle, mode):
    server_env, client_env = environments(bundle, mode)
    server = runtime.OpcUaServerWorker.from_environment(
        "custom-scada", [payload()], server_env
    )
    server.start()

    async def exercise():
        client = await client_for(client_env)
        async with client:
            ns = await client.get_namespace_index("urn:metropolis:scada")
            level = client.get_node(ua.NodeId("cell/north%20basin/level_percent", ns))
            quality = client.get_node(ua.NodeId("cell/north%20basin/quality", ns))
            stamp = client.get_node(ua.NodeId("cell/north%20basin/timestamp", ns))
            initial = await level.read_data_value(raise_on_bad_status=False)
            assert initial.StatusCode.value == ua.StatusCodes.BadWaitingForInitialData
            assert initial.Value.Value is None
            server.submit(
                {
                    "cell": "north basin",
                    "quality": "good",
                    "timestamp": payload()["timestamp"],
                    "alarms": [],
                }
            )
            for _ in range(60):
                quality_value = await quality.read_data_value(raise_on_bad_status=False)
                if quality_value.StatusCode.is_good():
                    break
                await asyncio.sleep(0.03)
            assert quality_value.Value.Value == "good"
            unobserved = await level.read_data_value(raise_on_bad_status=False)
            assert (
                unobserved.StatusCode.value == ua.StatusCodes.BadWaitingForInitialData
            )
            assert unobserved.Value.Value is None
            changes = asyncio.Queue()

            class Handler:
                def datachange_notification(self, node, value, data):
                    changes.put_nowait(
                        (value, data.monitored_item.Value.StatusCode.value)
                    )

            subscription = await client.create_subscription(100, Handler())
            await subscription.subscribe_data_change(level)
            server.submit(payload(43.5))

            async def wait_change(expected):
                while True:
                    value, status = await changes.get()
                    if value == expected:
                        return status

            assert await asyncio.wait_for(wait_change(43.5), 5) == ua.StatusCodes.Good
            value = await level.read_data_value()
            expected_time = datetime(2026, 10, 30, 2, 3, 4, 123456, tzinfo=timezone.utc)
            assert value.SourceTimestamp == expected_time
            assert await stamp.read_value() == expected_time
            with pytest.raises(ua.UaStatusCodeError):
                await level.write_value(99.0)
            assert await level.read_value() == 43.5
            server.submit(
                {
                    "cell": "north basin",
                    "timestamp": payload()["timestamp"],
                    "quality": "unavailable",
                    "alarms": ["controller_unreachable"],
                }
            )

            async def wait_quality(expected):
                for _ in range(60):
                    if await quality.read_value() == expected:
                        return
                    await asyncio.sleep(0.03)
                pytest.fail("quality update did not arrive")

            await wait_quality("unavailable")
            missing = await level.read_data_value(raise_on_bad_status=False)
            assert missing.StatusCode.value == ua.StatusCodes.BadCommunicationError
            assert missing.Value.Value is None
            server.submit(
                {
                    "cell": "north basin",
                    "timestamp": payload()["timestamp"],
                    "quality": "stale",
                    "alarms": [],
                }
            )
            await wait_quality("stale")
            stale = await level.read_data_value(raise_on_bad_status=False)
            assert stale.StatusCode.value == ua.StatusCodes.UncertainLastUsableValue
            assert stale.Value.Value == 43.5
            await subscription.delete()

    try:
        asyncio.run(exercise())
    finally:
        server.close()
    # A clean close releases the listening port, not merely the thread flag.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", int(server_env["OPCUA_PORT"])))


def test_secure_server_rejects_unknown_identity_uri_and_plaintext(bundle):
    server_env, client_env = environments(bundle)
    server = runtime.OpcUaServerWorker.from_environment(
        "custom-scada", [payload()], server_env
    )
    server.start()

    async def rejected():
        for changes in (
            {
                "OPCUA_CERT_FILE": str(bundle / "rogue.crt"),
                "OPCUA_KEY_FILE": str(bundle / "rogue.key"),
            },
            {"OPCUA_APPLICATION_URI": "urn:test:spoofed"},
            {"OPCUA_SECURITY_MODE": "None"},
        ):
            client = await client_for(client_env, **changes)
            with pytest.raises((ua.UaError, ConnectionError, asyncio.TimeoutError)):
                await client.connect()
            await client.disconnect()

    try:
        asyncio.run(rejected())
    finally:
        server.close()


def test_certificate_preflight_refuses_expiry_uri_address_and_bad_modes(
    bundle, tmp_path
):
    server_env, client_env = environments(bundle)
    for change in (
        {"OPCUA_PEER_APPLICATION_URI": "urn:wrong"},
        {"OPCUA_PEER_ADDRESS": "192.0.2.99"},
        {"OPCUA_SECURITY_MODE": "Sign"},
    ):
        with pytest.raises(ValueError):
            runtime.OpcUaHistorianWorker.from_environment(
                "reader", {**client_env, **change}
            )
    original = x509.load_pem_x509_certificate((bundle / "client.crt").read_bytes())
    ca_key, ca = certs.build_ca("Expired fixture issuer")
    expired = (
        x509.CertificateBuilder()
        .subject_name(original.subject)
        .issuer_name(ca.subject)
        .public_key(original.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=3))
        .not_valid_after(datetime.now(timezone.utc) - timedelta(days=1))
        .sign(ca_key, hashes.SHA256())
    )
    certs.write_pem(tmp_path / "expired.crt", expired)
    with pytest.raises(ValueError, match="validity period"):
        runtime.OpcUaHistorianWorker.from_environment(
            "reader", {**client_env, "OPCUA_CERT_FILE": str(tmp_path / "expired.crt")}
        )
    assert runtime.OpcUaHistorianWorker.from_environment("reader", {}) is None
    assert runtime.OpcUaServerWorker.from_environment("scada", [], {}) is None


def test_historian_resumes_real_notifications_after_server_restart(
    bundle, monkeypatch, capsys
):
    server_env, client_env = environments(bundle)
    observations = []
    condition = threading.Condition()
    real_emit = runtime.emit

    def observe(source, event, **fields):
        real_emit(source, event, **fields)
        with condition:
            observations.append({"source": source, "event": event, **fields})
            condition.notify_all()

    monkeypatch.setattr(runtime, "emit", observe)

    def received(value):
        with condition:
            return condition.wait_for(
                lambda: any(
                    item.get("source") == "custom-historian"
                    and item.get("event") == "data_received"
                    and item.get("field") == "level_percent"
                    and item.get("value") == value
                    for item in observations
                ),
                timeout=12,
            )

    first = runtime.OpcUaServerWorker.from_environment(
        "custom-scada", [payload()], server_env
    )
    historian = runtime.OpcUaHistorianWorker.from_environment(
        "custom-historian", client_env
    )
    second = None
    try:
        first.start()
        historian.start()
        first.submit(payload(12.5))
        assert received(12.5)
        first.close()
        second = runtime.OpcUaServerWorker.from_environment(
            "custom-scada", [payload()], server_env
        )
        second.start()
        second.submit(payload(87.5))
        assert received(87.5)
    finally:
        historian.close()
        first.close()
        if second is not None:
            second.close()
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    values = [
        record
        for record in records
        if record["event"] == "data_received" and record["field"] == "level_percent"
    ]
    assert any(
        record["value"] == 87.5
        and record["status"] == "Good"
        and record["peer_application_uri"] == "urn:test:scada"
        for record in values
    )
    assert all(
        datetime.fromisoformat(record["timestamp"]).utcoffset().total_seconds() == 0
        for record in values
    )
    assert any(record["event"] == "historian_disconnected" for record in records)
    assert any(
        record["event"] == "historian_recovered"
        and record["evidence"] == "data_notification"
        for record in records
    )


def test_idle_historian_receives_periodic_wire_keepalives(bundle, monkeypatch):
    server_env, client_env = environments(bundle)
    keepalives = []
    condition = threading.Condition()
    original_callback = Subscription.publish_callback

    async def observe(subscription, result):
        # Observe decoded responses from the actual client socket; retain the
        # library handler that updates subscription health and notifications.
        await original_callback(subscription, result)
        if not result.NotificationMessage.NotificationData:
            with condition:
                keepalives.append(time.monotonic())
                condition.notify_all()

    monkeypatch.setattr(Subscription, "publish_callback", observe)
    server = runtime.OpcUaServerWorker.from_environment(
        "custom-scada", [payload()], server_env
    )
    historian = runtime.OpcUaHistorianWorker.from_environment(
        "custom-historian", client_env
    )
    try:
        server.start()
        historian.start()
        with condition:
            assert condition.wait_for(lambda: len(keepalives) >= 2, timeout=8)
        # At a 100ms publishing interval, ten quiet cycles should produce a
        # keepalive about each second, independent of source value changes.
        assert 0.3 <= keepalives[1] - keepalives[0] < 3
    finally:
        historian.close()
        server.close()
