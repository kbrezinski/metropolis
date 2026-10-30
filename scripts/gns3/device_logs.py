"""Read-only, incremental Docker log collection for a GNS3 experiment run."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


DOCKER_TIME = re.compile(r"^(\d{4}-\d\d-\d\dT\S+Z) (.*)$", re.DOTALL)
SECRET = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|private[_-]?key)"
    r"([\"']?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,}]+)"
)
# Preserve quote escaping when console-safe PSKs appear inside JSON log fields.
IPSEC_PSK = re.compile(
    r"(?i)(\bauthentication\s+pre-shared-secret\s+)"
    r"(?P<quote>\\*[\"']|)(?P<value>[A-Za-z0-9_+/=-]+)(?P=quote)"
)
PEM_START = re.compile(r"-----BEGIN (?:[A-Z ]* )?PRIVATE KEY-----")
PEM_END = re.compile(r"-----END (?:[A-Z ]* )?PRIVATE KEY-----")
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
SSH_AUTH = re.compile(
    r"\b(?P<outcome>Accepted|Failed) (?P<method>password|publickey) for "
    r"(?:(?:invalid user) )?(?P<user>\S+) from (?P<peer>\S+) port (?P<port>\d+)"
)


def redact(line: str, *, in_private_key: bool = False) -> tuple[str, bool, bool]:
    """Redact common credential assignments and whole PEM private-key blocks."""
    if in_private_key or PEM_START.search(line):
        return "[REDACTED PRIVATE KEY]", True, not bool(PEM_END.search(line))

    def substitute(match: re.Match) -> str:
        value = match[3]
        replacement = (
            f"{value[0]}[REDACTED]{value[0]}" if value[0] in "\"'" else "[REDACTED]"
        )
        return match[1] + match[2] + replacement

    cleaned, count = SECRET.subn(substitute, line)
    cleaned, psk_count = IPSEC_PSK.subn(
        lambda match: match[1] + match["quote"] + "[REDACTED]" + match["quote"],
        cleaned,
    )
    return cleaned, bool(count or psk_count), False


def normalize_line(
    line: str, *, run_id: str, source: str, component: str, stream: str
) -> dict:
    """Preserve plain text and structured fields without trusting their timestamp."""
    record = {
        "timestamp": utc_now(),
        "run_id": run_id,
        "source": source,
        "component": component,
        "stream": stream,
        "level": "UNKNOWN",
        "event": None,
        "message": line,
        "attributes": {},
        "raw": line,
    }
    try:
        value = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        auth = SSH_AUTH.search(line) if component in {"engineering", "ssh"} else None
        if auth:
            record["component"] = "ssh"
            record["event"] = (
                "ssh_auth_succeeded"
                if auth["outcome"] == "Accepted"
                else "ssh_auth_failed"
            )
            record["level"] = "INFO" if auth["outcome"] == "Accepted" else "WARNING"
            record["attributes"] = {
                "method": auth["method"],
                "user": auth["user"],
                "peer_ip": auth["peer"],
                "peer_port": int(auth["port"]),
            }
        return record
    if not isinstance(value, dict):
        return record
    event_time = value.get("timestamp") or value.get("time") or value.get("ts")
    if isinstance(event_time, str):
        try:
            parsed = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
        except ValueError:
            pass
        else:
            if parsed.tzinfo is not None:
                record["device_timestamp"] = event_time
    if isinstance(value.get("level"), str):
        record["level"] = value["level"].upper()
    if isinstance(value.get("component"), str):
        record["component"] = value["component"]
    if isinstance(value.get("event"), str):
        record["event"] = value["event"]
    if isinstance(value.get("message"), str):
        record["message"] = value["message"]
    record["attributes"] = value
    if value.get("_metropolis_syslog") is True:
        record["source"] = str(value.get("source") or source)
        record["component"] = "router"
        record["stream"] = "syslog"
        relay_time = record.pop("device_timestamp", None)
        if relay_time:
            record["relay_timestamp"] = relay_time
        record["attributes"]["transport"] = "udp_relay"
    elif value.get("_metropolis_switch_syslog") is True:
        # The Docker node identity is authoritative; do not trust a log payload's
        # claimed hostname for switch attribution.
        record["source"] = source
        record["component"] = "switch"
        record["stream"] = "syslog"
        record["attributes"]["transport"] = "docker_stdout_bridge"
    return record


def container_id(node: dict) -> str | None:
    value = node.get("properties", {}).get("container_id") or node.get("container_id")
    return str(value) if value else None


class DeviceLogCollector:
    """Poll GNS3 nodes, follow each Docker container, and checkpoint files."""

    def __init__(
        self,
        client,
        project_id: str,
        run_dir: Path,
        run_id: str,
        docker_hosts: dict[str, str],
        *,
        metadata: dict | None = None,
        expected_syslog_sources: list[str] | None = None,
        expected_switch_syslog_sources: list[str] | None = None,
        silent_sources: list[str] | None = None,
        docker_binary: str = "docker",
        popen=subprocess.Popen,
    ):
        self.client = client
        self.project_id = project_id
        self.run_dir = run_dir
        self.run_id = run_id
        self.docker_hosts = docker_hosts
        self.docker_binary = docker_binary
        self.silent_sources = set(silent_sources or [])
        self.switch_syslog_sources = set(expected_switch_syslog_sources or [])
        self.popen = popen
        self.lock = threading.RLock()
        self.processes: dict[str, subprocess.Popen] = {}
        self.threads: list[threading.Thread] = []
        self.started_at = utc_now()
        self.manifest = {
            **(metadata or {}),
            "schema_version": "1.0",
            "run_id": run_id,
            "project_id": project_id,
            "started_at_utc": self.started_at,
            "status": "running",
            "logs": {
                "normalized_path": "logs/devices.jsonl",
                "sources": {},
                "intentionally_silent_sources": sorted(self.silent_sources),
            },
            "syslog": {
                "path": "logs/syslog.jsonl",
                "expected_sources": sorted(
                    set(expected_syslog_sources or []) | self.switch_syslog_sources
                ),
                "source_kinds": {
                    **{name: "router" for name in expected_syslog_sources or []},
                    **{name: "switch" for name in self.switch_syslog_sources},
                },
                "sources": {},
                "transports": {},
                "missing_sources": [],
            },
            "failures": [],
        }
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "logs" / "raw").mkdir(parents=True)
        (run_dir / "logs" / "devices.jsonl").touch()
        (run_dir / "logs" / "syslog.jsonl").touch()
        (run_dir / "timeline.jsonl").touch()
        self._write_manifest()

    def _write_manifest(self) -> None:
        with self.lock:
            path = self.run_dir / "manifest.json"
            temporary = self.run_dir / "manifest.json.tmp"
            temporary.write_text(
                json.dumps(self.manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            deadline = time.monotonic() + 0.4
            while True:
                try:
                    temporary.replace(path)
                    return
                except PermissionError as exc:
                    if sys.platform != "win32" or getattr(
                        exc, "winerror", None
                    ) not in (5, 32):
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise
                    time.sleep(min(0.02, remaining))

    def _failure(self, source: str, reason: str) -> None:
        with self.lock:
            self.manifest["failures"].append({"source": source, "reason": reason})
            if source in self.manifest["logs"]["sources"]:
                self.manifest["logs"]["sources"][source]["complete"] = False
            self._write_manifest()

    def _read_stream(self, source: str, component: str, stream: str, pipe) -> None:
        try:
            key_open = False
            raw_path = (
                self.run_dir / self.manifest["logs"]["sources"][source]["raw_path"]
            )
            with (
                raw_path.open("ab") as raw,
                (self.run_dir / "logs" / "devices.jsonl").open(
                    "a", encoding="utf-8"
                ) as normalized,
            ):
                while chunk := pipe.readline():
                    key_open = self._write_line(
                        chunk, source, component, stream, raw, normalized, key_open
                    )
        except Exception as exc:  # noqa: BLE001 - background failures must reach manifest
            self._failure(source, f"log stream read failed: {exc}")

    def _write_line(self, chunk, source, component, stream, raw, normalized, key_open):
        decoded = chunk.decode("utf-8", errors="replace").rstrip("\r\n")
        match = DOCKER_TIME.match(decoded)
        device_time, original = (match[1], match[2]) if match else (None, decoded)
        safe_line, redacted, key_open = redact(original, in_private_key=key_open)
        event = normalize_line(
            safe_line,
            run_id=self.run_id,
            source=source,
            component=component,
            stream=stream,
        )
        if device_time:
            event["docker_timestamp"] = device_time
            event.setdefault("device_timestamp", device_time)
        if redacted:
            event["redacted"] = True
        if (
            event["attributes"].get("_metropolis_switch_syslog") is True
            and source not in self.switch_syslog_sources
        ):
            event["component"] = component
            event["stream"] = stream
            event["attributes"].pop("transport", None)
        with self.lock:
            if redacted:
                raw.write((safe_line + "\n").encode("utf-8"))
            else:
                raw.write(chunk.split(b" ", 1)[1] if match else chunk)
            raw.flush()
            normalized.write(json.dumps(event, ensure_ascii=False) + "\n")
            normalized.flush()
            self.manifest["logs"]["sources"][source]["lines"] += 1
            if event["stream"] == "syslog":
                with (self.run_dir / "logs" / "syslog.jsonl").open(
                    "a", encoding="utf-8"
                ) as syslog_output:
                    syslog_output.write(json.dumps(event, ensure_ascii=False) + "\n")
                    syslog_output.flush()
                sources = self.manifest["syslog"]["sources"]
                sources[event["source"]] = sources.get(event["source"], 0) + 1
                self.manifest["syslog"]["transports"][event["source"]] = event[
                    "attributes"
                ].get("transport", "unknown")
                if event["event"] in {
                    "ovs_log_unavailable",
                    "ovs_log_read_failed",
                    "switch_start_failed",
                    "switch_config_failed",
                    "switch_health_failed",
                }:
                    self._failure(
                        source,
                        f"switch syslog reported {event['event']}: {event['message']}",
                    )
        return key_open

    def discover(self) -> None:
        """Attach once per started Docker node; retry later if it has not started."""
        nodes = self.client.project_nodes(self.project_id)
        for node in nodes:
            if node.get("node_type") != "docker":
                continue
            source = node["name"]
            if source in self.manifest["logs"]["sources"]:
                continue
            if node.get("status") != "started":
                continue
            compute_id = node.get("compute_id", "local")
            host = self.docker_hosts.get(compute_id)
            cid = container_id(node)
            if not host or not cid:
                continue
            raw_name = SAFE_NAME.sub("_", source).strip("._") or node["node_id"]
            raw_path = f"logs/raw/{raw_name}-{node['node_id'][:8]}.log"
            self.manifest["logs"]["sources"][source] = {
                "node_id": node["node_id"],
                "compute_id": compute_id,
                "container_id": cid,
                "raw_path": raw_path,
                "lines": 0,
                "complete": False,
            }
            self._write_manifest()
            since = self.started_at.replace("Z", "+00:00")
            command = [
                self.docker_binary,
                "--host",
                host,
                "logs",
                "--follow",
                "--timestamps",
                "--since",
                since,
                cid,
            ]
            try:
                process = self.popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
            except (OSError, ValueError) as exc:
                self._failure(source, f"Docker log connection failed: {exc}")
                continue
            self.processes[source] = process
            component = source.removeprefix("MET-").split("-", 1)[0].lower()
            for stream, pipe in (
                ("stdout", process.stdout),
                ("stderr", process.stderr),
            ):
                thread = threading.Thread(
                    target=self._read_stream,
                    args=(source, component, stream, pipe),
                    daemon=True,
                )
                thread.start()
                self.threads.append(thread)

    def run(
        self,
        duration: float,
        *,
        poll_interval: float = 1.0,
        stop_event: threading.Event | None = None,
    ) -> dict:
        """Follow until the deadline or interruption and always finish the manifest."""
        if duration <= 0:
            raise ValueError("duration must be positive")
        deadline = time.monotonic() + duration
        interrupted = False
        try:
            while time.monotonic() < deadline and not (
                stop_event is not None and stop_event.is_set()
            ):
                try:
                    self.discover()
                except Exception as exc:  # noqa: BLE001 - retry until run ends
                    self._failure("collector", f"GNS3 node discovery failed: {exc}")
                for source, process in list(self.processes.items()):
                    if process.poll() is not None:
                        self._failure(
                            source,
                            f"Docker log process ended during run (exit {process.returncode})",
                        )
                        del self.processes[source]
                delay = min(poll_interval, max(0, deadline - time.monotonic()))
                if stop_event is None:
                    time.sleep(delay)
                else:
                    stop_event.wait(delay)
        except KeyboardInterrupt:
            interrupted = True
            self._failure("collector", "interrupted")
        except Exception as exc:  # noqa: BLE001 - preserve the run manifest
            self._failure("collector", f"collector failed: {exc}")
        finally:
            for source, process in self.processes.items():
                try:
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                except Exception as exc:  # noqa: BLE001 - attempt every source
                    self._failure(source, f"could not stop Docker log follower: {exc}")
            for thread in self.threads:
                thread.join(timeout=5)
                if thread.is_alive():
                    self._failure("collector", "a Docker log reader did not stop")
            try:
                nodes = self.client.project_nodes(self.project_id)
                for node in nodes:
                    if node.get("node_type") != "docker":
                        continue
                    source = node["name"]
                    if source not in self.manifest["logs"]["sources"]:
                        reason = (
                            f"no Docker host mapping for compute {node.get('compute_id', 'local')}"
                            if node.get("compute_id", "local") not in self.docker_hosts
                            else "node did not start or has no container ID"
                        )
                        self._failure(source, reason)
            except Exception as exc:  # noqa: BLE001
                self._failure("collector", f"final GNS3 discovery failed: {exc}")
            for source, item in self.manifest["logs"]["sources"].items():
                if item["lines"] == 0:
                    if source not in self.silent_sources:
                        self._failure(source, "no device logs captured")
                elif not any(f["source"] == source for f in self.manifest["failures"]):
                    item["complete"] = True
            if not any(
                item["lines"] > 0 for item in self.manifest["logs"]["sources"].values()
            ):
                self._failure("collector", "no Docker sources produced logs")
            self.manifest["syslog"]["missing_sources"] = sorted(
                set(self.manifest["syslog"]["expected_sources"])
                - self.manifest["syslog"]["sources"].keys()
            )
            for source in self.manifest["syslog"]["missing_sources"]:
                kind = self.manifest["syslog"]["source_kinds"].get(source, "router")
                self._failure(source, f"no {kind} syslog captured")
            self.manifest["stopped_at_utc"] = utc_now()
            self.manifest["status"] = (
                "interrupted"
                if interrupted
                else "failed"
                if self.manifest["failures"]
                else "complete"
            )
            self._write_manifest()
        return self.manifest
