"""Protect the host/container boundary, not simulated live Docker behavior."""

import io
import os
import shutil
import sys
import tarfile
import time

import pytest

from agent_bench.contracts import AttemptLimit, InfrastructureError
from agent_bench import runtime


def result(stdout=b"", exit_code=0, stderr=b""):
    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "timed_out": False,
        "output_truncated": False,
    }


def archive(entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as output:
        for name, kind, content in entries:
            item = tarfile.TarInfo(name)
            item.type = kind
            if kind == tarfile.SYMTYPE:
                item.linkname = "/etc/passwd"
                output.addfile(item)
            else:
                item.size = len(content)
                output.addfile(item, io.BytesIO(content))
    return buffer.getvalue()


@pytest.mark.parametrize(
    "entries",
    [
        [("../outside.py", tarfile.REGTYPE, b"x")],
        [("/outside.py", tarfile.REGTYPE, b"x")],
        [("C:/outside.py", tarfile.REGTYPE, b"x")],
        [("NUL", tarfile.REGTYPE, b"x")],
        [("nested/CoM1.txt", tarfile.REGTYPE, b"x")],
        [("trailing.", tarfile.REGTYPE, b"x")],
        [("trailing ", tarfile.REGTYPE, b"x")],
        [("name?.py", tarfile.REGTYPE, b"x")],
        [("bad\x01name.py", tarfile.REGTYPE, b"x")],
        [("link", tarfile.SYMTYPE, b"")],
        [("same", tarfile.REGTYPE, b"x"), ("same", tarfile.REGTYPE, b"y")],
        [("File.py", tarfile.REGTYPE, b"x"), ("file.py", tarfile.REGTYPE, b"y")],
        [("Dir", tarfile.REGTYPE, b"x"), ("dir/file.py", tarfile.REGTYPE, b"y")],
        [("dir", tarfile.REGTYPE, b"x"), ("dir/file.py", tarfile.REGTYPE, b"y")],
        [("large.py", tarfile.REGTYPE, b"x" * (runtime.FILE_LIMIT + 1))],
    ],
)
def test_collection_rejects_unsafe_archive_before_writing(tmp_path, monkeypatch, entries):
    payload = archive(entries)
    monkeypatch.setattr(runtime, "_docker", lambda *args, **kwargs: result(payload))
    sandbox = runtime.Sandbox("fixture:test")
    sandbox.container_id = "owned"
    destination = tmp_path / "artifacts"
    with pytest.raises(ValueError):
        sandbox.collect(destination)
    assert not destination.exists()
    assert not (tmp_path / "outside.py").exists()


def test_load_and_collection_preserve_regular_artifacts(tmp_path, monkeypatch):
    source = tmp_path / "input"
    source.mkdir()
    (source / "tools").mkdir()
    (source / "tools" / "entry.py").write_bytes(b"print('fixture')\n")
    calls = []
    payload = archive([("tools/entry.py", tarfile.REGTYPE, b"print('fixture')\n")])

    def docker(*args, **kwargs):
        calls.append((args, kwargs))
        return result(payload if args[1].endswith(":/workspace/.") else b"")

    monkeypatch.setattr(runtime, "_docker", docker)
    sandbox = runtime.Sandbox("fixture:test")
    sandbox.container_id = "owned"
    sandbox.load(source)
    with tarfile.open(fileobj=io.BytesIO(calls[0][1]["data"])) as stream:
        entry = stream.getmember("tools/entry.py")
        assert entry.uid == 1000
        assert stream.extractfile(entry).read() == b"print('fixture')\n"
    target = tmp_path / "collected"
    sandbox.collect(target)
    assert (target / "tools" / "entry.py").read_bytes() == b"print('fixture')\n"


def test_docker_launch_contract_is_no_egress_and_no_host_mounts(monkeypatch):
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return result(b"sha256:fixture\n")
        return result(b"managed-container\n")

    monkeypatch.setattr(runtime, "_docker", docker)
    with runtime.Sandbox("fixture:test", raw_network=False) as sandbox:
        command = next(args for args in calls if args[0] == "run")
        assert command[command.index("--network") + 1] == "none"
        assert "--read-only" in command
        assert command[command.index("--cap-drop") + 1] == "ALL"
        assert "NET_RAW" not in command
        assert "--privileged" not in command
        assert not any(arg in command for arg in ("--volume", "-v", "--mount"))
        assert "no-new-privileges:true" in command
        assert sandbox.image_id == "sha256:fixture"
    assert ("rm", "--force", "managed-container") in calls


def test_peer_has_protected_filesystem_and_task_local_network(monkeypatch):
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return result(b"sha256:fixture\n")
        if args[0] == "run":
            return result(f"container-{len(calls)}".encode())
        return result()

    monkeypatch.setattr(runtime, "_docker", docker)
    with runtime.Sandbox("fixture:test") as sandbox:
        sandbox.start_peer({"observer.py": "print('ready')"}, ["python", "/workspace/observer.py"])
        launches = [args for args in calls if args[0] == "run"]
        main, peer = launches
        assert "/workspace:rw,nosuid,size=64m,uid=1000,gid=1000,mode=0755" in main
        assert "/workspace:rw,nosuid,size=64m,uid=0,gid=0,mode=0755" in peer
        assert peer[peer.index("--network") + 1] == "container:" + sandbox.container_id
        assert "--pid" not in peer
        assert not any(arg in peer for arg in ("--volume", "-v", "--mount"))


def test_failed_peer_cleanup_still_removes_main_container(monkeypatch):
    calls = []

    class BrokenPeer:
        def close(self):
            raise InfrastructureError("peer removal unavailable")

    monkeypatch.setattr(runtime, "_docker", lambda *args, **kwargs: calls.append(args) or result())
    sandbox = runtime.Sandbox("fixture:test")
    sandbox.container_id = "owned-main"
    sandbox.peers = [BrokenPeer()]
    with pytest.raises(InfrastructureError, match="peer removal"):
        sandbox.close()
    assert ("rm", "--force", "owned-main") in calls
    assert sandbox.container_id is None


def test_partial_container_start_is_cleaned_up_by_known_name(monkeypatch):
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return result(b"sha256:fixture\n")
        if args[0] == "run":
            return result(exit_code=1, stderr=b"transport interrupted")
        return result()

    monkeypatch.setattr(runtime, "_docker", docker)
    with pytest.raises(InfrastructureError):
        with runtime.Sandbox("fixture:test"):
            pytest.fail("Failed setup must not enter the task")
    launch = next(args for args in calls if args[0] == "run")
    known_name = launch[launch.index("--name") + 1]
    assert ("rm", "--force", known_name) in calls


@pytest.mark.parametrize(
    "raw,expected", [(False, "--bounding-set=-all"), (True, "--bounding-set=-all,+net_raw")]
)
def test_submitted_commands_drop_root_and_unneeded_capabilities(monkeypatch, raw, expected):
    commands = []
    monkeypatch.setattr(
        runtime, "_docker", lambda *args, **kwargs: commands.append(args) or result()
    )
    sandbox = runtime.Sandbox("fixture:test", raw_network=raw)
    sandbox.container_id = "owned"
    sandbox.started = time.monotonic()
    sandbox.execute("printf done")
    command = commands[0]
    assert "--reuid=1000" in command
    assert "--regid=1000" in command
    assert "--clear-groups" in command
    assert expected in command
    assert "--signal=KILL" in command


def test_process_output_is_drained_but_retention_is_bounded():
    response = runtime._invoke(
        [
            sys.executable,
            "-c",
            "import sys;sys.stdout.write('A'*200000);sys.stderr.write('B'*200000)",
        ],
        limit=1000,
        timeout=10,
    )
    assert response["stdout"] == b"A" * 1000
    assert response["stderr"] == b"B" * 1000
    assert response["output_truncated"]
    assert response["exit_code"] == 0


def test_process_timeout_and_attempt_budget_are_bounded():
    response = runtime._invoke([sys.executable, "-c", "import time;time.sleep(10)"], timeout=0.1)
    assert response["timed_out"]
    sandbox = runtime.Sandbox("fixture:test", timeout=1)
    sandbox.started = time.monotonic() - 2
    with pytest.raises(AttemptLimit):
        sandbox.execute("echo never-start")


@pytest.mark.parametrize(
    "method,arguments", [("read_file", ("input.txt",)), ("write_file", ("output.txt", "content"))]
)
def test_workspace_file_deadline_reaches_docker_and_stops_attempt(monkeypatch, method, arguments):
    calls = []

    def docker(*args, **kwargs):
        calls.append((args, kwargs))
        return {**result(exit_code=124), "timed_out": True}

    monkeypatch.setattr(runtime, "_docker", docker)
    sandbox = runtime.Sandbox("fixture:test")
    sandbox.container_id = "owned"
    sandbox.started = time.monotonic()
    with pytest.raises(AttemptLimit, match="Workspace file operation timed out"):
        getattr(sandbox, method)(*arguments, timeout=1.25)
    command, options = calls[0]
    assert command[command.index("--signal=KILL") + 1] == "1.250"
    assert options["timeout"] == 4.25  # Container deadline plus Docker CLI housekeeping.


def test_nonzero_artifact_exit_does_not_become_infrastructure_failure(monkeypatch):
    def docker(*args, **kwargs):
        if args[0] == "exec":
            return result(exit_code=125, stderr=b"application exited")
        return result(b"true\n")

    monkeypatch.setattr(runtime, "_docker", docker)
    sandbox = runtime.Sandbox("fixture:test")
    sandbox.container_id = "owned"
    sandbox.started = time.monotonic()
    assert sandbox.execute("exit 125")["exit_code"] == 125


def test_stopped_container_is_infrastructure_failure(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "_docker",
        lambda *args, **kwargs: result(exit_code=1) if args[0] == "exec" else result(b"false\n"),
    )
    sandbox = runtime.Sandbox("fixture:test")
    sandbox.container_id = "owned"
    sandbox.started = time.monotonic()
    with pytest.raises(InfrastructureError, match="stopped"):
        sandbox.execute("echo unreachable")


def test_oom_stopped_container_is_model_resource_failure(monkeypatch):
    def docker(*args, **kwargs):
        if args[0] == "exec":
            return result(exit_code=1, stderr=b"container stopped")
        if "{{.State.OOMKilled}}" in args:
            return result(b"true\n")
        return result(b"false\n")

    monkeypatch.setattr(runtime, "_docker", docker)
    sandbox = runtime.Sandbox("fixture:test")
    sandbox.container_id = "owned"
    sandbox.started = time.monotonic()
    with pytest.raises(AttemptLimit, match="memory limit"):
        sandbox.execute("python memory_exhaustion.py")


def test_workspace_tool_paths_reject_escape_before_docker(monkeypatch):
    def must_not_call(*args, **kwargs):
        pytest.fail("Unsafe paths must be rejected before entering Docker")

    monkeypatch.setattr(runtime, "_docker", must_not_call)
    sandbox = runtime.Sandbox("fixture:test")
    with pytest.raises(ValueError):
        sandbox.write_file("../proof/a.trace", "forged")
    with pytest.raises(ValueError):
        sandbox.read_file("/etc/passwd")


def test_real_docker_workspace_peer_and_protected_audit(tmp_path):
    """Opt-in evidence for the real kernel/namespace boundary, not a mocked run."""
    if os.environ.get("AGENT_BENCH_DOCKER_TESTS") != "1":
        pytest.skip("Set AGENT_BENCH_DOCKER_TESTS=1 after building the fixture image")
    if not shutil.which("docker"):
        pytest.skip("Docker CLI is unavailable")
    image = os.environ.get("AGENT_BENCH_IMAGE", runtime.DEFAULT_IMAGE)
    runtime.check_image(image)
    with runtime.Sandbox(image, raw_network=True) as sandbox:
        identity = sandbox.execute("id -u")
        assert identity["exit_code"] == 0
        assert identity["stdout"].strip() == "1000"
        assert sandbox.execute("touch /etc/agent-bench-forbidden")["exit_code"] != 0
        assert sandbox.execute("cat /proc/net/route")["stdout"].strip().count("\n") == 0
        sandbox.write_file("artifact.py", "print('independent artifact')\n")
        peer = sandbox.start_peer(
            {"peer.py": "import time;time.sleep(60)\n"}, ["python", "/workspace/peer.py"]
        )
        assert peer.execute("printf protected >/tmp/observer")["exit_code"] == 0
        assert peer.read_file("/tmp/observer") == "protected"
        assert sandbox.execute("cat /tmp/observer")["exit_code"] != 0
        audit = sandbox.audited_execute("python artifact.py")
        assert audit["exit_code"] == 0
        assert "independent artifact" in audit["stdout"]
        assert "execve(" in audit["exec_trace"]
        assert sandbox.execute("ls /proof")["exit_code"] != 0
        destination = tmp_path / "submission"
        sandbox.collect(destination)
        assert (destination / "artifact.py").read_text() == "print('independent artifact')\n"
