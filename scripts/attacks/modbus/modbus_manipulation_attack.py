"""Bounded Modbus writes and readback using the selected cell's action profile."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from _common import (
    DEFAULT_INVENTORY,
    Run,
    cli,
    endpoint,
    inventory,
    ipv4,
    parser,
    positive,
    schedule,
)

PROFILE_FILENAME = "modbus_actions_v1.yaml"
DEFAULT_PROFILE_FILE = (
    Path(__file__).resolve().parents[3]
    / "testbeds/metropolis/datasets/water_treatment_v1/protocol_profiles"
    / PROFILE_FILENAME
)


def checked(response):
    if response.isError():
        raise RuntimeError(f"Modbus exception response: {response}")
    return response


def read_value(client, address, unit, coil):
    if coil:
        return bool(
            checked(client.read_coils(address=address, count=1, slave=unit)).bits[0]
        )
    return checked(
        client.read_holding_registers(address=address, count=1, slave=unit)
    ).registers[0]


def write_value(client, address, value, unit, coil):
    if coil:
        checked(client.write_coil(address=address, value=value, slave=unit))
    else:
        checked(client.write_register(address=address, value=value, slave=unit))


def profile_file_for(args) -> Path:
    if args.profile_file:
        return args.profile_file.expanduser().resolve()
    if args.inventory.parent.name == "device_instances":
        local = args.inventory.parent.parent / "protocol_profiles" / PROFILE_FILENAME
        if local.is_file():
            return local.resolve()
        raise ValueError(
            f"Dataset {args.inventory.parent.parent} needs {PROFILE_FILENAME}; "
            "supply --profile-file for its Modbus register map"
        )
    # Standalone --inventory files may use the bundled map, but must declare
    # CELL_ROLE or select --profile explicitly before an action is resolved.
    return DEFAULT_PROFILE_FILE


def load_profiles(path: Path) -> tuple[dict, str, str]:
    content = path.read_bytes()
    document = yaml.safe_load(content)
    if not isinstance(document, dict) or document.get("schema_version") != "1.0":
        raise ValueError(f"{path} must use Modbus action schema version 1.0")
    version = document.get("profile_version")
    profiles = document.get("profiles")
    if (
        not isinstance(version, str)
        or not version
        or not isinstance(profiles, dict)
        or not profiles
    ):
        raise ValueError(f"{path} needs a profile_version and profiles mapping")
    for profile, entry in profiles.items():
        if not isinstance(profile, str) or not profile or not isinstance(entry, dict):
            raise ValueError(f"{path} has an invalid Modbus profile name or entry")
        actions = entry.get("actions")
        if not isinstance(actions, dict) or not actions:
            raise ValueError(f"{path} profile {profile} needs an actions mapping")
        for action, spec in actions.items():
            if not isinstance(action, str) or not action:
                raise ValueError(f"{path} profile {profile} has an invalid action name")
            validate_action_spec(profile, action, spec)
    return profiles, version, hashlib.sha256(content).hexdigest()


def inferred_profile(args, device: dict) -> str | None:
    environment = device.get("environment") or {}
    cell_role = environment.get("CELL_ROLE")
    if cell_role == "remote_site":
        site = environment.get("REMOTE_SITE_ROLE")
        if not site:
            raise ValueError(f"{device['name']} needs REMOTE_SITE_ROLE")
        return str(site)
    if cell_role:
        return str(cell_role)
    if (
        device["name"] == "MET-PLC-INTAKE-01"
        and args.inventory.resolve() == DEFAULT_INVENTORY.resolve()
        and environment.get("DEVICE_ROLE") == "PLC"
    ):
        # Only the bundled intake instance predates CELL_ROLE.
        return "intake"
    return None


def selected_profile(args, device: dict, host: str) -> str:
    declared_host = str(device["address"]).split("/")[0]
    inferred = inferred_profile(args, device)
    if host != declared_host:
        if not args.profile:
            raise ValueError(
                "An address outside the selected node needs --profile; "
                "the inventory cannot establish its register map"
            )
        return args.profile
    if args.profile and inferred and args.profile != inferred:
        raise ValueError(
            f"{device['name']} declares Modbus profile {inferred}; "
            f"--profile {args.profile} disagrees"
        )
    profile = args.profile or inferred
    if not profile:
        raise ValueError(
            f"Cannot infer a Modbus profile for {device['name']}; use --profile"
        )
    return profile


def inventory_node_at(args, host: str) -> dict | None:
    matches = [
        item
        for item in inventory(args.inventory)
        if "modbus-tcp" in item.get("protocols", [])
        and str(item["address"]).split("/")[0] == host
    ]
    if len(matches) > 1:
        raise ValueError(f"Several Modbus nodes declare {host}; correct the inventory")
    return matches[0] if matches else None


def target_endpoint(args) -> tuple[dict, str, int, bool]:
    requested_node = args.node
    if args.host:
        requested_host = ipv4(args.host)
        matched = inventory_node_at(args, requested_host)
        if matched and requested_node and matched["name"] != requested_node:
            raise ValueError(
                f"--host {requested_host} belongs to {matched['name']}, "
                f"but --node selected {requested_node}"
            )
        if matched and not requested_node:
            args.node = matched["name"]
        if not matched and args.port is None:
            raise ValueError("An unlisted --host also needs --port and --profile")
    device, host, port = endpoint(args, "modbus-tcp", "PLC", 502)
    if host == str(device["address"]).split("/")[0]:
        # endpoint() synthesizes an IP-named record for an unlisted --host.
        verified = inventory_node_at(args, host) is not None
        return device, host, port, verified
    matched = inventory_node_at(args, host)
    if matched:
        if requested_node and matched["name"] != requested_node:
            raise ValueError(
                f"Destination {host} belongs to {matched['name']}, "
                f"but --node selected {requested_node}"
            )
        args.node = matched["name"]
        device, host, port = endpoint(args, "modbus-tcp", "PLC", 502)
        return device, host, port, True
    unverified = {"name": host, "address": host, "environment": {}}
    return unverified, host, port, False


def validate_action_spec(profile: str, action: str, spec: dict) -> None:
    if (
        not isinstance(spec, dict)
        or set(spec) != {"area", "address", "value", "repeat", "meaning"}
        or not isinstance(spec.get("area"), str)
        or spec["area"] not in {"holding-register", "coil"}
        or type(spec.get("address")) is not int
        or not 0 <= spec["address"] <= 65535
        or not isinstance(spec.get("value"), str)
        or spec["value"] not in {"argument", "true", "false", "zero"}
        or not isinstance(spec.get("repeat"), bool)
        or not isinstance(spec.get("meaning"), str)
        or not spec["meaning"]
    ):
        raise ValueError(f"Invalid action {action!r} in Modbus profile {profile}")
    if spec["area"] == "coil" and spec["value"] not in {"true", "false"}:
        raise ValueError(f"Coil action {action!r} must specify true or false")
    if spec["area"] == "holding-register" and spec["value"] in {"true", "false"}:
        raise ValueError(f"Holding-register action {action!r} needs a numeric value")


def action_for(profiles: dict, profile: str, action: str) -> dict:
    entry = profiles.get(profile)
    if entry is None:
        raise ValueError(
            f"Unknown Modbus profile {profile!r}; provide a profile file for this cell"
        )
    actions = entry["actions"]
    spec = actions.get(action)
    if spec is None:
        available = ", ".join(sorted(actions))
        raise ValueError(
            f"Action {action!r} is not defined for {profile}; available actions: {available}"
        )
    return spec


def action_value(spec: dict, argument: int) -> int | bool:
    source = spec["value"]
    if source == "argument":
        return argument
    if source == "zero":
        return 0
    return source == "true"


def main():
    options = parser(__doc__, "MET-PLC-INTAKE-01")
    options.add_argument("--unit-id", type=int, default=1)
    options.add_argument("--action", help="Action defined by the selected cell profile")
    options.add_argument("--value", type=int, default=999)
    options.add_argument(
        "--profile", help="Explicit Modbus profile; required for an unlisted host"
    )
    options.add_argument(
        "--profile-file", type=Path, help="Versioned Modbus action profile YAML"
    )
    options.add_argument(
        "--restore", action="store_true", help="Restore the initial value on exit"
    )
    options.add_argument("--duration", type=positive, default=10)
    options.add_argument("--period", type=positive, default=1)
    options.add_argument("--max-messages", type=int, default=100)
    args = options.parse_args()
    if args.action is None:
        options.error("--action is required")
    device, host, port, verified = target_endpoint(args)
    profile = selected_profile(args, device, host)
    profile_file = profile_file_for(args)
    profiles, profile_version, profile_sha256 = load_profiles(profile_file)
    spec = action_for(profiles, profile, args.action)
    if not 0 <= args.value <= 65535 or not 0 <= args.unit_id <= 255:
        raise ValueError("Value must be 0–65535 and unit ID 0–255")
    if (
        args.duration > 3600
        or args.period < 0.001
        or not 1 <= args.max_messages <= 100000
    ):
        raise ValueError(
            "Use duration <=3600, period >=0.001, and max-messages 1–100000"
        )
    if spec["repeat"] and args.restore:
        raise ValueError("Cannot restore an advancing heartbeat to its initial value")
    coil = spec["area"] == "coil"
    address = spec["address"]
    value = action_value(spec, args.value)
    with Run(
        args,
        "modbus_write",
        host=host,
        port=port,
        node=device["name"] if verified else None,
        node_attribution="inventory" if verified else "unverified",
        requested_node=args.node if not verified else None,
        action=args.action,
        profile=profile,
        profile_version=profile_version,
        profile_file=str(profile_file),
        profile_sha256=profile_sha256,
        area=spec["area"],
        meaning=spec["meaning"],
        address=address,
        value=value,
        unit=args.unit_id,
        restore=args.restore,
    ) as run:
        if args.dry_run:
            return 0
        from pymodbus.client import ModbusTcpClient

        client = ModbusTcpClient(host, port=port, timeout=args.timeout, retries=0)
        original = None
        wrote = False
        try:
            if not client.connect():
                raise ConnectionError("PLC connection failed")
            original = read_value(client, address, args.unit_id, coil)
            run.emit("before", value=original)
            cycles = (
                schedule(1 / args.period, args.duration, args.max_messages)
                if spec["repeat"]
                else [0]
            )
            confirmed = 0
            for index in cycles:
                write_value(client, address, value, args.unit_id, coil)
                wrote = True
                actual = read_value(client, address, args.unit_id, coil)
                run.emit(
                    "write",
                    index=index + 1,
                    requested=value,
                    observed=actual,
                    matched=actual == value,
                )
                confirmed += actual == value
            if spec["repeat"]:
                run.emit(
                    "note",
                    evidence="Repeated heartbeat overwrites; the PLC continues its own timer",
                )
            return 0 if confirmed else 1
        finally:
            try:
                if args.restore and wrote:
                    write_value(client, address, original, args.unit_id, coil)
                    actual = read_value(client, address, args.unit_id, coil)
                    run.emit("restored", requested=original, observed=actual)
                    if actual != original:
                        raise RuntimeError(
                            "Original value was not restored at readback"
                        )
            finally:
                client.close()


if __name__ == "__main__":
    raise SystemExit(cli(main))
