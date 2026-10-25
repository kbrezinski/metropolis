"""Coordinate logs, PCAPs, and resource measurements under one run ID."""

from __future__ import annotations

import json
import copy
import math
import threading
from pathlib import Path

from _cli import router_scripts
from capture import capture_for, coverage_report, resolve_links
from device_logs import DeviceLogCollector, utc_now
from lifecycle import start_all
from metrics_report import finalize_metrics
from profile_enforcement import ProfileSession
from resource_metrics import ResourceCollector
from router_config import DEFAULT_PASSWORD, DEFAULT_USERNAME, configure_project_routers


def run_experiment(
    client,
    project,
    topology,
    inventory: dict,
    infrastructure: dict,
    link_plan: dict,
    dataset: Path,
    run_id: str,
    duration: float,
    docker_hosts: dict[str, str],
    *,
    selected_links: list[str] | None = None,
    start_lab: bool = False,
    configure_routers: bool = False,
    firewall_commands: dict[str, list[str]] | None = None,
    baseline_firewall_commands: dict[str, list[str]] | None = None,
    firewall_exceptions: list[dict] | None = None,
    router_username: str = DEFAULT_USERNAME,
    router_password: str = DEFAULT_PASSWORD,
    startup_timeout: float = 120.0,
    metadata: dict | None = None,
    metrics_enabled: bool = True,
    metrics_interval: float = 5.0,
    profile: dict | None = None,
    collector_factory=DeviceLogCollector,
    capture_fn=capture_for,
    resource_collector_factory=ResourceCollector,
    metrics_report_fn=finalize_metrics,
    profile_session_factory=ProfileSession,
) -> dict:
    """Start telemetry before optional lab startup, and stop it after capture."""
    if not math.isfinite(metrics_interval) or metrics_interval <= 0:
        raise ValueError("metrics interval must be positive and finite")
    if firewall_exceptions and (
        not configure_routers
        or firewall_commands is None
        or baseline_firewall_commands is None
    ):
        raise ValueError(
            "attack exceptions require router configuration and a baseline policy"
        )
    collector = collector_factory(
        client,
        project.project_id,
        dataset / "runs" / run_id,
        run_id,
        docker_hosts,
        metadata={
            "captures": [],
            "timeline_path": "timeline.jsonl",
            **(metadata or {}),
        },
        expected_syslog_sources=[
            router["name"] for router in infrastructure["routers"]
        ],
        expected_switch_syslog_sources=[
            switch["name"]
            for group in ("switches", "segments")
            for switch in infrastructure.get(group, [])
        ],
        silent_sources=[
            device["name"]
            for device in inventory["devices"]
            if device["image"] == "metropolis/reachability-probe:dev"
        ],
    )
    collector.manifest["attack_events_path"] = "attacks.jsonl"
    collector.manifest["metrics"] = {
        "enabled": metrics_enabled,
        "status": "running" if metrics_enabled else "disabled",
        "sample_interval_seconds": metrics_interval,
    }
    (collector.run_dir / "attacks.jsonl").touch(exist_ok=True)
    collector._write_manifest()
    timeline = collector.run_dir / "timeline.jsonl"
    exception_records = firewall_exceptions or []
    collector.manifest["firewall"] = {
        "policy_sha256": (metadata or {}).get("firewall_policy_sha256", ""),
        "attack_exceptions": exception_records,
        "configuration_requested": configure_routers and firewall_commands is not None,
        "restored_baseline": False,
        "failures": [],
    }
    collector._write_manifest()

    def record(event: str, **attributes) -> None:
        with timeline.open("a", encoding="utf-8") as output:
            output.write(
                json.dumps(
                    {
                        "timestamp": utc_now(),
                        "run_id": run_id,
                        "event": event,
                        **attributes,
                    }
                )
                + "\n"
            )
            output.flush()

    stop_event = threading.Event()
    # The extra time permits startup and router configuration before PCAP capture.
    thread = threading.Thread(
        target=collector.run,
        args=(
            duration
            + (startup_timeout if start_lab else 0)
            + (startup_timeout if configure_routers else 0)
            + (startup_timeout if exception_records else 0)
            + 60,
        ),
        kwargs={"stop_event": stop_event},
        daemon=True,
    )
    thread.start()
    capture_result = None
    capture_manifest = None
    orchestration_failures: list[str] = []
    interrupted = False
    exception_apply_attempted = False
    resource_collector = None
    resource_thread = None
    resource_failures: list[dict] = []
    profile_session = None

    def collect_resources() -> None:
        try:
            resource_collector.run(stop_event)
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
            resource_failures.append(
                {"source": "collector", "reason": f"resource collector failed: {exc}"}
            )

    try:
        if metrics_enabled:
            try:
                resource_collector = resource_collector_factory(
                    client,
                    project.project_id,
                    collector.run_dir,
                    run_id,
                    docker_hosts,
                    interval=metrics_interval,
                )
                resource_thread = threading.Thread(
                    target=collect_resources, name="metropolis-resources", daemon=True
                )
                resource_thread.start()
                record("metrics_started", sample_interval_seconds=metrics_interval)
            except Exception as exc:  # noqa: BLE001 - retain PCAP/log collection
                resource_failures.append(
                    {"source": "collector", "reason": f"resource setup failed: {exc}"}
                )
        record("run_started", project_status=project.status)
        if start_lab:
            kinds = {node.name: node.kind for node in topology.nodes}
            images = {
                device["name"]: device["image"] for device in inventory["devices"]
            }
            started = start_all(
                client, project.project_id, kinds, images, timeout=startup_timeout
            )
            record(
                "lab_start_completed", started=started.started, failures=started.failed
            )
            orchestration_failures.extend(started.failed)
        if configure_routers:
            exception_apply_attempted = bool(exception_records)
            results = configure_project_routers(
                client,
                project.project_id,
                router_scripts(infrastructure),
                username=router_username,
                password=router_password,
                firewall_commands=firewall_commands,
            )
            router_failures = [
                f"{item.node}: {item.error or ', '.join(item.missing)}"
                for item in results
                if item.error or item.missing
            ]
            record(
                "router_configuration_completed",
                configured=[
                    item.node for item in results if not item.error and not item.missing
                ],
                failures=router_failures,
                attack_exceptions=exception_records,
            )
            orchestration_failures.extend(router_failures)
            collector.manifest["firewall"]["failures"].extend(router_failures)
            if not exception_records:
                collector.manifest["firewall"]["restored_baseline"] = (
                    firewall_commands is not None and not router_failures
                )
            collector._write_manifest()
        if profile is not None:
            profile_session = profile_session_factory(
                client,
                project.project_id,
                topology,
                profile,
                collector.run_dir,
                run_id,
                docker_hosts,
            )
            collector.manifest["profiles"] = copy.deepcopy(profile_session.result)
            collector._write_manifest()
            profile_session.prepare()
            profile_session.apply()
            collector.manifest["profiles"] = copy.deepcopy(profile_session.result)
            collector._write_manifest()
            record("profile_applied", name=profile["name"], configuration_verified=True)
        declared = [link["id"] for link in link_plan["links"]]
        resolution = resolve_links(client, project.project_id, topology, declared)
        chosen = selected_links if selected_links is not None else resolution.capturable
        collector.manifest["selected_capture_links"] = list(chosen)
        collector._write_manifest()
        coverage = coverage_report(
            resolution, chosen, link_plan, inventory, infrastructure
        )
        preflight = [
            f"{name}: {detail['status']}"
            for name, detail in resolution.status.items()
            if name in resolution.declared_ids and detail["status"] != "built"
        ]
        if not chosen:
            orchestration_failures.append("no built, capturable PCAP links")
        else:
            record("capture_started", selected_links=chosen)
            capture_result = capture_fn(
                client,
                project.project_id,
                resolution,
                chosen,
                duration,
                dataset / "captures",
                {
                    "link_status": resolution.status,
                    "coverage": coverage,
                    **(metadata or {}),
                },
                run_id=run_id,
                preflight_failures=preflight,
            )
            capture_manifest = json.loads(capture_result.manifest_path.read_text())
            record(
                "capture_finished",
                captured_links=sorted(capture_result.files),
                failures=capture_result.failed,
            )
            orchestration_failures.extend(capture_result.failed)
    except KeyboardInterrupt:
        interrupted = True
        orchestration_failures.append("experiment interrupted")
    except Exception as exc:  # noqa: BLE001 - always stop telemetry and retain manifest
        orchestration_failures.append(f"experiment failed: {exc}")
    finally:
        if profile_session is not None:
            try:
                profile_session.restore()
            except (Exception, KeyboardInterrupt) as exc:
                orchestration_failures.append(f"profile restoration failed: {exc}")
            finally:
                try:
                    profile_session.close()
                except (Exception, KeyboardInterrupt) as exc:
                    orchestration_failures.append(
                        f"profile connection cleanup failed: {exc}"
                    )
            collector.manifest["profiles"] = copy.deepcopy(profile_session.result)
            try:
                collector._write_manifest()
                record("profile_restored", restored=profile_session.result["restored"])
            except OSError as exc:
                orchestration_failures.append(
                    f"profile cleanup checkpoint failed: {exc}"
                )
        if exception_apply_attempted:
            try:
                if baseline_firewall_commands is None:
                    raise RuntimeError("baseline firewall policy was not supplied")
                restored = configure_project_routers(
                    client,
                    project.project_id,
                    router_scripts(infrastructure),
                    username=router_username,
                    password=router_password,
                    firewall_commands=baseline_firewall_commands,
                )
                restore_failures = [
                    f"{item.node}: {item.error or ', '.join(item.missing)}"
                    for item in restored
                    if item.error or item.missing
                ]
                collector.manifest["firewall"][
                    "restored_baseline"
                ] = not restore_failures
                collector.manifest["firewall"]["failures"].extend(restore_failures)
                orchestration_failures.extend(
                    f"firewall restore: {reason}" for reason in restore_failures
                )
                record(
                    "firewall_baseline_restored",
                    restored=not restore_failures,
                    failures=restore_failures,
                )
            except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
                reason = f"firewall baseline restore failed: {exc}"
                collector.manifest["firewall"]["failures"].append(reason)
                orchestration_failures.append(reason)
                record("firewall_baseline_restore_failed", reason=reason)
            collector._write_manifest()
        stop_event.set()
        if resource_thread is not None and resource_thread.ident is not None:
            try:
                resource_thread.join(timeout=60)
            except KeyboardInterrupt:
                interrupted = True
                resource_thread.join(timeout=60)
            if resource_thread.is_alive():
                resource_failures.append(
                    {"source": "collector", "reason": "resource collector did not stop"}
                )
        try:
            thread.join(timeout=60)
        except KeyboardInterrupt:
            interrupted = True
            thread.join(timeout=60)
        if thread.is_alive():
            orchestration_failures.append("device log collector did not stop")
        if capture_manifest is not None:
            collector.manifest["captures"] = [str(capture_result.manifest_path)]
            collector.manifest["capture"] = {
                "manifest_path": str(capture_result.manifest_path),
                "links": capture_manifest["captures"],
                "unavailable_declared_links": {
                    name: detail
                    for name, detail in capture_manifest["link_status"].items()
                    if name in resolution.declared_ids and detail["status"] != "built"
                },
                "failures": capture_result.failed,
            }
        if metrics_enabled:
            resource_result = {
                "status": "failed",
                "container_samples_path": "metrics/containers.jsonl",
                "compute_samples_path": "metrics/computes.jsonl",
                "sources": {},
                "computes": {},
                "failures": [],
            }
            try:
                if resource_collector is not None and not (
                    resource_thread is not None and resource_thread.is_alive()
                ):
                    if resource_thread is None or resource_thread.ident is None:
                        resource_collector.close()
                    resource_result.update(resource_collector.snapshot())
                resource_result["failures"].extend(resource_failures)
                if resource_result["failures"]:
                    resource_result["status"] = "failed"
                metrics = metrics_report_fn(
                    collector.run_dir,
                    run_id,
                    resource_result,
                    capture_manifest,
                    capture_result.manifest_path if capture_result else None,
                    interval=metrics_interval,
                )
            except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
                if isinstance(exc, KeyboardInterrupt):
                    interrupted = True
                metrics = {
                    "enabled": True,
                    "status": "failed",
                    "sample_interval_seconds": metrics_interval,
                    "resource": resource_result,
                    "failures": [
                        *resource_result["failures"],
                        {"source": "report", "reason": f"metrics report failed: {exc}"},
                    ],
                }
            collector.manifest["metrics"] = metrics
            for failure in metrics.get("failures", []):
                collector.manifest["failures"].append(
                    {
                        "source": f"metrics:{failure['source']}",
                        "reason": failure["reason"],
                    }
                )
            if metrics["status"] != "complete" and not metrics.get("failures"):
                collector.manifest["failures"].append(
                    {"source": "metrics", "reason": "metrics reporting incomplete"}
                )
            record("metrics_finished", status=metrics["status"])
        if profile_session is not None:
            try:
                profile_session.observe(collector.manifest.get("metrics"))
            except (Exception, KeyboardInterrupt) as exc:
                orchestration_failures.append(
                    f"profile observations unavailable: {exc}"
                )
            collector.manifest["profiles"] = copy.deepcopy(profile_session.result)
            for failure in profile_session.result["failures"]:
                collector.manifest["failures"].append(
                    {
                        "source": f"profile:{failure['source']}",
                        "reason": failure["reason"],
                    }
                )
        for reason in orchestration_failures:
            collector.manifest["failures"].append(
                {"source": "experiment", "reason": reason}
            )
        if interrupted:
            collector.manifest["status"] = "interrupted"
        elif collector.manifest["failures"]:
            collector.manifest["status"] = "failed"
        record(
            "run_finished",
            status=collector.manifest["status"],
            failures=orchestration_failures,
        )
        collector._write_manifest()
    return collector.manifest
