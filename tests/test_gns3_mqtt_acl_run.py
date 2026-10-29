"""MQTT ACL checks use real GNS3 node origins and broker evidence."""

from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

GNS3 = Path(__file__).resolve().parents[1] / "scripts" / "gns3"
sys.path.insert(0, str(GNS3))

import mqtt_acl_run  # noqa: E402

HMI = Path(__file__).resolve().parents[1] / "testbeds/metropolis/devices/operations/hmi"
RUNTIME = Path(__file__).resolve().parents[1] / "testbeds/metropolis/devices/runtime"
sys.path.insert(0, str(HMI))
sys.path.insert(0, str(RUNTIME))
sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[1]
        / "testbeds/metropolis/devices/operations/scada"
    ),
)
import scada_collector  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "mqtt_acl_probe", HMI / "mqtt_acl_probe.py"
)
assert spec and spec.loader
mqtt_acl_probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mqtt_acl_probe)


def _inventory():
    return {
        "devices": [
            {
                "name": "HMI-ALPHA",
                "address": "192.0.2.11/24",
                "image": "example/hmi:v2",
                "environment": {
                    "MQTT_ACCESS_ROLE": "hmi",
                    "MQTT_HOST": "192.0.2.30",
                    "MQTT_USERNAME": "alpha",
                    "MQTT_PASSWORD": "synthetic-alpha",
                    "MQTT_TOPIC": "lab/alpha/command",
                    "HMI_CELL_ID": "alpha",
                },
            },
            {
                "name": "HMI-BETA",
                "address": "198.51.100.11/24",
                "image": "example/hmi:v2",
                "environment": {
                    "MQTT_ACCESS_ROLE": "hmi",
                    "MQTT_HOST": "192.0.2.30",
                    "MQTT_USERNAME": "beta",
                    "MQTT_PASSWORD": "synthetic-beta",
                    "MQTT_TOPIC": "lab/beta/command",
                    "HMI_CELL_ID": "beta",
                },
            },
            {
                "name": "BROKER",
                "address": "192.0.2.30/24",
                "image": "example/broker:v2",
                "environment": {"MQTT_OPERATOR_USERNAME": "experiment"},
            },
            {
                "name": "SCADA",
                "address": "192.0.2.40/24",
                "image": "example/scada:v2",
                "environment": {
                    "MQTT_ACCESS_ROLE": "scada",
                    "SCADA_TARGETS": json.dumps([{"id": "alpha"}, {"id": "beta"}]),
                },
            },
        ]
    }


def _nodes():
    return [
        {
            "name": name,
            "node_type": "docker",
            "status": "started",
            "compute_id": "compute-a" if name == "HMI-ALPHA" else "compute-b",
            "properties": {
                "image": "example/hmi:v2"
                if name.startswith("HMI")
                else "example/broker:v2"
                if name == "BROKER"
                else "example/scada:v2",
                "container_id": f"container-{name}",
            },
        }
        for name in ("HMI-ALPHA", "BROKER", "SCADA")
    ]


def _links():
    return {
        "links": [
            {
                "id": "alpha-attach",
                "kind": "access",
                "endpoints": [{"node": "HMI-ALPHA"}, {"node": "switch-a"}],
            },
            {
                "id": "broker-attach",
                "kind": "access",
                "endpoints": [{"node": "BROKER"}, {"node": "switch-b"}],
            },
            {
                "id": "scada-attach",
                "kind": "access",
                "endpoints": [{"node": "SCADA"}, {"node": "switch-b"}],
            },
        ]
    }


def _run(executor):
    client = SimpleNamespace(project_nodes=lambda _: _nodes())
    return mqtt_acl_run.MqttAclRun(
        client,
        "project-1",
        _inventory(),
        {"compute-a": "ssh://a@example", "compute-b": "ssh://b@example"},
        "run123",
        "HMI-ALPHA",
        "HMI-BETA",
        executor=executor,
    )


