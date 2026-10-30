"""Build, validate, export, and draw OT network topologies from Python.

The Metropolis testbed is described by four YAML documents under a dataset
directory. They are the authoritative record, and the scripts that build a lab
in GNS3 read them. This module is the library form of the same thing: you build a
topology as Python objects, check it, write it back out as those documents, and
draw it to see how the routers and switches connect.

Building one reads as a description of the network, because the connections and
the VLAN assignments are the interesting part:

    from metropolis.topology import Device, Network, Router, Switch, Topology

    lab = Topology(testbed_id="metropolis", description="Two-cell plant")
    lab.add(Network("cell-a", cidr="10.0.10.0/24", vlan_id=10))
    lab.add(Network("cell-b", cidr="10.0.11.0/24", vlan_id=11))
    lab.add(Router("R-EDGE", config="router_edge.sh", interfaces=(
        Interface("eth0", cidr="10.0.254.1/30"),
        Interface("eth1", kind="trunk", vlans=(10, 11)),
    )))
    lab.add(Switch("sw-a", attachment_points=("router-uplink", "cell-a-uplink")))
    lab.add(Device("PLC-1", image="metropolis/controller:dev",
                   subnet="10.0.10.0/24", address="10.0.10.10/24"))
    lab.link("link-r-a", "R-EDGE:eth1", "sw-a:router-uplink",
             kind="trunk", vlans=(10, 11), description="Trunk to cell A")
    lab.link("link-a-plc", "sw-a:cell-a-uplink", "PLC-1:eth0", kind="access")

    lab.validate()                      # raises on anything contradictory
    lab.to_yaml()                       # the four documents as one mapping
    lab.save("testbeds/metropolis/datasets/two_cell_v1")
    lab.to_dot()                        # draw it with Graphviz

The YAML documents are generated, not hand-written, so the same topology can be
produced reliably and changed by changing code. Comments in hand-written YAML
are not preserved when a topology is loaded and written again; the data is.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

import yaml

SCHEMA_VERSION = "1.0"

# Keys whose short lists read better inline, the way the checked-in datasets are
# written: `protocols: [mqtt, coap]` rather than one entry per line. Everything
# else is a block sequence, so records stay readable in a diff.
INLINE_SEQUENCE_KEYS = frozenset(
    {"protocols", "vlans", "attachment_points", "keywords"}
)
INLINE_MAPPING_KEYS = frozenset({"environment"})

# Zone ordering for drawing: lower levels first, so a picture reads from the
# physical equipment up towards the business network.
DRAW_ORDER = (
    "levels_0_1",
    "level_2",
    "level_3",
    "level_3_5",
    "level_4",
    "lab_test",
    "routing_transit",
    "transport",
)


def _empty_flow() -> list:
    """An empty list that YAML renders as ``[]`` rather than nothing."""
    return []


class TopologyError(ValueError):
    """The topology is contradictory and cannot be built as described."""


# -- nodes --------------------------------------------------------------------


@dataclass
class Network:
    """One subnet: an address range, its gateway, and optionally a VLAN."""

    id: str
    cidr: str
    name: str | None = None
    site: str | None = None
    zone: str | None = None
    kind: str = "lan"
    gateway: str | None = None
    vlan_id: int | None = None
    purpose: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _without_none(
            {
                "id": self.id,
                "name": self.name,
                "site": self.site,
                "zone": self.zone,
                "kind": self.kind,
                "cidr": self.cidr,
                "gateway": self.gateway,
                "vlan_id": self.vlan_id,
                "purpose": self.purpose,
            }
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Network:
        return cls(
            id=data["id"],
            cidr=data["cidr"],
            name=data.get("name"),
            site=data.get("site"),
            zone=data.get("zone"),
            kind=data.get("kind", "lan"),
            gateway=data.get("gateway"),
            vlan_id=data.get("vlan_id"),
            purpose=data.get("purpose"),
        )


@dataclass
class Interface:
    """An Ethernet interface, VLAN trunk, or logical IPsec VTI."""

    name: str
    cidr: str | None = None
    kind: str | None = None
    vlans: tuple[int, ...] = ()
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        body = _without_none({"name": self.name, "kind": self.kind, "cidr": self.cidr})
        if self.vlans:
            body["vlans"] = list(self.vlans)
        if self.description:
            body["description"] = self.description
        return body

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Interface:
        return cls(
            name=data["name"],
            cidr=data.get("cidr"),
            kind=data.get("kind"),
            vlans=tuple(data.get("vlans") or ()),
            description=data.get("description"),
        )


@dataclass
class Router:
    """A routing node, configured by a script."""

    name: str
    config: str
    kind: str = "vyos"
    role: str | None = None
    notes: str | None = None
    interfaces: tuple[Interface, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _without_none(
            {
                "name": self.name,
                "kind": self.kind,
                "role": self.role,
                "config": self.config,
                "notes": self.notes,
                "interfaces": [item.to_dict() for item in self.interfaces],
            }
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Router:
        return cls(
            name=data["name"],
            config=data["config"],
            kind=data.get("kind", "vyos"),
            role=data.get("role"),
            notes=data.get("notes"),
            interfaces=tuple(
                Interface.from_dict(item) for item in data.get("interfaces", ())
            ),
        )


@dataclass
class SwitchPort:
    """One port on a switch.

    ``mode`` and ``vlans`` may be left out, which is the usual case: what a port
    actually carries is decided by the link plugged into it, and reading them
    back falls through to that link. They can be stated here when you want to.
    """

    role: str
    mode: str | None = None
    vlans: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"role": self.role}
        if self.mode:
            body["mode"] = self.mode
        if self.vlans:
            body["vlans"] = list(self.vlans)
        return body

    @classmethod
    def parse(cls, value: Any) -> SwitchPort:
        """Accept ``"router-uplink"`` or a mapping with a ``role``."""
        if isinstance(value, SwitchPort):
            return value
        if isinstance(value, str):
            return cls(role=value)
        if isinstance(value, Mapping):
            if "role" not in value:
                raise TopologyError(f"switch port {value!r} needs a 'role'")
            return cls(
                role=value["role"],
                mode=value.get("mode"),
                vlans=tuple(value.get("vlans") or ()),
            )
        raise TopologyError(f"cannot read switch port {value!r}")


class Ports(Sequence[SwitchPort]):
    """A switch's ports, in order. A port's number is its position.

    Both access styles work, because both are natural for a port table::

        switch.ports[0].vlans        # attribute access
        switch.ports[0]["vlans"]     # mapping access

    Comparing against plain role names also works, so the YAML's list of strings
    still compares equal.
    """

    __slots__ = ("_items", "_topology", "_switch")

    def __init__(self, items: Iterable[Any] = ()) -> None:
        self._items: tuple[SwitchPort, ...] = tuple(
            SwitchPort.parse(item) for item in items
        )
        self._topology: Topology | None = None
        self._switch: str | None = None

    def __getitem__(self, index):
        if isinstance(index, slice):
            return Ports(self._items[index])
        return _PortView(self._items[index], self)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return (_PortView(port, self) for port in self._items)

    def bind(self, topology: Topology, switch: str) -> Ports:
        """Remember which topology and switch these ports belong to.

        This lets a port read the link plugged into it, so ``vlans`` and
        ``mode`` can be answered from the cabling. Done in place, so the switch
        that owns this list sees it too.
        """
        self._topology = topology
        self._switch = switch
        return self

    @property
    def topology(self) -> Topology | None:
        return self._topology

    @property
    def switch(self) -> str | None:
        return self._switch

    def index(self, value: Any) -> int:
        """The position of a role."""
        role = value.role if isinstance(value, (SwitchPort, _PortView)) else value
        for position, port in enumerate(self._items):
            if port.role == role:
                return position
        raise ValueError(f"{role!r} is not a port on this switch")

    def __contains__(self, value: Any) -> bool:
        """Membership by role name.

        Spelled out because the sequence default would compare a port object
        against the name and never match.
        """
        role = value.role if isinstance(value, (SwitchPort, _PortView)) else value
        return any(port.role == role for port in self._items)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Ports):
            return self._items == other._items
        if isinstance(other, (list, tuple)):
            return list(self._items) == [SwitchPort.parse(item) for item in other]
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._items)

    def __repr__(self) -> str:
        return f"Ports({[port.role for port in self._items]})"

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(port.role for port in self._items)

    def to_strings(self) -> list[str]:
        """The YAML form: roles in order, which is what the dataset stores."""
        return [port.role for port in self._items]

    def to_dicts(self) -> list[dict[str, Any]]:
        """The full form, including a mode or VLANs given explicitly."""
        return [port.to_dict() for port in self._items]


class _PortView(Mapping[str, Any]):
    """A port that reads as either an object or a mapping.

    Reading ``vlans`` or ``mode`` falls back to whatever the port is cabled to,
    so a port answers the question people actually ask: what does this carry?
    """

    __slots__ = ("_port", "_ports")

    def __init__(self, port: SwitchPort, ports: Ports | None = None) -> None:
        self._port = port
        self._ports = ports

    @property
    def role(self) -> str:
        return self._port.role

    @property
    def mode(self) -> str | None:
        if self._port.mode is not None:
            return self._port.mode
        link = self.link
        if link is None:
            return None
        if not link.vlans:
            # Nothing is carried, so there is no mode to report. A link's kind
            # defaults to "access", which would otherwise claim one.
            return None
        return "trunk" if link.kind == "trunk" else "access"

    @property
    def vlans(self) -> tuple[int, ...]:
        if self._port.vlans:
            return self._port.vlans
        link = self.link
        return tuple(link.vlans) if link is not None else ()

    @property
    def link(self) -> Link | None:
        """The link plugged into this port, if any."""
        ports = self._ports
        if ports is None or ports.topology is None or ports.switch is None:
            return None
        for link in ports.topology.links:
            for endpoint in link.endpoints:
                if (
                    endpoint.node == ports.switch
                    and endpoint.port_role == self._port.role
                ):
                    return link
        return None

    def __getitem__(self, key: str) -> Any:
        if key not in {"role", "mode", "vlans", "link"}:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self):
        return iter(("role", "mode", "vlans", "link"))

    def __len__(self) -> int:
        return 4

    def __eq__(self, other: object) -> bool:
        """Equal to another port with the same role, or to the role itself."""
        if isinstance(other, _PortView):
            return self.role == other.role
        if isinstance(other, SwitchPort):
            return self.role == other.role
        if isinstance(other, str):
            return self.role == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.role)

    def __repr__(self) -> str:
        return (
            f"SwitchPort(role={self.role!r}, mode={self.mode!r}, vlans={self.vlans!r})"
        )

    def __str__(self) -> str:
        # A port is named by its role, so f-strings and joins give the role.
        return self.role


@dataclass
class Switch:
    """A managed switch. A port's number is its position in ``ports``.

    The port list can be given as ``ports`` or, matching the dataset's field
    name, as ``attachment_points``. Both accept plain role names or mappings.
    """

    name: str
    ports: Ports = field(default_factory=Ports)
    kind: str = "ethernet-switch"
    role: str | None = None
    notes: str | None = None

    def __init__(
        self,
        name: str,
        ports: Iterable[Any] = (),
        kind: str = "ethernet-switch",
        role: str | None = None,
        notes: str | None = None,
        *,
        attachment_points: Iterable[Any] | None = None,
    ) -> None:
        self.name = name
        # `attachment_points` is the dataset's name for the same list.
        self.ports = Ports(ports if attachment_points is None else attachment_points)
        self.kind = kind
        self.role = role
        self.notes = notes

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "attachment_points":
            object.__setattr__(self, "ports", Ports(value))
            return
        if name == "ports" and not isinstance(value, Ports):
            value = Ports(value)
        object.__setattr__(self, name, value)

    @property
    def attachment_points(self) -> Ports:
        """The name the dataset uses for the port list, for compatibility."""
        return self.ports

    def port_number(self, role: str) -> int:
        """The port a role is cabled to, which is its position in the list."""
        try:
            return self.ports.index(role)
        except ValueError:
            raise TopologyError(
                f"switch {self.name!r} has no port role {role!r}; "
                f"it has {list(self.ports.roles)}"
            ) from None

    def to_dict(self) -> dict[str, Any]:
        body = _without_none(
            {
                "name": self.name,
                "kind": self.kind,
                "role": self.role,
                "notes": self.notes,
            }
        )
        # Roles only, in order. A port's VLANs live on the link that uses it, so
        # writing them here as well would be a second source of truth.
        body["attachment_points"] = self.ports.to_strings()
        return body

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Switch:
        ports = data.get("ports") or data.get("attachment_points") or ()
        return cls(
            name=data["name"],
            ports=Ports(ports),
            kind=data.get("kind", "ethernet-switch"),
            role=data.get("role"),
            notes=data.get("notes"),
        )

    @classmethod
    def with_ports(cls, name: str, ports: Iterable[Any], **kwargs: Any) -> Switch:
        """Build a switch stating each port's mode and VLANs up front."""
        return cls(name=name, ports=Ports(ports), **kwargs)


