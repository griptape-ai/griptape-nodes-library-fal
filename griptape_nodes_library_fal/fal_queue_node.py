from __future__ import annotations

import asyncio
import logging
import os
import threading
from abc import ABC, abstractmethod
from contextlib import suppress
from typing import Any
from urllib.parse import urlparse

import httpx
from griptape_nodes.exe_types.core_types import NodeError, ParameterMode
from griptape_nodes.exe_types.node_types import SuccessFailureNode
from griptape_nodes.exe_types.param_types.parameter_button import ParameterButton
from griptape_nodes.exe_types.param_types.parameter_int import ParameterInt
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

from griptape_nodes_library_fal.fal_node_errors import (
    API_KEY_NAME,
    API_KEY_URL,
    PROVIDER,
    failed_status_error,
)

logger = logging.getLogger("griptape_nodes")

__all__ = ["FalQueueNode"]

# fal queue generation states
STATUS_IN_QUEUE = "IN_QUEUE"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
STATUS_ERROR = "ERROR"
STATUS_TIMED_OUT = "TIMED_OUT"

TERMINAL_FAILURE_STATUSES = (STATUS_FAILED, STATUS_ERROR)

# Polling gives up after this many failed polls in a row, instead of waiting out the whole timeout.
MAX_CONSECUTIVE_POLL_ERRORS = 5
# fal answers 401 for a key it refuses. Other 4xx statuses, such as a locked account's 403, are not key problems.
HTTP_UNAUTHORIZED = 401
# Client errors that retrying cannot fix. 408 and 429 are excluded because they are transient.
NON_RETRYABLE_HTTP_STATUSES = frozenset(code for code in range(400, 500) if code not in (408, 425, 429))


