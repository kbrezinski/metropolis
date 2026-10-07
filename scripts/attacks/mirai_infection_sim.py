"""Telnet credential and command-execution experiment; no Mirai lifecycle."""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

from _common import Run, cli, endpoint, ipv4, parser, passwords, positive
from _telnet import Rejected, Session


def main():
    options = parser(__doc__, "MET-LEGACY-GATEWAY-01")
    options.add_argument(
        "--targets", nargs="+", help="Override with up to 16 private lab hosts"
    )
    options.add_argument("--username", default="root")
    options.add_argument("--wordlist", type=Path)
    options.add_argument("--max-attempts", type=int, default=20)
    options.add_argument("--interval", type=positive, default=1)
    options.add_argument(
        "--write-marker",
        action="store_true",
        help="Write /tmp/metropolis-login-test after login",
    )
    args = options.parse_args()
    device, host, port = endpoint(args, "telnet", "TELNET", 23)
    if args.targets and args.host:
        raise ValueError("Select --targets or --host, not both")
    hosts = [ipv4(item) for item in args.targets] if args.targets else [host]
    if len(hosts) > 16:
        raise ValueError("Use at most 16 targets per run")
    candidates = passwords(
        args.wordlist,
        os.getenv("METROPOLIS_TELNET_PASSWORD")
        or device["environment"]["LEGACY_ROOT_PASSWORD"],
        args.max_attempts,
    )
    with Run(
        args,
        "telnet_login",
        hosts=hosts,
        port=port,
        username=args.username,
        attempts_per_host=len(candidates),
        write_marker=args.write_marker,
    ) as run:
        if args.dry_run:
            return 0
        successes = 0
        for target in hosts:
            for index, candidate in enumerate(candidates):
                session = None
                try:
                    session = Session(target, port, args.timeout)
                    session.login(args.username, candidate)
                    verified = session.prove_command(
                        "metropolis_" + secrets.token_hex(16), args.write_marker
                    )
                    run.emit(
                        "attempt",
                        host=target,
                        index=index + 1,
                        status="accepted",
                        command_verified=verified,
                    )
                    successes += verified
                    break
                except Rejected:
                    run.emit("attempt", host=target, index=index + 1, status="rejected")
                except (OSError, RuntimeError) as exc:
                    run.emit(
                        "attempt",
                        host=target,
                        index=index + 1,
                        status="indeterminate",
                        error=str(exc),
                    )
                finally:
                    if session:
                        session.close()
                if index + 1 < len(candidates):
                    time.sleep(args.interval)
        run.emit("summary", command_verified_hosts=successes, targets=len(hosts))
        return 0 if successes else 1


if __name__ == "__main__":
    raise SystemExit(cli(main))
