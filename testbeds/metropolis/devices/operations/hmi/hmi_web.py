"""Authenticated HTTP interface for the emulated HMI (isolated lab use)."""

from __future__ import annotations

import base64
import copy
import hmac
import json
import math
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_setpoint(
    value: object, *, minimum: float = 0.0, maximum: float = 100.0
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"Setpoint must be a number between {minimum:g} and {maximum:g}"
        )
    value = float(value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(
            f"Setpoint must be a number between {minimum:g} and {maximum:g}"
        )
    tenths = round(value * 10)
    if not math.isclose(value * 10, tenths, rel_tol=0, abs_tol=1e-9):
        raise ValueError("Setpoint must have at most one decimal place")
    return tenths / 10


class PendingCommandError(ValueError):
    """The panel accepts one outstanding controller command at a time."""


def _utc_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    offset = parsed.utcoffset()
    return offset is not None and offset.total_seconds() == 0


class HmiState:
    def __init__(
        self,
        name: str,
        setpoint: float,
        *,
        panel_title: str = "Intake operator panel",
        setpoint_label: str = "Requested level setpoint",
        setpoint_command: str = "level_setpoint",
        cell: str = "intake",
        setpoint_unit: str = "percent",
        setpoint_min: float = 0.0,
        setpoint_max: float = 100.0,
        ack_timeout: float = 15.0,
        telemetry_stale_seconds: float = 30.0,
    ):
        if not cell or not setpoint_unit or not setpoint_command:
            raise ValueError("HMI cell, unit, and command must be nonempty")
        if ack_timeout <= 0 or telemetry_stale_seconds <= 0:
            raise ValueError("HMI timeouts must be positive")
        if (
            not math.isfinite(setpoint_min)
            or not math.isfinite(setpoint_max)
            or setpoint_min >= setpoint_max
        ):
            raise ValueError("HMI setpoint bounds must be finite and ordered")
        self.lock = threading.Lock()
        self.changed = threading.Event()
        self.ack_timeout = ack_timeout
        self.telemetry_stale_seconds = telemetry_stale_seconds
        self.setpoint_min = setpoint_min
        self.setpoint_max = setpoint_max
        self._outgoing: deque[tuple[dict, float]] = deque()
        self._latest_command: dict | None = None
        self._command_deadline: float | None = None
        self._telemetry_received_monotonic: float | None = None
        self.data = {
            "device": name,
            "cell": cell,
            "panel_title": panel_title,
            "setpoint_label": setpoint_label,
            "setpoint_command": setpoint_command,
            "setpoint_unit": setpoint_unit,
            "setpoint_min": setpoint_min,
            "setpoint_max": setpoint_max,
            "setpoint": validate_setpoint(
                setpoint, minimum=setpoint_min, maximum=setpoint_max
            ),
            "mqtt_connected": False,
            "telemetry": None,
            "telemetry_received_at": None,
            "last_publish_queued_at": None,
            "request_id": None,
            "command_requested_at": None,
            "command_status": "idle",
            "command_ack": None,
            "command_reason": None,
        }

    def snapshot(self) -> dict:
        with self.lock:
            now = time.monotonic()
            self._expire_pending_locked(now)
            result = copy.deepcopy(self.data)
            if self._telemetry_received_monotonic is None:
                result["telemetry_status"] = "unavailable"
                result["telemetry_age_seconds"] = None
            else:
                age = max(0.0, now - self._telemetry_received_monotonic)
                quality = result["telemetry"].get("quality")
                if quality in ("unavailable", "stale"):
                    result["telemetry_status"] = quality
                else:
                    result["telemetry_status"] = (
                        "stale" if age > self.telemetry_stale_seconds else "fresh"
                    )
                result["telemetry_age_seconds"] = round(age, 1)
            return result

    def set_setpoint(self, value: object) -> str:
        value = validate_setpoint(
            value, minimum=self.setpoint_min, maximum=self.setpoint_max
        )
        with self.lock:
            self._expire_pending_locked(time.monotonic())
            if self.data["command_status"] == "pending":
                raise PendingCommandError(
                    "Wait for the previous controller command to finish"
                )
            if len(self._outgoing) >= 64:
                raise OverflowError("Too many queued HMI requests")
            request_id = str(uuid4())
            requested_at = utc_now()
            self.data["setpoint"] = value
            self.data["request_id"] = request_id
            self.data["command_requested_at"] = requested_at
            self.data["command_status"] = "pending"
            self.data["command_ack"] = None
            self.data["command_reason"] = None
            self.data["last_publish_queued_at"] = None
            self._command_deadline = time.monotonic() + self.ack_timeout
            self._latest_command = {
                "request_id": request_id,
                "cell": self.data["cell"],
                "device": self.data["device"],
                "command": self.data["setpoint_command"],
                "value": value,
                "unit": self.data["setpoint_unit"],
                "timestamp": requested_at,
            }
            self._outgoing.append((self._latest_command, self._command_deadline))
            self.changed.set()
            return request_id

    def take_pending_command(self) -> dict | None:
        """Take each explicit operator request once, without periodic replay."""
        with self.lock:
            now = time.monotonic()
            self._expire_pending_locked(now)
            while self._outgoing:
                payload, deadline = self._outgoing.popleft()
                if now < deadline:
                    return copy.deepcopy(payload)
            self.changed.clear()
            return None

    def _expire_pending_locked(self, now: float) -> bool:
        if (
            self.data["command_status"] == "pending"
            and self._command_deadline is not None
            and now >= self._command_deadline
        ):
            self.data["command_status"] = "timeout"
            self.data["command_reason"] = "No controller acknowledgement received"
            return True
        return False

    def expire_pending(self) -> bool:
        with self.lock:
            return self._expire_pending_locked(time.monotonic())

    def set_connected(self, connected: bool) -> None:
        with self.lock:
            self.data["mqtt_connected"] = connected

    def record_telemetry(self, payload: dict) -> None:
        with self.lock:
            self.data["telemetry"] = copy.deepcopy(payload)
            self.data["telemetry_received_at"] = utc_now()
            self._telemetry_received_monotonic = time.monotonic()

    def record_publish(self, request_id: str) -> None:
        with self.lock:
            if self.data["request_id"] == request_id:
                self.data["last_publish_queued_at"] = utc_now()

    def record_publish_failure(self, request_id: str, reason: str) -> None:
        with self.lock:
            if (
                self.data["request_id"] == request_id
                and self.data["command_status"] == "pending"
            ):
                self.data["command_status"] = "failed"
                self.data["command_reason"] = reason

    def record_ack(self, payload: dict) -> bool:
        """Accept only a well-formed acknowledgement for the current request."""
        with self.lock:
            self._expire_pending_locked(time.monotonic())
            if (
                not isinstance(payload, dict)
                or self.data["command_status"] != "pending"
                or payload.get("request_id") != self.data["request_id"]
                or payload.get("cell") != self.data["cell"]
                or payload.get("status") not in {"stored", "rejected", "failed"}
                or not isinstance(payload.get("controller"), str)
                or not payload["controller"]
                or not _utc_timestamp(payload.get("timestamp"))
            ):
                return False
            value = payload.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            try:
                if not math.isfinite(value) or not math.isclose(
                    value, self.data["setpoint"], rel_tol=0, abs_tol=1e-9
                ):
                    return False
            except OverflowError:
                return False
            self.data["command_status"] = payload["status"]
            self.data["command_ack"] = copy.deepcopy(payload)
            self.data["command_reason"] = payload.get("reason")
            return True

    def command_payload(self) -> dict | None:
        with self.lock:
            return copy.deepcopy(self._latest_command)


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
                request_id = state.set_setpoint(payload["value"])
            except PendingCommandError as exc:
                self.reply(409, json.dumps({"error": str(exc)}).encode())
                return
            except (ValueError, OverflowError) as exc:
                self.reply(400, json.dumps({"error": str(exc)}).encode())
                return
            # Accepted locally; MQTT publication and process actuation are
            # separate events. This is not a PLC acknowledgement.
            self.reply(
                202,
                json.dumps(
                    {
                        "status": "accepted",
                        "request_id": request_id,
                        "setpoint": state.snapshot()["setpoint"],
                    }
                ).encode(),
            )

    return ThreadingHTTPServer((host, port), Handler)
