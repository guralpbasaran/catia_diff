"""View models for the dashboard.

Pure functions over an :class:`AuditReport`: no Dash import, no file IO, no
network.  The whole presentation layer is therefore testable without starting a
server, and the callbacks in :mod:`catia_diff.ui.app` stay thin enough to read
in one screen.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from catia_diff.models.findings import (
    SEVERITY_COLORS,
    AuditReport,
    Category,
    Finding,
    Severity,
)
from catia_diff.reporting.normalize import overlay_numbers

# ---------------------------------------------------------------------------
# UI copy.  Findings are already bilingual in the model; these are the labels
# around them.
TEXT: dict[str, dict[str, str]] = {
    "title": {"tr": "Teknik resim denetimi", "en": "Technical drawing audit"},
    "subtitle": {
        "tr": "DXF · PDF · taranmış resim → eksik, hatalı ve tutarsız gösterimler",
        "en": "DXF · PDF · scan → missing, wrong and inconsistent callouts",
    },
    "drop": {
        "tr": "Resmi buraya sürükleyin veya **seçmek için tıklayın**",
        "en": "Drop a drawing here or **click to choose one**",
    },
    "drop_hint": {
        "tr": "DXF, PDF, PNG, JPG, TIFF · en çok {size} MB",
        "en": "DXF, PDF, PNG, JPG, TIFF · up to {size} MB",
    },
    "sample": {"tr": "Örnek resmi dene", "en": "Try the sample drawing"},
    "profile": {"tr": "Standart profili", "en": "Standard profile"},
    "language": {"tr": "Dil", "en": "Language"},
    "gate": {"tr": "Serbest bırakma eşiği", "en": "Release threshold"},
    "running": {"tr": "Denetleniyor…", "en": "Auditing…"},
    "idle": {
        "tr": "Denetlemek için bir dosya yükleyin.",
        "en": "Upload a file to start an audit.",
    },
    "filter_severity": {"tr": "Önem", "en": "Severity"},
    "filter_category": {"tr": "Kategori", "en": "Category"},
    "filter_all": {"tr": "tümü", "en": "all"},
    "tab_findings": {"tr": "Bulgular", "en": "Findings"},
    "tab_overlay": {"tr": "İşaretli resim", "en": "Marked-up sheet"},
    "tab_document": {"tr": "Belge", "en": "Document"},
    "col_number": {"tr": "No", "en": "No"},
    "col_rule": {"tr": "Kural", "en": "Rule"},
    "col_severity": {"tr": "Önem", "en": "Severity"},
    "col_category": {"tr": "Kategori", "en": "Category"},
    "col_sheet": {"tr": "Sayfa", "en": "Sheet"},
    "col_object": {"tr": "Nesne", "en": "Object"},
    "col_message": {"tr": "Bulgu", "en": "Finding"},
    "detail_pick": {
        "tr": "Ayrıntı için tablodan bir satır seçin.",
        "en": "Pick a row to see the detail.",
    },
    "detail_message": {"tr": "Bulgu", "en": "Finding"},
    "detail_suggestion": {"tr": "Önerilen düzeltme", "en": "Suggested fix"},
    "detail_standard": {"tr": "Standart", "en": "Standard"},
    "detail_confidence": {"tr": "Güven", "en": "Confidence"},
    "detail_location": {"tr": "Konum", "en": "Location"},
    "detail_box": {"tr": "Resimdeki kutu", "en": "Box on the sheet"},
    "chart_title": {"tr": "Kategoriye göre bulgular", "en": "Findings by category"},
    "no_findings": {"tr": "Bu filtrede bulgu yok.", "en": "No findings for this filter."},
    "no_overlay": {
        "tr": "Bu resim için işaretli görüntü üretilemedi.",
        "en": "No marked-up image could be rendered for this drawing.",
    },
    "downloads": {"tr": "Raporu indir", "en": "Download report"},
    "stats": {"tr": "Çıkarılan nesneler", "en": "Extracted objects"},
    "warnings": {"tr": "Uyarılar", "en": "Warnings"},
    "rules_executed": {"tr": "Çalıştırılan kural", "en": "Rules executed"},
    "duration": {"tr": "Süre", "en": "Duration"},
    "source_format": {"tr": "Kaynak format", "en": "Source format"},
    "gate_pass": {
        "tr": "Eşiğin üstünde bulgu yok — serbest bırakılabilir (çıkış kodu 0).",
        "en": "Nothing at or above the threshold — releasable (exit code 0).",
    },
    "gate_block": {
        "tr": "{count} bulgu eşiği aşıyor — serbest bırakmayın (çıkış kodu 1).",
        "en": "{count} finding(s) at or above the threshold — do not release (exit code 1).",
    },
    "gate_failed": {
        "tr": "Dosya okunamadı; boş bulgu listesi 'temiz resim' anlamına gelmez (çıkış kodu 2).",
        "en": "The file could not be read; an empty finding list does not mean a clean drawing "
        "(exit code 2).",
    },
}

#: Labels for the extracted-object counters the pipeline reports.
STAT_LABELS: dict[str, dict[str, str]] = {
    "sheets": {"tr": "Sayfa", "en": "Sheets"},
    "dimensions": {"tr": "Ölçü", "en": "Dimensions"},
    "geometric_tolerances": {"tr": "Geometrik tolerans", "en": "Geometric tolerances"},
    "datums": {"tr": "Datum", "en": "Datums"},
    "surface_finishes": {"tr": "Yüzey sembolü", "en": "Surface finishes"},
    "welds": {"tr": "Kaynak sembolü", "en": "Welds"},
    "annotations": {"tr": "Not", "en": "Annotations"},
    "features": {"tr": "Unsur", "en": "Features"},
    "outline_entities": {"tr": "Kontur", "en": "Outline entities"},
    "views": {"tr": "Görünüş", "en": "Views"},
}


def t(key: str, lang: str = "tr", **fmt: object) -> str:
    """Localised UI string; unknown languages fall back to English."""
    entry = TEXT.get(key)
    if entry is None:
        return key
    value = entry.get(lang, entry["en"])
    return value.format(**fmt) if fmt else value


def stat_label(key: str, lang: str = "tr") -> str:
    entry = STAT_LABELS.get(key)
    return entry.get(lang, entry["en"]) if entry else key


# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SeverityCard:
    """One counter tile."""

    severity: Severity
    label: str
    count: int
    color: str


@dataclass(frozen=True)
class FindingRow:
    """One row of the findings table, already localised."""

    id: str
    number: int | None
    rule_id: str
    severity: Severity
    severity_label: str
    category: Category
    category_label: str
    sheet: int
    objects: str
    title: str
    message: str
    suggestion: str | None
    standards: str
    confidence: float
    color: str

    def as_table_row(self) -> dict[str, object]:
        return {
            "id": self.id,
            "number": self.number if self.number is not None else "—",
            "rule_id": self.rule_id,
            "severity": self.severity_label,
            "severity_key": self.severity.value,
            "category": self.category_label,
            "sheet": self.sheet + 1,
            "objects": self.objects or "—",
            "message": self.message,
        }


@dataclass(frozen=True)
class ChartSeries:
    severity: Severity
    label: str
    color: str
    values: tuple[int, ...]


@dataclass(frozen=True)
class CategoryChart:
    """Findings per category, split by severity - the data behind the bar chart."""

    categories: tuple[str, ...]
    series: tuple[ChartSeries, ...]

    @property
    def is_empty(self) -> bool:
        return not self.categories


@dataclass(frozen=True)
class GateStatus:
    """Mirrors the CLI exit code so the screen and the pipeline agree."""

    ok: bool
    exit_code: int
    text: str


# ---------------------------------------------------------------------------
def severity_cards(report: AuditReport, lang: str = "tr") -> list[SeverityCard]:
    """One tile per severity, zeros included - a zero is information too."""
    counts = report.counts_by_severity()
    return [
        SeverityCard(
            severity=severity,
            label=severity.label(lang),
            count=counts[severity],
            color=SEVERITY_COLORS[severity],
        )
        for severity in Severity
    ]


def finding_rows(
    report: AuditReport,
    lang: str = "tr",
    *,
    severities: Iterable[Severity] | None = None,
    categories: Iterable[Category] | None = None,
) -> list[FindingRow]:
    """Localised rows, filtered but never re-ordered.

    The numbering comes from :func:`overlay_numbers`, so row *n* in the table is
    box *n* on the marked-up sheet; a finding the renderer cannot place (no
    bounding box) keeps ``None``.
    """
    wanted_sev = set(severities) if severities is not None else None
    wanted_cat = set(categories) if categories is not None else None
    numbers = overlay_numbers(report.findings)

    rows: list[FindingRow] = []
    for finding in report.findings:
        if wanted_sev is not None and finding.severity not in wanted_sev:
            continue
        if wanted_cat is not None and finding.category not in wanted_cat:
            continue
        rows.append(_row(finding, numbers.get(finding.id), lang))
    return rows


def _row(finding: Finding, number: int | None, lang: str) -> FindingRow:
    return FindingRow(
        id=finding.id,
        number=number,
        rule_id=finding.rule_id,
        severity=finding.severity,
        severity_label=finding.severity.label(lang),
        category=finding.category,
        category_label=finding.category.label(lang),
        sheet=finding.evidence.sheet_index,
        objects=", ".join(finding.evidence.object_ids),
        title=finding.localized_title(lang),
        message=finding.localized_message(lang),
        suggestion=finding.localized_suggestion(lang),
        standards=", ".join(finding.standards),
        confidence=finding.confidence,
        color=SEVERITY_COLORS[finding.severity],
    )


def row_by_id(rows: Sequence[FindingRow], finding_id: str | None) -> FindingRow | None:
    if not finding_id:
        return None
    return next((row for row in rows if row.id == finding_id), None)


def category_chart(rows: Sequence[FindingRow], lang: str = "tr") -> CategoryChart:
    """Stacked-bar data: categories that actually have findings, worst first.

    Severities with no finding anywhere are dropped so the legend never carries
    an empty entry.
    """
    per_category: dict[Category, dict[Severity, int]] = {}
    for row in rows:
        per_category.setdefault(row.category, dict.fromkeys(Severity, 0))
        per_category[row.category][row.severity] += 1
    if not per_category:
        return CategoryChart(categories=(), series=())

    ordered = sorted(
        per_category,
        key=lambda cat: (
            -sum(per_category[cat].values()),
            min((sev.rank for sev, n in per_category[cat].items() if n), default=99),
        ),
    )
    used = [sev for sev in Severity if any(counts[sev] for counts in per_category.values())]
    series = tuple(
        ChartSeries(
            severity=severity,
            label=severity.label(lang),
            color=SEVERITY_COLORS[severity],
            values=tuple(per_category[cat][severity] for cat in ordered),
        )
        for severity in used
    )
    return CategoryChart(
        categories=tuple(cat.label(lang) for cat in ordered),
        series=series,
    )


def gate_status(
    report: AuditReport, threshold: Severity = Severity.CRITICAL, lang: str = "tr"
) -> GateStatus:
    """What the release gate would decide, with the CLI's own exit code."""
    if report.extraction_failed:
        return GateStatus(ok=False, exit_code=2, text=t("gate_failed", lang))
    blocking = [f for f in report.findings if f.severity.rank <= threshold.rank]
    if blocking:
        return GateStatus(
            ok=False, exit_code=1, text=t("gate_block", lang, count=len(blocking))
        )
    return GateStatus(ok=True, exit_code=0, text=t("gate_pass", lang))


def document_stats(report: AuditReport, lang: str = "tr") -> list[tuple[str, str]]:
    """Label/value pairs describing what was read out of the file."""
    rows = [(stat_label(key, lang), str(value)) for key, value in report.document_stats.items()]
    rows.append((t("source_format", lang), report.source_format))
    rows.append((t("rules_executed", lang), str(report.rules_executed)))
    rows.append((t("duration", lang), f"{report.duration_ms:.0f} ms"))
    return rows

