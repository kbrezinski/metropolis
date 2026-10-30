"""Verified mutual-TLS requests for the DMZ telemetry path."""

from __future__ import annotations

import json
import os
import ssl
import urllib.request
from urllib.parse import urlsplit


def client_context(env: dict[str, str] | None = None) -> ssl.SSLContext:
    settings = os.environ if env is None else env
    context = ssl.create_default_context(
        cafile=settings.get("TELEMETRY_CA_FILE", "/certs/ca.crt")
    )
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(
        settings.get("TELEMETRY_CERT_FILE", "/certs/client.crt"),
        settings.get("TELEMETRY_KEY_FILE", "/certs/client.key"),
    )
    return context


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(url: str, payload: dict | None, context, timeout: float) -> dict:
    address = urlsplit(url)
    if (
        address.scheme != "https"
        or not address.hostname
        or address.username is not None
        or address.password is not None
        or address.query
        or address.fragment
        or address.path != "/v1/status"
    ):
        raise ValueError("Telemetry URL must be HTTPS with path /v1/status")
    data = (
        json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        if payload is not None
        else None
    )
    if data is not None and len(data) > 65536:
        raise ValueError("Telemetry snapshot exceeds 64 KiB")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    # Lab traffic must use its declared route, regardless of controller proxies.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
        _NoRedirect(),
    )
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(131073)
    if len(raw) > 131072:
        raise ValueError("Telemetry response exceeds 128 KiB")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Telemetry response must be an object")
    return result


def post_snapshot(url: str, payload: dict, context, timeout: float = 2) -> dict:
    return _request(url, payload, context, timeout)


def read_snapshot(url: str, context, timeout: float = 2) -> dict:
    return _request(url, None, context, timeout)
