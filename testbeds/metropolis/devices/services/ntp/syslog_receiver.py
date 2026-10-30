"""Receive lab VyOS syslog over UDP and emit structured stdout records."""

from __future__ import annotations

import json
import re
import socket
from datetime import datetime, timezone

SYSLOG = re.compile(
    r"^<(?P<pri>\d{1,3})>(?:(?P<version>1) )?"
    r"(?:(?P<stamp>[A-Z][a-z]{2} +\d{1,2} \d\d:\d\d:\d\d|\S+) )?"
    r"(?P<source>[A-Za-z0-9_.-]+)\s+(?P<message>.*)$"
)
SECRET = re.compile(
    r"(?i)\b(password|passwd|secret|token|private[_-]?key)(\s*[:=]\s*)\S+"
)
# Match the console-safe PSK token without consuming JSON string delimiters.
IPSEC_PSK = re.compile(
    r"(?i)(\bauthentication\s+pre-shared-secret\s+)"
    r"(?P<quote>\\*[\"']|)(?P<value>[A-Za-z0-9_+/=-]+)(?P=quote)"
)
LEVELS = (
    "EMERGENCY",
    "ALERT",
    "CRITICAL",
    "ERROR",
    "WARNING",
    "NOTICE",
    "INFO",
    "DEBUG",
)


def parse_syslog(payload: bytes, peer: str) -> dict:
    raw = payload.decode("utf-8", errors="replace").rstrip("\x00\r\n")
    safe = SECRET.sub(lambda match: match[1] + match[2] + "[REDACTED]", raw)
    safe = IPSEC_PSK.sub(
        lambda match: match[1] + match["quote"] + "[REDACTED]" + match["quote"],
        safe,
    )
    match = SYSLOG.match(safe)
    pri = int(match["pri"]) if match else None
    return {
        "_metropolis_syslog": True,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": match["source"] if match else peer,
        "event": "syslog_message",
        "level": LEVELS[pri % 8] if pri is not None and pri < 192 else "UNKNOWN",
        "message": match["message"] if match else safe,
        "raw_syslog": safe,
        "peer_ip": peer,
        "facility": pri // 8 if pri is not None and pri < 192 else None,
    }


def main() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("0.0.0.0", 514))
        print(
            json.dumps({"event": "syslog_collector_started", "port": 514}), flush=True
        )
        while True:
            payload, address = listener.recvfrom(65535)
            print(json.dumps(parse_syslog(payload, address[0])), flush=True)


if __name__ == "__main__":
    main()