def test_declared_probe_rejects_shared_identity_and_wrong_broker():
    inventory = _inventory()
    selected = mqtt_acl_run.declared_probe(inventory, "HMI-ALPHA", "HMI-BETA")
    assert selected["scada"] == "SCADA"
    assert selected["source_address"] == "192.0.2.11"
    inventory["devices"][1]["environment"]["MQTT_USERNAME"] = "alpha"
    with pytest.raises(ValueError, match="distinct"):
        mqtt_acl_run.declared_probe(inventory, "HMI-ALPHA", "HMI-BETA")
    inventory["devices"][1]["environment"]["MQTT_USERNAME"] = "beta"
    inventory["devices"][0]["environment"]["MQTT_HOST"] = "192.0.2.99"
    with pytest.raises(ValueError, match="broker address"):
        mqtt_acl_run.declared_probe(inventory, "HMI-ALPHA", "HMI-BETA")
    inventory["devices"][0]["environment"]["MQTT_HOST"] = "192.0.2.30"
    inventory["devices"][3]["environment"]["SCADA_TARGETS"] = json.dumps(
        [{"id": "beta"}]
    )
    with pytest.raises(ValueError, match="both HMI cells"):
        mqtt_acl_run.declared_probe(inventory, "HMI-ALPHA", "HMI-BETA")


def test_broker_denial_requires_a_real_log_entry():
    client_id = "aclprobe-run123-abc"
    logs = (
        f"Received PUBLISH from {client_id} (d0, q1, r0, m1, 'lab/alpha/command', 42 bytes)\n"
        f"Denied PUBLISH from {client_id} (d0, q1, r0, m2, 'lab/beta/command', 42 bytes)\n"
    )
    evidence = mqtt_acl_run._broker_evidence(
        logs, client_id, "lab/alpha/command", "lab/beta/command"
    )
    assert evidence == {
        "own_received": True,
        "own_denied": False,
        "cross_received": False,
        "cross_denied": True,
    }
    assert not mqtt_acl_run._broker_evidence(
        logs, "other-client", "lab/alpha/command", "lab/beta/command"
    )["cross_denied"]


def test_scada_delivery_evidence_is_correlated_and_probe_is_inert(caplog, monkeypatch):
    class Publisher:
        def publish(self, *_args, **_kwargs):
            pytest.fail("probe marker must not publish a PLC acknowledgement")

    monkeypatch.setattr(
        scada_collector,
        "apply_setpoint",
        lambda *_args, **_kwargs: pytest.fail("probe marker must not reach Modbus"),
    )
    bridge = scada_collector.CommandBridge(
        [scada_collector.Target("alpha", "192.0.2.50", "intake", "lab/status")],
        Publisher(),
        port=502,
        unit_id=1,
    )
    topic = "metropolis/plant/alpha/command"
    marker = {
        "acl_probe": True,
        "run_id": "run123",
        "client_id": "aclprobe-run123-abc",
        "kind": "own",
    }
    with caplog.at_level("INFO", logger="metropolis.scada"):
        assert bridge.handle(topic, json.dumps(marker).encode()) is None
    observed = mqtt_acl_run._scada_evidence(
        caplog.text,
        "run123",
        "aclprobe-run123-abc",
        topic,
        "metropolis/plant/beta/command",
    )
    assert observed == {"own_received": True, "cross_received": False}
    assert mqtt_acl_run._scada_evidence(
        caplog.text,
        "another-run",
        "aclprobe-run123-abc",
        topic,
        "metropolis/plant/beta/command",
    ) == {"own_received": False, "cross_received": False}
    cross_marker = {**marker, "kind": "cross"}
    cross_log = "MQTT_ACL_PROBE " + json.dumps(
        {**cross_marker, "topic": "metropolis/plant/beta/command"}
    )
    assert mqtt_acl_run._scada_evidence(
        cross_log,
        "run123",
        "aclprobe-run123-abc",
        topic,
        "metropolis/plant/beta/command",
    )["cross_received"]


