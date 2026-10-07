"""Cross-check Metropolis address, inventory, link, router, and switch plans."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, ip_interface, ip_network
import json
from pathlib import Path
import re

import yaml

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - the dev dependency group provides it
    raise SystemExit(
        "jsonschema is required to validate the topology schemas; "
        "run 'uv sync --group dev'"
    ) from None

# "GNS3 interface map:" entries read "ethN -> neighbour, detail".
INTERFACE_MAP = re.compile(r"^#\s+(eth\d+)\s*->\s*(.+?)\s*$")
# A switch specification lists its ports as Markdown table rows.
SWITCH_PORT_ROW = re.compile(r"^\|\s*`?([a-z][a-z0-9-]*)`?\s*\|", re.M)


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
    link_plan = yaml.safe_load(
        (dataset_root / "topology" / "links.yaml").read_text(encoding="utf-8")
    )
    infrastructure = yaml.safe_load(
        (dataset_root / "device_instances" / "infrastructure.yaml").read_text(
            encoding="utf-8"
        )
    )
    infrastructure_names = {
        node["name"]
        for node in infrastructure.get("routers", [])
        + infrastructure.get("switches", [])
        + infrastructure.get("segments", [])
    }
    networks = address_plan["networks"]
    testbed_id = address_plan["testbed_id"]
    network_by_id = {record["id"]: record for record in networks}
    network_by_cidr = {ip_network(record["cidr"]): record for record in networks}
    checks: list[Check] = []

    checks.append(
        Check(
            "Documents conform to their JSON schemas",
            tuple(
                _schema_problems(
                    repository_root,
                    [
                        ("address-plan.schema.json", address_plan),
                        ("device-inventory.schema.json", inventory),
                        ("infrastructure.schema.json", infrastructure),
                        ("link-plan.schema.json", link_plan),
                    ],
                )
            ),
        )
    )
    checks.append(
        Check(
            "Topology documents describe the same testbed",
            tuple(
                f"{name} declares testbed_id {value!r}"
                for name, value in (
                    ("address-plan.yaml", address_plan.get("testbed_id")),
                    ("initial_devices.yaml", inventory.get("testbed_id")),
                    ("infrastructure.yaml", infrastructure.get("testbed_id")),
                    ("links.yaml", link_plan.get("testbed_id")),
                )
                if value != testbed_id
            ),
        )
    )

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
    checks.append(
        Check(
            "Link plan matches the inventory, address plan, and router interfaces",
            tuple(
                _validate_links(
                    link_plan,
                    inventory["devices"],
                    network_by_id,
                    network_by_cidr,
                    router_files,
                    infrastructure_names,
                )
            ),
        )
    )
    checks.append(
        Check(
            "Routers and switches match their scripts, specs, and the link plan",
            tuple(
                _validate_infrastructure(
                    infrastructure,
                    inventory["devices"],
                    network_by_cidr,
                    link_plan,
                    repository_root,
                )
            ),
        )
    )
    return checks


def _router_interfaces(router_file: Path) -> tuple[set[str], set[tuple[int, int]]]:
    """Interface numbers and (vif, vlan) pairs a router script configures."""
    text = router_file.read_text(encoding="utf-8")
    numbers: set[str] = set()
    subinterfaces: set[tuple[int, int]] = set()
    for match in re.finditer(r"^set interfaces ethernet (eth(\d+))\b(.*)$", text, re.M):
        prefix, number, rest = match.groups()
        numbers.add(number)
        if subinterface := re.search(r"vif (\d+)", rest):
            subinterfaces.add((int(number), int(subinterface.group(1))))
    return numbers, subinterfaces


def _router_addresses(router_file: Path) -> dict[str, str]:
    """Address configured on each router interface, keyed by interface number."""
    addresses: dict[str, str] = {}
    for match in re.finditer(
        r"^set interfaces ethernet eth(\d+) address '([^']+)'",
        router_file.read_text(encoding="utf-8"),
        re.M,
    ):
        number, address = match.groups()
        addresses[number] = address
    return addresses


def _documented_router_interfaces(router_file: Path) -> set[str]:
    """Interface numbers named by the script's GNS3 interface map comment."""
    numbers: set[str] = set()
    in_map = False
    for line in router_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("# GNS3 interface map:"):
            in_map = True
            continue
        if in_map:
            if not line.startswith("#"):
                break
            if match := INTERFACE_MAP.match(line):
                numbers.add(match.group(1).removeprefix("eth"))
    return numbers


