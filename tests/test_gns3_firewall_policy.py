"""Offline flow, route, and VyOS command checks for the Metropolis firewall."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
GNS3 = ROOT / "scripts/gns3"
if str(GNS3) not in sys.path:
    sys.path.insert(0, str(GNS3))

from firewall_policy import (  # noqa: E402
    compile_policy,
    managed_deletes,
    parse_attack_exception,
)
import experiment  # noqa: E402
from router_config import RouterResult  # noqa: E402

DATASET = ROOT / "testbeds/metropolis/datasets/water_treatment_v1"


@pytest.fixture
def documents():
    inventory = yaml.safe_load(
        (DATASET / "device_instances/initial_devices.yaml").read_text()
    )
    infrastructure = yaml.safe_load(
        (DATASET / "device_instances/infrastructure.yaml").read_text()
    )
    return inventory, infrastructure


def test_policy_schema_and_all_baseline_paths(documents):
    inventory, infrastructure = documents
    policy = yaml.safe_load((DATASET / "topology/firewall-policy.yaml").read_text())
    schema = json.loads((ROOT / "schemas/firewall-policy.schema.json").read_text())
    Draft202012Validator(schema).validate(policy)
    commands, report = compile_policy(DATASET, infrastructure, inventory)
    assert len(commands) == 6
    assert len(report["baseline_flows"]) == len(policy["flows"])
    assert report["local_subnet_flows"] == []
    routed = {flow["id"]: flow["routers"] for flow in report["baseline_flows"]}
    assert routed["scada-intake-modbus"] == ["MET-OT-CORE", "MET-PLANT"]
    assert routed["scada-reservoir-modbus"] == [
        "MET-OT-CORE",
        "MET-OT-WAN",
        "MET-RESERVOIR",
    ]
    assert routed["reservoir-syslog"] == ["MET-RESERVOIR", "MET-OT-WAN", "MET-OT-CORE"]


def test_exact_baseline_allow_and_default_drop(documents):
    inventory, infrastructure = documents
    commands, _ = compile_policy(DATASET, infrastructure, inventory)
    core = "\n".join(commands["MET-OT-CORE"])
    plant = "\n".join(commands["MET-PLANT"])
    dmz = "\n".join(commands["MET-OT-DMZ"])
    assert "set zone-policy zone MET_e3v20 default-action 'drop'" in core
    assert "set zone-policy zone MET_e3v20 interface 'eth3.20'" in core
    assert (
        "set firewall name MET_e3v20_e0 rule 100 source address '10.20.20.10'" in core
    )
    assert (
        "set firewall name MET_e3v20_e0 rule 100 destination address '10.20.10.10'"
        in core
    )
    assert "set firewall name MET_e3v20_e0 rule 100 destination port '502'" in core
    assert "set firewall name MET_e0_e1v10 rule 100 destination port '502'" in plant
    assert "set firewall name MET_e0_e3v20 rule 20 state established 'enable'" in core
    assert "set zone-policy zone MET_LOCAL local-zone" in dmz
    assert not any(
        "source address '10.99.10." in line
        for lines in commands.values()
        for line in lines
    )


def test_attack_exception_is_narrow_and_follows_the_route(documents):
    inventory, infrastructure = documents
    exception = parse_attack_exception("MET-MERLIN-AGENT-01:MET-PLC-INTAKE-01:tcp:502")
    commands, report = compile_policy(DATASET, infrastructure, inventory, [exception])
    assert report["attack_exceptions"][0]["routers"] == [
        "MET-OT-DMZ",
        "MET-OT-CORE",
        "MET-PLANT",
    ]
    for router in report["attack_exceptions"][0]["routers"]:
        lines = commands[router]
        assert any("source address '10.99.10.21'" in line for line in lines)
        assert any("destination address '10.20.10.10'" in line for line in lines)
    assert not any(
        "source address '10.99.10.21'" in line for line in commands["MET-OT-WAN"]
    )


def test_invalid_attack_source_and_missing_device_fail_closed(documents):
    inventory, infrastructure = documents
    outside = parse_attack_exception("MET-SCADA-01:MET-PLC-INTAKE-01:tcp:502")
    with pytest.raises(ValueError, match="not on attack-lab"):
        compile_policy(DATASET, infrastructure, inventory, [outside])
    changed = copy.deepcopy(inventory)
    changed["devices"] = [
        device for device in changed["devices"] if device["name"] != "MET-PLC-INTAKE-01"
    ]
    with pytest.raises(ValueError, match="not inventoried"):
        compile_policy(DATASET, infrastructure, changed)
    uncabled = parse_attack_exception("MET-MIRAI-BOT-01:MET-PLC-INTAKE-01:tcp:502")
    with pytest.raises(ValueError, match="no declared link"):
        compile_policy(DATASET, infrastructure, inventory, [uncabled])


def test_inventory_address_change_updates_rules(documents):
    inventory, infrastructure = documents
    changed = copy.deepcopy(inventory)
    scada = next(item for item in changed["devices"] if item["name"] == "MET-SCADA-01")
    scada["address"] = "10.20.20.99/24"
    commands, _ = compile_policy(DATASET, infrastructure, changed)
    core = "\n".join(commands["MET-OT-CORE"])
    assert "source address '10.20.20.99'" in core
    assert "source address '10.20.20.10'" not in core


def test_missing_return_route_rejects_flow(documents, tmp_path):
    inventory, infrastructure = documents
    changed = copy.deepcopy(infrastructure)
    wan = next(item for item in changed["routers"] if item["name"] == "MET-OT-WAN")
    original = ROOT / wan["config"]
    without_route = tmp_path / "router_ot_wan.sh"
    without_route.write_text(
        original.read_text().replace(
            "set protocols static route 10.20.20.0/24 next-hop '10.20.254.9'\n", ""
        )
    )
    wan["config"] = str(without_route)
    with pytest.raises(ValueError, match="scada-reservoir-modbus: return path"):
        compile_policy(DATASET, changed, inventory)


def test_missing_declared_router_cable_rejects_flow(documents, tmp_path):
    inventory, infrastructure = documents
    topology = tmp_path / "topology"
    topology.mkdir()
    for name in ("address-plan.yaml", "firewall-policy.yaml", "links.yaml"):
        (topology / name).write_bytes((DATASET / "topology" / name).read_bytes())
    links = yaml.safe_load((topology / "links.yaml").read_text())
    links["links"] = [
        link for link in links["links"] if link["id"] != "link-core-plant"
    ]
    (topology / "links.yaml").write_text(yaml.safe_dump(links))
    with pytest.raises(ValueError, match="no declared cable"):
        compile_policy(tmp_path, infrastructure, inventory)


@pytest.mark.parametrize(
    "value",
    [
        "MET-MERLIN-AGENT-01:MET-PLC-INTAKE-01:tcp:0",
        "MET-MERLIN-AGENT-01:MET-PLC-INTAKE-01:all:502",
        "MET-MERLIN-AGENT-01:MET-PLC-INTAKE-01:icmp:502",
        "MET-MERLIN-AGENT-01:MET-PLC-INTAKE-01:tcp:nope",
    ],
)
def test_attack_exception_rejects_broad_or_invalid_protocols(value):
    with pytest.raises(ValueError):
        parse_attack_exception(value)


def test_reapply_deletes_only_metropolis_owned_config():
    existing = "\n".join(
        [
            "set zone-policy zone MET_e0 interface eth0",
            "set zone-policy zone USER zone interface eth9",
            "set firewall name MET_e0_e1 default-action drop",
            "set firewall name USER_POLICY default-action accept",
        ]
    )
    assert managed_deletes(existing) == [
        "delete zone-policy zone MET_e0",
        "delete firewall name MET_e0_e1",
    ]


@pytest.mark.parametrize("interrupted", [False, True])
def test_experiment_restores_baseline_after_failure_or_interrupt(
    tmp_path, monkeypatch, interrupted
):
    calls = []

    class Collector:
        def __init__(self, _client, _project, run_dir, _run_id, _hosts, **_kwargs):
            self.run_dir = run_dir
            run_dir.mkdir(parents=True)
            self.manifest = {"status": "running", "failures": []}

        def run(self, _duration, *, stop_event):
            stop_event.wait(5)
            self.manifest["status"] = "complete"

        def _write_manifest(self):
            (self.run_dir / "manifest.json").write_text(json.dumps(self.manifest))

    def configure(_client, _project_id, _scripts, **kwargs):
        calls.append(kwargs["firewall_commands"])
        if len(calls) == 1:
            return [RouterResult(node="MET-PLANT", error="partial apply")]
        return [RouterResult(node="MET-PLANT", applied=True)]

    def fail_capture_setup(*_args):
        if interrupted:
            raise KeyboardInterrupt
        raise RuntimeError("capture setup failed")

    monkeypatch.setattr(experiment, "configure_project_routers", configure)
    monkeypatch.setattr(experiment, "resolve_links", fail_capture_setup)
    manifest = experiment.run_experiment(
        object(),
        SimpleNamespace(project_id="p1", status="opened"),
        SimpleNamespace(nodes=[]),
        {"devices": []},
        {"routers": [{"name": "MET-PLANT", "config": "unused.sh"}]},
        {"links": []},
        tmp_path,
        "firewall_run",
        0.01,
        {},
        configure_routers=True,
        firewall_commands={"MET-PLANT": ["exception"]},
        baseline_firewall_commands={"MET-PLANT": ["baseline"]},
        firewall_exceptions=[{"source": "attack", "destination": "target"}],
        collector_factory=Collector,
    )
    assert calls == [
        {"MET-PLANT": ["exception"]},
        {"MET-PLANT": ["baseline"]},
    ]
    assert manifest["firewall"]["restored_baseline"] is True
    assert manifest["firewall"]["failures"] == ["MET-PLANT: partial apply"]
    assert manifest["status"] == ("interrupted" if interrupted else "failed")
    timeline = (tmp_path / "runs/firewall_run/timeline.jsonl").read_text()
    assert "firewall_baseline_restored" in timeline


def test_experiment_reports_failed_baseline_restoration(tmp_path, monkeypatch):
    class Collector:
        def __init__(self, _client, _project, run_dir, _run_id, _hosts, **_kwargs):
            self.run_dir = run_dir
            run_dir.mkdir(parents=True)
            self.manifest = {"status": "running", "failures": []}

        def run(self, _duration, *, stop_event):
            stop_event.wait(5)
            self.manifest["status"] = "complete"

        def _write_manifest(self):
            (self.run_dir / "manifest.json").write_text(json.dumps(self.manifest))

    calls = 0

    def configure(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("console unavailable")
        return [RouterResult(node="MET-PLANT", applied=True)]

    def stop_before_capture(*_args):
        raise RuntimeError("capture setup failed")

    monkeypatch.setattr(experiment, "configure_project_routers", configure)
    monkeypatch.setattr(experiment, "resolve_links", stop_before_capture)
    manifest = experiment.run_experiment(
        object(),
        SimpleNamespace(project_id="p1", status="opened"),
        SimpleNamespace(nodes=[]),
        {"devices": []},
        {"routers": [{"name": "MET-PLANT", "config": "unused.sh"}]},
        {"links": []},
        tmp_path,
        "restore_failed",
        0.01,
        {},
        configure_routers=True,
        firewall_commands={"MET-PLANT": ["exception"]},
        baseline_firewall_commands={"MET-PLANT": ["baseline"]},
        firewall_exceptions=[{"source": "attack", "destination": "target"}],
        collector_factory=Collector,
    )
    assert calls == 2
    assert manifest["firewall"]["restored_baseline"] is False
    assert "console unavailable" in manifest["firewall"]["failures"][0]
    assert manifest["status"] == "failed"
