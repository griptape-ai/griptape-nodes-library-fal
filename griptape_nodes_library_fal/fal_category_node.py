from __future__ import annotations

import json
import logging
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

from griptape.artifacts import ImageArtifact
from griptape.artifacts.audio_url_artifact import AudioUrlArtifact
from griptape.artifacts.image_url_artifact import ImageUrlArtifact
from griptape.artifacts.video_url_artifact import VideoUrlArtifact
from griptape_nodes.exe_types.core_types import Parameter, ParameterGroup, ParameterList, ParameterMode
from griptape_nodes.exe_types.param_components.artifact_url.public_artifact_url_parameter import (
    PublicArtifactUrlParameter,
)
from griptape_nodes.exe_types.param_components.project_file_parameter import ProjectFileParameter
from griptape_nodes.exe_types.param_types.parameter_audio import ParameterAudio
from griptape_nodes.exe_types.param_types.parameter_bool import ParameterBool
from griptape_nodes.exe_types.param_types.parameter_dict import ParameterDict
from griptape_nodes.exe_types.param_types.parameter_float import ParameterFloat
from griptape_nodes.exe_types.param_types.parameter_image import ParameterImage
from griptape_nodes.exe_types.param_types.parameter_int import ParameterInt
from griptape_nodes.exe_types.param_types.parameter_json import ParameterJson
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.exe_types.param_types.parameter_three_d import Parameter3D
from griptape_nodes.exe_types.param_types.parameter_video import ParameterVideo
from griptape_nodes.retained_mode.events.parameter_events import RemoveParameterFromNodeRequest
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes
from griptape_nodes.traits.options import Options
from griptape_nodes.utils.artifact_normalization import normalize_artifact_input
from griptape_nodes_library.three_d.three_d_artifact import ThreeDUrlArtifact  # pyright: ignore[reportMissingImports]

from griptape_nodes_library_fal.fal_queue_node import FalQueueNode
from griptape_nodes_library_fal.fal_schema import (
    KIND_AUDIO,
    KIND_AUDIO_LIST,
    KIND_BOOL,
    KIND_ENUM,
    KIND_FLOAT,
    KIND_IMAGE,
    KIND_IMAGE_LIST,
    KIND_INT,
    KIND_JSON,
    KIND_TEXT,
    KIND_VIDEO,
    KIND_VIDEO_LIST,
    MEDIA_KINDS,
    MEDIA_LIST_KINDS,
    FieldSpec,
    ModelSpec,
    load_category,
)

logger = logging.getLogger("griptape_nodes")

__all__ = ["FalCategoryNode"]

EMPTY_CHOICE_LABEL = "(none)"


async def _get_public_url(helper: PublicArtifactUrlParameter) -> str:
    """Publish a helper's artifact, using the async method when the installed engine has it.

    Older engines only have the sync method, which is what the SeedVR nodes call.
    """
    aget_public_url = getattr(helper, "aget_public_url_for_parameter", None)
    if aget_public_url is not None:
        return await aget_public_url()
    return helper.get_public_url_for_parameter()


def _enum_label(choice: Any) -> str:
    """Dropdown text for an enum choice. A literal empty string is shown as a label, not as a blank."""
    text = str(choice)
    return text or EMPTY_CHOICE_LABEL


OUTPUT_IMAGE = "image"
OUTPUT_VIDEO = "video"
OUTPUT_AUDIO = "audio"
OUTPUT_THREE_D = "three_d"
OUTPUT_TEXT = "text"

# Names that belong to the node itself. A model field with one of these names gets a `fal_` prefix.
RESERVED_NAMES = frozenset(
    {
        "model",
        "timeout",
        "extra_payload",
        "provider_response",
        "generation_id",
        "generation_status",
        "output_file",
        OUTPUT_IMAGE,
        OUTPUT_VIDEO,
        OUTPUT_AUDIO,
        OUTPUT_THREE_D,
        OUTPUT_TEXT,
    }
)

