"""Build `NodeError`s from fal responses and failures.

fal reports errors as `{"detail": "..."}`, or as `{"detail": [{"loc": [...], "msg": "..."}]}` for
validation problems. The queue's status endpoint can also carry `error`. These helpers read the
reason out of those shapes so the message is fal's own words, and attach the status code, request ID
and response body to the `NodeError` instead of printing them into the text.
"""

from __future__ import annotations

from typing import Any

import httpx
from griptape_nodes.exe_types.core_types import NodeError, NodeErrorLink

__all__ = [
    "API_KEY_NAME",
    "FAL_KEYS_URL",
    "error_from_failed_status",
    "error_from_http_response",
    "error_from_request_exception",
    "missing_key_error",
]

API_KEY_NAME = "FAL_KEY"
FAL_KEYS_URL = "https://fal.ai/dashboard/keys"

HTTP_UNAUTHORIZED = 401
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500
REQUEST_ID_HEADER = "x-fal-request-id"
MAX_TEXT_RESPONSE_CHARS = 2000


def missing_key_error() -> NodeError:
    """The error for a node that runs without FAL_KEY, linking to Settings and to fal's key page."""
    return NodeError(
        f"{API_KEY_NAME} is not set. Add it in Settings → API Keys & Secrets, then run the node again.",
        links=[
            NodeErrorLink.from_secret(API_KEY_NAME),
            NodeErrorLink(label="Get a fal API key", url=FAL_KEYS_URL),
        ],
    )


def _reason_from_detail(detail: Any) -> str:
    if isinstance(detail, str):
        return detail.strip()
    if isinstance(detail, dict):
        return _reason_from_body(detail)
    if isinstance(detail, list):
        parts = [_reason_from_validation_item(item) for item in detail]
        return "; ".join(part for part in parts if part)
    return ""


def _reason_from_validation_item(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item).strip() if item else ""
    message = item.get("msg") or item.get("message") or ""
    location = item.get("loc")
    if isinstance(location, list) and location:
        # Locations look like ["body", "prompt"]. "body" only says where the field sits.
        field = ".".join(str(part) for part in location if part != "body")
        if field and message:
            return f"'{field}': {message}"
    return str(message).strip()


def _reason_from_body(body: Any) -> str:
    """The reason fal gave, or an empty string when the body has none."""
    if not isinstance(body, dict):
        return ""
    for key in ("detail", "error", "message"):
        value = body.get(key)
        if value:
            reason = _reason_from_detail(value)
            if reason:
                return reason
    return ""


def _json_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _response_payload(response: httpx.Response, body: Any) -> dict[str, Any] | None:
    if isinstance(body, dict):
        return body
    if body is not None:
        return {"body": body}
    text = (response.text or "").strip()
    return {"text": text[:MAX_TEXT_RESPONSE_CHARS]} if text else None


def _fields_from_response(response: httpx.Response, body: Any) -> dict[str, str | int | float | bool]:
    fields: dict[str, str | int | float | bool] = {"status_code": response.status_code}
    request_id = response.headers.get(REQUEST_ID_HEADER)
    if not request_id and isinstance(body, dict):
        request_id = body.get("request_id")
    if request_id:
        fields["request_id"] = str(request_id)
    return fields


def error_from_http_response(
    response: httpx.Response,
    *,
    fields: dict[str, str | int | float | bool] | None = None,
) -> NodeError:
    """The error for an HTTP response from fal that the node cannot use.

    A 401 means fal refused the key, and gets a link to update it. Other statuses use fal's own reason
    when the body has one.
    """
    status = response.status_code
    body = _json_body(response)
    reason = _reason_from_body(body)
    links: list[NodeErrorLink] = []

    if status == HTTP_UNAUTHORIZED:
        message = (
            f"fal refused the API key. Update {API_KEY_NAME} in Settings → API Keys & Secrets, then run the node again."
        )
        links = [
            NodeErrorLink.from_secret(API_KEY_NAME, "Update the API key"),
            NodeErrorLink(label="Get a fal API key", url=FAL_KEYS_URL),
        ]
    elif status == HTTP_TOO_MANY_REQUESTS:
        message = "fal is rate-limiting requests. Wait a minute and run the node again."
        if reason:
            message = f"{message} fal said: {reason}"
    elif reason:
        message = reason
    elif status >= HTTP_SERVER_ERROR:
        message = f"fal had a problem handling the request (HTTP {status}). Run the node again in a moment."
    else:
        message = f"fal rejected the request (HTTP {status})."

    all_fields = _fields_from_response(response, body)
    all_fields.update(fields or {})
    return NodeError(message, fields=all_fields, response=_response_payload(response, body), links=links)


def error_from_request_exception(
    exc: Exception,
    *,
    fields: dict[str, str | int | float | bool] | None = None,
) -> NodeError:
    """The error for a call to fal that failed before or without a usable response."""
    if isinstance(exc, httpx.HTTPStatusError):
        return error_from_http_response(exc.response, fields=fields)
    if isinstance(exc, httpx.TimeoutException):
        message = "fal did not answer in time. Run the node again."
    elif isinstance(exc, httpx.ConnectError):
        message = "Could not reach fal. Check your internet connection and run the node again."
    else:
        message = f"The request to fal failed: {exc}"
    return NodeError(message, fields=fields)


def error_from_failed_status(
    status: str,
    status_json: dict[str, Any] | None,
    *,
    request_id: str,
) -> NodeError:
    """The error for a queue status that says the generation failed."""
    reason = _reason_from_body(status_json)
    message = reason or "fal reported that the generation failed and gave no reason."
    fields: dict[str, str | int | float | bool] = {"status": status}
    if request_id:
        fields["generation_id"] = request_id
    error_type = status_json.get("error_type") if isinstance(status_json, dict) else None
    if error_type:
        fields["error_code"] = str(error_type)
    return NodeError(message, fields=fields, response=status_json or None)
