"""Thin GNS3 controller client.

One method per API operation the topology builder needs. Nothing here decides
what to build or in which order; it only translates a call into a request and
returns the server's answer, so the builder and the template registration can be
read on their own.

Paths and payloads follow the GNS3 v2 controller API.
"""

from __future__ import annotations

from dataclasses import dataclass

from _transport import Gns3Error, Server, Transport

# GNS3 refuses a request for an unknown id with 404, which is a normal answer
# when asking whether something exists.
_NOT_FOUND = (404,)


@dataclass(frozen=True)
class Project:
    """A GNS3 project."""

    project_id: str
    name: str
    status: str

    @classmethod
    def from_api(cls, payload: dict) -> "Project":
        return cls(
            project_id=payload["project_id"],
            name=payload["name"],
            status=payload.get("status", "closed"),
        )


@dataclass(frozen=True)
class Node:
    """A node inside a project."""

    node_id: str
    name: str
    status: str

    @classmethod
    def from_api(cls, payload: dict) -> "Node":
        return cls(
            node_id=payload["node_id"],
            name=payload["name"],
            status=payload.get("status", "stopped"),
        )


class Gns3Client:
    """Operations against one GNS3 server."""

    def __init__(self, server: Server, transport: Transport | None = None):
        self.server = server
        self.transport = transport or Transport(server)

    # -- server -----------------------------------------------------------

    def version(self) -> str:
        """The controller's version string."""
        payload = self.transport.request("GET", "/version")
        return payload["version"]

    def reachable(self) -> bool:
        """True when the controller answers, rather than raising."""
        try:
            self.version()
        except Gns3Error:
            return False
        return True

    # -- projects ---------------------------------------------------------

    def projects(self) -> list[Project]:
        payload = self.transport.request("GET", "/projects") or []
        return [Project.from_api(item) for item in payload]

    def project_by_name(self, name: str) -> Project | None:
        return next((item for item in self.projects() if item.name == name), None)

    def create_project(self, name: str) -> Project:
        payload = self.transport.request("POST", "/projects", {"name": name})
        return Project.from_api(payload)

    def open_project(self, project_id: str) -> None:
        self.transport.request("POST", f"/projects/{project_id}/open")

    def ensure_project(self, name: str) -> tuple[Project, bool]:
        """Return (project, created). Opens an existing project if needed."""
        project = self.project_by_name(name)
        if project is None:
            return self.create_project(name), True
        if project.status != "opened":
            self.open_project(project.project_id)
            project = Project(project.project_id, project.name, "opened")
        return project, False

    def project_nodes(self, project_id: str) -> list[dict]:
        payload = self.transport.request("GET", f"/projects/{project_id}/nodes") or []
        return list(payload)

    def project_links(self, project_id: str) -> list[dict]:
        payload = self.transport.request("GET", f"/projects/{project_id}/links") or []
        return list(payload)

    # -- templates --------------------------------------------------------

    def templates(self) -> list[dict]:
        payload = self.transport.request("GET", "/templates") or []
        return list(payload)

    def template_by_name(self, name: str) -> dict | None:
        return next((item for item in self.templates() if item["name"] == name), None)

    def create_template(self, payload: dict) -> dict:
        return self.transport.request("POST", "/templates", payload)

    def update_template(self, template_id: str, payload: dict) -> dict:
        return self.transport.request("PUT", f"/templates/{template_id}", payload)

    def delete_template(self, template_id: str) -> None:
        self.transport.request("DELETE", f"/templates/{template_id}", allow=(200, 204))

    def create_docker_template(
        self,
        name: str,
        image: str,
        *,
        adapters: int = 1,
        console_type: str = "none",
        environment: str = "",
        start_command: str | None = None,
        description: str = "",
    ) -> dict:
        """Register one Docker image as a reusable GNS3 template."""
        payload: dict = {
            "name": name,
            "template_type": "docker",
            "image": image,
            "adapters": adapters,
            "console_type": console_type,
            "environment": environment,
            "description": description,
            "category": "guest",
        }
        if start_command:
            payload["start_command"] = start_command
        return self.create_template(payload)

    # -- nodes ------------------------------------------------------------

    def create_node(
        self,
        project_id: str,
        template_id: str,
        *,
        x: int = 0,
        y: int = 0,
        name: str | None = None,
        compute_id: str = "local",
    ) -> Node:
        payload: dict = {
            "x": x,
            "y": y,
            "compute_id": compute_id,
        }
        if name:
            payload["name"] = name
        created = self.transport.request(
            "POST", f"/projects/{project_id}/templates/{template_id}", payload
        )
        return Node.from_api(created)

    def start_node(self, project_id: str, node_id: str) -> None:
        self.transport.request("POST", f"/projects/{project_id}/nodes/{node_id}/start")

    def stop_node(self, project_id: str, node_id: str) -> None:
        self.transport.request("POST", f"/projects/{project_id}/nodes/{node_id}/stop")

    def delete_node(self, project_id: str, node_id: str) -> None:
        self.transport.request(
            "DELETE", f"/projects/{project_id}/nodes/{node_id}", allow=(200, 204)
        )

    def node(self, project_id: str, node_id: str) -> dict | None:
        """One node's raw record, or None when the id is unknown."""
        return self.transport.request(
            "GET",
            f"/projects/{project_id}/nodes/{node_id}",
            allow=(200, *_NOT_FOUND),
        )

    def set_node_environment(
        self, project_id: str, node_id: str, environment: str
    ) -> dict:
        """Replace a Docker node's environment string."""
        return self.transport.request(
            "PUT",
            f"/projects/{project_id}/nodes/{node_id}",
            {"properties": {"environment": environment}},
        )

    # -- links ------------------------------------------------------------

    def create_link(
        self,
        project_id: str,
        first: tuple[str, int, int],
        second: tuple[str, int, int],
    ) -> dict:
        """Connect two node ports.

        Each endpoint is (node_id, adapter_number, port_number). Docker nodes
        use adapter 0; a switch uses its port number as the adapter.
        """
        payload = {
            "nodes": [
                {
                    "node_id": first[0],
                    "adapter_number": first[1],
                    "port_number": first[2],
                },
                {
                    "node_id": second[0],
                    "adapter_number": second[1],
                    "port_number": second[2],
                },
            ]
        }
        return self.transport.request("POST", f"/projects/{project_id}/links", payload)

    # -- captures ---------------------------------------------------------

    def start_capture(
        self, project_id: str, link_id: str, *, data_link_type: str = "DLT_EN10MB"
    ) -> dict:
        """Begin capturing packets on a link.

        GNS3 names the capture file itself; ``capture_file_name`` and
        ``capture_file_path`` are read-only properties of the link.
        """
        return self.transport.request(
            "POST",
            f"/projects/{project_id}/links/{link_id}/start_capture",
            {"data_link_type": data_link_type},
        )

    def stop_capture(self, project_id: str, link_id: str) -> dict:
        """Stop capturing on a link and return its record."""
        return self.transport.request(
            "POST", f"/projects/{project_id}/links/{link_id}/stop_capture"
        )

    def link(self, project_id: str, link_id: str) -> dict | None:
        """One link's raw record, which carries its capture state."""
        return self.transport.request(
            "GET",
            f"/projects/{project_id}/links/{link_id}",
            allow=(200, *_NOT_FOUND),
        )
