"""Read-only SCADA OPC UA data access and a subscribing historian worker."""

from __future__ import annotations

import asyncio
import copy
import ipaddress
import json
import math
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from asyncua import Client, Server, ua
from asyncua.common.utils import ServiceError
from asyncua.crypto.permission_rules import SimpleRoleRuleset, User, UserRole
from asyncua.crypto.uacrypto import CertProperties
from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256
from asyncua.server.user_managers import CertificateUserManager
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import ExtendedKeyUsageOID

NAMESPACE = "urn:metropolis:scada"
METADATA = {"quality", "alarms", "alarm_mask", "timestamp"}
EXCLUDED = {"device", "cell", "controller", "transition"}


def emit(source, event, **fields):
    print(
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": source,
                "component": "opcua",
                "event": event,
                **fields,
            },
            default=lambda item: (
                item.isoformat() if isinstance(item, datetime) else str(item)
            ),
        ),
        flush=True,
    )


def _certificate(path):
    raw = Path(path).read_bytes()
    return (
        x509.load_pem_x509_certificate(raw)
        if raw.startswith(b"-----BEGIN")
        else x509.load_der_x509_certificate(raw)
    )


def _identity(cert, application_uri, role, address=None):
    now = datetime.now(timezone.utc)
    if not cert.not_valid_before_utc <= now <= cert.not_valid_after_utc:
        raise ValueError("OPC UA certificate is outside its validity period")
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        extended = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        if application_uri not in san.get_values_for_type(
            x509.UniformResourceIdentifier
        ):
            raise ValueError("OPC UA certificate ApplicationUri mismatch")
        if not all(
            (
                usage.digital_signature,
                usage.content_commitment,
                usage.key_encipherment,
                usage.data_encipherment,
            )
        ):
            raise ValueError("OPC UA certificate key usage is invalid")
        expected = (
            ExtendedKeyUsageOID.SERVER_AUTH
            if role == "server"
            else ExtendedKeyUsageOID.CLIENT_AUTH
        )
        if expected not in extended:
            raise ValueError("OPC UA certificate role is invalid")
        if address:
            try:
                match = ipaddress.ip_address(address) in san.get_values_for_type(
                    x509.IPAddress
                )
            except ValueError:
                match = address.lower() in [
                    name.lower() for name in san.get_values_for_type(x509.DNSName)
                ]
            if not match:
                raise ValueError("OPC UA server certificate address mismatch")
    except x509.ExtensionNotFound:
        raise ValueError("OPC UA certificate lacks required extensions") from None


class _Settings:
    def __init__(self, env, role):
        self.mode = env.get("OPCUA_SECURITY_MODE", "SignAndEncrypt")
        if self.mode not in {"SignAndEncrypt", "None"}:
            raise ValueError("OPCUA_SECURITY_MODE must be SignAndEncrypt or None")
        self.secure = self.mode == "SignAndEncrypt"
        self.cert = env.get("OPCUA_CERT_FILE", "/certs/opcua/own.crt")
        self.key = env.get("OPCUA_KEY_FILE", "/certs/opcua/own.key")
        self.peer_file = env.get("OPCUA_PEER_CERT_FILE", "/certs/opcua/peer.crt")
        self.uri = env.get("OPCUA_APPLICATION_URI", "")
        self.peer_uri = env.get("OPCUA_PEER_APPLICATION_URI", "")
        self.peer_address = env.get("OPCUA_PEER_ADDRESS", "")
        self.role = role
        if self.secure:
            if not self.uri or not self.peer_uri or not self.peer_address:
                raise ValueError(
                    "OPC UA application identities and peer address are required"
                )
            own = _certificate(self.cert)
            self.peer = _certificate(self.peer_file)
            _identity(own, self.uri, role)
            _identity(
                self.peer,
                self.peer_uri,
                "client" if role == "server" else "server",
                self.peer_address if role == "client" else None,
            )
            key = serialization.load_pem_private_key(
                Path(self.key).read_bytes(), password=None
            )
            if key.public_key().public_numbers() != own.public_key().public_numbers():
                raise ValueError("OPC UA certificate and private key do not match")

    async def validate_peer(self, cert, description):
        try:
            if cert.fingerprint(hashes.SHA256()) != self.peer.fingerprint(
                hashes.SHA256()
            ):
                raise ValueError("Untrusted OPC UA peer")
            if description.ApplicationUri != self.peer_uri:
                raise ValueError("Unexpected OPC UA peer ApplicationUri")
            _identity(
                cert,
                self.peer_uri,
                "client" if self.role == "server" else "server",
                self.peer_address if self.role == "client" else None,
            )
        except ValueError:
            raise ServiceError(ua.StatusCodes.BadCertificateInvalid) from None


