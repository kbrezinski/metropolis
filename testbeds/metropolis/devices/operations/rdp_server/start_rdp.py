#!/usr/bin/env python3
"""Supervise xrdp, synthetic UDP and an optional cohosted HTTPS relay."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path


XRDP_CONFIG = Path("/etc/xrdp/xrdp.ini")
IDENTITY_DIR = Path("/var/lib/metropolis-rdp")


def emit(event: str, **attributes) -> None:
    print(json.dumps({"component": "rdp", "event": event, **attributes}), flush=True)


def setup_account() -> None:
    import pwd  # Linux container only; keep module importable for offline tests.

    username = os.getenv("RDP_USERNAME", "")
    password = os.environ.pop("RDP_PASSWORD", "")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", username) or username == "root":
        raise ValueError("RDP_USERNAME must name a non-root local account")
    if not password or any(character in password for character in "\r\n\x00:"):
        raise ValueError("RDP_PASSWORD must be non-empty single-line text")
    try:
        pwd.getpwnam(username)
    except KeyError:
        subprocess.run(
            ["useradd", "--create-home", "--shell", "/bin/bash", username], check=True
        )
    try:
        subprocess.run(
            ["chpasswd"], input=f"{username}:{password}\n", text=True, check=True
        )
    finally:
        del password
    sessions = IDENTITY_DIR / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    subprocess.run(["chown", username, str(sessions)], check=True)
    sessions.chmod(0o700)
    session = Path("/home") / username / ".xsession"
    session.write_text(
        "#!/bin/sh\n"
        "openbox-session &\n"
        "desktop_pid=$!\n"
        "sleep 2\n"
        'if kill -0 "$desktop_pid" 2>/dev/null; then\n'
        f"    date +%s%N > {sessions / 'started_ns'}\n"
        "fi\n"
        'wait "$desktop_pid"\n',
        encoding="utf-8",
    )
    session.chmod(0o700)
    subprocess.run(["chown", f"{username}:{username}", str(session)], check=True)


def setup_certificate() -> tuple[Path, Path]:
    IDENTITY_DIR.mkdir(parents=True, exist_ok=True)
    certificate = IDENTITY_DIR / "cert.pem"
    key = IDENTITY_DIR / "key.pem"
    if not certificate.exists() or not key.exists():
        hostname = os.getenv("NODE_HOSTNAME", "metropolis-rdp")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,62}", hostname):
            raise ValueError("NODE_HOSTNAME is not a valid certificate name")
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "365",
                "-subj",
                f"/CN={hostname}",
                "-keyout",
                str(key),
                "-out",
                str(certificate),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    subprocess.run(["chgrp", "ssl-cert", str(key)], check=True)
    key.chmod(0o640)
    certificate.chmod(0o644)
    return certificate, key


def configure_xrdp(certificate: Path, key: Path) -> None:
    config = XRDP_CONFIG.read_text(encoding="utf-8")
    for setting, value in (
        ("security_layer", "tls"),
        ("require_credentials", "true"),
        ("autorun", "Xorg"),
        ("certificate", str(certificate)),
        ("key_file", str(key)),
    ):
        config, count = re.subn(
            rf"(?m)^[#;]?{re.escape(setting)}=.*$",
            lambda _match: f"{setting}={value}",
            config,
            count=1,
        )
        if count != 1:
            raise ValueError(f"xrdp setting is missing: {setting}")
    XRDP_CONFIG.write_text(config, encoding="utf-8")


def main() -> int:
    children: list[tuple[str, subprocess.Popen]] = []
    interrupted = False

    def stop(_signum, _frame) -> None:
        nonlocal interrupted
        interrupted = True

    previous_signals = {
        signum: signal.signal(signum, stop)
        for signum in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        relay_mode = os.getenv("TELEMETRY_ENABLED", "false").strip().lower()
        if relay_mode not in {"true", "1", "yes", "on", "false", "0", "no", "off", ""}:
            raise ValueError("TELEMETRY_ENABLED must be a boolean")
        relay_enabled = relay_mode in {"true", "1", "yes", "on"}
        setup_account()
        certificate, key = setup_certificate()
        configure_xrdp(certificate, key)
        Path("/run/xrdp").mkdir(parents=True, exist_ok=True)
        services = [
            ("xrdp-sesman", ["/usr/sbin/xrdp-sesman", "--nodaemon"]),
            ("synthetic-udp", ["python3", "-u", "/opt/metropolis/rdp_udp.py", "serve"]),
            ("xrdp", ["/usr/sbin/xrdp", "--nodaemon"]),
        ]
        if relay_enabled:
            services.append(
                ("telemetry-relay", ["python3", "-u", "/opt/metropolis/relay.py"])
            )
        for name, command in services:
            children.append((name, subprocess.Popen(command)))
        emit("rdp_service_starting", tcp_port=3389, udp_port=3389, udp_kind="synthetic")
        while not interrupted:
            for name, child in children:
                if child.poll() is not None:
                    emit("rdp_service_failed", service=name, exit_code=child.returncode)
                    return 1
            time.sleep(0.5)
        return 0
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        emit("rdp_service_failed", error=type(exc).__name__)
        return 1
    finally:
        for _name, child in reversed(children):
            if child.poll() is None:
                try:
                    child.terminate()
                except ProcessLookupError:
                    pass  # It exited between the poll and termination.
        for _name, child in reversed(children):
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    child.kill()
                except ProcessLookupError:
                    pass
                child.wait()
        for signum, handler in previous_signals.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
