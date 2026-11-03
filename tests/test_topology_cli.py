"""Checks for the topology command line.

The commands are run in-process, so a failure points at the command rather than
at a subprocess wrapper.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from metropolis.topology.__main__ import main  # noqa: E402


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