# Optional fields that are shown at the top level instead of under Advanced Settings.
PRIMARY_FIELD_NAMES = frozenset(
    {
        "prompt",
        "negative_prompt",
        "text",
        "aspect_ratio",
        "resolution",
        "duration",
        "image_size",
        "num_images",
        "seed",
    }
)
ADVANCED_GROUP_NAME = "Advanced Settings"
# Kinds whose parameter holds a plain value that has a meaningful default.
_SCALAR_KINDS = (KIND_TEXT, KIND_BOOL, KIND_INT, KIND_FLOAT, KIND_ENUM)
# Shown for an optional enum that has no default. Picking it leaves the field out of the request.
# It is a label rather than a blank so the dropdown never falls back to the engine's empty-text placeholder.
UNSET_CHOICE = "(model default)"

# Per output kind: (artifact class, file extensions that identify it, default saved filename).
_MEDIA_OUTPUTS: dict[str, tuple[type, frozenset[str], str]] = {
    OUTPUT_IMAGE: (ImageUrlArtifact, frozenset({"png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff"}), "fal_image.png"),
    OUTPUT_VIDEO: (VideoUrlArtifact, frozenset({"mp4", "webm", "mov", "mkv", "avi"}), "fal_video.mp4"),
    OUTPUT_AUDIO: (AudioUrlArtifact, frozenset({"mp3", "wav", "flac", "ogg", "m4a", "aac", "opus"}), "fal_audio.mp3"),
    OUTPUT_THREE_D: (
        ThreeDUrlArtifact,
        frozenset({"glb", "gltf", "obj", "fbx", "usdz", "ply", "stl"}),
        "fal_model.glb",
    ),
}
_TEXT_RESULT_KEYS = ("output", "text", "caption", "description", "generated_text", "transcription", "response")
_MAX_RESULT_DEPTH = 4