@dataclass
class Segment:
    """An access switch that is not a managed node: cabling into it is not modelled."""

    name: str
    role: str | None = None
    network_id: str | None = None
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _without_none(
            {
                "name": self.name,
                "role": self.role,
                "network_id": self.network_id,
                "notes": self.notes,
            }
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Segment:
        return cls(
            name=data["name"],
            role=data.get("role"),
            network_id=data.get("network_id"),
            notes=data.get("notes"),
        )


@dataclass
class Device:
    """A host: a container image plus the settings it starts with.

    Set ``auto_network=True`` and the gateway, plus the ``NODE_HOSTNAME``,
    ``NODE_IP`` and ``NODE_GATEWAY`` values the container entrypoint applies, are
    filled in from the subnet and address and kept in step with them.
    """

    name: str
    image: str | None = None
    model: str | None = None
    subnet: str | None = None
    address: str | None = None
    gateway: str | None = None
    site: str | None = None
    protocols: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=dict)
    notes: str | None = None
    link_policy: str | None = None
    auto_network: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _without_none(
            {
                "name": self.name,
                "model": self.model,
                "image": self.image,
                "site": self.site,
                "subnet": self.subnet,
                "address": self.address,
                "gateway": self.gateway,
                "protocols": list(self.protocols),
                "notes": self.notes,
                "link_policy": self.link_policy,
                "environment": dict(self.environment),
            }
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Device:
        return cls(
            name=data["name"],
            image=data.get("image"),
            model=data.get("model"),
            subnet=data.get("subnet"),
            address=data.get("address"),
            gateway=data.get("gateway"),
            site=data.get("site"),
            protocols=tuple(data.get("protocols") or ()),
            environment=dict(data.get("environment") or {}),
            notes=data.get("notes"),
            link_policy=data.get("link_policy"),
        )

    def refresh_network(self, gateway: str | None = None) -> Device:
        """Re-apply the derived gateway and ``NODE_*`` values.

        Called after changing ``address``, ``subnet`` or ``name``, so the
        settings the container entrypoint applies stay in step with the record.
        """
        if not self.auto_network:
            return self
        if gateway is not None:
            self.gateway = gateway
        if self.gateway:
            self.environment["NODE_GATEWAY"] = self.gateway
        if self.address:
            self.environment["NODE_IP"] = self.address
        self.environment["NODE_HOSTNAME"] = self.name
        return self


@dataclass
class Endpoint:
    """One end of a link: a node, plus either an interface or a switch port role."""

    node: str
    interface: str | None = None
    port_role: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _without_none(
            {
                "node": self.node,
                "interface": self.interface,
                "port_role": self.port_role,
            }
        )

    @classmethod
    def parse(cls, value: Any) -> Endpoint:
        """Accept ``"NODE:eth0"``, ``"NODE:role"``, or a mapping.

        A suffix beginning with ``eth`` or ``vti`` is an interface; anything else is a
        switch port role.
        """
        if isinstance(value, Endpoint):
            return value
        if isinstance(value, str):
            node, _, suffix = value.partition(":")
            if not suffix:
                return cls(node=node)
            if suffix.startswith(("eth", "vti")):
                return cls(node=node, interface=suffix)
            return cls(node=node, port_role=suffix)
        if isinstance(value, Mapping):
            return cls(
                node=value["node"],
                interface=value.get("interface"),
                port_role=value.get("port_role"),
            )
        raise TopologyError(f"cannot read link endpoint {value!r}")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Endpoint:
        return cls(
            node=data["node"],
            interface=data.get("interface"),
            port_role=data.get("port_role"),
        )


@dataclass
class Link:
    """A physical cable or logical overlay between two endpoints."""

    id: str
    first: Endpoint
    second: Endpoint
    kind: str = "access"
    description: str | None = None
    network_id: str | None = None
    cidr: str | None = None
    vlans: tuple[int, ...] = ()
    status: str | None = None
    notes: str | None = None

    @property
    def endpoints(self) -> tuple[Endpoint, Endpoint]:
        return (self.first, self.second)

    def other(self, node: str) -> Endpoint:
        """The far end from ``node``."""
        if self.first.node == node:
            return self.second
        if self.second.node == node:
            return self.first
        raise TopologyError(f"link {self.id!r} does not touch {node!r}")

    def to_dict(self) -> dict[str, Any]:
        body = _without_none(
            {
                "id": self.id,
                "kind": self.kind,
                "description": self.description,
                "network_id": self.network_id,
                "cidr": self.cidr,
                "status": self.status,
            }
        )
        if self.vlans:
            body["vlans"] = list(self.vlans)
        body["endpoints"] = [self.first.to_dict(), self.second.to_dict()]
        if self.notes:
            body["notes"] = self.notes
        return body

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Link:
        endpoints = [Endpoint.from_dict(item) for item in data["endpoints"]]
        if len(endpoints) != 2:
            raise TopologyError(
                f"link {data.get('id')!r} needs two endpoints, found {len(endpoints)}"
            )
        return cls(
            id=data["id"],
            first=endpoints[0],
            second=endpoints[1],
            kind=data.get("kind", "access"),
            description=data.get("description"),
            network_id=data.get("network_id"),
            cidr=data.get("cidr"),
            vlans=tuple(data.get("vlans") or ()),
            status=data.get("status"),
            notes=data.get("notes"),
        )


# -- the topology itself ------------------------------------------------------


@dataclass
class Topology:
    """A whole testbed network: subnets, nodes, and the cables between them."""

    testbed_id: str = "metropolis"
    description: str | None = None
    infrastructure_description: str | None = None
    devices_description: str | None = None
    links_description: str | None = None
    networks: list[Network] = field(default_factory=list)
    routers: list[Router] = field(default_factory=list)
    switches: list[Switch] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    devices: list[Device] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)

    # -- adding things --------------------------------------------------------

    def add(self, *items: Any) -> Topology:
        """Add networks and nodes. Links go through :meth:`link`."""
        for item in items:
            if isinstance(item, Network):
                self.networks.append(item)
            elif isinstance(item, Router):
                self.routers.append(item)
            elif isinstance(item, Switch):
                item.ports.bind(self, item.name)
                self.switches.append(item)
            elif isinstance(item, Segment):
                self.segments.append(item)
            elif isinstance(item, Device):
                self.devices.append(item)
            elif isinstance(item, Link):
                self.links.append(item)
            else:
                raise TopologyError(f"cannot add a {type(item).__name__} to a topology")
        return self

    def add_network(self, network_id: str, cidr: str, **kwargs: Any) -> Network:
        network = Network(id=network_id, cidr=cidr, **kwargs)
        self.networks.append(network)
        return network

    def add_device(
        self,
        name: str,
        *,
        subnet: str | None = None,
        address: str | None = None,
        auto_network: bool = False,
        **kwargs: Any,
    ) -> Device:
        """Add a host, resolving the subnet and allocating an address.

        ``subnet`` may be a network id from the address plan or a CIDR. Leave
        ``address`` out and the lowest free host address in the subnet is used.
        With ``auto_network=True`` the gateway and the ``NODE_*`` values are
        derived too, so identity is stated once and kept consistent.
        """
        resolved = self._resolve_subnet(subnet) if subnet else None
        if address is None and resolved is not None:
            address = self.allocate_address(resolved)
        device = Device(
            name=name,
            subnet=resolved,
            address=address,
            auto_network=auto_network,
            **kwargs,
        )
        if auto_network:
            device.refresh_network(self._gateway_for(resolved))
        self.devices.append(device)
        return device

    def allocate_address(self, subnet: str) -> str:
        """The lowest free host address in a subnet, as CIDR.

        Used addresses are those already handed out in this topology. Hosts are
        numbered from the bottom; the gateway is skipped whatever its number.
        """
        network = ipaddress.ip_network(subnet, strict=False)
        if network.prefixlen >= network.max_prefixlen - 1:
            raise TopologyError(
                f"{subnet} has no usable host addresses to allocate from"
            )
        taken = {
            ipaddress.ip_interface(device.address).ip
            for device in self.devices
            if device.address
        }
        gateway = self._gateway_for(subnet)
        if gateway:
            taken.add(ipaddress.ip_address(gateway))
        for candidate in network.hosts():
            if candidate not in taken:
                return f"{candidate}/{network.prefixlen}"
        raise TopologyError(f"{subnet} has no free host addresses left")

    def _resolve_subnet(self, subnet: str) -> str:
        """A network id or a CIDR, resolved to the CIDR the plan records."""
        if "/" in subnet:
            return str(ipaddress.ip_network(subnet, strict=False))
        for network in self.networks:
            if network.id == subnet:
                return network.cidr
        raise TopologyError(
            f"no network with id {subnet!r}; add it with add_network first, "
            "or pass a CIDR"
        )

    def _gateway_for(self, subnet: str | None) -> str | None:
        if subnet is None:
            return None
        for network in self.networks:
            if network.cidr == subnet:
                return network.gateway
        return None

    def add_switch(self, name: str, **kwargs: Any) -> Switch:
        switch = Switch(name=name, **kwargs)
        switch.ports.bind(self, name)
        self.switches.append(switch)
        return switch

    def add_segment(self, name: str, **kwargs: Any) -> Segment:
        segment = Segment(name=name, **kwargs)
        self.segments.append(segment)
        return segment

    def link(
        self,
        link_id: str,
        first: Any,
        second: Any,
        *,
        kind: str | None = None,
        vlans: Iterable[int] | None = None,
        description: str | None = None,
        network_id: str | None = None,
        cidr: str | None = None,
    ) -> Link:
        """Connect two endpoints.

        Each endpoint may be written ``"NODE:eth1"``, ``"NODE:port-role"``, a
        bare node name, or a mapping with explicit keys.

        Name a ``network_id`` and the VLANs are taken from that network's
        ``vlan_id``, so a port's membership is stated in one place.
        """
        inferred = vlans
        if inferred is None and network_id is not None:
            for network in self.networks:
                if network.id == network_id and network.vlan_id is not None:
                    inferred = (network.vlan_id,)
                    break
        selected_vlans = tuple(inferred or ())
        selected_kind = kind or ("trunk" if len(selected_vlans) > 1 else "access")

        created = Link(
            id=link_id,
            first=Endpoint.parse(first),
            second=Endpoint.parse(second),
            kind=selected_kind,
            vlans=selected_vlans,
            description=description,
            network_id=network_id,
            cidr=cidr,
        )
        self.links.append(created)
        return created

    # -- reading --------------------------------------------------------------

    @classmethod
    def from_directory(cls, directory: str | Path) -> Topology:
        """Read a dataset directory, as written by :meth:`save`."""
        root = Path(directory)
        paths = {
            "address-plan": root / "topology/address-plan.yaml",
            "links": root / "topology/links.yaml",
            "initial-devices": root / "device_instances/initial_devices.yaml",
            "infrastructure": root / "device_instances/infrastructure.yaml",
        }
        missing = [str(path) for path in paths.values() if not path.is_file()]
        if missing:
            raise FileNotFoundError(
                "dataset is missing " + ", ".join(missing) + f" (under {root})"
            )
        return to_topology({name: _load_yaml(path) for name, path in paths.items()})

    @classmethod
    def from_bundled(
        cls,
        testbed_id: str = "metropolis",
        dataset_version: str = "water_treatment_v1",
    ) -> Topology:
        """Read the dataset shipped with the package, or the checkout copy of it.

        No repository checkout is needed, so this works from an installed wheel.
        """
        from importlib.resources import files

        relative = {
            "address-plan": "topology/address-plan.yaml",
            "links": "topology/links.yaml",
            "initial-devices": "device_instances/initial_devices.yaml",
            "infrastructure": "device_instances/infrastructure.yaml",
        }
        documents: dict[str, dict[str, Any]] = {}
        for name, suffix in relative.items():
            parts = (
                "data",
                "testbeds",
                testbed_id,
                dataset_version,
                *suffix.split("/"),
            )
            resource = files("metropolis").joinpath(*parts)
            try:
                text = resource.read_text(encoding="utf-8")
            except FileNotFoundError:
                text = _read_from_checkout(suffix, testbed_id, dataset_version)
            documents[name] = yaml.safe_load(text)
        return to_topology(documents)

    # -- looking things up ----------------------------------------------------

    def node_names(self) -> set[str]:
        """Every name a link endpoint may legally use."""
        return (
            {item.name for item in self.routers}
            | {item.name for item in self.switches}
            | {item.name for item in self.segments}
            | {item.name for item in self.devices}
        )

    def network(self, network_id: str) -> Network:
        for item in self.networks:
            if item.id == network_id:
                return item
        raise TopologyError(f"no network with id {network_id!r}")

    def switch(self, name: str) -> Switch:
        for item in self.switches:
            if item.name == name:
                return item
        raise TopologyError(f"no switch named {name!r}")

    def ports_of(self, switch: str) -> Ports:
        """A switch's ports, able to resolve what each one carries.

        Reading ``vlans`` or ``mode`` follows the link plugged into the port, so
        the answer comes from the cabling rather than a second copy of it.
        """
        return self.switch(switch).ports.bind(self, switch)

    def port_vlans(self, switch: str, role: str) -> tuple[int, ...]:
        """The VLANs a switch port carries, from the link using it."""
        ports = self.ports_of(switch)
        return tuple(ports[ports.index(role)].vlans)

    def port_mode(self, switch: str, role: str) -> str | None:
        """Whether a switch port is a trunk or an access port."""
        ports = self.ports_of(switch)
        return ports[ports.index(role)].mode

    def interfaces_of(self, router: str) -> tuple[Interface, ...]:
        """A router's interfaces, for reading what it carries."""
        return self.router(router).interfaces

    def device(self, name: str) -> Device:
        for item in self.devices:
            if item.name == name:
                return item
        raise TopologyError(f"no device named {name!r}")

    def router(self, name: str) -> Router:
        for item in self.routers:
            if item.name == name:
                return item
        raise TopologyError(f"no router named {name!r}")

    def links_at(self, node: str) -> list[Link]:
        return [
            link for link in self.links if any(e.node == node for e in link.endpoints)
        ]

    def counts(self) -> dict[str, int]:
        return {
            "networks": len(self.networks),
            "routers": len(self.routers),
            "switches": len(self.switches),
            "segments": len(self.segments),
            "devices": len(self.devices),
            "links": len(self.links),
        }

    # -- validation -----------------------------------------------------------

    def problems(self) -> list[str]:
        """Everything contradictory, as a list of readable messages."""
        self._bind_all()
        found: list[str] = []
        found.extend(self._duplicate_names())

        network_by_id: dict[str, Network] = {}
        for network in self.networks:
            if network.id in network_by_id:
                continue
            network_by_id[network.id] = network
            try:
                ipaddress.ip_network(network.cidr, strict=False)
            except ValueError as exc:
                found.append(f"network {network.id}: bad cidr {network.cidr!r} ({exc})")
            if network.gateway is not None:
                found.extend(self._gateway_problems(network))

        found.extend(self._overlaps())
        found.extend(self._device_problems(network_by_id))
        found.extend(self._router_problems())
        found.extend(self._link_problems(network_by_id))
        return found

    def validate(self) -> Topology:
        """Raise :class:`TopologyError` if anything is contradictory."""
        problems = self.problems()
        if problems:
            joined = "\n  - ".join(problems)
            raise TopologyError(f"{len(problems)} topology problem(s):\n  - {joined}")
        return self

    def _duplicate_names(self) -> list[str]:
        seen: dict[str, str] = {}
        found: list[str] = []
        for kind, items in (
            ("network", self.networks),
            ("router", self.routers),
            ("switch", self.switches),
            ("segment", self.segments),
            ("device", self.devices),
            ("link", self.links),
        ):
            for item in items:
                name = getattr(item, "name", None) or getattr(item, "id")
                if name in seen:
                    found.append(
                        f"{kind} {name!r} is defined more than once "
                        f"(also a {seen[name]})"
                    )
                else:
                    seen[name] = kind
        return found

    def _gateway_problems(self, network: Network) -> list[str]:
        try:
            subnet = ipaddress.ip_network(network.cidr, strict=False)
            gateway = ipaddress.ip_address(network.gateway)
        except ValueError:
            return []
        if gateway not in subnet:
            return [
                f"network {network.id}: gateway {network.gateway} is outside {network.cidr}"
            ]
        return []

    def _overlaps(self) -> list[str]:
        found: list[str] = []
        parsed: list[tuple[str, Any]] = []
        for network in self.networks:
            try:
                parsed.append(
                    (network.id, ipaddress.ip_network(network.cidr, strict=False))
                )
            except ValueError:
                continue
        for index, (name, subnet) in enumerate(parsed):
            for other_name, other in parsed[index + 1 :]:
                if subnet.overlaps(other):
                    found.append(f"networks {name} and {other_name} overlap")
        return found

    def _device_problems(self, network_by_id: Mapping[str, Network]) -> list[str]:
        found: list[str] = []
        by_cidr = {network.cidr: network for network in network_by_id.values()}
        assigned_addresses: dict[
            ipaddress.IPv4Address | ipaddress.IPv6Address, str
        ] = {}
        for device in self.devices:
            if device.link_policy not in {None, "required", "intentionally_uncabled"}:
                found.append(
                    f"device {device.name}: unknown link policy {device.link_policy!r}"
                )
            address = None
            if device.address is not None:
                try:
                    address = ipaddress.ip_interface(device.address)
                except ValueError as exc:
                    found.append(
                        f"device {device.name}: bad address {device.address!r} ({exc})"
                    )
                else:
                    if previous := assigned_addresses.get(address.ip):
                        found.append(
                            f"devices {previous} and {device.name} share address {address.ip}"
                        )
                    else:
                        assigned_addresses[address.ip] = device.name
            if device.subnet is None:
                continue
            network = by_cidr.get(device.subnet)
            if network is None:
                found.append(
                    f"device {device.name}: subnet {device.subnet} is not in the address plan"
                )
            if address is None:
                continue
            if address.network != ipaddress.ip_network(device.subnet, strict=False):
                found.append(
                    f"device {device.name}: address {device.address} is not in "
                    f"{device.subnet}"
                )
            if device.gateway is not None and network is not None:
                if device.gateway != network.gateway:
                    found.append(
                        f"device {device.name}: gateway {device.gateway} does not match "
                        f"network {network.id} gateway {network.gateway}"
                    )
            for key in ("NODE_HOSTNAME", "NODE_IP", "NODE_GATEWAY"):
                if key in device.environment:
                    expected = {
                        "NODE_HOSTNAME": device.name,
                        "NODE_IP": device.address,
                        "NODE_GATEWAY": device.gateway,
                    }[key]
                    if expected is not None and device.environment[key] != expected:
                        found.append(
                            f"device {device.name}: {key} is "
                            f"{device.environment[key]!r} but should be {expected!r}"
                        )
        return found

    def _router_problems(self) -> list[str]:
        found: list[str] = []
        for router in self.routers:
            if not router.interfaces:
                found.append(f"router {router.name}: declares no interfaces")
            names = [interface.name for interface in router.interfaces]
            if len(set(names)) != len(names):
                found.append(f"router {router.name}: interface names are not unique")
            for interface in router.interfaces:
                if interface.name.startswith("vti") or interface.kind == "vti":
                    if (
                        not re.fullmatch(r"vti\d+", interface.name)
                        or interface.kind != "vti"
                        or interface.cidr is None
                        or interface.vlans
                    ):
                        found.append(
                            f"router {router.name} {interface.name}: VTI requires "
                            "a vti name, kind vti, an address, and no VLANs"
                        )
                if interface.cidr is None and interface.kind is None:
                    found.append(
                        f"router {router.name} {interface.name}: neither an address "
                        "nor a trunk"
                    )
                if interface.cidr is not None:
                    try:
                        ipaddress.ip_interface(interface.cidr)
                    except ValueError as exc:
                        found.append(
                            f"router {router.name} {interface.name}: bad cidr "
                            f"{interface.cidr!r} ({exc})"
                        )
        return found

    def _link_problems(self, network_by_id: Mapping[str, Network]) -> list[str]:
        found: list[str] = []
        known = self.node_names()
        switch_names = {item.name for item in self.switches}
        router_interfaces = {
            (router.name, interface.name): interface
            for router in self.routers
            for interface in router.interfaces
        }
        for link in self.links:
            for endpoint in link.endpoints:
                if endpoint.node not in known:
                    found.append(
                        f"link {link.id}: endpoint {endpoint.node!r} is not a node "
                        "in this topology"
                    )
                elif endpoint.node in switch_names and endpoint.port_role is not None:
                    # A switch endpoint may name a port role, or name none at all
                    # to attach to the segment as a whole.
                    switch = self.switch(endpoint.node)
                    if endpoint.port_role not in switch.attachment_points:
                        found.append(
                            f"link {link.id}: {endpoint.node} has no port role "
                            f"{endpoint.port_role!r}"
                        )
                interface = router_interfaces.get((endpoint.node, endpoint.interface))
                if link.kind == "overlay":
                    if (
                        interface is None
                        or interface.kind != "vti"
                        or endpoint.port_role
                    ):
                        found.append(
                            f"link {link.id}: overlay endpoint {endpoint.node} "
                            f"{endpoint.interface} must select a declared router VTI"
                        )
                    elif interface.cidr and link.cidr:
                        try:
                            matches = ipaddress.ip_interface(
                                interface.cidr
                            ).network == ipaddress.ip_network(link.cidr)
                        except ValueError:
                            matches = False
                        if not matches:
                            found.append(
                                f"link {link.id}: VTI {endpoint.node} "
                                f"{endpoint.interface} does not match overlay CIDR"
                            )
                elif endpoint.interface and endpoint.interface.startswith("vti"):
                    found.append(
                        f"link {link.id}: a VTI is a logical overlay, not a physical cable"
                    )
            if link.kind == "overlay":
                network = network_by_id.get(link.network_id)
                if (
                    network is None
                    or network.kind != "overlay"
                    or not link.cidr
                    or link.vlans
                ):
                    found.append(
                        f"link {link.id}: overlay requires an overlay network, CIDR, and no VLANs"
                    )
                elif network.cidr != link.cidr:
                    found.append(
                        f"link {link.id}: overlay CIDR differs from its network"
                    )
            if link.network_id is not None and link.network_id not in network_by_id:
                found.append(
                    f"link {link.id}: network {link.network_id!r} is not in the "
                    "address plan"
                )
            if link.cidr is not None:
                try:
                    ipaddress.ip_network(link.cidr, strict=False)
                except ValueError as exc:
                    found.append(f"link {link.id}: bad cidr {link.cidr!r} ({exc})")
        found.extend(self._port_problems())
        return found

    def _port_problems(self) -> list[str]:
        """A port may state its mode and VLANs, but they must be consistent."""
        self._bind_all()
        found: list[str] = []
        for switch in self.switches:
            for declared in switch.ports:
                stated = declared._port.vlans
                link = declared.link
                if stated and link is not None and tuple(link.vlans) != stated:
                    found.append(
                        f"{switch.name}/{declared.role}: declares VLANs "
                        f"{list(stated)} but its link carries {list(link.vlans)}"
                    )
                if declared._port.mode and stated:
                    expected = "trunk" if len(stated) > 1 else "access"
                    if declared._port.mode != expected:
                        found.append(
                            f"{switch.name}/{declared.role}: mode "
                            f"{declared._port.mode!r} does not match {expected!r} "
                            f"for its VLANs"
                        )
        return found

    def _bind_all(self) -> None:
        """Attach every switch's port list to this topology.

        A port reads the link plugged into it, and a port list built separately
        would not know which topology it belongs to. Done here as well as on
        insert, so a list assigned later still resolves.
        """
        for switch in self.switches:
            switch.ports.bind(self, switch.name)

    # -- serialising ----------------------------------------------------------

    def to_dicts(self) -> dict[str, dict[str, Any]]:
        """The four dataset documents as plain Python mappings.

        Each document carries its own description, because they say different
        things; a document with none omits the key rather than inheriting.
        """
        self._bind_all()
        identity: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "testbed_id": self.testbed_id,
        }

        def with_description(text: str | None) -> dict[str, Any]:
            return {"description": text} if text else {}

        return {
            "address-plan": {
                **identity,
                **with_description(self.description),
                "networks": [item.to_dict() for item in self.networks],
            },
            "infrastructure": {
                **identity,
                **with_description(self.infrastructure_description),
                "routers": [item.to_dict() for item in self.routers],
                "switches": [item.to_dict() for item in self.switches],
                "segments": [item.to_dict() for item in self.segments],
            },
            "initial-devices": {
                **identity,
                **with_description(self.devices_description),
                "devices": [item.to_dict() for item in self.devices],
            },
            "links": {
                **identity,
                **with_description(self.links_description),
                "links": [item.to_dict() for item in self.links],
            },
        }

    def to_yaml(self) -> dict[str, str]:
        """The four documents rendered as YAML text, keyed by document name."""
        return {name: _dump(document) for name, document in self.to_dicts().items()}

    def save(self, directory: str | Path) -> list[Path]:
        """Write the dataset, creating the directory layout.

        Safe to repeat: files are overwritten, so regenerating a topology does
        not leave stale documents behind.
        """
        root = Path(directory)
        written: list[Path] = []
        targets = {
            "address-plan": root / "topology/address-plan.yaml",
            "links": root / "topology/links.yaml",
            "initial-devices": root / "device_instances/initial_devices.yaml",
            "infrastructure": root / "device_instances/infrastructure.yaml",
        }
        documents = self.to_yaml()
        for name, path in targets.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(documents[name], encoding="utf-8")
            written.append(path)
        return written

    # -- drawing --------------------------------------------------------------

    def to_dot(self, title: str | None = None) -> str:
        """Graphviz source, grouped by zone with devices under their subnet."""
        lines = [
            "digraph topology {",
            '  graph [rankdir="TB", fontname="Helvetica", '
            f'label="{_escape(title or self.testbed_id)}", labelloc="t"];',
            '  node [shape="box", style="rounded", fontname="Helvetica", '
            'fontsize="10"];',
            '  edge [fontname="Helvetica", fontsize="8"];',
        ]
        lines.extend(_dot_networks(self))
        lines.extend(_dot_infrastructure(self))
        lines.append("")
        lines.extend(_dot_links(self))
        lines.append("}")
        return "\n".join(lines) + "\n"

    def to_mermaid(self, title: str | None = None) -> str:
        """Mermaid source, which renders in Markdown without a Graphviz install."""
        lines = ["graph TD"]
        if title:
            lines.append(f"  %% {title}")
        for network in self.networks:
            lines.append(
                f"  subgraph {_mermaid_id(network.id)}"
                f'["{_escape(network.name or network.id)}<br/>{network.cidr}"]'
            )
            for device in self._devices_on(network.id):
                lines.append(
                    f'    {_mermaid_id(device.name)}["{_escape(device.name)}"]'
                )
            lines.append("  end")
        for router in self.routers:
            lines.append(f'  {_mermaid_id(router.name)}["{_escape(router.name)}"]')
        for switch in self.switches:
            lines.append(
                f'  {_mermaid_id(switch.name)}{{{{"{_escape(switch.name)}"}}}}'
            )
        for link in self.links:
            label = _link_label(link)
            lines.append(
                f"  {_mermaid_id(link.first.node)} -->"
                f"|{_escape(label)}| {_mermaid_id(link.second.node)}"
            )
        return "\n".join(lines) + "\n"

    def _devices_on(self, network_id: str) -> list[Device]:
        found: list[Device] = []
        for network in self.networks:
            if network.id != network_id:
                continue
            for device in self.devices:
                if device.subnet == network.cidr:
                    found.append(device)
        return found

    def summary(self) -> str:
        """A short text description, for a terminal or a log line."""
        counts = self.counts()
        lines = [
            f"topology {self.testbed_id}: "
            + "  ".join(f"{name} {value}" for name, value in counts.items())
        ]
        if self.description:
            lines.append(self.description)
        lines.append("")
        for network in self.networks:
            vlan = f" vlan {network.vlan_id}" if network.vlan_id else ""
            attached = len(self._devices_on(network.id))
            lines.append(
                f"  {network.id:24} {network.cidr:18}{vlan:9} {attached} device(s)"
            )
        lines.append("")
        for router in self.routers:
            lines.append(
                f"  router {router.name:22} {len(router.interfaces)} interface(s)"
            )
        for switch in self.switches:
            lines.append(
                f"  switch {switch.name:22} {len(switch.attachment_points)} port(s)"
            )
        return "\n".join(lines)


