"""Docker-only execution. Untrusted artifacts never execute on the controller."""

from __future__ import annotations

import io
from importlib.resources import files
import json
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import tarfile
import threading
import time
import uuid

from .contracts import AttemptLimit, InfrastructureError


OUTPUT_LIMIT = 64 * 1024
FILE_LIMIT = 1024 * 1024
ARCHIVE_LIMIT = 10 * 1024 * 1024
FILE_COUNT_LIMIT = 1024
DEFAULT_IMAGE = "industrial-agent-bench:0.1.0"
_DOS_NAMES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}


def _invoke(arguments, *, data=None, timeout=30, limit=OUTPUT_LIMIT):
    """Drain both pipes while retaining only bounded output prefixes."""
    try:
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise InfrastructureError(f"Cannot start Docker CLI: {error}") from error
    captured = [bytearray(), bytearray()]
    truncated = [False, False]

    def drain(pipe, index):
        while chunk := pipe.read(8192):
            remaining = max(0, limit - len(captured[index]))
            captured[index].extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated[index] = True
        pipe.close()

    readers = [
        threading.Thread(target=drain, args=(pipe, index), daemon=True)
        for index, pipe in enumerate((process.stdout, process.stderr))
    ]
    for reader in readers:
        reader.start()

    # Input is a bounded fixture archive or JSON tool payload.
    def feed():
        try:
            if data:
                process.stdin.write(data)
        except (BrokenPipeError, OSError):
            pass
        finally:
            process.stdin.close()

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
    writer.join(timeout=2)
    for reader in readers:
        reader.join(timeout=2)
    return {
        "exit_code": process.returncode,
        "stdout": bytes(captured[0]),
        "stderr": bytes(captured[1]),
        "timed_out": timed_out,
        "output_truncated": any(truncated),
    }


def _docker(*arguments, data=None, timeout=30, limit=OUTPUT_LIMIT):
    return _invoke(["docker", *arguments], data=data, timeout=timeout, limit=limit)


def _checked(result):
    if result["timed_out"] or result["exit_code"] != 0:
        message = result["stderr"].decode(errors="replace")
        raise InfrastructureError(message or "Docker operation failed or timed out")
    return result


def _relative(name):
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in name
        or any(":" in part for part in path.parts)
    ):
        raise ValueError("Artifact paths must be relative POSIX paths without traversal")
    if str(path) == ".":
        raise ValueError("Expected a file path")
    for part in path.parts:
        if (
            part.endswith((".", " "))
            or part.split(".", 1)[0].upper() in _DOS_NAMES
            or any(
                character in '<>:"|?*' or ord(character) < 32 or ord(character) == 127
                for character in part
            )
        ):
            raise ValueError("Artifact path is not a portable regular-file name")
    return path


def _archive(source):
    stream = io.BytesIO()
    total = 0
    count = 0
    names = set()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for path in sorted(Path(source).rglob("*")):
            if path.is_symlink():
                raise ValueError("Artifact links are not accepted")
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError("Only regular artifact files are accepted")
            name = _relative(path.relative_to(source).as_posix()).as_posix()
            if name.casefold() in names:
                raise ValueError("Case-insensitive artifact path collision")
            names.add(name.casefold())
            size = path.stat().st_size
            total += size
            count += 1
            if size > FILE_LIMIT or total > ARCHIVE_LIMIT or count > FILE_COUNT_LIMIT:
                raise ValueError("Artifact size/count limit exceeded")
            directories = list(PurePosixPath(name).parents)[:-1]
            for directory in reversed(directories):
                entry = tarfile.TarInfo(directory.as_posix())
                entry.type, entry.mode, entry.uid, entry.gid = tarfile.DIRTYPE, 0o755, 1000, 1000
                archive.addfile(entry)
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.uid, entry.gid = size, 0o644, 1000, 1000
            with path.open("rb") as content:
                archive.addfile(entry, content)
    return stream.getvalue()


def _extract(data, destination):
    """Validate the complete archive before writing anything to the controller."""
    members = []
    total = 0
    names = set()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for member in archive:
            if member.isdir():
                if member.name not in (".", "./"):
                    _relative(member.name)
                continue
            if not member.isfile():
                raise ValueError("Artifact links and special files are not accepted")
            name = _relative(member.name).as_posix()
            total += member.size
            if (
                member.size > FILE_LIMIT
                or total > ARCHIVE_LIMIT
                or len(members) >= FILE_COUNT_LIMIT
                or name.casefold() in names
            ):
                raise ValueError("Artifact size/count limit exceeded or duplicate path")
            names.add(name.casefold())
            members.append((member, name))
        for _, name in members:
            if any(parent.as_posix().casefold() in names for parent in PurePosixPath(name).parents):
                raise ValueError("Artifact file/directory collision")
        destination = Path(destination)
        if destination.exists() and any(destination.iterdir()):
            raise ValueError("Artifact destination must be empty")
        destination.mkdir(parents=True, exist_ok=True)
        for member, name in members:
            target = destination.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())


