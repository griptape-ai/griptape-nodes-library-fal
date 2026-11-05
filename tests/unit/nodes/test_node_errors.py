"""Failures reach the caller as NodeError, through the nodes' real httpx calls."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from griptape_nodes.exe_types.core_types import NodeError

from griptape_nodes_library_fal import fal_queue_node
from griptape_nodes_library_fal.fal_categories import FalImageToImage, FalTextToImage
from griptape_nodes_library_fal.fal_queue_node import FalQueueNode
from griptape_nodes_library_fal.fal_seedvr_image_upscale import FalSeedVRImageUpscale
from griptape_nodes_library_fal.fal_seedvr_video_upscale import FalSeedVRVideoUpscale

ROOT = Path(__file__).parents[3]
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
    # The SeedVR nodes look up a Griptape Cloud credential when they are built.
    monkeypatch.setenv("GT_CLOUD_API_KEY", "test-cloud-key")
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


def test_locked_account_403_shows_fals_reason_without_key_link(fal: Callable[[Handler], None]) -> None:
    reason = "User is locked. Reason: Exhausted balance. Top up your balance at fal.ai/dashboard/billing."
    fal(_route(lambda: _json(403, {"detail": reason}), _completed, _completed))

    with pytest.raises(NodeError) as caught:
        _run(_node())

    assert str(caught.value) == reason
    assert caught.value.links == []
    assert caught.value.fields["status_code"] == 403


def test_refused_key_links_to_settings(fal: Callable[[Handler], None]) -> None:
    fal(
        _route(
            lambda: _json(401, {"detail": "invalid key credentials"}),
            _completed,
            _completed,
        )
    )
    node = _node()

    with pytest.raises(NodeError) as caught:
        _run(node)

    error = caught.value
    assert "refused" in str(error)
    assert "text_to_image" not in str(error)
    assert error.fields["status_code"] == 401
    assert error.response == {"detail": "invalid key credentials"}
    assert error.links[0].url == "#settings-secrets?filter=FAL_KEY"
    assert node.parameter_output_values["was_successful"] is False


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


BAD_2XX_BODIES = {
    "html": lambda: httpx.Response(200, text="<html>Bad gateway</html>"),
    "empty": lambda: httpx.Response(200, text=""),
    "list": lambda: _json(200, ["not", "an", "object"]),
}


@pytest.mark.parametrize("body", BAD_2XX_BODIES)
@pytest.mark.parametrize("stage", ["submit", "result"])
@pytest.mark.parametrize("failed_wired", [False, True], ids=["unwired", "wired"])
def test_2xx_without_a_json_object_is_a_node_error(
    fal: Callable[[Handler], None], body: str, stage: str, failed_wired: bool
) -> None:
    bad = BAD_2XX_BODIES[body]
    fal(_route(bad if stage == "submit" else _submitted, _completed, bad))
    node = _node(failed_wired=failed_wired)

    if failed_wired:
        _run(node)
        assert node.parameter_output_values["was_successful"] is False
        assert (
            "not JSON" in node.parameter_output_values["result_details"]
            or "not a JSON object" in (node.parameter_output_values["result_details"])
        )
        return

    with pytest.raises(NodeError) as caught:
        _run(node)

    assert "fal's" in str(caught.value)
    assert caught.value.fields["status_code"] == 200
    assert caught.value.response is not None or body == "empty"


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


def test_node_files_load_the_way_the_engine_loads_them() -> None:
    """The engine loads each node file standalone, with the manifest folder on sys.path."""
    manifest = json.loads((ROOT / "griptape-nodes-library.json").read_text())
    files = {node["file_path"] for node in manifest["nodes"]}
    assert files
    sys.path.insert(0, str(ROOT))
    try:
        for index, file_path in enumerate(sorted(files)):
            spec = importlib.util.spec_from_file_location(f"fal_node_file_{index}", ROOT / file_path)
            assert spec is not None
            assert spec.loader is not None
            spec.loader.exec_module(importlib.util.module_from_spec(spec))
    finally:
        sys.path.remove(str(ROOT))
