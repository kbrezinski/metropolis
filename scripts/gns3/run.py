#!/usr/bin/env python3
"""Single entry point for the GNS3 automation.

Every command in this directory is a Python module. This dispatcher names the
module, forwards the remaining arguments to it, and returns its exit code::

    python scripts/gns3/run.py --help
    python scripts/gns3/run.py build_topology --plan
    python scripts/gns3/run.py lab_lifecycle --start

The tool list is discovered from the directory, so a new command appears here
automatically. Modules whose name starts with an underscore are libraries.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent
ENTRY_POINT = re.compile(r'^if __name__ == "__main__":', re.MULTILINE)


def is_entry_point(path: Path) -> bool:
    if path.name.startswith("_") or path.name == Path(__file__).name:
        return False
    try:
        return bool(ENTRY_POINT.search(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError):
        return False


def tools() -> dict[str, Path]:
    return {
        path.stem: path
        for path in sorted(DIRECTORY.glob("*.py"))
        if is_entry_point(path)
    }


def usage(stream) -> None:
    names = sorted(tools())
    print(
        "usage: run.py <command> [command arguments]\n\n"
        "Single entry point for the GNS3 automation.\n"
        "Run 'run.py <command> --help' for a command's own options.\n",
        file=stream,
    )
    print("commands:", file=stream)
    for name in names:
        print(f"  {name}", file=stream)


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in {"-h", "--help", "help", "list"}:
        usage(sys.stdout if len(argv) > 1 else sys.stderr)
        return 0 if len(argv) > 1 else 2

    requested = argv[1]
    available = tools()
    if requested not in available:
        print(f"error: unknown command {requested!r}", file=sys.stderr)
        matches = [name for name in available if requested in name]
        if matches:
            print(f"       did you mean: {', '.join(matches)}", file=sys.stderr)
        usage(sys.stderr)
        return 2

    environment = os.environ.copy()
    existing = environment.get("PYTHONPATH")
    if str(DIRECTORY) not in (existing or "").split(os.pathsep):
        environment["PYTHONPATH"] = (
            f"{DIRECTORY}{os.pathsep}{existing}" if existing else str(DIRECTORY)
        )
    interpreter = os.environ.get("METROPOLIS_PYTHON") or sys.executable
    completed = subprocess.run(
        [interpreter, str(available[requested]), *argv[2:]], env=environment
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
