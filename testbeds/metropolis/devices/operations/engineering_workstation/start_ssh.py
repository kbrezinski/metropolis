"""Initialize the lab account and per-container SSH identity, then run sshd."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def validate_password(password: str) -> str:
    if not password or any(character in password for character in "\r\n\x00"):
        raise ValueError("ENGINEER_PASSWORD must be non-empty single-line text")
    return password


def main() -> None:
    password = validate_password(os.environ.pop("ENGINEER_PASSWORD", ""))
    subprocess.run(["chpasswd"], input=f"engineer:{password}\n", text=True, check=True)
    key_dir = Path("/var/lib/metropolis/ssh")
    key_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = key_dir / "ssh_host_ed25519_key"
    if not key.exists():
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
            check=True,
        )
    key.chmod(0o600)
    Path("/run/sshd").mkdir(parents=True, exist_ok=True)
    config = "/etc/ssh/sshd_config_metropolis"
    subprocess.run(["/usr/sbin/sshd", "-t", "-f", config], check=True)
    os.execv("/usr/sbin/sshd", ["sshd", "-D", "-e", "-f", config])


if __name__ == "__main__":
    main()
