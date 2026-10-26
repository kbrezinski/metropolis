"""Check or explicitly apply the selected dataset's container resource baseline."""

from __future__ import annotations

import math

from device_logs import container_id, utc_now
from device_profiles import CPU_PERIOD, DeviceProfileBackend


def differences(limits: dict, desired: dict) -> dict:
    """Compare effective CPU allowance and explicit RAM/swap settings."""
    nano = limits.get("nano_cpus", 0)
    quota, period = limits.get("cpu_quota", 0), limits.get("cpu_period", 0)
    if nano > 0:
        cpu = nano / 1_000_000_000
        wanted_cpu = round(desired["cpus"] * 1_000_000_000) / 1_000_000_000
    else:
        cpu = quota / period if quota > 0 and period > 0 else None
        wanted_cpu = round(desired["cpus"] * CPU_PERIOD) / CPU_PERIOD
    observed = {
        "cpus": cpu,
        "memory_bytes": limits.get("mem_limit"),
        "memory_swap_bytes": limits.get("memswap_limit"),
    }
    result = {}
    for field, value in observed.items():
        matches = (
            value is not None and math.isclose(value, wanted_cpu, abs_tol=1e-12)
            if field == "cpus"
            else value == desired[field]
        )
        if not matches:
            result[field] = {"expected": desired[field], "observed": value}
    return result


def configure_baseline(
    client,
    project_id: str,
    baseline: dict,
    docker_hosts: dict,
    *,
    apply: bool = False,
    backend_factory=DeviceProfileBackend,
) -> dict:
    """Read all targets; only explicit setup applies persistent resource limits."""
    report = {
        "path": baseline["path"],
        "sha256": baseline["sha256"],
        "mode": "apply" if apply else "check",
        "status": "matched",
        "checked_at_utc": utc_now(),
        "nodes": {},
        "unsupported": baseline.get("unsupported", []),
        "failures": [],
    }
    backend = backend_factory(docker_hosts)
    failed_computes = set()

    def failure(source: str, reason: str) -> None:
        report["failures"].append({"source": source, "reason": reason})

    try:
        by_name = {}
        for node in client.project_nodes(project_id):
            by_name.setdefault(node["name"], []).append(node)
        for target in baseline["targets"]:
            name, desired = target["name"], target["settings"]
            record = {
                "status": "unavailable",
                "desired": desired,
                "observed": None,
                "differences": {},
            }
            report["nodes"][name] = record
            compute = None
            stage = "resolve"
            try:
                matches = by_name.get(name, [])
                if len(matches) != 1:
                    raise ValueError("expected one GNS3 node with the declared name")
                node = matches[0]
                record.update(
                    node_id=node["node_id"],
                    compute_id=node.get("compute_id", "local"),
                    container_id=container_id(node),
                )
                compute = record["compute_id"]
                if node.get("node_type") != "docker":
                    raise ValueError("declared resource target is not a Docker node")
                if node.get("properties", {}).get("image") != target["image"]:
                    raise ValueError("GNS3 Docker image differs from the dataset")
                if not record["container_id"]:
                    raise ValueError("node has no container yet; start it before setup")
                if compute in failed_computes:
                    raise ValueError(
                        "Docker compute connection failed earlier in this check"
                    )
                stage = "inspect"
                snapshot = backend.inspect(node)
                record["observed"] = snapshot["limits"]
                record["differences"] = differences(snapshot["limits"], desired)
                if not record["differences"]:
                    record["status"] = "matched"
                elif not apply:
                    record["status"] = "drift"
                else:
                    # Baseline setup intentionally persists; an unlimited starting
                    # value cannot be restored by the temporary-profile workflow.
                    stage = "apply"
                    backend.prepare_baseline(snapshot, desired)
                    record["before"] = snapshot["limits"]
                    record["observed"] = None
                    actual = backend.apply_baseline(node, desired)
                    record["observed"] = actual["limits"]
                    record["differences"] = differences(actual["limits"], desired)
                    if record["differences"]:
                        raise RuntimeError(
                            "baseline readback did not match the requested limits"
                        )
                    record["status"] = "applied"
            except Exception as exc:  # noqa: BLE001 - preserve other target results
                record["status"] = "unavailable"
                reason = (
                    str(exc)
                    if isinstance(exc, (ValueError, RuntimeError))
                    else type(exc).__name__
                )
                record["error"] = reason
                failure(name, reason)
                engines = getattr(backend, "engines", None)
                not_connected = (
                    stage == "inspect"
                    and engines is not None
                    and compute not in engines
                )
                http_error = getattr(exc, "response", None) is not None
                if compute and (
                    not_connected
                    or (
                        not http_error
                        and isinstance(exc, (OSError, ConnectionError, EOFError))
                    )
                    or any(
                        marker in reason
                        for marker in (
                            "(ReadTimeout)",
                            "(ConnectTimeout)",
                            "(ConnectionError)",
                        )
                    )
                ):
                    failed_computes.add(compute)
        if not baseline["targets"]:
            failure("baseline", "no declared Docker targets to check")
    except Exception as exc:  # noqa: BLE001 - still close every connection
        failure("baseline", f"baseline discovery failed: {type(exc).__name__}")
    finally:
        try:
            backend.close()
        except Exception as exc:  # noqa: BLE001
            failure(
                "baseline",
                f"could not close baseline connections: {type(exc).__name__}",
            )
    report["status"] = (
        "failed"
        if report["failures"]
        else "drift"
        if any(node["status"] == "drift" for node in report["nodes"].values())
        else "matched"
    )
    return report
