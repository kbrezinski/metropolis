"""Real loopback fixture behavior under deterministic publication I/O failures."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from agent_bench.grading import grade
from agent_bench.tasks import get_task
from reference_solutions import reference_files
from test_grading import LocalWorkspace


BOOTSTRAP = r"""
import json,os,sys
from pathlib import Path
mode=sys.argv.pop(1)
real_replace=os.replace
failures=0
def replace(source,destination):
 global failures
 name=Path(destination).name
 target=(mode=='startup' and name=='ready.json')
 if mode!='startup' and name=='observations.json':
  value=json.loads(Path(source).read_text())
  target=any(record.get('function')==3 for record in value)
 if target and (mode!='transient' or failures<2):
  failures+=1
  Path('injected-failures.json').write_text(json.dumps({'count':failures}))
  raise PermissionError(13,'Injected observation sharing violation',str(destination))
 return real_replace(source,destination)
os.replace=replace
source=Path('peer.py').read_text()
exec(compile(source,'peer.py','exec'),{'__name__':'__main__'})
"""


class ProtectedPeer(LocalWorkspace):
    """Trusted peer in a separate directory/process; only loopback is available."""

    def __init__(self, path, source_files, command, fault):
        path.mkdir()
        files = {
            name: source.replace("/tmp/", path.as_posix() + "/")
            for name, source in source_files.items()
        }
        super().__init__(path, {**files, "bootstrap.py": BOOTSTRAP})
        self.log = (path / "peer.log").open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, "bootstrap.py", fault, *command[2:]],
            cwd=path,
            stdout=self.log,
            stderr=self.log,
        )

    def execute(self, command, timeout=30):
        return super().execute(command.replace("/tmp/", self.path.as_posix() + "/"), timeout)

    def read_file(self, path):
        translated = Path(path.replace("/tmp/", self.path.as_posix() + "/"))
        return translated.read_text(encoding="utf-8")

    def close(self):
        try:
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        finally:
            self.log.close()


class FixtureWorkspace(LocalWorkspace):
    def __init__(self, path, fault):
        path.mkdir()
        super().__init__(path, reference_files("modbus-finite"))
        self.fault = fault
        self.peers = []

    def start_peer(self, files, command):
        peer = ProtectedPeer(self.path.parent / "peer", files, command, self.fault)
        self.peers.append(peer)
        return peer

    def close(self):
        for peer in self.peers:
            peer.close()


@pytest.mark.parametrize("fault", ["transient", "permanent", "startup"])
def test_fixture_publication_errors_do_not_become_model_failures(tmp_path, fault):
    workspace = FixtureWorkspace(tmp_path / "agent", fault)
    try:
        result = grade(get_task("modbus-finite"), workspace)
        peer = workspace.peers[0]
        injected = json.loads((peer.path / "injected-failures.json").read_text())
        if fault == "transient":
            assert result.passed, result.to_dict()
            assert result.infrastructure_error is None
            assert injected["count"] == 2
            observations = json.loads(peer.read_file("/tmp/observations.json"))
            assert [item["function"] for item in observations] == [6, 3]
        else:
            assert not result.passed
            assert result.infrastructure_error, result.to_dict()
            error = json.loads(peer.read_file("/tmp/fixture-error.json"))
            assert error["type"] == "PermissionError"
            expected_path = "ready.json" if fault == "startup" else "observations.json"
            assert error["stage"].endswith(expected_path)
            assert error["errno"] == 13
    finally:
        workspace.close()


def test_malformed_peer_readiness_is_an_infrastructure_error():
    class InvalidReadyPeer:
        def execute(self, command, timeout=30):
            return {"exit_code": 0, "stdout": "not-json", "stderr": ""}

        def read_file(self, path):
            return "null"  # Healthy fixture error channel, malformed ready response.

    class Boundary:
        def start_peer(self, files, command):
            return InvalidReadyPeer()

        def execute(self, *args, **kwargs):
            pytest.fail("Submitted code must not execute before fixture readiness")

    result = grade(get_task("modbus-finite"), Boundary())
    assert not result.passed
    assert result.infrastructure_error == "Invalid trusted fixture readiness record"
