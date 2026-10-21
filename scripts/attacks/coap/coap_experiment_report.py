"""Combine CoAP sender, sensor and receiver counts beside a lab pcap."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def summary(path: Path, tool: str) -> dict:
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("{")
    ]
    matches = [
        item
        for item in records
        if item.get("tool") == tool and item.get("event") == "summary"
    ]
    if len(matches) != 1:
        raise ValueError(f"{path} must contain exactly one {tool} summary")
    return matches[0]


def sensor_counts(path: Path, experiment_id: str) -> dict:
    count = request_bytes = response_bytes = 0
    codes: Counter[str] = Counter()
    resources: Counter[str] = Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        if "COAP_AUDIT " not in line:
            continue
        try:
            record = json.loads(line.split("COAP_AUDIT ", 1)[1])
        except json.JSONDecodeError:
            continue
        if record.get("experiment_id") != experiment_id:
            continue
        count += 1
        request_bytes += record["request_bytes"]
        response_bytes += record["response_bytes"]
        codes[record["response_code"]] += 1
        resources[record["resource"]] += 1
    return {
        "responses": count,
        "request_bytes": request_bytes,
        "response_bytes": response_bytes,
        "response_codes": dict(codes),
        "resources": dict(resources),
    }


def main() -> int:
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--sender", type=Path, required=True)
    options.add_argument("--sensor-log", type=Path, required=True)
    options.add_argument("--receiver", type=Path, required=True)
    options.add_argument("--pcap", type=Path, help="Capture file kept with the report")
    options.add_argument("--output", type=Path, required=True)
    args = options.parse_args()
    sent = summary(args.sender, "coap")
    received = summary(args.receiver, "coap_receiver")
    experiment_id = sent["experiment_id"]
    if experiment_id != received["experiment_id"]:
        raise ValueError("Sender and receiver experiment IDs differ")
    sensor = sensor_counts(args.sensor_log, experiment_id)
    if sensor["responses"] == 0:
        raise ValueError("No matching COAP_AUDIT records in sensor log")
    if args.pcap and not args.pcap.is_file():
        raise ValueError(f"Capture file does not exist: {args.pcap}")
    report = {
        "experiment_id": experiment_id,
        "pcap": str(args.pcap) if args.pcap else None,
        "sender": {
            "sent": sent["sent"],
            "request_bytes": sent["request_bytes"],
            "resources_sent": sent["resources_sent"],
        },
        "sensor": sensor,
        "receiver": {
            "received": received["received"],
            "response_bytes": received["response_bytes"],
            "response_codes": received["response_codes"],
        },
        "observed_payload_ratio": (
            received["response_bytes"] / sent["request_bytes"]
            if sent["request_bytes"]
            else None
        ),
        "sensor_payload_ratio": (
            sensor["response_bytes"] / sensor["request_bytes"]
            if sensor["request_bytes"]
            else None
        ),
        "notes": "Counts are independent observations; missing packets and capture gaps can make them differ.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "event": "report_written",
                "path": str(args.output),
                "experiment_id": experiment_id,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
