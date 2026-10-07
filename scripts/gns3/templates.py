"""GNS3 Docker templates for the Metropolis device images.

Each built image needs a GNS3 template before a node can be created from it.
Rather than hand-creating templates in the GUI, the images are listed here and
registered with one command.

The key is the Docker image reference exactly as built (see
``testbeds/metropolis/devices/README.md``); the values describe the template
GNS3 should hold for it. ``console_type: none`` is right for these images
because their entrypoint starts the application directly rather than a shell or
console server.

This replaces Gotham's approach of deriving templates by parsing a Makefile and
reading each target's Python ``config`` dictionary.
"""

from __future__ import annotations

from dataclasses import dataclass

from client import Gns3Client

# Adapt every modelled node with a single interface; the entrypoint configures
# one interface and the models default to eth0.
DEFAULT_ADAPTERS = 1


@dataclass(frozen=True)
class ImageTemplate:
    """How one Docker image should appear as a GNS3 template."""

    name: str
    image: str
    adapters: int = DEFAULT_ADAPTERS
    console_type: str = "none"
    description: str = ""

    def payload(self) -> dict:
        """The GNS3 template creation body."""
        return {
            "name": self.name,
            "template_type": "docker",
            "image": self.image,
            "adapters": self.adapters,
            "console_type": self.console_type,
            "environment": "",
            "description": self.description,
            "category": "guest",
        }


def spec(name: str, image: str, **overrides) -> ImageTemplate:
    return ImageTemplate(name=name, image=image, **overrides)


# Every image built from testbeds/metropolis/devices/. Keep this list in step
# with the build commands in that directory's README; register_templates.py
# reports images that are inventoried but missing here.
TEMPLATES: tuple[ImageTemplate, ...] = (
    spec(
        "Metropolis PLC/RTU",
        "metropolis/controller:dev",
        description="Modbus TCP controller; PLC or RTU role by DEVICE_ROLE",
    ),
    spec(
        "Metropolis MQTT/CoAP sensor",
        "metropolis/mqtt-sensor:dev",
        description="MQTT telemetry publisher with CoAP discovery and status",
    ),
    spec(
        "Metropolis MQTT broker",
        "metropolis/mqtt-broker:dev",
        description="Authenticated Mosquitto broker on TCP 1883",
    ),
    spec(
        "Metropolis SCADA",
        "metropolis/scada:dev",
        description="Modbus poller that republishes status over MQTT",
    ),
    spec(
        "Metropolis HMI",
        "metropolis/hmi:dev",
        description="Authenticated HTTP operator panel on TCP 8080",
    ),
    spec(
        "Metropolis historian",
        "metropolis/historian:dev",
        description="MQTT subscriber that logs received events",
    ),
    spec(
        "Metropolis engineering workstation",
        "metropolis/engineering:dev",
        description="OpenSSH shell and SFTP on TCP 22",
    ),
    spec(
        "Metropolis control client",
        "metropolis/control-client:dev",
        description="Routine Modbus read/write cycle with DNS and NTP sampling",
    ),
    spec(
        "Metropolis legacy gateway",
        "metropolis/legacy-gateway:dev",
        description="BusyBox Telnet host on TCP 23",
    ),
    spec(
        "Metropolis DNS",
        "metropolis/dns:dev",
        description="Local DNS records on UDP/TCP 53",
    ),
    spec(
        "Metropolis NTP",
        "metropolis/ntp:dev",
        description="Local time service on UDP 123",
    ),
    spec(
        "Metropolis reachability probe",
        "metropolis/reachability-probe:dev",
        description="Minimal reachable host with no service; a routing positive control",
    ),
    spec(
        "Metropolis Mirai CNC",
        "metropolis/mirai-cnc:dev",
        description="Synthetic bot registry and operator console",
    ),
    spec(
        "Metropolis Mirai scan listener",
        "metropolis/mirai-scan-listener:dev",
        description="Synthetic bot report collector",
    ),
    spec(
        "Metropolis Mirai loader",
        "metropolis/mirai-loader:dev",
        description="Synthetic TFTP payload-delivery role",
    ),
    spec(
        "Metropolis Mirai wget loader",
        "metropolis/mirai-wget-loader:dev",
        description="Synthetic HTTP payload-delivery role",
    ),
    spec(
        "Metropolis Mirai bot",
        "metropolis/mirai-bot:dev",
        description="Inert bot agent",
    ),
    spec(
        "Metropolis Merlin CNC",
        "metropolis/merlin-cnc:dev",
        description="Synthetic C2 console and agent channel",
    ),
    spec(
        "Metropolis Merlin agent",
        "metropolis/merlin-agent:dev",
        description="Inert C2 agent",
    ),
)

# Routers and switches are appliances, not images, so they are registered once
# in the GNS3 controller rather than created here.
APPLIANCES = {
    "router": "VyOS 1.3.0",
    "switch": "Open vSwitch",
}


@dataclass
class TemplateResult:
    """What a registration run did."""

    created: list[str]
    updated: list[str]
    unchanged: list[str]
    pruned: list[str]

    @property
    def changed(self) -> int:
        return len(self.created) + len(self.updated) + len(self.pruned)


# Fields this code owns. GNS3 supplies the rest (ids, compute, ports), so an
# update must not resend the whole template.
MANAGED_FIELDS = (
    "name",
    "image",
    "adapters",
    "console_type",
    "environment",
    "description",
)


def _differs(existing: dict, desired: dict) -> dict:
    """Managed fields where an existing template disagrees with the desired one.

    A field the server did not return is left alone rather than reported as a
    difference, so a template that already matches stays untouched on a rerun.
    """
    return {
        key: desired[key]
        for key in MANAGED_FIELDS
        if key in existing and existing[key] != desired[key]
    }


def register_templates(
    client: Gns3Client,
    templates: tuple[ImageTemplate, ...] = TEMPLATES,
    *,
    prune: bool = False,
    dry_run: bool = False,
) -> TemplateResult:
    """Create or update a template per image, optionally pruning strays.

    Idempotent: a second run with unchanged images updates nothing, unlike
    Gotham's create_templates.py, which added a duplicate template per rerun.
    """
    created: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    pruned: list[str] = []

    existing = {item["name"]: item for item in client.templates()}
    managed = {item.name for item in templates}

    for template in templates:
        current = existing.get(template.name)
        if current is None:
            created.append(template.name)
            if not dry_run:
                client.create_template(template.payload())
            continue
        changes = _differs(current, template.payload())
        if changes:
            updated.append(template.name)
            if not dry_run:
                client.update_template(current["template_id"], changes)
        else:
            unchanged.append(template.name)

    if prune:
        for name, item in existing.items():
            if name in managed or not name.startswith("Metropolis "):
                continue
            pruned.append(name)
            if not dry_run:
                client.delete_template(item["template_id"])

    return TemplateResult(
        created=created, updated=updated, unchanged=unchanged, pruned=pruned
    )