def check_image(image):
    """Return the immutable local image ID; do not implicitly pull an image."""
    return (
        _checked(_docker("image", "inspect", "--format", "{{.Id}}", image))["stdout"]
        .decode()
        .strip()
    )


def build_image(tag=DEFAULT_IMAGE):
    """Explicit setup step, potentially requiring downloads."""
    directory = files("agent_bench").joinpath("fixtures")
    _checked(_docker("build", "--tag", tag, str(directory), timeout=900))
    return check_image(tag)


_FILE_TOOL = r"""
import json, os, pathlib, sys
request = json.load(sys.stdin)
root = pathlib.Path('/workspace')
path = root / request['path']
if not path.resolve().is_relative_to(root) or path.resolve() != path:
    raise ValueError('Links or paths outside the workspace are not accepted')
if request['action'] == 'write':
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, 'w') as target:
        target.write(request['content'])
else:
    with path.open('rb') as source:
        content = source.read(65537)
    if len(content) > 65536:
        raise ValueError('File exceeds the 64 KiB read limit')
    sys.stdout.buffer.write(content)
"""


class Sandbox:
    def __init__(self, image, raw_network=False, timeout=600):
        self.image = image
        self.raw_network = raw_network
        self.timeout = timeout
        self.container_id = None
        self.image_id = None
        self.peers = []
        self.started = None

    def __enter__(self):
        self.image_id = check_image(self.image)
        self.started = time.monotonic()
        self.container_id = self._create("none", audit=True)
        return self

    def _create(self, network, audit=False):
        capabilities = (
            ["--cap-add", "SETUID", "--cap-add", "SETGID", "--cap-add", "SETPCAP"] if audit else []
        )
        if self.raw_network:
            capabilities += ["--cap-add", "NET_RAW"]
        if audit:
            capabilities += ["--cap-add", "SYS_PTRACE"]
        workspace_owner = "1000" if audit else "0"
        name = "agent-bench-" + uuid.uuid4().hex
        try:
            result = _checked(
                _docker(
                    "run",
                    "--detach",
                    "--name",
                    name,
                    "--network",
                    network,
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    *capabilities,
                    "--security-opt",
                    "no-new-privileges:true",
                    "--pids-limit",
                    "128",
                    "--memory",
                    "512m",
                    "--memory-swap",
                    "512m",
                    "--cpus",
                    "1",
                    "--ulimit",
                    "fsize=16777216:16777216",
                    "--tmpfs",
                    f"/workspace:rw,nosuid,size=64m,uid={workspace_owner},gid={workspace_owner},mode=0755",
                    "--tmpfs",
                    "/tmp:rw,nosuid,size=32m,mode=1777",
                    "--tmpfs",
                    "/proof:rw,nosuid,noexec,size=8m,mode=0700",
                    "--workdir",
                    "/workspace",
                    "--env",
                    "PYTHONDONTWRITEBYTECODE=1",
                    "--label",
                    "agent-bench.managed=true",
                    self.image_id,
                    "sleep",
                    "infinity",
                )
            )
            identity = result["stdout"].decode().strip()
            if not identity:
                raise InfrastructureError("Docker returned no container identity")
            return identity
        except BaseException:
            # The daemon may have created the named container before a CLI timeout.
            _docker("rm", "--force", name, timeout=10)
            raise

    def _limit(self, timeout):
        remaining = self.timeout - (time.monotonic() - self.started)
        if remaining <= 0:
            raise AttemptLimit("Task container wall-clock budget exhausted")
        return max(0.1, min(timeout, remaining))

    def _exec(self, arguments, timeout=30, data=None, user="0"):
        timeout = self._limit(timeout)
        result = _docker(
            "exec",
            "--interactive",
            "--user",
            user,
            self.container_id,
            "timeout",
            "--signal=KILL",
            f"{timeout:.3f}",
            *arguments,
            data=data,
            timeout=timeout + 3,
        )
        if result["exit_code"] != 0 and not result["timed_out"]:
            # Docker and a submitted command can use the same exit codes. A
            # daemon/container failure is infrastructure; an ordinary nonzero
            # artifact result in a running container is a model failure.
            state = _checked(
                _docker("inspect", "--format", "{{.State.Running}}", self.container_id)
            )
            if state["stdout"].strip() != b"true":
                oom = _checked(
                    _docker("inspect", "--format", "{{.State.OOMKilled}}", self.container_id)
                )
                if oom["stdout"].strip() == b"true":
                    raise AttemptLimit("Task memory limit exceeded (container OOM-killed)")
                raise InfrastructureError("The task container stopped unexpectedly")
        return {
            **result,
            "stdout": result["stdout"].decode(errors="replace"),
            "stderr": result["stderr"].decode(errors="replace"),
            "timed_out": result["timed_out"] or result["exit_code"] in (124, 137),
        }

    def _launcher(self):
        capability = "+net_raw" if self.raw_network else "-all"
        return [
            "setpriv",
            "--reuid=1000",
            "--regid=1000",
            "--clear-groups",
            "--bounding-set=" + ("-all,+net_raw" if self.raw_network else "-all"),
            "--inh-caps=" + capability,
            "--ambient-caps=" + capability,
        ]

    def execute(self, command, timeout=30):
        return self._exec([*self._launcher(), "sh", "-c", command], timeout)

    def audited_execute(self, command, timeout=30):
        trace = "/proof/" + uuid.uuid4().hex + ".trace"
        result = self._exec(
            [
                "strace",
                "-f",
                "-qq",
                "-s",
                "512",
                "-e",
                "trace=execve,sendto,exit_group",
                "-o",
                trace,
                *self._launcher(),
                "sh",
                "-c",
                command,
            ],
            timeout,
        )
        evidence = self.trusted("cat " + shlex.quote(trace), timeout=5)
        if evidence["exit_code"] != 0:
            raise InfrastructureError("Could not retrieve protected execution trace")
        result["exec_trace"] = evidence["stdout"]
        return result

    def trusted(self, command, timeout=30):
        return self._exec(["sh", "-c", command], timeout)

    def _file(self, action, path, content=None, timeout=30):
        path = _relative(path).as_posix()
        if content is not None and len(content.encode()) > FILE_LIMIT:
            raise ValueError("File exceeds the 1 MiB write limit")
        request = {"action": action, "path": path, "content": content}
        result = self._exec(
            ["python", "-c", _FILE_TOOL],
            data=json.dumps(request).encode(),
            user="1000",
            timeout=timeout,
        )
        if result["timed_out"]:
            raise AttemptLimit("Workspace file operation timed out")
        if result["exit_code"] != 0:
            raise ValueError(result["stderr"] or "Workspace file operation failed")
        return result["stdout"]

    def write_file(self, path, content, timeout=30):
        self._file("write", path, content, timeout=timeout)

    def read_file(self, path, timeout=30):
        return self._file("read", path, timeout=timeout)

    def collect(self, destination):
        result = _checked(
            _docker(
                "cp",
                self.container_id + ":/workspace/.",
                "-",
                limit=ARCHIVE_LIMIT + 2 * 1024 * 1024,
            )
        )
        if result["output_truncated"]:
            raise ValueError("Artifact archive exceeds its transfer limit")
        _extract(result["stdout"], destination)

    def load(self, source):
        _checked(
            _docker(
                "cp",
                "-a",
                "-",
                self.container_id + ":/workspace/",
                data=_archive(Path(source)),
                timeout=30,
            )
        )

    def start_peer(self, files, command):
        peer = Peer(self, files, command)
        self.peers.append(peer)
        return peer

    def close(self):
        errors = []
        for peer in reversed(self.peers):
            try:
                peer.close()
            except InfrastructureError as error:
                errors.append(str(error))
        if self.container_id:
            try:
                _checked(_docker("rm", "--force", self.container_id, timeout=10))
                self.container_id = None
            except InfrastructureError as error:
                errors.append(str(error))
        if errors:
            raise InfrastructureError("Container cleanup failed: " + "; ".join(errors))

    def __exit__(self, _kind, error, _traceback):
        try:
            self.close()
        except InfrastructureError as cleanup_error:
            if error is None:
                raise
            error.add_note(str(cleanup_error))


