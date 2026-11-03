"""Snapshot fal.ai model schemas into griptape_nodes_library_fal/schemas/<category>.json.

The generic category nodes read these files at load time, so the library works
offline and does not depend on fal's schema endpoints at runtime. Re-run this
script to pick up new models or schema changes.

Usage:
    python scripts/fetch_fal_schemas.py text-to-image image-to-video
    python scripts/fetch_fal_schemas.py --all

Existing entries are kept, so an interrupted run can be resumed. Pass --refresh
to re-fetch models that are already in a snapshot.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

MODELS_URL = "https://api.fal.ai/v1/models"
OPENAPI_URL = "https://fal.ai/api/openapi/queue/openapi.json?endpoint_id={endpoint_id}"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "griptape_nodes_library_fal" / "schemas"
REQUEST_DELAY_S = 0.5
MAX_RETRIES = 6
MAX_REF_DEPTH = 6


def http_get_json(url: str) -> dict[str, Any]:
    """GET a JSON document, backing off when fal returns 429."""
    for attempt in range(MAX_RETRIES):
        request = urllib.request.Request(url, headers={"User-Agent": "griptape-nodes-library-fal"})  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                return json.load(response)
        except urllib.error.HTTPError as e:
            if e.code != 429:  # noqa: PLR2004
                raise
            time.sleep(2 * 2**attempt)
    msg = f"Rate limited fetching {url}"
    raise RuntimeError(msg)


def list_models() -> list[dict[str, Any]]:
    """Return every active endpoint from the fal model catalog."""
    models: list[dict[str, Any]] = []
    cursor = None
    while True:
        url = f"{MODELS_URL}?limit=100" + (f"&cursor={cursor}" if cursor else "")
        page = http_get_json(url)
        models.extend(m for m in page["models"] if m["metadata"].get("status") == "active")
        cursor = page.get("next_cursor")
        if not page.get("has_more") or not cursor:
            return models
        time.sleep(REQUEST_DELAY_S)


def resolve_refs(node: Any, components: dict[str, Any], depth: int = 0) -> Any:
    """Inline $ref pointers into components/schemas, up to MAX_REF_DEPTH levels."""
    if isinstance(node, list):
        return [resolve_refs(item, components, depth) for item in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        if depth >= MAX_REF_DEPTH:
            return {"type": "object"}
        target = components.get(ref.rsplit("/", 1)[-1], {})
        return resolve_refs(target, components, depth + 1)
    # Sample prompts and outputs are large and the nodes never read them.
    return {key: resolve_refs(value, components, depth) for key, value in node.items() if key != "examples"}


def extract_entry(model: dict[str, Any], openapi: dict[str, Any]) -> dict[str, Any]:
    """Reduce an OpenAPI document to what the nodes read: the display name, description, and input schema.

    The output schema and queue paths are left out on purpose. The nodes parse results by file type, and
    fal serves status under the app id, which the OpenAPI paths do not always show.
    """
    endpoint_id = model["endpoint_id"]
    components = openapi["components"]["schemas"]
    post = openapi["paths"][f"/{endpoint_id}"]["post"]
    input_ref = post["requestBody"]["content"]["application/json"]["schema"]
    metadata = model["metadata"]
    return {
        "endpoint_id": endpoint_id,
        "display_name": metadata.get("display_name", endpoint_id),
        "description": metadata.get("description", ""),
        "input": resolve_refs(input_ref, components),
    }


def snapshot_category(category: str, models: list[dict[str, Any]], *, refresh: bool) -> None:
    path = OUTPUT_DIR / f"{category}.json"
    existing: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
    entries: dict[str, Any] = existing.get("models", {})

    todo = [m for m in models if refresh or m["endpoint_id"] not in entries]
    print(f"{category}: {len(models)} models, {len(todo)} to fetch")
    for index, model in enumerate(todo, start=1):
        endpoint_id = model["endpoint_id"]
        try:
            openapi = http_get_json(OPENAPI_URL.format(endpoint_id=endpoint_id))
            entries[endpoint_id] = extract_entry(model, openapi)
        except Exception as e:  # noqa: BLE001
            print(f"  skip {endpoint_id}: {type(e).__name__}: {e}", file=sys.stderr)
        if index % 25 == 0:
            write_snapshot(path, category, entries)
            print(f"  {index}/{len(todo)}")
        time.sleep(REQUEST_DELAY_S)

    # Drop models that fal no longer lists as active.
    active_ids = {m["endpoint_id"] for m in models}
    entries = {k: v for k, v in entries.items() if k in active_ids}
    write_snapshot(path, category, entries)


def write_snapshot(path: Path, category: str, entries: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = dict(sorted(entries.items(), key=lambda kv: kv[1]["display_name"].lower()))
    path.write_text(json.dumps({"category": category, "models": ordered}, indent=1) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("categories", nargs="*", help="fal category slugs, e.g. text-to-image")
    parser.add_argument("--all", action="store_true", help="snapshot every category")
    parser.add_argument("--refresh", action="store_true", help="re-fetch models already in a snapshot")
    args = parser.parse_args()

    if not args.categories and not args.all:
        parser.error("pass category names or --all")

    by_category: dict[str, list[dict[str, Any]]] = {}
    for model in list_models():
        by_category.setdefault(model["metadata"]["category"], []).append(model)

    wanted = sorted(by_category) if args.all else args.categories
    unknown = [c for c in wanted if c not in by_category]
    if unknown:
        parser.error(f"unknown categories: {', '.join(unknown)}. Available: {', '.join(sorted(by_category))}")

    for category in wanted:
        snapshot_category(category, by_category[category], refresh=args.refresh)


if __name__ == "__main__":
    main()
