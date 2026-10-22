"""Offline checks for router OSPF adjacencies and their local firewall rules."""

from __future__ import annotations

import copy
from ipaddress import ip_address, ip_interface, ip_network
from pathlib import Path
import re
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
GNS3 = ROOT / "scripts/gns3"
if str(GNS3) not in sys.path:
    sys.path.insert(0, str(GNS3))

from firewall_policy import Router, _path, compile_policy  # noqa: E402

DATASET = ROOT / "testbeds/metropolis/datasets/water_treatment_v1"
ADDRESS = re.compile(
    r"^set interfaces ethernet (eth\d+)(?: vif (\d+))? address '([^']+)'$",
    re.M,
)


@pytest.fixture
def documents():
    infrastructure = yaml.safe_load(
        (DATASET / "device_instances/infrastructure.yaml").read_text()
    )
    inventory = yaml.safe_load(
        (DATASET / "device_instances/initial_devices.yaml").read_text()
    )
    return infrastructure, inventory


def test_router_ospf_processes_advertise_all_connected_networks(documents):
    infrastructure, _ = documents
    router_ids = set()
    active = {}
    for router in infrastructure["routers"]:
        text = (ROOT / router["config"]).read_text(encoding="utf-8")
        assert "set protocols static route" not in text
        assert "set protocols ospf passive-interface default" in text
        assert "set protocols ospf log-adjacency-changes" in text
        router_id = re.findall(
            r"^set protocols ospf parameters router-id '([^']+)'$", text, re.M
        )
        assert len(router_id) == 1
        router_ids.add(ip_address(router_id[0]))
        networks = {
            ip_network(value)
            for value in re.findall(
                r"^set protocols ospf area 0 network '([^']+)'$", text, re.M
            )
        }
        interfaces = {
            f"{base}.{vlan}" if vlan else base: ip_interface(address)
            for base, vlan, address in ADDRESS.findall(text)
        }
        assert networks == {address.network for address in interfaces.values()}
        active[router["name"]] = set(
            re.findall(
                r"^set protocols ospf passive-interface-exclude '([^']+)'$",
                text,
                re.M,
            )
        )
        assert active[router["name"]] <= interfaces.keys()
        assert all(
            interfaces[name].network in networks for name in active[router["name"]]
        )
        if router["name"] == "MET-OT-DMZ":
            assert ip_network("10.30.10.0/24") in networks
            assert ip_network("10.99.10.0/24") in networks
            assert active[router["name"]].isdisjoint({"eth2", "eth3"})
        for name in active[router["name"]]:
            if interfaces[name].network.prefixlen == 30:
                assert (
                    f"set interfaces ethernet {name} ip ospf network 'point-to-point'"
                    in text
                )
        assert "set protocols ospf redistribute" not in text
        assert "set protocols ospf default-information originate" not in text
    assert len(router_ids) == len(infrastructure["routers"])
    assert active == {
        "MET-OT-CORE": {"eth0", "eth1", "eth2"},
        "MET-PLANT": {"eth0"},
        "MET-OT-DMZ": {"eth0"},
        "MET-OT-WAN": {"eth0", "eth1"},
        "MET-RAW-WATER": {"eth1"},
        "MET-RESERVOIR": {"eth1"},
    }


def test_firewall_allows_ospf_only_between_declared_router_peers(documents):
    infrastructure, inventory = documents
    commands, _ = compile_policy(DATASET, infrastructure, inventory)
    core = "\n".join(commands["MET-OT-CORE"])
    dmz = "\n".join(commands["MET-OT-DMZ"])
    wan = "\n".join(commands["MET-OT-WAN"])
    assert "set firewall name MET_e0_local rule 1 source address '10.20.254.2'" in core
    assert "set firewall name MET_e0_local rule 1 protocol '89'" in core
    assert "set firewall name MET_local_e0 rule 1 source address '10.20.254.1'" in core
    assert "set firewall name MET_local_e0 rule 1 protocol '89'" in core
    assert "set firewall name MET_e1_local rule 1 source address '172.31.255.11'" in wan
    assert "set firewall name MET_e1_local rule 2 source address '172.31.255.21'" in wan
    assert "set firewall name MET_e0_local rule 1 state new" not in core
    assert "MET_e2_local" not in dmz  # Enterprise interface is not an OSPF peer.
    assert "MET_e3_local" not in dmz  # Attack-test interface is not an OSPF peer.


def test_firewall_rejects_an_ospf_interface_without_peer(documents, tmp_path):
    infrastructure, inventory = documents
    changed = copy.deepcopy(infrastructure)
    core = next(item for item in changed["routers"] if item["name"] == "MET-OT-CORE")
    original = ROOT / core["config"]
    script = tmp_path / "router_ot_core.sh"
    script.write_text(
        original.read_text(encoding="utf-8").replace(
            "set protocols ospf passive-interface-exclude 'eth2'\n", ""
        ),
        encoding="utf-8",
    )
    core["config"] = str(script)
    with pytest.raises(ValueError, match="no declared OSPF neighbor"):
        compile_policy(DATASET, changed, inventory)


def test_firewall_rejects_equal_cost_paths_it_cannot_cover():
    source = ip_interface("10.1.0.1/24")
    destination = ip_interface("10.4.0.1/24")
    routers = {
        "source": Router("source", {"lan": source}, {source.network}, set()),
        "left": Router("left", {}, set(), set()),
        "right": Router("right", {}, set(), set()),
        "destination": Router(
            "destination", {"lan": destination}, {destination.network}, set()
        ),
    }
    adjacency = {
        "source": [("left", "a", "a"), ("right", "b", "b")],
        "left": [("destination", "c", "c")],
        "right": [("destination", "d", "d")],
        "destination": [],
    }
    with pytest.raises(ValueError, match="multiple equal-cost OSPF paths"):
        _path(
            routers,
            adjacency,
            "source",
            ip_address("10.1.0.20"),
            ip_address("10.4.0.20"),
        )
