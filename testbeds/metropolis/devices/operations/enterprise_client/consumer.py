"""Read DMZ status snapshots without accessing OT controllers."""

from __future__ import annotations

import json
import math
import os
import signal
import threading
from datetime import datetime, timezone

from telemetry_http import client_context, read_snapshot


def run(stop: threading.Event, env: dict[str, str] | None = None) -> None:
    settings = os.environ if env is None else env
    context = client_context(settings)
    url = settings["TELEMETRY_RELAY_URL"]
    interval = float(settings.get("TELEMETRY_INTERVAL", "10"))
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("TELEMETRY_INTERVAL must be positive and finite")
    source = settings.get("NODE_HOSTNAME", "enterprise-client")

    def report(name, **fields):
        print(
            json.dumps(
                {
                    "timestamp": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "source": source,
                    "event": name,
                    **fields,
                }
            ),
            flush=True,
        )

    report("consumer_started", interval_seconds=interval)
    try:
        while not stop.is_set():
            try:
                snapshot = read_snapshot(url, context)
                if not isinstance(snapshot.get("cells"), list):
                    raise ValueError("Relay response has no cell list")
                report(
                    "snapshot_received",
                    writer=snapshot["source"],
                    cell_count=len(snapshot["cells"]),
                    snapshot_timestamp=snapshot["timestamp"],
                    received_at=snapshot["received_at"],
                )
            except Exception as exc:
                # Exception classes identify failure without URLs, keys or payloads.
                report("snapshot_failed", error=type(exc).__name__)
            stop.wait(interval)
    finally:
        report("consumer_stopped")


def main() -> None:
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    run(stop)


if __name__ == "__main__":
    main()
