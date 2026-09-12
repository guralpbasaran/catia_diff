"""Source-format ingestion (DXF, PDF, raster images)."""

from catia_diff.extract.base import SourceExtractor
from catia_diff.extract.registry import EXTRACTORS, SUPPORTED_SUFFIXES, get_extractor

__all__ = ["EXTRACTORS", "SUPPORTED_SUFFIXES", "SourceExtractor", "get_extractor"]
