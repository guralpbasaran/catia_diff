"""Findings and the audit report produced by the agents."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from catia_diff.models.geometry import BBox


class Severity(str, Enum):
    """Finding severity, ordered from worst to informational."""

    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    INFO = "info"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    def label(self, lang: str = "en") -> str:
        return _SEVERITY_LABELS[lang if lang in _SEVERITY_LABELS else "en"][self]

    def __lt__(self, other: object) -> bool:  # enables sorted() on severities
        if not isinstance(other, Severity):
            return NotImplemented
        return self.rank < other.rank


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.CRITICAL: 0,
    Severity.MAJOR: 1,
    Severity.MINOR: 2,
    Severity.INFO: 3,
}

_SEVERITY_LABELS: dict[str, dict[Severity, str]] = {
    "en": {
        Severity.CRITICAL: "Critical",
        Severity.MAJOR: "Major",
        Severity.MINOR: "Minor",
        Severity.INFO: "Info",
    },
    "tr": {
        Severity.CRITICAL: "Kritik",
        Severity.MAJOR: "Majör",
        Severity.MINOR: "Minör",
        Severity.INFO: "Bilgi",
    },
}

#: One palette for every surface that shows a severity - overlay boxes, the
#: HTML report and the Dash dashboard - so the same defect never changes colour
#: between them.  The steps are not a taste decision: adjacent pairs are kept
#: above the perceptual-separation floor for normal and colour-deficient vision
#: (critical/major used to sit at DE 11.7, close enough to be confused side by
#: side in an overlay).  Severity is always carried by a label as well, never by
#: colour alone.
SEVERITY_COLORS: dict[Severity, str] = {
    Severity.CRITICAL: "#b3001b",
    Severity.MAJOR: "#f3922b",
    Severity.MINOR: "#8f7200",
    Severity.INFO: "#3f88c5",
}


class Category(str, Enum):
    DIMENSIONING = "dimensioning"
    CROSS_VIEW = "cross_view"
    REFERENCE = "reference"
    TOLERANCING = "tolerancing"
    GDT = "gdt"
    TITLE_BLOCK = "title_block"
    SYMBOLS = "symbols"
    CONSISTENCY = "consistency"
    EXTRACTION = "extraction"

    def label(self, lang: str = "en") -> str:
        return _CATEGORY_LABELS[lang if lang in _CATEGORY_LABELS else "en"][self]


_CATEGORY_LABELS: dict[str, dict[Category, str]] = {
    "en": {
        Category.DIMENSIONING: "Dimensioning",
        Category.CROSS_VIEW: "Cross-view",
        Category.REFERENCE: "References",
        Category.TOLERANCING: "Tolerancing",
        Category.GDT: "Geometric tolerancing",
        Category.TITLE_BLOCK: "Title block",
        Category.SYMBOLS: "Symbols",
        Category.CONSISTENCY: "Consistency",
        Category.EXTRACTION: "Extraction",
    },
    "tr": {
        Category.DIMENSIONING: "Ölçülendirme",
        Category.CROSS_VIEW: "Görünüşler arası",
        Category.REFERENCE: "Referanslar",
        Category.TOLERANCING: "Tolerans",
        Category.GDT: "Geometrik tolerans",
        Category.TITLE_BLOCK: "Antet",
        Category.SYMBOLS: "Semboller",
        Category.CONSISTENCY: "Tutarlılık",
        Category.EXTRACTION: "Veri çıkarımı",
    },
}


class Evidence(BaseModel):
    """Where on the drawing a finding is anchored."""

    model_config = ConfigDict(frozen=True)

    sheet_index: int = 0
    bbox: BBox | None = None
    object_ids: tuple[str, ...] = ()
    snippet: str | None = None


class Finding(BaseModel):
    """A single audit finding, always bilingual (EN + TR)."""

    rule_id: str
    severity: Severity
    category: Category
    title: str
    title_tr: str
    message: str
    message_tr: str
    suggestion: str | None = None
    suggestion_tr: str | None = None
    standards: tuple[str, ...] = ()
    evidence: Evidence = Field(default_factory=Evidence)
    confidence: float = 1.0
    agent: str = "unknown"
    id: str = ""

    def model_post_init(self, __context: Any) -> None:
        if not self.id:
            self.id = self.fingerprint()

    def fingerprint(self) -> str:
        payload = "|".join(
            [
                self.rule_id,
                str(self.evidence.sheet_index),
                ",".join(sorted(self.evidence.object_ids)),
                self.message,
            ]
        )
        return f"{self.rule_id}-{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:8]}"

    def localized_title(self, lang: str = "en") -> str:
        return self.title_tr if lang == "tr" and self.title_tr else self.title

    def localized_message(self, lang: str = "en") -> str:
        return self.message_tr if lang == "tr" and self.message_tr else self.message

    def localized_suggestion(self, lang: str = "en") -> str | None:
        if lang == "tr" and self.suggestion_tr:
            return self.suggestion_tr
        return self.suggestion

    def sort_key(self) -> tuple[int, int, str, str]:
        return (self.severity.rank, self.evidence.sheet_index, self.rule_id, self.id)


class CoverageGap(BaseModel):
    """One dimension the drawing does not have, and where it would run.

    The overlay draws it as a dashed line: from a coordinate the drawing already
    controls to the one it leaves free.  That is the most direct way to say what
    is missing - the reader sees the dimension that should be there.
    """

    model_config = ConfigDict(frozen=True)

    #: The free coordinate, projected onto the axis.
    coordinate: float
    #: The nearest coordinate the view already controls, or ``None`` when the
    #: view constrains nothing at all on this axis.
    anchor: float | None = None
    #: The free geometry, so the line can be drawn beside it.
    bbox: BBox | None = None
    #: The features sitting on the free coordinate - the same ids the finding
    #: carries, which is how the two layers are kept in step.
    feature_ids: tuple[str, ...] = ()


class AxisCoverage(BaseModel):
    """How well one view is dimensioned along one measuring direction."""

    model_config = ConfigDict(frozen=True)

    #: "X", "Y" or the angle of an oblique measuring direction.
    axis: str
    #: The missing dimensions the audit reported; ``len`` is the shortfall.
    gaps: tuple[CoverageGap, ...] = ()
    #: Dimensions more than this axis needs - each one closes a cycle.
    redundant: int = 0
    #: True when an orthographically aligned view already fixes this axis, so
    #: nothing is missing here even if this view alone does not constrain it.
    inherited: bool = False

    @property
    def missing(self) -> int:
        return len(self.gaps)

    @property
    def is_complete(self) -> bool:
        return not self.gaps and not self.redundant


class ViewCoverage(BaseModel):
    """Dimensional coverage of one view, axis by axis."""

    model_config = ConfigDict(frozen=True)

    sheet_index: int = 0
    view_id: str
    label: str | None = None
    axes: tuple[AxisCoverage, ...] = ()

    @property
    def is_complete(self) -> bool:
        return all(axis.is_complete for axis in self.axes)

    def name(self, lang: str = "en") -> str:
        if self.label:
            return self.label
        number = self.view_id.removeprefix("VIEW").lstrip("0") or self.view_id
        return f"Görünüş {number}" if lang == "tr" else f"View {number}"


class AuditReport(BaseModel):
    """Aggregated result of one audit run."""

    document: Path
    source_format: str = "unknown"
    profile: str = "ISO"
    language: str = "en"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    findings: list[Finding] = Field(default_factory=list)
    document_stats: dict[str, int] = Field(default_factory=dict)
    agent_traces: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    overlays: list[Path] = Field(default_factory=list)
    rules_executed: int = 0
    duration_ms: float = 0.0
    #: Constraint-graph coverage per view - the same numbers the overlay draws
    #: and the HTML table prints, derived once.
    coverage: list[ViewCoverage] = Field(default_factory=list)
    #: True when the file could not be read at all - an empty finding list
    #: then means "not audited", not "clean".
    extraction_failed: bool = False

    # -- queries ------------------------------------------------------------
    def counts_by_severity(self) -> dict[Severity, int]:
        counter = Counter(f.severity for f in self.findings)
        return {sev: counter.get(sev, 0) for sev in Severity}

    def counts_by_category(self) -> dict[Category, int]:
        counter = Counter(f.category for f in self.findings)
        return {cat: counter.get(cat, 0) for cat in Category if counter.get(cat, 0)}

    def by_severity(self, severity: Severity) -> list[Finding]:
        return [f for f in self.findings if f.severity is severity]

    def for_sheet(self, index: int) -> list[Finding]:
        return [f for f in self.findings if f.evidence.sheet_index == index]

    @property
    def worst_severity(self) -> Severity | None:
        return min((f.severity for f in self.findings), key=lambda s: s.rank, default=None)

    def is_clean(self, threshold: Severity = Severity.MINOR) -> bool:
        return all(f.severity.rank > threshold.rank for f in self.findings)

    def summary_line(self, lang: str = "en") -> str:
        counts = self.counts_by_severity()
        parts = [f"{sev.label(lang)}: {counts[sev]}" for sev in Severity if counts[sev]]
        if not parts:
            return "No findings" if lang != "tr" else "Bulgu yok"
        return " | ".join(parts)
