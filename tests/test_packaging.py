"""Packaging checks for the bundled topology dataset.

The topology library is only useful from an installed wheel if the wheel ships
all four dataset documents. A source checkout hides a packaging mistake because
``Topology.from_bundled()`` falls back to the repository, so this test builds the
wheel, installs it into a temporary target, and runs the CLI with only that
target on ``PYTHONPATH``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WHEEL_PREFIX = Path("metropolis/data/testbeds/metropolis/water_treatment_v1")
DOCUMENTS = (
    "topology/address-plan.yaml",
    "topology/links.yaml",
    "device_instances/initial_devices.yaml",
    "device_instances/infrastructure.yaml",
)


def _uv() -> str:
    executable = shutil.which("uv")
    if executable is None:
        pytest.skip("uv is needed to build and install the wheel")
    return executable


def _build_wheel(tmp_path: Path, uv: str) -> Path:
    destination = (tmp_path / "dist").resolve()
    environment = {
        **os.environ,
        # Keep the build's cache inside the test so it cannot collide with a
        # developer's or CI's uv cache.
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "UV_NO_PROGRESS": "1",
    }
    completed = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(destination)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    wheels = list(destination.glob("*.whl"))
    assert len(wheels) == 1, f"expected one wheel, found {wheels}"
    return wheels[0]


def _install_wheel(tmp_path: Path, uv: str, wheel: Path) -> Path:
    target = (tmp_path / "installed").resolve()
    environment = {
        **os.environ,
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "UV_NO_PROGRESS": "1",
    }
    completed = subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--no-deps",
            "--python",
            sys.executable,
            "--target",
            str(target),
            str(wheel),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return target


@pytest.mark.slow
def test_the_wheel_ships_every_topology_document_and_runs_the_cli(tmp_path):
    uv = _uv()
    wheel = _build_wheel(tmp_path, uv)

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    expected = {(WHEEL_PREFIX / document).as_posix() for document in DOCUMENTS}
    missing = sorted(expected - names)
    assert not missing, f"{wheel.name} is missing {missing}"

    target = _install_wheel(tmp_path, uv, wheel)
    environment = {**os.environ, "PYTHONPATH": str(target)}

    probe = (
        "import sys\n"
        "from pathlib import Path\n"
        "import metropolis\n"
        "installed = Path(sys.argv[1]).resolve()\n"
        "package = Path(metropolis.__file__).resolve()\n"
        "assert installed in package.parents, (installed, package)\n"
        "from metropolis.topology import load_bundled_topology\n"
        "topology = load_bundled_topology()\n"
        "assert topology.networks and topology.links\n"
        "assert topology.devices and topology.routers\n"
        "assert not topology.problems(), topology.problems()\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe, str(target)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    cli = subprocess.run(
        [sys.executable, "-m", "metropolis.topology", "check"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert cli.returncode == 0, cli.stdout + cli.stderr
    assert "No contradictions found." in cli.stdout
