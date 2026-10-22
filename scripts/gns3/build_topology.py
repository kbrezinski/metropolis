#!/usr/bin/env python3
"""Create the Metropolis project, nodes, and links in GNS3 from the declared topology.

Reads the address plan, device inventory, infrastructure inventory, and link
plan, and makes the server match them. Safe to rerun: existing nodes and links
are reused, so a build that stopped partway resumes.

Examples::

    run.py build_topology --plan                # show what would be created
    run.py build_topology --plan --write-ports  # also print the derived switch ports
    run.py build_topology                       # create it
    run.py build_topology --prune-missing       # also delete nodes not in the plan
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _cli import (
    DATASET,
    add_appliance_arguments,
    add_server_arguments,
    connect,
    load_documents,
    load_plan,
    report_skipped,
    router_scripts,
)
from builder import build, port_map_yaml
from client import Gns3Client
from collect_device_logs import docker_host_mapping
from firewall_policy import compile_policy
from router_config import (
    DEFAULT_PASSWORD,
    DEFAULT_USERNAME,
    configure_project_routers,
)
from templates import TEMPLATES


def template_ids(client: Gns3Client) -> dict[str, str]:
    """Template name to id, for every template the plan needs."""
    return {item["name"]: item["template_id"] for item in client.templates()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    add_server_arguments(parser)
    add_appliance_arguments(parser)
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Print the plan and exit without contacting GNS3",
    )
    parser.add_argument(
        "--prune-missing",
        action="store_true",
        help="Delete project nodes the plan does not contain",
    )
    parser.add_argument(
        "--docker-host",
        action="append",
        default=[],
        metavar="COMPUTE_ID=URI",
        help="Docker engine on each switch compute; repeat for remote computes",
    )
    parser.add_argument(
        "--write-ports",
        type=Path,
        help="Write the derived switch port map to this path",
    )
    parser.add_argument(
        "--configure-routers",
        action="store_true",
        help="Load each router's config script onto it (needs the routers running)",
    )
    parser.add_argument(
        "--router-username",
        default=DEFAULT_USERNAME,
        help="Console account for the VyOS routers",
    )
    parser.add_argument(
        "--router-password",
        default=DEFAULT_PASSWORD,
        help="Console password for the VyOS routers",
    )
    args = parser.parse_args()

    topology, inventory = load_plan(
        router_template=args.router_template, switch_template=args.switch_template
    )
    firewall_commands = None
    infrastructure = None
    if args.configure_routers:
        _, _, infrastructure, _ = load_documents()
        try:
            firewall_commands, _ = compile_policy(DATASET, infrastructure, inventory)
        except ValueError as exc:
            parser.error(f"firewall policy: {exc}")

    if args.plan:
        print(f"{len(topology.nodes)} nodes, {len(topology.links)} links")
        for kind in ("router", "switch", "device"):
            names = [node.name for node in topology.nodes if node.kind == kind]
            print(f"  {kind}: {len(names)}")
        by_template: dict[str, int] = {}
        for node in topology.nodes:
            by_template[node.template_name] = by_template.get(node.template_name, 0) + 1
        for name, count in sorted(by_template.items()):
            print(f"    {count:>3}  {name}")
        report_skipped(topology.skipped)
        if args.write_ports:
            args.write_ports.write_text(port_map_yaml(topology, args.project), "utf-8")
            print(f"port map written to {args.write_ports}")
        return 0

    client = connect(args)
    try:
        docker_hosts = docker_host_mapping(args.docker_host)
    except ValueError as exc:
        parser.error(str(exc))

    missing = [
        node.name
        for node in topology.nodes
        if node.template_name not in {item.name for item in TEMPLATES}
        and node.kind == "device"
    ]
    if missing:
        for name in missing:
            print(f"error: {name} has no registered template", file=sys.stderr)
        return 2

    result = build(
        client,
        args.project,
        topology,
        template_ids(client),
        prune_missing=args.prune_missing,
        compute_id=args.compute_id,
        docker_hosts=docker_hosts,
    )
    switch_failed = any(
        item["status"] == "failed" for item in result.switch_configuration.values()
    )
    print(f"project {args.project}")
    print(
        f"  {len(result.created_nodes)} nodes created, {len(result.reused_nodes)} reused"
    )
    print(
        f"  {len(result.created_links)} links created, {len(result.reused_links)} reused"
    )
    report_skipped(result.skipped)
    if result.switch_configuration:
        verified = sum(
            item["status"] != "failed" for item in result.switch_configuration.values()
        )
        print(
            f"  switch VLAN configuration: {verified}/{len(result.switch_configuration)} verified"
        )
        for name, item in sorted(result.switch_configuration.items()):
            if item["status"] == "failed":
                print(f"  unconfigured: {name}: {item['reason']}", file=sys.stderr)

    if args.write_ports:
        args.write_ports.write_text(port_map_yaml(topology, args.project), "utf-8")
        print(f"port map written to {args.write_ports}")

    if args.configure_routers:
        assert infrastructure is not None
        scripts = router_scripts(infrastructure)
        project = client.project_by_name(args.project)
        if project is None:
            print(f"error: project {args.project} is missing", file=sys.stderr)
            return 2
        results = configure_project_routers(
            client,
            project.project_id,
            scripts,
            username=args.router_username,
            password=args.router_password,
            firewall_commands=firewall_commands,
        )
        failed = 0
        for router_result in results:
            if router_result.error:
                failed += 1
                print(f"  {router_result.node}: {router_result.error}", file=sys.stderr)
                continue
            note = (
                f", {len(router_result.missing)} address(es) not reported"
                if router_result.missing
                else ""
            )
            for missing in router_result.missing:
                print(
                    f"  {router_result.node}: {missing} is not configured",
                    file=sys.stderr,
                )
            print(
                f"  {router_result.node}: {router_result.commands} commands applied{note}"
            )
        print(f"{len(results) - failed}/{len(results)} routers configured")
        return 1 if failed or switch_failed else 0
    return 1 if switch_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
