"""Load explicitly selected resource/link profiles without contacting a server."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

import yaml

SCHEMA = Path(__file__).resolve().parents[2] / "schemas/run-profile.schema.json"
INTEGER_FIELDS = {
    "memory_bytes",
    "bandwidth_kbps",
    "delay_ms",
    "jitter_ms",
    "loss_percent",
}


class ProfileLoader(yaml.SafeLoader):
    """Reject duplicate keys instead of silently replacing an earlier setting."""


def _mapping(loader: ProfileLoader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ValueError("profile mapping keys must be strings")
        if key in result:
            raise ValueError(f"duplicate profile key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


ProfileLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _finite(value, ancestors=frozenset()):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("profile numeric settings must be finite")
    if isinstance(value, (dict, list)):
        if id(value) in ancestors:
            raise ValueError("recursive profile values are unsupported")
        ancestors = ancestors | {id(value)}
        for item in value.values() if isinstance(value, dict) else value:
            _finite(item, ancestors)


def load_profile(
    path: Path, inventory: dict, infrastructure: dict, link_plan: dict
) -> dict:
    """Validate one profile and resolve its names against the selected dataset."""
    try:
        document = yaml.load(
            Path(path).read_text(encoding="utf-8"), Loader=ProfileLoader
        )
    except yaml.YAMLError as exc:
        raise ValueError("profile must contain one valid YAML document") from exc
    _finite(document)
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        raise ValueError("profile validation requires uv sync --group dev") from exc
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda item: str(item.path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.path) or "document"
        raise ValueError(f"profile {location}: {error.message}")
    devices = document.setdefault("devices", [])
    links = document.setdefault("links", [])
    for entry in [*devices, *links]:
        for key in INTEGER_FIELDS & entry.keys():
            if type(entry[key]) is not int:
                raise ValueError(f"profile {key} must be an integer")
    node_names = Counter(
        item["name"]
        for group in (
            inventory.get("devices", []),
            infrastructure.get("switches", []),
            infrastructure.get("segments", []),
        )
        for item in group
    )
    declared_links = Counter(item["id"] for item in link_plan.get("links", []))
    logical_links = {
        item["id"]
        for item in link_plan.get("links", [])
        if item.get("kind") == "overlay"
    }
    for entries, key, declared in (
        (devices, "node", node_names),
        (links, "link", declared_links),
    ):
        selected = Counter(entry[key] for entry in entries)
        for name, count in selected.items():
            if count != 1:
                raise ValueError(f"duplicate profile {key} target: {name}")
            if declared[name] == 0:
                raise ValueError(f"unknown declared profile {key}: {name}")
            if declared[name] != 1:
                raise ValueError(f"ambiguous declared profile {key}: {name}")
            if key == "link" and name in logical_links:
                raise ValueError(
                    f"profile link {name} is a logical overlay, not a physical GNS3 link"
                )
    return document
