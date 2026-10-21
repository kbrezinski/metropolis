"""Inventory defaults and structured run records shared by lab experiments."""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY = (
    ROOT
    / "testbeds/metropolis/datasets/water_treatment_v1"
    / "device_instances/initial_devices.yaml"
)


def gns3_client():
    """Use the repository's GNS3 connector for live lab setup checks."""
    gns3_tools = ROOT / "scripts/gns3"
    if str(gns3_tools) not in sys.path:
        sys.path.insert(0, str(gns3_tools))
    from client import Gns3Client
    from config import resolve_server

    return Gns3Client(resolve_server())


class AttackArgumentParser(argparse.ArgumentParser):
    """Expose an explicit Gotham shell alternative on every attack command."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_argument(
            "--gotham-script",
            type=Path,
            help="Run this user-supplied Gotham .sh file instead of the Python attack",
        )
        self.add_argument(
            "--gotham-arg",
            action="append",
            default=[],
            help="Argument for the Gotham script; repeat as needed",
        )

    def parse_args(self, args=None, namespace=None):
        parsed = super().parse_args(args, namespace)
        if hasattr(parsed, "inventory"):
            if parsed.inventory and parsed.dataset:
                self.error("use --dataset or --inventory, not both")
            dataset = parsed.dataset or os.getenv("METROPOLIS_DATASET")
            selected = parsed.inventory or (
                None if parsed.dataset else os.getenv("METROPOLIS_INVENTORY")
            )
            parsed.inventory = (
                Path(selected).expanduser()
                if selected
                else Path(dataset).expanduser()
                / "device_instances/initial_devices.yaml"
                if dataset
                else DEFAULT_INVENTORY
            )
            if not parsed.inventory.is_file():
                self.error(f"device inventory does not exist: {parsed.inventory}")
        if parsed.gotham_arg and not parsed.gotham_script:
            self.error("--gotham-arg requires --gotham-script")
        if parsed.gotham_script:
            script = parsed.gotham_script.expanduser().resolve()
            if script.suffix.lower() != ".sh" or not script.is_file():
                self.error("--gotham-script must name an existing .sh file")
            if not hasattr(parsed, "dry_run"):
                self.error("this command does not support Gotham script execution")
            with Run(
                parsed,
                "gotham_shell",
                script=str(script),
                selected_by=Path(sys.argv[0]).stem,
            ) as run:
                if parsed.dry_run:
                    returncode = 0
                else:
                    bash = shutil.which("bash")
                    if bash is None:
                        raise RuntimeError("bash is required to run a Gotham .sh file")
                    result = subprocess.run(
                        [bash, str(script), *parsed.gotham_arg],
                        cwd=script.parent,
                        check=False,
                    )
                    returncode = result.returncode
                    run.emit("script_finished", returncode=returncode)
                    if returncode:
                        raise SystemExit(returncode)
            raise SystemExit(returncode)
        return parsed


def parser(description: str, node: str) -> argparse.ArgumentParser:
    result = AttackArgumentParser(description=description)
    result.add_argument(
        "--dataset",
        type=Path,
        help="Dataset directory containing device_instances/initial_devices.yaml",
    )
    result.add_argument(
        "--inventory",
        type=Path,
        help="Device inventory YAML; overrides the default dataset",
    )
    result.add_argument("--node", default="all" if node == "all" else None)
    result.set_defaults(preferred_node=None if node == "all" else node)
    result.add_argument("--host", help="Override destination with a lab IPv4 address")
    result.add_argument("--port", type=int)
    result.add_argument("--timeout", type=positive, default=3.0)
    result.add_argument(
        "--dry-run", action="store_true", help="Validate and print without traffic"
    )
    result.add_argument("--output", type=Path, help="Also write UTC JSONL run events")
    return result


def positive(value: str | float) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Must be a finite positive number")
    return number


def ipv4(value: str) -> str:
    address = ipaddress.IPv4Address(value)
    if (
        not address.is_private
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    ):
        raise ValueError("Use an IPv4 address within the private lab network")
    return str(address)


def inventory(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
        raise ValueError(f"{path} must contain a devices list")
    names = [item.get("name") for item in data["devices"] if isinstance(item, dict)]
    if (
        len(names) != len(data["devices"])
        or any(not isinstance(name, str) or not name for name in names)
        or len(set(names)) != len(names)
    ):
        raise ValueError(f"{path} has invalid or duplicate device names")
    for item in data["devices"]:
        environment = item.get("environment") or {}
        if not isinstance(environment, dict) or not isinstance(
            item.get("protocols"), list
        ):
            raise ValueError(
                f"{item['name']} needs protocols and an environment mapping"
            )
        configured = environment.get("NODE_IP")
        if configured and item.get("address") != configured:
            raise ValueError(
                f"{item['name']} address {item.get('address')} disagrees with NODE_IP {configured}"
            )
    return data["devices"]


def project_name_for(args) -> str:
    """Infer the builder's conventional project name from the selected dataset."""
    document = yaml.safe_load(args.inventory.read_text(encoding="utf-8"))
    testbed_id = document.get("testbed_id") if isinstance(document, dict) else None
    if not testbed_id:
        raise ValueError("Inventory must declare testbed_id or use --project")
    return f"{testbed_id}_{args.inventory.parent.parent.name}"