def test_hmi_origin_and_broker_evidence_are_required_before_completion():
    operations = []

    def executor(node, hosts, image, kind, args):
        operations.append((node["name"], hosts[node["compute_id"]], kind, args))
        if kind == "probe":
            return {
                "status": "sent",
                "client_id": args["client_id"],
                "source": "HMI-ALPHA",
                "run_id": "run123",
                "connected": True,
                "attempts": [
                    {
                        "kind": "own",
                        "topic": "lab/alpha/command",
                        "timestamp": "2026-10-29T01:00:00Z",
                        "acknowledged": True,
                    },
                    {
                        "kind": "cross",
                        "topic": "lab/beta/command",
                        "timestamp": "2026-10-29T01:00:01Z",
                        "acknowledged": True,
                    },
                ],
            }
        if kind == "scada_evidence":
            return {"own_received": True, "cross_received": False}
        return {
            "own_received": True,
            "own_denied": False,
            "cross_received": False,
            "cross_denied": True,
        }

    run = _run(executor)
    run.prepare()
    run.set_capture_points(
        _links(),
        ["alpha-attach", "broker-attach", "scada-attach"],
        {"alpha-attach": "1", "broker-attach": "2", "scada-attach": "3"},
    )
    run.mark_capture_started(["alpha-attach", "broker-attach", "scada-attach"])
    run.start()
    run.finish()
    assert run.snapshot()["status"] == "pending_capture"
    run.mark_capture_saved(
        {
            "alpha-attach": "alpha.pcap",
            "broker-attach": "broker.pcap",
            "scada-attach": "scada.pcap",
        }
    )
    result = run.snapshot()
    assert result["status"] == "complete"
    assert result["downstream_delivery"] == "blocked_by_broker"
    assert result["subscriber_evidence"] == {
        "own_received": True,
        "cross_received": False,
    }
    assert result["downstream_capture_selected"] is True
    assert [item[0] for item in operations] == ["HMI-ALPHA", "BROKER", "SCADA"]
    assert operations[0][1] == "ssh://a@example"
    assert operations[1][1] == "ssh://b@example"
    assert operations[2][1] == "ssh://b@example"
    assert all("synthetic-" not in str(item) for item in operations)


def test_publish_ack_without_broker_denial_is_not_success():
    def executor(node, _hosts, _image, kind, args):
        if kind == "probe":
            return {
                "status": "sent",
                "client_id": args["client_id"],
                "source": "HMI-ALPHA",
                "run_id": "run123",
                "connected": True,
                "attempts": [
                    {
                        "kind": "own",
                        "topic": "lab/alpha/command",
                        "timestamp": "2026-10-29T01:00:00Z",
                        "acknowledged": True,
                    },
                    {
                        "kind": "cross",
                        "topic": "lab/beta/command",
                        "timestamp": "2026-10-29T01:00:01Z",
                        "acknowledged": True,
                    },
                ],
            }
        return {"own_received": True, "cross_denied": False}

    run = _run(executor)
    run.prepare()
    run.set_capture_points(
        _links(),
        ["alpha-attach", "broker-attach", "scada-attach"],
        {"alpha-attach": "1", "broker-attach": "2", "scada-attach": "3"},
    )
    run.mark_capture_started(["alpha-attach", "broker-attach", "scada-attach"])
    run.start()
    run.finish()
    run.mark_capture_saved(
        {"alpha-attach": "a.pcap", "broker-attach": "b.pcap", "scada-attach": "c.pcap"}
    )
    assert run.snapshot()["status"] == "failed"
    assert run.snapshot()["downstream_delivery"] == "unverified"


@pytest.mark.parametrize(
    "scada_observed",
    [
        {"own_received": False, "cross_received": False},
        {"own_received": True, "cross_received": True},
    ],
)
def test_missing_or_cross_cell_scada_delivery_prevents_completion(scada_observed):
    def executor(_node, _hosts, _image, kind, args):
        if kind == "probe":
            return {
                "status": "sent",
                "client_id": args["client_id"],
                "source": "HMI-ALPHA",
                "run_id": "run123",
                "connected": True,
                "attempts": [
                    {
                        "kind": label,
                        "topic": args[f"{label}_topic"]
                        if label == "own"
                        else args["cross_topic"],
                        "timestamp": "2026-10-29T01:00:00Z",
                        "acknowledged": True,
                    }
                    for label in ("own", "cross")
                ],
            }
        if kind == "evidence":
            return {
                "own_received": True,
                "own_denied": False,
                "cross_received": False,
                "cross_denied": True,
            }
        return scada_observed

    run = _run(executor)
    run.prepare()
    links = ["alpha-attach", "broker-attach", "scada-attach"]
    run.set_capture_points(_links(), links, dict(zip(links, ("1", "2", "3"))))
    run.mark_capture_started(links)
    run.start()
    run.finish()
    run.mark_capture_saved(dict(zip(links, ("a.pcap", "b.pcap", "c.pcap"))))
    assert run.snapshot()["status"] == "failed"
    assert run.snapshot()["downstream_delivery"] == "unverified"
    assert any(item["source"] == "scada" for item in run.snapshot()["failures"])


