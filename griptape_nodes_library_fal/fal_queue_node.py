from __future__ import annotations

import asyncio
import logging
import os
import threading
from abc import ABC, abstractmethod
from contextlib import suppress
from typing import Any

import httpx
from griptape_nodes.exe_types.core_types import ParameterMode
from griptape_nodes.exe_types.node_types import SuccessFailureNode
from griptape_nodes.exe_types.param_types.parameter_button import ParameterButton
from griptape_nodes.exe_types.param_types.parameter_int import ParameterInt
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

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
    API_KEY_NAME = "FAL_KEY"

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

    def _validate_api_key(self) -> str:
        api_key = os.getenv("FAL_KEY") or GriptapeNodes.SecretsManager().get_secret(self.API_KEY_NAME)
        if not api_key:
            self._set_safe_defaults()
            msg = f"{self.name} is missing {self.API_KEY_NAME}. Ensure it's set in the environment/config."
            raise ValueError(msg)
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

    def _status_url(self, request_id: str) -> str:
        return f"{self._base_url}/{self.QUEUE_STATUS_BASE}/requests/{request_id}/status"

    def _result_url(self, request_id: str) -> str:
        return f"{self._base_url}/{self.QUEUE_STATUS_BASE}/requests/{request_id}"

    def _log(self, message: str) -> None:
        with suppress(Exception):
            logger.info("%s: %s", self.name, message)

    def _extract_error_message(self, response_json: dict[str, Any] | None) -> str:
        if not response_json:
            return f"{self.name} generation failed with no error details provided by fal."

        error = response_json.get("error")
        if error:
            if isinstance(error, dict):
                error_msg = error.get("message") or error.get("error") or str(error)
                return f"{self.name} {error_msg}"
            return f"{self.name} {error}"

        detail = response_json.get("detail")
        if detail:
            return f"{self.name} generation failed: {detail}"

        return f"{self.name} generation failed.\n\nFull fal response:\n{response_json}"

    def _extract_http_error_message(self, response: httpx.Response) -> str:
        try:
            error_json = response.json()
        except Exception:
            return f"{self.name}: fal error: {response.status_code} - {response.text}"
        return f"{self.name}: {self._extract_error_message(error_json)}"

    async def _submit_generation(self, payload: dict[str, Any], headers: dict[str, str]) -> str | None:
        submit_url = f"{self._base_url}/{self.QUEUE_MODEL_ID}"
        self._log(f"Submitting request to {submit_url}")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(submit_url, json=payload, headers=headers, timeout=60)
                response.raise_for_status()
                response_json = response.json()
        except httpx.HTTPStatusError as e:
            self._log(f"HTTP error: {e.response.status_code} - {e.response.text}")
            raise RuntimeError(self._extract_http_error_message(e.response)) from e
        except Exception as e:
            self._log(f"Request failed: {e}")
            msg = f"{self.name} request failed: {e}"
            raise RuntimeError(msg) from e

        request_id = response_json.get("request_id")
        if request_id:
            self._log(f"Submitted. request_id={request_id}")
            return str(request_id)

        self._log("No request_id returned from POST response")
        return None

    def _handle_terminal_status(self, status: str, status_json: dict[str, Any]) -> bool:
        """Return True if status is terminal. Sets failure status results for failure states."""
        if status == STATUS_COMPLETED:
            return True

        if status in TERMINAL_FAILURE_STATUSES:
            request_id = self.parameter_output_values.get("generation_id", "") or ""
            logger.error("%s: Generation failed with status: %s", self.name, status)
            logger.error("%s: Error response: %s", self.name, status_json)
            self._set_safe_defaults()
            self.parameter_output_values["generation_id"] = request_id
            self.parameter_output_values["generation_status"] = status
            self._set_status_results(was_successful=False, result_details=self._extract_error_message(status_json))
            return True

        return False

    async def _poll_generation_status(self, request_id: str, headers: dict[str, str]) -> dict[str, Any] | None:
        status_url = self._status_url(request_id)
        poll_interval = self.DEFAULT_POLL_INTERVAL
        timeout_s = self._resolve_timeout_seconds()
        # None means unbounded (timeout=0 set by user)
        max_attempts = max(1, (timeout_s + poll_interval - 1) // poll_interval) if timeout_s > 0 else None

        attempt = 0
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    self._log(f"Polling attempt #{attempt + 1} for request {request_id}")
                    response = await client.get(status_url, headers=headers, timeout=60)
                    response.raise_for_status()
                    status_json = response.json()

                    status = status_json.get("status", "unknown")
                    self._log(f"Status: {status}")
                    self.parameter_output_values["generation_status"] = status

                    if self._handle_terminal_status(status, status_json):
                        return status_json if status == STATUS_COMPLETED else None

                    attempt += 1
                    if max_attempts is not None and attempt >= max_attempts:
                        break
                    await asyncio.sleep(poll_interval)
                except httpx.HTTPStatusError as e:
                    self._log(f"HTTP error while polling: {e.response.status_code} - {e.response.text}")
                    attempt += 1
                    if max_attempts is not None and attempt >= max_attempts:
                        self._set_safe_defaults()
                        self._set_status_results(
                            was_successful=False,
                            result_details=f"Failed to poll generation status: HTTP {e.response.status_code}",
                        )
                        return None
                    await asyncio.sleep(poll_interval)
                except Exception as e:
                    self._log(f"Error while polling: {e}")
                    attempt += 1
                    if max_attempts is not None and attempt >= max_attempts:
                        self._set_safe_defaults()
                        self._set_status_results(
                            was_successful=False, result_details=f"Failed to poll generation status: {e}"
                        )
                        return None
                    await asyncio.sleep(poll_interval)

        # Timeout reached. Preserve generation_id so the user can recover via Refresh.
        self._log("Polling timed out waiting for result")
        self._set_safe_defaults()
        self.parameter_output_values["generation_id"] = request_id
        self.parameter_output_values["generation_status"] = STATUS_TIMED_OUT
        self._set_status_results(
            was_successful=False,
            result_details=(
                f"Generation `{request_id}` did not finish within {timeout_s} seconds. "
                f"It may still be running on fal. Click the refresh icon on the "
                f"`generation_status` parameter to re-check and pull the result onto this node."
            ),
        )
        return None

    async def _fetch_generation_result(self, request_id: str, headers: dict[str, str]) -> dict[str, Any] | None:
        result_url = self._result_url(request_id)
        self._log(f"Fetching result from {result_url}")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(result_url, headers=headers, timeout=300)
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            self._log(f"HTTP error fetching result: {e.response.status_code} - {e.response.text}")
            self._set_safe_defaults()
            self._set_status_results(
                was_successful=False, result_details=f"Failed to fetch generation result: HTTP {e.response.status_code}"
            )
            return None
        except Exception as e:
            self._log(f"Error fetching result: {e}")
            self._set_safe_defaults()
            self._set_status_results(was_successful=False, result_details=f"Failed to fetch generation result: {e}")
            return None

        result_json = response.json()
        self._log("Result fetched successfully")
        return result_json

    async def _process_generation(self) -> None:
        self._clear_execution_status()
        self.parameter_output_values["generation_id"] = ""
        self.parameter_output_values["generation_status"] = ""

        try:
            api_key = self._validate_api_key()
        except ValueError as e:
            self._handle_api_key_validation_error(e)
            return

        headers = self._headers(api_key)

        try:
            payload = await self._build_payload()
        except Exception as e:
            self._handle_payload_build_error(e)
            return

        try:
            request_id = await self._submit_generation(payload, headers)
        except RuntimeError as e:
            self._handle_submission_error(e)
            return

        if not request_id:
            self._set_safe_defaults()
            self._set_status_results(
                was_successful=False,
                result_details="No request_id returned from fal. Cannot proceed with generation.",
            )
            return

        # Store request_id so the Refresh affordance can recover the result on timeout/failure.
        self.parameter_output_values["generation_id"] = request_id

        status_response = await self._poll_generation_status(request_id, headers)
        if not status_response:
            return

        result_json = await self._fetch_generation_result(request_id, headers)
        if not result_json:
            return

        if "provider_response" in self.parameter_output_values:
            self.parameter_output_values["provider_response"] = result_json

        try:
            await self._parse_result(result_json, request_id)
        except Exception as e:
            self._handle_result_parsing_error(e)

    def _handle_api_key_validation_error(self, e: ValueError) -> None:
        self._set_safe_defaults()
        self._set_status_results(was_successful=False, result_details=str(e))
        self._handle_failure_exception(e)

    def _handle_payload_build_error(self, e: Exception) -> None:
        self._set_safe_defaults()
        self._set_status_results(
            was_successful=False, result_details=f"{self.name}: Failed to build request payload: {e}"
        )
        self._handle_failure_exception(e)

    def _handle_submission_error(self, e: RuntimeError) -> None:
        self._set_safe_defaults()
        self._set_status_results(was_successful=False, result_details=str(e))
        self._handle_failure_exception(e)

    def _handle_result_parsing_error(self, e: Exception) -> None:
        self._log(f"Error parsing result: {e}")
        self._set_safe_defaults()
        self._set_status_results(was_successful=False, result_details=f"Failed to parse generation result: {e}")
        self._handle_failure_exception(e)

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
        except ValueError as e:
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
                return response.json()
        except httpx.HTTPStatusError as e:
            self._set_status_results(
                was_successful=False,
                result_details=f"Failed to fetch status for `{request_id}`: HTTP {e.response.status_code}",
            )
        except Exception as e:
            self._set_status_results(
                was_successful=False, result_details=f"Failed to fetch status for `{request_id}`: {e}"
            )
        return None

    async def _refresh_completed(self, request_id: str, headers: dict[str, str]) -> None:
        result_json = await self._fetch_generation_result(request_id, headers)
        if not result_json:
            self._set_status_results(
                was_successful=False,
                result_details=f"Generation `{request_id}` is COMPLETED, but fetching the result failed. See node logs.",
            )
            return
        if "provider_response" in self.parameter_output_values:
            self.parameter_output_values["provider_response"] = result_json
        try:
            await self._parse_result(result_json, request_id)
        except Exception as e:
            self._handle_result_parsing_error(e)
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