class FalQueueNode(SuccessFailureNode, ABC):
    """Base class for nodes that call the fal.ai queue API directly.

    This class provides common functionality for nodes that:
    1. Submit a request to POST {BASE_URL}/{QUEUE_MODEL_ID}
    2. Poll status via GET {BASE_URL}/{QUEUE_STATUS_BASE}/requests/{request_id}/status
    3. Handle terminal states (COMPLETED, FAILED, ERROR)
    4. Fetch the final result from GET {BASE_URL}/{QUEUE_STATUS_BASE}/requests/{request_id}

    Subclasses must set QUEUE_MODEL_ID and QUEUE_STATUS_BASE and implement:
    - _build_payload(): Build the fal input payload for submission
    - _parse_result(): Parse the model-specific result data
    - _set_safe_defaults(): Clear output parameters on error

    The status/result paths use QUEUE_STATUS_BASE rather than QUEUE_MODEL_ID because
    fal serves status under the base model id (e.g. submit to fal-ai/seedvr/upscale/video
    but poll fal-ai/seedvr/requests/{id}/status). Reconstructing URLs from the request_id
    also lets the Refresh button recover a result after the workflow is reloaded.
    """

    SERVICE_NAME = "fal"

    # Subclasses must override these.
    QUEUE_MODEL_ID = ""
    QUEUE_STATUS_BASE = ""

    # Polling configuration
    DEFAULT_POLL_INTERVAL = 5
    DEFAULT_MAX_ATTEMPTS = 120  # 10 minutes with 5s intervals

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        base = os.getenv("FAL_BASE_URL", "https://queue.fal.run")
        self._base_url = base.rstrip("/")
        # request_id -> {"status_url", "response_url"} as returned by fal on submit.
        self._queue_urls: dict[str, dict[str, str]] = {}

        default_timeout = self.DEFAULT_MAX_ATTEMPTS * self.DEFAULT_POLL_INTERVAL
        self.add_parameter(
            ParameterInt(
                name="timeout",
                default_value=default_timeout,
                tooltip="Polling timeout in seconds. Set to 0 for no timeout.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                min_val=0,
                max_val=86400,
            )
        )

    def _create_status_parameters(
        self,
        *,
        result_details_tooltip: str = "Details about the operation result",
        result_details_placeholder: str = "Details on the operation will be presented here.",
        parameter_group_initially_collapsed: bool = True,
    ) -> None:
        super()._create_status_parameters(
            result_details_tooltip=result_details_tooltip,
            result_details_placeholder=result_details_placeholder,
            parameter_group_initially_collapsed=parameter_group_initially_collapsed,
        )
        # Inject generation_id (the fal request_id), generation_status, and a Refresh button
        # into the Status group. The button lets users recover a result after a timeout without
        # re-running the workflow.
        status_group = self.status_component.get_parameter_group()
        status_group.add_child(
            ParameterString(
                name="generation_id",
                default_value="",
                tooltip="fal request id. Preserved across timeouts and failures so the result can be recovered via the Refresh button.",
                allowed_modes={ParameterMode.OUTPUT},
                settable=False,
                placeholder_text="The fal request id will appear here.",
                hide=True,
                hide_property=True,
            )
        )
        status_group.add_child(
            ParameterString(
                name="generation_status",
                default_value="",
                tooltip="Latest known status of the generation (e.g., IN_PROGRESS, COMPLETED, TIMED_OUT).",
                allowed_modes={ParameterMode.OUTPUT},
                settable=False,
                placeholder_text="The generation status will appear here.",
            )
        )
        status_group.add_child(
            ParameterButton(
                name="generation_refresh",
                label="Refresh / Retrieve Result",
                icon="refresh-cw",
                variant="secondary",
                full_width=True,
                tooltip="Re-check the generation status and pull the result onto the node if completed.",
                on_click=self._on_refresh_clicked,
            )
        )

    @abstractmethod
    async def _build_payload(self) -> dict[str, Any]:
        """Build the fal input payload for submission.

        Returns:
            dict: The request payload to send to the fal queue API.
        """

    @abstractmethod
    async def _parse_result(self, result_json: dict[str, Any], request_id: str) -> None:
        """Parse the model-specific result data and set output parameters.

        Args:
            result_json: The JSON response from the result endpoint.
            request_id: The fal request id for this request.
        """

    @abstractmethod
    def _set_safe_defaults(self) -> None:
        """Clear all output parameters on error."""

    async def aprocess(self) -> None:
        await self._process_generation()

    def _get_api_key(self) -> str | None:
        return os.getenv(API_KEY_NAME) or GriptapeNodes.SecretsManager().get_secret(API_KEY_NAME)

    def _missing_key_errors(self) -> list[Exception]:
        """Validation errors for a missing key. Nothing can run without one, so callers return it alone."""
        return [] if self._get_api_key() else [NodeError.from_missing_secret(API_KEY_NAME, key_url=API_KEY_URL)]

    def _validate_api_key(self) -> str:
        api_key = self._get_api_key()
        if not api_key:
            self._set_safe_defaults()
            raise NodeError.from_missing_secret(API_KEY_NAME, key_url=API_KEY_URL)
        return api_key

    def _headers(self, api_key: str) -> dict[str, str]:
        return {"Authorization": f"Key {api_key}", "Content-Type": "application/json"}

    def _resolve_timeout_seconds(self) -> int:
        try:
            value = self.get_parameter_value("timeout")
        except Exception:
            value = None
        if value is None:
            return self.DEFAULT_MAX_ATTEMPTS * self.DEFAULT_POLL_INTERVAL
        return max(0, int(value))

    def _submit_url(self) -> str:
        return f"{self._base_url}/{self.QUEUE_MODEL_ID}"

    def _status_base(self) -> str:
        """Path segment fal serves status and results under, used when fal's own URLs are not on hand."""
        return self.QUEUE_STATUS_BASE

    def _status_url(self, request_id: str) -> str:
        recorded = self._queue_urls.get(request_id, {}).get("status_url")
        return recorded or f"{self._base_url}/{self._status_base()}/requests/{request_id}/status"

    def _result_url(self, request_id: str) -> str:
        recorded = self._queue_urls.get(request_id, {}).get("response_url")
        return recorded or f"{self._base_url}/{self._status_base()}/requests/{request_id}"

    def _record_queue_urls(self, request_id: str, response_json: dict[str, Any]) -> None:
        """Remember the status and result URLs fal returned on submit.

        fal's own URLs are authoritative: for endpoints with a sub-path (such as
        fal-ai/florence-2-large/caption) status is served under the app id only. They are used
        only when they point at the configured queue host, because requests to them carry the API key.
        """
        queue_host = urlparse(self._base_url).netloc
        urls = {
            key: value
            for key in ("status_url", "response_url")
            if isinstance(value := response_json.get(key), str) and urlparse(value).netloc == queue_host
        }
        if urls:
            self._queue_urls[request_id] = urls

    def _log(self, message: str) -> None:
        with suppress(Exception):
            logger.info("%s: %s", self.name, message)

    def _extract_error_message(self, response_json: dict[str, Any] | None) -> str:
        return str(failed_status_error("", response_json or {}, request_id=""))

    def _generation_error(self, error: NodeError, *, request_id: str = "", status: str = "") -> NodeError:
        """Clear the outputs for a failed generation and return the error to raise.

        The request id and status are kept when given, so the Refresh button can still recover the result.
        """
        self._set_safe_defaults()
        self.parameter_output_values["generation_id"] = request_id
        if status:
            self.parameter_output_values["generation_status"] = status
        return error

    @staticmethod
    def _read_json_object(
        response: httpx.Response, what: str, fields: dict[str, str | int | float | bool] | None = None
    ) -> dict[str, Any]:
        """The JSON object in a 2xx response, or a NodeError saying what fal sent instead."""
        all_fields = {"status_code": response.status_code, **(fields or {})}
        try:
            body = response.json()
        except ValueError as e:
            text = (response.text or "").strip()
            raise NodeError(
                f"fal's {what} is not JSON. Run the node again.",
                fields=all_fields,
                response={"text": text[:2000]} if text else None,
            ) from e
        if not isinstance(body, dict):
            raise NodeError(
                f"fal's {what} is not a JSON object. Run the node again.",
                fields=all_fields,
                response={"body": body},
            )
        return body

    async def _submit_generation(self, payload: dict[str, Any], headers: dict[str, str]) -> str:
        submit_url = self._submit_url()
        self._log(f"Submitting request to {submit_url}")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(submit_url, json=payload, headers=headers, timeout=60)
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            self._log(f"HTTP error: {e.response.status_code} - {e.response.text}")
            raise NodeError.from_http_response(
                e.response,
                provider=PROVIDER,
                secret_name=API_KEY_NAME,
                key_refused=e.response.status_code == HTTP_UNAUTHORIZED,
            ) from e
        except Exception as e:
            self._log(f"Request failed: {e}")
            raise NodeError.from_request_exception(e, provider=PROVIDER, secret_name=API_KEY_NAME) from e

        response_json = self._read_json_object(response, "reply to the request")
        request_id = response_json.get("request_id")
        if request_id:
            self._log(f"Submitted. request_id={request_id}")
            self._record_queue_urls(str(request_id), response_json)
            return str(request_id)

        self._log("No request_id returned from POST response")
        msg = "fal accepted the request but did not return a request ID, so the result cannot be fetched. Run the node again."
        raise NodeError(msg, response=response_json if isinstance(response_json, dict) else None)

    def _handle_terminal_status(self, status: str, status_json: dict[str, Any]) -> bool:
        """Return True if status is terminal. Raises a NodeError for failure states."""
        if status == STATUS_COMPLETED:
            return True

        if status in TERMINAL_FAILURE_STATUSES:
            request_id = self.parameter_output_values.get("generation_id", "") or ""
            logger.error("%s: Generation failed with status: %s", self.name, status)
            logger.error("%s: Error response: %s", self.name, status_json)
            error = failed_status_error(status, status_json, request_id=request_id)
            raise self._generation_error(error, request_id=request_id, status=status)

        return False

    async def _poll_generation_status(self, request_id: str, headers: dict[str, str]) -> dict[str, Any]:
        status_url = self._status_url(request_id)
        poll_interval = self.DEFAULT_POLL_INTERVAL
        timeout_s = self._resolve_timeout_seconds()
        # None means unbounded (timeout=0 set by user)
        max_attempts = max(1, (timeout_s + poll_interval - 1) // poll_interval) if timeout_s > 0 else None

        attempt = 0
        consecutive_errors = 0
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    self._log(f"Polling attempt #{attempt + 1} for request {request_id}")
                    response = await client.get(status_url, headers=headers, timeout=60)
                    response.raise_for_status()
                    status_json = response.json()
                    consecutive_errors = 0

                    status = status_json.get("status", "unknown")
                    self._log(f"Status: {status}")
                    self.parameter_output_values["generation_status"] = status

                    if self._handle_terminal_status(status, status_json):
                        return status_json

                    attempt += 1
                    if max_attempts is not None and attempt >= max_attempts:
                        break
                    await asyncio.sleep(poll_interval)
                except NodeError:
                    raise
                except httpx.HTTPStatusError as e:
                    status_code = e.response.status_code
                    self._log(f"HTTP error while polling: {status_code} - {e.response.text}")
                    consecutive_errors += 1
                    # A client error such as 400, 401, 403 or 404 cannot succeed on retry.
                    if status_code in NON_RETRYABLE_HTTP_STATUSES or consecutive_errors >= MAX_CONSECUTIVE_POLL_ERRORS:
                        error = NodeError.from_http_response(
                            e.response,
                            provider=PROVIDER,
                            secret_name=API_KEY_NAME,
                            key_refused=status_code == HTTP_UNAUTHORIZED,
                            fields={"generation_id": request_id, "status_url": status_url},
                        )
                        raise self._generation_error(error, request_id=request_id, status=STATUS_ERROR) from e
                    attempt += 1
                    if max_attempts is not None and attempt >= max_attempts:
                        break
                    await asyncio.sleep(poll_interval)
                except Exception as e:
                    self._log(f"Error while polling: {e}")
                    consecutive_errors += 1
                    if consecutive_errors >= MAX_CONSECUTIVE_POLL_ERRORS:
                        error = NodeError.from_request_exception(
                            e, provider=PROVIDER, secret_name=API_KEY_NAME, fields={"generation_id": request_id}
                        )
                        raise self._generation_error(error, request_id=request_id, status=STATUS_ERROR) from e
                    attempt += 1
                    if max_attempts is not None and attempt >= max_attempts:
                        break
                    await asyncio.sleep(poll_interval)

        # Timeout reached. Preserve generation_id so the user can recover via Refresh.
        self._log("Polling timed out waiting for result")
        error = NodeError(
            f"The generation did not finish within {timeout_s} seconds. It may still be running on fal. "
            f"Click the refresh icon on the `generation_status` parameter to re-check and pull the result "
            f"onto this node.",
            fields={"generation_id": request_id, "status": STATUS_TIMED_OUT},
        )
        raise self._generation_error(error, request_id=request_id, status=STATUS_TIMED_OUT)

    async def _fetch_generation_result(self, request_id: str, headers: dict[str, str]) -> dict[str, Any]:
        result_url = self._result_url(request_id)
        self._log(f"Fetching result from {result_url}")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(result_url, headers=headers, timeout=300)
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            self._log(f"HTTP error fetching result: {e.response.status_code} - {e.response.text}")
            error = NodeError.from_http_response(
                e.response,
                provider=PROVIDER,
                secret_name=API_KEY_NAME,
                key_refused=e.response.status_code == HTTP_UNAUTHORIZED,
                fields={"generation_id": request_id},
            )
            raise self._generation_error(error) from e
        except Exception as e:
            self._log(f"Error fetching result: {e}")
            error = NodeError.from_request_exception(
                e, provider=PROVIDER, secret_name=API_KEY_NAME, fields={"generation_id": request_id}
            )
            raise self._generation_error(error) from e

        fields: dict[str, str | int | float | bool] = {"generation_id": request_id}
        try:
            result_json = self._read_json_object(response, "result", fields)
        except NodeError as e:
            raise self._generation_error(e) from e.__cause__
        if not result_json:
            msg = "fal returned an empty result."
            raise self._generation_error(NodeError(msg, fields={**fields, "status_code": response.status_code}))
        self._log("Result fetched successfully")
        return result_json

    async def _process_generation(self) -> None:
        self._clear_execution_status()
        self.parameter_output_values["generation_id"] = ""
        self.parameter_output_values["generation_status"] = ""

        try:
            await self._run_generation()
        except NodeError as e:
            self._route_failure(e)

    async def _run_generation(self) -> None:
        api_key = self._validate_api_key()
        headers = self._headers(api_key)

        try:
            payload = await self._build_payload()
        except NodeError:
            self._set_safe_defaults()
            raise
        except Exception as e:
            self._set_safe_defaults()
            self._route_failure(e)
            return

        request_id = await self._submit_generation(payload, headers)

        # Store request_id so the Refresh affordance can recover the result on timeout/failure.
        self.parameter_output_values["generation_id"] = request_id

        await self._poll_generation_status(request_id, headers)
        result_json = await self._fetch_generation_result(request_id, headers)

        if "provider_response" in self.parameter_output_values:
            self.parameter_output_values["provider_response"] = result_json

        try:
            await self._parse_result(result_json, request_id)
        except Exception as e:
            self._log(f"Error parsing result: {e}")
            self._set_safe_defaults()
            raise self._result_error(e, result_json, request_id) from e

    @staticmethod
    def _result_error(e: Exception, result_json: dict[str, Any], request_id: str) -> NodeError:
        """A NodeError for a result the node could not use. A NodeError raised while parsing is kept as is."""
        if isinstance(e, NodeError):
            return e
        return NodeError(
            f"Could not read the result fal returned: {e}",
            fields={"generation_id": request_id},
            response=result_json,
        )

    def _route_failure(self, error: Exception) -> None:
        """Record the failure, then follow Failed if it is wired and raise otherwise."""
        self._set_status_results(was_successful=False, result_details=str(error))
        self._handle_failure_exception(error)

    def _on_refresh_clicked(self, _button: Any, _details: Any) -> None:
        """Sync entry point for the Refresh button. Bridges into the async refresh flow.

        Button.on_click_callback is invoked synchronously from a thread that may already
        have a running event loop, so we run the coroutine on a dedicated worker thread
        with its own fresh loop to avoid `RuntimeError: This event loop is already running`.
        """

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._refresh_async())
            finally:
                loop.close()

        thread = threading.Thread(target=_runner, name=f"{self.name}-refresh", daemon=True)
        thread.start()
        thread.join()

    async def _refresh_async(self) -> None:
        """Re-check the generation status and pull the result if it has completed.

        A single GET to the status endpoint; never re-enters the polling loop.
        """
        request_id = (self.parameter_output_values.get("generation_id") or "").strip()
        if not request_id:
            self._set_status_results(
                was_successful=False,
                result_details="No request id is available on this node yet. Run the node first to submit a generation.",
            )
            return

        try:
            api_key = self._validate_api_key()
        except NodeError as e:
            self._set_status_results(was_successful=False, result_details=f"Cannot refresh: {e}")
            return

        headers = self._headers(api_key)
        status_json = await self._fetch_status_for_refresh(request_id, headers)
        if status_json is None:
            return

        status = status_json.get("status", "unknown")
        self.parameter_output_values["generation_status"] = status

        if status == STATUS_COMPLETED:
            await self._refresh_completed(request_id, headers)
            return

        self._refresh_render_status(request_id, status, status_json)

    async def _fetch_status_for_refresh(self, request_id: str, headers: dict[str, str]) -> dict[str, Any] | None:
        status_url = self._status_url(request_id)
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(status_url, headers=headers, timeout=60)
                response.raise_for_status()
            return self._read_json_object(response, "status reply")
        except NodeError as e:
            self._set_status_results(
                was_successful=False, result_details=f"Failed to fetch status for `{request_id}`: {e}"
            )
        except Exception as e:
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            error = NodeError.from_request_exception(
                e, provider=PROVIDER, secret_name=API_KEY_NAME, key_refused=status_code == HTTP_UNAUTHORIZED
            )
            self._set_status_results(
                was_successful=False, result_details=f"Failed to fetch status for `{request_id}`: {error}"
            )
        return None

    async def _refresh_completed(self, request_id: str, headers: dict[str, str]) -> None:
        # A button callback never raises, so failures are shown in the status text.
        try:
            result_json = await self._fetch_generation_result(request_id, headers)
        except NodeError as e:
            self._set_status_results(
                was_successful=False,
                result_details=f"Generation `{request_id}` is COMPLETED, but fetching the result failed: {e}",
            )
            return
        if "provider_response" in self.parameter_output_values:
            self.parameter_output_values["provider_response"] = result_json
        try:
            await self._parse_result(result_json, request_id)
        except Exception as e:
            self._log(f"Error parsing result: {e}")
            self._set_safe_defaults()
            self._set_status_results(
                was_successful=False,
                result_details=f"Generation `{request_id}` completed, but parsing the result failed: {e}",
            )
            return
        self._set_status_results(
            was_successful=True,
            result_details=f"Refreshed: generation `{request_id}` completed and result was retrieved.",
        )

    def _refresh_render_status(self, request_id: str, status: str, status_json: dict[str, Any]) -> None:
        if status in TERMINAL_FAILURE_STATUSES:
            error_message = self._extract_error_message(status_json)
            self._set_status_results(
                was_successful=False,
                result_details=f"Generation `{request_id}` ended with status {status}.\n\n{error_message}",
            )
            return

        self._set_status_results(
            was_successful=False,
            result_details=(
                f"Generation `{request_id}` is still in progress (status: {status}). "
                f"Click the refresh icon again to re-check."
            ),
        )

    @staticmethod
    async def _download_bytes_from_url(url: str) -> bytes | None:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=120)
                resp.raise_for_status()
                return resp.content
        except Exception:
            return None
