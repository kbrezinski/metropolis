"""Versioned, address-free attack scenario metadata."""

from __future__ import annotations

import json
from pathlib import Path

CATALOG = Path(__file__).resolve().parent / "scenarios/v1.json"
ORIGINS = {"invoking_host", "mixed", "external_script", "gns3_node"}


def catalog(path: Path = CATALOG) -> dict[str, dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != "1.0" or not isinstance(
        document.get("scenarios"), list
    ):
        raise ValueError(f"Invalid attack scenario catalog: {path}")
    found: dict[str, dict] = {}
    ids: set[str] = set()
    for item in document["scenarios"]:
        if (
            not isinstance(item, dict)
            or not all(
                isinstance(item.get(key), str) and item[key]
                for key in ("id", "family", "tool", "origin_kind", "capture_focus")
            )
            or not item["id"].endswith(".v1")
            or item["origin_kind"] not in ORIGINS
            or item["id"] in ids
            or item["tool"] in found
        ):
            raise ValueError(f"Invalid or duplicate attack scenario in {path}")
        ids.add(item["id"])
        found[item["tool"]] = item
    return found


def for_tool(tool: str) -> dict | None:
    return catalog().get(tool)