class TargetSelectionError(ValueError):
    """The inventory cannot identify one service target."""


def select_device(
    args,
    protocol: str,
    *,
    name: str | None = None,
    preferred: str | None = None,
    service_env: str | None = None,
    exclude: set[str] | None = None,
    env_equals: tuple[str, str] | None = None,
    use_args_node: bool = True,
    selection_option: str = "--node",
) -> dict:
    """Choose a declared service endpoint; refuse ambiguous automatic choices."""
    records = inventory(args.inventory)
    excluded = exclude or set()
    requested = (
        name
        if name is not None
        else getattr(args, "node", None)
        if use_args_node
        else None
    )
    if requested:
        record = next((item for item in records if item["name"] == requested), None)
        if record is None:
            raise TargetSelectionError(f"{requested} is not in the inventory")
        if requested in excluded or protocol not in record.get("protocols", []):
            raise TargetSelectionError(
                f"{requested} is not an inventoried {protocol} node"
            )
        if env_equals:
            key, value = env_equals
            declared = record.get("environment", {}).get(key)
            if declared is not None and declared != value:
                raise TargetSelectionError(
                    f"{requested} declares {key}={declared}; expected {value}"
                )
        return record
    candidates = [
        item
        for item in records
        if protocol in item.get("protocols", []) and item["name"] not in excluded
    ]
    if service_env:
        marked = [
            item for item in candidates if service_env in item.get("environment", {})
        ]
        if marked:
            candidates = marked
    if env_equals:
        key, value = env_equals
        matching = [
            item for item in candidates if item.get("environment", {}).get(key) == value
        ]
        if matching:
            candidates = matching
        elif any(key in item.get("environment", {}) for item in candidates):
            raise TargetSelectionError(
                f"No {protocol} node declares {key}={value} in {args.inventory}"
            )
    preferred = preferred or getattr(args, "preferred_node", None)
    if preferred:
        match = next((item for item in candidates if item["name"] == preferred), None)
        if match:
            return match
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise TargetSelectionError(
            f"No {protocol} node exists in {args.inventory}; select another inventory"
        )
    choices = ", ".join(sorted(item["name"] for item in candidates))
    raise TargetSelectionError(
        f"Multiple {protocol} nodes are available ({choices}); select one with {selection_option}"
    )


def endpoint(args, protocol: str, prefix: str, default_port: int):
    service_env = {
        "MQTT": "MQTT_OPERATOR_USERNAME",
        "PLC": "DEVICE_ROLE",
        "SSH": "ENGINEER_PASSWORD",
        "TELNET": "LEGACY_ROOT_PASSWORD",
        "COAP": "COAP_PORT",
    }.get(prefix)
    if args.host and args.port and not getattr(args, "node", None):
        explicit_host = ipv4(args.host)
        device = next(
            (
                item
                for item in inventory(args.inventory)
                if item["address"].split("/")[0] == explicit_host
                and protocol in item.get("protocols", [])
            ),
            None,
        )
        if device is None:
            # Do not borrow credentials or service settings from an unrelated
            # inventoried node when a different appliance was named explicitly.
            device = {
                "name": explicit_host,
                "address": explicit_host,
                "protocols": [protocol],
                "environment": {},
            }
    else:
        device = select_device(args, protocol, service_env=service_env)
    host = ipv4(
        args.host
        or os.getenv(f"METROPOLIS_{prefix}_HOST")
        or device["address"].split("/")[0]
    )
    port_key = {"PLC": "MODBUS_PORT", "COAP": "COAP_PORT", "MQTT": "MQTT_PORT"}.get(
        prefix, f"{prefix}_PORT"
    )
    default = device.get("environment", {}).get(port_key, default_port)
    port = (
        args.port
        if args.port is not None
        else int(os.getenv(f"METROPOLIS_{prefix}_PORT", str(default)))
    )
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    return device, host, port


def device_by_name(args, name: str, protocol: str) -> dict:
    """Look up one inventoried node by explicit name and required protocol."""
    return select_device(args, protocol, name=name)


