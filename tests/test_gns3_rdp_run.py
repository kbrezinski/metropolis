"""RDP run orchestration with synthetic GNS3 and Docker boundaries."""

from __future__ import annotations

import json
import struct
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, FormatChecker

GNS3 = Path(__file__).resolve().parents[1] / "scripts" / "gns3"
sys.path.insert(0, str(GNS3))

import experiment  # noqa: E402
import rdp_run  # noqa: E402
from capture import CaptureResult, LinkResolution, capture_for  # noqa: E402


def _inventory():
    return {
        "devices": [
            {
                "name": "WORKSTATION",
                "address": "192.168.30.9/24",
                "image": "example/desktop:v2",
                "environment": {
                    "RDP_TARGET_NODE": "DESKTOP-SERVER",
                    "RDP_USERNAME": "tester",
                    "RDP_PASSWORD": "do-not-record",
                },
            },
            {
                "name": "DESKTOP-SERVER",
                "address": "192.168.40.7/24",
                "image": "example/rdp-relay:v3",
                "protocols": ["rdp", "rdp-udp-synthetic", "https", "telemetry-relay"],
            },
        ]
    }


def _links():
    return {
        "links": [
            {
                "id": "client-access",
                "kind": "access",
                "endpoints": [{"node": "WORKSTATION"}, {"node": "SW-CLIENT"}],
            },
            {
                "id": "client-uplink",
                "kind": "trunk",
                "endpoints": [{"node": "SW-CLIENT"}, {"node": "ROUTER-A"}],
            },
            {
                "id": "router-transit",
                "kind": "transit",
                "endpoints": [{"node": "ROUTER-A"}, {"node": "ROUTER-B"}],
            },
            {
                "id": "server-uplink",
                "kind": "access",
                "endpoints": [{"node": "ROUTER-B"}, {"node": "SW-SERVER"}],
            },
            {
                "id": "server-access",
                "kind": "access",
                "endpoints": [{"node": "DESKTOP-SERVER"}, {"node": "SW-SERVER"}],
            },
        ]
    }


def _gns3_nodes():
    return [
        {
            "name": "WORKSTATION",
            "node_type": "docker",
            "status": "started",
            "compute_id": "remote-compute",
            "properties": {
                "image": "example/desktop:v2",
                "container_id": "client-container",
            },
        },
        {
            "name": "DESKTOP-SERVER",
            "node_type": "docker",
            "status": "started",
            "compute_id": "remote-compute",
            "properties": {
                "image": "example/rdp-relay:v3",
                "container_id": "server-container",
            },
        },
    ]


def _run(
    executor,
    amplified=None,
    nodes=None,
    duration=30,
    readiness_timeout=0,
    inventory=None,
):
    client = SimpleNamespace(project_nodes=lambda _project: nodes or _gns3_nodes())
    return rdp_run.RdpRun(
        client,
        "project-1",
        inventory or _inventory(),
        {"remote-compute": "ssh://operator@gns3-compute.example"},
        duration,
        amplified or [],
        routers=["ROUTER-A", "ROUTER-B"],
        readiness_timeout=readiness_timeout,
        executor=executor,
    )


def test_inventory_resolves_changed_names_and_rejects_invalid_targets():
    sessions = rdp_run.declared_rdp(_inventory(), ["DESKTOP-SERVER"])
    assert sessions == [
        {
            "source": "WORKSTATION",
            "source_address": "192.168.30.9",
            "destination": "DESKTOP-SERVER",
            "destination_address": "192.168.40.7",
        }
    ]
    with pytest.raises(ValueError, match="not a declared RDP UDP server"):
        rdp_run.declared_rdp(_inventory(), ["WORKSTATION"])
    invalid = _inventory()
    invalid["devices"][0]["environment"]["RDP_TARGET_NODE"] = "OLD-SERVER"
    with pytest.raises(ValueError, match="unknown RDP server"):
        rdp_run.declared_rdp(invalid, [])


