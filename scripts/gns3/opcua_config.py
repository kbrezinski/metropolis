"""Resolve OPC UA application identities and endpoints from a selected inventory."""

from __future__ import annotations

from ipaddress import ip_interface
import math


def compile_opcua(inventory: dict) -> dict[str, dict[str, str]]:
    """Validate paired OPC UA roles and derive their runtime configuration."""
    devices = {device["name"]: device for device in inventory["devices"]}
    result: dict[str, dict[str, str]] = {}
    for device in devices.values():
        environment = device.get("environment", {})
        roles = {"opcua-server", "opcua-client"} & set(device.get("protocols", []))
        if not roles and not any(
            key in environment for key in ("OPCUA_SERVER_NODE", "OPCUA_CLIENT_NODE")
        ):
            continue
        if len(roles) != 1:
            raise ValueError(f"{device['name']}: select one OPC UA role")
        if environment.get("OPCUA_ENABLED", "").strip().lower() not in {
            "true",
            "1",
            "yes",
            "on",
        }:
            raise ValueError(
                f"{device['name']}: declared OPC UA role requires OPCUA_ENABLED=true"
            )
        server_role = "opcua-server" in roles
        key, peer_key, peer_role = (
            ("OPCUA_CLIENT_NODE", "OPCUA_SERVER_NODE", "opcua-client")
            if server_role
            else ("OPCUA_SERVER_NODE", "OPCUA_CLIENT_NODE", "opcua-server")
        )
        peer = devices.get(environment.get(key))
        if peer is None or peer_role not in peer.get("protocols", []):
            raise ValueError(f"{device['name']}: {key} must name an {peer_role}")
        peer_environment = peer.get("environment", {})
        if peer_environment.get(peer_key) != device["name"]:
            raise ValueError(
                f"{device['name']}: peer {peer_key} does not authorize this node"
            )
        security = environment.get("OPCUA_SECURITY_MODE", "SignAndEncrypt")
        if security not in {"SignAndEncrypt", "None"}:
            raise ValueError(
                f"{device['name']}: OPCUA_SECURITY_MODE must be SignAndEncrypt or None"
            )
        if peer_environment.get("OPCUA_SECURITY_MODE", "SignAndEncrypt") != security:
            raise ValueError(f"{device['name']}: OPCUA_SECURITY_MODE differs from peer")
        if (
            device.get("link_policy") == "intentionally_uncabled"
            or peer.get("link_policy") == "intentionally_uncabled"
        ):
            raise ValueError(f"{device['name']}: OPC UA peers require physical links")
        server = device if server_role else peer
        try:
            port = int(server.get("environment", {}).get("OPCUA_PORT", "4840"))
            if not 1 <= port <= 65535:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError(f"{server['name']}: invalid OPCUA_PORT") from None
        derived = {
            "NODE_HOSTNAME": device["name"],
            "DEVICE_NAME": device["name"],
            "OPCUA_APPLICATION_URI": f"urn:metropolis:opcua:{device['name']}",
            "OPCUA_PEER_APPLICATION_URI": f"urn:metropolis:opcua:{peer['name']}",
            "OPCUA_PEER_ADDRESS": str(ip_interface(peer["address"]).ip),
            "OPCUA_SECURITY_MODE": security,
            "OPCUA_PORT": str(port),
            "OPCUA_CERT_FILE": environment.get(
                "OPCUA_CERT_FILE", "/certs/opcua/own.crt"
            ),
            "OPCUA_KEY_FILE": environment.get("OPCUA_KEY_FILE", "/certs/opcua/own.key"),
            "OPCUA_PEER_CERT_FILE": environment.get(
                "OPCUA_PEER_CERT_FILE", "/certs/opcua/peer.crt"
            ),
        }
        for setting in (
            "OPCUA_APPLICATION_URI",
            "OPCUA_PEER_APPLICATION_URI",
            "OPCUA_PEER_ADDRESS",
        ):
            if environment.get(setting, derived[setting]) != derived[setting]:
                raise ValueError(
                    f"{device['name']}: {setting} disagrees with selected inventory"
                )
        if not server_role:
            endpoint = (
                f"opc.tcp://{ip_interface(server['address']).ip}:{port}/metropolis/"
            )
            if environment.get("OPCUA_ENDPOINT_URL", endpoint) != endpoint:
                raise ValueError(
                    f"{device['name']}: OPCUA_ENDPOINT_URL disagrees with selected server"
                )
            try:
                interval = float(environment.get("OPCUA_PUBLISH_INTERVAL_MS", "1000"))
                if not math.isfinite(interval) or interval <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError(
                    f"{device['name']}: OPCUA_PUBLISH_INTERVAL_MS must be finite and positive"
                ) from None
            derived["OPCUA_ENDPOINT_URL"] = endpoint
            derived["OPCUA_PUBLISH_INTERVAL_MS"] = f"{interval:g}"
        result[device["name"]] = derived
    return result
