"""Send selected SCADA status asynchronously without delaying controller polls."""

from __future__ import annotations

import logging
import os
import queue
import threading
from datetime import datetime, timezone
from urllib.parse import urlsplit

from telemetry_http import client_context, post_snapshot

log = logging.getLogger("metropolis.telemetry_export")


class TelemetryExporter:
    """Keep only the latest pending snapshot when the DMZ relay is unavailable."""

    def __init__(self, source: str, url: str, context) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != "/v1/status"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("TELEMETRY_RELAY_URL must be an HTTPS /v1/status URL")
        self.source = source
        self.url = url
        self.context = context
        self.pending: queue.Queue[dict] = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self._send, daemon=True)

    @classmethod
    def from_environment(cls, source: str, env=None) -> TelemetryExporter | None:
        settings = os.environ if env is None else env
        url = settings.get("TELEMETRY_RELAY_URL")
        if not url:
            return None
        return cls(source, url, client_context(settings))

    def start(self) -> None:
        self.worker.start()

    def submit(self, cells: list[dict]) -> None:
        if self.stop.is_set():
            return
        selected = []
        for cell in cells:
            record = {
                "cell": cell["cell"],
                "timestamp": cell["timestamp"],
                "quality": cell.get("quality", "good"),
                "alarms": list(cell.get("alarms", [])),
            }
            if "heartbeat" in cell:
                record["heartbeat"] = cell["heartbeat"]
            selected.append(record)
        snapshot = {
            "source": self.source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cells": selected,
        }
        try:
            self.pending.get_nowait()
        except queue.Empty:
            pass
        self.pending.put_nowait(snapshot)

    def _send(self) -> None:
        failed = False
        while not self.stop.is_set():
            try:
                snapshot = self.pending.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                post_snapshot(self.url, snapshot, self.context, timeout=2)
            except Exception as exc:
                # Transport exceptions can contain URLs or response text. Log the
                # exception type alone so credentials and payloads stay private.
                log.warning(
                    "Telemetry relay delivery failed source=%s error=%s",
                    self.source,
                    type(exc).__name__,
                )
                failed = True
            else:
                if failed:
                    log.info(
                        "Telemetry relay delivery recovered source=%s", self.source
                    )
                failed = False

    def close(self) -> None:
        self.stop.set()
        if self.worker.ident is not None:
            self.worker.join(timeout=3)
            if self.worker.is_alive():
                log.error("Telemetry relay sender did not stop source=%s", self.source)
