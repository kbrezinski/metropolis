"""Hold the latest SCADA status snapshot for an enterprise HTTPS reader."""

from __future__ import annotations

import json
import os
import signal
import ssl
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 65536
QUALITIES = {"good", "degraded", "stale", "unavailable"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def event(source: str, name: str, **fields) -> None:
    print(
        json.dumps({"timestamp": utc_now(), "source": source, "event": name, **fields}),
        flush=True,
    )


def _utc(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0
    except ValueError:
        return False


def validate_snapshot(payload, writer: str) -> dict:
    """Accept only status fields; commands and arbitrary forwarded fields fail."""
    if (
        not isinstance(payload, dict)
        or set(payload) != {"source", "timestamp", "cells"}
        or payload["source"] != writer
        or not _utc(payload["timestamp"])
        or not isinstance(payload["cells"], list)
        or not 1 <= len(payload["cells"]) <= 256
    ):
        raise ValueError("Invalid telemetry snapshot")
    names = set()
    for cell in payload["cells"]:
        if (
            not isinstance(cell, dict)
            or not {"cell", "timestamp", "quality", "alarms"} <= cell.keys()
            or cell.keys() - {"cell", "timestamp", "quality", "alarms", "heartbeat"}
            or not isinstance(cell["cell"], str)
            or not 1 <= len(cell["cell"]) <= 128
            or cell["cell"] in names
            or not _utc(cell["timestamp"])
            or cell["quality"] not in QUALITIES
            or not isinstance(cell["alarms"], list)
            or len(cell["alarms"]) > 64
            or any(
                not isinstance(alarm, str) or not 1 <= len(alarm) <= 128
                for alarm in cell["alarms"]
            )
            or (
                "heartbeat" in cell
                and (
                    type(cell["heartbeat"]) is not int
                    or not 0 <= cell["heartbeat"] <= 65535
                )
            )
        ):
            raise ValueError("Invalid telemetry cell")
        names.add(cell["cell"])
    return payload


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        try:
            return self.context.wrap_socket(connection, server_side=True), address
        except (OSError, ssl.SSLError):
            connection.close()
            event(self.source, "tls_client_rejected")
            raise


class Handler(BaseHTTPRequestHandler):
    server: RelayServer
    server_version = "MetropolisRelay"
    sys_version = ""

    def log_message(self, format, *args):
        # Never include request contents or certificate/key material.
        pass

    def _reply(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True
        event(
            self.server.source, "request_completed", method=self.command, status=status
        )

    def _authorized(self, role: str) -> bool:
        names = [
            value
            for item in self.connection.getpeercert().get("subject", ())
            for key, value in item
            if key == "commonName"
        ]
        if names != [role]:
            self._reply(403, {"error": "role_denied"})
            return False
        return True

    def do_GET(self) -> None:
        if self.path != "/v1/status":
            self._reply(404, {"error": "unknown_path"})
        elif self._authorized(self.server.reader):
            with self.server.lock:
                snapshot = self.server.snapshot
            self._reply(200 if snapshot else 503, snapshot or {"error": "no_snapshot"})

    def do_POST(self) -> None:
        if self.path != "/v1/status":
            self._reply(404, {"error": "unknown_path"})
            return
        if not self._authorized(self.server.writer):
            return
        if (
            self.headers.get("Transfer-Encoding")
            or self.headers.get_content_type() != "application/json"
        ):
            self._reply(400, {"error": "expected_json_with_content_length"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= MAX_BODY:
            self._reply(413 if length > MAX_BODY else 400, {"error": "invalid_size"})
            return
        try:
            payload = validate_snapshot(
                json.loads(self.rfile.read(length)), self.server.writer
            )
        except (ValueError, UnicodeError, TypeError):
            self._reply(400, {"error": "invalid_snapshot"})
            return
        received_at = utc_now()
        with self.server.lock:
            self.server.snapshot = {**payload, "received_at": received_at}
        self._reply(200, {"status": "accepted", "received_at": received_at})


def make_server(host: str, port: int, env: dict[str, str] | None = None) -> RelayServer:
    settings = os.environ if env is None else env
    writer, reader = (
        settings["TELEMETRY_WRITER_NODE"],
        settings["TELEMETRY_READER_NODE"],
    )
    if not writer or not reader or writer == reader:
        raise ValueError("Telemetry writer and reader must be distinct node identities")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(
        settings.get("TELEMETRY_CERT_FILE", "/certs/server.crt"),
        settings.get("TELEMETRY_KEY_FILE", "/certs/server.key"),
    )
    context.load_verify_locations(settings.get("TELEMETRY_CA_FILE", "/certs/ca.crt"))
    context.verify_mode = ssl.CERT_REQUIRED
    server = RelayServer((host, port), Handler)
    server.context = context
    server.writer, server.reader = writer, reader
    server.source = settings.get("NODE_HOSTNAME", "telemetry-relay")
    server.snapshot = None
    server.lock = threading.Lock()
    return server


def main() -> None:
    server = make_server(
        os.getenv("TELEMETRY_BIND", "0.0.0.0"), int(os.getenv("TELEMETRY_PORT", "443"))
    )
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    event(server.source, "relay_started", port=server.server_port)
    try:
        stop.wait()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=4)
        event(server.source, "relay_stopped")


if __name__ == "__main__":
    main()
