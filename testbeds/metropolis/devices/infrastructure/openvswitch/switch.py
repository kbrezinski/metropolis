"""Owned Open vSwitch control surface for GNS3 Docker switch nodes."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

BRIDGE = "br0"


def command(*args: str) -> str:
    return subprocess.run(
        args, check=True, text=True, capture_output=True
    ).stdout.strip()


def error_text(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        return (exc.stderr or "").strip() or str(exc)
    return str(exc)


def port_state(name: str) -> dict | None:
    try:
        bridge = command("ovs-vsctl", "port-to-br", name)
    except subprocess.CalledProcessError:
        return None
    if bridge != BRIDGE:
        return None

    def get(field: str) -> str:
        return command("ovs-vsctl", "get", "Port", name, field).strip('"')

    tag = get("tag")
    trunks = get("trunks")
    return {
        "tag": None if tag == "[]" else int(tag),
        "trunks": []
        if trunks == "[]"
        else [int(v.strip()) for v in trunks.strip("[]").split(",")],
        "vlan_mode": get("vlan_mode"),
    }


def expected(setting: dict) -> dict:
    mode = setting["mode"]
    vlans = setting["vlans"]
    if (
        mode == "trunk"
        and vlans
        and all(type(v) is int and 1 <= v <= 4094 for v in vlans)
    ):
        return {"tag": None, "trunks": sorted(set(vlans)), "vlan_mode": "trunk"}
    if (
        mode == "access"
        and len(vlans) == 1
        and type(vlans[0]) is int
        and 1 <= vlans[0] <= 4094
    ):
        return {"tag": vlans[0], "trunks": [], "vlan_mode": "access"}
    if mode == "untagged" and not vlans:
        # VLAN 1 is internal to this isolated bridge; wire traffic is untagged.
        return {"tag": 1, "trunks": [], "vlan_mode": "access"}
    raise ValueError(f"invalid port mode or VLANs: {mode}")


def validate(config: dict) -> dict[str, dict]:
    if config.get("schema_version") != 1 or not isinstance(config.get("ports"), dict):
        raise ValueError("invalid switch configuration version or ports")
    desired = {}
    for number, setting in config["ports"].items():
        if not number.isdecimal() or int(number) > 255:
            raise ValueError("invalid adapter number")
        desired[f"eth{int(number)}"] = expected(setting)
    return desired


def reconcile(config: dict) -> dict:
    desired = validate(config)
    for attempt in range(30):
        try:
            command("ovs-vsctl", "--timeout=2", "show")
            break
        except subprocess.CalledProcessError:
            if attempt == 29:
                raise RuntimeError(
                    "Open vSwitch database did not become ready"
                ) from None
            time.sleep(1)
    command("ovs-vsctl", "--may-exist", "add-br", BRIDGE)
    changed = []
    for name in command("ovs-vsctl", "list-ports", BRIDGE).splitlines():
        if name not in desired:
            command("ovs-vsctl", "--if-exists", "del-port", BRIDGE, name)
            changed.append(name)
    for name, state in desired.items():
        for _ in range(30):
            if Path(f"/sys/class/net/{name}").exists():
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"GNS3 adapter {name} did not appear")
        if port_state(name) == state:
            continue
        command("ovs-vsctl", "--may-exist", "add-port", BRIDGE, name)
        if state["vlan_mode"] == "trunk":
            command("ovs-vsctl", "clear", "Port", name, "tag")
            command(
                "ovs-vsctl",
                "set",
                "Port",
                name,
                "vlan_mode=trunk",
                "trunks=" + ",".join(map(str, state["trunks"])),
            )
        else:
            command("ovs-vsctl", "clear", "Port", name, "trunks")
            command(
                "ovs-vsctl",
                "set",
                "Port",
                name,
                "vlan_mode=access",
                f"tag={state['tag']}",
            )
        changed.append(name)
    actual = {name: port_state(name) for name in desired}
    if actual != desired:
        raise RuntimeError("applied Open vSwitch state did not match the plan")
    return {"verified": True, "changed": changed, "ports": actual}


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "serve":
        try:
            command(
                "/usr/share/openvswitch/scripts/ovs-ctl", "start", "--system-id=random"
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            print(
                json.dumps({"event": "switch_start_failed", "error": error_text(exc)}),
                flush=True,
            )
            return 1
        print(json.dumps({"event": "switch_ready"}), flush=True)
        while True:
            time.sleep(3600)
    if action == "apply":
        try:
            result = reconcile(json.load(sys.stdin))
        except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
            print(json.dumps({"verified": False, "error": error_text(exc)}))
            return 1
        print(json.dumps(result))
        return 0
    print("expected serve or apply", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
