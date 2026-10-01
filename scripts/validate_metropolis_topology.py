"""Cross-check Metropolis address, inventory, router, and switch plans."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, ip_interface, ip_network
from pathlib import Path
import re

import yaml


@dataclass(frozen=True)
class Check:
    name: str
    problems: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.problems


def validate_topology(repository_root: Path) -> list[Check]:
    """Validate the checked-in Metropolis v1 topology definitions."""
    testbed_root = repository_root / "testbeds" / "metropolis"
    dataset_root = testbed_root / "datasets" / "water_treatment_v1"
    address_plan = yaml.safe_load(
        (dataset_root / "topology" / "address-plan.yaml").read_text(encoding="utf-8")
    )
    inventory = yaml.safe_load(
        (dataset_root / "device_instances" / "initial_devices.yaml").read_text(
            encoding="utf-8"
        )
    )
    networks = address_plan["networks"]
    network_by_id = {record["id"]: record for record in networks}
    network_by_cidr = {ip_network(record["cidr"]): record for record in networks}
    checks: list[Check] = []

    problems: list[str] = []
    if len(network_by_id) != len(networks):
        problems.append("network IDs are not unique")
    if len(network_by_cidr) != len(networks):
        problems.append("network CIDRs are not unique")
    parsed_networks = [
        (record, ip_network(record["cidr"], strict=True)) for record in networks
    ]
    for index, (record, subnet) in enumerate(parsed_networks):
        gateway = record.get("gateway")
        if gateway and IPv4Address(gateway) not in subnet:
            problems.append(f"{record['id']}: gateway {gateway} is outside {subnet}")
        for other_record, other_subnet in parsed_networks[index + 1 :]:
            if subnet.overlaps(other_subnet):
                problems.append(
                    f"overlapping CIDRs: {record['id']} {subnet} and "
                    f"{other_record['id']} {other_subnet}"
                )
    checks.append(
        Check(
            "Address plan: unique, valid, non-overlapping IPv4 networks",
            tuple(problems),
        )
    )

    problems = []
    known_sites = {record["site"] for record in networks}
    device_addresses: set[IPv4Address] = set()
    inventory_by_name = {device["name"]: device for device in inventory["devices"]}
    if len(inventory_by_name) != len(inventory["devices"]):
        problems.append("device names are not unique")
    for device in inventory["devices"]:
        name = device["name"]
        subnet = ip_network(device["subnet"], strict=True)
        plan_record = network_by_cidr.get(subnet)
        if plan_record is None:
            problems.append(f"{name}: subnet {subnet} is absent from the address plan")
        elif device["site"] != plan_record["site"]:
            problems.append(
                f"{name}: inventory site {device['site']!r} does not match "
                f"address-plan site {plan_record['site']!r}"
            )
        if device["site"] not in known_sites:
            problems.append(f"{name}: unknown site {device['site']!r}")
        address = ip_interface(device["address"])
        if address.network != subnet:
            problems.append(
                f"{name}: address {address} does not belong to declared subnet {subnet}"
            )
        if address.ip in device_addresses:
            problems.append(f"{name}: duplicate device address {address.ip}")
        device_addresses.add(address.ip)
        expected_gateway = plan_record.get("gateway") if plan_record else None
        if device.get("gateway") != expected_gateway:
            problems.append(
                f"{name}: gateway {device.get('gateway')!r} does not match "
                f"address-plan gateway {expected_gateway!r}"
            )
        environment = device.get("environment", {})
        for key, expected in (
            ("NODE_HOSTNAME", name),
            ("NODE_IP", device["address"]),
            ("NODE_GATEWAY", device.get("gateway")),
        ):
            if environment.get(key) != expected:
                problems.append(f"{name}: environment {key} should be {expected!r}")
    checks.append(
        Check(
            "Device inventory: site, subnet, IP, gateway, and environment",
            tuple(problems),
        )
    )

    router_files = sorted((testbed_root / "router").rglob("*.sh"))
    router_addresses: list[IPv4Interface] = []
    router_vlans: set[tuple[int, IPv4Network]] = set()
    router_problems: list[str] = []
    route_problems: list[str] = []
    address_pattern = re.compile(
        r"set interfaces ethernet \S+(?: vif (\d+))? address '([^']+)'"
    )
    route_pattern = re.compile(r"set protocols static route (\S+) next-hop '([^']+)'")
    for router_file in router_files:
        text = router_file.read_text(encoding="utf-8")
        local_addresses: list[IPv4Interface] = []
        for match in address_pattern.finditer(text):
            vlan_text, address_text = match.groups()
            address = ip_interface(address_text)
            if not isinstance(address, IPv4Interface):
                router_problems.append(
                    f"{router_file.name}: expected IPv4 address {address_text}"
                )
                continue
            local_addresses.append(address)
            router_addresses.append(address)
            plan_record = network_by_cidr.get(address.network)
            if plan_record is None:
                router_problems.append(
                    f"{router_file.name}: interface {address} uses unplanned subnet {address.network}"
                )
            if vlan_text is not None:
                vlan = int(vlan_text)
                router_vlans.add((vlan, address.network))
                if plan_record and plan_record.get("vlan_id") != vlan:
                    router_problems.append(
                        f"{router_file.name}: VLAN {vlan} on {address.network} "
                        f"does not match address-plan VLAN {plan_record.get('vlan_id')}"
                    )
        connected = [address.network for address in local_addresses]
        for route, next_hop in route_pattern.findall(text):
            route_network = ip_network(route, strict=False)
            next_hop_address = IPv4Address(next_hop)
            if not any(next_hop_address in subnet for subnet in connected):
                route_problems.append(
                    f"{router_file.name}: next hop {next_hop} for {route} "
                    "is not on a directly connected subnet"
                )
            if not any(subnet.subnet_of(route_network) for subnet in network_by_cidr):
                route_problems.append(
                    f"{router_file.name}: route {route} contains no planned subnet"
                )

    for record in networks:
        subnet = ip_network(record["cidr"])
        if record.get("kind") == "overlay":
            continue
        if not any(address.network == subnet for address in router_addresses):
            router_problems.append(f"{record['id']}: {subnet} has no router interface")
        gateway = record.get("gateway")
        if gateway and not any(
            address.ip == IPv4Address(gateway) for address in router_addresses
        ):
            router_problems.append(
                f"{record['id']}: gateway {gateway} is absent from router scripts"
            )
        vlan = record.get("vlan_id")
        if vlan and (vlan, subnet) not in router_vlans:
            router_problems.append(
                f"{record['id']}: VLAN {vlan} has no matching router subinterface"
            )
    checks.append(
        Check(
            "Router interfaces and VLAN subinterfaces match address plan",
            tuple(router_problems),
        )
    )
    checks.append(
        Check(
            "Static routes use reachable next hops and planned destinations",
            tuple(route_problems),
        )
    )

    vlan_by_id: dict[int, IPv4Network] = {}
    switch_problems: list[str] = []
    for record in networks:
        if record.get("vlan_id"):
            vlan = record["vlan_id"]
            subnet = ip_network(record["cidr"])
            if vlan in vlan_by_id and vlan_by_id[vlan] != subnet:
                switch_problems.append(f"VLAN {vlan} is assigned to multiple subnets")
            vlan_by_id[vlan] = subnet

    switch_files = sorted((testbed_root / "switch").rglob("*.md"))
    cidr_pattern = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}")
    for switch_file in switch_files:
        text = switch_file.read_text(encoding="utf-8")
        for cidr in cidr_pattern.findall(text):
            subnet = ip_network(cidr, strict=False)
            if subnet not in network_by_cidr:
                switch_problems.append(
                    f"{switch_file.name}: references unplanned subnet {subnet}"
                )
        for line in text.splitlines():
            if "| Trunk |" not in line:
                continue
            columns = [column.strip() for column in line.strip().strip("|").split("|")]
            if len(columns) < 3:
                continue
            vlan_text = columns[2]
            for token in vlan_text.replace(" ", "").split(","):
                if not token:
                    continue
                if "-" in token:
                    start, end = (int(value) for value in token.split("-", maxsplit=1))
                    trunk_vlans = range(start, end + 1)
                else:
                    trunk_vlans = (int(token),)
                for vlan in trunk_vlans:
                    if vlan not in vlan_by_id:
                        switch_problems.append(
                            f"{switch_file.name}: trunk VLAN {vlan} is absent from address plan"
                        )
                    elif (vlan, vlan_by_id[vlan]) not in router_vlans:
                        switch_problems.append(
                            f"{switch_file.name}: trunk VLAN {vlan} has no matching router interface"
                        )
    checks.append(
        Check(
            "Switch subnet references and trunk VLANs match plan",
            tuple(switch_problems),
        )
    )
    checks.append(
        Check(
            "All device service targets resolve to inventoried nodes",
            tuple(
                problem for problem in _validate_device_targets(inventory["devices"])
            ),
        )
    )
    return checks


def render_report(repository_root: Path, checks: list[Check]) -> str:
    """Render the validation results and the source address table as Markdown."""
    dataset_root = (
        repository_root / "testbeds" / "metropolis" / "datasets" / "water_treatment_v1"
    )
    address_plan = yaml.safe_load(
        (dataset_root / "topology" / "address-plan.yaml").read_text(encoding="utf-8")
    )
    passed = sum(check.passed for check in checks)
    lines = [
        "# Metropolis v1 topology validation",
        "",
        f"**Result: {'PASS' if passed == len(checks) else 'FAIL'} ({passed}/{len(checks)} checks passed)**",
        "",
        "Regenerate this report from the repository root with:",
        "",
        "```bash",
        "uv run python scripts/validate_metropolis_topology.py --report testbeds/metropolis/datasets/water_treatment_v1/topology/validation-report.md",
        "```",
        "",
        "## What the checks mean",
        "",
        "| Check | What it compares | Result |",
        "|---|---|---|",
    ]
    for check in checks:
        detail = "; ".join(check.problems) if check.problems else "No mismatches found"
        lines.append(
            f"| {check.name} | {detail} | {'PASS' if check.passed else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "## Address plan cross-reference",
            "",
            "This is the central lookup table. Match each network to the router interface and switch/VLAN entries in the corresponding files.",
            "",
            "| Network ID | Site | Zone | Kind | CIDR | Gateway | VLAN |",
            "|---|---|---|---|---|---|---:|",
        ]
    )
    for record in address_plan["networks"]:
        lines.append(
            "| {id} | {site} | {zone} | {kind} | `{cidr}` | {gateway} | {vlan} |".format(
                id=record["id"],
                site=record["site"],
                zone=record["zone"],
                kind=record["kind"],
                cidr=record["cidr"],
                gateway=record.get("gateway", "—"),
                vlan=record.get("vlan_id", "—"),
            )
        )
    lines.extend(
        [
            "",
            "## Corrections made during this review",
            "",
            "- Matched the reservoir and raw-water device inventory site IDs to the address plan.",
            "- Added VLAN 30 to the OT services network and recorded the reserved reservoir IPsec overlay `10.20.254.16/30`.",
            "- Changed the DMZ switch overview to say untagged, matching the physical router interface and switch port spec; this segment does not use VLAN 40.",
            "",
            "## Scope and limitations",
            "",
            "This validates consistency across the checked-in YAML, VyOS command text, and switch Markdown. A blank VLAN means a standalone untagged subnet. It does not execute VyOS configuration, confirm GNS3 cabling or port numbers, test packet reachability, or validate firewall/IPsec behavior. The two overlay `/30`s are reservations and intentionally have no configured router interfaces yet.",
            "",
        ]
    )
    return "\n".join(lines)


def _validate_device_targets(devices: list[dict]) -> list[str]:
    known_addresses = {ip_interface(device["address"]).ip for device in devices}
    problems = []
    for device in devices:
        for key, target in device.get("environment", {}).items():
            if key.endswith("_HOST") and IPv4Address(target) not in known_addresses:
                problems.append(
                    f"{device['name']}: {key} target {target} is not inventoried"
                )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (defaults to the checkout containing this script)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="optional path to write a Markdown validation report",
    )
    args = parser.parse_args()
    checks = validate_topology(args.repo_root)
    for check in checks:
        print(f"{'PASS' if check.passed else 'FAIL'}  {check.name}")
        for problem in check.problems:
            print(f"      - {problem}")
    passed = sum(check.passed for check in checks)
    print(f"\n{passed}/{len(checks)} checks passed")
    if args.report:
        report = render_report(args.repo_root, checks)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
        print(f"Report written to {args.report}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
