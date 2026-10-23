"""Cross-check Metropolis address, inventory, link, router, and switch plans."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
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


@dataclass(frozen=True)
class Check:
    name: str
    problems: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.problems


def validate_topology(
    repository_root: Path, dataset_root: Path | None = None
) -> list[Check]:
    """Validate a Metropolis dataset against its inventory and router configs."""
    testbed_root = repository_root / "testbeds" / "metropolis"
    bundled_root = testbed_root / "datasets" / "water_treatment_v1"
    if dataset_root is None:
        dataset_root = bundled_root
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
    firewall_path = dataset_root / "topology" / "firewall-policy.yaml"
    firewall_policy = (
        yaml.safe_load(firewall_path.read_text(encoding="utf-8"))
        if firewall_path.is_file()
        else None
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
                    ]
                    + (
                        [("firewall-policy.schema.json", firewall_policy)]
                        if firewall_policy is not None
                        else []
                    ),
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
                    *(
                        [("firewall-policy.yaml", firewall_policy.get("testbed_id"))]
                        if firewall_policy is not None
                        else []
                    ),
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

    router_sources = {
        router["name"]: repository_root / router["config"]
        for router in infrastructure.get("routers", [])
    }
    router_files = [path for path in router_sources.values() if path.is_file()]
    router_addresses: list[IPv4Interface] = []
    router_vlans: set[tuple[int, IPv4Network]] = set()
    router_problems: list[str] = []
    ospf_problems: list[str] = []
    router_ids: dict[IPv4Address, str] = {}
    address_pattern = re.compile(
        r"set interfaces ethernet \S+(?: vif (\d+))? address '([^']+)'"
    )
    ospf_pattern = re.compile(r"^set protocols ospf area 0 network '([^']+)'$", re.M)
    active_pattern = re.compile(
        r"^set protocols ospf passive-interface-exclude '([^']+)'$", re.M
    )
    for router_file in router_files:
        text = router_file.read_text(encoding="utf-8")
        lines = set(text.splitlines())
        router_id_values = re.findall(
            r"^set protocols ospf parameters router-id '([^']+)'$", text, re.M
        )
        if len(router_id_values) != 1:
            ospf_problems.append(
                f"{router_file.name}: expected exactly one OSPF router ID"
            )
        else:
            try:
                router_id = IPv4Address(router_id_values[0])
            except ValueError:
                ospf_problems.append(
                    f"{router_file.name}: invalid OSPF router ID {router_id_values[0]!r}"
                )
            else:
                if previous := router_ids.get(router_id):
                    ospf_problems.append(
                        f"{router_file.name}: OSPF router ID {router_id} duplicates {previous}"
                    )
                router_ids[router_id] = router_file.name
        if "set protocols ospf log-adjacency-changes" not in lines:
            ospf_problems.append(
                f"{router_file.name}: OSPF adjacency-change logging is disabled"
            )
        if re.search(
            r"^set protocols ospf (?:redistribute\b|default-information originate\b)",
            text,
            re.M,
        ):
            ospf_problems.append(
                f"{router_file.name}: OSPF redistribution or default origination is configured"
            )
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
        connected = {address.network for address in local_addresses}
        advertised = {ip_network(value) for value in ospf_pattern.findall(text)}
        if advertised != connected:
            ospf_problems.append(
                f"{router_file.name}: OSPF networks differ from connected networks; "
                f"missing {sorted(str(item) for item in connected - advertised)}, "
                f"extra {sorted(str(item) for item in advertised - connected)}"
            )
        if "set protocols ospf passive-interface default" not in text:
            ospf_problems.append(
                f"{router_file.name}: OSPF interfaces are not passive by default"
            )
        if "set protocols static route " in text:
            ospf_problems.append(f"{router_file.name}: static routes remain configured")
        configured_interfaces = {
            f"{base}.{vlan}" if vlan else base: ip_interface(address)
            for base, vlan, address in re.findall(
                r"^set interfaces ethernet (eth\d+)(?: vif (\d+))? address '([^']+)'$",
                text,
                re.M,
            )
        }
        for interface in active_pattern.findall(text):
            if interface not in configured_interfaces:
                ospf_problems.append(
                    f"{router_file.name}: active OSPF interface {interface} has no address"
                )
            elif (
                configured_interfaces[interface].network.prefixlen == 30
                and "set interfaces ethernet "
                f"{interface.replace('.', ' vif ', 1)} ip ospf network 'point-to-point'"
                not in lines
            ):
                ospf_problems.append(
                    f"{router_file.name}: active /30 OSPF interface {interface} "
                    "must use point-to-point network type"
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
            "OSPF advertises connected networks without static routes",
            tuple(ospf_problems),
        )
    )
    checks.append(
        Check(
            "Router syslog destination matches the inventoried receiver",
            tuple(_validate_router_syslog(inventory["devices"], router_sources)),
        )
    )
    checks.append(
        Check(
            "SNMP manager targets and read-only router access match",
            tuple(
                _validate_router_snmp(
                    inventory["devices"], router_sources, firewall_policy
                )
            ),
        )
    )
    if dataset_root.resolve() == bundled_root.resolve():
        checks.append(
            Check(
                "Bundled MQTT broker matches default certificate names and addresses",
                tuple(
                    _validate_bundled_broker_certificate_defaults(
                        inventory["devices"],
                        repository_root / "scripts" / "gen_lab_certificates.py",
                    )
                ),
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

    # The generated reference describes only the bundled dataset, not a custom one.
    switch_files = (
        sorted((testbed_root / "switch").rglob("*.md"))
        if dataset_root.resolve() == bundled_root.resolve()
        else []
    )
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
            "VLAN port membership and router link interfaces match the plan",
            tuple(
                _validate_vlan_ports(
                    infrastructure,
                    inventory["devices"],
                    link_plan,
                    network_by_id,
                    repository_root,
                )
            ),
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
            "HMI cell and telemetry settings match SCADA targets",
            tuple(_validate_hmi_targets(inventory["devices"])),
        )
    )
    if firewall_policy is not None:
        checks.append(
            Check(
                "Routed SCADA and HMI service paths are permitted by policy",
                tuple(
                    _validate_service_flows(
                        inventory["devices"], networks, firewall_policy
                    )
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
                    router_sources,
                    infrastructure_names,
                )
            ),
        )
    )
    checks.append(
        Check(
            "Inventoried devices are cabled or explicitly marked uncabled",
            tuple(_validate_device_links(inventory["devices"], link_plan)),
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


def _validate_router_syslog(
    devices: list[dict], router_sources: dict[str, Path]
) -> list[str]:
    """Check declared routers against the inventory's syslog service node."""
    problems: list[str] = []
    receivers = [device for device in devices if "syslog" in device["protocols"]]
    if len(receivers) > 1:
        return ["multiple inventoried syslog receivers; expected one"]
    if router_sources and not receivers:
        problems.append("no inventoried syslog receiver for declared routers")
    expected = str(ip_interface(receivers[0]["address"]).ip) if receivers else None
    for name, router_file in router_sources.items():
        if not router_file.is_file():
            continue  # The infrastructure check reports the missing script.
        configured = set(
            re.findall(
                r"^set system syslog host '?([^'\s]+)'?\s+",
                router_file.read_text(encoding="utf-8"),
                re.M,
            )
        )
        if expected is None:
            if configured:
                problems.append(
                    f"{name}: syslog hosts {sorted(configured)} have no inventoried receiver"
                )
        elif configured != {expected}:
            problems.append(
                f"{name}: syslog hosts {sorted(configured)} differ from "
                f"inventoried receiver {expected}"
            )
    return problems


