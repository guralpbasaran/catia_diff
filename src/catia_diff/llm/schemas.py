"""Structured-output schemas for the multimodal extraction pass.

These models are what Claude is asked to fill in; they are deliberately flat
and literal (transcribe what is drawn, do not interpret) - the interpretation
happens afterwards in :mod:`catia_diff.llm.merge` with the same text grammar
used for the vector paths.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class VisionBox(BaseModel):
    """Bounding box normalised to the image, ``0..1``, origin at top-left."""

    x0: float = Field(ge=0.0, le=1.0)
    y0: float = Field(ge=0.0, le=1.0)
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)


class VisionDimension(BaseModel):
    text: str = Field(description="Callout exactly as printed, e.g. '⌀12,5 H7' or '30±0,1'")
    kind: Literal["linear", "angular", "diameter", "radial", "thread", "ordinate", "other"]
    box: VisionBox


class VisionSymbol(BaseModel):
    kind: Literal["feature_control_frame", "datum", "surface_finish", "weld", "other"]
    text: str = Field(description="Symbol content, e.g. '⌖|⌀0.2Ⓜ|A|B|C', '-A-', 'Ra 3.2'")
    box: VisionBox


class VisionTitleBlockField(BaseModel):
    label: str = Field(description="Printed label, e.g. 'ÖLÇEK', 'MATERIAL'")
    value: str | None = Field(description="Value next to the label, null when the cell is empty")
    box: VisionBox


class VisionView(BaseModel):
    label: str = Field(description="View caption, e.g. 'SECTION A-A', 'DETAY B'")
    box: VisionBox


class VisionNote(BaseModel):
    text: str
    box: VisionBox


class VisionSheetExtraction(BaseModel):
    """One image (full sheet or tile) transcribed into structured callouts."""

    dimensions: list[VisionDimension] = Field(default_factory=list)
    symbols: list[VisionSymbol] = Field(default_factory=list)
    title_block: list[VisionTitleBlockField] = Field(default_factory=list)
    views: list[VisionView] = Field(default_factory=list)
    notes: list[VisionNote] = Field(default_factory=list)
    illegible_regions: list[VisionBox] = Field(
        default_factory=list,
        description="Areas that carry drawing information but could not be read",
    )
    observations: list[str] = Field(
        default_factory=list,
        description="Short remarks about anything unusual (missing frame, overlapping text, ...)",
    )

    def is_empty(self) -> bool:
        return not (self.dimensions or self.symbols or self.title_block or self.notes)
