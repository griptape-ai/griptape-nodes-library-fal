"""What the fal nodes raise beyond what `NodeError` builds itself.

A missing key, an HTTP failure and a failed request come from `NodeError.from_missing_secret`,
`NodeError.from_http_response` and `NodeError.from_request_exception`. This module covers a queue
status that reports the generation failed.
"""

from __future__ import annotations

from typing import Any

from griptape_nodes.exe_types.core_types import NodeError

__all__ = ["API_KEY_NAME", "API_KEY_URL", "PROVIDER", "failed_status_error"]

API_KEY_NAME = "FAL_KEY"
API_KEY_URL = "https://fal.ai/dashboard/keys"
PROVIDER = "fal"


def failed_status_error(status: str, status_json: dict[str, Any], *, request_id: str) -> NodeError:
    """The error for a queue status of FAILED or ERROR."""
    reason = status_json.get("error") or status_json.get("detail")
    message = str(reason) if reason else "fal reported that the generation failed and gave no reason."
    fields = {"status": status, "generation_id": request_id, "error_code": status_json.get("error_type")}
    return NodeError(message, fields={k: v for k, v in fields.items() if v}, response=status_json)