def _enabled(env):
    value = env.get("OPCUA_ENABLED", "false").strip().lower()
    if value not in {"true", "1", "yes", "on", "false", "0", "no", "off", ""}:
        raise ValueError("OPCUA_ENABLED must be a boolean")
    return value in {"true", "1", "yes", "on"}


class _Worker:
    def __init__(self, source):
        self.source = source
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = None
        self._loop = None
        self._task = None
        self._error = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError("OPC UA worker has already started")

        def runner():
            async def run():
                self._loop = asyncio.get_running_loop()
                self._task = asyncio.current_task()
                try:
                    await self._run()
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    self._error = exc
                    emit(self.source, "worker_failed", error=type(exc).__name__)
                finally:
                    self._ready.set()

            asyncio.run(run())

        self._thread = threading.Thread(
            target=runner, name=f"opcua-{self.source}", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(30):
            self.close()
            raise RuntimeError("OPC UA worker startup timed out")
        if self._error is not None:
            raise RuntimeError(
                f"OPC UA worker startup failed ({type(self._error).__name__})"
            ) from None

    def close(self):
        self._stop.set()
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._task.cancel)
        if self._thread is not None:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                raise RuntimeError("OPC UA worker did not stop")


class _ReadOnlyRules(SimpleRoleRuleset):
    def check_validity(self, user, action_type_id, body):
        if action_type_id in {
            ua.NodeId(ua.ObjectIds.WriteRequest_Encoding_DefaultBinary),
            ua.NodeId(ua.ObjectIds.CallRequest_Encoding_DefaultBinary),
        }:
            return False
        return super().check_validity(user, action_type_id, body)


class _AnonymousReader:
    def get_user(self, iserver, username=None, password=None, certificate=None):
        return (
            User(role=UserRole.User, name="plaintext-lab-reader")
            if username is None
            else None
        )


def _node_id(cell, field=None):
    base = "cell/" + quote(cell, safe="")
    return base if field is None else base + "/" + quote(field, safe="")


def _value(value, field):
    if field == "timestamp":
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("OPC UA source timestamp must include a timezone")
        return stamp.astimezone(timezone.utc), ua.VariantType.DateTime
    if isinstance(value, bool):
        return value, ua.VariantType.Boolean
    if isinstance(value, int):
        return value, ua.VariantType.Int64
    if isinstance(value, float) and math.isfinite(value):
        return value, ua.VariantType.Double
    if isinstance(value, str):
        return value, ua.VariantType.String
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value.copy(), ua.VariantType.String
    raise ValueError("Unsupported OPC UA field value")