def _validate_router_snmp(
    devices: list[dict], router_sources: dict[str, Path], policy: dict | None
) -> list[str]:
    """Cross-check inventoried manager targets, VyOS access, and routed flow."""
    problems: list[str] = []
    managers = [device for device in devices if "snmp-manager" in device["protocols"]]
    for manager in managers:
        name = manager["name"]
        source = str(ip_interface(manager["address"]).ip)
        environment = manager.get("environment", {})
        community = environment.get("SNMP_COMMUNITY", "")
        targets = environment.get("SNMP_TARGETS", "")
        if not community or not targets:
            problems.append(f"{name}: SNMP community and targets are required")
            continue
        seen: set[str] = set()
        for item in targets.split(","):
            target_name, separator, target_ip = item.strip().partition("=")
            if (
                not separator
                or target_name in seen
                or target_name not in router_sources
            ):
                problems.append(f"{name}: invalid SNMP target {item!r}")
                continue
            seen.add(target_name)
            try:
                target_address = IPv4Address(target_ip)
            except ValueError:
                problems.append(f"{name}: invalid SNMP target address {target_ip!r}")
                continue
            router_file = router_sources[target_name]
            if not router_file.is_file():
                continue  # The infrastructure check reports the missing script.
            script = router_file.read_text(encoding="utf-8")
            addresses = {
                ip_interface(value).ip
                for value in re.findall(
                    r"^set interfaces ethernet \S+(?: vif \d+)? address '([^']+)'$",
                    script,
                    re.M,
                )
            }
            if target_address not in addresses:
                problems.append(
                    f"{name}: {target_name} SNMP target {target_address} "
                    "is not a router interface"
                )
            if (
                f"set service snmp community {community} authorization ro" not in script
                or f"set service snmp community {community} client {source}"
                not in script
            ):
                problems.append(
                    f"{target_name}: read-only SNMP access from {source} is missing"
                )
            if policy is not None and not any(
                flow.get("protocol") == "udp"
                and flow.get("port") == 161
                and f"{source}/32"
                in (
                    [flow["source"]]
                    if isinstance(flow["source"], str)
                    else flow["source"]
                )
                and f"{target_address}/32"
                in (
                    [flow["destination"]]
                    if isinstance(flow["destination"], str)
                    else flow["destination"]
                )
                for flow in policy.get("flows", [])
            ):
                problems.append(f"{name}: no UDP/161 firewall flow to {target_name}")
    return problems


