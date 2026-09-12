"""Raster image ingestion (scanned or photographed drawings).

There is nothing to parse in a bitmap, so this extractor only prepares the
sheet: page size, cleaned-up raster and the ``needs_vision`` flag that tells
the extraction agent to run Claude Vision on it.
"""

from __future__ import annotations

from pathlib import Path

from catia_diff.config import AuditConfig
from catia_diff.extract.base import SourceExtractor
from catia_diff.extract.raster import IMAGE_SUFFIXES, image_size
from catia_diff.models.drawing import DrawingDocument, Sheet, SourceFormat, Units
from catia_diff.models.geometry import CoordinateSpace


class ImageExtractor(SourceExtractor):
    name = "image"
    suffixes = IMAGE_SUFFIXES

    def extract(self, path: Path, config: AuditConfig) -> DrawingDocument:
        width, height = image_size(path)
        sheet = Sheet(
            index=0,
            name=path.name,
            width=float(width),
            height=float(height),
            units=Units(config.default_units),
            coordinate_space=CoordinateSpace.Y_DOWN,
            raster_path=path,
            raster_dpi=float(config.raster_dpi),
            needs_vision=True,
            warnings=["raster input - every callout comes from the vision pass"],
        )
        return DrawingDocument(
            source_path=path,
            source_format=SourceFormat.IMAGE,
            units=Units(config.default_units),
            extractor=self.name,
            sheets=[sheet],
        )