# -- reading ------------------------------------------------------------------


def to_topology(documents: Mapping[str, Mapping[str, Any]]) -> Topology:
    """Build a topology from parsed dataset documents."""
    address_plan = documents["address-plan"]
    infrastructure = documents["infrastructure"]
    devices = documents["initial-devices"]
    links = documents["links"]

    for name, document in documents.items():
        declared = document.get("testbed_id")
        if declared != address_plan.get("testbed_id"):
            raise TopologyError(
                f"{name} belongs to testbed {declared!r}, but the address plan is "
                f"{address_plan.get('testbed_id')!r}"
            )

    return Topology(
        testbed_id=address_plan["testbed_id"],
        description=address_plan.get("description"),
        infrastructure_description=infrastructure.get("description"),
        devices_description=devices.get("description"),
        links_description=links.get("description"),
        networks=[Network.from_dict(item) for item in address_plan.get("networks", ())],
        routers=[Router.from_dict(item) for item in infrastructure.get("routers", ())],
        switches=[
            Switch.from_dict(item) for item in infrastructure.get("switches", ())
        ],
        segments=[
            Segment.from_dict(item) for item in infrastructure.get("segments", ())
        ],
        devices=[Device.from_dict(item) for item in devices.get("devices", ())],
        links=[Link.from_dict(item) for item in links.get("links", ())],
    )


