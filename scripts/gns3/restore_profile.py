#!/usr/bin/env python3
"""Retry restoration of a run's original profile settings after a failed cleanup."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _cli import add_server_arguments, connect
from collect_device_logs import docker_host_mapping
from profile_enforcement import ProfileSession


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_server_arguments(parser)
    parser.add_argument("--state", type=Path, required=True, help="Run's profiles.json")
    parser.add_argument(
        "--docker-host", action="append", default=[], metavar="COMPUTE_ID=URI"
    )
    args = parser.parse_args()
    try:
        result = ProfileSession.recover(
            connect(args), args.state, docker_host_mapping(args.docker_host)
        )
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0 if result["restored"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
