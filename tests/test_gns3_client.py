"""Checks for the GNS3 controller client and its HTTP transport.

No GNS3 server is needed: a fake transport records the calls the client makes,
and the transport's own `_send` seam is replaced to exercise status handling.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
from base64 import b64encode
from pathlib import Path

import pytest

GNS3 = Path(__file__).resolve().parents[1] / "scripts" / "gns3"
if str(GNS3) not in sys.path:
    sys.path.insert(0, str(GNS3))

from _transport import Gns3Error, Server, Transport  # noqa: E402
from client import Gns3Client  # noqa: E402


class FakeTransport:
    """Records each call and replays a queued response."""

    def __init__(self, responses=None):
        self.server = Server("gns3.test", 3080)
        self.calls: list[tuple[str, str, dict | None]] = []
        self.responses = list(responses or [])

    def request(self, method, path, payload=None, **kwargs):
        self.calls.append((method, path, payload))
        if self.responses:
            return self.responses.pop(0)
        return []


def client_with(responses=None) -> tuple[Gns3Client, FakeTransport]:
    transport = FakeTransport(responses)
    return Gns3Client(transport.server, transport=transport), transport


def test_version_and_reachability():
    client, transport = client_with([{"version": "2.2.43"}, {"version": "2.2.43"}])
    assert client.version() == "2.2.43"
    assert transport.calls == [("GET", "/version", None)]
    assert client.reachable() is True
    assert transport.calls[-1] == ("GET", "/version", None)


def test_reachable_is_false_when_the_server_refuses():
    class Unreachable(FakeTransport):
        def request(self, *args, **kwargs):
            raise Gns3Error("connection refused")

    client = Gns3Client(Server("gns3.test", 3080), transport=Unreachable())
    assert client.reachable() is False


def test_ensure_project_creates_only_when_missing():
    client, transport = client_with(
        [[], {"project_id": "p1", "name": "lab", "status": "opened"}]
    )
    project, created = client.ensure_project("lab")
    assert created is True
    assert project.project_id == "p1"
    assert transport.calls[0] == ("GET", "/projects", None)
    assert transport.calls[1] == ("POST", "/projects", {"name": "lab"})


def test_ensure_project_reuses_and_opens_an_existing_project():
    existing = [{"project_id": "p9", "name": "lab", "status": "closed"}]
    client, transport = client_with([existing, None])
    project, created = client.ensure_project("lab")

    assert created is False
    assert project.status == "opened"
    assert transport.calls[1] == ("POST", "/projects/p9/open", None)


def test_docker_template_payload_shape():
    client, transport = client_with([{"template_id": "t1", "name": "Metropolis PLC"}])
    client.create_docker_template(
        "Metropolis PLC", "metropolis/controller:dev", description="a controller"
    )
    method, path, payload = transport.calls[0]

    assert (method, path) == ("POST", "/templates")
    assert payload["template_type"] == "docker"
    assert payload["image"] == "metropolis/controller:dev"
    assert payload["adapters"] == 1
    assert payload["console_type"] == "none"
    # An empty environment string is meaningful: the node supplies NODE_* later.
    assert payload["environment"] == ""


def test_create_node_posts_to_the_template_path():
    client, transport = client_with([{"node_id": "n1", "name": "MET-PLC-INTAKE-01"}])
    node = client.create_node("p1", "t1", name="MET-PLC-INTAKE-01", x=10, y=20)

    assert node.node_id == "n1"
    assert transport.calls[0] == (
        "POST",
        "/projects/p1/templates/t1",
        {"x": 10, "y": 20, "compute_id": "local", "name": "MET-PLC-INTAKE-01"},
    )


def test_create_link_sends_both_endpoints():
    client, transport = client_with([{"link_id": "l1"}])
    client.create_link("p1", ("n1", 0, 0), ("n2", 3, 0))

    _, path, payload = transport.calls[0]
    assert path == "/projects/p1/links"
    assert payload["nodes"] == [
        {"node_id": "n1", "adapter_number": 0, "port_number": 0},
        {"node_id": "n2", "adapter_number": 3, "port_number": 0},
    ]


def test_node_returns_none_for_an_unknown_id():
    class NotFound(FakeTransport):
        def request(self, method, path, payload=None, **kwargs):
            return None

    client = Gns3Client(Server("gns3.test", 3080), transport=NotFound())
    assert client.node("p1", "missing") is None


def test_capture_endpoints_and_payload_match_the_api():
    """Start and stop capture are their own endpoints, not a capture sub-resource.

    GNS3 exposes POST .../links/{id}/start_capture and .../stop_capture, and
    accepts only data_link_type; capture_file_name is a read-only property.
    """
    client, transport = client_with([{"link_id": "L1"}, {"link_id": "L1"}])

    client.start_capture("p1", "L1")
    assert transport.calls[0] == (
        "POST",
        "/projects/p1/links/L1/start_capture",
        {"data_link_type": "DLT_EN10MB"},
    )

    client.stop_capture("p1", "L1")
    assert transport.calls[1] == ("POST", "/projects/p1/links/L1/stop_capture", None)


def test_capture_payload_never_sends_a_read_only_field():
    client, transport = client_with([{}])
    client.start_capture("p1", "L1", data_link_type="DLT_EN10MB")

    payload = transport.calls[0][2]
    assert set(payload) == {"data_link_type"}


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_transport_sends_json_and_decodes_the_reply(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["method"] = request.get_method()
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["auth"] = request.get_header("Authorization")
        return FakeResponse(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    transport = Transport(Server("gns3.test", 3080, "admin", "secret"))

    assert transport.request("POST", "/projects", {"name": "lab"}) == {"ok": True}
    assert captured["method"] == "POST"
    assert captured["url"] == "http://gns3.test:3080/v2/projects"
    assert json.loads(captured["body"]) == {"name": "lab"}
    expected = b64encode(b"admin:secret").decode()
    assert captured["auth"] == f"Basic {expected}"


def test_transport_omits_auth_when_no_username_is_configured(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["auth"] = request.get_header("Authorization")
        return FakeResponse(b"[]")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    Transport(Server("gns3.test", 3080)).request("GET", "/templates")
    assert captured["auth"] is None


def test_transport_raises_with_the_server_message(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url,
            404,
            "Not Found",
            {},
            io.BytesIO(b'{"message":"no such project"}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    transport = Transport(Server("gns3.test", 3080))

    with pytest.raises(Gns3Error) as caught:
        transport.request("GET", "/projects/nope")
    assert "404" in str(caught.value)
    assert "no such project" in str(caught.value)


def test_transport_reports_an_unreachable_server(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(Gns3Error) as caught:
        Transport(Server("gns3.test", 3080)).request("GET", "/version")
    assert "Could not reach" in str(caught.value)


def test_transport_accepts_an_empty_body(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None: FakeResponse(b"", 204)
    )
    assert (
        Transport(Server("gns3.test", 3080)).request("DELETE", "/templates/t1") is None
    )