def test_run_uses_remote_compute_and_records_actual_capture_coverage():
    calls = []
    lock = threading.Lock()

    def executor(node, hosts, command, image, _timeout):
        with lock:
            calls.append((node["name"], hosts[node["compute_id"]], command, image))
        if command[-1] == "ready":
            return {"tcp_ready": True, "udp_ready": True}
        if "--target-host" in command:
            return {
                "status": "complete",
                "source": "WORKSTATION",
                "target_host": "192.168.40.7",
                "tcp_success": True,
                "udp_sent": 2,
                "udp_received": 2,
                "udp_response_bytes": 1024,
            }
        return {"mode": command[command.index("--mode") + 1], "verified": True}

    run = _run(executor, amplified=["DESKTOP-SERVER"])
    run.prepare()
    run.set_capture_points(
        _links(),
        [
            "client-access",
            "client-uplink",
            "router-transit",
            "server-uplink",
            "server-access",
        ],
        {
            "client-access": "gns3-1",
            "client-uplink": "gns3-2",
            "router-transit": "gns3-3",
            "server-uplink": "gns3-4",
            "server-access": "gns3-5",
        },
    )
    assert run.snapshot()["sessions"][0]["capture_selected"] is True
    assert run.snapshot()["sessions"][0]["capture_started"] is False
    run.mark_capture_started(
        [
            "client-access",
            "client-uplink",
            "router-transit",
            "server-uplink",
            "server-access",
        ]
    )
    run.start()
    run.mark_capture_saved({"client-access": "a.pcap"})
    run.finish()
    result = run.snapshot()
    assert result["status"] == "failed"
    assert result["udp_modes"]["DESKTOP-SERVER"]["status"] == "restored"
    assert result["sessions"][0]["status"] == "complete"
    assert result["sessions"][0]["capture_started"] is True
    assert result["sessions"][0]["capture_saved"] is False
    assert result["sessions"][0]["udp_response_bytes"] == 1024
    assert any("PCAP was not saved" in item["reason"] for item in result["failures"])
    assert result["sessions"][0]["capture_links"]["routed"] == ["router-transit"]
    assert [item[0] for item in calls] == [
        "DESKTOP-SERVER",
        "DESKTOP-SERVER",
        "WORKSTATION",
        "DESKTOP-SERVER",
    ]
    assert all(item[1] == "ssh://operator@gns3-compute.example" for item in calls)
    assert calls[0][3] == "example/rdp-relay:v3"
    assert calls[2][2][2:4] == ["--target-host", "192.168.40.7"]
    assert "do-not-record" not in json.dumps(result)
    assert "do-not-record" not in json.dumps(calls)
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "schemas/experiment-run.schema.json"
        ).read_text()
    )
    Draft202012Validator(
        {**schema["properties"]["rdp"], "$defs": schema["$defs"]},
        format_checker=FormatChecker(),
    ).validate(result)


def test_uncertain_mode_change_is_restored_after_session_failure():
    calls = []

    def executor(_node, _hosts, command, _image, _timeout):
        calls.append(command)
        if command[-1] == "ready":
            return {"tcp_ready": True, "udp_ready": True}
        if "--target-host" in command:
            raise TimeoutError("remote execution deadline")
        if "amplify" in command:
            raise TimeoutError("reply was lost")
        return {"mode": "benign", "verified": True}

    run = _run(executor, amplified=["DESKTOP-SERVER"])
    run.prepare()
    run.start()
    run.finish()
    result = run.snapshot()
    assert result["status"] == "failed"
    assert result["sessions"][0]["status"] == "failed"
    assert result["udp_modes"]["DESKTOP-SERVER"]["status"] == "restored"
    assert any("benign" in command for command in calls)


def test_repeatable_amp_restores_other_server_when_one_restore_fails():
    inventory = _inventory()
    inventory["devices"].append(
        {
            "name": "SECOND-SERVER",
            "address": "192.168.50.7/24",
            "image": "example/rdp-server:v2",
            "protocols": ["rdp", "rdp-udp-synthetic"],
        }
    )
    nodes = _gns3_nodes()
    nodes.append(
        {
            "name": "SECOND-SERVER",
            "node_type": "docker",
            "status": "started",
            "compute_id": "remote-compute",
            "properties": {
                "image": "example/rdp-server:v2",
                "container_id": "second-container",
            },
        }
    )
    restored = []

    def executor(node, _hosts, command, _image, _timeout):
        if command[-1] == "ready":
            return {"tcp_ready": True, "udp_ready": True}
        if "--target-host" in command:
            return {
                "status": "complete",
                "source": "WORKSTATION",
                "target_host": "192.168.40.7",
                "tcp_success": True,
                "udp_sent": 1,
                "udp_received": 1,
            }
        mode = command[command.index("--mode") + 1]
        if mode == "benign":
            restored.append(node["name"])
            if node["name"] == "DESKTOP-SERVER":
                raise TimeoutError
        return {"mode": mode, "verified": True}

    run = _run(
        executor,
        amplified=["DESKTOP-SERVER", "SECOND-SERVER"],
        nodes=nodes,
        inventory=inventory,
    )
    run.prepare()
    run.start()
    run.finish()
    assert restored == ["DESKTOP-SERVER", "SECOND-SERVER"]
    assert run.snapshot()["udp_modes"]["SECOND-SERVER"]["status"] == "restored"
    assert run.snapshot()["status"] == "failed"


