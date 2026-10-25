"""Resource/traffic reports use recorded samples rather than live services."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

GNS3 = Path(__file__).resolve().parents[1] / "scripts" / "gns3"
sys.path.insert(0, str(GNS3))
import metrics_report  # noqa: E402

START = 1_700_000_000


def timestamp(seconds: float) -> str:
    return (
        datetime.fromtimestamp(seconds, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def pcap(path: Path, records, *, endian="<", nanoseconds=False):
    magic = 0xA1B23C4D if nanoseconds else 0xA1B2C3D4
    payload = struct.pack(f"{endian}IHHIIII", magic, 2, 4, 0, 0, 65535, 1)
    for sec, fraction, original, captured in records:
        payload += struct.pack(f"{endian}IIII", sec, fraction, captured, original)
        payload += b"a" * captured
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def entry(path, **extra):
    return {
        "declared_link_id": "device-switch",
        "gns3_link_id": "physical-123",
        "local_file": str(path),
        "started_at_utc": timestamp(START + 0.5),
        "stopped_at_utc": timestamp(START + 3.25),
        **extra,
    }


@pytest.mark.parametrize("endian", ["<", ">"])
@pytest.mark.parametrize("nano", [False, True])
def test_classic_formats_original_lengths_empty_bins_and_partial_windows(
    tmp_path, endian, nano
):
    path = tmp_path / "data.pcap"
    unit = 1_000_000_000 if nano else 1_000_000
    digest = pcap(
        path,
        [(START + 3, unit // 8, 100, 20), (START, unit * 3 // 4, 80, 10)],
        endian=endian,
        nanoseconds=nano,
    )
    rows, summary = metrics_report._bandwidth(path, entry(path, sha256=digest))
    assert [row["packets"] for row in rows] == [1, 0, 0, 1]
    assert [row["bytes"] for row in rows] == [80, 0, 0, 100]
    assert [row["interval_seconds"] for row in rows] == [0.5, 1, 1, 0.25]
    assert rows[0]["bits_per_second"] == 1280
    assert rows[-1]["packets_per_second"] == 4
    assert summary["bytes"] == 180
    assert summary["duration_seconds"] == 2.75
    assert summary["mean_bits_per_second"] == pytest.approx(1440 / 2.75)
    assert summary["warnings"] == []
    assert rows[0]["timestamp"].endswith("Z")


def test_packet_time_envelope_is_visible_and_no_packet_dropped(tmp_path):
    path = tmp_path / "data.pcap"
    pcap(path, [(START, 250000, 100, 10), (START + 4, 100000, 200, 10)])
    rows, summary = metrics_report._bandwidth(path, entry(path))
    assert sum(row["packets"] for row in rows) == 2
    assert summary["warnings"]
    assert datetime.fromisoformat(
        summary["effective_started_at_utc"]
    ) == datetime.fromisoformat(timestamp(START))
    assert summary["recorded_started_at_utc"] == timestamp(START + 0.5)
    assert summary["window_estimated"] is True


def test_final_packet_at_exact_second_uses_full_estimated_bin(tmp_path):
    path = tmp_path / "later-packet.pcap"
    pcap(path, [(START + 4, 0, 100, 20)])
    rows, summary = metrics_report._bandwidth(path, entry(path))
    assert rows[-1]["interval_seconds"] == 1
    assert rows[-1]["bits_per_second"] == 800
    assert summary["peak_bits_per_second"] == 800
    assert summary["window_estimated"] is True


def test_near_boundary_first_packet_uses_full_estimated_bin(tmp_path):
    path = tmp_path / "earlier-packet.pcap"
    pcap(path, [(START, 999999, 100, 20)])
    rows, summary = metrics_report._bandwidth(
        path,
        entry(path, started_at_utc=timestamp(START + 1.000001)),
    )
    assert rows[0]["interval_seconds"] == 1
    assert rows[0]["bits_per_second"] == 800
    assert summary["window_estimated"] is True


def test_large_packet_clock_offset_is_reported_without_allocating_empty_years(tmp_path):
    path = tmp_path / "old-clock.pcap"
    pcap(path, [(1, 0, 100, 20)])
    with pytest.raises(ValueError, match="align compute clocks"):
        metrics_report._bandwidth(path, entry(path))


def test_resource_series_leaves_gaps_for_missing_readings_and_samples():
    samples = [
        {"timestamp": timestamp(START), "cpu_percent": None},
        {"timestamp": timestamp(START + 5), "cpu_percent": 10},
        {"timestamp": timestamp(START + 20), "cpu_percent": 20},
    ]
    _, values = metrics_report._series_values(samples, "cpu_percent", 5)
    assert math.isnan(values[0])
    assert values[1] == 10
    assert math.isnan(values[2])
    assert values[3] == 20


@pytest.mark.parametrize(
    "corruption", ["empty", "header", "packet", "length", "fraction", "hash"]
)
def test_bad_capture_is_failure_not_zero_traffic(tmp_path, corruption):
    path = tmp_path / "data.pcap"
    records = [] if corruption == "empty" else [(START + 1, 0, 100, 20)]
    pcap(path, records)
    extra = {}
    if corruption == "header":
        path.write_bytes(path.read_bytes()[:10])
    elif corruption == "packet":
        path.write_bytes(path.read_bytes()[:-1])
    elif corruption == "length":
        pcap(path, [(START + 1, 0, 10, 20)])
    elif corruption == "fraction":
        pcap(path, [(START + 1, 1_000_000, 100, 20)])
    elif corruption == "hash":
        extra["sha256"] = "0" * 64
    with pytest.raises(ValueError):
        metrics_report._bandwidth(path, entry(path, **extra))


def sample_files(run_dir, run_id="run-1"):
    (run_dir / "metrics").mkdir(parents=True, exist_ok=True)
    samples = []
    for second, cpu, cid in (
        (0, None, "first"),
        (1, 25.0, "first"),
        (2, 10.0, "second"),
    ):
        samples.append(
            {
                "timestamp": timestamp(START + second),
                "run_id": run_id,
                "source": "PLC / custom",
                "node_id": "node-unique",
                "compute_id": "remote",
                "container_id": cid,
                "cpu_percent": cpu,
                "memory_usage_bytes": 1024 * 1024,
                "memory_working_set_bytes": 512 * 1024,
            }
        )
    (run_dir / "metrics/containers.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in samples)
    )
    compute = {
        "timestamp": timestamp(START),
        "run_id": run_id,
        "source": "Compute / remote",
        "compute_id": "remote",
        "cpu_percent": 33,
        "memory_percent": 40,
    }
    (run_dir / "metrics/computes.jsonl").write_text(json.dumps(compute) + "\n")
    return {
        "status": "complete",
        "container_samples_path": "metrics/containers.jsonl",
        "compute_samples_path": "metrics/computes.jsonl",
        "failures": [],
    }


def test_finalize_separate_link_rates_attribution_and_real_charts(tmp_path):
    run_dir = tmp_path / "run"
    resources = sample_files(run_dir)
    path = tmp_path / "capture.pcap"
    pcap(path, [(START + 1, 0, 100, 20)])
    manifest = {
        "run_id": "run-1",
        "captures": {
            "first-link": entry(path),
            "second-link": entry(
                path, gns3_link_id="physical-456", declared_link_id=None
            ),
        },
    }
    result = metrics_report.finalize_metrics(
        run_dir, "run-1", resources, manifest, tmp_path / "manifest.json"
    )
    assert result["status"] == "complete"
    assert len(result["charts"]) == 4
    for chart in result["charts"]:
        assert (run_dir / chart).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert ".." not in Path(chart).parts
    with (run_dir / result["traffic"]["csv_path"]).open() as file:
        rows = list(csv.DictReader(file))
    assert {row["gns3_link_id"] for row in rows} == {"physical-123", "physical-456"}
    assert all(row["run_id"] == "run-1" for row in rows)
    assert len(result["traffic"]["links"]) == 2
    assert "total_bytes" not in result["traffic"]
    summary = json.loads((run_dir / result["summary_path"]).read_text())
    cpu = summary["container_statistics"]["node-unique"]["cpu_percent"]
    assert cpu == {"valid_samples": 2, "mean": 17.5, "peak": 25.0}
    assert (run_dir / "metrics/containers.jsonl").read_text().count("\n") == 3


def test_partial_capture_and_missing_chart_dependency_preserve_outputs(
    tmp_path, monkeypatch
):
    run_dir = tmp_path / "run"
    resources = sample_files(run_dir)
    path = tmp_path / "capture.pcap"
    pcap(path, [(START + 1, 0, 100, 20)])
    manifest = {
        "run_id": "run-1",
        "captures": {"good": entry(path), "failed": {"gns3_link_id": "other"}},
        "coverage": {"selected_links": ["good", "failed", "not-returned"]},
    }
    monkeypatch.setattr(
        metrics_report,
        "_pyplot",
        lambda: (_ for _ in ()).throw(ImportError("no plotting library")),
    )
    result = metrics_report.finalize_metrics(
        run_dir, "run-1", resources, manifest, tmp_path / "manifest.json"
    )
    assert result["status"] == "failed"
    assert result["traffic"]["links"]["good"]["status"] == "complete"
    assert result["traffic"]["links"]["failed"]["status"] == "failed"
    assert result["traffic"]["links"]["not-returned"]["status"] == "failed"
    assert any(item["source"] == "charts" for item in result["failures"])
    assert (run_dir / "metrics/summary.json").is_file()
    assert (run_dir / "metrics/bandwidth.csv").stat().st_size > 0


def test_bad_sample_and_capture_run_identity_are_reported(tmp_path):
    run_dir = tmp_path / "run"
    resources = sample_files(run_dir)
    with (run_dir / "metrics/containers.jsonl").open("a") as output:
        output.write("malformed\n")
        output.write(
            json.dumps({"run_id": "other", "timestamp": timestamp(START)}) + "\n"
        )
    result = metrics_report.finalize_metrics(
        run_dir, "run-1", resources, {"run_id": "other", "captures": {}}, None
    )
    assert result["status"] == "failed"
    assert sum("line " in item["reason"] for item in result["failures"]) == 2
    assert any("wrong run ID" in item["reason"] for item in result["failures"])


def test_no_samples_or_captures_is_never_success(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "metrics").mkdir(parents=True)
    (run_dir / "metrics/containers.jsonl").touch()
    (run_dir / "metrics/computes.jsonl").touch()
    result = metrics_report.finalize_metrics(
        run_dir, "run-1", {"status": "complete"}, None, None
    )
    assert result["status"] == "failed"
    assert result["charts"] == []
    assert any(
        item["reason"] == "no resource samples available" for item in result["failures"]
    )


def test_chart_names_disambiguate_sanitized_identity_collisions():
    one = metrics_report._chart_name("container", "node/a", "first-id")
    two = metrics_report._chart_name("container", "node?a", "second-id")
    assert one != two
    assert "/" not in one and "?" not in two