def _switch_spec_ports(spec: Path) -> set[str]:
    """Port roles a switch specification lists in its table."""
    return {
        match.group(1)
        for match in SWITCH_PORT_ROW.finditer(spec.read_text(encoding="utf-8"))
        # Skip the table header and alignment row.
        if match.group(1) not in {"port-role", "---"}
    }


def _validate_infrastructure(
    infrastructure: dict,
    devices: list[dict],
    network_by_cidr: dict,
    link_plan: dict,
    repository_root: Path,
) -> list[str]:
    """Check routers and switches against their sources and the link plan."""
    problems: list[str] = []
    routers = infrastructure.get("routers", [])
    switches = infrastructure.get("switches", [])
    segments = infrastructure.get("segments", [])

    device_names = {device["name"] for device in devices}
    infra_names = [node["name"] for node in routers + switches + segments]
    if len(set(infra_names)) != len(infra_names):
        problems.append("infrastructure node names are not unique")
    if clash := sorted(set(infra_names) & device_names):
        problems.append(f"names appear in both inventories: {clash}")

    for segment in segments:
        network_id = segment.get("network_id")
        if network_id and network_id not in {
            record["id"] for record in network_by_cidr.values()
        }:
            problems.append(
                f"{segment['name']}: network {network_id} is not in the address plan"
            )

    for router in routers:
        name = router["name"]
        config = Path(router["config"])
        if not (repository_root / config).is_file():
            problems.append(f"{name}: config {config} does not exist")
            continue
        config_path = repository_root / config
        configured, _ = _router_interfaces(config_path)
        addresses = _router_addresses(config_path)
        declared = router.get("interfaces", [])
        if len({item["name"] for item in declared}) != len(declared):
            problems.append(f"{name}: duplicate interface declaration")
        for interface in declared:
            number = interface["name"].removeprefix("eth")
            if number not in configured:
                problems.append(
                    f"{name}: {interface['name']} is not configured by {config.name}"
                )
            cidr = interface.get("cidr")
            if cidr and ip_network(cidr, strict=False) not in network_by_cidr:
                problems.append(f"{name}: {interface['name']} {cidr} is unplanned")
            # The script is the authority for the address it configures.
            if cidr and number in addresses and addresses[number] != cidr:
                problems.append(
                    f"{name}: {interface['name']} declares {cidr} but "
                    f"{config.name} configures {addresses[number]}"
                )
        if undeclared := configured - {i["name"].removeprefix("eth") for i in declared}:
            problems.append(
                f"{name}: {config.name} configures undeclared interface(s) "
                f"{sorted(undeclared)}"
            )

    switch_roles: dict[str, set[str]] = {}
    for switch in switches:
        name = switch["name"]
        spec = Path(switch["spec"])
        if not (repository_root / spec).is_file():
            problems.append(f"{name}: spec {spec} does not exist")
            continue
        documented = _switch_spec_ports(repository_root / spec)
        declared = set(switch.get("attachment_points", []))
        switch_roles[name] = documented
        if undeclared := documented - declared:
            problems.append(
                f"{name}: spec lists undeclared port role(s) {sorted(undeclared)}"
            )
        if overspecified := declared - documented:
            problems.append(
                f"{name}: declares port role(s) {sorted(overspecified)} absent from spec"
            )

    # Every switch a link references must be in the infrastructure inventory,
    # and each port role it names must exist on that switch. Segments accept a
    # link but declare no port roles of their own.
    segment_names = {segment["name"] for segment in segments}
    for link in link_plan["links"]:
        for endpoint in link["endpoints"]:
            node = endpoint["node"]
            role = endpoint.get("port_role")
            if node in segment_names:
                continue
            if node not in switch_roles:
                if role:
                    problems.append(
                        f"{link['id']}: {node} is not an inventoried switch"
                    )
                continue
            if role and role not in switch_roles[node]:
                problems.append(f"{link['id']}: {node} has no port role {role!r}")

    linked = {
        endpoint["node"]
        for link in link_plan["links"]
        for endpoint in link["endpoints"]
    }
    for name in infra_names:
        if name not in linked:
            problems.append(
                f"{name}: infrastructure node has no links in the link plan"
            )

    return problems


def _schema_problems(
    repository_root: Path, documents: list[tuple[Path, dict]]
) -> list[str]:
    """Validate each loaded document against its JSON schema."""
    problems: list[str] = []
    for schema_name, document in documents:
        schema_path = repository_root / "schemas" / schema_name
        if not schema_path.is_file():
            problems.append(f"{schema_path.name} is missing")
            continue
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        errors = sorted(
            validator.iter_errors(document), key=lambda item: list(item.path)
        )
        for error in errors:
            problems.append(f"{error.json_path}: {error.message}")
    return problems