class Peer:
    def __init__(self, parent, source_files, command):
        self.parent = parent
        self.container_id = parent._create("container:" + parent.container_id)
        try:
            for name, content in source_files.items():
                name = _relative(name).as_posix()
                payload = json.dumps({"action": "write", "path": name, "content": content}).encode()
                result = self._exec(["python", "-c", _FILE_TOOL], data=payload)
                if result["exit_code"]:
                    raise InfrastructureError(result["stderr"])
            result = self.execute(
                "nohup " + shlex.join(command) + " >/tmp/peer.log 2>&1 </dev/null &"
            )
            if result["exit_code"]:
                raise InfrastructureError("Could not start fixture peer")
        except BaseException:
            self.close()
            raise

    def _exec(self, arguments, timeout=30, data=None):
        result = _docker(
            "exec",
            "--interactive",
            self.container_id,
            "timeout",
            "--signal=KILL",
            str(timeout),
            *arguments,
            data=data,
            timeout=timeout + 3,
        )
        return {
            **result,
            "stdout": result["stdout"].decode(errors="replace"),
            "stderr": result["stderr"].decode(errors="replace"),
        }

    def execute(self, command, timeout=30):
        return self._exec(["sh", "-c", command], timeout)

    def read_file(self, path):
        result = self._exec(
            [
                "python",
                "-c",
                "import pathlib,sys;sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes()[:65536])",
                path,
            ]
        )
        if result["exit_code"]:
            raise InfrastructureError(result["stderr"])
        return result["stdout"]

    def close(self):
        if self.container_id:
            _checked(_docker("rm", "--force", self.container_id, timeout=10))
            self.container_id = None