def test_routed_uplink_is_required_and_same_router_vlan_path_uses_both_uplinks():
    run = _run(lambda *_args: pytest.fail("capture planning must not execute nodes"))
    declared = [link["id"] for link in _links()["links"]]
    built = {name: f"gns3-{index}" for index, name in enumerate(declared)}
    run.set_capture_points(
        _links(),
        [name for name in declared if name != "router-transit"],
        built,
    )
    session = run.snapshot()["sessions"][0]
    assert session["capture_links"]["routed"] == ["router-transit"]
    assert session["capture_selected"] is False

    same_router = _links()
    same_router["links"] = [
        link for link in same_router["links"] if link["id"] != "router-transit"
    ]
    same_router["links"][2]["endpoints"][0]["node"] = "ROUTER-A"
    other = _run(lambda *_args: pytest.fail("capture planning must not execute nodes"))
    selected = [link["id"] for link in same_router["links"]]
    other.set_capture_points(
        same_router,
        selected,
        {name: f"gns3-{index}" for index, name in enumerate(selected)},
    )
    session = other.snapshot()["sessions"][0]
    assert session["capture_path_status"] == "resolved"
    assert session["capture_links"]["routed"] == []
    assert session["capture_links"]["source"] == [
        "client-access",
        "client-uplink",
    ]
    assert session["capture_links"]["destination"] == [
        "server-access",
        "server-uplink",
    ]
    assert session["capture_selected"] is True


def test_unstarted_client_and_missing_capture_points_are_reported():
    nodes = _gns3_nodes()
    nodes[0]["status"] = "stopped"
    calls = []

    def executor(*args):
        calls.append(args)
        raise AssertionError("unstarted client must not execute")

    run = _run(executor, nodes=nodes)
    run.prepare()
    run.set_capture_points(_links(), ["server-access"], {"server-access": "gns3-2"})
    run.start()
    run.finish()
    assert not calls
    assert run.snapshot()["status"] == "failed"
    assert run.snapshot()["sessions"][0]["capture_selected"] is False
    assert run.snapshot()["sessions"][0]["status"] == "failed"


def test_readiness_failure_prevents_session_without_executing_client():
    commands = []

    def executor(_node, _hosts, command, _image, _timeout):
        commands.append(command)
        return {"tcp_ready": False, "udp_ready": True}

    run = _run(executor, readiness_timeout=0)
    run.prepare()
    run.start()
    run.finish()
    assert commands == [["python3", rdp_run.UDP_HELPER, "ready"]]
    assert run.snapshot()["readiness"]["DESKTOP-SERVER"]["tcp_ready"] is False
    assert run.snapshot()["sessions"][0]["status"] == "failed"
    assert any(
        "readiness failed" in item["reason"] for item in run.snapshot()["failures"]
    )


def test_failed_benign_reset_skips_client_and_retries_on_cleanup():
    calls = []

    def executor(_node, _hosts, command, _image, _timeout):
        calls.append(command)
        if command[-1] == "ready":
            return {"tcp_ready": True, "udp_ready": True}
        if "--target-host" in command:
            pytest.fail(
                "client must not run while prior amplification mode is uncertain"
            )
        if len(calls) == 2:
            raise TimeoutError("lost reset reply")
        return {"mode": "benign", "verified": True}

    run = _run(executor)
    run.prepare()
    run.start()
    run.finish()
    assert len(calls) == 3
    assert run.snapshot()["sessions"][0]["reason"] == "server_or_udp_mode_unavailable"
    assert run.snapshot()["udp_modes"]["DESKTOP-SERVER"]["status"] == "restored"
    assert run.snapshot()["status"] == "failed"


