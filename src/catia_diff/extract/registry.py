"""Extractor selection."""

from __future__ import annotations

from pathlib import Path

from catia_diff.config import AuditConfig
from catia_diff.errors import UnsupportedFormatError
from catia_diff.extract.base import SourceExtractor
from catia_diff.extract.dxf_extractor import DxfExtractor
from catia_diff.extract.image_extractor import ImageExtractor
from catia_diff.extract.pdf_extractor import PdfExtractor

#: Ordered - the first extractor that supports the file wins.
EXTRACTORS: tuple[SourceExtractor, ...] = (
    DxfExtractor(),
    PdfExtractor(),
    ImageExtractor(),
)

SUPPORTED_SUFFIXES: tuple[str, ...] = tuple(
    sorted({suffix for extractor in EXTRACTORS for suffix in extractor.suffixes})
)


def get_extractor(path: Path, config: AuditConfig | None = None) -> SourceExtractor:
    for extractor in EXTRACTORS:
        if extractor.supports(path):
            return extractor
    if path.suffix.lower() == ".dwg":
        raise UnsupportedFormatError(
            "DWG is a closed binary format. Convert it to DXF first, for example with the "
            "free ODA File Converter: `ODAFileConverter <in-dir> <out-dir> ACAD2018 DXF 0 1`."
        )
    raise UnsupportedFormatError(
        f"No extractor for '{path.suffix or path.name}'. Supported: {', '.join(SUPPORTED_SUFFIXES)}"
    )
