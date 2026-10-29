"""Conservative, configurable cleaning of run-scoped packet features."""

import csv
import hashlib
import importlib.util
import json
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "clean_metropolis_features.py"
)
SPEC = importlib.util.spec_from_file_location("clean_metropolis_features", SCRIPT)
cleaner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleaner)


def setup_extraction(tmp_path, rows=None):
    feature_dir = tmp_path / "dataset" / "runs" / "custom_run" / "features"
    raw_dir = feature_dir / "raw"
    raw_dir.mkdir(parents=True)
    source = raw_dir / "user_link_47.csv"
    with source.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["frame.number", "ip.src", "mqtt.msgtype"])
        writer.writerows(
            rows
            if rows is not None
            else [[" 1 ", " 10.5.6.7 ", ""], ["2", " ", "PUBLISH"]]
        )
    report_path = feature_dir / "extraction_report.json"
    report_path.write_text(
        json.dumps(
            {
                "run_id": "custom_run",
                "testbed_id": "user_testbed",
                "dataset_version": "v2",
                "status": "complete",
                "fields": ["frame.number", "ip.src", "mqtt.msgtype"],
                "captures": {
                    "user_link_47": {
                        "status": "extracted",
                        "declared_link_id": "user_link_47",
                        "gns3_link_id": "gns3-random-id",
                        "output_file": str(source),
                        "output_sha256": hashlib.sha256(
                            source.read_bytes()
                        ).hexdigest(),
                    }
                },
                "failures": [],
            }
        )
    )
    return report_path, source


def read_output(report):
    path = Path(report["captures"]["user_link_47"]["output_file"])
    with path.open("r", encoding="utf-8", newline="") as source:
        return list(csv.reader(source))


def test_default_normalizes_whitespace_but_preserves_rows_fields_and_blanks(tmp_path):
    report_path, source = setup_extraction(tmp_path)
    original = source.read_bytes()
    report = cleaner.clean(report_path)
    assert report["status"] == "complete"
    assert report["run_id"] == "custom_run"
    assert read_output(report) == [
        ["frame.number", "ip.src", "mqtt.msgtype"],
        ["1", "10.5.6.7", ""],
        ["2", "", "PUBLISH"],
    ]
    assert source.read_bytes() == original
    assert report["captures"]["user_link_47"]["blank_values_before"] == {
        "frame.number": 0,
        "ip.src": 1,
        "mqtt.msgtype": 1,
    }


def test_user_rules_select_fill_and_drop_fields_without_fixed_ips(tmp_path):
    report_path, _ = setup_extraction(tmp_path)
    rules = tmp_path / "rules.json"
    rules.write_text(
        json.dumps(
            {
                "keep_fields": ["mqtt.msgtype", "frame.number", "ip.src"],
                "drop_fields": ["ip.src"],
                "drop_rows_missing_fields": ["frame.number"],
                "fill_missing": {"mqtt.msgtype": "unknown"},
            }
        )
    )
    report = cleaner.clean(report_path, rules_path=rules)
    assert read_output(report) == [
        ["mqtt.msgtype", "frame.number"],
        ["unknown", "1"],
        ["PUBLISH", "2"],
    ]
    assert report["captures"]["user_link_47"]["fields_dropped"] == ["ip.src"]


def test_required_field_drops_only_rows_missing_that_field(tmp_path):
    report_path, _ = setup_extraction(tmp_path)
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"drop_rows_missing_fields": ["ip.src"]}))
    report = cleaner.clean(report_path, rules_path=rules)
    assert report["captures"]["user_link_47"]["rows_in"] == 2
    assert report["captures"]["user_link_47"]["rows_out"] == 1
    assert report["captures"]["user_link_47"]["rows_dropped"] == 1


def test_changed_raw_csv_hash_is_rejected(tmp_path):
    report_path, source = setup_extraction(tmp_path)
    source.write_text("changed", encoding="utf-8")
    report = cleaner.clean(report_path)
    assert report["status"] == "incomplete"
    assert "SHA256 differs" in report["failures"][0]
    assert not (report_path.parent / "cleaned" / "user_link_47.csv").exists()


def test_malformed_csv_fails_without_publishing_partial(tmp_path):
    report_path, source = setup_extraction(tmp_path)
    source.write_text(
        'frame.number,ip.src,mqtt.msgtype\n1,"unclosed,2\n', encoding="utf-8"
    )
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    upstream = json.loads(report_path.read_text())
    upstream["captures"]["user_link_47"]["output_sha256"] = source_hash
    report_path.write_text(json.dumps(upstream))
    report = cleaner.clean(report_path)
    assert report["status"] == "incomplete"
    assert not list((report_path.parent / "cleaned").glob("*.csv*"))


def test_upstream_failure_is_reported(tmp_path):
    report_path, _ = setup_extraction(tmp_path)
    upstream = json.loads(report_path.read_text())
    upstream["status"] = "incomplete"
    upstream["failures"] = ["uncabled_link: no PCAP"]
    upstream["captures"]["uncabled_link"] = {"status": "failed"}
    report_path.write_text(json.dumps(upstream))
    report = cleaner.clean(report_path)
    assert report["captures"]["user_link_47"]["status"] == "cleaned"
    assert report["captures"]["uncabled_link"]["status"] == "failed"
    assert report["status"] == "incomplete"


def test_rules_that_remove_every_row_fail_explicitly(tmp_path):
    report_path, _ = setup_extraction(tmp_path)
    rules = tmp_path / "rules.json"
    rules.write_text(
        json.dumps({"drop_rows_missing_fields": ["mqtt.msgtype", "ip.src"]})
    )
    report = cleaner.clean(report_path, rules_path=rules)
    assert report["status"] == "incomplete"
    assert "removed every packet row" in report["failures"][0]
    assert not (report_path.parent / "cleaned" / "user_link_47.csv").exists()