def address_of(
    args,
    name: str | None,
    protocol: str,
    env_key: str,
    port_env: str = "C2_PORT",
    default_port: int | None = None,
    *,
    preferred: str | None = None,
    exclude: set[str] | None = None,
    env_equals: tuple[str, str] | None = None,
    host_override: str | None = None,
    port_override: int | None = None,
    use_common_overrides: bool = False,
    use_args_node: bool = True,
    selection_option: str = "--node",
) -> tuple[dict, str, int]:
    """Resolve a named node to (record, host, port) for a multi-node driver.

    The port is resolved from ``--port``, then the node's own ``port_env``
    value, then ``default_port``. A driver that only needs reachability should
    use ``host_of`` instead.
    """
    record = select_device(
        args,
        protocol,
        name=name,
        preferred=preferred,
        exclude=exclude,
        env_equals=env_equals,
        use_args_node=use_args_node,
        selection_option=selection_option,
    )
    environment = record.get("environment", {})
    host = ipv4(
        host_override
        or (args.host if use_common_overrides else None)
        or os.getenv(env_key)
        or record["address"].split("/")[0]
    )
    if port_override is not None:
        port = port_override
    elif use_common_overrides and args.port is not None:
        port = args.port
    elif port_env in environment:
        port = int(environment[port_env])
    elif "C2_PORT" in environment:
        port = int(environment["C2_PORT"])
    elif default_port is not None:
        port = default_port
    else:
        raise ValueError(f"{record['name']} has no usable port; pass --port")
    if not 1 <= port <= 65535:
        raise ValueError(f"{record['name']} has an out-of-range port")
    return record, host, port


def host_of(
    args,
    name: str | None,
    protocol: str,
    env_key: str,
    *,
    exclude: set[str] | None = None,
    host_override: str | None = None,
    use_args_node: bool = True,
    selection_option: str = "--node",
) -> tuple[dict, str]:
    """Resolve a named node to (record, host) when no listening port is needed."""
    record = select_device(
        args,
        protocol,
        name=name,
        exclude=exclude,
        use_args_node=use_args_node,
        selection_option=selection_option,
    )
    host = ipv4(host_override or os.getenv(env_key) or record["address"].split("/")[0])
    return record, host


def add_tls_arguments(parser: argparse.ArgumentParser) -> None:
    """Let a tool talk to a broker that has TLS switched on.

    The settings mirror the device models' environment variables, so the same
    certificate and the same switches work on both sides.
    """
    parser.add_argument(
        "--tls", action="store_true", help="Connect to the broker over TLS"
    )
    parser.add_argument("--tls-ca", type=Path, help="CA certificate to trust")
    parser.add_argument(
        "--tls-insecure",
        action="store_true",
        help="Keep the encryption but skip hostname verification",
    )


def tls_from_args(args):
    """A TlsConfig from the command line, or None when TLS is not asked for."""
    if not getattr(args, "tls", False):
        return None
    from _mqtt import TlsConfig

    ca_file = str(args.tls_ca) if getattr(args, "tls_ca", None) else None
    if ca_file and not Path(ca_file).is_file():
        raise ValueError(f"CA certificate {ca_file} does not exist")
    return TlsConfig(ca_file=ca_file, insecure=bool(args.tls_insecure))


def limits(args) -> None:
    if not 0 < args.rate <= 1000 or not 0 < args.duration <= 3600:
        raise ValueError("Rate must be 0–1000/s and duration at most 3600 seconds")
    if not 1 <= args.max_messages <= 100000:
        raise ValueError("max-messages must be 1–100000")


def schedule(rate: float, duration: float, maximum: int):
    start = time.monotonic()
    due = start
    for index in range(maximum):
        now = time.monotonic()
        if now >= start + duration:
            return
        if due >= start + duration:
            return
        time.sleep(max(0, due - now))
        if time.monotonic() >= start + duration:
            return
        yield index
        # Never catch up with a burst after a slow response or timeout.
        due = max(due + 1 / rate, time.monotonic())


def passwords(path: Path | None, good: str, maximum: int) -> list[str]:
    if not 1 <= maximum <= 1000:
        raise ValueError("max-attempts must be 1–1000")
    if path:
        with path.open(encoding="utf-8") as source:
            values = [line.rstrip("\r\n") for _, line in zip(range(maximum), source)]
    else:
        values = ["incorrect-lab-password", good][:maximum]
    if not values or any("\x00" in value for value in values):
        raise ValueError("Password fixtures must contain valid entries")
    return values


class Run:
    def __init__(self, args, tool: str, **settings):
        self.args = args
        self.tool = tool
        self.settings = settings
        self.file = None

    def emit(self, event: str, **fields):
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": self.tool,
            "event": event,
            **fields,
        }
        line = json.dumps(record, sort_keys=True)
        print(line, flush=True)
        if self.file:
            self.file.write(line + "\n")
            self.file.flush()

    def __enter__(self):
        if not self.args.dry_run and os.getenv("METROPOLIS_LAB_ACK") != "yes":
            raise ValueError("Set METROPOLIS_LAB_ACK=yes for an authorized lab run")
        if self.args.output:
            self.args.output.parent.mkdir(parents=True, exist_ok=True)
            self.file = self.args.output.open("a", encoding="utf-8")
        self.emit("started", dry_run=self.args.dry_run, **self.settings)
        return self

    def __exit__(self, error_type, error, traceback):
        self.emit(
            "finished",
            outcome="error" if error else "complete",
            error=str(error) if error else None,
        )
        if self.file:
            self.file.close()


def cli(main):
    try:
        return main()
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(json.dumps({"event": "error", "error": str(exc)}), flush=True)
        return 2
    except KeyboardInterrupt:
        return 130
