"""Run-owned Open vSwitch bandwidth settings, separate from VLAN configuration."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

INTERFACE = re.compile(r"eth[0-9]+$")
RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,128}$")
UUID = re.compile(r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}$")
# Other kinds can carry customized parameters. Only a bare veth noqueue needs
# no configuration snapshot to restore after the owned HTB is removed.
DEFAULT_QDISCS = {"noqueue"}
LOCK_DIRECTORY = Path("/run/metropolis/profile-locks")


def _flock(stream, locked: bool) -> None:
    # The helper runs in a Linux switch container. Import lazily so its pure
    # configuration logic can also be tested on Windows controllers.
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_EX if locked else fcntl.LOCK_UN)


@contextmanager
def _port_lock(interface: str):
    """Serialize execs even if their controller-side connection disappears."""
    LOCK_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (LOCK_DIRECTORY / f"{interface}.lock").open("a+b") as stream:
        _flock(stream, True)
        try:
            yield
        finally:
            _flock(stream, False)


def _cancelled(interface: str, run_id: str) -> Path:
    return LOCK_DIRECTORY / f"{run_id}-{interface}.cancelled"


def _cancel(interface: str, run_id: str) -> None:
    # The marker is intentionally retained for the container's lifetime. A
    # delayed or replayed apply must never reapply this run after restoration.
    try:
        with _cancelled(interface, run_id).open("x", encoding="ascii") as stream:
            stream.write("restoration requested\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        pass


def command(*args: str) -> str:
    return subprocess.run(
        args, check=True, text=True, capture_output=True, timeout=3
    ).stdout.strip()


def ovs_value(value):
    if isinstance(value, list) and len(value) == 2:
        tag, items = value
        if tag == "map":
            return {key: ovs_value(item) for key, item in items}
        if tag == "set":
            return [ovs_value(item) for item in items]
        if tag in {"uuid", "named-uuid"}:
            return items
    return value


def rows(table: str, *selectors: str) -> list[dict]:
    value = json.loads(
        command("ovs-vsctl", "--timeout=2", "--format=json", "find", table, *selectors)
    )
    return [
        {key: ovs_value(item) for key, item in zip(value["headings"], row, strict=True)}
        for row in value["data"]
    ]


def _one(table: str, selector: str) -> dict:
    found = rows(table, selector)
    if len(found) != 1:
        raise ValueError(f"expected one {table} for {selector}")
    return found[0]


def _uuid(value) -> str:
    if not isinstance(value, str) or not UUID.fullmatch(value):
        raise ValueError("invalid OVS row identity")
    return value


def inspect(interface: str) -> dict:
    if command("ovs-vsctl", "--timeout=2", "port-to-br", interface) != "br0":
        raise ValueError("bandwidth target is not a port on the owned br0 bridge")
    port = _one("Port", f"name={interface}")
    device = _one("Interface", f"name={interface}")
    if device["_uuid"] not in port.get("interfaces", []):
        # OVS encodes a single UUID without a set wrapper.
        if port.get("interfaces") != device["_uuid"]:
            raise ValueError("OVS port does not contain the selected interface")
    qos = port.get("qos")
    if isinstance(qos, list):
        if len(qos) > 1:
            raise ValueError("unexpected OVS QoS references")
        qos = qos[0] if qos else None
    return {
        "interface": interface,
        "port_uuid": _uuid(port["_uuid"]),
        "interface_uuid": _uuid(device["_uuid"]),
        "qos": _uuid(qos) if qos else None,
        "ingress_policing_rate": int(device.get("ingress_policing_rate", 0)),
        "ingress_policing_burst": int(device.get("ingress_policing_burst", 0)),
    }


def qdiscs(interface: str) -> list[dict]:
    return json.loads(command("tc", "-j", "qdisc", "show", "dev", interface))


def _tags(run_id: str, state: dict) -> dict:
    return {
        "metropolis_run": run_id,
        "metropolis_interface": state["interface"],
        "metropolis_port": state["port_uuid"],
    }


def owned(table: str, run_id: str, state: dict) -> list[dict]:
    tags = _tags(run_id, state)
    found = rows(table, *(f"external_ids:{key}={value}" for key, value in tags.items()))
    for row in found:
        _uuid(row["_uuid"])
        if any(
            row.get("external_ids", {}).get(key) != value for key, value in tags.items()
        ):
            raise ValueError("refusing to change an unowned OVS row")
    return found


def _validate_identity(current: dict, baseline: dict) -> None:
    for key in ("interface", "interface_uuid", "port_uuid"):
        if current[key] != baseline.get(key):
            raise ValueError("OVS interface or port identity changed")


def apply(
    interface: str, run_id: str, bandwidth_kbps: int, baseline: dict | None = None
) -> dict:
    if type(bandwidth_kbps) is not int or not 1 <= bandwidth_kbps <= 1_000_000_000:
        raise ValueError("bandwidth_kbps must be a positive integer up to 1000000000")
    current = inspect(interface)
    if baseline is not None:
        if not isinstance(baseline, dict):
            raise ValueError("baseline must be an object")
        _validate_identity(current, baseline)
        if current != baseline:
            raise ValueError("bandwidth settings changed after preflight")
    else:
        baseline = current
    if baseline["qos"]:
        raise ValueError("existing OVS QoS must be removed or managed separately")
    if any(row.get("kind") not in DEFAULT_QDISCS for row in qdiscs(interface)):
        raise ValueError(
            "existing non-default interface queue discipline is unsupported"
        )
    tags = [
        f"external_ids:{key}={value}" for key, value in _tags(run_id, baseline).items()
    ]
    rate = bandwidth_kbps * 1000
    burst = max(128, bandwidth_kbps // 10)
    command(
        "ovs-vsctl",
        "--timeout=2",
        "--",
        "--id=@queue",
        "create",
        "Queue",
        f"other-config:max-rate={rate}",
        *tags,
        "--",
        "--id=@qos",
        "create",
        "QoS",
        "type=linux-htb",
        f"other-config:max-rate={rate}",
        "queues:0=@queue",
        *tags,
        "--",
        "set",
        "Port",
        interface,
        "qos=@qos",
        "--",
        "set",
        "Interface",
        interface,
        f"ingress_policing_rate={bandwidth_kbps}",
        f"ingress_policing_burst={burst}",
    )
    current = inspect(interface)
    _validate_identity(current, baseline)
    qos = owned("QoS", run_id, baseline)
    queues = owned("Queue", run_id, baseline)
    if (
        len(qos) != 1
        or len(queues) != 1
        or current["qos"] != qos[0]["_uuid"]
        or qos[0].get("type") != "linux-htb"
        or qos[0].get("other_config", {}).get("max-rate") != str(rate)
        or queues[0].get("other_config", {}).get("max-rate") != str(rate)
        or current["ingress_policing_rate"] != bandwidth_kbps
        or current["ingress_policing_burst"] != burst
    ):
        raise RuntimeError("OVS bandwidth configuration readback differs")
    for _ in range(10):
        if any(row.get("kind") == "htb" for row in qdiscs(interface)):
            return {
                "verified": True,
                "state": current,
                "bandwidth_kbps": bandwidth_kbps,
                "mechanism": "ingress_policing_and_egress_htb",
                "achieved_rate_verified": False,
            }
        time.sleep(0.1)
    raise RuntimeError("OVS requested HTB but no kernel HTB queue discipline appeared")


def restore(interface: str, run_id: str, baseline: dict) -> dict:
    if not isinstance(baseline, dict):
        raise ValueError("baseline must be an object")
    current = inspect(interface)
    _validate_identity(current, baseline)
    if baseline.get("qos") is not None:
        raise ValueError("only an originally empty QoS reference can be restored")
    for key in ("ingress_policing_rate", "ingress_policing_burst"):
        if type(baseline.get(key)) is not int or baseline[key] < 0:
            raise ValueError("invalid baseline policing setting")
    qos, queues = owned("QoS", run_id, baseline), owned("Queue", run_id, baseline)
    if current["qos"] and current["qos"] not in {row["_uuid"] for row in qos}:
        raise ValueError("refusing to replace another owner's QoS")
    if not qos and not queues:
        if current != baseline:
            raise ValueError("refusing to restore policing without run-owned settings")
        if any(row.get("kind") not in DEFAULT_QDISCS for row in qdiscs(interface)):
            raise ValueError("unowned queue discipline appeared during restoration")
        return {"verified": True, "state": current}
    args = ["ovs-vsctl", "--timeout=2"]
    if current["qos"]:
        args += ["--", "clear", "Port", interface, "qos"]
    args += ["--", "set", "Interface", interface]
    args += [
        f"{key}={baseline[key]}"
        for key in ("ingress_policing_rate", "ingress_policing_burst")
    ]
    for table, entries in (("QoS", qos), ("Queue", queues)):
        for row in entries:
            args += ["--", "destroy", table, row["_uuid"]]
    command(*args)
    for _ in range(10):
        actual = inspect(interface)
        if actual == baseline and all(
            row.get("kind") in DEFAULT_QDISCS for row in qdiscs(interface)
        ):
            return {"verified": True, "state": actual}
        time.sleep(0.1)
    raise RuntimeError("original OVS bandwidth settings did not return")


def execute(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("profile request must be an object")
    interface, run_id = payload.get("interface"), payload.get("run_id")
    if not isinstance(interface, str) or not INTERFACE.fullmatch(interface):
        raise ValueError("interface must be a GNS3 ethN adapter")
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run ID")
    action = payload.get("action")
    if action not in {"inspect", "apply", "restore"}:
        raise ValueError("unsupported bandwidth profile action")
    with _port_lock(interface):
        if action == "restore":
            _cancel(interface, run_id)
            return restore(interface, run_id, payload.get("snapshot", {}))
        if action == "apply":
            if _cancelled(interface, run_id).exists():
                raise ValueError(
                    "this run's bandwidth apply was cancelled by restoration"
                )
            return apply(
                interface,
                run_id,
                payload.get("bandwidth_kbps"),
                payload.get("snapshot"),
            )
        state = inspect(interface)
        if state["qos"] or any(
            row.get("kind") not in DEFAULT_QDISCS for row in qdiscs(interface)
        ):
            raise ValueError(
                "existing QoS or non-default queue discipline is unsupported"
            )
        return {"verified": True, "state": state}


def main() -> int:
    def deadline(_signal, _frame):
        raise TimeoutError("bandwidth helper exceeded its operation deadline")

    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(15)
    try:
        payload = (
            json.loads(sys.argv[1]) if len(sys.argv) == 2 else json.load(sys.stdin)
        )
        result = execute(payload)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"verified": False, "error": str(exc)}))
        return 1
    finally:
        signal.alarm(0)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
