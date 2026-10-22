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
    assert len(report["baseline_flows"]) == 27
    assert report["local_subnet_flows"] == []
    routed = {
        (flow["id"], flow["source"], flow["destination"]): flow["routers"]
        for flow in report["baseline_flows"]
    }
    assert routed[("scada-modbus", "scada", "plant-intake")] == [
        "MET-OT-CORE",
        "MET-PLANT",
    ]
    assert routed[("scada-modbus", "scada", "reservoir")] == [
        "MET-OT-CORE",
        "MET-OT-WAN",
        "MET-RESERVOIR",
    ]
    assert routed[("router-syslog", "router/MET-PLANT", "ot-services")] == [
        "MET-PLANT",
        "MET-OT-CORE",
    ]
    assert routed[("router-syslog", "router/MET-OT-CORE", "ot-services")] == [
        "MET-OT-CORE"
    ]
    assert routed[("router-syslog", "router/MET-RESERVOIR", "ot-services")] == [
        "MET-RESERVOIR",
        "MET-OT-WAN",
        "MET-OT-CORE",
    ]
    assert len(policy["flows"]) < len(report["baseline_flows"])


def _has_rule(lines, source, destination, protocol, port):
    for line in lines:
        if not line.endswith(f"source address '{source}'"):
            continue
        prefix = line.rsplit(" source address", 1)[0]
        if all(
            f"{prefix} {part}" in lines
            for part in (
                f"destination address '{destination}'",
                f"protocol '{protocol}'",
                f"destination port '{port}'",
            )
        ):
            return True
    return False


def test_subnet_baseline_allow_and_default_drop(documents):
    inventory, infrastructure = documents
    commands, _ = compile_policy(DATASET, infrastructure, inventory)
    core = "\n".join(commands["MET-OT-CORE"])
    plant = "\n".join(commands["MET-PLANT"])
    dmz = "\n".join(commands["MET-OT-DMZ"])
    assert "set zone-policy zone MET_e3v20 default-action 'drop'" in core
    assert "set zone-policy zone MET_e3v20 interface 'eth3.20'" in core
    assert _has_rule(core.splitlines(), "10.20.20.0/24", "10.20.10.0/24", "tcp", 502)
    assert _has_rule(core.splitlines(), "10.20.20.0/24", "10.20.11.0/24", "tcp", 502)
    assert _has_rule(plant.splitlines(), "10.20.20.0/24", "10.20.11.0/24", "tcp", 502)
    assert "set firewall name MET_e0_e3v20 rule 20 state established 'enable'" in core
    assert "set zone-policy zone MET_LOCAL local-zone" in dmz
    assert not any(
        "source address '10.99.10.0/24'" in line
        for lines in commands.values()
        for line in lines
    )


def test_attack_exception_is_run_scoped_subnet_rule_and_follows_route(documents):
    inventory, infrastructure = documents
    exception = parse_attack_exception("attack-lab:plant-intake:tcp:502")
    commands, report = compile_policy(DATASET, infrastructure, inventory, [exception])
    assert report["attack_exceptions"][0]["routers"] == [
        "MET-OT-DMZ",
        "MET-OT-CORE",
        "MET-PLANT",
    ]
    for router in report["attack_exceptions"][0]["routers"]:
        lines = commands[router]
        assert _has_rule(lines, "10.99.10.0/24", "10.20.10.0/24", "tcp", 502)
    assert not any(
        "source address '10.99.10.0/24'" in line for line in commands["MET-OT-WAN"]
    )


def test_invalid_attack_source_and_unknown_network_fail_closed(documents):
    inventory, infrastructure = documents
    outside = parse_attack_exception("scada:plant-intake:tcp:502")
    with pytest.raises(ValueError, match="not on attack-lab"):
        compile_policy(DATASET, infrastructure, inventory, [outside])
    unknown = parse_attack_exception("attack-lab:MET-PLC-INTAKE-01:tcp:502")
    with pytest.raises(ValueError, match="unknown firewall network selector"):
        compile_policy(DATASET, infrastructure, inventory, [unknown])
    outside_target = parse_attack_exception("attack-lab:attack-lab:tcp:502")
    with pytest.raises(ValueError, match="attack target is on attack-lab"):
        compile_policy(DATASET, infrastructure, inventory, [outside_target])
    uncabled = parse_attack_exception("attack-lab:plant-dosing:tcp:502")
    changed = copy.deepcopy(infrastructure)
    changed["segments"] = [
        segment
        for segment in changed["segments"]
        if segment["network_id"] != "plant-dosing"
    ]
    with pytest.raises(ValueError, match="no declared link"):
        compile_policy(DATASET, changed, inventory, [uncabled])


