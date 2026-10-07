#!/usr/bin/env python3
"""Single entry point for the Metropolis experiment toolkit.

Every other tool in this directory is a Python module. This dispatcher names
the module, forwards the remaining arguments to it, and returns the module's
own exit code::

    python scripts/attacks/run.py mqtt_bruteforce_attack --host 10.20.23.10

It exists so a lab host needs one documented path to every tool, and so the
interpreter that carries the research dependencies can be chosen in one place
instead of eleven shell shims. The per-tool ``.sh`` launchers remain for
compatibility; both routes resolve to the same module.

The tool list is discovered from the directory, so a new module appears here
automatically.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent
# A module is an entry point when it guards execution with the usual main
# block. Helper modules such as _common, _mqtt, _telnet, and _c2 define no such
# guard, and underscore-prefixed modules are never treated as tools.
ENTRY_POINT = re.compile(r'^if __name__ == "__main__":', re.MULTILINE)


def is_entry_point(path: Path) -> bool:
    if path.name.startswith("_"):
        return False
    try:
        return bool(ENTRY_POINT.search(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError):
        return False


def tools() -> dict[str, Path]:
    """Map tool name to module for every runnable module in this directory."""
    return {
        path.stem: path
        for path in sorted(DIRECTORY.glob("*.py"))
        if path.name != Path(__file__).name and is_entry_point(path)
    }


def usage(stream) -> None:
    names = sorted(tools())
    print(
        "usage: run.py <tool> [tool arguments]\n\n"
        "Single entry point for the Metropolis experiment toolkit.\n"
        "Run 'run.py <tool> --help' for a tool's own options.\n",
        file=stream,
    )
    print("tools:", file=stream)
    for name in names:
        print(f"  {name}", file=stream)
    print("\nexample:", file=stream)
    print(f"  run.py {names[0]} --dry-run" if names else "", file=stream)


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in {"-h", "--help", "help", "list"}:
        usage(sys.stdout if len(argv) > 1 else sys.stderr)
        return 0 if len(argv) > 1 else 2

    requested = argv[1]
    available = tools()
    if requested not in available:
        print(f"error: unknown tool {requested!r}", file=sys.stderr)
        matches = [name for name in available if requested in name]
        if matches:
            print(f"       did you mean: {', '.join(matches)}", file=sys.stderr)
        usage(sys.stderr)
        return 2

    module = available[requested]
    # METROPOLIS_PYTHON names the interpreter that carries the research
    # dependencies; without it the current interpreter is reused.
    interpreter = os.environ.get("METROPOLIS_PYTHON") or sys.executable
    # The tools import their shared helpers as siblings, so the toolkit
    # directory must be on the child's sys.path.
    environment = os.environ.copy()
    existing = environment.get("PYTHONPATH")
    if str(DIRECTORY) not in (existing or "").split(os.pathsep):
        environment["PYTHONPATH"] = (
            f"{DIRECTORY}{os.pathsep}{existing}" if existing else str(DIRECTORY)
        )
    # A child process rather than os.execv: on Windows os.execv does not
    # propagate the child's exit code, which would silently turn a tool's
    # documented exit 2 into a success.
    completed = subprocess.run([interpreter, str(module), *argv[2:]], env=environment)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
