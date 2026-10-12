"""Bounded MQTT credential attempts against an inventoried lab broker."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from _common import (
    Run,
    add_tls_arguments,
    cli,
    endpoint,
    parser,
    passwords,
    positive,
    tls_from_args,
)
from _mqtt import BrokerConnection


MODULE = "auxiliary/scanner/mqtt/connect"
METASPLOIT_WRONG_PASSWORDS = (
    "admin",
    "password",
    "password123",
    "12345678",
    "changeme",
    "mqtt",
    "mosquitto",
    "guest",
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
    "SensorNet1!",
    "PumpStation1!",
    "Welcome123!",
    "Default123!",
)


def password_candidates(args, known_good: str) -> list[str]:
    """Use a reproducible failure sequence before lab success in MSF mode."""
    if args.wordlist or args.engine != "metasploit":
        return passwords(args.wordlist, known_good, args.max_attempts)
    # Reuse the shared validation for --max-attempts. The configured lab
    # password is excluded from the failure fixtures if a future testbed
    # happens to use one of them.
    passwords(None, known_good, args.max_attempts)
    wrong = [value for value in METASPLOIT_WRONG_PASSWORDS if value != known_good]
    return wrong[: args.max_attempts - 1] + [known_good]


def metasploit_scan(args, run, host: str, port: int, username: str, candidates: list[str]) -> int:
    """Run Rapid7's MQTT scanner, retaining only credential-free evidence."""
    if args.tls_ca or args.tls_insecure:
        raise ValueError("Metasploit mode supports --tls, but not --tls-ca/--tls-insecure")
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", username):
        raise ValueError("Metasploit username must be 1–64 simple ASCII characters")
    executable = shutil.which("msfconsole")
    if not executable:
        raise RuntimeError("msfconsole is not installed or is not on PATH")

    with tempfile.TemporaryDirectory(prefix="metropolis_msf_") as directory:
        password_file = Path(directory) / "passwords.txt"
        if any(character in str(password_file) for character in ('"', "\n", "\r")):
            raise ValueError("Temporary path cannot be represented in a Metasploit resource")
        password_file.write_text("\n".join(candidates) + "\n", encoding="utf-8")
        if os.name == "posix":
            password_file.chmod(0o600)
        # Resource commands arrive on stdin; no password goes into argv, logs,
        # or the resource script. -n keeps credential findings out of the MSF DB.
        resource = "\n".join(
            [
                f"use {MODULE}",
                f"set RHOSTS {host}",
                f"set RPORT {port}",
                f"set USERNAME {username}",
                'set USER_FILE ""',
                'set USERPASS_FILE ""',
                'set PASSWORD ""',
                f'set PASS_FILE "{password_file.as_posix()}"',
                "set USER_AS_PASS false",
                "set BLANK_PASSWORDS false",
                "set STOP_ON_SUCCESS true",
                "set THREADS 1",
                "set VERBOSE false",
                f"set READ_TIMEOUT {args.timeout}",
                f"set ConnectTimeout {args.timeout}",
                f"set BRUTEFORCE_SPEED {args.metasploit_speed}",
                f"set SSL {str(args.tls).lower()}",
                "run",
                "exit -y",
                "",
            ]
        )
        try:
            result = subprocess.run(
                [executable, "-q", "-n", "-r", "-"],
                input=resource,
                text=True,
                capture_output=True,
                timeout=args.metasploit_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            run.emit("summary", engine="metasploit", status="timeout", module=MODULE)
            return 2

    # The module prints successful username/password pairs. Never forward raw
    # stdout/stderr, even on failure, because a console error may echo them.
    output = result.stdout or ""
    if (
        result.returncode != 0
        or "Auxiliary module execution completed" not in output
        or "Auxiliary failed:" in output
        or "Failed to load module" in output
        or "Unknown command" in output
    ):
        status = "execution_error"
        code = 2
    elif "Does not require authentication" in output:
        status = "anonymous_access"
        code = 0
    elif "MQTT Login Successful:" in output:
        status = "credential_accepted"
        code = 0
    else:
        status = "no_credential_accepted"
        code = 1
    run.emit("summary", engine="metasploit", module=MODULE, status=status)
    return code


def main():
    options = parser(__doc__, "MET-MQTT-BROKER-01")
    options.add_argument("--username")
    options.add_argument("--wordlist", type=Path)
    options.add_argument("--max-attempts", type=int, default=25)
    options.add_argument("--interval", type=positive, default=1)
    options.add_argument("--engine", choices=("paho", "metasploit"), default="paho")
    options.add_argument("--metasploit-speed", type=int, choices=range(6), default=3)
    options.add_argument("--metasploit-timeout", type=positive, default=180)
    add_tls_arguments(options)
    args = options.parse_args()
    device, host, port = endpoint(args, "mqtt", "MQTT", 1883)
    env = device.get("environment", {})
    username = (
        args.username
        or os.getenv("METROPOLIS_MQTT_USERNAME")
        or env["MQTT_OPERATOR_USERNAME"]
    )
    candidates = password_candidates(
        args, os.getenv("METROPOLIS_MQTT_PASSWORD") or env["MQTT_OPERATOR_PASSWORD"]
    )
    with Run(
        args,
        "mqtt_credentials",
        host=host,
        port=port,
        username=username,
        attempts=len(candidates),
        engine=args.engine,
    ) as run:
        if args.dry_run:
            return 0
        if args.engine == "metasploit":
            return metasploit_scan(args, run, host, port, username, candidates)
        for index, candidate in enumerate(candidates):
            with BrokerConnection(
                host, port, username, candidate, args.timeout, tls_from_args(args)
            ) as connection:
                run.emit(
                    "attempt",
                    index=index + 1,
                    status=connection.status,
                    reason=connection.reason,
                )
                if connection.status == "accepted":
                    return 0
                if connection.status != "rejected":
                    return 2
            if index + 1 < len(candidates):
                time.sleep(args.interval)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli(main))
