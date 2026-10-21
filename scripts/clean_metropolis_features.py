"""Clean extracted Metropolis packet CSVs using explicit, topology-neutral rules."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
DEFAULT_RULES = Path(__file__).parent / "features" / "cleaning_rules.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_rules(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("cleaning rules must be a JSON object")
    allowed = {
        "strip_whitespace",
        "keep_fields",
        "drop_fields",
        "drop_rows_missing_fields",
        "fill_missing",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"unknown cleaning rules: {sorted(unknown)}")
    rules = {
        "strip_whitespace": data.get("strip_whitespace", True),
        "keep_fields": data.get("keep_fields"),
        "drop_fields": data.get("drop_fields", []),
        "drop_rows_missing_fields": data.get("drop_rows_missing_fields", []),
        "fill_missing": data.get("fill_missing", {}),
    }
    if not isinstance(rules["strip_whitespace"], bool):
        raise ValueError("strip_whitespace must be a boolean")
    for key in ("keep_fields", "drop_fields", "drop_rows_missing_fields"):
        values = rules[key]
        if key == "keep_fields" and values is None:
            continue
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError(f"{key} must be a list of field names")
        if len(values) != len(set(values)):
            raise ValueError(f"{key} contains duplicate fields")
    fill = rules["fill_missing"]
    if not isinstance(fill, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in fill.items()
    ):
        raise ValueError("fill_missing must map field names to replacement strings")
    return rules


def selected_fields(header: list[str], rules: dict) -> list[str]:
    if not header or len(header) != len(set(header)):
        raise ValueError("input CSV has an empty or duplicate field header")
    referenced = (
        (rules["keep_fields"] or [])
        + rules["drop_fields"]
        + rules["drop_rows_missing_fields"]
        + list(rules["fill_missing"])
    )
    unknown = sorted(set(referenced) - set(header))
    if unknown:
        raise ValueError(f"cleaning rules reference missing fields: {unknown}")
    if rules["keep_fields"] is None:
        kept = [field for field in header if field not in rules["drop_fields"]]
    else:
        kept = [
            field for field in rules["keep_fields"] if field not in rules["drop_fields"]
        ]
    if not kept:
        raise ValueError("cleaning rules select no fields")
    unavailable = sorted(
        (set(rules["drop_rows_missing_fields"]) | set(rules["fill_missing"]))
        - set(kept)
    )
    if unavailable:
        raise ValueError(f"row and fill rules require kept fields: {unavailable}")
    return kept


def clean_csv(
    source: Path, partial: Path, expected_fields: list[str], rules: dict
) -> dict:
    """Stream one CSV; malformed input fails without publishing a partial output."""
    with source.open("r", encoding="utf-8", newline="") as input_file:
        reader = csv.reader(input_file, strict=True)
        header = next(reader, None)
        if header != expected_fields:
            raise ValueError("raw CSV header differs from extraction report")
        kept = selected_fields(header, rules)
        indexes = {field: header.index(field) for field in kept}
        rows_in = rows_out = 0
        blanks_before = {field: 0 for field in kept}
        with partial.open("w", encoding="utf-8", newline="") as output_file:
            writer = csv.writer(output_file)
            writer.writerow(kept)
            for row in reader:
                rows_in += 1
                if len(row) != len(header):
                    raise ValueError(
                        f"raw CSV row {rows_in} has {len(row)} values, expected {len(header)}"
                    )
                values = {
                    field: row[index].strip()
                    if rules["strip_whitespace"]
                    else row[index]
                    for field, index in indexes.items()
                }
                for field, value in values.items():
                    if value == "":
                        blanks_before[field] += 1
                if any(
                    values[field] == "" for field in rules["drop_rows_missing_fields"]
                ):
                    continue
                for field, replacement in rules["fill_missing"].items():
                    if values[field] == "":
                        values[field] = replacement
                writer.writerow([values[field] for field in kept])
                rows_out += 1
    if not rows_in:
        raise ValueError("raw CSV contains no packet rows")
    if not rows_out:
        raise ValueError("cleaning rules removed every packet row")
    return {
        "rows_in": rows_in,
        "rows_out": rows_out,
        "rows_dropped": rows_in - rows_out,
        "fields_kept": kept,
        "fields_dropped": [field for field in header if field not in kept],
        "blank_values_before": blanks_before,
    }


def clean(
    extraction_report: Path,
    *,
    rules_path: Path = DEFAULT_RULES,
    output_dir: Path | None = None,
) -> dict:
    """Clean each extracted capture independently and write a provenance report."""
    extraction_report = extraction_report.resolve()
    extracted = json.loads(extraction_report.read_text(encoding="utf-8"))
    run_id = extracted.get("run_id")
    if not isinstance(run_id, str) or not SAFE_ID.fullmatch(run_id):
        raise ValueError("extraction report has an invalid run_id")
    captures = extracted.get("captures")
    fields = extracted.get("fields")
    if not isinstance(captures, dict) or not isinstance(fields, list) or not fields:
        raise ValueError("extraction report needs captures and fields")
    rules_path = rules_path.resolve()
    rules = load_rules(rules_path)
    selected_fields(fields, rules)
    output_dir = (output_dir or extraction_report.parent).resolve()
    cleaned_dir = output_dir / "cleaned"
    cleaned_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "1.0",
        "run_id": run_id,
        "testbed_id": extracted.get("testbed_id"),
        "dataset_version": extracted.get("dataset_version"),
        "started_at_utc": utc_now(),
        "extraction_report": str(extraction_report),
        "extraction_report_sha256": sha256(extraction_report),
        "upstream_status": extracted.get("status"),
        "upstream_failures": extracted.get("failures", []),
        "rules_file": str(rules_path),
        "rules_sha256": sha256(rules_path),
        "rules": rules,
        "captures": {},
        "failures": [],
    }
    report_path = output_dir / "cleaning_report.json"
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
                if not isinstance(entry, dict) or entry.get("status") != "extracted":
                    raise ValueError("capture was not successfully extracted")
                source_value = entry.get("output_file")
                if not isinstance(source_value, str) or not source_value:
                    raise ValueError("extracted capture has no output_file")
                source = Path(source_value)
                if not source.is_absolute():
                    source = extraction_report.parent / source
                source = source.resolve()
                if not source.is_file():
                    raise ValueError(f"raw CSV is missing: {source}")
                actual_hash = sha256(source)
                if actual_hash != entry.get("output_sha256"):
                    raise ValueError("raw CSV SHA256 differs from extraction report")
                details["raw_file"] = str(source)
                details["raw_sha256"] = actual_hash
                destination = cleaned_dir / f"{capture_id}.csv"
                partial = cleaned_dir / f"{capture_id}.csv.part"
                counts = clean_csv(source, partial, fields, rules)
                os.replace(partial, destination)
                details.update(
                    {
                        "status": "cleaned",
                        "output_file": str(destination),
                        "output_sha256": sha256(destination),
                        "output_bytes": destination.stat().st_size,
                        **counts,
                    }
                )
            except (OSError, ValueError, csv.Error) as exc:
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
        if not captures:
            report["failures"].append("extraction report contains no captures")
        if extracted.get("status") != "complete":
            report["failures"].append("upstream extraction report is incomplete")
        report["status"] = (
            "complete"
            if captures
            and not report["failures"]
            and extracted.get("status") == "complete"
            else "incomplete"
        )
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "extraction_report",
        type=Path,
        help="runs/<run-id>/features/extraction_report.json",
    )
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument(
        "--output-dir", type=Path, help="defaults to the extraction report's directory"
    )
    args = parser.parse_args()
    report = clean(
        args.extraction_report, rules_path=args.rules, output_dir=args.output_dir
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
