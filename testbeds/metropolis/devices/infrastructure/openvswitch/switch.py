"""Owned Open vSwitch control surface for GNS3 Docker switch nodes."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BRIDGE = "br0"
OVS_LOGS = (
    Path("/var/log/openvswitch/ovsdb-server.log"),
    Path("/var/log/openvswitch/ovs-vswitchd.log"),
)
OVS_LINE = re.compile(
    r"^(?P<timestamp>[^|]+)\|\d+\|(?P<module>[^|]+)\|(?P<level>[^|]+)\|(?P<message>.*)$"
)


def switch_event(event: str, *, level: str = "INFO", **fields) -> dict:
    return {
        "_metropolis_switch_syslog": True,
        "timestamp": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "level": level,
        "event": event,
        **fields,
    }


def emit(event: dict, *, primary: bool = True) -> None:
    """Send supervisor events to Docker logs, including from an exec process."""
    line = json.dumps(event, ensure_ascii=False) + "\n"
    if primary:
        sys.stdout.write(line)
        sys.stdout.flush()
    else:
        with Path("/proc/1/fd/1").open("w", encoding="utf-8") as output:
            output.write(line)


def ovs_log_event(daemon: str, line: str) -> dict:
    match = OVS_LINE.match(line)
    fields = {"daemon": daemon, "message": line}
    if match:
        fields.update(
            timestamp=match["timestamp"],
            level=match["level"],
            module=match["module"],
            message=match["message"],
        )
    return switch_event("ovs_log", **fields)


def health_event() -> dict:
    try:
        command("ovs-vsctl", "--timeout=2", "show")
    except (OSError, subprocess.CalledProcessError) as exc:
        return switch_event(
            "switch_health_failed", level="ERROR", message=error_text(exc)
        )
    return switch_event("switch_health", message="Open vSwitch database responding")


class OvsLogFollower:
    """Read daemon log files from the beginning, then follow appended bytes."""

    def __init__(self, paths=OVS_LOGS):
        self.paths = paths
        self.offsets: dict[Path, int] = {}
        self.reported_missing: set[Path] = set()

    def poll(self) -> list[dict]:
        events = []
        for path in self.paths:
            try:
                size = path.stat().st_size
                offset = self.offsets.get(path, 0)
                if size < offset:
                    offset = 0
                with path.open(encoding="utf-8", errors="replace") as source:
                    source.seek(offset)
                    while True:
                        position = source.tell()
                        line = source.readline()
                        if not line:
                            break
                        if not line.endswith("\n"):
                            source.seek(position)
                            break
                        events.append(ovs_log_event(path.stem, line.rstrip("\r\n")))
                    self.offsets[path] = source.tell()
            except FileNotFoundError:
                if path not in self.reported_missing:
                    events.append(
                        switch_event(
                            "ovs_log_unavailable",
                            level="ERROR",
                            message=f"Open vSwitch log file missing: {path.name}",
                            daemon=path.stem,
                        )
                    )
                    self.reported_missing.add(path)
            except OSError as exc:
                events.append(
                    switch_event(
                        "ovs_log_read_failed",
                        level="ERROR",
                        message=str(exc),
                        daemon=path.stem,
                    )
                )
        return events


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
            emit(
                switch_event(
                    "switch_start_failed", level="ERROR", message=error_text(exc)
                )
            )
            return 1
        emit(switch_event("switch_ready", message="Open vSwitch daemons started"))
        follower = OvsLogFollower()
        next_health = time.monotonic()
        while True:
            for event in follower.poll():
                emit(event)
            if time.monotonic() >= next_health:
                emit(health_event())
                next_health = time.monotonic() + 15
            time.sleep(1)
    if action == "apply":
        try:
            result = reconcile(json.load(sys.stdin))
        except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
            try:
                emit(
                    switch_event(
                        "switch_config_failed", level="ERROR", message=error_text(exc)
                    ),
                    primary=False,
                )
            except OSError:
                pass  # The API response below still reports the failure.
            print(json.dumps({"verified": False, "error": error_text(exc)}))
            return 1
        if result["changed"]:
            try:
                emit(
                    switch_event(
                        "switch_config_applied",
                        message="Open vSwitch ports configured",
                        changed=result["changed"],
                    ),
                    primary=False,
                )
            except OSError as exc:
                print(
                    json.dumps(
                        {
                            "verified": False,
                            "error": f"switch syslog forwarding failed: {exc}",
                        }
                    )
                )
                return 1
        print(json.dumps(result))
        return 0
    print("expected serve or apply", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
