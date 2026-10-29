"""Compile the selected inventory's MQTT clients into one broker ACL payload."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from ipaddress import ip_interface


_USERNAME = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
_TOPIC = re.compile(r"[A-Za-z0-9_./+#-]{1,256}\Z")
_ROLES = {"publisher", "subscriber", "hmi", "scada"}


def _required(environment: dict, key: str, source: str) -> str:
    value = environment.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{source}: missing {key}")
    return value


def _topic(value: str, source: str, field: str, *, allow_filter: bool = False) -> str:
    if not isinstance(value, str) or not _TOPIC.fullmatch(value):
        raise ValueError(f"{source}: invalid {field}")
    if "+" in value or (
        "#" in value
        and (not allow_filter or not value.endswith("/#") or value.count("#") != 1)
    ):
        raise ValueError(f"{source}: invalid {field}")
    return value


def _credentials(environment: dict, source: str, prefix: str) -> tuple[str, str]:
    username = _required(environment, f"{prefix}USERNAME", source)
    password = _required(environment, f"{prefix}PASSWORD", source)
    if not _USERNAME.fullmatch(username) or any(ch in password for ch in ":\r\n\x00"):
        raise ValueError(f"{source}: invalid MQTT credentials")
    return username, password


def _scada_targets(environment: dict, source: str) -> list[dict]:
    try:
        targets = json.loads(_required(environment, "SCADA_TARGETS", source))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source}: invalid SCADA_TARGETS") from exc
    if not isinstance(targets, list) or not targets:
        raise ValueError(f"{source}: invalid SCADA_TARGETS")
    seen: set[str] = set()
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError(f"{source}: invalid SCADA_TARGETS")
        cell = target.get("id")
        if not isinstance(cell, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", cell):
            raise ValueError(f"{source}: invalid SCADA_TARGETS")
        if cell in seen:
            raise ValueError(f"{source}: duplicate SCADA target")
        seen.add(cell)
        _topic(target.get("topic"), source, "SCADA_TARGETS topic")
    return targets


def _broker(inventory: dict) -> tuple[dict | None, list[dict]]:
    devices = inventory.get("devices", [])
    if not isinstance(devices, list):
        raise ValueError("inventory devices must be a list")
    brokers = [
        item
        for item in devices
        if any(
            key in item.get("environment", {})
            for key in ("MQTT_OPERATOR_USERNAME", "MQTT_OPERATOR_PASSWORD")
        )
    ]
    clients = [
        item
        for item in devices
        if "mqtt" in item.get("protocols", []) and item not in brokers
    ]
    if len(brokers) > 1:
        raise ValueError("inventory declares more than one MQTT broker")
    if clients and not brokers:
        raise ValueError("MQTT clients have no declared broker")
    return (brokers[0] if brokers else None), clients


def compile_mqtt_access(inventory: dict) -> dict[str, str]:
    """Return broker environment entries; never include credentials in diagnostics.

    The base64 value is transport for GNS3's line-oriented environment format,
    not encryption. Client credentials remain synthetic lab data in inventory.
    """
    broker, clients = _broker(inventory)
    if broker is None:
        return {}
    broker_name = broker["name"]
    broker_env = broker.get("environment", {})
    if "MQTT_DEVICE_USERNAME" in broker_env or "MQTT_DEVICE_PASSWORD" in broker_env:
        raise ValueError(f"{broker_name}: remove the legacy shared MQTT device account")
    operator_user, operator_password = _credentials(
        broker_env, broker_name, "MQTT_OPERATOR_"
    )
    try:
        broker_host = str(ip_interface(broker["address"]).ip)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"{broker_name}: invalid broker address") from exc
    hosts = {broker_host, broker_name, broker_env.get("NODE_HOSTNAME")}
    accounts = [
        {
            "username": operator_user,
            "password": operator_password,
            "read": [],
            "write": ["metropolis/experiment/#"],
        }
    ]
    identities = {operator_user}
    records: list[tuple[dict, dict, dict]] = []
    for device in clients:
        source = device["name"]
        env = device.get("environment", {})
        role = env.get("MQTT_ACCESS_ROLE")
        if role not in _ROLES:
            raise ValueError(f"{source}: missing or unsupported MQTT_ACCESS_ROLE")
        if _required(env, "MQTT_HOST", source) not in hosts:
            raise ValueError(f"{source}: MQTT_HOST does not resolve to declared broker")
        username, password = _credentials(env, source, "MQTT_")
        if username in identities:
            raise ValueError(f"{source}: duplicate MQTT username")
        identities.add(username)
        account = {"username": username, "password": password, "read": [], "write": []}
        records.append((device, env, account))
        accounts.append(account)

    scada_cells: dict[str, tuple[str, dict]] = {}
    for device, env, account in records:
        source = device["name"]
        role = env["MQTT_ACCESS_ROLE"]
        if role == "scada":
            for target in _scada_targets(env, source):
                cell = target["id"]
                if cell in scada_cells:
                    raise ValueError(f"{source}: duplicate SCADA cell across clients")
                scada_cells[cell] = (target["topic"], account)
                account["write"].append(target["topic"])
        elif role == "publisher":
            account["write"].append(
                _topic(_required(env, "MQTT_TOPIC", source), source, "MQTT_TOPIC")
            )
        elif role == "subscriber":
            account["read"].append(
                _topic(
                    _required(env, "MQTT_TOPIC", source),
                    source,
                    "MQTT_TOPIC",
                    allow_filter=True,
                )
            )

    for device, env, account in records:
        if env["MQTT_ACCESS_ROLE"] != "hmi":
            continue
        source = device["name"]
        cell = _required(env, "HMI_CELL_ID", source)
        target = scada_cells.get(cell)
        if target is None:
            raise ValueError(f"{source}: HMI cell has no declared SCADA target")
        status_topic, scada_account = target
        command = _topic(_required(env, "MQTT_TOPIC", source), source, "MQTT_TOPIC")
        ack = _topic(_required(env, "MQTT_ACK_TOPIC", source), source, "MQTT_ACK_TOPIC")
        status = _topic(
            _required(env, "MQTT_TELEMETRY_TOPIC", source),
            source,
            "MQTT_TELEMETRY_TOPIC",
        )
        if (
            command != f"metropolis/plant/{cell}/command"
            or ack != f"metropolis/scada/{cell}/ack"
            or status != status_topic
        ):
            raise ValueError(f"{source}: HMI topics do not match SCADA behavior")
        account["write"].append(command)
        account["read"].extend((status, ack))
        scada_account["read"].append(command)
        scada_account["write"].append(ack)

    for account in accounts:
        account["read"] = sorted(set(account["read"]))
        account["write"] = sorted(set(account["write"]))
    accounts.sort(key=lambda item: item["username"])
    payload = json.dumps(
        {"version": 1, "accounts": accounts}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "MQTT_ACCESS_B64": base64.b64encode(payload).decode("ascii"),
        "MQTT_ACCESS_SHA256": hashlib.sha256(payload).hexdigest(),
    }
