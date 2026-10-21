"""Stage-one extraction uses the capture manifest, not fixed topology addresses."""

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "extract_metropolis_features.py"
)
SPEC = importlib.util.spec_from_file_location("extract_metropolis_features", SCRIPT)
extractor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(extractor)


def setup_capture(tmp_path, capture_id="user_link_47"):
    dataset = tmp_path / "my_dataset"
    capture_dir = dataset / "captures" / "run_1"
    capture_dir.mkdir(parents=True)
    pcap = capture_dir / f"{capture_id}.pcap"
    pcap.write_bytes(b"test packet bytes")
    manifest = capture_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "run_id": "run_1",
                "testbed_id": "custom",
                "dataset_version": "v2",
                "dataset_path": str(dataset),
                "captures": {
                    capture_id: {
                        "declared_link_id": capture_id,
                        "gns3_link_id": "random-gns3-uuid",
                        "local_file": str(pcap),
                        "sha256": hashlib.sha256(pcap.read_bytes()).hexdigest(),
                    }
                },
            }
        )
    )
    fields = tmp_path / "fields.json"
    fields.write_text(json.dumps({"fields": ["frame.number", "ip.src", "eth.src"]}))
    return manifest, fields


def test_extract_all_packet_rows_without_ip_filter(tmp_path, monkeypatch):
    manifest, fields = setup_capture(tmp_path)

    def fake_run(command, **kwargs):
        assert command[:5] == [
            "tshark",
            "-n",
            "-r",
            str(manifest.parent / "user_link_47.pcap"),
            "-T",
        ]
        assert command.count("-e") == 3
        kwargs["stdout"].write(
            "frame.number,ip.src,eth.src\n1,10.99.4.2,aa:bb\n2,,cc:dd\n"
        )
        return subprocess.CompletedProcess(command, 0, stderr="")

    monkeypatch.setattr(extractor.subprocess, "run", fake_run)
    report = extractor.extract(manifest, fields_config=fields)
    output = tmp_path / "my_dataset" / "runs" / "run_1" / "features"
    assert report["status"] == "complete"
    assert report["captures"]["user_link_47"]["rows"] == 2
    assert (output / "raw" / "user_link_47.csv").read_text().splitlines()[
        2
    ] == "2,,cc:dd"
    assert json.loads((output / "extraction_report.json").read_text()) == report


def test_partial_failure_is_reported_and_other_capture_survives(tmp_path, monkeypatch):
    manifest, fields = setup_capture(tmp_path)
    data = json.loads(manifest.read_text())
    data["captures"]["uncabled_link"] = {"declared_link_id": "uncabled_link"}
    manifest.write_text(json.dumps(data))

    def fake_run(command, **kwargs):
        kwargs["stdout"].write("frame.number,ip.src,eth.src\n1,192.0.2.5,aa\n")
        return subprocess.CompletedProcess(command, 0, stderr="")

    monkeypatch.setattr(extractor.subprocess, "run", fake_run)
    report = extractor.extract(manifest, fields_config=fields)
    assert report["status"] == "incomplete"
    assert report["captures"]["user_link_47"]["status"] == "extracted"
    assert report["captures"]["uncabled_link"]["status"] == "failed"
    assert "no local_file" in report["failures"][0]


def test_hash_mismatch_blocks_extraction(tmp_path, monkeypatch):
    manifest, fields = setup_capture(tmp_path)
    data = json.loads(manifest.read_text())
    data["captures"]["user_link_47"]["sha256"] = "wrong"
    manifest.write_text(json.dumps(data))
    monkeypatch.setattr(
        extractor.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    report = extractor.extract(manifest, fields_config=fields)
    assert report["status"] == "incomplete"
    assert "SHA256 differs" in report["failures"][0]


def test_tshark_failure_does_not_publish_partial_csv(tmp_path, monkeypatch):
    manifest, fields = setup_capture(tmp_path)

    def fake_run(command, **kwargs):
        kwargs["stdout"].write("frame.number,ip.src,eth.src\n")
        return subprocess.CompletedProcess(command, 2, stderr="bad field")

    monkeypatch.setattr(extractor.subprocess, "run", fake_run)
    report = extractor.extract(manifest, fields_config=fields)
    raw = tmp_path / "my_dataset" / "runs" / "run_1" / "features" / "raw"
    assert report["status"] == "incomplete"
    assert "bad field" in report["failures"][0]
    assert not list(raw.glob("*.csv*"))


def test_rejects_malformed_or_empty_output(tmp_path, monkeypatch):
    manifest, fields = setup_capture(tmp_path)

    def fake_run(command, **kwargs):
        kwargs["stdout"].write("frame.number,ip.src,eth.src\n1,too,few,columns\n")
        return subprocess.CompletedProcess(command, 0, stderr="")

    monkeypatch.setattr(extractor.subprocess, "run", fake_run)
    report = extractor.extract(manifest, fields_config=fields)
    assert report["status"] == "incomplete"
    assert "malformed row" in report["failures"][0]
