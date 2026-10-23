"""Unit checks for OSPF peer rules and route selection."""

from __future__ import annotations

from ipaddress import ip_address, ip_interface
from pathlib import Path
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
GNS3 = ROOT / "scripts/gns3"
if str(GNS3) not in sys.path:
    sys.path.insert(0, str(GNS3))

from firewall_policy import Router, _path, compile_policy  # noqa: E402


@pytest.fixture
def peer_lab(tmp_path):
    """Two synthetic OSPF peers; no bundled topology names or addresses."""
    topology = tmp_path / "topology"
    topology.mkdir()
    (topology / "firewall-policy.yaml").write_text(
        yaml.safe_dump({"schema_version": "2.0", "testbed_id": "test", "flows": []})
    )
    (topology / "address-plan.yaml").write_text(yaml.safe_dump({"networks": []}))
    (topology / "links.yaml").write_text(
        yaml.safe_dump(
            {
                "links": [
                    {
                        "id": "transit",
                        "endpoints": [
                            {"node": "router-a", "interface": "eth0"},
                            {"node": "router-b", "interface": "eth0"},
                        ],
                    }
                ]
            }
        )
    )
    routers = []
    for name, address in (
        ("router-a", "10.0.0.1/30"),
        ("router-b", "10.0.0.2/30"),
    ):
        script = tmp_path / f"{name}.sh"
        script.write_text(
            f"set interfaces ethernet eth0 address '{address}'\n"
            "set protocols ospf area 0 network '10.0.0.0/30'\n"
            "set protocols ospf passive-interface-exclude 'eth0'\n"
        )
        routers.append({"name": name, "config": str(script)})
    return tmp_path, {"routers": routers}, {"testbed_id": "test"}


def test_firewall_allows_ospf_only_from_declared_peers(peer_lab):
    dataset, infrastructure, inventory = peer_lab
    commands, report = compile_policy(dataset, infrastructure, inventory)
    assert report["baseline_flows"] == []
    assert set(commands) == {"router-a", "router-b"}
    for name, peer_address in (
        ("router-a", "10.0.0.2"),
        ("router-b", "10.0.0.1"),
    ):
        lines = commands[name]
        source_rule = next(
            line.rsplit(" source address", 1)[0]
            for line in lines
            if line.endswith(f"source address '{peer_address}'")
        )
        assert f"{source_rule} protocol '89'" in lines
        assert f"{source_rule} action 'accept'" in lines
        assert f"{source_rule} state new 'enable'" not in lines


def test_firewall_rejects_an_ospf_interface_without_peer(peer_lab):
    dataset, infrastructure, inventory = peer_lab
    peer_script = Path(infrastructure["routers"][1]["config"])
    peer_script.write_text(
        peer_script.read_text().replace(
            "set protocols ospf passive-interface-exclude 'eth0'\n", ""
        )
    )
    with pytest.raises(ValueError, match="no declared OSPF neighbor"):
        compile_policy(dataset, infrastructure, inventory)


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
