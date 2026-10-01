import subprocess
import sys
from pathlib import Path


def test_metropolis_topology_documents_are_consistent():
    repository_root = Path(__file__).resolve().parents[1]
    validator = repository_root / "scripts" / "validate_metropolis_topology.py"
    result = subprocess.run(
        [sys.executable, str(validator), "--repo-root", str(repository_root)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "6/6 checks passed" in result.stdout