class OpcUaServerWorker(_Worker):
    @classmethod
    def from_environment(cls, source, initial_cells, env=None):
        settings = dict(os.environ if env is None else env)
        return cls(source, initial_cells, settings) if _enabled(settings) else None

    def __init__(self, source, initial_cells, env):
        super().__init__(source)
        self.settings = _Settings(env, "server")
        port = int(env.get("OPCUA_PORT", "4840"))
        if not 1 <= port <= 65535:
            raise ValueError("OPCUA_PORT must be between 1 and 65535")
        self.endpoint = (
            f"opc.tcp://{env.get('OPCUA_BIND', '0.0.0.0')}:{port}/metropolis/"
        )
        self.templates = {}
        for cell in initial_cells:
            name = cell["cell"]
            if not isinstance(name, str) or not name or name in self.templates:
                raise ValueError("OPC UA cell IDs must be nonempty and unique")
            fields = {
                key: _value(value, key)
                for key, value in cell.items()
                if key not in EXCLUDED
            }
            fields.setdefault("quality", ("unavailable", ua.VariantType.String))
            fields.setdefault("alarms", ([], ua.VariantType.String))
            if "timestamp" not in fields:
                raise ValueError("OPC UA cell timestamp is required")
            self.templates[name] = fields
        if not self.templates:
            raise ValueError("At least one OPC UA cell is required")
        self._lock = threading.Lock()
        self._pending = {}

    def submit(self, payload):
        if self._error is not None:
            raise RuntimeError("OPC UA server worker has failed")
        cell = payload.get("cell")
        if cell not in self.templates:
            raise ValueError("Unknown OPC UA cell")
        selected = {
            key: copy.deepcopy(value)
            for key, value in payload.items()
            if key in self.templates[cell]
        }
        with self._lock:
            self._pending[cell] = selected

    async def _run(self):
        settings = self.settings
        manager = CertificateUserManager() if settings.secure else _AnonymousReader()
        if settings.secure:
            await manager.add_user(
                Path(settings.peer_file), name="historian", format="pem"
            )
        server = Server(user_manager=manager)
        await server.init()
        server.allow_remote_admin(False)
        server.set_endpoint(self.endpoint)
        await server.set_application_uri(
            settings.uri or f"urn:metropolis:opcua:{self.source}"
        )
        if settings.secure:
            await server.load_certificate(settings.cert, format="pem")
            await server.load_private_key(settings.key, format="pem")
            server.set_certificate_validator(settings.validate_peer)
            server.set_identity_tokens([ua.X509IdentityToken])
            policy = ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt
        else:
            server.set_identity_tokens([ua.AnonymousIdentityToken])
            policy = ua.SecurityPolicyType.NoSecurity
        server.set_security_policy([policy], permission_ruleset=_ReadOnlyRules())
        index = await server.register_namespace(NAMESPACE)
        root = await server.nodes.objects.add_folder(ua.NodeId("cells", index), "Cells")
        nodes, values, observed = {}, {}, set()
        for cell, fields in self.templates.items():
            folder = await root.add_folder(ua.NodeId(_node_id(cell), index), cell)
            for field, (initial, kind) in fields.items():
                node = await folder.add_variable(
                    ua.NodeId(_node_id(cell, field), index), field, initial, kind
                )
                await node.set_read_only()
                nodes[cell, field] = node
                values[cell, field] = initial
                await node.write_value(
                    ua.DataValue(
                        ua.Variant(initial, kind),
                        StatusCode=ua.StatusCode(
                            ua.StatusCodes.BadWaitingForInitialData
                        ),
                    )
                )
        async with server:
            emit(self.source, "server_started", security_mode=settings.mode)
            self._ready.set()
            while not self._stop.is_set():
                with self._lock:
                    pending, self._pending = self._pending, {}
                for cell, payload in pending.items():
                    quality = payload.get("quality", "good")
                    stamp, _ = _value(payload["timestamp"], "timestamp")
                    for field, (_initial, kind) in self.templates[cell].items():
                        if field in payload:
                            value, new_kind = _value(payload[field], field)
                            if new_kind != kind:
                                raise ValueError("OPC UA field type changed")
                            values[cell, field] = value
                            observed.add((cell, field))
                        status = (
                            ua.StatusCodes.Good
                            if (cell, field) in observed
                            else ua.StatusCodes.BadWaitingForInitialData
                        )
                        if field not in METADATA:
                            if quality == "unavailable":
                                status = ua.StatusCodes.BadCommunicationError
                            elif quality == "stale" and (cell, field) in observed:
                                status = ua.StatusCodes.UncertainLastUsableValue
                        await nodes[cell, field].write_value(
                            ua.DataValue(
                                ua.Variant(values[cell, field], kind),
                                StatusCode=ua.StatusCode(status),
                                SourceTimestamp=stamp,
                            )
                        )
                await asyncio.sleep(0.05)
        emit(self.source, "server_stopped")


