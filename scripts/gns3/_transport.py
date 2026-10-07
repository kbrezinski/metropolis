"""GNS3 server HTTP transport.

The GNS3 controller exposes a versioned JSON REST API. This module holds the
only code that knows about HTTP, authentication, and URL shape; everything
above it works with plain dictionaries.

It uses the standard library rather than a client library so the GNS3
automation adds no runtime dependency, and so tests can intercept a single
seam (`_send`) without a live server.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

# A local GNS3 server answers quickly. Anything slower means it is not
# listening, and a long default would just make a typo look like a hang.
DEFAULT_TIMEOUT = 10.0


class Gns3Error(RuntimeError):
    """The server refused a request or answered with an unexpected status."""


@dataclass(frozen=True)
class Server:
    """Where the GNS3 controller is listening and how to authenticate."""

    host: str
    port: int
    username: str | None = None
    password: str | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v2"


class Transport:
    """Issues JSON requests against one GNS3 server."""

    def __init__(self, server: Server, timeout: float = DEFAULT_TIMEOUT):
        self.server = server
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.server.username:
            credentials = f"{self.server.username}:{self.server.password or ''}"
            token = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        return headers

    def _send(self, request: urllib.request.Request) -> tuple[int, bytes]:
        """Perform one request and return (status, body).

        This is the seam tests replace; it never raises for an HTTP error
        status, so callers decide what a status means.
        """
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except urllib.error.URLError as exc:
            raise Gns3Error(
                f"Could not reach {self.server.base_url}: {exc.reason}"
            ) from exc

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        allow: tuple[int, ...] = (200, 201, 204),
    ) -> dict | list | None:
        """Call the API and return the decoded body.

        An unexpected status raises Gns3Error carrying the server's message, so
        a failure reports what GNS3 objected to rather than a bare stack trace.
        """
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.server.base_url}{path}",
            data=body,
            headers=self._headers(),
            method=method,
        )
        status, raw = self._send(request)
        if status not in allow:
            detail = raw.decode("utf-8", errors="replace").strip()
            raise Gns3Error(f"{method} {path} returned {status}: {detail}")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise Gns3Error(f"{method} {path} returned invalid JSON: {exc}") from exc
