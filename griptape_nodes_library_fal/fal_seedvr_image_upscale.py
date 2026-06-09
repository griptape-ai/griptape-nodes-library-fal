from __future__ import annotations

import logging
from typing import Any

from griptape.artifacts import ImageArtifact
from griptape.artifacts.image_url_artifact import ImageUrlArtifact
from griptape_nodes.exe_types.core_types import Parameter, ParameterGroup, ParameterMode
from griptape_nodes.exe_types.param_components.artifact_url.public_artifact_url_parameter import (
    PublicArtifactUrlParameter,
)
from griptape_nodes.exe_types.param_components.project_file_parameter import ProjectFileParameter
from griptape_nodes.exe_types.param_components.seed_parameter import SeedParameter
from griptape_nodes.exe_types.param_types.parameter_dict import ParameterDict
from griptape_nodes.exe_types.param_types.parameter_float import ParameterFloat
from griptape_nodes.exe_types.param_types.parameter_image import ParameterImage
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.traits.options import Options
from griptape_nodes.utils.artifact_normalization import normalize_artifact_input

from griptape_nodes_library_fal.fal_queue_node import FalQueueNode

logger = logging.getLogger("griptape_nodes")

__all__ = ["FalSeedVRImageUpscale"]


class FalSeedVRImageUpscale(FalQueueNode):
    """Upscale an image using the SeedVR2 model directly via the fal.ai queue API."""

    QUEUE_MODEL_ID = "fal-ai/seedvr/upscale/image"
    QUEUE_STATUS_BASE = "fal-ai/seedvr"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.category = "image"
        self.description = "Upscale an image using SeedVR2 via fal.ai"

        # Image URL
        self._public_image_url_parameter = PublicArtifactUrlParameter(
            node=self,
            artifact_url_parameter=ParameterImage(
                name="image_url",
                default_value="",
                tooltip="Image URL",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            ),
            disclaimer_message="The fal SeedVR2 service utilizes this URL to access the image for upscaling.",
        )
        self._public_image_url_parameter.add_input_parameters()

        self.add_parameter(
            ParameterString(
                name="upscale_mode",
                default_value="factor",
                tooltip="Upscale mode",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=["factor", "target"])},
            )
        )

        self.add_parameter(
            ParameterString(
                name="target_resolution",
                default_value="1080p",
                tooltip="Target resolution",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=["720p", "1080p", "1440p", "2160p"])},
                hide=True,
            )
        )

        self.add_parameter(
            ParameterFloat(
                name="upscale_factor",
                default_value=2.0,
                tooltip="The upscale factor",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                ui_options={"slider": {"min_val": 1.0, "max_val": 10.0}, "step": 0.1},
            )
        )

        with ParameterGroup(name="Generation Settings", ui_options={"collapsed": True}) as generation_settings_group:
            self._seed_parameter = SeedParameter(self)
            self._seed_parameter.add_input_parameters(inside_param_group=True)

            ParameterFloat(
                name="noise_scale",
                default_value=0.1,
                tooltip="Noise scale",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                ui_options={"slider": {"min_val": 0.0, "max_val": 1.0}, "step": 0.001},
            )

            ParameterString(
                name="output_format",
                default_value="jpg",
                tooltip="Output format",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=["png", "jpg", "webp"])},
            )

        self.add_node_element(generation_settings_group)

        # OUTPUTS
        self.add_parameter(
            ParameterDict(
                name="provider_response",
                tooltip="Verbatim response from fal (final result)",
                allowed_modes={ParameterMode.OUTPUT},
                hide_property=True,
                hide=True,
            )
        )

        self.add_parameter(
            ParameterImage(
                name="image",
                tooltip="Saved image as artifact for downstream display",
                allowed_modes={ParameterMode.OUTPUT, ParameterMode.PROPERTY},
                settable=False,
                ui_options={"pulse_on_run": True},
            )
        )

        self._output_file = ProjectFileParameter(
            node=self,
            name="output_file",
            default_filename="fal_seedvr_upscale.jpg",
        )
        self._output_file.add_parameter()

        self._create_status_parameters(
            result_details_tooltip="Details about the image generation result or any errors",
            result_details_placeholder="Generation status and details will appear here.",
            parameter_group_initially_collapsed=True,
        )

    def validate_before_node_run(self) -> list[Exception] | None:
        exceptions = super().validate_before_node_run() or []
        image_url = self.get_parameter_value("image_url")
        if not image_url:
            exceptions.append(ValueError("Image URL must be provided"))
        return exceptions if exceptions else None

    def after_value_set(self, parameter: Parameter, value: Any) -> None:
        super().after_value_set(parameter, value)
        self._seed_parameter.after_value_set(parameter, value)

        # Convert string paths to ImageUrlArtifact by uploading to static storage
        if parameter.name == "image_url" and isinstance(value, str) and value:
            artifact = normalize_artifact_input(value, ImageUrlArtifact, accepted_types=(ImageArtifact,))
            if artifact != value:
                self.set_parameter_value("image_url", artifact)

        if parameter.name == "upscale_mode":
            upscale_mode = str(value)
            if upscale_mode == "factor":
                self.hide_parameter_by_name("target_resolution")
                self.show_parameter_by_name("upscale_factor")
            if upscale_mode == "target":
                self.show_parameter_by_name("target_resolution")
                self.hide_parameter_by_name("upscale_factor")

    def preprocess(self) -> None:
        self._seed_parameter.preprocess()

    async def aprocess(self) -> None:
        self.preprocess()
        try:
            await self._process_generation()
        finally:
            self._public_image_url_parameter.delete_uploaded_artifact()

    async def _build_payload(self) -> dict[str, Any]:
        # Normalize string paths to ImageUrlArtifact during processing (handles values
        # that come from connections and bypass after_value_set).
        image_url = self.get_parameter_value("image_url")
        normalized_image_url = normalize_artifact_input(image_url, ImageUrlArtifact, accepted_types=(ImageArtifact,))
        if normalized_image_url != image_url:
            self.parameter_values["image_url"] = normalized_image_url

        public_image_url = self._public_image_url_parameter.get_public_url_for_parameter()
        if not public_image_url:
            msg = "Image URL must be provided"
            raise ValueError(msg)

        upscale_mode = self.get_parameter_value("upscale_mode")
        payload: dict[str, Any] = {
            "image_url": public_image_url,
            "upscale_mode": upscale_mode,
            "noise_scale": self.get_parameter_value("noise_scale"),
            "output_format": self.get_parameter_value("output_format"),
            "seed": self._seed_parameter.get_seed(),
        }

        if upscale_mode == "target":
            payload["target_resolution"] = self.get_parameter_value("target_resolution")
        else:
            payload["upscale_factor"] = self.get_parameter_value("upscale_factor")

        return payload

    async def _parse_result(self, result_json: dict[str, Any], request_id: str) -> None:  # noqa: ARG002
        extracted_url = self._extract_image_url(result_json)
        if not extracted_url:
            self.parameter_output_values["image"] = None
            self._set_status_results(
                was_successful=False,
                result_details="Generation completed but no image URL was found in the response.",
            )
            return

        image_bytes = await self._download_bytes_from_url(extracted_url)

        if not image_bytes:
            self.parameter_output_values["image"] = ImageUrlArtifact(value=extracted_url)
            self._set_status_results(
                was_successful=True,
                result_details="Image generated successfully. Using provider URL (could not download image bytes).",
            )
            return

        try:
            dest = self._output_file.build_file()
            saved = await dest.awrite_bytes(image_bytes)
        except Exception as e:
            logger.info("Failed to save image: %s, using provider URL", e)
            self.parameter_output_values["image"] = ImageUrlArtifact(value=extracted_url)
            self._set_status_results(
                was_successful=True,
                result_details=f"Image generated successfully. Using provider URL (could not save: {e}).",
            )
            return

        self.parameter_output_values["image"] = ImageUrlArtifact(value=saved.location, name=saved.name)
        logger.info("Saved image as %s", saved.name)
        self._set_status_results(
            was_successful=True, result_details=f"Image generated successfully and saved as {saved.name}."
        )

    def _set_safe_defaults(self) -> None:
        self.parameter_output_values["generation_id"] = ""
        self.parameter_output_values["provider_response"] = None
        self.parameter_output_values["image"] = None

    @staticmethod
    def _extract_image_url(obj: dict[str, Any] | None) -> str | None:
        if not obj:
            return None
        image_obj = obj.get("image")
        if isinstance(image_obj, dict):
            url = image_obj.get("url")
            if isinstance(url, str):
                return url
        return None
