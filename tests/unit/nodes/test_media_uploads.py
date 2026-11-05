import asyncio
from typing import Any

import pytest
from griptape.artifacts import ImageArtifact
from griptape.artifacts.image_url_artifact import ImageUrlArtifact
from griptape_nodes.drivers.storage.griptape_cloud_storage_driver import GriptapeCloudStorageDriver
from griptape_nodes.exe_types.core_types import ParameterList
from griptape_nodes.exe_types.param_components.artifact_url import public_artifact_url_parameter

from griptape_nodes_library_fal.fal_categories import FalImageToImage, FalTextToImage

IMAGE_BYTES = b"\x89PNG fake image bytes"
PUBLIC_URL = "https://public.example.com/uploaded.png"


@pytest.fixture
def uploads(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    """Run the engine's real upload helper against a fake Griptape Cloud, recording uploaded bytes."""
    uploaded: list[bytes] = []

    def upload_file(self: Any, path: Any, file_content: bytes) -> str:  # noqa: ARG001
        uploaded.append(file_content)
        return PUBLIC_URL

    monkeypatch.setattr(public_artifact_url_parameter, "resolve_cloud_credential", lambda *_, **__: "key")
    monkeypatch.setattr(
        public_artifact_url_parameter.PublicArtifactUrlParameter, "_get_bucket_id", classmethod(lambda *_, **__: "b")
    )
    # Newer engines resolve the bucket through this method on the async upload path.
    monkeypatch.setattr(
        public_artifact_url_parameter.PublicArtifactUrlParameter,
        "_resolve_bucket_id",
        classmethod(lambda *_, **__: "b"),
        raising=False,
    )
    monkeypatch.setattr(GriptapeCloudStorageDriver, "upload_file", upload_file)
    monkeypatch.setattr(GriptapeCloudStorageDriver, "delete_file", lambda *_: None)
    return uploaded


def _image_artifact() -> ImageArtifact:
    return ImageArtifact(IMAGE_BYTES, format="png", width=1, height=1)


def _image_values() -> list[Any]:
    return [_image_artifact(), ImageUrlArtifact(PUBLIC_URL), PUBLIC_URL]


@pytest.mark.parametrize("value", _image_values(), ids=["ImageArtifact", "ImageUrlArtifact", "str"])
def test_image_list_uploads_each_artifact_kind(uploads: list[bytes], value: Any) -> None:
    node = FalTextToImage(name="text_to_image")
    node.set_parameter_value("model", "fal-ai/hidream-o1-image")
    image_list = node.get_parameter_by_name("reference_image_urls")
    assert isinstance(image_list, ParameterList)
    child = image_list.add_child_parameter()
    node.set_parameter_value(child.name, value)

    payload = asyncio.run(node._build_payload())

    assert payload["reference_image_urls"] == [PUBLIC_URL]
    assert uploads == ([IMAGE_BYTES] if isinstance(value, ImageArtifact) else [])


def test_image_list_uploads_row_saved_with_another_type(uploads: list[bytes]) -> None:
    node = FalTextToImage(name="text_to_image")
    node.set_parameter_value("model", "fal-ai/hidream-o1-image")
    image_list = node.get_parameter_by_name("reference_image_urls")
    assert isinstance(image_list, ParameterList)
    child = image_list.add_child_parameter()
    child.type = "ImageArtifact"
    node.set_parameter_value(child.name, _image_artifact())

    payload = asyncio.run(node._build_payload())

    assert payload["reference_image_urls"] == [PUBLIC_URL]
    assert uploads == [IMAGE_BYTES]


@pytest.mark.parametrize("value", _image_values(), ids=["ImageArtifact", "ImageUrlArtifact", "str"])
def test_single_image_uploads_each_artifact_kind(uploads: list[bytes], value: Any) -> None:
    node = FalImageToImage(name="image_to_image")
    assert node._field_by_param["image_url"].required
    node.set_parameter_value("image_url", value)

    payload = asyncio.run(node._build_payload())

    assert payload["image_url"] == PUBLIC_URL
    assert uploads == ([IMAGE_BYTES] if isinstance(value, ImageArtifact) else [])
