"""Extract packet fields from a Metropolis capture manifest without topology filters."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
DEFAULT_FIELDS = Path(__file__).parent / "features" / "packet_fields.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_fields(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    fields = data.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError("field configuration must contain a nonempty fields list")
    if any(
        not isinstance(field, str) or not re.fullmatch(r"[A-Za-z0-9_.]+", field)
        for field in fields
    ):
        raise ValueError("field names must be TShark field identifiers")
    if len(fields) != len(set(fields)):
        raise ValueError("field configuration contains duplicate fields")
    return fields


def _capture_path(entry: dict, manifest_path: Path) -> Path:
    value = entry.get("local_file")
    if not isinstance(value, str) or not value:
        raise ValueError("capture has no local_file")
    path = Path(value)
    if not path.is_absolute():
        path = manifest_path.parent / path
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"PCAP is missing or empty: {path}")
    return path.resolve()


def _count_csv_rows(path: Path, expected_header: list[str]) -> int:
    with path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.reader(source)
        if next(reader, None) != expected_header:
            raise ValueError("TShark output header does not match selected fields")
        count = 0
        for row in reader:
            if len(row) != len(expected_header):
                raise ValueError("TShark output contains a malformed row")
            count += 1
    if not count:
        raise ValueError("TShark produced no packet rows")
    return count


def extract(
    capture_manifest: Path,
    *,
    fields_config: Path = DEFAULT_FIELDS,
    output_dir: Path | None = None,
    tshark: str = "tshark",
) -> dict:
    """Process each recorded capture independently; always write a run report."""
    capture_manifest = capture_manifest.resolve()
    source = json.loads(capture_manifest.read_text(encoding="utf-8"))
    run_id = source.get("run_id")
    if not isinstance(run_id, str) or not SAFE_ID.fullmatch(run_id):
        raise ValueError("capture manifest has an invalid run_id")
    captures = source.get("captures")
    if not isinstance(captures, dict):
        raise ValueError("capture manifest has no captures mapping")
    fields_config = fields_config.resolve()
    fields = load_fields(fields_config)
    if output_dir is None:
        dataset_path = source.get("dataset_path")
        if not isinstance(dataset_path, str) or not dataset_path:
            raise ValueError("capture manifest has no dataset_path; pass --output-dir")
        output_dir = Path(dataset_path) / "runs" / run_id / "features"
    output_dir = output_dir.resolve()
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "1.0",
        "run_id": run_id,
        "testbed_id": source.get("testbed_id"),
        "dataset_version": source.get("dataset_version"),
        "started_at_utc": utc_now(),
        "capture_manifest": str(capture_manifest),
        "fields_config": str(fields_config),
        "fields_config_sha256": sha256(fields_config),
        "fields": fields,
        "captures": {},
        "failures": [],
    }
    report_path = output_dir / "extraction_report.json"
    try:
        for capture_id, entry in captures.items():
            details = {
                "declared_link_id": entry.get("declared_link_id")
                if isinstance(entry, dict)
                else None,
                "gns3_link_id": entry.get("gns3_link_id")
                if isinstance(entry, dict)
                else None,
            }
            report["captures"][capture_id] = details
            partial = None
            try:
                if not isinstance(capture_id, str) or not SAFE_ID.fullmatch(capture_id):
                    raise ValueError("invalid capture point ID")
                if not isinstance(entry, dict):
                    raise ValueError("capture entry must be an object")
                pcap = _capture_path(entry, capture_manifest)
                details["pcap_file"] = str(pcap)
                actual_hash = sha256(pcap)
                if entry.get("sha256") and entry["sha256"] != actual_hash:
                    raise ValueError("PCAP SHA256 differs from capture manifest")
                details["pcap_sha256"] = actual_hash
                destination = raw_dir / f"{capture_id}.csv"
                partial = raw_dir / f"{capture_id}.csv.part"
                command = [
                    tshark,
                    "-n",
                    "-r",
                    str(pcap),
                    "-T",
                    "fields",
                    "-E",
                    "header=y",
                    "-E",
                    "separator=,",
                    "-E",
                    "quote=d",
                    "-E",
                    "occurrence=f",
                ]
                for field in fields:
                    command.extend(("-e", field))
                with partial.open("w", encoding="utf-8", newline="") as target:
                    completed = subprocess.run(
                        command,
                        stdout=target,
                        stderr=subprocess.PIPE,
                        text=True,
                        check=False,
                    )
                if completed.returncode:
                    raise RuntimeError(
                        f"TShark exited {completed.returncode}: {completed.stderr.strip()[:500]}"
                    )
                rows = _count_csv_rows(partial, fields)
                os.replace(partial, destination)
                details.update(
                    {
                        "status": "extracted",
                        "output_file": str(destination),
                        "output_sha256": sha256(destination),
                        "rows": rows,
                        "output_bytes": destination.stat().st_size,
                    }
                )
            except (OSError, ValueError, RuntimeError, csv.Error) as exc:
                details["status"] = "failed"
                details["error"] = f"{type(exc).__name__}: {exc}"
                report["failures"].append(f"{capture_id}: {details['error']}")
            finally:
                if partial is not None:
                    partial.unlink(missing_ok=True)
                report_path.write_text(
                    json.dumps(report, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
    finally:
        report["stopped_at_utc"] = utc_now()
        report["status"] = (
            "complete" if captures and not report["failures"] else "incomplete"
        )
        if not captures:
            report["failures"].append("capture manifest contains no captures")
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "capture_manifest", type=Path, help="captures/<run-id>/manifest.json"
    )
    parser.add_argument("--fields-config", type=Path, default=DEFAULT_FIELDS)
    parser.add_argument(
        "--output-dir", type=Path, help="defaults to dataset/runs/<run-id>/features"
    )
    parser.add_argument("--tshark", default="tshark", help="TShark executable")
    args = parser.parse_args()
    report = extract(
        args.capture_manifest,
        fields_config=args.fields_config,
        output_dir=args.output_dir,
        tshark=args.tshark,
    )
    print(
        json.dumps(
            {
                "run_id": report["run_id"],
                "status": report["status"],
                "captures": report["captures"],
                "failures": report["failures"],
            },
            indent=2,
        )
    )
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
