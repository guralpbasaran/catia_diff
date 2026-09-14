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
        Category.TOLERANCING: "Tolerancing",
        Category.GDT: "Geometric tolerancing",
        Category.TITLE_BLOCK: "Title block",
        Category.SYMBOLS: "Symbols",
        Category.CONSISTENCY: "Consistency",
        Category.EXTRACTION: "Extraction",
    },
    "tr": {
        Category.DIMENSIONING: "Ölçülendirme",
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
