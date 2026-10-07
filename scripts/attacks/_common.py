"""Inventory defaults and structured run records shared by lab experiments."""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
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


def parser(description: str, node: str) -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=description)
    result.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    result.add_argument("--node", default=node)
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
    return data["devices"]


def endpoint(args, protocol: str, prefix: str, default_port: int):
    devices = inventory(args.inventory)
    device = next((item for item in devices if item["name"] == args.node), None)
    if device is None or protocol not in device["protocols"]:
        raise ValueError(f"{args.node} is not an inventoried {protocol} node")
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
    record = next(
        (item for item in inventory(args.inventory) if item["name"] == name), None
    )
    if record is None:
        raise ValueError(f"{name} is not in the inventory")
    if protocol not in record["protocols"]:
        raise ValueError(f"{name} does not declare the {protocol} protocol")
    return record


def address_of(
    args,
    name: str,
    protocol: str,
    env_key: str,
    port_env: str = "C2_PORT",
    default_port: int | None = None,
) -> tuple[dict, str, int]:
    """Resolve a named node to (record, host, port) for a multi-node driver.

    The port is resolved from ``--port``, then the node's own ``port_env``
    value, then ``default_port``. A driver that only needs reachability should
    use ``host_of`` instead.
    """
    record = device_by_name(args, name, protocol)
    environment = record.get("environment", {})
    host = ipv4(args.host or os.getenv(env_key) or record["address"].split("/")[0])
    if args.port is not None:
        port = args.port
    elif port_env in environment:
        port = int(environment[port_env])
    elif default_port is not None:
        port = default_port
    else:
        raise ValueError(f"{name} has no usable port; pass --port")
    if not 1 <= port <= 65535:
        raise ValueError(f"{name} has an out-of-range port")
    return record, host, port


def host_of(args, name: str, protocol: str, env_key: str) -> tuple[dict, str]:
    """Resolve a named node to (record, host) when no listening port is needed."""
    record = device_by_name(args, name, protocol)
    host = ipv4(args.host or os.getenv(env_key) or record["address"].split("/")[0])
    return record, host


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