def test_nonzero_client_exit_retains_safe_failure_reason_without_raw_output():
    class Engine:
        def inspect_container(self, cid):
            return {"Id": cid, "Config": {"Image": "example/desktop:v2"}}

        def exec_create(self, _cid, *, cmd, stdout, stderr):
            assert cmd[1] == rdp_run.CLIENT_HELPER
            assert stdout and stderr
            return {"Id": "exec-1"}

        def exec_start(self, _exec_id):
            return json.dumps(
                {
                    "status": "failed",
                    "source": "WORKSTATION",
                    "target_host": "192.168.40.7",
                    "tcp_success": False,
                    "udp_sent": 1,
                    "udp_received": 0,
                    "reason": "authentication_not_observed",
                    "password": "do-not-record",
                }
            ).encode()

        def exec_inspect(self, _exec_id):
            return {"ExitCode": 1}

    command = ["python3", rdp_run.CLIENT_HELPER, "--target-host", "192.168.40.7"]
    parsed = rdp_run._exec(
        Engine(),
        "client-container",
        {"command": command, "image": "example/desktop:v2"},
    )
    assert parsed["reason"] == "authentication_not_observed"
    assert "do-not-record" not in json.dumps(parsed)

    calls = []

    def executor(_node, _hosts, command, _image, _timeout):
        calls.append(command)
        if command[-1] == "ready":
            return {"tcp_ready": True, "udp_ready": True}
        if "--mode" in command:
            return {"mode": "benign", "verified": True}
        return parsed

    run = _run(executor)
    run.prepare()
    run.start()
    run.finish()
    session = run.snapshot()["sessions"][0]
    assert session["status"] == "failed"
    assert session["reason"] == "authentication_not_observed"
    assert run.snapshot()["udp_modes"]["DESKTOP-SERVER"]["status"] == "benign_verified"
    assert calls[1][-1] == "benign"
    assert "do-not-record" not in json.dumps(run.snapshot())
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "schemas/experiment-run.schema.json"
        ).read_text()
    )
    Draft202012Validator(
        {**schema["properties"]["rdp"], "$defs": schema["$defs"]},
        format_checker=FormatChecker(),
    ).validate(run.snapshot())


def test_execute_sends_only_helper_command_to_hosting_compute(monkeypatch):
    captured = {}

    class Proxy:
        def __init__(self, base_url, *, timeout, worker_target):
            captured.update(url=base_url, timeout=timeout, worker_target=worker_target)

        def _request(self, operation, cid, *, timeout):
            captured.update(
                request=json.loads(operation), cid=cid, request_timeout=timeout
            )
            return {"status": "complete"}

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(rdp_run, "RemoteDockerClient", Proxy)
    node = _gns3_nodes()[0]
    command = ["python3", rdp_run.CLIENT_HELPER, "--target-host", "192.168.40.7"]
    result = rdp_run.execute(
        node,
        {"remote-compute": "ssh://operator@gns3-compute.example"},
        command,
        "example/desktop:v2",
        45,
    )
    assert result["status"] == "complete"
    assert captured["url"] == "ssh://operator@gns3-compute.example"
    assert captured["cid"] == "client-container"
    assert captured["request"]["command"] == command
    assert captured["closed"] is True


def test_capture_start_hook_runs_after_all_acks_and_stops_after_interrupt(tmp_path):
    events = []

    class Client:
        def __init__(self, fail_start=False):
            self.fail_start = fail_start

        def start_capture(self, _project, link_id):
            events.append(("start", link_id))
            if self.fail_start and link_id == "gns3-2":
                raise RuntimeError("start rejected")
            return {"capture_file_path": f"/compute/{link_id}.pcap"}

        def stop_capture(self, _project, link_id):
            events.append(("stop", link_id))
            return {"capture_file_path": f"/compute/{link_id}.pcap"}

        def download_pcap(self, _project, _link_id, destination):
            header = b"\xd4\xc3\xb2\xa1" + struct.pack("<HHIIII", 2, 4, 0, 0, 65535, 1)
            packet = struct.pack("<IIII", 1, 0, 4, 4) + b"abcd"
            destination.write_bytes(header + packet)

    resolution = LinkResolution(
        resolved={"A": "gns3-1", "B": "gns3-2"},
        status={"A": {"status": "built"}, "B": {"status": "built"}},
        declared_ids={"A", "B"},
    )

    def on_started(result):
        events.append(("hook", list(result.started)))
        raise KeyboardInterrupt

    result = capture_for(
        Client(),
        "project-1",
        resolution,
        ["A", "B"],
        1,
        tmp_path,
        {},
        on_started=on_started,
        sleep=lambda _duration: events.append("sleep"),
    )
    assert events[:3] == [
        ("start", "gns3-1"),
        ("start", "gns3-2"),
        ("hook", ["A", "B"]),
    ]
    assert "sleep" not in events
    assert [event for event in events if event[0] == "stop"] == [
        ("stop", "gns3-1"),
        ("stop", "gns3-2"),
    ]
    assert set(result.files) == {"A", "B"}
    assert result.interrupted is True
    assert any("KeyboardInterrupt" in reason for reason in result.failed)

    events.clear()
    partial = capture_for(
        Client(fail_start=True),
        "project-1",
        resolution,
        ["A", "B"],
        1,
        tmp_path,
        {},
        on_started=lambda _result: events.append("hook"),
    )
    assert "hook" not in events
    assert partial.started == partial.stopped == ["A"]


