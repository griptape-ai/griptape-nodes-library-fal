from __future__ import annotations

from griptape_nodes_library_fal.fal_category_node import (
    OUTPUT_AUDIO,
    OUTPUT_IMAGE,
    OUTPUT_TEXT,
    OUTPUT_THREE_D,
    OUTPUT_VIDEO,
    FalCategoryNode,
)

__all__ = [
    "FalAudioToAudio",
    "FalAudioToText",
    "FalAudioToVideo",
    "FalImageTo3D",
    "FalImageToImage",
    "FalImageToText",
    "FalImageToVideo",
    "FalSpeechToSpeech",
    "FalSpeechToText",
    "FalTextTo3D",
    "FalTextToAudio",
    "FalTextToImage",
    "FalTextToSpeech",
    "FalTextToVideo",
    "FalThreeDTo3D",
    "FalVideoToAudio",
    "FalVideoToText",
    "FalVideoToVideo",
    "FalVision",
]


# One class per fal category, named after the category slug so users who know fal can find them.
class FalTextToImage(FalCategoryNode):
    CATEGORY = "text-to-image"
    OUTPUT_KIND = OUTPUT_IMAGE
    NODE_CATEGORY = "image"
    DEFAULT_MODEL = "fal-ai/flux/dev"


class FalImageToImage(FalCategoryNode):
    CATEGORY = "image-to-image"
    OUTPUT_KIND = OUTPUT_IMAGE
    NODE_CATEGORY = "image"


class FalTextToVideo(FalCategoryNode):
    CATEGORY = "text-to-video"
    OUTPUT_KIND = OUTPUT_VIDEO
    NODE_CATEGORY = "video"


class FalImageToVideo(FalCategoryNode):
    CATEGORY = "image-to-video"
    OUTPUT_KIND = OUTPUT_VIDEO
    NODE_CATEGORY = "video"


class FalVideoToVideo(FalCategoryNode):
    CATEGORY = "video-to-video"
    OUTPUT_KIND = OUTPUT_VIDEO
    NODE_CATEGORY = "video"


class FalAudioToVideo(FalCategoryNode):
    CATEGORY = "audio-to-video"
    OUTPUT_KIND = OUTPUT_VIDEO
    NODE_CATEGORY = "video"


class FalTextToAudio(FalCategoryNode):
    CATEGORY = "text-to-audio"
    OUTPUT_KIND = OUTPUT_AUDIO
    NODE_CATEGORY = "audio"


class FalAudioToAudio(FalCategoryNode):
    CATEGORY = "audio-to-audio"
    OUTPUT_KIND = OUTPUT_AUDIO
    NODE_CATEGORY = "audio"


class FalTextToSpeech(FalCategoryNode):
    CATEGORY = "text-to-speech"
    OUTPUT_KIND = OUTPUT_AUDIO
    NODE_CATEGORY = "audio"


class FalSpeechToSpeech(FalCategoryNode):
    CATEGORY = "speech-to-speech"
    OUTPUT_KIND = OUTPUT_AUDIO
    NODE_CATEGORY = "audio"


class FalVideoToAudio(FalCategoryNode):
    CATEGORY = "video-to-audio"
    OUTPUT_KIND = OUTPUT_AUDIO
    NODE_CATEGORY = "audio"


class FalSpeechToText(FalCategoryNode):
    CATEGORY = "speech-to-text"
    OUTPUT_KIND = OUTPUT_TEXT
    NODE_CATEGORY = "audio"


class FalAudioToText(FalCategoryNode):
    CATEGORY = "audio-to-text"
    OUTPUT_KIND = OUTPUT_TEXT
    NODE_CATEGORY = "audio"


class FalVideoToText(FalCategoryNode):
    CATEGORY = "video-to-text"
    OUTPUT_KIND = OUTPUT_TEXT
    NODE_CATEGORY = "vision"


class FalImageToText(FalCategoryNode):
    CATEGORY = "image-to-text"
    OUTPUT_KIND = OUTPUT_TEXT
    NODE_CATEGORY = "vision"


class FalVision(FalCategoryNode):
    CATEGORY = "vision"
    OUTPUT_KIND = OUTPUT_TEXT
    NODE_CATEGORY = "vision"


class FalImageTo3D(FalCategoryNode):
    CATEGORY = "image-to-3d"
    OUTPUT_KIND = OUTPUT_THREE_D
    NODE_CATEGORY = "three_d"


class FalTextTo3D(FalCategoryNode):
    CATEGORY = "text-to-3d"
    OUTPUT_KIND = OUTPUT_THREE_D
    NODE_CATEGORY = "three_d"


class FalThreeDTo3D(FalCategoryNode):
    CATEGORY = "3d-to-3d"
    OUTPUT_KIND = OUTPUT_THREE_D
    NODE_CATEGORY = "three_d"
