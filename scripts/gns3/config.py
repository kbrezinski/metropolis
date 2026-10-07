"""Locate and read a local GNS3 server's connection settings.

The GNS3 client stores its server settings per version, for example
``~/.config/GNS3/2.2/gns3_server.conf``. Gotham hardcoded that version, so it
broke on any other install. Here the newest version directory that actually
contains a config is used, and environment variables override the file so a
remote or containerised server needs no local config at all.
"""

from __future__ import annotations

import configparser
import os
from pathlib import Path

from _transport import Server

# Overrides, checked before the config file.
ENV_HOST = "GNS3_SERVER_HOST"
ENV_PORT = "GNS3_SERVER_PORT"
ENV_USER = "GNS3_SERVER_USERNAME"
ENV_PASSWORD = "GNS3_SERVER_PASSWORD"

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 3080

_CONFIG_NAME = "gns3_server.conf"
_SECTION = "Server"


def config_root() -> Path:
    """The GNS3 settings directory for the current platform."""
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home())) / "GNS3"
    if os.uname().sysname == "Darwin":  # pragma: no cover - macOS only
        return Path.home() / "Library" / "Application Support" / "GNS3"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "GNS3"


def config_files(root: Path | None = None) -> list[Path]:
    """Every server config found, newest version first.

    A version directory is taken literally rather than assumed to be ``2.2``,
    so an upgrade does not silently hide the settings.
    """
    base = root or config_root()
    if not base.is_dir():
        return []
    found = [path / _CONFIG_NAME for path in base.iterdir() if path.is_dir()]
    return sorted(
        (path for path in found if path.is_file()),
        key=lambda path: path.parent.name,
        reverse=True,
    )


def read_server_config(path: Path) -> dict[str, str]:
    """Read the ``[Server]`` section of one config file."""
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    if not parser.has_section(_SECTION):
        return {}
    return {key.lower(): value for key, value in parser.items(_SECTION)}


def resolve_server(
    root: Path | None = None, env: dict[str, str] | None = None
) -> Server:
    """Build a Server from the environment, falling back to the newest config.

    Raises FileNotFoundError when neither source provides settings, since
    guessing a host would produce a confusing connection error later.
    """
    environment = os.environ if env is None else env
    settings: dict[str, str] = {}
    for path in config_files(root):
        settings = read_server_config(path)
        if settings:
            break

    host = environment.get(ENV_HOST) or settings.get("host") or DEFAULT_HOST
    port_text = environment.get(ENV_PORT) or settings.get("port")
    if port_text is None and ENV_HOST not in environment and not settings:
        raise FileNotFoundError(
            "No GNS3 server settings found. Set GNS3_SERVER_HOST and GNS3_SERVER_PORT, "
            "or run the GNS3 client once so it writes gns3_server.conf."
        )
    try:
        port = int(port_text) if port_text is not None else DEFAULT_PORT
    except ValueError as exc:
        raise ValueError(f"GNS3 server port {port_text!r} is not a number") from exc

    username = environment.get(ENV_USER) or settings.get("user") or None
    password = environment.get(ENV_PASSWORD) or settings.get("password") or None
    return Server(host=host, port=port, username=username, password=password)
