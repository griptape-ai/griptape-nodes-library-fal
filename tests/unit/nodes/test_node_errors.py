"""Failures reach the caller as NodeError, through the nodes' real httpx calls."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from griptape_nodes.exe_types.core_types import NodeError

from griptape_nodes_library_fal import fal_queue_node
from griptape_nodes_library_fal.fal_categories import FalImageToImage, FalTextToImage
from griptape_nodes_library_fal.fal_queue_node import FalQueueNode
from griptape_nodes_library_fal.fal_seedvr_image_upscale import FalSeedVRImageUpscale
from griptape_nodes_library_fal.fal_seedvr_video_upscale import FalSeedVRVideoUpscale

REQUEST_ID = "req-123"
QUEUE = "https://queue.fal.run"
MODEL = "fal-ai/flux/dev"

Handler = Callable[[httpx.Request], httpx.Response]


def _json(status: int, body: Any, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers)


def _submitted() -> httpx.Response:
    return _json(200, {"request_id": REQUEST_ID})


def _completed() -> httpx.Response:
    return _json(200, {"status": "COMPLETED"})


@pytest.fixture(autouse=True)
def _fal_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAL_KEY", "test-key")
    monkeypatch.setattr(FalQueueNode, "DEFAULT_POLL_INTERVAL", 1)

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(fal_queue_node.asyncio, "sleep", no_sleep)


@pytest.fixture
def fal(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], None]:
    """Route the queue node's real httpx clients to a handler."""
    real_client = httpx.AsyncClient

    def install(handler: Handler) -> None:
        def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(fal_queue_node.httpx, "AsyncClient", client)

    return install


def _node(*, failed_wired: bool = False) -> FalTextToImage:
    node = FalTextToImage(name="text_to_image")
    node.set_parameter_value("model", MODEL)
    node.set_parameter_value("prompt", "a cat")
    node._has_outgoing_connections = lambda _param: failed_wired  # type: ignore[method-assign]
    return node


def _run(node: FalQueueNode) -> None:
    asyncio.run(node.aprocess())


def _route(
    submit: Callable[[], httpx.Response], status: Callable[[], httpx.Response], result: Callable[[], httpx.Response]
) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST":
            return submit()
        if path.endswith("/status"):
            return status()
        return result()

    return handler


def test_missing_key_is_reported_first_and_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FalQueueNode, "_get_api_key", lambda _self: None)
    node = FalImageToImage(name="image_to_image")  # required inputs are also empty

    errors = node.validate_before_node_run()

    assert errors is not None
    assert len(errors) == 1
    error = errors[0]
    assert isinstance(error, NodeError)
    assert "FAL_KEY is not set" in str(error)
    assert error.links[0].url == "#settings-secrets?filter=FAL_KEY"
    assert error.links[1].url == "https://fal.ai/dashboard/keys"


def test_missing_key_at_run_raises_when_failed_is_unwired(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FalQueueNode, "_get_api_key", lambda _self: None)
    node = _node()

    with pytest.raises(NodeError, match="FAL_KEY is not set"):
        _run(node)


def test_missing_required_input_names_the_parameter() -> None:
    node = FalImageToImage(name="image_to_image")

    errors = node.validate_before_node_run()

    assert errors is not None
    required = [e for e in errors if isinstance(e, NodeError) and e.fields.get("parameter") == "image_url"]
    assert required
    assert str(required[0]).startswith("'image_url' is required by")
    assert all(isinstance(e, NodeError) for e in errors)


@pytest.mark.parametrize(
    ("node_class", "param"), [(FalSeedVRImageUpscale, "image_url"), (FalSeedVRVideoUpscale, "video_url")]
)
def test_seedvr_missing_input(node_class: type[FalQueueNode], param: str) -> None:
    node = node_class(name="upscale")

    errors = node.validate_before_node_run()

    assert errors is not None
    assert isinstance(errors[0], NodeError)
    assert errors[0].fields == {"parameter": param}
    assert param in str(errors[0])


def test_refused_key_links_to_settings(fal: Callable[[Handler], None]) -> None:
    fal(
        _route(
            lambda: _json(401, {"detail": "Authentication required"}, {"x-fal-request-id": "abc"}),
            _completed,
            _completed,
        )
    )
    node = _node()

    with pytest.raises(NodeError) as caught:
        _run(node)

    error = caught.value
    assert str(error).startswith("fal refused the API key")
    assert error.fields == {"status_code": 401, "request_id": "abc"}
    assert error.response == {"detail": "Authentication required"}
    assert error.links[0].url == "#settings-secrets?filter=FAL_KEY"
    assert node.parameter_output_values["was_successful"] is False


def test_validation_detail_list_becomes_message(fal: Callable[[Handler], None]) -> None:
    body = {"detail": [{"loc": ["body", "prompt"], "msg": "Field required", "type": "missing"}]}
    fal(_route(lambda: _json(422, body), _completed, _completed))

    with pytest.raises(NodeError) as caught:
        _run(_node())

    assert str(caught.value) == "'prompt': Field required"
    assert caught.value.fields["status_code"] == 422
    assert caught.value.response == body