def _validate_bundled_broker_certificate_defaults(
    devices: list[dict], generator_file: Path
) -> list[str]:
    """Check the bundled dataset's no-argument lab certificate workflow."""
    brokers = [
        device
        for device in devices
        if device.get("model", "").replace("\\", "/").endswith("/mqtt_broker")
    ]
    if not brokers:
        return []
    if not generator_file.is_file():
        return [f"default certificate generator is missing: {generator_file}"]
    spec = importlib.util.spec_from_file_location(
        "gen_lab_certificates", generator_file
    )
    if spec is None or spec.loader is None:
        return [f"cannot load default certificate generator: {generator_file}"]
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    names = set(module.DEFAULT_NAMES)
    addresses = set(module.DEFAULT_ADDRESSES)
    problems: list[str] = []
    for broker in brokers:
        if broker["name"] not in names:
            problems.append(
                f"{broker['name']}: broker name is absent from default certificate DNS names"
            )
        address = str(ip_interface(broker["address"]).ip)
        if address not in addresses:
            problems.append(
                f"{broker['name']}: broker address {address} is absent from "
                "default certificate IP addresses"
            )
    return problems


def _validate_vlan_ports(
    infrastructure: dict,
    devices: list[dict],
    link_plan: dict,
    network_by_id: dict,
    repository_root: Path,
) -> list[str]:
    """Check every declared VLAN path against the commands and cabling plan."""
    problems: list[str] = []
    links = link_plan["links"]
    devices_by_name = {device["name"]: device for device in devices}
    routers = {router["name"]: router for router in infrastructure.get("routers", [])}

    for name, router in routers.items():
        config_path = repository_root / router["config"]
        if not config_path.is_file():
            continue  # The infrastructure check reports the missing script.
        text = config_path.read_text(encoding="utf-8")
        configured_vlans: dict[str, set[int]] = {}
        for interface, vlan in re.findall(
            r"^set interfaces ethernet (eth\d+) vif (\d+)\b", text, re.M
        ):
            configured_vlans.setdefault(interface, set()).add(int(vlan))
        configured_addresses = _router_addresses(config_path)
        for interface in router.get("interfaces", []):
            interface_name = interface["name"]
            expected = set(interface.get("vlans", []))
            actual = configured_vlans.get(interface_name, set())
            if actual != expected:
                problems.append(
                    f"{name} {interface_name}: declared VLANs {sorted(expected)} "
                    f"differ from VyOS vifs {sorted(actual)}"
                )
            attached = [
                link
                for link in links
                if any(
                    endpoint["node"] == name
                    and endpoint.get("interface") == interface_name
                    for endpoint in link["endpoints"]
                )
            ]
            if len(attached) != 1:
                problems.append(
                    f"{name} {interface_name}: expected one link, found {len(attached)}"
                )
                continue
            link = attached[0]
            if expected:
                if link["kind"] != "trunk" or set(link.get("vlans", [])) != expected:
                    problems.append(
                        f"{link['id']}: {name} {interface_name} trunk VLANs "
                        f"must be {sorted(expected)}"
                    )
            elif link.get("vlans"):
                problems.append(
                    f"{link['id']}: {name} {interface_name} is untagged but "
                    f"the link lists VLANs {link['vlans']}"
                )
            if link.get("network_id") in network_by_id and not expected:
                network = network_by_id[link["network_id"]]
                address = configured_addresses.get(interface_name.removeprefix("eth"))
                if address and ip_interface(address).network != ip_network(
                    network["cidr"]
                ):
                    problems.append(
                        f"{link['id']}: {name} {interface_name} address {address} "
                        f"does not match {network['id']} ({network['cidr']})"
                    )

    for link in links:
        network = network_by_id.get(link.get("network_id"))
        if network is None:
            continue
        for endpoint in link["endpoints"]:
            device = devices_by_name.get(endpoint["node"])
            if device and ip_network(device["subnet"]) != ip_network(network["cidr"]):
                problems.append(
                    f"{link['id']}: {device['name']} subnet {device['subnet']} "
                    f"does not match {network['id']} ({network['cidr']})"
                )

    for segment in infrastructure.get("segments", []):
        name = segment["name"]
        network = network_by_id.get(segment.get("network_id"))
        if network is None:
            continue  # The infrastructure check reports an unknown network.
        expected_vlan = network.get("vlan_id")
        uplinks = [
            link
            for link in links
            if link["kind"] == "trunk"
            and any(endpoint["node"] == name for endpoint in link["endpoints"])
        ]
        if len(uplinks) != 1 or uplinks[0].get("vlans") != [expected_vlan]:
            problems.append(
                f"{name}: expected one uplink carrying VLAN {expected_vlan}"
            )
        for link in links:
            if link["kind"] != "access" or not any(
                endpoint["node"] == name for endpoint in link["endpoints"]
            ):
                continue
            if link.get("network_id") != segment["network_id"]:
                problems.append(
                    f"{link['id']}: {name} access port must use {segment['network_id']}"
                )
    return problems


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

    # The attachment points are the only record of a switch's ports. The
    # per-switch Markdown specifications that used to restate them are gone:
    # testbeds/metropolis/switch/README.md is generated from this same data.
    switch_roles: dict[str, set[str]] = {}
    for switch in switches:
        name = switch["name"]
        declared = set(switch.get("attachment_points", []))
        switch_roles[name] = declared
        if not declared:
            problems.append(f"{name}: declares no attachment points")

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
    router_sources: dict[str, Path],
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
    for node, router_file in router_sources.items():
        if not router_file.is_file():
            continue  # The infrastructure check reports the missing script.
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


