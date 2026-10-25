"""Summarize run resource samples and existing PCAPs without sending traffic."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import struct
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SECOND = 1_000_000_000
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
BANDWIDTH_FIELDS = (
    "run_id",
    "declared_link_id",
    "gns3_link_id",
    "timestamp",
    "interval_seconds",
    "packets",
    "bytes",
    "packets_per_second",
    "bits_per_second",
)


def _time_ns(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset")
    delta = parsed.astimezone(timezone.utc) - EPOCH
    return ((delta.days * 86400 + delta.seconds) * SECOND) + delta.microseconds * 1000


def _utc(value: int) -> str:
    seconds, fraction = divmod(value, SECOND)
    result = (EPOCH + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S")
    return result + (f".{fraction:09d}".rstrip("0") if fraction else "") + "Z"


def _read_pcap(path: Path) -> tuple[dict[int, list[int]], str, int, int]:
    """Validate classic-PCAP and count original frame bytes in UTC seconds."""
    formats = {
        b"\xd4\xc3\xb2\xa1": ("<", 1000),
        b"\xa1\xb2\xc3\xd4": (">", 1000),
        b"\x4d\x3c\xb2\xa1": ("<", 1),
        b"\xa1\xb2\x3c\x4d": (">", 1),
    }
    bins: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    first = last = None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        header = source.read(24)
        if len(header) != 24 or header[:4] not in formats:
            raise ValueError("file is not a complete classic PCAP")
        endian, multiplier = formats[header[:4]]
        digest.update(header)
        while record := source.read(16):
            if len(record) != 16:
                raise ValueError("truncated PCAP packet header")
            digest.update(record)
            seconds, fraction, included, original = struct.unpack(
                f"{endian}IIII", record
            )
            if fraction * multiplier >= SECOND:
                raise ValueError("invalid PCAP packet timestamp")
            if not 0 < included <= original <= 16 << 20:
                raise ValueError("invalid PCAP packet length")
            remaining = included
            while remaining:
                block = source.read(min(remaining, 1 << 20))
                if not block:
                    raise ValueError("truncated PCAP packet")
                digest.update(block)
                remaining -= len(block)
            timestamp = seconds * SECOND + fraction * multiplier
            first = timestamp if first is None else min(first, timestamp)
            last = timestamp if last is None else max(last, timestamp)
            bins[seconds][0] += 1
            bins[seconds][1] += original
    if first is None:
        raise ValueError("PCAP contains no packets")
    return bins, digest.hexdigest(), first, last


def _bandwidth(path: Path, entry: dict) -> tuple[list[dict], dict]:
    start = _time_ns(entry["started_at_utc"])
    stop = _time_ns(entry["stopped_at_utc"])
    if stop <= start:
        raise ValueError("capture stop must be after its start")
    bins, digest, first, last = _read_pcap(path)
    if entry.get("sha256") and entry["sha256"] != digest:
        raise ValueError("PCAP hash differs from the capture manifest")
    if start - first > 60 * SECOND or last - stop > 60 * SECOND:
        raise ValueError(
            "packet and controller capture clocks differ by more than 60 seconds; "
            "align compute clocks before interpreting traffic rates"
        )
    # API acknowledgment times and packet clocks may differ. Outside the
    # recorded window, only the packet's UTC-second bin is known. Include that
    # whole second rather than inventing a tiny observation window and rate.
    effective_start = start if first >= start else (first // SECOND) * SECOND
    effective_stop = stop if last < stop else (last // SECOND + 1) * SECOND
    estimated = effective_start != start or effective_stop != stop
    warnings = []
    if estimated:
        warnings.append(
            "Packet timestamps extend beyond controller capture times; "
            "check compute clock alignment. The effective window is estimated "
            "and includes complete UTC seconds for out-of-window packets."
        )
    rows = []
    for second in range(effective_start // SECOND, (effective_stop - 1) // SECOND + 1):
        left = max(second * SECOND, effective_start)
        right = min((second + 1) * SECOND, effective_stop)
        duration = (right - left) / SECOND
        packets, size = bins.get(second, (0, 0))
        rows.append(
            {
                "timestamp": _utc(left),
                "interval_seconds": duration,
                "packets": packets,
                "bytes": size,
                "packets_per_second": packets / duration,
                "bits_per_second": size * 8 / duration,
            }
        )
    total_packets = sum(item[0] for item in bins.values())
    total_bytes = sum(item[1] for item in bins.values())
    duration = (effective_stop - effective_start) / SECOND
    return rows, {
        "status": "complete",
        "declared_link_id": entry.get("declared_link_id"),
        "gns3_link_id": entry.get("gns3_link_id"),
        "local_file": str(path),
        "sha256": digest,
        "recorded_started_at_utc": entry["started_at_utc"],
        "recorded_stopped_at_utc": entry["stopped_at_utc"],
        "effective_started_at_utc": _utc(effective_start),
        "effective_stopped_at_utc": _utc(effective_stop),
        "window_estimated": estimated,
        "duration_seconds": duration,
        "packets": total_packets,
        "bytes": total_bytes,
        "mean_bits_per_second": total_bytes * 8 / duration,
        "mean_packets_per_second": total_packets / duration,
        "peak_bits_per_second": max(row["bits_per_second"] for row in rows),
        "warnings": warnings,
    }


def _number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _statistics(records: list[dict], fields: tuple[str, ...]) -> dict:
    result = {"samples": len(records)}
    for field in fields:
        values = [record[field] for record in records if _number(record.get(field))]
        result[field] = {
            "valid_samples": len(values),
            "mean": sum(values) / len(values) if values else None,
            "peak": max(values) if values else None,
        }
    return result


def _load_samples(
    run_dir: Path, relative: str, run_id: str, failures: list, *, identity_key: str
) -> list[dict]:
    records = []
    path = (run_dir / relative).resolve()
    if not path.is_relative_to(run_dir.resolve()):
        failures.append(
            {"source": "metrics", "reason": "sample path leaves run directory"}
        )
        return records
    try:
        with path.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, 1):
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict) or record.get("run_id") != run_id:
                        raise ValueError("sample has the wrong run identity")
                    for key in ("timestamp", "source", "compute_id", identity_key):
                        if not isinstance(record.get(key), str) or not record[key]:
                            raise ValueError(f"sample has no valid {key}")
                    _time_ns(record["timestamp"])
                except (ValueError, KeyError, TypeError) as exc:
                    failures.append(
                        {"source": relative, "reason": f"line {line_number}: {exc}"}
                    )
                else:
                    records.append(record)
    except (OSError, ValueError) as exc:
        failures.append({"source": relative, "reason": f"cannot read samples: {exc}"})
    return records


def _chart_name(kind: str, name: str, identity: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:80] or "source"
    suffix = hashlib.sha256(identity.encode()).hexdigest()[:12]
    return f"{kind}-{safe}-{suffix}.png"


def _series_values(
    samples: list[dict], field: str, interval: float, factor: float = 1
) -> tuple[list, list]:
    times, values = [], []
    previous = None
    for sample in samples:
        current = datetime.fromisoformat(sample["timestamp"].replace("Z", "+00:00"))
        if (
            previous is not None
            and (current - previous).total_seconds() > interval * 1.5
        ):
            times.append(previous + (current - previous) / 2)
            values.append(math.nan)
        times.append(current)
        values.append(
            sample[field] / factor if _number(sample.get(field)) else math.nan
        )
        previous = current
    return times, values


def _plot_resources(
    plt, run_dir: Path, records: list[dict], kind: str, failures: list, interval: float
) -> list[str]:
    grouped = defaultdict(list)
    key = "node_id" if kind == "container" else "compute_id"
    for record in records:
        grouped[record[key]].append(record)
    charts = []
    for identity, samples in sorted(grouped.items()):
        samples.sort(key=lambda value: _time_ns(value["timestamp"]))
        name = samples[0].get("source", identity)
        relative = "metrics/charts/" + _chart_name(kind, name, identity)
        figure, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        try:
            series = defaultdict(list)
            for sample in samples:
                series[sample.get("container_id", identity)].append(sample)
            for members in series.values():
                axes[0].plot(
                    *_series_values(members, "cpu_percent", interval), marker="."
                )
                memory_field = (
                    "memory_working_set_bytes"
                    if kind == "container"
                    else "memory_percent"
                )
                factor = 1024 * 1024 if kind == "container" else 1
                axes[1].plot(
                    *_series_values(members, memory_field, interval, factor), marker="."
                )
            axes[0].set_ylabel(
                "CPU % (100% = one core)" if kind == "container" else "Compute CPU %"
            )
            axes[1].set_ylabel(
                "Working-set RAM (MiB)" if kind == "container" else "Compute RAM %"
            )
            axes[1].set_xlabel("UTC time")
            for axis in axes:
                axis.grid(alpha=0.25)
                axis.set_ylim(bottom=0)
            if kind == "compute":
                for axis in axes:
                    axis.set_ylim(0, 100)
            figure.suptitle(f"{kind.title()}: {name}\n{identity}")
            figure.autofmt_xdate()
            figure.tight_layout()
            figure.savefig(run_dir / relative, dpi=120)
            charts.append(relative)
        except Exception as exc:  # noqa: BLE001 - keep other source charts
            failures.append(
                {"source": identity, "reason": f"resource chart failed: {exc}"}
            )
        finally:
            plt.close(figure)
    return charts


def _plot_traffic(plt, run_dir: Path, link: str, rows: list[dict]) -> str:
    relative = "metrics/charts/" + _chart_name("link", link, link)
    figure, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    try:
        times = [
            datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            for row in rows
        ]
        times.append(times[-1] + timedelta(seconds=rows[-1]["interval_seconds"]))
        rates = [row["bits_per_second"] / 1_000_000 for row in rows]
        packets = [row["packets_per_second"] for row in rows]
        axes[0].step(times, rates + rates[-1:], where="post")
        axes[1].step(times, packets + packets[-1:], where="post")
        axes[0].set_ylabel("Captured throughput (Mbps)")
        axes[1].set_ylabel("Packets / second")
        axes[1].set_xlabel("UTC time")
        for axis in axes:
            axis.grid(alpha=0.25)
            axis.set_ylim(bottom=0)
        figure.suptitle(f"Captured link: {link}")
        figure.autofmt_xdate()
        figure.tight_layout()
        figure.savefig(run_dir / relative, dpi=120)
        return relative
    finally:
        plt.close(figure)


def _pyplot():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def finalize_metrics(
    run_dir: Path,
    run_id: str,
    resource_result: dict,
    capture_manifest: dict | None,
    capture_manifest_path: Path | None,
    *,
    interval: float = 5.0,
) -> dict:
    """Keep durable samples and record partial reporting or chart failures."""
    run_dir = Path(run_dir)
    (run_dir / "metrics" / "charts").mkdir(parents=True, exist_ok=True)
    failures = list(resource_result.get("failures", []))
    traffic = {
        "csv_path": "metrics/bandwidth.csv",
        "bin_seconds": 1,
        "links": {},
        "failures": [],
    }
    result = {
        "enabled": True,
        "status": "running",
        "sample_interval_seconds": interval,
        "resource": resource_result,
        "traffic": traffic,
        "summary_path": "metrics/summary.json",
        "charts": [],
        "failures": failures,
    }
    containers = _load_samples(
        run_dir,
        resource_result.get("container_samples_path", "metrics/containers.jsonl"),
        run_id,
        failures,
        identity_key="node_id",
    )
    computes = _load_samples(
        run_dir,
        resource_result.get("compute_samples_path", "metrics/computes.jsonl"),
        run_id,
        failures,
        identity_key="compute_id",
    )
    if not containers and not computes:
        failures.append(
            {"source": "metrics", "reason": "no resource samples available"}
        )
    try:
        plt = _pyplot()
    except Exception as exc:  # noqa: BLE001 - data must survive unavailable charts
        plt = None
        failures.append(
            {"source": "charts", "reason": f"cannot initialize charts: {exc}"}
        )
    if plt is not None:
        for kind, samples in (("container", containers), ("compute", computes)):
            try:
                result["charts"].extend(
                    _plot_resources(plt, run_dir, samples, kind, failures, interval)
                )
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    {"source": kind, "reason": f"resource chart failed: {exc}"}
                )
    if not capture_manifest:
        traffic["failures"].append(
            {"source": "capture", "reason": "no capture manifest available"}
        )
    elif capture_manifest.get("run_id") != run_id:
        traffic["failures"].append(
            {"source": "capture", "reason": "capture manifest has the wrong run ID"}
        )
        capture_manifest = None
    entries = (capture_manifest or {}).get("captures", {})
    selected = (
        set((capture_manifest or {}).get("coverage", {}).get("selected_links", []))
        | entries.keys()
    )
    with (run_dir / traffic["csv_path"]).open(
        "w", encoding="utf-8", newline=""
    ) as output:
        writer = csv.DictWriter(output, fieldnames=BANDWIDTH_FIELDS)
        writer.writeheader()
        for link in sorted(selected):
            entry = entries.get(link, {})
            try:
                if not entry.get("local_file"):
                    raise ValueError("selected link has no saved PCAP")
                if not entry.get("gns3_link_id"):
                    raise ValueError("capture has no GNS3 link identity")
                path = Path(entry["local_file"])
                if not path.is_absolute():
                    if capture_manifest_path is None:
                        raise ValueError(
                            "relative PCAP requires its capture manifest path"
                        )
                    path = capture_manifest_path.parent / path
                rows, summary = _bandwidth(path.resolve(), entry)
                traffic["links"][link] = summary
                for row in rows:
                    writer.writerow(
                        {
                            "run_id": run_id,
                            "declared_link_id": entry.get("declared_link_id"),
                            "gns3_link_id": entry["gns3_link_id"],
                            **row,
                        }
                    )
                output.flush()
                if plt is not None:
                    try:
                        result["charts"].append(_plot_traffic(plt, run_dir, link, rows))
                    except Exception as exc:  # noqa: BLE001
                        failures.append(
                            {"source": link, "reason": f"traffic chart failed: {exc}"}
                        )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                traffic["links"][link] = {
                    "status": "failed",
                    "declared_link_id": entry.get("declared_link_id"),
                    "gns3_link_id": entry.get("gns3_link_id"),
                    "reason": str(exc),
                }
                traffic["failures"].append({"source": link, "reason": str(exc)})
    if not selected:
        traffic["failures"].append(
            {"source": "capture", "reason": "no captured links available"}
        )
    failures.extend(traffic["failures"])
    if resource_result.get("status") != "complete" and not resource_result.get(
        "failures"
    ):
        failures.append(
            {"source": "resources", "reason": "resource collection was incomplete"}
        )
    result["status"] = "failed" if failures else "complete"
    container_groups = defaultdict(list)
    compute_groups = defaultdict(list)
    for sample in containers:
        container_groups[sample.get("node_id", "unknown")].append(sample)
    for sample in computes:
        compute_groups[sample.get("compute_id", "unknown")].append(sample)
    summary = {
        **result,
        "run_id": run_id,
        "container_statistics": {
            key: _statistics(
                values,
                ("cpu_percent", "memory_usage_bytes", "memory_working_set_bytes"),
            )
            for key, values in container_groups.items()
        },
        "compute_statistics": {
            key: _statistics(values, ("cpu_percent", "memory_percent"))
            for key, values in compute_groups.items()
        },
        "interpretation": {
            "throughput": "Original frame bytes observed per captured link; excludes physical-layer overhead. Links are not summed because packets can cross multiple capture points.",
            "compute_usage": "High compute utilization can indicate resource pressure; it does not establish the cause of a slowdown.",
            "unsupported_nodes": "Non-Docker nodes have no per-container samples. QEMU and kernel switching work can contribute to compute usage.",
        },
    }
    (run_dir / result["summary_path"]).write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return result
