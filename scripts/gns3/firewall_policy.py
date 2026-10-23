"""Compile network-level flows into VyOS 1.3 zone-policy commands offline.

Every routed flow is installed at each router it crosses. A flow within one
subnet bypasses the routers and is deliberately omitted from the policy.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from itertools import product
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, ip_interface, ip_network
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
ADDRESS = re.compile(
    r"^set interfaces ethernet (eth\d+)(?: vif (\d+))? address '([^']+)'$"
)
OSPF_NETWORK = re.compile(r"^set protocols ospf area 0 network '([^']+)'$")
OSPF_ACTIVE = re.compile(r"^set protocols ospf passive-interface-exclude '([^']+)'$")
BFD_INTERFACE = re.compile(r"^set interfaces ethernet (eth\d+) ip ospf bfd$")
BFD_PEER = re.compile(r"^set protocols bfd peer '([^']+)'$")
MANAGED_ZONE = re.compile(r"\bzone-policy zone (MET_[A-Za-z0-9_]+)\b")
MANAGED_RULESET = re.compile(r"\bfirewall name (MET_[A-Za-z0-9_]+)\b")


@dataclass(frozen=True)
class Flow:
    name: str
    source: tuple[str, ...]
    destination: tuple[str, ...]
    protocol: str
    port: int | None


@dataclass
class Router:
    name: str
    interfaces: dict[str, IPv4Interface]
    advertised: set[IPv4Network]
    ospf_active: set[str]
    bfd_active: set[str] = field(default_factory=set)
    bfd_peers: set[IPv4Address] = field(default_factory=set)


@dataclass(frozen=True)
class Endpoint:
    selector: str
    network: IPv4Network | None
    router: str | None = None
    address: IPv4Address | None = None


def _routers(infrastructure: dict) -> dict[str, Router]:
    routers = {}
    for declared in infrastructure["routers"]:
        name = declared["name"]
        path = ROOT / declared["config"]
        interfaces: dict[str, IPv4Interface] = {}
        advertised: set[IPv4Network] = set()
        ospf_active: set[str] = set()
        bfd_active: set[str] = set()
        bfd_peers: set[IPv4Address] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            match = ADDRESS.fullmatch(line.strip())
            if match:
                base, vlan, address = match.groups()
                interfaces[f"{base}.{vlan}" if vlan else base] = ip_interface(address)
            match = OSPF_NETWORK.fullmatch(line.strip())
            if match:
                advertised.add(ip_network(match.group(1)))
            match = OSPF_ACTIVE.fullmatch(line.strip())
            if match:
                ospf_active.add(match.group(1))
            match = BFD_INTERFACE.fullmatch(line.strip())
            if match:
                bfd_active.add(match.group(1))
            match = BFD_PEER.fullmatch(line.strip())
            if match:
                bfd_peers.add(IPv4Address(match.group(1)))
        if not interfaces:
            raise ValueError(f"{name}: router script has no interfaces")
        connected = {address.network for address in interfaces.values()}
        if advertised != connected:
            raise ValueError(
                f"{name}: OSPF advertised networks do not match connected networks: "
                f"missing {sorted(str(item) for item in connected - advertised)}, "
                f"extra {sorted(str(item) for item in advertised - connected)}"
            )
        if not bfd_active <= ospf_active:
            raise ValueError(f"{name}: BFD must use active OSPF interfaces")
        routers[name] = Router(
            name, interfaces, advertised, ospf_active, bfd_active, bfd_peers
        )
    return routers


def _flow(record: dict) -> Flow:
    try:
        name = record["id"]
        source = _selectors(record["source"])
        destination = _selectors(record["destination"])
        protocol = record["protocol"]
        port = record.get("port")
    except (KeyError, TypeError) as exc:
        raise ValueError(f"invalid firewall flow: {record!r}") from exc
    if protocol not in {"tcp", "udp", "icmp"}:
        raise ValueError(f"{name}: unsupported protocol {protocol!r}")
    if protocol == "icmp":
        if port is not None:
            raise ValueError(f"{name}: ICMP has no port")
    elif type(port) is not int or not 1 <= port <= 65535:
        raise ValueError(f"{name}: TCP/UDP port must be 1–65535")
    if not isinstance(name, str) or not name:
        raise ValueError("flow ID must be a nonempty string")
    return Flow(name, source, destination, protocol, port)


def _selectors(value: str | list[str]) -> tuple[str, ...]:
    entries = [value] if isinstance(value, str) else value
    if (
        not isinstance(entries, list)
        or not entries
        or any(not isinstance(item, str) or not item for item in entries)
        or len(set(entries)) != len(entries)
    ):
        raise ValueError(f"invalid firewall network selector: {value!r}")
    return tuple(entries)


def parse_attack_exception(value: str) -> Flow:
    """Parse SOURCE-NET:DEST-NET:PROTOCOL:PORT; '-' means no ICMP port."""
    parts = value.split(":")
    if len(parts) != 4:
        raise ValueError(
            "attack exception must be SOURCE-NET:DEST-NET:tcp|udp|icmp:PORT"
        )
    source, destination, protocol, port_text = parts
    try:
        port = None if port_text == "-" else int(port_text)
    except ValueError as exc:
        raise ValueError(
            "attack exception port must be a number or '-' for ICMP"
        ) from exc
    return _flow(
        {
            "id": f"attack-{source}-{destination}-{protocol}-{port_text}",
            "source": source,
            "destination": destination,
            "protocol": protocol,
            "port": port,
        }
    )


def _token(interface: str) -> str:
    return interface.replace("eth", "e").replace(".", "v")


def _zone(interface: str) -> str:
    return "MET_LOCAL" if interface == "local" else f"MET_{_token(interface)}"


def _ruleset(source: str, destination: str) -> str:
    return f"MET_{_token(source)}_{_token(destination)}"


def managed_deletes(configuration: str) -> list[str]:
    """Remove only Metropolis-owned zones/rules before a repeat application."""
    zones = sorted(set(MANAGED_ZONE.findall(configuration)))
    rulesets = sorted(set(MANAGED_RULESET.findall(configuration)))
    return [f"delete zone-policy zone {name}" for name in zones] + [
        f"delete firewall name {name}" for name in rulesets
    ]


def _owner(routers: dict[str, Router], address: IPv4Address) -> tuple[str, str]:
    exact = [
        (router.name, interface)
        for router in routers.values()
        for interface, configured in router.interfaces.items()
        if address == configured.ip
    ]
    if len(exact) == 1:
        return exact[0]
    matches = [
        (router.name, interface)
        for router in routers.values()
        for interface, configured in router.interfaces.items()
        if address in configured.network
    ]
    if len(matches) != 1:
        raise ValueError(
            f"{address}: expected one directly connected router, got {matches}"
        )
    return matches[0]


def _resolve_endpoint(
    selector: str,
    networks: list[dict],
    infrastructure: dict,
    links: list[dict],
    routers: dict[str, Router],
    *,
    source: bool,
) -> Endpoint:
    if selector.startswith("router/"):
        name = selector.removeprefix("router/")
        if not source or name not in routers:
            raise ValueError(f"invalid router source selector {selector!r}")
        return Endpoint(selector, None, name)
    by_id = [item for item in networks if item["id"] == selector]
    by_vlan = []
    if selector.startswith("vlan-") and selector[5:].isdecimal():
        by_vlan = [
            item for item in networks if item.get("vlan_id") == int(selector[5:])
        ]
    if by_id or by_vlan:
        matches = by_id or by_vlan
        if len(matches) != 1:
            raise ValueError(f"{selector}: expected one address-plan network")
        declared = matches[0]
        network = ip_network(declared["cidr"], strict=True)
    else:
        try:
            network = ip_network(selector, strict=True)
        except ValueError as exc:
            raise ValueError(f"unknown firewall network selector {selector!r}") from exc
        if not isinstance(network, IPv4Network):
            raise ValueError(f"{selector}: firewall selectors must be IPv4 CIDRs")
        matches = [
            item
            for item in networks
            if network.subnet_of(ip_network(item["cidr"], strict=True))
        ]
        if len(matches) != 1:
            raise ValueError(f"{selector}: CIDR must fit one declared network")
        declared = matches[0]
    if not isinstance(network, IPv4Network):
        raise ValueError(f"{selector}: firewall selectors must be IPv4 CIDRs")
    if not source and network.prefixlen == 32:
        owners = [
            router.name
            for router in routers.values()
            if any(
                address.ip == network.network_address
                for address in router.interfaces.values()
            )
        ]
        if len(owners) == 1:
            return Endpoint(selector, None, owners[0], network.network_address)
    if declared.get("kind") != "lan":
        raise ValueError(f"{selector}: firewall endpoint must be a LAN")
    if not _network_cabled(declared, infrastructure, links):
        raise ValueError(f"{selector}: network has no declared link")
    _owner(routers, network.network_address)
    return Endpoint(selector, network)


def _network_cabled(network: dict, infrastructure: dict, links: list[dict]) -> bool:
    network_id = network["id"]
    segment_names = {
        item["name"]
        for item in infrastructure.get("segments", [])
        if item["network_id"] == network_id
    }
    for link in links:
        if link.get("network_id") == network_id:
            return True
        if network.get("vlan_id") in link.get(
            "vlans", []
        ) and segment_names.intersection(
            endpoint["node"] for endpoint in link["endpoints"]
        ):
            return True
    return False


def _path(
    routers: dict[str, Router],
    adjacency: dict[str, list[tuple[str, str, str]]],
    source_name: str,
    source_ip: IPv4Address | None,
    destination: IPv4Address,
) -> tuple[list[tuple[str, str, str]], IPv4Address]:
    if source_ip is None:
        start, incoming = source_name, "local"
    else:
        start, incoming = _owner(routers, source_ip)
    end, destination_interface = _owner(routers, destination)
    destination_network = routers[end].interfaces[destination_interface].network
    if destination_network not in routers[end].advertised:
        raise ValueError(f"{end}: {destination_network} is not advertised in OSPF")
    queue = deque([(start, [])])
    shortest: list[list[tuple[str, str, str]]] = []
    while queue:
        current, edges = queue.popleft()
        if shortest and len(edges) > len(shortest[0]):
            break
        if current == end:
            shortest.append(edges)
            continue
        for neighbor, outgoing, peer_interface in adjacency[current]:
            if (
                neighbor not in {start, *(edge[0] for edge in edges)}
                and neighbor != current
            ):
                queue.append((neighbor, [*edges, (current, outgoing, peer_interface)]))
    if not shortest:
        raise ValueError(f"no declared OSPF path from {start} to {end}")
    if len(shortest) != 1:
        raise ValueError(f"multiple equal-cost OSPF paths from {start} to {end}")
    edges = shortest[0]
    path = []
    for router_name, outgoing, peer_interface in edges:
        path.append((router_name, incoming, outgoing))
        incoming = peer_interface
    if start != end or incoming != destination_interface:
        path.append((end, incoming, destination_interface))
    if source_ip is None:
        first_outgoing = path[0][2] if path else destination_interface
        source_ip = routers[start].interfaces[first_outgoing].ip
    if any(in_iface == out_iface for _, in_iface, out_iface in path):
        raise ValueError("flow enters and leaves the same OSPF interface")
    return path, source_ip


def _cabled_between(
    links: list[dict],
    switch_names: set[str],
    first: str,
    first_interface: str,
    second: str,
    second_interface: str,
) -> bool:
    """Check a declared router transit, allowing switches between its ends."""
    graph: dict[str, list[tuple[str, str | None, str | None]]] = {}
    for link in links:
        left, right = link["endpoints"]
        graph.setdefault(left["node"], []).append(
            (right["node"], left.get("interface"), right.get("interface"))
        )
        graph.setdefault(right["node"], []).append(
            (left["node"], right.get("interface"), left.get("interface"))
        )
    queue = deque([first])
    visited = {first}
    while queue:
        current = queue.popleft()
        for neighbor, outgoing, incoming in graph.get(current, []):
            if current == first and outgoing != first_interface.split(".")[0]:
                continue
            if neighbor == second:
                if incoming == second_interface.split(".")[0]:
                    return True
                continue
            if neighbor in switch_names and neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return False


def compile_policy(
    dataset: Path,
    infrastructure: dict,
    inventory: dict,
    attack_exceptions: list[Flow] | None = None,
) -> tuple[dict[str, list[str]], dict]:
    """Return router commands and an offline coverage report.

    Policy endpoints are address-plan network IDs, VLAN IDs, or contained CIDRs.
    Router-originated syslog uses ``router/NAME`` as a source selector.
    Attack exceptions must originate within the declared attack-lab network.
    """
    document = yaml.safe_load(
        (dataset / "topology/firewall-policy.yaml").read_text(encoding="utf-8")
    )
    if document.get("schema_version") != "2.0" or document.get(
        "testbed_id"
    ) != inventory.get("testbed_id"):
        raise ValueError("firewall policy version or testbed does not match inventory")
    routers = _routers(infrastructure)
    baseline = [_flow(item) for item in document["flows"]]
    exceptions = attack_exceptions or []
    if len({flow.name for flow in baseline}) != len(baseline):
        raise ValueError("duplicate firewall flow ID")
    address_plan = yaml.safe_load(
        (dataset / "topology/address-plan.yaml").read_text(encoding="utf-8")
    )
    networks = address_plan["networks"]
    if len({item["id"] for item in networks}) != len(networks):
        raise ValueError("duplicate address-plan network ID")
    link_plan = yaml.safe_load((dataset / "topology/links.yaml").read_text("utf-8"))
    links = link_plan["links"]
    switch_names = {
        item["name"]
        for group in ("switches", "segments")
        for item in infrastructure.get(group, [])
    }
    attack_networks = [
        ip_network(item["cidr"]) for item in networks if item["id"] == "attack-lab"
    ]
    if len(attack_networks) > 1 or (exceptions and not attack_networks):
        raise ValueError("attack exceptions require one attack-lab network")
    pairs: dict[str, dict[tuple[str, str], set[tuple[str, str, str, int | None]]]] = {
        name: {} for name in routers
    }
    adjacency: dict[str, list[tuple[str, str, str]]] = {name: [] for name in routers}
    # OSPF terminates on the routers themselves. Permit IP protocol 89 only on
    # declared neighbor interfaces, from the named peer addresses. These rules
    # precede the generic invalid-state drop because multicast control packets
    # are not reliably classified as NEW by connection tracking.
    for name, router in routers.items():
        for interface in sorted(router.ospf_active):
            address = router.interfaces.get(interface)
            if address is None:
                raise ValueError(f"{name}: OSPF interface {interface} has no address")
            if address.network not in router.advertised:
                raise ValueError(
                    f"{name}: OSPF interface {interface} is not advertised"
                )
            peers = [
                (other.name, peer_interface, peer_address)
                for other in routers.values()
                if other.name != name
                for peer_interface, peer_address in other.interfaces.items()
                if peer_interface in other.ospf_active
                and peer_address.network == address.network
            ]
            if not peers:
                raise ValueError(f"{name} {interface}: no declared OSPF neighbor")
            for peer_name, peer_interface, peer_address in peers:
                if not _cabled_between(
                    links, switch_names, name, interface, peer_name, peer_interface
                ):
                    raise ValueError(
                        f"{name} {interface}: no declared cable to OSPF neighbor "
                        f"{peer_name} {peer_interface}"
                    )
                adjacency[name].append((peer_name, interface, peer_interface))
                pairs[name].setdefault((interface, "local"), set()).add(
                    (str(peer_address.ip), "0.0.0.0/0", "89", None)
                )
                if interface in router.bfd_active:
                    peer = routers[peer_name]
                    if (
                        peer_interface not in peer.bfd_active
                        or peer_address.ip not in router.bfd_peers
                        or address.ip not in peer.bfd_peers
                    ):
                        raise ValueError(
                            f"{name} {interface}: BFD peer {peer_name} is not configured on both ends"
                        )
                    pairs[name].setdefault((interface, "local"), set()).add(
                        (f"{peer_address.ip}/32", f"{address.ip}/32", "udp", 3784)
                    )
            pairs[name].setdefault(("local", interface), set()).add(
                (str(address.ip), "0.0.0.0/0", "89", None)
            )
            if interface in router.bfd_active:
                for _, _, peer_address in peers:
                    pairs[name].setdefault(("local", interface), set()).add(
                        (f"{address.ip}/32", f"{peer_address.ip}/32", "udp", 3784)
                    )
    report = {"baseline_flows": [], "attack_exceptions": [], "local_subnet_flows": []}
    for flow, category in [
        *((flow, "baseline_flows") for flow in baseline),
        *((flow, "attack_exceptions") for flow in exceptions),
    ]:
        for source_name, destination_name in product(flow.source, flow.destination):
            try:
                source = _resolve_endpoint(
                    source_name, networks, infrastructure, links, routers, source=True
                )
                destination = _resolve_endpoint(
                    destination_name,
                    networks,
                    infrastructure,
                    links,
                    routers,
                    source=False,
                )
                if destination.network is None and destination.address is None:
                    raise ValueError(
                        "destination must select a declared LAN or router IP"
                    )
                source_ip = source.network.network_address if source.network else None
                destination_ip = (
                    destination.network.network_address
                    if destination.network is not None
                    else destination.address
                )
                if category == "attack_exceptions":
                    if destination.network is None:
                        raise ValueError("attack target must select a declared LAN")
                    if source.network is None or not source.network.subnet_of(
                        attack_networks[0]
                    ):
                        raise ValueError("attack source is not on attack-lab")
                    if destination.network.subnet_of(attack_networks[0]):
                        raise ValueError("attack target is on attack-lab")
                elif attack_networks and (
                    (
                        source.network is not None
                        and source.network.subnet_of(attack_networks[0])
                    )
                    or (
                        destination.network is not None
                        and destination.network.subnet_of(attack_networks[0])
                    )
                ):
                    raise ValueError("baseline policy cannot permit attack-lab traffic")
                path, resolved_source = _path(
                    routers, adjacency, source.router or "", source_ip, destination_ip
                )
                if destination.router:
                    if not path and source_ip is not None:
                        _, incoming = _owner(routers, source_ip)
                        path = [(destination.router, incoming, "local")]
                    if not path or path[-1][0] != destination.router:
                        raise ValueError("router-local path did not reach the target")
                    name, incoming, _ = path[-1]
                    path[-1] = (name, incoming, "local")
                if path and source_ip is not None and not destination.router:
                    reverse, _ = _path(
                        routers, adjacency, "", destination_ip, source_ip
                    )
                    if reverse != [
                        (name, outgoing, incoming)
                        for name, incoming, outgoing in reversed(path)
                    ]:
                        raise ValueError("return route is asymmetric")
                for (first, _, outgoing), (second, incoming, _) in zip(path, path[1:]):
                    if not _cabled_between(
                        links, switch_names, first, outgoing, second, incoming
                    ):
                        raise ValueError(
                            f"no declared cable from {first} {outgoing} "
                            f"to {second} {incoming}"
                        )
            except ValueError as exc:
                raise ValueError(
                    f"{flow.name} ({source_name} -> {destination_name}): {exc}"
                ) from exc
            source_cidr = (
                str(source.network) if source.network else f"{resolved_source}/32"
            )
            record = {
                "id": flow.name,
                "source": source_name,
                "destination": destination_name,
                "source_cidr": source_cidr,
                "destination_cidr": (
                    str(destination.network)
                    if destination.network is not None
                    else f"{destination_ip}/32"
                ),
                "protocol": flow.protocol,
                "port": flow.port,
                "routers": [name for name, _, _ in path],
            }
            report[category].append(record)
            if not path:
                report["local_subnet_flows"].append(
                    f"{flow.name}:{source_name}:{destination_name}"
                )
                continue
            for name, incoming, outgoing in path:
                allowed = pairs[name].setdefault((incoming, outgoing), set())
                allowed.add(
                    (source_cidr, record["destination_cidr"], flow.protocol, flow.port)
                )
                pairs[name].setdefault((outgoing, incoming), set())
    commands = {}
    for name, router in routers.items():
        lines = []
        for interface in [*router.interfaces, "local"]:
            zone = _zone(interface)
            lines.append(f"set zone-policy zone {zone} default-action 'drop'")
            lines.append(
                f"set zone-policy zone {zone} local-zone"
                if interface == "local"
                else f"set zone-policy zone {zone} interface '{interface}'"
            )
        for (incoming, outgoing), allowed in sorted(pairs[name].items()):
            ruleset = _ruleset(incoming, outgoing)
            lines += [
                f"set firewall name {ruleset} default-action 'drop'",
                f"set firewall name {ruleset} rule 10 action 'drop'",
                f"set firewall name {ruleset} rule 10 state invalid 'enable'",
                f"set firewall name {ruleset} rule 20 action 'accept'",
                f"set firewall name {ruleset} rule 20 state established 'enable'",
                f"set firewall name {ruleset} rule 21 action 'accept'",
                f"set firewall name {ruleset} rule 21 state related 'enable'",
            ]
            ospf = sorted(item for item in allowed if item[2] == "89")
            other = sorted(item for item in allowed if item[2] != "89")
            if len(ospf) >= 10:
                raise ValueError(f"{name}: too many OSPF peers on {incoming}")
            for number, (source, destination, protocol, port) in [
                *enumerate(ospf, 1),
                *enumerate(other, 100),
            ]:
                prefix = f"set firewall name {ruleset} rule {number}"
                lines += [
                    f"{prefix} action 'accept'",
                    f"{prefix} source address '{source}'",
                    f"{prefix} destination address '{destination}'",
                    f"{prefix} protocol '{protocol}'",
                ]
                if protocol != "89":
                    lines.append(f"{prefix} state new 'enable'")
                if port is not None:
                    lines.append(f"{prefix} destination port '{port}'")
            lines.append(
                f"set zone-policy zone {_zone(outgoing)} from {_zone(incoming)} firewall name {ruleset}"
            )
        commands[name] = lines
    return commands, report