def _validate_links(
    link_plan: dict,
    devices: list[dict],
    network_by_id: dict,
    network_by_cidr: dict,
    router_files: list[Path],
    infrastructure_names: set[str],
) -> list[str]:
    """Check the link plan against the inventory, address plan, and routers."""
    problems: list[str] = []
    endpoint_names = {device["name"] for device in devices}
    links = link_plan["links"]

    seen_ids: set[str] = set()
    used_interfaces: dict[tuple[str, str], str] = {}

    for link in links:
        link_id = link["id"]
        if link_id in seen_ids:
            problems.append(f"{link_id}: duplicate link id")
        seen_ids.add(link_id)

        if network_id := link.get("network_id"):
            if network_id not in network_by_id:
                problems.append(
                    f"{link_id}: network {network_id} is not in the address plan"
                )
        if cidr := link.get("cidr"):
            subnet = ip_network(cidr, strict=False)
            if subnet not in network_by_cidr:
                problems.append(f"{link_id}: {subnet} is not a planned network")
            elif network_id and network_by_cidr[subnet]["id"] != network_id:
                problems.append(
                    f"{link_id}: {subnet} belongs to network "
                    f"{network_by_cidr[subnet]['id']}, not {network_id}"
                )
        if vlans := link.get("vlans"):
            for vlan in vlans:
                planned = [
                    record
                    for record in network_by_id.values()
                    if record.get("vlan_id") == vlan
                ]
                if not planned:
                    problems.append(
                        f"{link_id}: VLAN {vlan} is absent from the address plan"
                    )

        for endpoint in link["endpoints"]:
            node = endpoint["node"]
            # Infrastructure nodes are specified in infrastructure.yaml rather
            # than the device inventory.
            infrastructure = node in infrastructure_names
            if endpoint.get("inventoried", True):
                if node not in endpoint_names and not infrastructure:
                    problems.append(
                        f"{link_id}: {node} is marked inventoried but absent"
                    )
            elif node in endpoint_names or infrastructure:
                problems.append(f"{link_id}: {node} is known but marked as not")
            interface = endpoint.get("interface")
            if not interface:
                continue
            key = (node, interface)
            if key in used_interfaces:
                problems.append(
                    f"{link_id}: {node} {interface} is already used by "
                    f"{used_interfaces[key]}"
                )
            used_interfaces[key] = link_id

    # Every router script must be represented, and the interfaces the link plan
    # claims must be the ones the script configures.
    documented_by_node: dict[str, set[str]] = {}
    for router_file in router_files:
        # A router script is named after its node: router_ot_wan.sh -> MET-OT-WAN.
        stem = router_file.stem.replace("_", "-")
        node = f"MET-{stem.removeprefix('router-').upper()}"
        configured, _ = _router_interfaces(router_file)
        documented = _documented_router_interfaces(router_file)
        documented_by_node[node] = documented
        if configured != documented:
            problems.append(
                f"{router_file.name}: interface map lists "
                f"{sorted(documented)} but the config sets {sorted(configured)}"
            )

    linked_nodes = {
        endpoint["node"] for link in links for endpoint in link["endpoints"]
    }
    for node, documented in sorted(documented_by_node.items()):
        if node not in linked_nodes:
            problems.append(f"{node}: router has no links in the link plan")
            continue
        claimed = {
            endpoint["interface"].removeprefix("eth")
            for link in links
            for endpoint in link["endpoints"]
            if endpoint["node"] == node and endpoint.get("interface")
        }
        if unknown := claimed - documented:
            problems.append(
                f"{node}: link plan uses uncabled interface(s) {sorted(unknown)}"
            )
        if unclaimed := documented - claimed:
            problems.append(
                f"{node}: interface(s) {sorted(unclaimed)} are not in the link plan"
            )

    return problems


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
            "This validates consistency across the checked-in YAML, VyOS command text, and switch Markdown. A blank VLAN means a standalone untagged subnet. For cabling it checks the link plan against the inventory, the address plan, and each router script's configured interfaces, and it checks that every router script is represented. It does not execute VyOS configuration, assign or confirm literal GNS3 port numbers, verify that a link is physically cabled, test packet reachability, or validate firewall/IPsec behavior. The two overlay `/30`s are reservations and intentionally have no configured router interfaces yet.",
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
