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
STATIC_ROUTE = re.compile(r"^set protocols static route (\S+) next-hop '([^']+)'$")
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
    routes: list[tuple[IPv4Network, IPv4Address]]

    def route(self, destination: IPv4Address) -> tuple[str, IPv4Address | None]:
        candidates: list[tuple[int, int, str, IPv4Address | None]] = []
        for interface, address in self.interfaces.items():
            if destination in address.network:
                candidates.append((address.network.prefixlen, 1, interface, None))
        for network, next_hop in self.routes:
            if destination not in network:
                continue
            outgoing = [
                name
                for name, address in self.interfaces.items()
                if next_hop in address.network
            ]
            if len(outgoing) != 1:
                raise ValueError(
                    f"{self.name}: next hop {next_hop} is not on one interface"
                )
            candidates.append((network.prefixlen, 0, outgoing[0], next_hop))
        if not candidates:
            raise ValueError(f"{self.name}: no route to {destination}")
        _, _, interface, next_hop = max(candidates)
        return interface, next_hop


def _routers(infrastructure: dict) -> dict[str, Router]:
    routers = {}
    for declared in infrastructure["routers"]:
        name = declared["name"]
        path = ROOT / declared["config"]
        interfaces: dict[str, IPv4Interface] = {}
        routes = []
        for line in path.read_text(encoding="utf-8").splitlines():
            match = ADDRESS.fullmatch(line.strip())
            if match:
                base, vlan, address = match.groups()
                interfaces[f"{base}.{vlan}" if vlan else base] = ip_interface(address)
            match = STATIC_ROUTE.fullmatch(line.strip())
            if match:
                network, next_hop = match.groups()
                routes.append((ip_network(network), IPv4Address(next_hop)))
        if not interfaces:
            raise ValueError(f"{name}: router script has no interfaces")
        routers[name] = Router(name, interfaces, routes)
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
    source_name: str,
    source_ip: IPv4Address | None,
    destination: IPv4Address,
) -> tuple[list[tuple[str, str, str]], IPv4Address]:
    if source_ip is None:
        router_name, incoming = source_name, "local"
    else:
        router_name, incoming = _owner(routers, source_ip)
    path = []
    visited = set()
    while router_name not in visited:
        visited.add(router_name)
        router = routers[router_name]
        outgoing, next_hop = router.route(destination)
        if source_ip is None:
            source_ip = router.interfaces[outgoing].ip
        if incoming == outgoing:
            if not path and next_hop is None:
                return [], source_ip
            raise ValueError(f"{router_name}: flow enters and leaves {incoming}")
        path.append((router_name, incoming, outgoing))
        if next_hop is None:
            return path, source_ip
        next_routers = [
            (other.name, interface)
            for other in routers.values()
            if other.name != router_name
            for interface, configured in other.interfaces.items()
            if configured.ip == next_hop
        ]
        if len(next_routers) != 1:
            raise ValueError(f"{router_name}: next hop {next_hop} is not one router")
        router_name, incoming = next_routers[0]
    raise ValueError(f"route to {destination} loops at {router_name}")


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
                routers, flow.source, source_ip, destination_ip
            )
        except ValueError as exc:
            raise ValueError(f"{flow.name}: {exc}") from exc
        if path and source_ip is not None:
            try:
                reverse, _ = _path(routers, flow.destination, destination_ip, source_ip)
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
            for number, (source, destination, protocol, port) in enumerate(
                sorted(allowed), 100
            ):
                prefix = f"set firewall name {ruleset} rule {number}"
                lines += [
                    f"{prefix} action 'accept'",
                    f"{prefix} source address '{source}'",
                    f"{prefix} destination address '{destination}'",
                    f"{prefix} protocol '{protocol}'",
                    f"{prefix} state new 'enable'",
                ]
                if port is not None:
                    lines.append(f"{prefix} destination port '{port}'")
            lines.append(
                f"set zone-policy zone {_zone(outgoing)} from {_zone(incoming)} firewall name {ruleset}"
            )
        commands[name] = lines
    return commands, report
