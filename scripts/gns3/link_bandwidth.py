"""Opt-in bandwidth profiles on explicitly selected owned Open vSwitch ports."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from device_logs import container_id
from resource_metrics import RemoteDockerClient, _direct_docker_client

SWITCH_IMAGE = "metropolis/openvswitch:dev"
HELPER = "/opt/metropolis/link_profile.py"


def _call(engine, cid: str, payload: dict) -> dict:
    inspected = engine.inspect_container(cid)
    if (
        inspected.get("Id") != cid
        or inspected.get("Config", {}).get("Image") != SWITCH_IMAGE
    ):
        raise ValueError("bandwidth target is not the selected owned switch container")
    process = engine.exec_create(
        cid,
        cmd=["python3", HELPER, json.dumps(payload, separators=(",", ":"))],
        stdout=True,
        stderr=True,
    )
    output = engine.exec_start(process["Id"])
    if not isinstance(output, bytes) or len(output) > 128 * 1024:
        raise ValueError("invalid bandwidth helper output")
    try:
        result = json.loads(output)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            "bandwidth helper missing or returned invalid JSON; rebuild the switch image"
        ) from exc
    if not isinstance(result, dict):
        raise ValueError("invalid bandwidth helper result")
    if (
        engine.exec_inspect(process["Id"]).get("ExitCode") != 0
        or result.get("verified") is not True
    ):
        raise RuntimeError(
            result.get("error", "bandwidth helper did not verify settings")
        )
    return result


def _worker(connection, base_url: str, factory=None):
    engine = None
    try:
        if factory is None:
            import paramiko

            config_path = Path.home() / ".ssh" / "config"
            if config_path.is_file():
                config = paramiko.SSHConfig()
                with config_path.open(encoding="utf-8") as source:
                    config.parse(source)
                if config.lookup(urlsplit(base_url).hostname).get("proxycommand"):
                    raise ValueError("SSH ProxyCommand is unsupported for profiles")
        engine = (factory or _direct_docker_client)(base_url)
        # The helper has its own 15-second operation deadline. Wait longer than
        # that so rollback cannot race an exec still applying settings.
        engine.timeout = 20
        while True:
            operation, cid = connection.recv()
            if operation == "close":
                connection.send((True, None))
                break
            try:
                payload = json.loads(operation)
                if payload.get("action") not in {"inspect", "apply", "restore"}:
                    raise ValueError("unsupported bandwidth operation")
                connection.send((True, _call(engine, cid, payload)))
            except Exception as exc:  # noqa: BLE001 - retain useful helper errors, no inspection payloads
                safe = (
                    str(exc)
                    if isinstance(exc, (ValueError, RuntimeError))
                    else type(exc).__name__
                )
                connection.send((True, {"verified": False, "error": safe}))
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:  # noqa: BLE001
        try:
            connection.send((False, type(exc).__name__))
        except (EOFError, BrokenPipeError, OSError):
            pass
    finally:
        if engine is not None:
            engine.close()
        connection.close()


class LinkBandwidthBackend:
    """One switch port enforces ingress policing and egress HTB shaping."""

    def __init__(
        self, docker_hosts: dict[str, str], run_id: str, *, client_factory=None
    ):
        self.hosts = dict(docker_hosts)
        self.run_id = run_id
        self.factory = client_factory
        self.clients = {}
        self.snapshots = {}

    def _client(self, compute_id: str):
        if compute_id not in self.hosts:
            raise ValueError(f"no Docker host mapping for compute {compute_id}")
        previous = self.clients.get(compute_id)
        if previous is None or getattr(previous, "closed", False):
            if self.factory:
                previous = self.factory(self.hosts[compute_id])
            else:
                # Isolate local engines too: exec-start can block beyond an SDK
                # HTTP timeout, and an uncertain write still needs rollback.
                previous = RemoteDockerClient(
                    self.hosts[compute_id], timeout=25, worker_target=_worker
                )
            self.clients[compute_id] = previous
        return previous

    def _request(self, node: dict, adapter: int, action: str, **fields) -> dict:
        if node.get("node_type") != "docker" or node.get("status") != "started":
            raise ValueError("bandwidth profiles require a running Docker switch")
        if node.get("properties", {}).get("image") != SWITCH_IMAGE:
            raise ValueError("bandwidth profiles require the owned Open vSwitch image")
        cid = container_id(node)
        if not cid:
            raise ValueError("switch has no container ID")
        if type(adapter) is not int or not 0 <= adapter <= 4095:
            raise ValueError("invalid switch adapter")
        payload = {
            "action": action,
            "interface": f"eth{adapter}",
            "run_id": self.run_id,
            **fields,
        }
        engine = self._client(node.get("compute_id", "local"))
        if isinstance(engine, RemoteDockerClient):
            result = engine._request(json.dumps(payload), cid)
            if result.get("verified") is not True:
                raise RuntimeError(result.get("error", "bandwidth operation failed"))
        else:
            result = _call(engine, cid, payload)
        return result

    def inspect(self, node: dict, adapter: int) -> dict:
        state = self._request(node, adapter, "inspect")["state"]
        self.snapshots[(node["node_id"], container_id(node), adapter)] = state.copy()
        return state

    def apply(self, node: dict, adapter: int, bandwidth_kbps: int) -> dict:
        key = (node["node_id"], container_id(node), adapter)
        snapshot = self.snapshots.get(key)
        if snapshot is None:
            snapshot = self.inspect(node, adapter)
        return self._request(
            node, adapter, "apply", bandwidth_kbps=bandwidth_kbps, snapshot=snapshot
        )

    def restore(self, node: dict, adapter: int, snapshot: dict) -> dict:
        return self._request(node, adapter, "restore", snapshot=snapshot)

    def close(self) -> None:
        errors = []
        for client in self.clients.values():
            try:
                client.close()
            except Exception as exc:  # noqa: BLE001 - close every compute
                errors.append(type(exc).__name__)
        if errors:
            raise RuntimeError(
                "could not close bandwidth profile clients: " + ", ".join(errors)
            )