def test_message_has_no_node_name_prefix(fal: Callable[[Handler], None]) -> None:
    fal(_route(lambda: _json(403, {"detail": "User is locked. Reason: Exhausted balance."}), _completed, _completed))

    with pytest.raises(NodeError) as caught:
        _run(_node())

    assert str(caught.value) == "User is locked. Reason: Exhausted balance."


def test_non_json_server_error(fal: Callable[[Handler], None]) -> None:
    fal(_route(lambda: httpx.Response(502, text="Bad gateway"), _completed, _completed))

    with pytest.raises(NodeError) as caught:
        _run(_node())

    assert "HTTP 502" in str(caught.value)
    assert caught.value.response == {"text": "Bad gateway"}


def test_connection_failure(fal: Callable[[Handler], None]) -> None:
    def submit() -> httpx.Response:
        raise httpx.ConnectError("boom")

    fal(_route(submit, _completed, _completed))

    with pytest.raises(NodeError, match="Could not reach fal"):
        _run(_node())


def test_failed_status_keeps_generation_id(fal: Callable[[Handler], None]) -> None:
    body = {"status": "FAILED", "error": "Model crashed", "error_type": "runtime"}
    fal(_route(_submitted, lambda: _json(200, body), _completed))
    node = _node()

    with pytest.raises(NodeError) as caught:
        _run(node)

    assert str(caught.value) == "Model crashed"
    assert caught.value.fields == {"status": "FAILED", "generation_id": REQUEST_ID, "error_code": "runtime"}
    assert caught.value.response == body
    assert node.parameter_output_values["generation_id"] == REQUEST_ID
    assert node.parameter_output_values["generation_status"] == "FAILED"


def test_polling_client_error_stops_at_once(fal: Callable[[Handler], None]) -> None:
    calls: list[str] = []

    def status() -> httpx.Response:
        calls.append("status")
        return _json(404, {"detail": "Request not found"})

    fal(_route(_submitted, status, _completed))
    node = _node()

    with pytest.raises(NodeError) as caught:
        _run(node)

    assert str(caught.value) == "Request not found"
    assert caught.value.fields["generation_id"] == REQUEST_ID
    assert len(calls) == 1
    assert node.parameter_output_values["generation_id"] == REQUEST_ID


def test_timeout_points_at_refresh(fal: Callable[[Handler], None]) -> None:
    fal(_route(_submitted, lambda: _json(200, {"status": "IN_PROGRESS"}), _completed))
    node = _node()
    node.set_parameter_value("timeout", 1)

    with pytest.raises(NodeError) as caught:
        _run(node)

    assert "did not finish within 1 seconds" in str(caught.value)
    assert REQUEST_ID not in str(caught.value)
    assert caught.value.fields == {"generation_id": REQUEST_ID, "status": "TIMED_OUT"}
    assert node.parameter_output_values["generation_id"] == REQUEST_ID


def test_result_fetch_failure_shows_fal_reason(fal: Callable[[Handler], None]) -> None:
    fal(_route(_submitted, _completed, lambda: _json(422, {"detail": "Content flagged by safety checker"})))

    with pytest.raises(NodeError) as caught:
        _run(_node())

    assert str(caught.value) == "Content flagged by safety checker"
    assert caught.value.fields["generation_id"] == REQUEST_ID


def test_submit_without_request_id(fal: Callable[[Handler], None]) -> None:
    fal(_route(lambda: _json(200, {"queued": True}), _completed, _completed))

    with pytest.raises(NodeError, match="did not return a request ID") as caught:
        _run(_node())

    assert caught.value.response == {"queued": True}


def test_result_without_media_file(fal: Callable[[Handler], None]) -> None:
    result = {"seed": 1}
    fal(_route(_submitted, _completed, lambda: _json(200, result)))

    with pytest.raises(NodeError) as caught:
        _run(_node())

    assert "no image file" in str(caught.value)
    assert caught.value.fields == {"generation_id": REQUEST_ID}
    assert caught.value.response == result


def test_invalid_extra_payload_json() -> None:
    node = _node()
    node.parameter_values["extra_payload"] = "{not json"

    with pytest.raises(NodeError) as caught:
        asyncio.run(node._build_payload())

    assert caught.value.fields == {"parameter": "extra_payload"}


def test_failed_output_wired_follows_failed_without_raising(fal: Callable[[Handler], None]) -> None:
    fal(_route(lambda: _json(422, {"detail": "bad"}), _completed, _completed))
    node = _node(failed_wired=True)

    _run(node)

    assert node.parameter_output_values["was_successful"] is False
    assert node.parameter_output_values["result_details"] == "bad"


def test_refresh_never_raises(fal: Callable[[Handler], None]) -> None:
    fal(_route(_submitted, _completed, lambda: _json(500, {"detail": "boom"})))
    node = _node()
    node.parameter_output_values["generation_id"] = REQUEST_ID

    asyncio.run(node._refresh_async())

    assert node.parameter_output_values["was_successful"] is False
    assert "boom" in node.parameter_output_values["result_details"]


def test_success_still_succeeds(fal: Callable[[Handler], None]) -> None:
    result = {"images": [{"url": "https://cdn.example.com/out.png"}]}
    fal(_route(_submitted, _completed, lambda: _json(200, result)))
    node = _node()

    _run(node)

    assert node.parameter_output_values["was_successful"] is True
    assert node.parameter_output_values["generation_id"] == REQUEST_ID
    assert json.dumps(result)
