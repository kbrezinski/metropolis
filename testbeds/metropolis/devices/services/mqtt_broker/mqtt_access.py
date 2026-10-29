"""Create Mosquitto password and ACL files from the selected lab inventory."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import pwd
except ImportError:  # Tests also import the helper on Windows.
    pwd = None


_USERNAME = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
_TOPIC = re.compile(r"[A-Za-z0-9_./+#-]{1,256}\Z")


def decode_access(encoded: str, expected_sha256: str) -> list[dict]:
    """Validate the broker transport payload without echoing its contents."""
    try:
        data = base64.b64decode(encoded, validate=True)
        if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), expected_sha256):
            raise ValueError("MQTT access payload hash mismatch")
        payload = json.loads(data)
    except (ValueError, TypeError, UnicodeError, binascii.Error) as exc:
        raise ValueError("invalid MQTT access payload") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or not isinstance(payload.get("accounts"), list)
        or not payload["accounts"]
    ):
        raise ValueError("invalid MQTT access payload")
    seen = set()
    for account in payload["accounts"]:
        if not isinstance(account, dict):
            raise ValueError("invalid MQTT access account")
        username = account.get("username")
        password = account.get("password")
        if (
            not isinstance(username, str)
            or not _USERNAME.fullmatch(username)
            or username in seen
            or not isinstance(password, str)
            or not password
            or any(char in password for char in ":\r\n\x00")
        ):
            raise ValueError("invalid MQTT access account")
        seen.add(username)
        for direction in ("read", "write"):
            topics = account.get(direction)
            if not isinstance(topics, list) or not all(
                isinstance(topic, str) for topic in topics
            ):
                raise ValueError("invalid MQTT access topics")
            if len(topics) != len(set(topics)):
                raise ValueError("invalid MQTT access topics")
            for topic in topics:
                if not _TOPIC.fullmatch(topic):
                    raise ValueError("invalid MQTT access topics")
                if "+" in topic or (
                    "#" in topic and (not topic.endswith("/#") or topic.count("#") != 1)
                ):
                    raise ValueError("invalid MQTT access topics")
    return payload["accounts"]


def _temporary_file(directory: Path, content: str) -> Path:
    descriptor, filename = tempfile.mkstemp(dir=directory)
    path = Path(filename)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    path.chmod(0o600)
    return path


def write_access(accounts: list[dict], directory: Path) -> None:
    """Hash temporary passwords, then install private broker files."""
    directory.mkdir(parents=True, exist_ok=True)
    password_text = "".join(
        f"{account['username']}:{account['password']}\n" for account in accounts
    )
    acl_text = "".join(
        f"user {account['username']}\n"
        + "".join(f"topic read {topic}\n" for topic in account["read"])
        + "".join(f"topic write {topic}\n" for topic in account["write"])
        for account in accounts
    )
    password_path = _temporary_file(directory, password_text)
    acl_path = _temporary_file(directory, acl_text)
    try:
        subprocess.run(
            ["mosquitto_passwd", "-U", str(password_path)],
            check=True,
            capture_output=True,
        )
        if pwd is not None and os.geteuid() == 0:
            owner = pwd.getpwnam("mosquitto")
            os.chown(password_path, owner.pw_uid, owner.pw_gid)
            os.chown(acl_path, owner.pw_uid, owner.pw_gid)
        password_path.replace(directory / "passwordfile")
        acl_path.replace(directory / "aclfile")
    finally:
        password_path.unlink(missing_ok=True)
        acl_path.unlink(missing_ok=True)


def main() -> int:
    try:
        accounts = decode_access(
            os.environ["MQTT_ACCESS_B64"], os.environ["MQTT_ACCESS_SHA256"]
        )
        write_access(accounts, Path("/mosquitto/data"))
    except (KeyError, ValueError, OSError, subprocess.CalledProcessError):
        print(
            "MQTT access configuration failed; check selected inventory",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
