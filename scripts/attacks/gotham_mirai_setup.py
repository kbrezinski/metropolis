#!/usr/bin/env python3
"""Metropolis adapter for the non-executable steps of Gotham's run_mirai.py.

Reference: https://github.com/xsaga/gotham-iot-testbed/blob/master/src/run_mirai.py

Gotham selects vulnerable Telnet nodes, configures their login service, and
starts a Mirai binary in a bot container. Metropolis declares its Telnet target
in the device inventory and configures it when its Docker image starts. This
adapter resolves those inventory roles and checks their GNS3 nodes. Starting
Metropolis's example inert bot is optional; users may supply their own bot.
It never installs or launches malware.

The router scripts have a route from the attack subnet to the plant target.
The example bot has an empty scan target list by default, so starting it only
produces its own check-in. Verify the route in a running GNS3 lab separately.
"""

from __future__ import annotations

from _common import Run, cli, gns3_client, inventory, ipv4, parser

DEFAULT_PROJECT = "metropolis_water_treatment_v1"
DEFAULT_TARGET = "MET-LEGACY-GATEWAY-01"
DEFAULT_BOT = "MET-MIRAI-BOT-01"


def resolve_roles(
    devices: list[dict], target_names: list[str], bot_name: str, require_example: bool
):
    """Map Gotham's vulnerable-device and bot roles to declared instances."""
    by_name = {item["name"]: item for item in devices}
    targets = []
    for name in target_names:
        device = by_name.get(name)
        if device is None or "telnet" not in device["protocols"]:
            raise ValueError(f"{name} is not an inventoried Telnet target")
        if not device.get("environment", {}).get("LEGACY_ROOT_PASSWORD"):
            raise ValueError(f"{name} has no configured lab root credential")
        targets.append(device)
    bot = by_name.get(bot_name)
    if require_example and bot is None:
        raise ValueError(f"{bot_name} is not an inventoried inert Mirai bot node")
    if bot is not None and "mirai-bot" not in bot["protocols"]:
        raise ValueError(f"{bot_name} is not an inventoried inert Mirai bot node")
    return targets, bot


def main() -> int:
    options = parser(__doc__, DEFAULT_BOT)
    options.add_argument("--project", default=DEFAULT_PROJECT)
    options.add_argument("--target-node", action="append", dest="targets")
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
    if args.host or args.port is not None:
        raise ValueError(
            "Use inventoried node addresses; --host/--port are unsupported"
        )

    target_names = args.targets or [DEFAULT_TARGET]
    if len(set(target_names)) != len(target_names):
        raise ValueError("Target node names must be unique")
    targets, bot = resolve_roles(
        inventory(args.inventory), target_names, args.node, args.start_inert_bot
    )
    target_addresses = [ipv4(item["address"].split("/")[0]) for item in targets]
    bot_address = ipv4(bot["address"].split("/")[0]) if bot else None

    with Run(
        args,
        "gotham_mirai_setup",
        project=args.project,
        telnet_targets=[
            {
                "name": item["name"],
                "address": address,
                "port": 23,
                "username": "root",
                "credential_source": "LEGACY_ROOT_PASSWORD",
            }
            for item, address in zip(targets, target_addresses, strict=True)
        ],
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
        required = list(target_names)
        if args.start_inert_bot:
            assert bot is not None
            required.append(bot["name"])
        missing = [name for name in required if name not in nodes]
        if missing:
            raise RuntimeError(f"GNS3 project is missing: {', '.join(missing)}")

        for target in targets:
            node = nodes[target["name"]]
            run.emit(
                "telnet_target",
                name=target["name"],
                status=node.get("status"),
                configured_by="legacy_gateway/start_telnet.sh",
                username="root",
                credential_source="LEGACY_ROOT_PASSWORD",
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
                "Telnet service is configured by the gateway image. No bot binary "
                "is installed or launched; the user supplies bot behavior."
            ),
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(cli(main))
