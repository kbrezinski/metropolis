"""Authenticated HTTP interface for the emulated HMI (isolated lab use)."""

from __future__ import annotations

import base64
import copy
import hmac
import json
import math
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_setpoint(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Setpoint must be a number between 0 and 100")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("Setpoint must be a number between 0 and 100")
    return value


class HmiState:
    def __init__(self, name: str, setpoint: float):
        self.lock = threading.Lock()
        self.changed = threading.Event()
        self.data = {
            "device": name,
            "setpoint": validate_setpoint(setpoint),
            "mqtt_connected": False,
            "telemetry": None,
            "telemetry_received_at": None,
            "last_publish_queued_at": None,
        }

    def snapshot(self) -> dict:
        with self.lock:
            return copy.deepcopy(self.data)

    def set_setpoint(self, value: object) -> None:
        value = validate_setpoint(value)
        with self.lock:
            self.data["setpoint"] = value
        self.changed.set()

    def set_connected(self, connected: bool) -> None:
        with self.lock:
            self.data["mqtt_connected"] = connected

    def record_telemetry(self, payload: dict) -> None:
        with self.lock:
            self.data["telemetry"] = copy.deepcopy(payload)
            self.data["telemetry_received_at"] = utc_now()

    def record_publish(self) -> None:
        with self.lock:
            self.data["last_publish_queued_at"] = utc_now()

    def command_payload(self) -> dict:
        snapshot = self.snapshot()
        return {
            "device": snapshot["device"],
            "command": "level_setpoint",
            "value": snapshot["setpoint"],
            "unit": "percent",
            "timestamp": utc_now(),
        }


def create_server(
    state: HmiState, username: str, password: str, host: str, port: int
) -> ThreadingHTTPServer:
    if not username or ":" in username or not password:
        raise ValueError("Set HMI_USERNAME (without ':') and a non-empty HMI_PASSWORD")
    expected = b"Basic " + base64.b64encode(f"{username}:{password}".encode())
    page = Path(__file__).with_name("hmi.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version = "MetropolisHMI/1.0"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(5)

        def reply(self, status: int, body: bytes, content_type="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            if status == 401:
                self.send_header("WWW-Authenticate", 'Basic realm="Metropolis HMI"')
            self.end_headers()
            self.wfile.write(body)

        def authenticated(self) -> bool:
            supplied = self.headers.get("Authorization", "").encode("utf-8")
            if hmac.compare_digest(supplied, expected):
                return True
            self.reply(401, b'{"error":"Authentication required"}')
            return False

        def do_GET(self) -> None:
            if not self.authenticated():
                return
            path = urlsplit(self.path).path
            if path == "/":
                self.reply(200, page, "text/html; charset=utf-8")
            elif path == "/api/status":
                self.reply(200, json.dumps(state.snapshot()).encode())
            elif path == "/healthz":
                self.reply(
                    200,
                    json.dumps(
                        {
                            "status": "ok",
                            "mqtt_connected": state.snapshot()["mqtt_connected"],
                        }
                    ).encode(),
                )
            else:
                self.reply(404, b'{"error":"Not found"}')

        def do_POST(self) -> None:
            if not self.authenticated():
                return
            if urlsplit(self.path).path != "/api/setpoint":
                self.reply(404, b'{"error":"Not found"}')
                return
            # A custom header plus JSON avoids authenticated cross-site form
            # submissions. This server grants no cross-origin CORS access.
            if self.headers.get("X-Metropolis-Request") != "hmi":
                self.reply(403, b'{"error":"X-Metropolis-Request: hmi required"}')
                return
            if self.headers.get_content_type() != "application/json":
                self.reply(415, b'{"error":"application/json required"}')
                return
            if self.headers.get("Transfer-Encoding"):
                self.reply(400, b'{"error":"Chunked requests not supported"}')
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 1 <= size <= 1024:
                    self.reply(413, b'{"error":"Body must be 1 to 1024 bytes"}')
                    return
                body = self.rfile.read(size)
                if len(body) != size:
                    raise ValueError("Incomplete request body")
                payload = json.loads(body)
                if not isinstance(payload, dict) or set(payload) != {"value"}:
                    raise ValueError("Expected a JSON object containing only 'value'")
                state.set_setpoint(payload["value"])
            except (ValueError, OverflowError) as exc:
                self.reply(400, json.dumps({"error": str(exc)}).encode())
                return
            # Accepted locally; MQTT publication and process actuation are
            # separate events. This is not a PLC acknowledgement.
            self.reply(
                202,
                json.dumps(
                    {"status": "accepted", "setpoint": state.snapshot()["setpoint"]}
                ).encode(),
            )

    return ThreadingHTTPServer((host, port), Handler)
