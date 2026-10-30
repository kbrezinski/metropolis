"""Resolve HTTPS telemetry peers from the selected device inventory."""

from __future__ import annotations

from ipaddress import ip_interface


def compile_telemetry(inventory: dict) -> dict[str, dict[str, str]]:
    """Validate relay roles and derive client URLs without fixed lab addresses."""
    devices = {device["name"]: device for device in inventory["devices"]}
    result: dict[str, dict[str, str]] = {}
    for device in devices.values():
        protocols = device.get("protocols", [])
        environment = device.get("environment", {})
        if "telemetry-relay" in protocols:
            if "rdp" in protocols and environment.get(
                "TELEMETRY_ENABLED", ""
            ).strip().lower() not in {"true", "1", "yes", "on"}:
                raise ValueError(
                    f"{device['name']}: cohosted relay requires TELEMETRY_ENABLED=true"
                )
            try:
                port = int(environment.get("TELEMETRY_PORT", "443"))
                if not 1 <= port <= 65535:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError(f"{device['name']}: invalid TELEMETRY_PORT") from None
            for key, role in (
                ("TELEMETRY_WRITER_NODE", "telemetry-publisher"),
                ("TELEMETRY_READER_NODE", "telemetry-consumer"),
            ):
                peer = devices.get(environment.get(key))
                if peer is None or role not in peer.get("protocols", []):
                    raise ValueError(f"{device['name']}: {key} must name a {role}")
                if (
                    peer.get("environment", {}).get("TELEMETRY_RELAY_NODE")
                    != device["name"]
                ):
                    raise ValueError(
                        f"{peer['name']}: TELEMETRY_RELAY_NODE must name {device['name']}"
                    )
        roles = {"telemetry-publisher", "telemetry-consumer"} & set(protocols)
        reference = environment.get("TELEMETRY_RELAY_NODE")
        if not roles and not reference:
            continue
        if len(roles) != 1:
            raise ValueError(f"{device['name']}: select one telemetry client role")
        relay = devices.get(reference)
        if relay is None or "telemetry-relay" not in relay.get("protocols", []):
            raise ValueError(
                f"{device['name']}: TELEMETRY_RELAY_NODE must name a telemetry-relay"
            )
        relay_env = relay.get("environment", {})
        role_key = (
            "TELEMETRY_WRITER_NODE"
            if "telemetry-publisher" in roles
            else "TELEMETRY_READER_NODE"
        )
        if relay_env.get(role_key) != device["name"]:
            raise ValueError(
                f"{device['name']}: relay {role_key} does not authorize this node"
            )
        try:
            port = int(relay_env.get("TELEMETRY_PORT", "443"))
            if not 1 <= port <= 65535:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError(f"{relay['name']}: invalid TELEMETRY_PORT") from None
        address = ip_interface(relay["address"]).ip
        url = f"https://{address}:{port}/v1/status"
        if environment.get("TELEMETRY_RELAY_URL", url) != url:
            raise ValueError(
                f"{device['name']}: TELEMETRY_RELAY_URL disagrees with selected relay"
            )
        if (
            device.get("link_policy") == "intentionally_uncabled"
            or relay.get("link_policy") == "intentionally_uncabled"
        ):
            raise ValueError(
                f"{device['name']}: telemetry peers require physical links"
            )
        result[device["name"]] = {
            "TELEMETRY_RELAY_URL": url,
            "NODE_HOSTNAME": device["name"],
        }
        if "telemetry-publisher" in roles:
            result[device["name"]]["DEVICE_NAME"] = device["name"]
    return result
