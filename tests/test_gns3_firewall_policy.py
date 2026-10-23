"""Unit checks for firewall flow compilation and VyOS commands."""

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


@pytest.fixture
def lab(tmp_path):
    """Small routed network independent of the shipped Metropolis inventory."""
    topology = tmp_path / "topology"
    topology.mkdir()
    address_plan = {
        "networks": [
            {"id": "source", "kind": "lan", "cidr": "10.1.0.0/24", "vlan_id": 10},
            {"id": "destination", "kind": "lan", "cidr": "10.2.0.0/24", "vlan_id": 20},
            {"id": "attack-lab", "kind": "lan", "cidr": "10.3.0.0/24"},
            {"id": "transit", "kind": "transit", "cidr": "10.0.0.0/30"},
        ]
    }
    policy = {
        "schema_version": "2.0",
        "testbed_id": "test",
        "flows": [
            {
                "id": "routed",
                "source": ["source", "vlan-10"],
                "destination": "destination",
                "protocol": "tcp",
                "port": 502,
            },
            {
                "id": "router-log",
                "source": "router/router-a",
                "destination": "destination",
                "protocol": "udp",
                "port": 514,
            },
            {
                "id": "local",
                "source": "source",
                "destination": "vlan-10",
                "protocol": "tcp",
                "port": 1234,
            },
        ],
    }
    links = {
        "links": [
            {
                "id": "transit",
                "endpoints": [
                    {"node": "router-a", "interface": "eth0"},
                    {"node": "router-b", "interface": "eth0"},
                ],
            },
            {
                "id": "source-link",
                "network_id": "source",
                "endpoints": [
                    {"node": "router-a", "interface": "eth1"},
                    {"node": "source-node", "interface": "eth0"},
                ],
            },
            {
                "id": "destination-link",
                "network_id": "destination",
                "endpoints": [
                    {"node": "router-b", "interface": "eth1"},
                    {"node": "destination-node", "interface": "eth0"},
                ],
            },
            {
                "id": "attack-link",
                "network_id": "attack-lab",
                "endpoints": [
                    {"node": "router-a", "interface": "eth2"},
                    {"node": "attack-node", "interface": "eth0"},
                ],
            },
        ]
    }
    for name, document in (
        ("address-plan.yaml", address_plan),
        ("firewall-policy.yaml", policy),
        ("links.yaml", links),
    ):
        (topology / name).write_text(yaml.safe_dump(document))
    routers = []
    for name, addresses in (
        ("router-a", ("10.0.0.1/30", "10.1.0.1/24", "10.3.0.1/24")),
        ("router-b", ("10.0.0.2/30", "10.2.0.1/24")),
    ):
        script = tmp_path / f"{name}.sh"
        lines = [
            f"set interfaces ethernet eth{index} address '{address}'"
            for index, address in enumerate(addresses)
        ]
        networks = ["10.0.0.0/30"]
        networks.extend(
            ["10.1.0.0/24", "10.3.0.0/24"] if name == "router-a" else ["10.2.0.0/24"]
        )
        lines.extend(
            f"set protocols ospf area 0 network '{network}'" for network in networks
        )
        lines.append("set protocols ospf passive-interface-exclude 'eth0'")
        script.write_text("\n".join(lines) + "\n")
        routers.append({"name": name, "config": str(script)})
    return (
        tmp_path,
        {"routers": routers},
        {
            "testbed_id": "test",
            "devices": [{"name": "source-node", "address": "10.1.0.10/24"}],
        },
    )


def test_policy_schema_and_baseline_paths(lab):
    dataset, infrastructure, inventory = lab
    policy = yaml.safe_load((dataset / "topology/firewall-policy.yaml").read_text())
    schema = json.loads((ROOT / "schemas/firewall-policy.schema.json").read_text())
    Draft202012Validator(schema).validate(policy)
    commands, report = compile_policy(dataset, infrastructure, inventory)
    assert set(commands) == {"router-a", "router-b"}
    assert len(report["baseline_flows"]) == 4
    assert report["local_subnet_flows"] == ["local:source:vlan-10"]
    routed = {
        (flow["id"], flow["source"], flow["destination"]): flow["routers"]
        for flow in report["baseline_flows"]
    }
    assert routed[("routed", "source", "destination")] == ["router-a", "router-b"]
    assert routed[("routed", "vlan-10", "destination")] == [
        "router-a",
        "router-b",
    ]
    assert routed[("router-log", "router/router-a", "destination")] == [
        "router-a",
        "router-b",
    ]
    assert routed[("local", "source", "vlan-10")] == []
    assert len(policy["flows"]) < len(report["baseline_flows"])


