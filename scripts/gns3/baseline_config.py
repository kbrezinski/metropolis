"""Resolve one dataset's declared container resource baseline without a server."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import yaml

from run_profiles import ProfileLoader, _finite

SCHEMA = Path(__file__).resolve().parents[2] / "schemas/resource-baseline.schema.json"
BASELINE_FILE = Path("topology/resource-baseline.yaml")
SWITCH_IMAGE = "metropolis/openvswitch:dev"
SWITCH_MODEL = "testbeds/metropolis/devices/infrastructure/openvswitch"


def _settings(value: dict) -> None:
    for field in ("memory_bytes", "memory_swap_bytes"):
        if field in value and type(value[field]) is not int:
            raise ValueError(f"baseline {field} must be an integer")
    if "memory_bytes" in value and "memory_swap_bytes" in value:
        if (
            value["memory_swap_bytes"] != -1
            and value["memory_swap_bytes"] < value["memory_bytes"]
        ):
            raise ValueError(
                "baseline memory_swap_bytes must cover memory_bytes or be -1"
            )


def load_baseline(path: Path, inventory: dict, infrastructure: dict) -> dict:
    """Merge defaults, model overrides, then image overrides for declared nodes."""
    path = Path(path).resolve()
    raw = path.read_bytes()
    try:
        document = yaml.load(raw.decode("utf-8"), Loader=ProfileLoader)
    except (yaml.YAMLError, UnicodeError) as exc:
        raise ValueError("baseline must contain one valid YAML document") from exc
    _finite(document)
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        raise ValueError("baseline validation requires uv sync --group dev") from exc
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda error: str(error.path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].path) or "document"
        raise ValueError(f"baseline {location}: {errors[0].message}")
    overrides = document.get("overrides", {})
    models, images = overrides.get("models", {}), overrides.get("images", {})
    for value in (document["defaults"], *models.values(), *images.values()):
        _settings(value)
    devices = list(inventory.get("devices", []))
    devices.extend(
        {
            **node,
            "image": node.get("image", SWITCH_IMAGE),
            "model": node.get("model", SWITCH_MODEL),
        }
        for group in ("switches", "segments")
        for node in infrastructure.get(group, [])
    )
    routers = infrastructure.get("routers", [])
    names = Counter(node["name"] for node in [*devices, *routers])
    duplicate = sorted(name for name, count in names.items() if count != 1)
    if duplicate:
        raise ValueError("ambiguous baseline node names: " + ", ".join(duplicate))
    known_models = {node["model"] for node in devices if node.get("model")}
    known_images = {node["image"] for node in devices if node.get("image")}
    for label, selected, known in (
        ("model", models, known_models),
        ("image", images, known_images),
    ):
        unknown = sorted(selected.keys() - known)
        if unknown:
            raise ValueError(f"unknown baseline {label} selector: {', '.join(unknown)}")
    targets, unsupported = [], []
    for node in devices:
        if not node.get("image"):
            unsupported.append(
                {"name": node["name"], "reason": "no declared Docker image"}
            )
            continue
        settings = {
            **document["defaults"],
            **models.get(node.get("model"), {}),
            **images.get(node["image"], {}),
        }
        _settings(settings)
        target = {"name": node["name"], "image": node["image"], "settings": settings}
        if node.get("model"):
            target["model"] = node["model"]
        targets.append(target)
    unsupported.extend(
        {"name": node["name"], "reason": "router appliances are not Docker containers"}
        for node in routers
    )
    return {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "targets": targets,
        "unsupported": unsupported,
    }