def test_missing_capture_does_not_launch_probe():
    operations = []
    run = _run(lambda *args: operations.append(args))
    run.prepare()
    run.set_capture_points(
        _links(),
        ["alpha-attach", "broker-attach"],
        {"alpha-attach": "1", "broker-attach": "2", "scada-attach": "3"},
    )
    run.mark_capture_started(["alpha-attach", "broker-attach"])
    run.start()
    run.finish()
    assert run.snapshot()["status"] == "failed"
    assert not operations


def test_missing_scada_node_is_reported():
    run = _run(lambda *_args: pytest.fail("probe must not run"))
    run.client = SimpleNamespace(
        project_nodes=lambda _: [node for node in _nodes() if node["name"] != "SCADA"]
    )
    run.prepare()
    assert run.snapshot()["status"] == "failed"
    assert any(item["source"] == "SCADA" for item in run.snapshot()["failures"])


def test_hmi_helper_payload_cannot_be_interpreted_as_a_setpoint(monkeypatch):
    published = []

    class Publication:
        rc = mqtt_acl_probe.mqtt.MQTT_ERR_SUCCESS

        def wait_for_publish(self, timeout):
            assert timeout == 5

        def is_published(self):
            return True

    class Client:
        def __init__(self, _version, client_id):
            assert client_id == "aclprobe-run123-abc"

        def username_pw_set(self, username, password):
            assert (username, password) == ("alpha", "synthetic-alpha")

        def connect(self, host, port, keepalive):
            assert (host, port, keepalive) == ("192.0.2.30", 1883, 10)

        def loop_start(self):
            self.on_connect(self, None, None, 0, None)

        def publish(self, topic, payload, qos, retain):
            published.append((topic, json.loads(payload), qos, retain))
            return Publication()

        def disconnect(self):
            pass

        def loop_stop(self):
            pass

    monkeypatch.setattr(mqtt_acl_probe.mqtt, "Client", Client)
    monkeypatch.setattr(mqtt_acl_probe, "apply_to", lambda _client: None)
    monkeypatch.setattr(mqtt_acl_probe.time, "sleep", lambda _: None)
    for key, value in {
        "NODE_HOSTNAME": "HMI-ALPHA",
        "MQTT_HOST": "192.0.2.30",
        "MQTT_USERNAME": "alpha",
        "MQTT_PASSWORD": "synthetic-alpha",
        "MQTT_TOPIC": "lab/alpha/command",
    }.items():
        monkeypatch.setenv(key, value)
    result = mqtt_acl_probe.probe(
        "lab/alpha/command", "lab/beta/command", "aclprobe-run123-abc", "run123"
    )
    assert result["status"] == "sent"
    assert [topic for topic, *_ in published] == [
        "lab/alpha/command",
        "lab/beta/command",
    ]
    assert all(qos == 1 and retain is False for _, _, qos, retain in published)
    assert all(
        "command" not in payload and "value" not in payload
        for _, payload, _, _ in published
    )


def test_probe_exec_uses_the_source_nodes_remote_compute(monkeypatch):
    calls = []

    class Proxy:
        def __init__(self, base_url, *, timeout, worker_target):
            calls.append(("connect", base_url, timeout, worker_target))

        def _request(self, operation, cid, *, timeout):
            calls.append(("request", json.loads(operation), cid, timeout))
            return {"status": "sent"}

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(mqtt_acl_run, "RemoteDockerClient", Proxy)
    request = {
        "own_topic": "lab/alpha/command",
        "cross_topic": "lab/beta/command",
        "client_id": "aclprobe-run123-abc",
        "run_id": "run123",
    }
    result = mqtt_acl_run.execute(
        _nodes()[0],
        {"compute-a": "ssh://a@example", "compute-b": "ssh://b@example"},
        "example/hmi:v2",
        "probe",
        request,
    )
    assert result == {"status": "sent"}
    assert calls[0][1] == "ssh://a@example"
    assert calls[1][1]["kind"] == "probe"
    assert calls[1][2] == "container-HMI-ALPHA"
    assert calls[-1] == ("close",)
    assert "synthetic-alpha" not in str(calls)