def _validate_device_links(devices: list[dict], link_plan: dict) -> list[str]:
    """Find unexpected orphan devices and stale intentional-uncabling markers."""
    linked = {
        endpoint["node"]
        for link in link_plan["links"]
        for endpoint in link["endpoints"]
    }
    problems: list[str] = []
    for device in devices:
        name = device["name"]
        policy = device.get("link_policy", "required")
        if policy == "required" and name not in linked:
            problems.append(f"{name}: inventoried device has no declared physical link")
        elif policy == "intentionally_uncabled" and name in linked:
            problems.append(
                f"{name}: marked intentionally uncabled but appears in the link plan"
            )
    return problems


def render_report(
    repository_root: Path, checks: list[Check], dataset_root: Path | None = None
) -> str:
    """Render the validation results and the source address table as Markdown."""
    bundled_root = (
        repository_root / "testbeds" / "metropolis" / "datasets" / "water_treatment_v1"
    )
    if dataset_root is None:
        dataset_root = bundled_root
    bundled = dataset_root.resolve() == bundled_root.resolve()
    try:
        displayed_dataset = dataset_root.resolve().relative_to(
            repository_root.resolve()
        )
    except ValueError:
        displayed_dataset = dataset_root.resolve()
    displayed_dataset_text = displayed_dataset.as_posix()
    address_plan = yaml.safe_load(
        (dataset_root / "topology" / "address-plan.yaml").read_text(encoding="utf-8")
    )
    passed = sum(check.passed for check in checks)
    lines = [
        f"# {dataset_root.name} topology validation",
        "",
        f"**Result: {'PASS' if passed == len(checks) else 'FAIL'} ({passed}/{len(checks)} checks passed)**",
        "",
        "Regenerate this report from the repository root with:",
        "",
        "```bash",
        (
            "uv run python scripts/validate_metropolis_topology.py "
            f"{'--dataset ' + displayed_dataset_text + ' ' if not bundled else ''}"
            f"--report {displayed_dataset_text}/topology/validation-report.md"
        ),
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
            "## Scope and limitations",
            "",
            "This checks consistency among the selected dataset's YAML documents, its declared router scripts, and the declared links and VLAN paths. "
            + (
                "It also checks the generated switch reference and no-argument "
                "MQTT certificate defaults for the bundled dataset. "
                if bundled
                else "The generated switch reference belongs to the bundled dataset and is not checked for this dataset. "
            )
            + "A blank VLAN means an untagged subnet. Custom certificate names, "
            "addresses, and mounts are outside this check. It does not execute "
            "router or switch configuration, verify physical GNS3 cabling, test "
            "packet reachability or Layer 2 isolation, or validate firewall/IPsec behavior.",
            "",
        ]
    )
    return "\n".join(lines)