class OpcUaHistorianWorker(_Worker):
    @classmethod
    def from_environment(cls, source, env=None):
        settings = dict(os.environ if env is None else env)
        return cls(source, settings) if _enabled(settings) else None

    def __init__(self, source, env):
        super().__init__(source)
        self.settings = _Settings(env, "client")
        self.endpoint = env.get("OPCUA_ENDPOINT_URL", "")
        address = urlsplit(self.endpoint)
        if (
            address.scheme != "opc.tcp"
            or not address.hostname
            or address.username
            or address.password
            or address.query
            or address.fragment
        ):
            raise ValueError("OPCUA_ENDPOINT_URL must identify an OPC TCP service")
        if self.settings.secure and address.hostname != self.settings.peer_address:
            raise ValueError("OPC UA endpoint and expected peer address disagree")
        self.interval = float(env.get("OPCUA_PUBLISH_INTERVAL_MS", "1000"))
        if not math.isfinite(self.interval) or self.interval <= 0:
            raise ValueError("OPCUA_PUBLISH_INTERVAL_MS must be positive and finite")
        self._lost = False

    async def _run(self):
        self._ready.set()
        while not self._stop.is_set():
            client = Client(
                self.endpoint, timeout=3, auto_reconnect=True, reconnect_max_delay=3
            )
            settings = self.settings
            client.application_uri = (
                settings.uri or f"urn:metropolis:opcua:{self.source}"
            )
            if settings.secure:
                await client.set_security(
                    SecurityPolicyBasic256Sha256,
                    CertProperties(settings.cert, extension="pem"),
                    CertProperties(settings.key, extension="pem"),
                    server_certificate=CertProperties(
                        settings.peer_file, extension="pem"
                    ),
                )
                client.certificate_validator = settings.validate_peer
                await client.load_client_certificate(settings.cert, extension="pem")
                await client.load_private_key(Path(settings.key), extension="pem")
            worker = self

            async def connection_lost(error):
                if not worker._lost:
                    emit(
                        worker.source,
                        "historian_disconnected",
                        error=type(error).__name__,
                    )
                worker._lost = True

            client.connection_lost_callback = connection_lost

            class Changes:
                def datachange_notification(self, node, value, data):
                    cell, field = mapping[node.nodeid.to_string()]
                    data_value = data.monitored_item.Value
                    if worker._lost and data_value.StatusCode.is_good():
                        worker._lost = False
                        emit(
                            worker.source,
                            "historian_recovered",
                            evidence="data_notification",
                        )
                    emit(
                        worker.source,
                        "data_received",
                        cell=cell,
                        field=field,
                        value=value,
                        status=data_value.StatusCode.name,
                        source_timestamp=data_value.SourceTimestamp,
                        peer_application_uri=settings.peer_uri or None,
                    )

            try:
                async with client:
                    index = await client.get_namespace_index(NAMESPACE)
                    root = client.get_node(ua.NodeId("cells", index))
                    mapping, monitored = {}, []
                    for cell in await root.get_children():
                        name = (await cell.read_browse_name()).Name
                        for node in await cell.get_children():
                            field = (await node.read_browse_name()).Name
                            mapping[node.nodeid.to_string()] = name, field
                            monitored.append(node)
                    if not monitored:
                        raise ValueError("OPC UA server has no data fields")
                    subscription = await client.create_subscription(
                        ua.CreateSubscriptionParameters(
                            RequestedPublishingInterval=self.interval,
                            RequestedMaxKeepAliveCount=10,
                            RequestedLifetimeCount=30,
                            MaxNotificationsPerPublish=10000,
                            PublishingEnabled=True,
                            Priority=0,
                        ),
                        Changes(),
                    )
                    handles = await subscription.subscribe_data_change(monitored)
                    for handle in handles:
                        if isinstance(handle, ua.StatusCode):
                            handle.check()
                    emit(
                        self.source,
                        "historian_connected",
                        security_mode=settings.mode,
                        field_count=len(monitored),
                    )
                    while not self._stop.is_set():
                        await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await connection_lost(exc)
                await asyncio.sleep(0.5)
