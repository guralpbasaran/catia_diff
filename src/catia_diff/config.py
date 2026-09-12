"""Runtime configuration for an audit run."""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from catia_diff.models.findings import Category, Severity

#: Default Claude model used for multimodal extraction.  Override with the
#: ``CATIA_DIFF_VISION_MODEL`` environment variable or ``--vision-model``.
DEFAULT_VISION_MODEL = "claude-opus-5"


class Profile(str, Enum):
    """Drawing standard the checkers are calibrated against."""

    ISO = "ISO"
    ASME = "ASME"

    @property
    def aliases(self) -> tuple[str, ...]:
        return ("ISO", "DIN", "EN", "TS") if self is Profile.ISO else ("ASME", "ANSI")


class VisionMode(str, Enum):
    AUTO = "auto"  # only for pages without an extractable vector/text layer
    ON = "on"  # always run the vision pass and merge its output
    OFF = "off"  # never call the model


class VisionConfig(BaseModel):
    mode: VisionMode = VisionMode.AUTO
    model: str = Field(default_factory=lambda: os.getenv("CATIA_DIFF_VISION_MODEL", DEFAULT_VISION_MODEL))
    max_tokens: int = 16000
    effort: str = "high"  # low | medium | high | xhigh | max
    max_images_per_call: int = 4
    tile_overlap: float = 0.08
    timeout_s: float = 300.0

    @property
    def enabled(self) -> bool:
        return self.mode is not VisionMode.OFF


class AuditConfig(BaseModel):
    """Everything the pipeline needs to know beyond the input file itself."""

    profile: Profile = Profile.ISO
    language: str = "tr"
    output_dir: Path = Path("reports")
    formats: tuple[str, ...] = ("json", "md", "html")
    render_overlay: bool = True
    raster_dpi: int = 200

    # Rule selection
    disabled_rules: frozenset[str] = frozenset()
    enabled_rules: frozenset[str] | None = None  # None => all rules of the profile
    categories: frozenset[Category] | None = None
    min_severity: Severity = Severity.INFO
    fail_on: Severity = Severity.MAJOR

    # Engineering assumptions used by the checkers
    assume_general_tolerance: bool = True
    default_units: str = "mm"
    max_findings_per_rule: int = 25
    duplicate_iou_threshold: float = 0.6
    undimensioned_feature_limit: int = 15

    # Dimensional coverage analysis (missing dimensions)
    #: Gap below which two geometry clusters count as one view, as a fraction
    #: of the sheet's larger side.
    view_gap_ratio: float = 0.06
    #: Coordinates closer than this fraction of the view size are one reference.
    node_merge_ratio: float = 1e-3
    #: Strict mode treats every contour vertex as a position that must be
    #: dimensioned; the default only uses feature centres, extents and steps.
    strict_dimensioning: bool = False

    vision: VisionConfig = Field(default_factory=VisionConfig)
    parallel_agents: bool = True
    verbose: bool = False

    @field_validator("language")
    @classmethod
    def _check_language(cls, value: str) -> str:
        value = value.lower()
        if value not in {"tr", "en"}:
            raise ValueError("language must be 'tr' or 'en'")
        return value

    @field_validator("formats", mode="before")
    @classmethod
    def _coerce_formats(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    def rule_enabled(self, rule_id: str, category: Category) -> bool:
        if rule_id in self.disabled_rules:
            return False
        if self.enabled_rules is not None and rule_id not in self.enabled_rules:
            return False
        return not (self.categories is not None and category not in self.categories)

    def keeps(self, severity: Severity) -> bool:
        return severity.rank <= self.min_severity.rank
