"""Checks for template registration and GNS3 server settings discovery."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

GNS3 = Path(__file__).resolve().parents[1] / "scripts" / "gns3"
if str(GNS3) not in sys.path:
    sys.path.insert(0, str(GNS3))

from config import (  # noqa: E402
    ENV_HOST,
    ENV_PASSWORD,
    ENV_PORT,
    ENV_USER,
    resolve_server,
)
from templates import (  # noqa: E402
    ImageTemplate,
    register_templates,
)


class FakeClient:
    """Stands in for Gns3Client, recording template calls."""

    def __init__(self, existing=None):
        self.existing = list(existing or [])
        self.created: list[dict] = []
        self.updated: list[tuple[str, dict]] = []
        self.deleted: list[str] = []

    def templates(self):
        return list(self.existing)

    def create_template(self, payload):
        self.created.append(payload)
        self.existing.append({**payload, "template_id": f"new-{len(self.created)}"})
        return payload

    def update_template(self, template_id, payload):
        self.updated.append((template_id, payload))
        return payload

    def delete_template(self, template_id):
        self.deleted.append(template_id)


def sample() -> tuple[ImageTemplate, ...]:
    return (
        ImageTemplate(name="Metropolis One", image="metropolis/one:dev"),
        ImageTemplate(name="Metropolis Two", image="metropolis/two:dev"),
    )


def test_register_creates_every_missing_template():
    client = FakeClient()
    result = register_templates(client, sample())

    assert result.created == ["Metropolis One", "Metropolis Two"]
    assert not result.updated
    assert [item["image"] for item in client.created] == [
        "metropolis/one:dev",
        "metropolis/two:dev",
    ]


def test_register_is_idempotent_on_a_second_run():
    """Rerunning must not add duplicates, unlike Gotham's create_templates.py."""
    client = FakeClient()
    register_templates(client, sample())
    result = register_templates(client, sample())

    assert result.created == []
    assert result.updated == []
    assert result.unchanged == ["Metropolis One", "Metropolis Two"]


def test_register_updates_only_the_fields_that_drifted():
    existing = [
        {
            "name": "Metropolis One",
            "template_id": "t-one",
            "image": "old/image:dev",
            "adapters": 9,
            "console_type": "none",
        }
    ]
    client = FakeClient(existing)
    result = register_templates(client, sample()[:1])

    assert result.updated == ["Metropolis One"]
    template_id, changes = client.updated[0]
    assert template_id == "t-one"
    assert changes == {"adapters": 1, "image": "metropolis/one:dev"}
    # GNS3 manages other keys, so an update must not resend the whole body.
    assert "template_id" not in changes


def test_register_dry_run_touches_nothing():
    client = FakeClient()
    result = register_templates(client, sample(), dry_run=True)

    assert result.created == ["Metropolis One", "Metropolis Two"]
    assert client.created == []
    assert client.updated == []


def test_prune_removes_only_unmanaged_metropolis_templates():
    existing = [
        {"name": "Metropolis Stale", "template_id": "t-stale"},
        {"name": "VyOS 1.3.0", "template_id": "t-vyos"},
        {"name": "Someone else's router", "template_id": "t-other"},
    ]
    client = FakeClient(existing)
    result = register_templates(client, sample(), prune=True)

    assert result.pruned == ["Metropolis Stale"]
    assert client.deleted == ["t-stale"]


def make_config(root: Path, version: str, body: str) -> Path:
    directory = root / version
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "gns3_server.conf"
    path.write_text(body, encoding="utf-8")
    return path


def test_resolve_server_raises_a_helpful_error_when_nothing_is_configured(tmp_path):
    with pytest.raises(FileNotFoundError) as caught:
        resolve_server(tmp_path, env={})
    assert ENV_HOST in str(caught.value)


def test_resolve_server_reads_the_newest_config(tmp_path):
    make_config(tmp_path, "2.2", "[Server]\nhost = 10.0.0.1\nport = 3080\n")
    server = resolve_server(tmp_path, env={})

    assert (server.host, server.port) == ("10.0.0.1", 3080)
    assert server.username is None


def test_environment_overrides_the_config_file(tmp_path):
    make_config(
        tmp_path,
        "2.2",
        "[Server]\nhost = 10.0.0.1\nport = 3080\nuser = from-file\npassword = file\n",
    )
    server = resolve_server(
        tmp_path,
        env={
            ENV_HOST: "192.168.1.5",
            ENV_PORT: "3081",
            ENV_USER: "from-env",
            ENV_PASSWORD: "secret",
        },
    )

    assert (server.host, server.port) == ("192.168.1.5", 3081)
    assert (server.username, server.password) == ("from-env", "secret")


def test_environment_alone_is_enough(tmp_path):
    """A remote server should not require a local GNS3 install."""
    server = resolve_server(tmp_path, env={ENV_HOST: "gns3.lab", ENV_PORT: "3080"})
    assert (server.host, server.port) == ("gns3.lab", 3080)