def load_topology(directory: str | Path) -> Topology:
    """Read a dataset directory into a topology."""
    return Topology.from_directory(directory)


def load_bundled_topology(
    testbed_id: str = "metropolis",
    dataset_version: str = "water_treatment_v1",
) -> Topology:
    """Read the dataset shipped with the package, or the checkout copy of it."""
    return Topology.from_bundled(testbed_id, dataset_version)


# -- helpers ------------------------------------------------------------------


def _read_from_checkout(
    relative_path: str, testbed_id: str, dataset_version: str
) -> str:
    """Read a dataset file from a source checkout, for development."""
    repository_root = Path(__file__).resolve().parents[3]
    source = (
        repository_root
        / "testbeds"
        / testbed_id
        / "datasets"
        / dataset_version
        / relative_path
    )
    if source.is_file():
        return source.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"No bundled {relative_path} for testbed {testbed_id!r}, "
        f"dataset {dataset_version!r}"
    )


def _without_none(mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None, so optional fields stay out of the file."""
    return {key: value for key, value in mapping.items() if value is not None}


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TopologyError(f"{path} does not contain a YAML mapping")
    return loaded


class _Dumper(yaml.SafeDumper):
    """A dumper that keeps records readable and short lists inline.

    PyYAML chooses flow or block only globally, so the choice is made per key
    here instead: the keys listed above are rendered inline, everything else as
    block, which is how the checked-in datasets are written.
    """

    def represent_mapping(  # type: ignore[override]
        self, tag: str, mapping: Mapping, flow_style: bool | None = None
    ) -> yaml.Node:
        node = yaml.MappingNode(tag, [])
        self.represent_mapping_items(node, mapping)
        # The style is only meaningful on the node itself.
        node.flow_style = bool(flow_style)
        return node

    def represent_mapping_items(self, node: yaml.Node, mapping: Mapping) -> None:
        for key, value in mapping.items():
            key_node = self.represent_data(key)
            if key in INLINE_SEQUENCE_KEYS and isinstance(value, (list, tuple)):
                value_node = self.represent_sequence(
                    "tag:yaml.org,2002:seq", value, flow_style=True
                )
            elif key in INLINE_MAPPING_KEYS and isinstance(value, Mapping):
                value_node = self.represent_mapping(
                    "tag:yaml.org,2002:map", value, flow_style=True
                )
            else:
                value_node = self.represent_data(value)
            node.value.append((key_node, value_node))


def _dump(document: Mapping[str, Any]) -> str:
    """Render a document with the same shape as the hand-written files."""
    return yaml.dump(
        document,
        Dumper=_Dumper,
        sort_keys=False,
        default_flow_style=False,
        width=100,
        allow_unicode=True,
    )


def _escape(value: str) -> str:
    return str(value).replace('"', '\\"')


def _mermaid_id(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value)


def _link_label(link: Link) -> str:
    parts = [link.kind]
    if link.vlans:
        parts.append("vlan " + ",".join(str(v) for v in link.vlans))
    return " ".join(parts)


def _dot_networks(topology: Topology) -> list[str]:
    lines: list[str] = []
    ordered = sorted(
        topology.networks,
        key=lambda network: (
            DRAW_ORDER.index(network.zone)
            if network.zone in DRAW_ORDER
            else len(DRAW_ORDER),
            network.id,
        ),
    )
    for network in ordered:
        label = network.name or network.id
        detail = network.cidr
        if network.vlan_id:
            detail += f"  vlan {network.vlan_id}"
        lines.append(f"  subgraph cluster_{_mermaid_id(network.id)} {{")
        lines.append(f'    label="{_escape(label)}\\n{detail}";')
        lines.append('    style="rounded,dashed";')
        for device in topology._devices_on(network.id):
            lines.append(
                f'    "{_escape(device.name)}" '
                '[shape="box", style="rounded,filled", fillcolor="#eef"];'
            )
        lines.append("  }")
    return lines


def _dot_infrastructure(topology: Topology) -> list[str]:
    lines: list[str] = []
    for router in topology.routers:
        lines.append(
            f'  "{_escape(router.name)}" '
            '[shape="box3d", style="filled", fillcolor="#fd9"];'
        )
    for switch in topology.switches:
        lines.append(
            f'  "{_escape(switch.name)}" '
            '[shape="box", style="filled", fillcolor="#9f9"];'
        )
    for segment in topology.segments:
        lines.append(
            f'  "{_escape(segment.name)}" '
            '[shape="box", style="dashed", fillcolor="#fff"];'
        )
    return lines


def _dot_links(topology: Topology) -> list[str]:
    lines: list[str] = []
    for link in topology.links:
        parts = [_link_label(link)]
        if link.first.interface or link.first.port_role:
            parts.append(link.first.interface or link.first.port_role or "")
        if link.second.interface or link.second.port_role:
            parts.append(link.second.interface or link.second.port_role or "")
        label = " / ".join(part for part in parts if part)
        style = (
            ' [style="dashed"'
            if link.status == "planned" or link.kind == "overlay"
            else " ["
        )
        lines.append(
            f'  "{_escape(link.first.node)}" -> "{_escape(link.second.node)}"'
            f'{style} label="{_escape(label)}"];'
        )
    return lines


def iter_nodes(topology: Topology) -> Iterator[Any]:
    """Every node in a stable order: routers, then switches, then devices."""
    yield from topology.routers
    yield from topology.switches
    yield from topology.devices
    yield from topology.segments


def node_names(topology: Topology) -> Sequence[str]:
    return [item.name for item in iter_nodes(topology)]


def replace_device(topology: Topology, name: str, **changes: Any) -> Device:
    """Return a copy of a device with fields changed, for quick experiments."""
    updated = replace(topology.device(name), **changes)
    for index, device in enumerate(topology.devices):
        if device.name == name:
            topology.devices[index] = updated
            break
    return updated