def _validate_device_targets(devices: list[dict]) -> list[str]:
    known_addresses = {ip_interface(device["address"]).ip for device in devices}
    problems = []
    for device in devices:
        environment = device.get("environment", {})
        for key, target in environment.items():
            if not key.endswith("_HOST"):
                continue
            try:
                address = IPv4Address(target)
            except ValueError:
                problems.append(
                    f"{device['name']}: {key} target {target!r} is not an IPv4 address"
                )
                continue
            if address not in known_addresses:
                problems.append(
                    f"{device['name']}: {key} target {target} is not inventoried"
                )
        if targets_text := environment.get("SCADA_TARGETS"):
            try:
                targets = json.loads(targets_text)
                if not isinstance(targets, list) or not targets:
                    raise ValueError("expected a nonempty array")
                for target in targets:
                    if not isinstance(target, dict) or not all(
                        isinstance(target.get(key), str) and target[key]
                        for key in ("id", "host", "profile", "topic")
                    ):
                        raise ValueError(
                            "each target needs id, host, profile, and topic"
                        )
                    if IPv4Address(target["host"]) not in known_addresses:
                        problems.append(
                            f"{device['name']}: SCADA target {target['host']} is not inventoried"
                        )
            except (ValueError, TypeError) as exc:
                problems.append(f"{device['name']}: invalid SCADA_TARGETS: {exc}")
    return problems


def _validate_hmi_targets(devices: list[dict]) -> list[str]:
    """Keep HMI subscriptions aligned with their selected SCADA target."""
    problems: list[str] = []
    targets_by_id: dict[str, set[str]] = {}
    for device in devices:
        configured = device.get("environment", {}).get("SCADA_TARGETS")
        if not configured:
            continue
        try:
            targets = json.loads(configured)
        except (TypeError, ValueError):
            continue  # The service-target check reports malformed JSON.
        if not isinstance(targets, list):
            continue
        for target in targets:
            if (
                isinstance(target, dict)
                and isinstance(target.get("id"), str)
                and isinstance(target.get("topic"), str)
            ):
                targets_by_id.setdefault(target["id"], set()).add(
                    target.get("topic", "")
                )
    for device in devices:
        environment = device.get("environment", {})
        if (
            "HMI_CELL_ID" not in environment
            and "MQTT_TELEMETRY_TOPIC" not in environment
        ):
            continue
        cell = environment.get("HMI_CELL_ID")
        topic = environment.get("MQTT_TELEMETRY_TOPIC")
        if not cell or not topic:
            problems.append(
                f"{device['name']}: HMI_CELL_ID and MQTT_TELEMETRY_TOPIC are both required"
            )
        elif targets_by_id.get(cell) != {topic}:
            problems.append(
                f"{device['name']}: telemetry topic {topic!r} does not match "
                f"SCADA target {cell!r}"
            )
    return problems