def test_scada_to_remote_flow_crosses_three_routers_in_order(lab):
    dataset, infrastructure, inventory = lab
    topology = dataset / "topology"
    address_plan_path = topology / "address-plan.yaml"
    address_plan = yaml.safe_load(address_plan_path.read_text())
    address_plan["networks"].append(
        {"id": "transit-bc", "kind": "transit", "cidr": "10.0.0.4/30"}
    )
    address_plan_path.write_text(yaml.safe_dump(address_plan))

    links_path = topology / "links.yaml"
    links = yaml.safe_load(links_path.read_text())
    links["links"].append(
        {
            "id": "transit-bc",
            "endpoints": [
                {"node": "router-b", "interface": "eth1"},
                {"node": "router-c", "interface": "eth0"},
            ],
        }
    )
    destination_link = next(
        link for link in links["links"] if link["id"] == "destination-link"
    )
    destination_link["endpoints"][0]["node"] = "router-c"
    links_path.write_text(yaml.safe_dump(links))

    Path(infrastructure["routers"][1]["config"]).write_text(
        "set interfaces ethernet eth0 address '10.0.0.2/30'\n"
        "set interfaces ethernet eth1 address '10.0.0.5/30'\n"
        "set protocols ospf area 0 network '10.0.0.0/30'\n"
        "set protocols ospf area 0 network '10.0.0.4/30'\n"
        "set protocols ospf passive-interface-exclude 'eth0'\n"
        "set protocols ospf passive-interface-exclude 'eth1'\n"
    )
    router_c = dataset / "router-c.sh"
    router_c.write_text(
        "set interfaces ethernet eth0 address '10.0.0.6/30'\n"
        "set interfaces ethernet eth1 address '10.2.0.1/24'\n"
        "set protocols ospf area 0 network '10.0.0.4/30'\n"
        "set protocols ospf area 0 network '10.2.0.0/24'\n"
        "set protocols ospf passive-interface-exclude 'eth0'\n"
    )
    infrastructure["routers"].append({"name": "router-c", "config": str(router_c)})

    commands, report = compile_policy(dataset, infrastructure, inventory)
    scada_to_remote = next(
        flow
        for flow in report["baseline_flows"]
        if (flow["id"], flow["source"], flow["destination"])
        == ("routed", "source", "destination")
    )
    assert scada_to_remote["routers"] == ["router-a", "router-b", "router-c"]
    assert all(
        _has_rule(commands[name], "10.1.0.0/24", "10.2.0.0/24", "tcp", 502)
        for name in scada_to_remote["routers"]
    )


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


def test_bfd_rules_follow_declared_ospf_peers(lab):
    dataset, infrastructure, inventory = lab
    first, second = (Path(item["config"]) for item in infrastructure["routers"])
    with first.open("a", encoding="utf-8") as script:
        script.write("set interfaces ethernet eth0 ip ospf bfd\n")
        script.write("set protocols bfd peer '10.0.0.2'\n")
    with second.open("a", encoding="utf-8") as script:
        script.write("set interfaces ethernet eth0 ip ospf bfd\n")
        script.write("set protocols bfd peer '10.0.0.1'\n")

    commands, _ = compile_policy(dataset, infrastructure, inventory)
    assert _has_rule(commands["router-a"], "10.0.0.2/32", "10.0.0.1/32", "udp", 3784)
    assert _has_rule(commands["router-b"], "10.0.0.1/32", "10.0.0.2/32", "udp", 3784)

    second.write_text(
        second.read_text().replace("set protocols bfd peer '10.0.0.1'\n", "")
    )
    with pytest.raises(ValueError, match="BFD peer router-b is not configured"):
        compile_policy(dataset, infrastructure, inventory)


