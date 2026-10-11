#!/usr/bin/env python3
"""Build a small topology in code, check it, draw it, and write it out.

This is the whole loop in one place: describe a network, prove it is not
contradictory, look at it, and turn it into the dataset documents the GNS3
builder and validator read.

    uv run python examples/build_a_topology.py --draw
    uv run python examples/build_a_topology.py --write /tmp/demo_v1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from metropolis.topology import (  # noqa: E402
    Interface,
    Network,
    Router,
    Switch,
    SwitchPort,
    Topology,
    TopologyError,
)


def build() -> Topology:
    """A two-cell plant: one router, one switch, a PLC and a sensor."""
    lab = Topology(
        testbed_id="demo",
        description="Two-cell demonstration plant",
        infrastructure_description="Routers and switches of the demo topology.",
        links_description="Point-to-point links and node attachments.",
    )

    # Subnets first. Each one is an address range with a gateway.
    lab.add(
        Network(
            "cell-a",
            "10.0.10.0/24",
            name="Cell A",
            zone="levels_0_1",
            vlan_id=10,
            gateway="10.0.10.1",
            purpose="Intake cell.",
        ),
        Network(
            "cell-b",
            "10.0.11.0/24",
            name="Cell B",
            zone="levels_0_1",
            vlan_id=11,
            gateway="10.0.11.1",
            purpose="Dosing cell.",
        ),
        Network(
            "ops",
            "10.0.20.0/24",
            name="Operations",
            zone="level_3",
            vlan_id=20,
            gateway="10.0.20.1",
            purpose="SCADA and services.",
        ),
        Network(
            "transit",
            "10.0.254.0/30",
            name="Core transit",
            zone="routing_transit",
            kind="transit",
        ),
    )

    # The router trunk carries the cell and operations VLANs.
    lab.add(
        Router(
            "R-EDGE",
            config="testbeds/metropolis/router/backbone/router_ot_core.sh",
            role="Edge router for both cells",
            interfaces=(
                Interface("eth0", cidr="10.0.254.1/30", description="Transit to core"),
                Interface(
                    "eth1",
                    kind="trunk",
                    vlans=(10, 11, 20),
                    description="802.1Q trunk to the cell switch",
                ),
            ),
        ),
        # A switch's ports are named by role. The port number is the position
        # in this list, so putting a role first makes it port 0. `with_ports`
        # lets each port state its mode up front; the VLANs still come from the
        # link plugged into it.
        Switch.with_ports(
            "sw-cells",
            (
                SwitchPort("router-uplink", mode="trunk"),
                SwitchPort("cell-a-uplink", mode="access"),
                SwitchPort("cell-b-uplink", mode="access"),
                SwitchPort("ops-uplink", mode="access"),
            ),
            role="Cell aggregation",
        ),
    )

    # Hosts. With auto_network the gateway and the NODE_* values the container
    # entrypoint applies are derived, so identity is stated once. Leaving the
    # address out allocates the next free one in the subnet.
    lab.add_device(
        "PLC-A",
        model="testbeds/metropolis/devices/controllers/plc",
        image="metropolis/controller:dev",
        site="treatment_plant",
        subnet="cell-a",
        address="10.0.10.10/24",
        protocols=("modbus-tcp",),
        auto_network=True,
    )
    lab.device("PLC-A").environment["DEVICE_ROLE"] = "PLC"

    lab.add_device(
        "SENSOR-A",
        image="metropolis/mqtt-sensor:dev",
        site="treatment_plant",
        subnet="cell-a",
        protocols=("mqtt", "coap"),
        auto_network=True,
    )
    lab.device("SENSOR-A").environment["PLC_HOST"] = "10.0.10.10"

    lab.add_device(
        "SCADA-1",
        image="metropolis/scada:dev",
        site="treatment_plant",
        subnet="ops",
        protocols=("modbus-tcp", "mqtt"),
        auto_network=True,
    )

    # Cables. An endpoint may be written "NODE:eth1" or "NODE:port-role".
    # Naming the network is enough: the VLAN comes from the network's vlan_id,
    # and the kind follows from how many VLANs that is.
    lab.link(
        "link-edge-switch",
        "R-EDGE:eth1",
        "sw-cells:router-uplink",
        kind="trunk",
        vlans=(10, 11, 20),
        description="Edge trunk to the cell switch",
    )
    lab.link(
        "link-sw-plc",
        "sw-cells:cell-a-uplink",
        "PLC-A:eth0",
        network_id="cell-a",
        description="PLC on the cell A segment",
    )
    lab.link(
        "link-sw-sensor",
        "sw-cells:ops-uplink",
        "SENSOR-A:eth0",
        network_id="cell-a",
        description="Sensor reachable from the cell A segment",
    )
    return lab


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--draw", action="store_true", help="Print mermaid source")
    parser.add_argument("--dot", action="store_true", help="Print Graphviz source")
    parser.add_argument(
        "--image", metavar="FILE", help="Write an image of the topology (.png/.svg)"
    )
    parser.add_argument("--write", metavar="DIR", help="Write the dataset documents")
    parser.add_argument(
        "--break-it",
        action="store_true",
        help="Move a device onto the wrong subnet, to show the checks working",
    )
    args = parser.parse_args()

    lab = build()
    if args.break_it:
        lab.device("PLC-A").subnet = "10.0.11.0/24"

    print(lab.summary())

    problems = lab.problems()
    print()
    if problems:
        print(f"{len(problems)} problem(s):")
        for problem in problems:
            print("  -", problem)
    else:
        print("No contradictions found.")

    if args.draw:
        print()
        print(lab.to_mermaid("Demo plant"))
    if args.dot:
        print()
        print(lab.to_dot("Demo plant"))
    if args.image:
        from metropolis.topology.draw import available, render

        if not available():
            print(
                "Writing an image needs matplotlib and networkx:\n"
                "  uv pip install matplotlib networkx",
                file=sys.stderr,
            )
            return 2
        print()
        print(f"wrote {render(lab, args.image, title='Demo plant')}")
    if args.write:
        for path in lab.save(args.write):
            print(f"wrote {path}")

    try:
        lab.validate()
    except TopologyError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