def _selector_contains(
    selector: str, address: IPv4Address, networks: list[dict]
) -> bool:
    """Resolve a firewall network selector against one device address."""
    declared = next((item for item in networks if item["id"] == selector), None)
    if declared is None and selector.startswith("vlan-") and selector[5:].isdecimal():
        vlan = int(selector[5:])
        declared = next(
            (item for item in networks if item.get("vlan_id") == vlan), None
        )
    if declared is not None:
        selector = declared["cidr"]
    try:
        return address in ip_network(selector, strict=False)
    except ValueError:
        return False


def _flow_allows(
    firewall_policy: dict,
    networks: list[dict],
    source: IPv4Address,
    destination: IPv4Address,
    port: int,
) -> bool:
    def selectors(value: str | list[str]) -> list[str]:
        return [value] if isinstance(value, str) else value

    return any(
        flow["protocol"] == "tcp"
        and flow["port"] == port
        and any(
            _selector_contains(selector, source, networks)
            for selector in selectors(flow["source"])
        )
        and any(
            _selector_contains(selector, destination, networks)
            for selector in selectors(flow["destination"])
        )
        for flow in firewall_policy["flows"]
    )


def _validate_service_flows(
    devices: list[dict], networks: list[dict], firewall_policy: dict
) -> list[str]:
    """Check declared routed SCADA polls and HMI broker sessions."""
    problems: list[str] = []
    devices_by_address = {
        ip_interface(device["address"]).ip: device for device in devices
    }
    for device in devices:
        environment = device.get("environment", {})
        targets_text = environment.get("SCADA_TARGETS")
        if targets_text:
            try:
                targets = json.loads(targets_text)
            except (TypeError, ValueError):
                continue  # The service-target check reports malformed JSON.
            if not isinstance(targets, list):
                continue
            for target in targets:
                if not isinstance(target, dict):
                    continue
                try:
                    destination = IPv4Address(target["host"])
                except (KeyError, ValueError):
                    continue
                source = ip_interface(device["address"]).ip
                target_device = devices_by_address.get(destination)
                if target_device and ip_network(device["subnet"]) == ip_network(
                    target_device["subnet"]
                ):
                    continue
                if not _flow_allows(
                    firewall_policy, networks, source, destination, 502
                ):
                    problems.append(
                        f"{device['name']}: no TCP/502 firewall flow to SCADA "
                        f"target {target.get('id', destination)} ({destination})"
                    )
        if "HMI_CELL_ID" in environment and "MQTT_HOST" in environment:
            try:
                source = ip_interface(device["address"]).ip
                destination = IPv4Address(environment["MQTT_HOST"])
            except ValueError:
                continue  # The service-target check reports an invalid host.
            target_device = devices_by_address.get(destination)
            if target_device and ip_network(device["subnet"]) == ip_network(
                target_device["subnet"]
            ):
                continue
            if not _flow_allows(firewall_policy, networks, source, destination, 1883):
                problems.append(
                    f"{device['name']}: no TCP/1883 firewall flow to MQTT "
                    f"broker {destination}"
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
        "--dataset",
        type=Path,
        help="dataset directory; relative paths are resolved from --repo-root",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="optional path to write a Markdown validation report",
    )
    args = parser.parse_args()
    repository_root = args.repo_root.resolve()
    dataset_root = args.dataset
    if dataset_root is not None and not dataset_root.is_absolute():
        dataset_root = repository_root / dataset_root
    checks = validate_topology(repository_root, dataset_root)
    for check in checks:
        print(f"{'PASS' if check.passed else 'FAIL'}  {check.name}")
        for problem in check.problems:
            print(f"      - {problem}")
    passed = sum(check.passed for check in checks)
    print(f"\n{passed}/{len(checks)} checks passed")
    if args.report:
        report = render_report(repository_root, checks, dataset_root)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
        print(f"Report written to {args.report}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
