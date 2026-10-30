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
        parts = [f"destination address '{destination}'", f"protocol '{protocol}'"]
        if port is not None:
            parts.append(f"destination port '{port}'")
        if all(f"{prefix} {part}" in lines for part in parts):
            return True
    return False


@pytest.fixture
def ipsec_lab(lab):
    """Two renamed VTI endpoints over a shared WAN, not the bundled topology."""
    dataset, infrastructure, inventory = lab
    plan_path = dataset / "topology/address-plan.yaml"
    plan = yaml.safe_load(plan_path.read_text())
    next(item for item in plan["networks"] if item["id"] == "transit").update(
        kind="underlay", cidr="10.0.0.0/24"
    )
    plan["networks"].append(
        {"id": "protected", "kind": "overlay", "cidr": "192.0.2.0/30"}
    )
    plan_path.write_text(yaml.safe_dump(plan))
    links_path = dataset / "topology/links.yaml"
    links = yaml.safe_load(links_path.read_text())
    transit = next(item for item in links["links"] if item["id"] == "transit")
    transit.update(kind="underlay", network_id="transit", cidr="10.0.0.0/24")
    transit["endpoints"][1] = {"node": "wan-switch", "port_role": "hub"}
    links["links"].extend(
        [
            {
                "id": "peer-underlay",
                "kind": "underlay",
                "network_id": "transit",
                "cidr": "10.0.0.0/24",
                "endpoints": [
                    {"node": "router-b", "interface": "eth0"},
                    {"node": "wan-switch", "port_role": "spoke"},
                ],
            },
            {
                "id": "protected-path",
                "kind": "overlay",
                "network_id": "protected",
                "cidr": "192.0.2.0/30",
                "endpoints": [
                    {"node": "router-a", "interface": "vti42"},
                    {"node": "router-b", "interface": "vti9"},
                ],
            },
        ]
    )
    links_path.write_text(yaml.safe_dump(links))
    infrastructure["switches"] = [{"name": "wan-switch"}]
    for number, declaration in enumerate(infrastructure["routers"], 1):
        script = Path(declaration["config"])
        text = script.read_text().replace("10.0.0.0/30", "192.0.2.0/30")
        text = text.replace(f"10.0.0.{number}/30", f"10.0.0.{number}/24")
        vti = "vti42" if number == 1 else "vti9"
        other = 3 - number
        text = text.replace(
            "passive-interface-exclude 'eth0'", f"passive-interface-exclude '{vti}'"
        )
        text += (
            f"set interfaces vti {vti} address '192.0.2.{number}/30'\n"
            f"set interfaces vti {vti} ip ospf network 'point-to-point'\n"
            f"set interfaces vti {vti} ip ospf bfd\n"
            f"set protocols bfd peer '192.0.2.{other}'\n"
            "set vpn ipsec ipsec-interfaces interface 'eth0'\n"
            f"set vpn ipsec site-to-site peer '10.0.0.{other}' local-address '10.0.0.{number}'\n"
            f"set vpn ipsec site-to-site peer '10.0.0.{other}' vti bind '{vti}'\n"
            f"set vpn ipsec site-to-site peer '10.0.0.{other}' authentication mode 'pre-shared-secret'\n"
            f"set vpn ipsec site-to-site peer '10.0.0.{other}' authentication pre-shared-secret \"${{PAIR_PSK}}\"\n"
        )
        script.write_text(text)
    return dataset, infrastructure, inventory


def test_ipsec_policy_separates_encrypted_underlay_from_inner_flows(ipsec_lab):
    commands, report = compile_policy(*ipsec_lab)
    for name, local, peer, vti in (
        ("router-a", "10.0.0.1", "10.0.0.2", "vti42"),
        ("router-b", "10.0.0.2", "10.0.0.1", "vti9"),
    ):
        lines = commands[name]
        assert _has_rule(lines, f"{peer}/32", f"{local}/32", "udp", 500)
        assert _has_rule(lines, f"{peer}/32", f"{local}/32", "udp", 4500)
        assert _has_rule(lines, f"{peer}/32", f"{local}/32", "50", None)
        assert _has_rule(lines, f"{local}/32", f"{peer}/32", "50", None)
        assert _has_rule(lines, "10.1.0.0/24", "10.2.0.0/24", "tcp", 502)
        assert not any("MET_e1_e0 rule" in line for line in lines)
        assert not any("MET_e0_e1 rule" in line for line in lines)
        assert f"set zone-policy zone MET_{vti} interface '{vti}'" in lines
        inner_local, inner_peer = (
            ("192.0.2.1", "192.0.2.2")
            if name == "router-a"
            else ("192.0.2.2", "192.0.2.1")
        )
        assert _has_rule(lines, inner_peer, "0.0.0.0/0", "89", None)
        assert _has_rule(lines, f"{inner_peer}/32", f"{inner_local}/32", "udp", 3784)
        outer = [line for line in lines if "name MET_e0_local " in line]
        assert not any(
            "protocol '89'" in line or "port '3784'" in line for line in outer
        )
        esp_prefix = next(
            line.rsplit(" protocol", 1)[0] for line in outer if "protocol '50'" in line
        )
        assert int(esp_prefix.rsplit(" ", 1)[1]) < 10
        assert f"{esp_prefix} state new 'enable'" not in lines
        assert not any(
            line.startswith(f"{esp_prefix} destination port") for line in lines
        )
    assert report["ipsec_tunnels"][0]["link_id"] == "protected-path"
    assert {
        endpoint["interface"] for endpoint in report["ipsec_tunnels"][0]["endpoints"]
    } == {"vti42", "vti9"}