def test_baseline_cannot_permit_attack_lab(documents, tmp_path):
    inventory, infrastructure = documents
    topology = tmp_path / "topology"
    topology.mkdir()
    for name in ("address-plan.yaml", "firewall-policy.yaml", "links.yaml"):
        (topology / name).write_bytes((DATASET / "topology" / name).read_bytes())
    policy_path = topology / "firewall-policy.yaml"
    policy = yaml.safe_load(policy_path.read_text())
    policy["flows"].append(
        {
            "id": "unsafe",
            "source": "attack-lab",
            "destination": "plant-intake",
            "protocol": "tcp",
            "port": 502,
        }
    )
    policy_path.write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match="baseline policy cannot permit attack-lab"):
        compile_policy(tmp_path, infrastructure, inventory)


def test_new_hosts_in_existing_vlan_inherit_rule_without_policy_change(documents):
    inventory, infrastructure = documents
    changed = copy.deepcopy(inventory)
    scada = next(item for item in changed["devices"] if item["name"] == "MET-SCADA-01")
    scada["address"] = "10.20.20.99/24"
    original_commands, _ = compile_policy(DATASET, infrastructure, inventory)
    changed_commands, _ = compile_policy(DATASET, infrastructure, changed)
    assert changed_commands == original_commands


def test_vlan_alias_and_cidr_subrange(documents):
    inventory, infrastructure = documents
    exception = parse_attack_exception("10.99.10.0/25:vlan-11:tcp:502")
    commands, report = compile_policy(DATASET, infrastructure, inventory, [exception])
    record = report["attack_exceptions"][0]
    assert record["source_cidr"] == "10.99.10.0/25"
    assert record["destination_cidr"] == "10.20.11.0/24"
    assert _has_rule(
        commands["MET-PLANT"], "10.99.10.0/25", "10.20.11.0/24", "tcp", 502
    )


def test_missing_ospf_advertisement_rejects_policy(documents, tmp_path):
    inventory, infrastructure = documents
    changed = copy.deepcopy(infrastructure)
    core = next(item for item in changed["routers"] if item["name"] == "MET-OT-CORE")
    original = ROOT / core["config"]
    without_network = tmp_path / "router_ot_core.sh"
    without_network.write_text(
        original.read_text().replace(
            "set protocols ospf area 0 network '10.20.20.0/24'\n", ""
        )
    )
    core["config"] = str(without_network)
    with pytest.raises(ValueError, match="OSPF advertised networks do not match"):
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
        "attack-lab:plant-intake:tcp:0",
        "attack-lab:plant-intake:all:502",
        "attack-lab:plant-intake:icmp:502",
        "attack-lab:plant-intake:tcp:nope",
    ],
)
def test_attack_exception_rejects_invalid_protocols_and_ports(value):
    with pytest.raises(ValueError):
        parse_attack_exception(value)


@pytest.mark.parametrize(
    "selector",
    ["10.20.10.0/23", "vlan-999", "2001:db8::/64", "10.20.254.0/30"],
)
def test_invalid_or_non_lan_selector_fails_closed(documents, selector):
    inventory, infrastructure = documents
    exception = (
        parse_attack_exception(f"attack-lab:{selector}:tcp:502")
        if ":" not in selector
        else None
    )
    if exception is None:
        # IPv6 cannot fit the four-part CLI grammar either.
        with pytest.raises(ValueError):
            parse_attack_exception(f"attack-lab:{selector}:tcp:502")
    else:
        with pytest.raises(ValueError):
            compile_policy(DATASET, infrastructure, inventory, [exception])


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
