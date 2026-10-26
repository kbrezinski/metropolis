"""Owned OVS bandwidth settings, rollback, and compute attribution without GNS3."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/gns3"))
import link_bandwidth  # noqa: E402
from link_bandwidth import LinkBandwidthBackend  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "ovs_link_profile",
    ROOT / "testbeds/metropolis/devices/infrastructure/openvswitch/link_profile.py",
)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

PORT = "00000000-0000-0000-0000-000000000001"
IFACE = "00000000-0000-0000-0000-000000000002"
QOS = "00000000-0000-0000-0000-000000000003"
QUEUE = "00000000-0000-0000-0000-000000000004"


class Ovs:
    def __init__(self):
        self.port = {"_uuid": PORT, "name": "eth2", "qos": None, "interfaces": IFACE}
        self.device = {
            "_uuid": IFACE,
            "name": "eth2",
            "ingress_policing_rate": 64,
            "ingress_policing_burst": 16,
        }
        self.tables = {"QoS": [], "Queue": []}
        self.calls = []
        self.kernel_htb = True
        self.qdisc = "noqueue"
        self.lost_reply = False
        self.bridge = "br0"

    def encoded(self, entries):
        if not entries:
            return json.dumps({"headings": [], "data": []})
        fields = list(entries[0])
        rows = []
        for entry in entries:
            row = []
            for key in fields:
                value = entry[key]
                if key in {"_uuid", "interfaces", "qos"}:
                    value = ["set", []] if value is None else ["uuid", value]
                elif isinstance(value, dict):
                    value = ["map", list(value.items())]
                row.append(value)
            rows.append(row)
        return json.dumps({"headings": fields, "data": rows})

    def __call__(self, *args):
        self.calls.append(args)
        if "port-to-br" in args:
            return self.bridge
        if args[0] == "tc":
            return json.dumps(
                [
                    {
                        "kind": "htb"
                        if self.port["qos"] and self.kernel_htb
                        else self.qdisc,
                        "root": True,
                    }
                ]
            )
        if "find" in args:
            index = args.index("find")
            table, selectors = args[index + 1], args[index + 2 :]
            entries = (
                [self.port]
                if table == "Port"
                else [self.device]
                if table == "Interface"
                else self.tables[table]
            )
            for selector in selectors:
                key, _, value = selector.partition("=")
                entries = [
                    row
                    for row in entries
                    if (
                        row.get("external_ids", {}).get(key.split(":", 1)[1])
                        if key.startswith("external_ids:")
                        else row.get(key)
                    )
                    == value
                ]
            return self.encoded(entries)
        if "--id=@qos" in args:
            tags = {
                key.split(":", 1)[1]: value
                for item in args
                if item.startswith("external_ids:")
                for key, _, value in [item.partition("=")]
            }
            rate = next(
                item.split("=", 1)[1]
                for item in args
                if item.startswith("other-config:max-rate=")
            )
            self.tables["QoS"] = [
                {
                    "_uuid": QOS,
                    "type": "linux-htb",
                    "other_config": {"max-rate": rate},
                    "external_ids": tags,
                    "queues": {0: ["uuid", QUEUE]},
                }
            ]
            self.tables["Queue"] = [
                {
                    "_uuid": QUEUE,
                    "other_config": {"max-rate": rate},
                    "external_ids": tags,
                }
            ]
            self.port["qos"] = QOS
        else:
            if "clear" in args:
                self.port["qos"] = None
            for table in ("QoS", "Queue"):
                if table in args and "destroy" in args:
                    self.tables[table] = []
        for key in ("ingress_policing_rate", "ingress_policing_burst"):
            for item in args:
                if item.startswith(key + "="):
                    self.device[key] = int(item.split("=", 1)[1])
        if self.lost_reply:
            self.lost_reply = False
            raise subprocess.TimeoutExpired(args, 3)
        return ""


@pytest.fixture
def ovs(monkeypatch, tmp_path):
    state = Ovs()
    monkeypatch.setattr(helper, "command", state)
    monkeypatch.setattr(helper.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(helper, "LOCK_DIRECTORY", tmp_path / "profile-locks")
    lock = threading.RLock()
    monkeypatch.setattr(
        helper,
        "_flock",
        lambda _stream, locked: lock.acquire() if locked else lock.release(),
    )
    return state


def request(action, **fields):
    return {"action": action, "interface": "eth2", "run_id": "run_1", **fields}


def test_applies_real_ovs_and_kernel_settings_and_restores_baseline(ovs):
    baseline = helper.execute(request("inspect"))["state"]
    result = helper.execute(request("apply", bandwidth_kbps=1000))
    assert result["verified"] is True
    assert result["achieved_rate_verified"] is False
    assert result["mechanism"] == "ingress_policing_and_egress_htb"
    assert ovs.device["ingress_policing_rate"] == 1000
    assert ovs.tables["QoS"][0]["other_config"]["max-rate"] == "1000000"
    assert ovs.tables["Queue"][0]["external_ids"]["metropolis_run"] == "run_1"
    assert any(call[0] == "tc" for call in ovs.calls)
    restored = helper.execute(request("restore", snapshot=baseline))
    assert restored == {"verified": True, "state": baseline}
    assert not ovs.tables["QoS"] and not ovs.tables["Queue"]
    assert ovs.device["ingress_policing_rate"] == 64
    assert not any(
        "vlan_mode" in item or "trunks=" in item or "tag=" in item
        for call in ovs.calls
        for item in call
    )


def test_lost_apply_response_can_be_rolled_back_by_owned_tags(ovs):
    baseline = helper.execute(request("inspect"))["state"]
    ovs.lost_reply = True
    with pytest.raises(subprocess.TimeoutExpired):
        helper.execute(request("apply", bandwidth_kbps=512))
    assert ovs.port["qos"] == QOS
    assert helper.execute(request("restore", snapshot=baseline))["verified"]
    assert ovs.port["qos"] is None


@pytest.mark.parametrize("field", ["qos", "qdisc"])
def test_preflight_rejects_existing_configuration_without_writing(ovs, field):
    if field == "qos":
        ovs.port["qos"] = QOS
    else:
        ovs.qdisc = "cake"
    with pytest.raises(ValueError):
        helper.execute(request("inspect"))
    assert not any("set" in call or "create" in call for call in ovs.calls)


def test_preflight_preserves_other_queue_disciplines_even_with_default_names(ovs):
    ovs.qdisc = "fq_codel"
    with pytest.raises(ValueError, match="non-default queue discipline"):
        helper.execute(request("inspect"))
    assert not any("set" in call or "create" in call for call in ovs.calls)


def test_refuses_foreign_qos_during_restore(ovs):
    baseline = helper.execute(request("inspect"))["state"]
    helper.execute(request("apply", bandwidth_kbps=512))
    ovs.tables["QoS"][0]["external_ids"]["metropolis_run"] = "another_run"
    with pytest.raises(ValueError, match="another owner"):
        helper.execute(request("restore", snapshot=baseline))
    assert ovs.port["qos"] == QOS


def test_refuses_changed_port_identity(ovs):
    baseline = helper.execute(request("inspect"))["state"]
    ovs.port["_uuid"] = QUEUE
    with pytest.raises(ValueError, match="identity changed"):
        helper.execute(request("restore", snapshot=baseline))


def test_refuses_a_port_moved_to_another_bridge(ovs):
    ovs.bridge = "foreign-bridge"
    with pytest.raises(ValueError, match="owned br0"):
        helper.execute(request("inspect"))
    assert not ovs.tables["QoS"]


def test_apply_rejects_settings_changed_since_preflight(ovs):
    baseline = helper.execute(request("inspect"))["state"]
    ovs.device["ingress_policing_rate"] = 99
    with pytest.raises(ValueError, match="changed after preflight"):
        helper.execute(request("apply", bandwidth_kbps=512, snapshot=baseline))
    assert not ovs.tables["QoS"]
    assert ovs.device["ingress_policing_rate"] == 99


def test_uncertain_apply_cannot_overwrite_unowned_policing_changes(ovs):
    baseline = helper.execute(request("inspect"))["state"]
    ovs.device["ingress_policing_rate"] = 99
    with pytest.raises(ValueError, match="without run-owned settings"):
        helper.execute(request("restore", snapshot=baseline))
    assert ovs.device["ingress_policing_rate"] == 99


def test_no_mutation_restoration_is_idempotent(ovs):
    baseline = helper.execute(request("inspect"))["state"]
    assert helper.execute(request("restore", snapshot=baseline))["verified"]
    assert not any("set" in call or "destroy" in call for call in ovs.calls)


def test_delayed_apply_after_restoration_is_cancelled_before_writes(ovs):
    baseline = helper.execute(request("inspect"))["state"]
    helper.execute(request("restore", snapshot=baseline))
    ovs.calls.clear()
    with pytest.raises(ValueError, match="cancelled by restoration"):
        helper.execute(request("apply", bandwidth_kbps=512, snapshot=baseline))
    assert not ovs.calls
    assert helper._cancelled("eth2", "run_1").is_file()
    # Cancellation is scoped to the prior run; another run may use the port.
    assert helper.execute({**request("apply", bandwidth_kbps=512), "run_id": "run_2"})[
        "verified"
    ]


def test_restore_serializes_with_an_apply_already_in_progress(ovs, monkeypatch):
    baseline = helper.execute(request("inspect"))["state"]
    apply_started = threading.Event()
    release_apply = threading.Event()
    restore_requested = threading.Event()
    restored = threading.Event()
    errors = []
    original = helper.apply

    def paused_apply(*args, **kwargs):
        apply_started.set()
        if not release_apply.wait(3):
            raise TimeoutError("test did not release apply")
        return original(*args, **kwargs)

    monkeypatch.setattr(helper, "apply", paused_apply)

    def apply_task():
        try:
            helper.execute(request("apply", bandwidth_kbps=512, snapshot=baseline))
        except Exception as exc:
            errors.append(exc)

    def restore_task():
        restore_requested.set()
        try:
            helper.execute(request("restore", snapshot=baseline))
            restored.set()
        except Exception as exc:
            errors.append(exc)

    apply_thread = threading.Thread(target=apply_task)
    restore_thread = threading.Thread(target=restore_task)
    apply_thread.start()
    try:
        assert apply_started.wait(1)
        restore_thread.start()
        assert restore_requested.wait(1)
        assert not restored.wait(0.05)
    finally:
        release_apply.set()
        apply_thread.join(timeout=3)
        if restore_thread.ident is not None:
            restore_thread.join(timeout=3)
    assert not apply_thread.is_alive() and not restore_thread.is_alive()
    assert not errors
    assert restored.is_set()
    assert helper.inspect("eth2") == baseline
    assert helper._cancelled("eth2", "run_1").is_file()


def test_kernel_qdisc_missing_is_failed_readback_and_can_restore(ovs):
    baseline = helper.execute(request("inspect"))["state"]
    ovs.kernel_htb = False
    with pytest.raises(RuntimeError, match="no kernel HTB"):
        helper.execute(request("apply", bandwidth_kbps=512))
    assert helper.execute(request("restore", snapshot=baseline))["verified"]


def test_restore_requires_kernel_htb_to_disappear(ovs):
    baseline = helper.execute(request("inspect"))["state"]
    helper.execute(request("apply", bandwidth_kbps=512))
    # Model ovsdb accepting the clear while the kernel still reports HTB.
    ovs.qdisc = "htb"
    with pytest.raises(RuntimeError, match="settings did not return"):
        helper.execute(request("restore", snapshot=baseline))
    assert ovs.port["qos"] is None
    assert not ovs.tables["QoS"]


@pytest.mark.parametrize("value", [0, True, 0.5])
def test_invalid_bandwidth_rejected_before_write(ovs, value):
    with pytest.raises(ValueError, match="bandwidth_kbps"):
        helper.execute(request("apply", bandwidth_kbps=value))
    assert not ovs.calls


@pytest.mark.parametrize(
    "field,value", [("interface", "eth2;id"), ("run_id", "../run"), ("action", "shell")]
)
def test_rejects_arbitrary_commands(ovs, field, value):
    payload = request("inspect")
    payload[field] = value
    with pytest.raises(ValueError):
        helper.execute(payload)
    assert not ovs.calls


class Engine:
    def __init__(self):
        self.image = link_bandwidth.SWITCH_IMAGE
        self.identity = "container-a"
        self.calls = []
        self.closed = False
        self.failure = None

    def inspect_container(self, cid):
        return {
            "Id": self.identity,
            "Config": {"Image": self.image, "Env": ["SECRET=DO_NOT_SAVE"]},
        }

    def exec_create(self, cid, **kwargs):
        self.calls.append((cid, kwargs))
        self.payload = json.loads(kwargs["cmd"][-1])
        return {"Id": "exec-a"}

    def exec_start(self, _exec):
        result = {"verified": True, "state": {"interface": self.payload["interface"]}}
        if self.failure:
            result = {"verified": False, "error": self.failure}
        return json.dumps(result).encode()

    def exec_inspect(self, _exec):
        return {"ExitCode": 1 if self.failure else 0}

    def close(self):
        self.closed = True


def switch_node():
    return {
        "node_id": "node-a",
        "node_type": "docker",
        "status": "started",
        "compute_id": "remote",
        "properties": {
            "image": link_bandwidth.SWITCH_IMAGE,
            "container_id": "container-a",
        },
    }


def test_backend_reuses_compute_mapping_and_only_calls_helper():
    engine, uris = Engine(), []
    backend = LinkBandwidthBackend(
        {"remote": "ssh://user@compute"},
        "run_1",
        client_factory=lambda uri: uris.append(uri) or engine,
    )
    node = switch_node()
    assert backend.inspect(node, 2) == {"interface": "eth2"}
    backend.apply(node, 2, 1000)
    assert engine.payload == request(
        "apply", bandwidth_kbps=1000, snapshot={"interface": "eth2"}
    )
    backend.restore(node, 2, {"qos": None})
    assert engine.payload["snapshot"] == {"qos": None}
    assert uris == ["ssh://user@compute"]
    assert all(
        call[1]["cmd"][:2] == ["python3", link_bandwidth.HELPER]
        for call in engine.calls
    )
    assert "DO_NOT_SAVE" not in json.dumps(engine.payload)
    backend.close()
    assert engine.closed


@pytest.mark.parametrize(
    "failure", ["wrong-image", "wrong-container", "helper-failure"]
)
def test_backend_rejects_wrong_targets_and_helper_failures(failure):
    engine = Engine()
    backend = LinkBandwidthBackend(
        {"remote": "unix:///socket"}, "run_1", client_factory=lambda _uri: engine
    )
    if failure == "wrong-image":
        engine.image = "other/image"
    elif failure == "wrong-container":
        engine.identity = "another-container"
    else:
        engine.failure = "HTB unavailable"
    with pytest.raises((ValueError, RuntimeError)):
        backend.apply(switch_node(), 2, 1000)
    if failure != "helper-failure":
        assert not engine.calls
    backend.close()


def test_missing_mapping_is_explicit_and_no_client_is_opened():
    backend = LinkBandwidthBackend(
        {}, "run_1", client_factory=lambda _uri: pytest.fail("unexpected connection")
    )
    with pytest.raises(ValueError, match="no Docker host mapping"):
        backend.inspect(switch_node(), 2)


def test_backend_preflight_propagates_helper_rejection_without_mutation(ovs):
    ovs.port["qos"] = QOS

    class HelperEngine(Engine):
        def exec_start(self, _exec):
            try:
                result = helper.execute(self.payload)
            except ValueError as exc:
                self.failure = str(exc)
                result = {"verified": False, "error": self.failure}
            return json.dumps(result).encode()

    engine = HelperEngine()
    backend = LinkBandwidthBackend(
        {"remote": "unix:///socket"}, "run_1", client_factory=lambda _uri: engine
    )
    with pytest.raises(RuntimeError, match="existing QoS or non-default"):
        backend.inspect(switch_node(), 2)
    assert not any("set" in call or "create" in call for call in ovs.calls)
    assert not backend.snapshots
    backend.close()


def test_default_backend_uses_bounded_worker_even_for_local_engine(monkeypatch):
    calls = []

    class Proxy:
        closed = False

        def __init__(self, uri, **kwargs):
            calls.append((uri, kwargs))

        def _request(self, payload, cid):
            assert cid == "container-a"
            assert json.loads(payload)["action"] == "inspect"
            return {"verified": True, "state": {"interface": "eth2"}}

        def close(self):
            self.closed = True

    monkeypatch.setattr(link_bandwidth, "RemoteDockerClient", Proxy)
    backend = LinkBandwidthBackend({"remote": "unix:///socket"}, "run_1")
    assert backend.inspect(switch_node(), 2) == {"interface": "eth2"}
    assert calls[0][0] == "unix:///socket"
    assert calls[0][1] == {"timeout": 25, "worker_target": link_bandwidth._worker}
    backend.close()