class FalCategoryNode(FalQueueNode):
    """Base for nodes that expose every fal.ai model in one fal category behind a `model` dropdown.

    Selecting a model rebuilds the node's input parameters from that model's schema snapshot
    (see scripts/fetch_fal_schemas.py). Subclasses set CATEGORY and OUTPUT_KIND.
    """

    # Subclasses must override these.
    CATEGORY = ""
    OUTPUT_KIND = ""
    NODE_CATEGORY = ""
    DEFAULT_MODEL: str | None = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.category = self.NODE_CATEGORY
        self._models = load_category(self.CATEGORY)
        self.description = f"Run any fal.ai {self.CATEGORY} model."

        self._current_model: ModelSpec | None = None
        self._loaded_endpoint_id = ""
        # Maps node parameter name -> the fal payload field it feeds.
        self._field_by_param: dict[str, FieldSpec] = {}
        self._dynamic_names: list[str] = []
        self._upload_helpers: list[PublicArtifactUrlParameter] = []

        endpoint_ids = list(self._models)
        default_model = self.DEFAULT_MODEL if self.DEFAULT_MODEL in self._models else (endpoint_ids or [""])[0]
        self.add_parameter(
            ParameterString(
                name="model",
                default_value=default_model,
                tooltip=self._model_tooltip(default_model),
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=endpoint_ids)},
            )
        )

        # Static group so dynamic parameters can nest in it and still be restored on workflow load.
        self.add_node_element(ParameterGroup(name=ADVANCED_GROUP_NAME, ui_options={"collapsed": True}))

        # extra_payload is the escape hatch for fields the schema mapping cannot express.
        # It is static, so it keeps its value and connections when the model changes.
        extra_payload = ParameterJson(
            name="extra_payload",
            default_value=None,
            tooltip="Extra JSON merged into the request body last. Use it for fields not shown above.",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        extra_payload.parent_element_name = ADVANCED_GROUP_NAME
        self.add_parameter(extra_payload)

        self._add_outputs()
        self._create_status_parameters(
            result_details_tooltip="Details about the generation result or any errors",
            result_details_placeholder="Generation status and details will appear here.",
            parameter_group_initially_collapsed=True,
        )
        self._rebuild_model_parameters(default_model)

    # ---- parameter construction -------------------------------------------------------------

    def _add_outputs(self) -> None:
        self.add_parameter(
            ParameterDict(
                name="provider_response",
                tooltip="Verbatim response from fal (final result)",
                allowed_modes={ParameterMode.OUTPUT},
                hide_property=True,
                hide=True,
            )
        )

        kind = self.OUTPUT_KIND
        if kind == OUTPUT_TEXT:
            self.add_parameter(
                ParameterString(
                    name=OUTPUT_TEXT,
                    tooltip="Text returned by the model",
                    allowed_modes={ParameterMode.OUTPUT},
                    multiline=True,
                    placeholder_text="The model's text output will appear here.",
                    ui_options={"pulse_on_run": True},
                )
            )
            return

        artifact_class, _, default_filename = _MEDIA_OUTPUTS[kind]
        output_types = {
            OUTPUT_IMAGE: ParameterImage,
            OUTPUT_VIDEO: ParameterVideo,
            OUTPUT_AUDIO: ParameterAudio,
            OUTPUT_THREE_D: Parameter3D,
        }
        self.add_parameter(
            output_types[kind](
                name=kind,
                tooltip=f"First {kind.replace('_', ' ')} returned by the model, saved to the project",
                allowed_modes={ParameterMode.OUTPUT, ParameterMode.PROPERTY},
                settable=False,
                ui_options={"pulse_on_run": True},
            )
        )
        self.add_parameter(
            Parameter(
                name=f"{kind}_list",
                type=f"list[{artifact_class.__name__}]",
                output_type=f"list[{artifact_class.__name__}]",
                tooltip=f"Every {kind.replace('_', ' ')} returned by the model, as provider URLs",
                allowed_modes={ParameterMode.OUTPUT},
                hide=True,
            )
        )
        self._output_file = ProjectFileParameter(node=self, name="output_file", default_filename=default_filename)
        self._output_file.add_parameter()

    def _rebuild_model_parameters(self, endpoint_id: str) -> None:
        """Update the model-specific inputs to match `endpoint_id`, touching only what differs.

        A field with the same name and signature in the old and new model keeps its parameter,
        value and connections. Other old fields are removed and new ones added. When a removed
        field has a same-named replacement of the same kind, its value carries over if it still fits.
        """
        new_model = self._models.get(endpoint_id)
        new_specs = {self._param_name_for(s): s for s in new_model.fields} if new_model else {}

        kept: dict[str, FieldSpec] = {}
        carried: dict[str, Any] = {}
        for name, old in list(self._field_by_param.items()):
            new = new_specs.get(name)
            if new is not None and self._signature(new) == self._signature(old):
                self._refresh_kept_parameter(name, old, new)
                kept[name] = new
                continue
            if new is not None and new.kind == old.kind and new.kind not in MEDIA_LIST_KINDS:
                value = self.get_parameter_value(name)
                if value not in (None, "", [], {}) and value != self._default_for(old) and self._value_fits(new, value):
                    carried[name] = value
            self._remove_parameter(name)

        self._field_by_param = kept
        self._loaded_endpoint_id = endpoint_id
        self._current_model = new_model
        if new_model is not None:
            self.description = new_model.description or self.description
        model_parameter = self.get_parameter_by_name("model")
        model_tooltip = self._model_tooltip(endpoint_id)
        if model_parameter is not None and model_parameter.tooltip != model_tooltip:
            model_parameter.tooltip = model_tooltip

        for name, spec in new_specs.items():
            if name not in kept:
                self._add_field_parameter(spec)
                if name in carried:
                    self.set_parameter_value(name, carried[name])

        # Follow the new model's field order when placing parameters.
        self._dynamic_names = list(new_specs)
        self._place_dynamic_parameters()

    def _remove_parameter(self, name: str) -> None:
        """Remove a parameter and its connections. Removing only the element leaves connections behind."""
        if self.get_element_by_name_and_type(name) is None:
            return
        result = GriptapeNodes.handle_request(RemoveParameterFromNodeRequest(parameter_name=name, node_name=self.name))
        # The request fails while the node is still being constructed and has no graph entry yet.
        if not result.succeeded():
            self.remove_parameter_element_by_name(name)

    def _refresh_kept_parameter(self, name: str, old: FieldSpec, new: FieldSpec) -> None:
        """Bring a parameter that survives a model switch in line with the new model's schema."""
        parameter = self.get_parameter_by_name(name)
        if parameter is None:
            return
        tooltip = self._tooltip_for(new)
        if parameter.tooltip != tooltip:
            parameter.tooltip = tooltip
        # Move an untouched value to the new model's default. A value the user set stays.
        if new.kind in _SCALAR_KINDS and self._default_for(new) != self._default_for(old):
            if self.get_parameter_value(name) == self._default_for(old):
                self.set_parameter_value(name, self._default_for(new))

    def _model_tooltip(self, endpoint_id: str) -> str:
        """Markdown tooltip for the `model` parameter, describing the selected model."""
        intro = "fal endpoint id. Search the list and pick a model to load its parameters."
        spec = self._models.get(endpoint_id)
        if spec is None:
            return intro
        description = " ".join(spec.description.split())
        return (
            f"**{spec.display_name}**\n\n{description}\n\n"
            f"[View on fal.ai](https://fal.ai/models/{endpoint_id})\n\n---\n\n{intro}"
        )

    def _tooltip_for(self, spec: FieldSpec) -> str:
        """Markdown tooltip for a generated field: fal's description plus its constraints."""
        text = " ".join(spec.description.split()) or spec.name
        facts = [f"fal field: `{spec.name}`"]
        if spec.required:
            facts.insert(0, "**Required**")
        if spec.kind in (KIND_INT, KIND_FLOAT) and (spec.minimum is not None or spec.maximum is not None):
            low = "any" if spec.minimum is None else f"{spec.minimum:g}"
            high = "any" if spec.maximum is None else f"{spec.maximum:g}"
            facts.append(f"Range: {low} to {high}")
        default = self._default_for(spec)
        if spec.kind in _SCALAR_KINDS and default not in (None, ""):
            facts.append(f"Default: `{default}`")
        return f"{text}\n\n{' · '.join(facts)}"

    def _signature(self, spec: FieldSpec) -> tuple[Any, ...]:
        """What must match for an existing parameter to be reused for a field in another model."""
        choices = tuple(self._enum_choices(spec)) if spec.kind == KIND_ENUM else ()
        return (spec.kind, spec.required, self._is_primary(spec), choices, spec.minimum, spec.maximum)

    @staticmethod
    def _is_primary(spec: FieldSpec) -> bool:
        return spec.required or spec.kind in MEDIA_KINDS + MEDIA_LIST_KINDS or spec.name in PRIMARY_FIELD_NAMES

    @staticmethod
    def _enum_choices(spec: FieldSpec) -> list[str]:
        choices = [_enum_label(c) for c in spec.choices]
        # An optional enum with no default gets an "unset" choice, so picking it omits the field from the request.
        if spec.default is None and not spec.required:
            return [UNSET_CHOICE, *choices]
        return choices

    def _default_for(self, spec: FieldSpec) -> Any:  # noqa: PLR0911
        kind = spec.kind
        if kind == KIND_TEXT:
            return spec.default or ""
        if kind == KIND_BOOL:
            return bool(spec.default)
        if kind in (KIND_INT, KIND_FLOAT):
            return spec.default
        if kind == KIND_ENUM:
            return _enum_label(spec.default) if spec.default is not None else self._enum_choices(spec)[0]
        if kind == KIND_JSON:
            return spec.default if isinstance(spec.default, (dict, list)) else None
        if kind in MEDIA_LIST_KINDS:
            return []
        if kind in MEDIA_KINDS:
            return ""
        return str(spec.default) if spec.default is not None else ""

    def _value_fits(self, spec: FieldSpec, value: Any) -> bool:
        if spec.kind == KIND_ENUM:
            return str(value) in self._enum_choices(spec)
        if spec.kind in (KIND_INT, KIND_FLOAT):
            if not isinstance(value, (int, float)):
                return False
            above_min = spec.minimum is None or value >= spec.minimum
            below_max = spec.maximum is None or value <= spec.maximum
            return above_min and below_max
        return True

    def _register_dynamic(self, parameter: Parameter) -> None:
        self.add_parameter(parameter)
        self._dynamic_names.append(parameter.name)

    def _place_dynamic_parameters(self) -> None:
        """Keep the model parameters directly after the `model` dropdown, ahead of outputs and status."""
        self.move_element_to_position("model", 1)
        position = 2
        root_names = {child.name for child in self.root_ui_element._children}
        for name in self._dynamic_names:
            if name in root_names:
                self.move_element_to_position(name, position)
                position += 1
        self.move_element_to_position(ADVANCED_GROUP_NAME, position)

    def _param_name_for(self, spec: FieldSpec) -> str:
        return f"fal_{spec.name}" if spec.name in RESERVED_NAMES else spec.name

    def _add_field_parameter(self, spec: FieldSpec) -> None:
        param_name = self._param_name_for(spec)
        common: dict[str, Any] = {
            "name": param_name,
            "tooltip": self._tooltip_for(spec),
            "allowed_modes": {ParameterMode.INPUT, ParameterMode.PROPERTY},
        }

        parameter = self._build_parameter(spec, common)
        if not self._is_primary(spec):
            parameter.parent_element_name = ADVANCED_GROUP_NAME
        self._field_by_param[param_name] = spec
        self._register_dynamic(parameter)

    def _build_parameter(self, spec: FieldSpec, common: dict[str, Any]) -> Parameter:  # noqa: PLR0911
        kind = spec.kind
        default = self._default_for(spec)
        if kind == KIND_TEXT:
            return ParameterString(default_value=default, multiline=True, **common)
        if kind == KIND_BOOL:
            return ParameterBool(default_value=default, **common)
        if kind == KIND_INT:
            return ParameterInt(default_value=default, min_val=spec.minimum, max_val=spec.maximum, **common)
        if kind == KIND_FLOAT:
            return ParameterFloat(default_value=default, min_val=spec.minimum, max_val=spec.maximum, **common)
        if kind == KIND_ENUM:
            return ParameterString(default_value=default, traits={Options(choices=self._enum_choices(spec))}, **common)
        if kind == KIND_IMAGE:
            return ParameterImage(default_value=default, **common)
        if kind == KIND_VIDEO:
            return ParameterVideo(default_value=default, **common)
        if kind == KIND_AUDIO:
            return ParameterAudio(default_value=default, **common)
        if kind in MEDIA_LIST_KINDS:
            return self._build_media_list(kind, common)
        if kind == KIND_JSON:
            return ParameterJson(default_value=default, **common)
        return ParameterString(default_value=default, **common)

    @staticmethod
    def _build_media_list(kind: str, common: dict[str, Any]) -> Parameter:
        artifact_name = {
            KIND_IMAGE_LIST: "ImageUrlArtifact",
            KIND_VIDEO_LIST: "VideoUrlArtifact",
            KIND_AUDIO_LIST: "AudioUrlArtifact",
        }[kind]
        artifact_types = [artifact_name, "str", f"list[{artifact_name}]", "list[str]"]
        if kind == KIND_IMAGE_LIST:
            artifact_types.insert(0, "ImageArtifact")
        return ParameterList(
            name=common["name"],
            tooltip=common["tooltip"],
            input_types=artifact_types,
            default_value=[],
            allowed_modes={ParameterMode.INPUT},
            ui_options={"expander": True},
        )

    # ---- node lifecycle ---------------------------------------------------------------------

    def after_value_set(self, parameter: Parameter, value: Any) -> None:
        super().after_value_set(parameter, value)
        if parameter.name == "model" and isinstance(value, str):
            self._set_model(value)
            return

        spec = self._field_by_param.get(parameter.name)
        if spec is not None and spec.kind in MEDIA_KINDS and isinstance(value, str) and value:
            artifact = self._normalize_media(value, spec.kind)
            if artifact != value:
                self.set_parameter_value(parameter.name, artifact)

    def _set_model(self, endpoint_id: str) -> None:
        if endpoint_id != self._loaded_endpoint_id:
            self._rebuild_model_parameters(endpoint_id)

    @staticmethod
    def _normalize_media(value: Any, kind: str) -> Any:
        artifact_class, accepted = {
            KIND_IMAGE: (ImageUrlArtifact, (ImageArtifact,)),
            KIND_VIDEO: (VideoUrlArtifact, ()),
            KIND_AUDIO: (AudioUrlArtifact, ()),
        }[kind]
        return normalize_artifact_input(value, artifact_class, accepted_types=accepted)

    def validate_before_node_run(self) -> list[Exception] | None:
        exceptions = super().validate_before_node_run() or []
        model_id = self.get_parameter_value("model")
        if not model_id:
            exceptions.append(ValueError("A model must be selected"))
        for param_name, spec in self._field_by_param.items():
            if not spec.required:
                continue
            value = self.get_parameter_value(param_name)
            if value in (None, "", [], {}):
                exceptions.append(ValueError(f"{spec.name} is required by {model_id}"))
        return exceptions if exceptions else None

    async def aprocess(self) -> None:
        self._upload_helpers = []
        try:
            await self._process_generation()
        finally:
            for helper in self._upload_helpers:
                helper.delete_uploaded_artifact()
            self._upload_helpers = []

    # ---- queue plumbing ---------------------------------------------------------------------

    @property
    def _endpoint_id(self) -> str:
        return str(self.get_parameter_value("model") or "")

    def _submit_url(self) -> str:
        return f"{self._base_url}/{self._endpoint_id}"

    def _status_base(self) -> str:
        # fal serves status and results under owner/app only, even for endpoints with a sub-path
        # such as fal-ai/florence-2-large/caption. The OpenAPI paths in the snapshots are not reliable here.
        return "/".join(self._endpoint_id.split("/")[:2])

    # ---- payload ----------------------------------------------------------------------------

    async def _build_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for param_name, spec in self._field_by_param.items():
            value = await self._payload_value(param_name, spec)
            if value is not None:
                payload[spec.name] = value

        extra = self.get_parameter_value("extra_payload")
        if isinstance(extra, str) and extra.strip():
            extra = json.loads(extra)
        if isinstance(extra, dict):
            payload.update(extra)
        return payload

    async def _payload_value(self, param_name: str, spec: FieldSpec) -> Any:  # noqa: PLR0911
        value = self.get_parameter_value(param_name)
        kind = spec.kind

        if kind in MEDIA_KINDS:
            return await self._upload_single(param_name, spec)
        if kind in MEDIA_LIST_KINDS:
            return await self._upload_list(param_name, spec)

        if value in (None, ""):
            return None
        if kind == KIND_JSON:
            parsed = json.loads(value) if isinstance(value, str) else value
            return parsed or None
        if kind == KIND_ENUM:
            return self._restore_enum_choice(spec, value)
        # Only send values that differ from fal's own default, so schema drift cannot break a run.
        if not spec.required and value == spec.default:
            return None
        return value

    @staticmethod
    def _restore_enum_choice(spec: FieldSpec, value: Any) -> Any:
        if value == UNSET_CHOICE or (
            spec.default is not None and not spec.required and _enum_label(value) == _enum_label(spec.default)
        ):
            return None
        for choice in spec.choices:
            if _enum_label(choice) == _enum_label(value):
                return choice
        return value

    async def _upload_single(self, param_name: str, spec: FieldSpec) -> str | None:
        value = self.get_parameter_value(param_name)
        if not value:
            return None
        normalized = self._normalize_media(value, spec.kind)
        if normalized != value:
            self.parameter_values[param_name] = normalized
        parameter = self.get_parameter_by_name(param_name)
        if parameter is None:
            return None
        helper = PublicArtifactUrlParameter(node=self, artifact_url_parameter=parameter)
        self._upload_helpers.append(helper)
        return await _get_public_url(helper)

    async def _upload_list(self, param_name: str, spec: FieldSpec) -> list[str] | None:
        container = self.get_parameter_by_name(param_name)
        urls: list[str] = []
        children = container.find_elements_by_type(Parameter) if container is not None else []
        for child in children:
            if not self.get_parameter_value(child.name):
                continue
            helper = PublicArtifactUrlParameter(node=self, artifact_url_parameter=child)
            self._upload_helpers.append(helper)
            urls.append(await _get_public_url(helper))
        if not urls and spec.required:
            msg = f"{spec.name} requires at least one item"
            raise ValueError(msg)
        return urls or None

    # ---- result -----------------------------------------------------------------------------

    async def _parse_result(self, result_json: dict[str, Any], request_id: str) -> None:  # noqa: ARG002
        if self.OUTPUT_KIND == OUTPUT_TEXT:
            self._parse_text_result(result_json)
            return
        await self._parse_media_result(result_json)

    def _parse_text_result(self, result_json: dict[str, Any]) -> None:
        for key in _TEXT_RESULT_KEYS:
            value = result_json.get(key)
            if isinstance(value, str) and value:
                self.parameter_output_values[OUTPUT_TEXT] = value
                self._set_status_results(was_successful=True, result_details="Completed.")
                return
        self.parameter_output_values[OUTPUT_TEXT] = json.dumps(result_json, indent=2)
        self._set_status_results(
            was_successful=True,
            result_details="Completed. No known text field in the response, so the full response is shown.",
        )

    async def _parse_media_result(self, result_json: dict[str, Any]) -> None:
        kind = self.OUTPUT_KIND
        artifact_class, extensions, _ = _MEDIA_OUTPUTS[kind]
        urls = self._find_file_urls(result_json, extensions)
        if not urls:
            self._set_safe_defaults()
            self._set_status_results(
                was_successful=False,
                result_details=f"Generation completed but no {kind.replace('_', ' ')} file was found in the response.",
            )
            return

        self.parameter_output_values[f"{kind}_list"] = [artifact_class(value=url) for url in urls]

        first_url = urls[0]
        file_bytes = await self._download_bytes_from_url(first_url)
        if not file_bytes:
            self.parameter_output_values[kind] = artifact_class(value=first_url)
            self._set_status_results(
                was_successful=True,
                result_details=f"Generated {len(urls)} file(s). Using provider URL (could not download).",
            )
            return

        try:
            destination = self._output_file.build_file()
            saved = await destination.awrite_bytes(file_bytes)
        except Exception as e:
            logger.info("Failed to save %s: %s, using provider URL", kind, e)
            self.parameter_output_values[kind] = artifact_class(value=first_url)
            self._set_status_results(
                was_successful=True,
                result_details=f"Generated {len(urls)} file(s). Using provider URL (could not save: {e}).",
            )
            return

        self.parameter_output_values[kind] = artifact_class(value=saved.location, name=saved.name)
        extra = f" {len(urls) - 1} more are in the {kind}_list output." if len(urls) > 1 else ""
        self._set_status_results(was_successful=True, result_details=f"Saved {saved.name}.{extra}")

    @classmethod
    def _find_file_urls(cls, result: Any, extensions: frozenset[str]) -> list[str]:
        """Collect file URLs from a fal result, preferring ones whose extension matches the output kind."""
        found: list[str] = []
        cls._walk_for_urls(result, found, depth=0)
        matching = [url for url in found if cls._extension(url) in extensions]
        return matching or found

    @classmethod
    def _walk_for_urls(cls, node: Any, found: list[str], depth: int) -> None:
        if depth > _MAX_RESULT_DEPTH:
            return
        if isinstance(node, dict):
            url = node.get("url")
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                found.append(url)
                return
            for value in node.values():
                cls._walk_for_urls(value, found, depth + 1)
        elif isinstance(node, list):
            for item in node:
                cls._walk_for_urls(item, found, depth + 1)

    @staticmethod
    def _extension(url: str) -> str:
        return PurePosixPath(urlparse(url).path).suffix.lstrip(".").lower()

    def _set_safe_defaults(self) -> None:
        self.parameter_output_values["generation_id"] = ""
        self.parameter_output_values["provider_response"] = None
        if self.OUTPUT_KIND == OUTPUT_TEXT:
            self.parameter_output_values[OUTPUT_TEXT] = ""
            return
        self.parameter_output_values[self.OUTPUT_KIND] = None
        self.parameter_output_values[f"{self.OUTPUT_KIND}_list"] = []
