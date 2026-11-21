"""The recorded harness version must match the distributed package version."""

import tomllib
from pathlib import Path

import agent_bench


def test_harness_version_matches_package_metadata():
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert agent_bench.__version__ == project["project"]["version"]
