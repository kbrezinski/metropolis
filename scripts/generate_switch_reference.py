#!/usr/bin/env python3
"""Generate the switch reference from the topology data.

The eight per-switch Markdown specifications used to restate what
``links.yaml`` already held, in prose that no code read. Eight files that drive
nothing drift, so they are gone: this writes one document from the same data the
builder uses, which means it cannot contradict it.

What is derived and what is written:

* the port number is the position of the role in ``attachment_points``
* the mode is the link kind — ``trunk`` or access
* the VLANs and the far end come from the link
* the description of each far end is written into ``links.yaml``

Usage::

    uv run python scripts/generate_switch_reference.py --check
    uv run python scripts/generate_switch_reference.py --write
    uv run python scripts/generate_switch_reference.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "testbeds/metropolis/datasets/water_treatment_v1"
OUTPUT = ROOT / "testbeds/metropolis/switch/README.md"

HEADER = """# Metropolis switch reference

**Generated file. Do not edit by hand.**

Written by `scripts/generate_switch_reference.py` from
`device_instances/infrastructure.yaml` and `topology/links.yaml`, so it cannot
disagree with the topology the builder creates. To change it, change the data
and regenerate.

Each switch is a GNS3 Ethernet switch appliance. Port numbers come from the
order of `attachment_points` in the infrastructure inventory, so the port
numbering below is exactly what the build uses.
"""

FOOTER = """
## Notes

- A port marked *spare* has no cable in `links.yaml`. It exists in the switch
  specification as room to extend, and nothing is attached to it.
- Trunk links carry the listed VLANs tagged; access links carry one untagged
  VLAN.
- VLAN numbers and subnets come from `topology/address-plan.yaml`. The router
  trunks must carry the same VLANs listed here, and
  `scripts/validate_metropolis_topology.py` checks that they do.
- Firewall policy and IPsec are router-layer concerns and are not configured.
  Switch VLAN membership separates traffic; it does not filter it.
"""


def load() -> tuple[dict, list[dict]]:
    infrastructure = yaml.safe_load(
        (DATASET / "device_instances/infrastructure.yaml").read_text(encoding="utf-8")
    )
    links = yaml.safe_load(
        (DATASET / "topology/links.yaml").read_text(encoding="utf-8")
    )["links"]
    return infrastructure, links


def index_ports(links: list[dict]) -> dict[tuple[str, str], dict]:
    """(switch, port_role) -> the link that uses that port."""
    indexed: dict[tuple[str, str], dict] = {}
    for link in links:
        for endpoint in link["endpoints"]:
            if "port_role" in endpoint:
                indexed[(endpoint["node"], endpoint["port_role"])] = link
    return indexed


def far_end(link: dict, switch_name: str) -> dict:
    """The endpoint at the other end of the link, as a mapping."""
    for endpoint in link["endpoints"]:
        if endpoint["node"] != switch_name:
            return endpoint
    return {}


def describe_far_end(endpoint: dict, link: dict) -> str:
    """Name the device, its interface, and what the link says it is."""
    node = endpoint.get("node", "?")
    interface = endpoint.get("interface")
    label = f"`{node}`" + (f" {interface}" if interface else "")
    if endpoint.get("port_role"):
        label += f" ({endpoint['port_role']})"
    description = link.get("description")
    return f"{label} — {description}" if description else label


def render() -> str:
    infrastructure, links = load()
    ports = index_ports(links)

    parts = [HEADER]
    for switch in infrastructure["switches"]:
        parts.append(f"\n## `{switch['name']}`\n\n")
        if switch.get("role"):
            parts.append(f"{switch['role']}.\n")
        parts.append(
            "\n| Port | Role | Mode | VLANs | Far end |\n|---|---|---|---|---|\n"
        )
        for number, role in enumerate(switch["attachment_points"]):
            link = ports.get((switch["name"], role))
            if link is None:
                parts.append(f"| {number} | `{role}` | spare | — | nothing cabled |\n")
                continue
            trunk = link.get("kind") == "trunk"
            mode = "trunk" if trunk else "access"
            vlans = link.get("vlans")
            vlan_text = ", ".join(str(v) for v in vlans) if vlans else "untagged"
            endpoint = far_end(link, switch["name"])
            parts.append(
                f"| {number} | `{role}` | {mode} | {vlan_text} | "
                f"{describe_far_end(endpoint, link)} |\n"
            )

    parts.append(FOOTER)
    return "".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if the file on disk is not what would be generated",
    )
    parser.add_argument(
        "--write", action="store_true", help="Write the file (the default)"
    )
    args = parser.parse_args()

    generated = render()

    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if current != generated:
            print(
                f"{OUTPUT.relative_to(ROOT)} is out of date. "
                "Run: uv run python scripts/generate_switch_reference.py --write",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT.relative_to(ROOT)} is up to date")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(generated, encoding="utf-8")
    switches = len(load()[0]["switches"])
    print(f"Wrote {OUTPUT.relative_to(ROOT)}: {switches} switches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
