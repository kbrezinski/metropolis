"""Initialize the lab account and per-container SSH identity, then run sshd."""

from __future__ import annotations

import os
import json
import subprocess
from pathlib import Path
from datetime import datetime, timezone


def emit(event: str, level: str = "INFO", **attributes) -> None:
    print(
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "component": "ssh",
                "event": event,
                "level": level,
                "message": event.replace("_", " "),
                **attributes,
            }
        ),
        flush=True,
    )


def validate_password(password: str) -> str:
    if not password or any(character in password for character in "\r\n\x00"):
        raise ValueError("ENGINEER_PASSWORD must be non-empty single-line text")
    return password


def main() -> None:
    try:
        password = validate_password(os.environ.pop("ENGINEER_PASSWORD", ""))
    except ValueError:
        emit("ssh_start_failed", "ERROR", stage="credential_validation")
        raise
    try:
        subprocess.run(
            ["chpasswd"], input=f"engineer:{password}\n", text=True, check=True
        )
    except subprocess.CalledProcessError:
        emit("ssh_start_failed", "ERROR", stage="account_setup")
        raise
    finally:
        del password
    key_dir = Path("/var/lib/metropolis/ssh")
    key_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = key_dir / "ssh_host_ed25519_key"
    if not key.exists():
        try:
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                check=True,
            )
        except subprocess.CalledProcessError:
            emit("ssh_start_failed", "ERROR", stage="host_key_generation")
            raise
        emit("ssh_host_key_created")
    key.chmod(0o600)
    Path("/run/sshd").mkdir(parents=True, exist_ok=True)
    config = "/etc/ssh/sshd_config_metropolis"
    try:
        subprocess.run(["/usr/sbin/sshd", "-t", "-f", config], check=True)
    except subprocess.CalledProcessError:
        emit("ssh_start_failed", "ERROR", stage="configuration_validation")
        raise
    emit("ssh_service_starting", port=22)
    try:
        os.execv("/usr/sbin/sshd", ["sshd", "-D", "-e", "-f", config])
    except OSError:
        emit("ssh_start_failed", "ERROR", stage="exec")
        raise


if __name__ == "__main__":
    main()