@pytest.mark.parametrize(
    "corruption,diagnostic",
    [
        ("missing-overlay", "overlay"),
        ("wrong-vti", "overlay"),
        ("missing-underlay", "underlay"),
        ("asymmetric-peer", "reciprocal"),
        ("missing-key-reference", "secret"),
        ("plaintext-routing", "underlay"),
    ],
)
def test_incomplete_or_plaintext_ipsec_paths_fail_closed(
    ipsec_lab, corruption, diagnostic
):
    dataset, infrastructure, _ = ipsec_lab
    path = dataset / "topology/links.yaml"
    links = yaml.safe_load(path.read_text())
    if corruption == "missing-overlay":
        links["links"] = [
            link for link in links["links"] if link["id"] != "protected-path"
        ]
    elif corruption == "wrong-vti":
        overlay = next(
            link for link in links["links"] if link["id"] == "protected-path"
        )
        overlay["endpoints"][1]["interface"] = "vti99"
    elif corruption == "missing-underlay":
        links["links"] = [
            link for link in links["links"] if link["id"] != "peer-underlay"
        ]
    else:
        script = Path(infrastructure["routers"][1]["config"])
        text = script.read_text()
        if corruption == "asymmetric-peer":
            text = text.replace("peer '10.0.0.1'", "peer '10.0.0.99'")
        elif corruption == "missing-key-reference":
            text = "\n".join(
                line
                for line in text.splitlines()
                if "authentication pre-shared-secret" not in line
            )
        else:
            text += "set protocols ospf area 0 network '10.0.0.0/24'\nset protocols ospf passive-interface-exclude 'eth0'\n"
        script.write_text(text)
    path.write_text(yaml.safe_dump(links))
    with pytest.raises(ValueError, match=diagnostic):
        compile_policy(*ipsec_lab)


def test_router_management_cannot_advertise_or_target_wan_underlay(ipsec_lab):
    dataset, _, _ = ipsec_lab
    path = dataset / "topology/firewall-policy.yaml"
    policy = yaml.safe_load(path.read_text())
    policy["flows"].append(
        {
            "id": "wan-management",
            "source": "source",
            "destination": "10.0.0.2/32",
            "protocol": "udp",
            "port": 161,
        }
    )
    path.write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match="not advertised"):
        compile_policy(*ipsec_lab)


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
            "destination": ["10.1.0.1/32", "10.2.0.1/32", "10.0.0.2/32"],
            "protocol": "udp",
            "port": 161,
        }
    )
    path.write_text(yaml.safe_dump(policy))

    commands, report = compile_policy(dataset, infrastructure, inventory)
    assert _has_rule(commands["router-a"], "10.1.0.10/32", "10.1.0.1/32", "udp", 161)
    assert _has_rule(commands["router-a"], "10.1.0.10/32", "10.2.0.1/32", "udp", 161)
    assert _has_rule(commands["router-b"], "10.1.0.10/32", "10.2.0.1/32", "udp", 161)
    assert _has_rule(commands["router-b"], "10.1.0.10/32", "10.0.0.2/32", "udp", 161)
    flows = [flow for flow in report["baseline_flows"] if flow["id"] == "management"]
    assert [flow["routers"] for flow in flows] == [
        ["router-a"],
        ["router-a", "router-b"],
        ["router-a", "router-b"],
    ]


def test_subnet_baseline_allow_and_default_drop(lab):
    dataset, infrastructure, inventory = lab
    policy = yaml.safe_load((dataset / "topology/firewall-policy.yaml").read_text())
    schema = json.loads((ROOT / "schemas/firewall-policy.schema.json").read_text())
    Draft202012Validator(schema).validate(policy)
    commands, report = compile_policy(dataset, infrastructure, inventory)
    assert report["local_subnet_flows"] == ["local:source:vlan-10"]
    assert (
        next(flow for flow in report["baseline_flows"] if flow["id"] == "local")[
            "routers"
        ]
        == []
    )
    assert len(report["baseline_flows"]) > len(policy["flows"])
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
    ],
)
def test_attack_exception_rejects_invalid_protocols_and_ports(value):
    with pytest.raises(ValueError):
        parse_attack_exception(value)


@pytest.mark.parametrize(
    "selector",
    ["vlan-999", "10.0.0.0/30"],
)
def test_invalid_or_non_lan_selector_fails_closed(lab, selector):
    dataset, infrastructure, inventory = lab
    exception = parse_attack_exception(f"attack-lab:{selector}:tcp:502")
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
        metrics_enabled=False,
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
        metrics_enabled=False,
    )
    assert calls == 2
    assert manifest["firewall"]["restored_baseline"] is False
    assert "console unavailable" in manifest["firewall"]["failures"][0]
    assert manifest["status"] == "failed"
