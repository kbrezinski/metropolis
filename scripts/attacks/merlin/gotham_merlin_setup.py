#!/usr/bin/env python3
"""Check the Metropolis roles needed for a user-supplied inert Merlin agent.

Gotham's run_merlin.py starts a listener and runs a Merlin agent executable in
a container. Metropolis provides a synthetic C2 node with an operator console
and an HTTPS agent channel. This adapter checks the inventory and GNS3 nodes;
it does not install, transfer, or run an agent executable. Starting the
repository's example inert agent is optional.
"""

from __future__ import annotations

from _common import (
    Run,
    cli,
    gns3_client,
    inventory,
    ipv4,
    parser,
    project_name_for,
    select_device,
)

DEFAULT_CNC = "MET-MERLIN-CNC-01"
DEFAULT_AGENT = "MET-MERLIN-AGENT-01"


def configured_port(device: dict, key: str, override: int | None = None) -> int:
    value = int(override or device.get("environment", {}).get(key, 0))
    if not 1 <= value <= 65535:
        raise ValueError(f"{device['name']} needs a valid {key}")
    return value


def resolve_roles(
    devices: list[dict],
    cnc_name: str,
    agent_name: str | None,
    require_example: bool,
    control_override: int | None = None,
    http_override: int | None = None,
):
    by_name = {item["name"]: item for item in devices}
    cnc = by_name.get(cnc_name)
    agent = by_name.get(agent_name)
    if cnc is None or "merlin-control" not in cnc["protocols"]:
        raise ValueError(f"{cnc_name} is not an inventoried Merlin C2 node")
    if require_example and agent is None:
        raise ValueError(f"{agent_name} is not an inventoried inert Merlin agent")
    if agent is not None and "merlin-agent" not in agent["protocols"]:
        raise ValueError(f"{agent_name} is not an inventoried inert Merlin agent")
    control_port = configured_port(cnc, "MERLIN_CONTROL_PORT", control_override)
    agent_port = configured_port(cnc, "MERLIN_HTTP_PORT", http_override)
    host = ipv4(cnc["address"].split("/")[0])
    expected_url = f"https://{host}:{agent_port}"
    if agent is not None:
        configured_url = agent.get("environment", {}).get("MERLIN_CNC_URL")
        if configured_url and configured_url != expected_url:
            raise ValueError(f"{agent_name} MERLIN_CNC_URL must be {expected_url}")
        if require_example and not configured_url:
            raise ValueError(f"{agent_name} needs MERLIN_CNC_URL to start")
    agent_host = ipv4(agent["address"].split("/")[0]) if agent else None
    return cnc, agent, host, agent_host, control_port, agent_port


def built_agent_url(node: dict) -> str | None:
    """Read a GNS3 Docker node's configured URL when the API returns it."""
    raw = node.get("properties", {}).get("environment")
    if not isinstance(raw, str):
        return None
    for line in raw.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "MERLIN_CNC_URL":
            return value.strip().strip('"')
    return None


def main() -> int:
    options = parser(__doc__, DEFAULT_CNC)
    options.add_argument(
        "--project", help="GNS3 project (defaults to the selected dataset)"
    )
    options.add_argument("--example-agent-node", help="Inert Merlin agent node")
    options.add_argument("--control-port", type=int)
    options.add_argument("--http-port", type=int)
    agent_choice = options.add_mutually_exclusive_group()
    agent_choice.add_argument(
        "--start-inert-agent",
        action="store_true",
        help="Use the repository's example inert agent: start its GNS3 node if needed",
    )
    agent_choice.add_argument(
        "--external-agent",
        action="store_true",
        help=(
            "Use your own inert agent on a lab node; print the connection "
            "contract without uploading or launching a binary"
        ),
    )
    args = options.parse_args()
    args.project = args.project or project_name_for(args)
    if args.host or args.port is not None:
        raise ValueError(
            "Use inventoried node addresses; --host/--port are unsupported"
        )

    records = inventory(args.inventory)
    cnc_name = select_device(args, "merlin-control", preferred=DEFAULT_CNC)["name"]
    agent_candidates = [
        item["name"]
        for item in records
        if "merlin-agent" in item.get("protocols", []) and item["name"] != cnc_name
    ]
    if args.start_inert_agent:
        agent_name = select_device(
            args,
            "merlin-agent",
            name=args.example_agent_node,
            preferred=DEFAULT_AGENT,
            exclude={cnc_name},
            use_args_node=False,
            selection_option="--example-agent-node",
        )["name"]
    else:
        agent_name = args.example_agent_node or (
            DEFAULT_AGENT
            if DEFAULT_AGENT in agent_candidates
            else agent_candidates[0]
            if len(agent_candidates) == 1
            else None
        )
    cnc, agent, host, agent_host, control_port, agent_port = resolve_roles(
        records,
        cnc_name,
        agent_name,
        args.start_inert_agent,
        args.control_port,
        args.http_port,
    )
    with Run(
        args,
        "gotham_merlin_setup",
        project=args.project,
        cnc={"name": cnc["name"], "address": host},
        operator_console={"host": host, "port": control_port, "transport": "tcp"},
        agent_channel={
            "url": f"https://{host}:{agent_port}",
            "transport": "verified_https",
            "ca_file": "certs/merlin/ca.crt",
            "checkin": "POST /checkin",
            "poll": "GET /commands/{agent_id}",
            "transfer": "GET /artifact/{agent_id}",
            "result": "POST /result/{agent_id}",
        },
        example_agent={"name": agent["name"], "address": agent_host} if agent else None,
        agent_source=(
            "example"
            if args.start_inert_agent
            else "user_supplied"
            if args.external_agent
            else "unspecified"
        ),
        starts_inert_agent=args.start_inert_agent,
        launches_external_agent=False,
    ) as run:
        if args.dry_run:
            return 0

        client = gns3_client()
        project = client.project_by_name(args.project)
        if project is None or project.status != "opened":
            raise RuntimeError(f"Open GNS3 project {args.project!r} before running")
        nodes = {
            node["name"]: node for node in client.project_nodes(project.project_id)
        }
        required = [cnc["name"]]
        if args.start_inert_agent:
            assert agent is not None
            required.append(agent["name"])
        missing = [name for name in required if name not in nodes]
        if missing:
            raise RuntimeError(f"GNS3 project is missing: {', '.join(missing)}")

        cnc_node = nodes[cnc["name"]]
        run.emit("cnc_status", name=cnc["name"], status=cnc_node.get("status"))
        if cnc_node.get("status") != "started":
            raise RuntimeError(f"Merlin C2 node {cnc['name']} is not started")

        if args.start_inert_agent:
            assert agent is not None
            agent_node = nodes[agent["name"]]
            actual_url = built_agent_url(agent_node)
            expected_url = f"https://{host}:{agent_port}"
            if actual_url is not None and actual_url != expected_url:
                raise RuntimeError(
                    f"GNS3 node {agent['name']} still uses {actual_url!r}; "
                    f"update its environment to {expected_url!r} or recreate the node"
                )
            if agent_node.get("status") != "started":
                client.start_node(project.project_id, agent_node["node_id"])
                run.emit("inert_agent_start_requested", name=agent["name"])
            else:
                run.emit("inert_agent_status", name=agent["name"], status="started")

        run.emit(
            "summary",
            note=(
                "C2 node is started in GNS3. Socket reachability and agent "
                "check-in need a live test from the agent subnet."
            ),
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(cli(main))
