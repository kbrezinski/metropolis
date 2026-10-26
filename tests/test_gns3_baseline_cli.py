"""Baseline commands select one dataset file and never apply implicitly."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

GNS3 = Path(__file__).resolve().parents[1] / "scripts/gns3"
sys.path.insert(0, str(GNS3))

import configure_baseline as cli  # noqa: E402


@pytest.fixture
def baseline_cli(tmp_path, monkeypatch):
    path = tmp_path / "selected" / "topology" / "resource-baseline.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        'schema_version: "1.0"\ndefaults:\n'
        "  cpus: 1\n  memory_bytes: 536870912\n"
        "  memory_swap_bytes: 536870912\n"
    )
    dataset = path.parent.parent
    inventory = {"devices": [{"name": "new-device", "image": "custom/image:1"}]}
    monkeypatch.setattr(cli, "load_documents", lambda folder: ({}, inventory, {}, {}))
    calls = []

    def connect(args):
        calls.append("connect")
        return SimpleNamespace(
            project_by_name=lambda name: SimpleNamespace(project_id="project")
        )

    monkeypatch.setattr(cli, "connect", connect)
    return dataset, path, calls


def test_plan_resolves_selected_dataset_without_connecting(
    baseline_cli, monkeypatch, capsys
):
    dataset, path, calls = baseline_cli
    monkeypatch.setattr(
        sys, "argv", ["configure_baseline", "--dataset", str(dataset), "--plan"]
    )
    assert cli.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["path"] == str(path.resolve())
    assert report["targets"][0]["name"] == "new-device"
    assert report["targets"][0]["settings"]["memory_bytes"] == 536870912
    assert calls == []


@pytest.mark.parametrize(
    "mode, apply", [([], False), (["--check"], False), (["--apply"], True)]
)
def test_only_explicit_apply_mutates(baseline_cli, monkeypatch, capsys, mode, apply):
    dataset, _, calls = baseline_cli
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "configure_baseline",
            "--dataset",
            str(dataset),
            "--docker-host",
            "remote=ssh://user@compute",
            *mode,
        ],
    )

    def configure(_client, project, baseline, hosts, *, apply):
        calls.append(("configure", apply))
        assert project == "project"
        assert baseline["targets"][0]["image"] == "custom/image:1"
        assert hosts == {"remote": "ssh://user@compute"}
        return {"status": "matched"}

    monkeypatch.setattr(cli, "configure_baseline", configure)
    assert cli.main() == 0
    assert calls == ["connect", ("configure", apply)]
    assert json.loads(capsys.readouterr().out)["status"] == "matched"


def test_drift_has_nonzero_exit_status(baseline_cli, monkeypatch):
    dataset, _, _ = baseline_cli
    monkeypatch.setattr(sys, "argv", ["configure_baseline", "--dataset", str(dataset)])
    monkeypatch.setattr(
        cli, "configure_baseline", lambda *_args, **_kwargs: {"status": "drift"}
    )
    assert cli.main() == 1


def test_missing_config_fails_before_connecting(baseline_cli, monkeypatch):
    dataset, path, calls = baseline_cli
    path.unlink()
    monkeypatch.setattr(
        sys, "argv", ["configure_baseline", "--dataset", str(dataset), "--apply"]
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert calls == []


def test_conflicting_modes_fail_before_connecting(baseline_cli, monkeypatch):
    dataset, _, calls = baseline_cli
    monkeypatch.setattr(
        sys,
        "argv",
        ["configure_baseline", "--dataset", str(dataset), "--check", "--apply"],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert calls == []