def test_router_local_snmp_flow_uses_exact_interface_address(lab):
    dataset, infrastructure, inventory = lab
    path = dataset / "topology/firewall-policy.yaml"
    policy = yaml.safe_load(path.read_text())
    policy["flows"].append(
        {
            "id": "management",
            "source": "10.1.0.10/32",
            "destination": ["10.1.0.1/32", "10.2.0.1/32"],
            "protocol": "udp",
            "port": 161,
        }
    )
    path.write_text(yaml.safe_dump(policy))

    commands, report = compile_policy(dataset, infrastructure, inventory)
    assert _has_rule(commands["router-a"], "10.1.0.10/32", "10.1.0.1/32", "udp", 161)
    assert _has_rule(commands["router-a"], "10.1.0.10/32", "10.2.0.1/32", "udp", 161)
    assert _has_rule(commands["router-b"], "10.1.0.10/32", "10.2.0.1/32", "udp", 161)
    flows = [flow for flow in report["baseline_flows"] if flow["id"] == "management"]
    assert [flow["routers"] for flow in flows] == [
        ["router-a"],
        ["router-a", "router-b"],
    ]


def test_subnet_baseline_allow_and_default_drop(lab):
    dataset, infrastructure, inventory = lab
    commands, _ = compile_policy(dataset, infrastructure, inventory)
    for router in ("router-a", "router-b"):
        lines = commands[router]
        assert _has_rule(lines, "10.1.0.0/24", "10.2.0.0/24", "tcp", 502)
        assert "set zone-policy zone MET_LOCAL local-zone" in lines
        assert any(line.endswith("default-action 'drop'") for line in lines)
        assert any(
            line.endswith("rule 20 state established 'enable'") for line in lines
        )
    assert not any(
        "source address '10.3.0.0/24'" in line
        for lines in commands.values()
        for line in lines
    )


def test_attack_exception_is_run_scoped_subnet_rule_and_follows_route(lab):
    dataset, infrastructure, inventory = lab
    exception = parse_attack_exception("attack-lab:destination:tcp:502")
    commands, report = compile_policy(dataset, infrastructure, inventory, [exception])
    assert report["attack_exceptions"][0]["routers"] == ["router-a", "router-b"]
    for router in report["attack_exceptions"][0]["routers"]:
        lines = commands[router]
        assert _has_rule(lines, "10.3.0.0/24", "10.2.0.0/24", "tcp", 502)
    baseline, _ = compile_policy(dataset, infrastructure, inventory)
    assert not any(
        "source address '10.3.0.0/24'" in line
        for lines in baseline.values()
        for line in lines
    )


def test_invalid_attack_source_and_unknown_network_fail_closed(lab):
    dataset, infrastructure, inventory = lab
    outside = parse_attack_exception("source:destination:tcp:502")
    with pytest.raises(ValueError, match="not on attack-lab"):
        compile_policy(dataset, infrastructure, inventory, [outside])
    unknown = parse_attack_exception("attack-lab:device-name:tcp:502")
    with pytest.raises(ValueError, match="unknown firewall network selector"):
        compile_policy(dataset, infrastructure, inventory, [unknown])
    outside_target = parse_attack_exception("attack-lab:attack-lab:tcp:502")
    with pytest.raises(ValueError, match="attack target is on attack-lab"):
        compile_policy(dataset, infrastructure, inventory, [outside_target])
    uncabled = parse_attack_exception("attack-lab:destination:tcp:502")
    link_path = dataset / "topology/links.yaml"
    links = yaml.safe_load(link_path.read_text())
    links["links"] = [
        link for link in links["links"] if link["id"] != "destination-link"
    ]
    link_path.write_text(yaml.safe_dump(links))
    with pytest.raises(ValueError, match="no declared link"):
        compile_policy(dataset, infrastructure, inventory, [uncabled])


def test_baseline_cannot_permit_attack_lab(lab):
    dataset, infrastructure, inventory = lab
    topology = dataset / "topology"
    policy_path = topology / "firewall-policy.yaml"
    policy = yaml.safe_load(policy_path.read_text())
    policy["flows"].append(
        {
            "id": "unsafe",
            "source": "attack-lab",
            "destination": "destination",
            "protocol": "tcp",
            "port": 502,
        }
    )
    policy_path.write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match="baseline policy cannot permit attack-lab"):
        compile_policy(dataset, infrastructure, inventory)


def test_new_hosts_in_existing_vlan_inherit_rule_without_policy_change(lab):
    dataset, infrastructure, inventory = lab
    changed = copy.deepcopy(inventory)
    changed["devices"].append({"name": "another-source", "address": "10.1.0.99/24"})
    original_commands, _ = compile_policy(dataset, infrastructure, inventory)
    changed_commands, _ = compile_policy(dataset, infrastructure, changed)
    assert changed_commands == original_commands


