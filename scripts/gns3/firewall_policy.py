"""Compile dataset flows into VyOS 1.3 zone-policy commands, without GNS3.

Every routed flow is installed at each router it crosses. A flow within one
subnet bypasses the routers and is deliberately omitted from the policy.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, ip_interface, ip_network
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
ADDRESS = re.compile(
    r"^set interfaces ethernet (eth\d+)(?: vif (\d+))? address '([^']+)'$"
)
OSPF_NETWORK = re.compile(r"^set protocols ospf area 0 network '([^']+)'$")
OSPF_ACTIVE = re.compile(r"^set protocols ospf passive-interface-exclude '([^']+)'$")
MANAGED_ZONE = re.compile(r"\bzone-policy zone (MET_[A-Za-z0-9_]+)\b")
MANAGED_RULESET = re.compile(r"\bfirewall name (MET_[A-Za-z0-9_]+)\b")


@dataclass(frozen=True)
class Flow:
    name: str
    source: str
    destination: str
    protocol: str
    port: int | None


@dataclass
class Router:
    name: str
    interfaces: dict[str, IPv4Interface]
    advertised: set[IPv4Network]
    ospf_active: set[str]


def _routers(infrastructure: dict) -> dict[str, Router]:
    routers = {}
    for declared in infrastructure["routers"]:
        name = declared["name"]
        path = ROOT / declared["config"]
        interfaces: dict[str, IPv4Interface] = {}
        advertised: set[IPv4Network] = set()
        ospf_active: set[str] = set()
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
        if not interfaces:
            raise ValueError(f"{name}: router script has no interfaces")
        connected = {address.network for address in interfaces.values()}
        if advertised != connected:
            raise ValueError(
                f"{name}: OSPF advertised networks do not match connected networks: "
                f"missing {sorted(str(item) for item in connected - advertised)}, "
                f"extra {sorted(str(item) for item in advertised - connected)}"
            )
        routers[name] = Router(name, interfaces, advertised, ospf_active)
    return routers


def _flow(record: dict) -> Flow:
    try:
        name = record["id"]
        source = record["source"]
        destination = record["destination"]
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
    if not all(
        isinstance(value, str) and value for value in (name, source, destination)
    ):
        raise ValueError(f"{name}: flow identifiers must be nonempty strings")
    return Flow(name, source, destination, protocol, port)


def parse_attack_exception(value: str) -> Flow:
    """Parse SOURCE:DESTINATION:PROTOCOL:PORT; use '-' for ICMP's port."""
    parts = value.split(":")
    if len(parts) != 4:
        raise ValueError("attack exception must be SOURCE:TARGET:tcp|udp|icmp:PORT")
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

    Policy endpoints are inventory names, except a router may originate a flow.
    Attack exceptions must originate on the declared attack-lab network.
    """
    document = yaml.safe_load(
        (dataset / "topology/firewall-policy.yaml").read_text(encoding="utf-8")
    )
    if document.get("schema_version") != "1.0" or document.get(
        "testbed_id"
    ) != inventory.get("testbed_id"):
        raise ValueError("firewall policy version or testbed does not match inventory")
    routers = _routers(infrastructure)
    devices = {item["name"]: item for item in inventory["devices"]}
    if len(devices) != len(inventory["devices"]):
        raise ValueError("duplicate device name in inventory")
    baseline = [_flow(item) for item in document["flows"]]
    exceptions = attack_exceptions or []
    if len({flow.name for flow in baseline}) != len(baseline):
        raise ValueError("duplicate firewall flow ID")
    address_plan = yaml.safe_load(
        (dataset / "topology/address-plan.yaml").read_text(encoding="utf-8")
    )
    link_plan = yaml.safe_load((dataset / "topology/links.yaml").read_text("utf-8"))
    links = link_plan["links"]
    cabled_nodes = {
        endpoint["node"] for link in links for endpoint in link["endpoints"]
    }
    switch_names = {
        item["name"]
        for group in ("switches", "segments")
        for item in infrastructure.get(group, [])
    }
    attack_networks = [
        ip_network(item["cidr"])
        for item in address_plan["networks"]
        if item["id"] == "attack-lab"
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
            pairs[name].setdefault(("local", interface), set()).add(
                (str(address.ip), "0.0.0.0/0", "89", None)
            )
    report = {"baseline_flows": [], "attack_exceptions": [], "local_subnet_flows": []}
    for flow in baseline + exceptions:
        if flow.destination not in devices:
            raise ValueError(
                f"{flow.name}: destination {flow.destination} is not inventoried"
            )
        if flow.destination not in cabled_nodes or (
            flow.source in devices and flow.source not in cabled_nodes
        ):
            raise ValueError(f"{flow.name}: source or destination has no declared link")
        if flow.source in devices:
            source_ip = ip_interface(devices[flow.source]["address"]).ip
        elif flow.source in routers:
            source_ip = None
        else:
            raise ValueError(f"{flow.name}: source {flow.source} is not inventoried")
        destination_ip = ip_interface(devices[flow.destination]["address"]).ip
        if flow in exceptions:
            if source_ip is None or source_ip not in attack_networks[0]:
                raise ValueError(f"{flow.name}: attack source is not on attack-lab")
            if destination_ip in attack_networks[0]:
                raise ValueError(f"{flow.name}: attack target is on attack-lab")
        elif attack_networks and (
            (source_ip is not None and source_ip in attack_networks[0])
            or destination_ip in attack_networks[0]
        ):
            raise ValueError(
                f"{flow.name}: baseline policy cannot permit attack-lab traffic"
            )
        try:
            path, resolved_source = _path(
                routers, adjacency, flow.source, source_ip, destination_ip
            )
        except ValueError as exc:
            raise ValueError(f"{flow.name}: {exc}") from exc
        if path and source_ip is not None:
            try:
                reverse, _ = _path(
                    routers, adjacency, flow.destination, destination_ip, source_ip
                )
            except ValueError as exc:
                raise ValueError(f"{flow.name}: return path: {exc}") from exc
            if reverse != [
                (name, outgoing, incoming)
                for name, incoming, outgoing in reversed(path)
            ]:
                raise ValueError(f"{flow.name}: return route is asymmetric")
        for (first, _, outgoing), (second, incoming, _) in zip(path, path[1:]):
            if not _cabled_between(
                links, switch_names, first, outgoing, second, incoming
            ):
                raise ValueError(
                    f"{flow.name}: no declared cable from {first} {outgoing} "
                    f"to {second} {incoming}"
                )
        record = {
            "id": flow.name,
            "source": flow.source,
            "destination": flow.destination,
            "protocol": flow.protocol,
            "port": flow.port,
            "routers": [name for name, _, _ in path],
        }
        category = "attack_exceptions" if flow in exceptions else "baseline_flows"
        report[category].append(record)
        if not path:
            report["local_subnet_flows"].append(flow.name)
            continue
        for name, incoming, outgoing in path:
            allowed = pairs[name].setdefault((incoming, outgoing), set())
            allowed.add(
                (str(resolved_source), str(destination_ip), flow.protocol, flow.port)
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
