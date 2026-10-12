#!/usr/bin/env python3
"""Check the Metropolis roles needed for a user-supplied inert Merlin agent.

Gotham's run_merlin.py starts a listener and runs a Merlin agent executable in
a container. Metropolis provides a synthetic C2 node with an operator console
and an HTTP agent channel. This adapter checks the inventory and GNS3 nodes;
it does not install, transfer, or run an agent executable. Starting the
repository's example inert agent is optional.
"""

from __future__ import annotations

from _common import Run, cli, gns3_client, inventory, ipv4, parser

DEFAULT_PROJECT = "metropolis_water_treatment_v1"

DEFAULT_CNC = "MET-MERLIN-CNC-01"
DEFAULT_AGENT = "MET-MERLIN-AGENT-01"


def configured_port(device: dict, key: str) -> int:
    value = int(device.get("environment", {}).get(key, 0))
    if not 1 <= value <= 65535:
        raise ValueError(f"{device['name']} needs a valid {key}")
    return value


def resolve_roles(
    devices: list[dict], cnc_name: str, agent_name: str, require_example: bool
):
    by_name = {item["name"]: item for item in devices}
    cnc = by_name.get(cnc_name)
    agent = by_name.get(agent_name)
    if cnc is None or not {"merlin-control", "merlin-agent"}.issubset(cnc["protocols"]):
        raise ValueError(f"{cnc_name} is not an inventoried Merlin C2 node")
    if require_example and agent is None:
        raise ValueError(f"{agent_name} is not an inventoried inert Merlin agent")
    if agent is not None and "merlin-agent" not in agent["protocols"]:
        raise ValueError(f"{agent_name} is not an inventoried inert Merlin agent")
    control_port = configured_port(cnc, "MERLIN_CONTROL_PORT")
    agent_port = configured_port(cnc, "MERLIN_HTTP_PORT")
    host = ipv4(cnc["address"].split("/")[0])
    expected_url = f"http://{host}:{agent_port}"
    if (
        agent is not None
        and agent.get("environment", {}).get("MERLIN_CNC_URL") != expected_url
    ):
        raise ValueError(f"{agent_name} MERLIN_CNC_URL must be {expected_url}")
    agent_host = ipv4(agent["address"].split("/")[0]) if agent else None
    return cnc, agent, host, agent_host, control_port, agent_port


def main() -> int:
    options = parser(__doc__, DEFAULT_CNC)
    options.add_argument("--project", default=DEFAULT_PROJECT)
    options.add_argument("--example-agent-node", default=DEFAULT_AGENT)
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
    if args.host or args.port is not None:
        raise ValueError(
            "Use inventoried node addresses; --host/--port are unsupported"
        )

    cnc, agent, host, agent_host, control_port, agent_port = resolve_roles(
        inventory(args.inventory),
        args.node,
        args.example_agent_node,
        args.start_inert_agent,
    )
    with Run(
        args,
        "gotham_merlin_setup",
        project=args.project,
        cnc={"name": cnc["name"], "address": host},
        operator_console={"host": host, "port": control_port, "transport": "tcp"},
        agent_channel={
            "url": f"http://{host}:{agent_port}",
            "transport": "http_without_tls",
            "checkin": "POST /checkin",
            "poll": "GET /commands/{agent_id}",
            "upload": "POST /upload/{agent_id}",
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
