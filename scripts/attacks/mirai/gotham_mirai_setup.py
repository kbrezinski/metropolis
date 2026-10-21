#!/usr/bin/env python3
"""Metropolis adapter for the non-executable steps of Gotham's run_mirai.py.

Reference: https://github.com/xsaga/gotham-iot-testbed/blob/master/src/run_mirai.py

Gotham selects vulnerable Telnet nodes, configures their login service, and
starts a Mirai binary in a bot container. Metropolis declares its Telnet target
in the device inventory and configures it when its Docker image starts. This
adapter resolves those inventory roles and checks their GNS3 nodes. Starting
Metropolis's example inert bot is optional; users may supply their own bot.
It never installs or launches malware.

The bundled dataset routes the attack subnet to its plant target. Custom
datasets must supply their own route and service configuration. The bundled
example bot has an empty scan target list and only checks in by default.
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

DEFAULT_BOT = "MET-MIRAI-BOT-01"


def resolve_roles(
    devices: list[dict],
    target_names: list[str],
    bot_name: str | None,
    require_example: bool,
):
    """Map Gotham's vulnerable-device and bot roles to declared instances."""
    by_name = {item["name"]: item for item in devices}
    targets = []
    for name in target_names:
        device = by_name.get(name)
        if device is None or "telnet" not in device["protocols"]:
            raise ValueError(f"{name} is not an inventoried Telnet target")
        targets.append(device)
    bot = by_name.get(bot_name)
    if require_example and bot is None:
        raise ValueError(f"{bot_name} is not an inventoried inert Mirai bot node")
    if bot is not None and "mirai-bot" not in bot["protocols"]:
        raise ValueError(f"{bot_name} is not an inventoried inert Mirai bot node")
    return targets, bot


def main() -> int:
    options = parser(__doc__, DEFAULT_BOT)
    options.add_argument(
        "--project", help="GNS3 project (defaults to the selected dataset)"
    )
    options.add_argument("--target-node", action="append", dest="targets")
    options.add_argument("--cnc-node", help="Mirai control node")
    options.add_argument("--listener-node", help="Mirai report listener")
    options.add_argument("--loader-node", help="HTTP payload loader")
    bot_choice = options.add_mutually_exclusive_group()
    bot_choice.add_argument(
        "--start-inert-bot",
        action="store_true",
        help="Use the repository's example inert bot: start its GNS3 node if needed",
    )
    bot_choice.add_argument(
        "--external-bot",
        action="store_true",
        help=(
            "Use your own inert bot on a lab node; print target details "
            "without uploading or launching a binary"
        ),
    )
    args = options.parse_args()
    args.project = args.project or project_name_for(args)
    if args.host or args.port is not None:
        raise ValueError(
            "Use inventoried node addresses; --host/--port are unsupported"
        )

    records = inventory(args.inventory)
    target_names = args.targets or [
        item["name"] for item in records if "telnet" in item.get("protocols", [])
    ]
    if not target_names:
        raise ValueError("No Telnet targets exist in the selected inventory")
    if len(set(target_names)) != len(target_names):
        raise ValueError("Target node names must be unique")
    bot_candidates = [
        item["name"] for item in records if "mirai-bot" in item.get("protocols", [])
    ]
    if args.start_inert_bot:
        bot_name = select_device(args, "mirai-bot", preferred=DEFAULT_BOT)["name"]
    else:
        bot_name = args.node or (
            DEFAULT_BOT
            if DEFAULT_BOT in bot_candidates
            else bot_candidates[0]
            if len(bot_candidates) == 1
            else None
        )
    targets, bot = resolve_roles(records, target_names, bot_name, args.start_inert_bot)
    cnc = select_device(
        args,
        "mirai-cnc",
        name=args.cnc_node,
        preferred="MET-MIRAI-CNC-01",
        use_args_node=False,
    )
    listener = select_device(
        args,
        "mirai-report",
        name=args.listener_node,
        exclude={cnc["name"]},
        preferred="MET-MIRAI-LISTENER-01",
        use_args_node=False,
        selection_option="--listener-node",
    )
    loader = select_device(
        args,
        "payload-delivery",
        name=args.loader_node,
        preferred="MET-MIRAI-LOADER-WGET-01",
        env_equals=("LOADER_KIND", "wget"),
        use_args_node=False,
        selection_option="--loader-node",
    )
    target_addresses = [ipv4(item["address"].split("/")[0]) for item in targets]
    bot_address = ipv4(bot["address"].split("/")[0]) if bot else None
    telnet_targets = []
    for item, address in zip(targets, target_addresses, strict=True):
        environment = item.get("environment", {})
        port = int(environment.get("TELNET_PORT", 23))
        if not 1 <= port <= 65535:
            raise ValueError(f"{item['name']} has an invalid TELNET_PORT")
        telnet_targets.append(
            {
                "name": item["name"],
                "address": address,
                "port": port,
                "username": environment.get("TELNET_USERNAME", "root"),
                "credential_source": (
                    "LEGACY_ROOT_PASSWORD"
                    if environment.get("LEGACY_ROOT_PASSWORD")
                    else "unspecified"
                ),
            }
        )

    with Run(
        args,
        "gotham_mirai_setup",
        project=args.project,
        cnc={"name": cnc["name"], "address": ipv4(cnc["address"].split("/")[0])},
        listener={
            "name": listener["name"],
            "address": ipv4(listener["address"].split("/")[0]),
        },
        loader={
            "name": loader["name"],
            "address": ipv4(loader["address"].split("/")[0]),
        },
        telnet_targets=telnet_targets,
        bot={"name": bot["name"], "address": bot_address} if bot else None,
        bot_source=(
            "example"
            if args.start_inert_bot
            else "user_supplied"
            if args.external_bot
            else "unspecified"
        ),
        starts_inert_bot=args.start_inert_bot,
        launches_external_bot=False,
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
        required = [*target_names, cnc["name"], listener["name"], loader["name"]]
        if args.start_inert_bot:
            assert bot is not None
            required.append(bot["name"])
        missing = [name for name in required if name not in nodes]
        if missing:
            raise RuntimeError(f"GNS3 project is missing: {', '.join(missing)}")

        for target in telnet_targets:
            node = nodes[target["name"]]
            run.emit(
                "telnet_target",
                name=target["name"],
                status=node.get("status"),
                configured_by="device inventory and node image",
                username=target["username"],
                port=target["port"],
                credential_source=target["credential_source"],
            )
            if node.get("status") != "started":
                raise RuntimeError(f"Telnet target {target['name']} is not started")

        if args.start_inert_bot:
            assert bot is not None
            bot_node = nodes[bot["name"]]
            if bot_node.get("status") != "started":
                client.start_node(project.project_id, bot_node["node_id"])
                run.emit("inert_bot_start_requested", name=bot["name"])
            else:
                run.emit("inert_bot_status", name=bot["name"], status="started")

        run.emit(
            "summary",
            telnet_targets=len(targets),
            note=(
                "Check live Telnet service configuration on the selected nodes. "
                "No bot binary is installed or launched by this setup command."
            ),
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(cli(main))
