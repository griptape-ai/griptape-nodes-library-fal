"""Turn fal OpenAPI input schemas into flat parameter specs.

This module has no engine imports so it can be tested without a Griptape Nodes install.
The snapshots it reads are produced by scripts/fetch_fal_schemas.py.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

SCHEMAS_DIR = Path(__file__).parent / "schemas"

# Field kinds a spec can have. Media kinds are uploaded to a public URL before submission.
KIND_STRING = "string"
KIND_TEXT = "text"
KIND_INT = "int"
KIND_FLOAT = "float"
KIND_BOOL = "bool"
KIND_ENUM = "enum"
KIND_JSON = "json"
KIND_IMAGE = "image"
KIND_VIDEO = "video"
KIND_AUDIO = "audio"
KIND_IMAGE_LIST = "image_list"
KIND_VIDEO_LIST = "video_list"
KIND_AUDIO_LIST = "audio_list"

MEDIA_KINDS = (KIND_IMAGE, KIND_VIDEO, KIND_AUDIO)
MEDIA_LIST_KINDS = (KIND_IMAGE_LIST, KIND_VIDEO_LIST, KIND_AUDIO_LIST)

_MEDIA_NAME_PATTERNS = (
    (re.compile(r"(^|_)image_urls$"), KIND_IMAGE_LIST),
    (re.compile(r"(^|_)video_urls$"), KIND_VIDEO_LIST),
    (re.compile(r"(^|_)audio_urls$"), KIND_AUDIO_LIST),
    (re.compile(r"(^|_)(image|mask)_url$"), KIND_IMAGE),
    (re.compile(r"(^|_)video_url$"), KIND_VIDEO),
    (re.compile(r"(^|_)audio_url$"), KIND_AUDIO),
)
_TEXT_NAME_HINTS = ("prompt", "text", "instruction", "script", "caption", "description")


@dataclass(frozen=True)
class FieldSpec:
    """One input field of a fal model, reduced to what a node parameter needs."""

    name: str
    kind: str
    description: str = ""
    required: bool = False
    default: Any = None
    choices: tuple[Any, ...] = ()
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class ModelSpec:
    """A fal endpoint with its parsed input fields."""

    endpoint_id: str
    display_name: str
    description: str
    fields: tuple[FieldSpec, ...] = field(default_factory=tuple)


def _unwrap(prop: dict[str, Any]) -> dict[str, Any]:
    """Collapse anyOf/oneOf/allOf wrappers into a single concrete schema.

    Optional fields arrive as anyOf [X, null]. Fields that accept either a preset name or a
    custom object (such as image_size) collapse to the preset enum.
    """
    for key in ("anyOf", "oneOf", "allOf"):
        options = prop.get(key)
        if not options:
            continue
        concrete = [o for o in options if o.get("type") != "null"]
        if not concrete:
            return {**prop, "type": "string"}
        chosen = next((o for o in concrete if "enum" in o), concrete[0])
        merged = {k: v for k, v in prop.items() if k != key}
        merged.update(
            {k: v for k, v in _unwrap(chosen).items() if k not in ("description", "title") or k not in merged}
        )
        return merged
    return prop


def _media_kind_for_name(name: str) -> str | None:
    for pattern, kind in _MEDIA_NAME_PATTERNS:
        if pattern.search(name):
            return kind
    return None


def _classify(name: str, prop: dict[str, Any]) -> str:
    media_kind = _media_kind_for_name(name)
    prop_type = prop.get("type")

    if media_kind in MEDIA_LIST_KINDS and prop_type == "array":
        return media_kind
    if media_kind in MEDIA_KINDS and prop_type == "string":
        return media_kind
    if prop.get("enum") and prop_type in ("string", "integer", "number", None):
        return KIND_ENUM
    if prop_type == "boolean":
        return KIND_BOOL
    if prop_type == "integer":
        return KIND_INT
    if prop_type == "number":
        return KIND_FLOAT
    if prop_type == "string":
        is_text = any(hint in name for hint in _TEXT_NAME_HINTS) or prop.get("maxLength", 0) > 200  # noqa: PLR2004
        return KIND_TEXT if is_text else KIND_STRING
    return KIND_JSON


def parse_input_schema(schema: dict[str, Any]) -> tuple[FieldSpec, ...]:
    """Flatten a model's input schema into field specs, required fields first."""
    required = set(schema.get("required", []))
    specs: list[FieldSpec] = []
    for name, raw in schema.get("properties", {}).items():
        prop = _unwrap(raw)
        kind = _classify(name, prop)
        default = raw.get("default", prop.get("default"))
        choices = tuple(prop.get("enum", ())) if kind == KIND_ENUM else ()
        specs.append(
            FieldSpec(
                name=name,
                kind=kind,
                description=(raw.get("description") or prop.get("description") or "").strip(),
                required=name in required,
                default=default,
                choices=choices,
                minimum=prop.get("minimum"),
                maximum=prop.get("maximum"),
            )
        )
    specs.sort(key=lambda s: not s.required)
    return tuple(specs)


@cache
def load_category(category: str) -> dict[str, ModelSpec]:
    """Load a category snapshot as {endpoint_id: ModelSpec}, ordered by display name."""
    path = SCHEMAS_DIR / f"{category}.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {
        endpoint_id: ModelSpec(
            endpoint_id=endpoint_id,
            display_name=entry["display_name"],
            description=entry.get("description", ""),
            fields=parse_input_schema(entry["input"]),
        )
        for endpoint_id, entry in data["models"].items()
    }
