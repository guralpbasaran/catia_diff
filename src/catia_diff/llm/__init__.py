"""Multimodal extraction backends."""

from catia_diff.llm.base import ImageRef, VisionModel
from catia_diff.llm.merge import merge_extraction
from catia_diff.llm.mock import MockVisionModel, NullVisionModel
from catia_diff.llm.schemas import VisionSheetExtraction

__all__ = [
    "ImageRef",
    "MockVisionModel",
    "NullVisionModel",
    "VisionModel",
    "VisionSheetExtraction",
    "merge_extraction",
]


def build_vision_model(config) -> VisionModel:
    """Factory used by the extraction agent (kept out of import time)."""
    from catia_diff.config import VisionMode
    from catia_diff.llm.anthropic_client import AnthropicVisionModel

    if config.mode is VisionMode.OFF:
        return NullVisionModel()
    return AnthropicVisionModel(config)