def test_vlan_alias_and_cidr_subrange(lab):
    dataset, infrastructure, inventory = lab
    exception = parse_attack_exception("10.3.0.0/25:vlan-20:tcp:502")
    commands, report = compile_policy(dataset, infrastructure, inventory, [exception])
    record = report["attack_exceptions"][0]
    assert record["source_cidr"] == "10.3.0.0/25"
    assert record["destination_cidr"] == "10.2.0.0/24"
    assert _has_rule(commands["router-b"], "10.3.0.0/25", "10.2.0.0/24", "tcp", 502)


def test_missing_ospf_advertisement_rejects_policy(lab):
    dataset, infrastructure, inventory = lab
    script = Path(infrastructure["routers"][0]["config"])
    script.write_text(
        script.read_text().replace(
            "set protocols ospf area 0 network '10.1.0.0/24'\n", ""
        )
    )
    with pytest.raises(ValueError, match="OSPF advertised networks do not match"):
        compile_policy(dataset, infrastructure, inventory)


def test_missing_declared_router_cable_rejects_flow(lab):
    dataset, infrastructure, inventory = lab
    topology = dataset / "topology"
    links = yaml.safe_load((topology / "links.yaml").read_text())
    links["links"] = [link for link in links["links"] if link["id"] != "transit"]
    (topology / "links.yaml").write_text(yaml.safe_dump(links))
    with pytest.raises(ValueError, match="no declared cable"):
        compile_policy(dataset, infrastructure, inventory)


@pytest.mark.parametrize(
    "value",
    [
        "attack-lab:destination:tcp:0",
        "attack-lab:destination:all:502",
        "attack-lab:destination:icmp:502",
        "attack-lab:destination:tcp:nope",
    ],
)
def test_attack_exception_rejects_invalid_protocols_and_ports(value):
    with pytest.raises(ValueError):
        parse_attack_exception(value)


@pytest.mark.parametrize(
    "selector",
    ["10.2.0.0/23", "vlan-999", "2001:db8::/64", "10.0.0.0/30"],
)
def test_invalid_or_non_lan_selector_fails_closed(lab, selector):
    dataset, infrastructure, inventory = lab
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
            compile_policy(dataset, infrastructure, inventory, [exception])


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
            return [RouterResult(node="router-a", error="partial apply")]
        return [RouterResult(node="router-a", applied=True)]

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
        {"routers": [{"name": "router-a", "config": "unused.sh"}]},
        {"links": []},
        tmp_path,
        "firewall_run",
        0.01,
        {},
        configure_routers=True,
        firewall_commands={"router-a": ["exception"]},
        baseline_firewall_commands={"router-a": ["baseline"]},
        firewall_exceptions=[{"source": "attack", "destination": "target"}],
        collector_factory=Collector,
    )
    assert calls == [
        {"router-a": ["exception"]},
        {"router-a": ["baseline"]},
    ]
    assert manifest["firewall"]["restored_baseline"] is True
    assert manifest["firewall"]["failures"] == ["router-a: partial apply"]
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
        return [RouterResult(node="router-a", applied=True)]

    def stop_before_capture(*_args):
        raise RuntimeError("capture setup failed")

    monkeypatch.setattr(experiment, "configure_project_routers", configure)
    monkeypatch.setattr(experiment, "resolve_links", stop_before_capture)
    manifest = experiment.run_experiment(
        object(),
        SimpleNamespace(project_id="p1", status="opened"),
        SimpleNamespace(nodes=[]),
        {"devices": []},
        {"routers": [{"name": "router-a", "config": "unused.sh"}]},
        {"links": []},
        tmp_path,
        "restore_failed",
        0.01,
        {},
        configure_routers=True,
        firewall_commands={"router-a": ["exception"]},
        baseline_firewall_commands={"router-a": ["baseline"]},
        firewall_exceptions=[{"source": "attack", "destination": "target"}],
        collector_factory=Collector,
    )
    assert calls == 2
    assert manifest["firewall"]["restored_baseline"] is False
    assert "console unavailable" in manifest["firewall"]["failures"][0]
    assert manifest["status"] == "failed"