def test_experiment_runs_session_only_after_captures_start_and_restores_on_interrupt(
    tmp_path, monkeypatch
):
    events = []

    class Logs:
        def __init__(self, _client, _project, run_dir, _run_id, _hosts, **_kwargs):
            self.run_dir = run_dir
            run_dir.mkdir(parents=True)
            self.manifest = {
                "run_id": _run_id,
                "status": "running",
                "failures": [],
                "logs": {"normalized_path": "logs/devices.jsonl", "sources": {}},
            }

        def run(self, _duration, *, stop_event):
            stop_event.wait(5)
            self.manifest["status"] = "complete"

        def _write_manifest(self):
            (self.run_dir / "manifest.json").write_text(json.dumps(self.manifest))

    def executor(node, _hosts, command, _image, _timeout):
        events.append(("exec", node["name"], command))
        if command[-1] == "ready":
            return {"tcp_ready": True, "udp_ready": True}
        if "--target-host" in command:
            return {
                "status": "complete",
                "source": "WORKSTATION",
                "target_host": "192.168.40.7",
                "tcp_success": True,
                "udp_sent": 1,
                "udp_received": 1,
            }
        return {"mode": command[command.index("--mode") + 1], "verified": True}

    def rdp_factory(*args, **kwargs):
        return rdp_run.RdpRun(*args, **kwargs, executor=executor)

    monkeypatch.setattr(experiment, "RdpRun", rdp_factory)
    expected_links = [link["id"] for link in _links()["links"]]
    resolution = LinkResolution(
        resolved={name: f"gns3-{index}" for index, name in enumerate(expected_links)},
        status={name: {"status": "built"} for name in expected_links},
        declared_ids=set(expected_links),
    )
    monkeypatch.setattr(experiment, "resolve_links", lambda *_args: resolution)
    monkeypatch.setattr(experiment, "coverage_report", lambda *_args: {})

    def capture(
        _client, _project, _resolution, selected, _duration, output, _metadata, **kw
    ):
        events.append("capture_started")
        assert selected == expected_links
        captured = CaptureResult(started=list(selected))
        kw["on_started"](captured)
        events.append("capture_interrupted")
        output_dir = output / kw["run_id"]
        output_dir.mkdir(parents=True)
        captured.manifest_path = output_dir / "manifest.json"
        captured.manifest_path.write_text(
            json.dumps(
                {"captures": {}, "link_status": resolution.status, "failures": []}
            )
        )
        captured.files = {name: str(output_dir / f"{name}.pcap") for name in selected}
        captured.failed.append("capture interrupted: KeyboardInterrupt")
        captured.interrupted = True
        return captured

    manifest = experiment.run_experiment(
        SimpleNamespace(project_nodes=lambda _project: _gns3_nodes()),
        SimpleNamespace(project_id="project-1", status="opened"),
        SimpleNamespace(nodes=[]),
        _inventory(),
        {"routers": [{"name": "ROUTER-A"}, {"name": "ROUTER-B"}]},
        _links(),
        tmp_path,
        "rdp-run-1",
        30,
        {"remote-compute": "ssh://operator@gns3-compute.example"},
        selected_links=expected_links,
        metrics_enabled=False,
        collector_factory=Logs,
        capture_fn=capture,
        rdp_udp_amplify=["DESKTOP-SERVER"],
    )
    kinds = [event if isinstance(event, str) else event[0] for event in events]
    amplify_index = next(
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and "amplify" in event[2]
    )
    assert kinds.index("capture_started") < amplify_index
    assert events[-1][2][events[-1][2].index("--mode") + 1] == "benign"
    assert manifest["rdp"]["sessions"][0]["capture_saved"] is True
    assert manifest["rdp"]["udp_modes"]["DESKTOP-SERVER"]["status"] == "restored"
    assert manifest["status"] == "interrupted"
    assert any(
        "capture interrupted" in failure["reason"] for failure in manifest["failures"]
    )
    assert (
        "do-not-record" not in (tmp_path / "runs/rdp-run-1/manifest.json").read_text()
    )
