"""Checks for the topology command line.

The commands are run in-process, so a failure points at the command rather than
at a subprocess wrapper.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from metropolis.topology.__main__ import main  # noqa: E402

DATASET = ROOT / "testbeds/metropolis/datasets/water_treatment_v1"


def test_show_prints_a_summary(capsys):
    assert main(["show"]) == 0
    out = capsys.readouterr().out
    assert "topology metropolis" in out
    assert "plant-intake" in out


def test_show_can_read_a_dataset_directory(capsys):
    assert main(["--dataset", str(DATASET), "show"]) == 0
    out = capsys.readouterr().out
    # The same topology as the bundled copy, read from disk instead.
    assert "networks 24" in out
    assert "devices 34" in out
    assert "plant-intake" in out


def test_show_can_write_to_a_file(capsys, tmp_path):
    target = tmp_path / "summary.txt"
    assert main(["show", "--out", str(target)]) == 0
    assert "topology metropolis" in target.read_text(encoding="utf-8")
    assert capsys.readouterr().out.startswith("wrote ")


def test_check_reports_no_problems(capsys):
    assert main(["check"]) == 0
    assert "No contradictions found." in capsys.readouterr().out


def test_check_exits_non_zero_when_something_is_wrong(tmp_path, capsys):
    """Write a dataset whose device sits on the wrong subnet, then check it."""
    documents = {
        "address-plan": (
            "schema_version: '1.0'\ntestbed_id: t\n"
            "networks:\n- id: a\n  cidr: 10.0.0.0/24\n  gateway: 10.0.0.1\n"
        ),
        "links": "schema_version: '1.0'\ntestbed_id: t\nlinks: []\n",
        "infrastructure": (
            "schema_version: '1.0'\ntestbed_id: t\n"
            "routers: []\nswitches: []\nsegments: []\n"
        ),
        "initial-devices": (
            "schema_version: '1.0'\ntestbed_id: t\n"
            "devices:\n- name: D1\n  subnet: 10.0.0.0/24\n  address: 10.0.9.9/24\n"
        ),
    }
    dataset = tmp_path / "broken"
    (dataset / "topology").mkdir(parents=True)
    (dataset / "device_instances").mkdir(parents=True)
    (dataset / "topology/address-plan.yaml").write_text(documents["address-plan"])
    (dataset / "topology/links.yaml").write_text(documents["links"])
    (dataset / "device_instances/infrastructure.yaml").write_text(
        documents["infrastructure"]
    )
    (dataset / "device_instances/initial_devices.yaml").write_text(
        documents["initial-devices"]
    )

    assert main(["--dataset", str(dataset), "check"]) == 1
    assert "is not in" in capsys.readouterr().err


def test_mermaid_render_goes_to_stdout(capsys):
    assert main(["render", "--format", "mermaid"]) == 0
    assert capsys.readouterr().out.startswith("graph TD")


def test_dot_render_goes_to_stdout(capsys):
    assert main(["render", "--format", "dot"]) == 0
    assert capsys.readouterr().out.startswith("digraph topology {")


def test_render_writes_a_file(capsys, tmp_path):
    target = tmp_path / "topology.dot"
    assert main(["render", "--format", "dot", "--out", str(target)]) == 0
    assert target.read_text(encoding="utf-8").startswith("digraph topology {")


def test_a_picture_format_needs_an_output_path(capsys):
    assert main(["render", "--format", "png"]) == 2
    assert "needs --out" in capsys.readouterr().err


def test_export_documents_load_back_identically(capsys, tmp_path):
    target = tmp_path / "exported"
    assert main(["export", "--out", str(target)]) == 0

    original = DATASET
    for relative in (
        "topology/address-plan.yaml",
        "topology/links.yaml",
        "device_instances/initial_devices.yaml",
        "device_instances/infrastructure.yaml",
    ):
        before = yaml.safe_load((original / relative).read_text(encoding="utf-8"))
        after = yaml.safe_load((target / relative).read_text(encoding="utf-8"))
        assert before == after, f"{relative} changed when exported"


def test_a_missing_dataset_is_reported_not_crashed(capsys, tmp_path):
    assert main(["--dataset", str(tmp_path / "nowhere"), "show"]) == 2
    assert "dataset is missing" in capsys.readouterr().err


def test_no_command_is_a_usage_error():
    with pytest.raises(SystemExit):
        main([])


@pytest.mark.parametrize("command", ["show", "check", "render", "export"])
def test_every_command_has_help(command):
    with pytest.raises(SystemExit) as caught:
        main([command, "--help"])
    assert caught.value.code == 0
