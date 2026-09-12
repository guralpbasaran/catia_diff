"""Cross-cutting consistency checks (document scope)."""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable

from catia_diff import fields as F
from catia_diff.models.drawing import DrawingDocument, Sheet, SourceFormat, Units
from catia_diff.models.findings import Category, Finding, Severity
from catia_diff.rules.base import Rule, RuleContext, RuleMeta, register

AGENT = "consistency"


@register
class MixedUnitsRule(Rule):
    meta = RuleMeta(
        id="CON001",
        title="Mixed units on one sheet",
        title_tr="Aynı sayfada karışık birimler",
        severity=Severity.CRITICAL,
        category=Category.CONSISTENCY,
        standards=("ISO 129-1 §4.4", "ASME Y14.5-2018 §1.6"),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        used = Counter(
            dim.units
            for dim in target.dimensions
            if dim.units.is_length and dim.units is not Units.UNKNOWN
        )
        if len(used) < 2:
            return
        rendered = ", ".join(f"{unit.value} ({count})" for unit, count in used.most_common())
        yield self.finding(
            message=f"Length dimensions use more than one unit: {rendered}.",
            message_tr=f"Uzunluk ölçüleri birden fazla birim kullanıyor: {rendered}.",
            suggestion="Use one unit for the whole sheet and state it in the title block.",
            suggestion_tr="Tüm sayfada tek birim kullanın ve bunu antette belirtin.",
            sheet_index=target.index,
            object_ids=[dim.id for dim in target.dimensions if dim.units.is_length][:20],
            agent=AGENT,
        )


@register
class ScaleMismatchRule(Rule):
    meta = RuleMeta(
        id="CON002",
        title="Stated scale disagrees with the drawn geometry",
        title_tr="Belirtilen ölçek çizilen geometriyle uyuşmuyor",
        severity=Severity.MAJOR,
        category=Category.CONSISTENCY,
        standards=("ISO 5455",),
        description="Only checkable on vector input, where the true measurement is known.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        if ctx.document.source_format is not SourceFormat.DXF or not target.stated_scale:
            return
        stated = target.scale
        if not stated or stated <= 0:
            return
        ratios = [
            dim.nominal / dim.measured
            for dim in target.dimensions
            if dim.nominal and dim.measured and dim.measured > 0 and not dim.is_text_override
        ]
        if len(ratios) < 3:
            return
        drawn = statistics.median(ratios)
        # In model space the geometry is 1:1 and the *plot* carries the scale;
        # a systematic deviation therefore means the values were scaled by hand.
        if abs(drawn - 1.0) <= 0.02:
            return
        yield self.finding(
            message=(
                f"Dimension values are on average {drawn:.3g}x the modelled geometry while the "
                f"title block states '{target.stated_scale}'."
            ),
            message_tr=(
                f"Ölçü değerleri modellenen geometrinin ortalama {drawn:.3g} katı; antette ise "
                f"'{target.stated_scale}' yazıyor."
            ),
            suggestion=(
                "Check DIMLFAC / the plot scale: dimension values must report true size, "
                "regardless of the sheet scale."
            ),
            suggestion_tr=(
                "DIMLFAC / baskı ölçeğini kontrol edin: sayfa ölçeğinden bağımsız olarak ölçü "
                "değerleri gerçek boyutu göstermelidir."
            ),
            sheet_index=target.index,
            confidence=0.7,
            agent=AGENT,
        )


@register
class DrawingNumberConflictRule(Rule):
    meta = RuleMeta(
        id="CON003",
        title="Sheets carry different drawing numbers",
        title_tr="Sayfalarda farklı resim numaraları var",
        severity=Severity.MAJOR,
        category=Category.CONSISTENCY,
        standards=("ISO 7200 §5.1",),
    )
    scope = "document"

    def check(self, target: DrawingDocument, ctx: RuleContext) -> Iterable[Finding]:
        numbers = {
            sheet.index: sheet.title_block.value(F.DRAWING_NUMBER)
            for sheet in target.sheets
            if sheet.title_block.value(F.DRAWING_NUMBER)
        }
        distinct = set(numbers.values())
        if len(distinct) < 2:
            return
        yield self.finding(
            message=f"The sheets of one file declare different drawing numbers: {', '.join(sorted(distinct))}.",
            message_tr=f"Tek bir dosyanın sayfaları farklı resim numaraları bildiriyor: {', '.join(sorted(distinct))}.",
            suggestion="Use one drawing number per document and distinguish sheets with 'n / total'.",
            suggestion_tr="Belge başına tek resim numarası kullanın; sayfaları 'n / toplam' ile ayırın.",
            sheet_index=0,
            agent=AGENT,
        )


@register
class RevisionMentionRule(Rule):
    meta = RuleMeta(
        id="CON004",
        title="Revision mentioned on the sheet but not in the title block",
        title_tr="Sayfada revizyon anılmış ancak antette yok",
        severity=Severity.MINOR,
        category=Category.CONSISTENCY,
        standards=("ISO 7200 §5.4",),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        mentions = [note for note in target.annotations if note.category == "revision"]
        if not mentions or target.title_block.has(F.REVISION):
            return
        yield self.finding(
            message=(
                f"{len(mentions)} revision note(s) appear on the sheet, but the title block has "
                "no revision entry."
            ),
            message_tr=(
                f"Sayfada {len(mentions)} revizyon notu var ancak antette revizyon kaydı yok."
            ),
            suggestion="Record the revision (index and date) in the title block.",
            suggestion_tr="Revizyonu (indis ve tarih) antette kaydedin.",
            sheet_index=target.index,
            bbox=mentions[0].bbox,
            object_ids=[note.id for note in mentions],
            agent=AGENT,
        )


@register
class DegradedExtractionRule(Rule):
    meta = RuleMeta(
        id="CON005",
        title="Sheet could not be read without vision extraction",
        title_tr="Sayfa görsel çıkarım olmadan okunamadı",
        severity=Severity.MAJOR,
        category=Category.EXTRACTION,
        standards=(),
        description="Reported so a clean report is never mistaken for a clean drawing.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        if not target.needs_vision:
            return
        if ctx.document.vision_used:
            return
        yield self.finding(
            message=(
                "This sheet has no text layer (scan) and no vision pass ran, so its callouts "
                "were not audited."
            ),
            message_tr=(
                "Bu sayfada metin katmanı yok (tarama) ve görsel çıkarım çalışmadı; bu nedenle "
                "gösterimleri denetlenmedi."
            ),
            suggestion="Re-run with --vision on (and ANTHROPIC_API_KEY set) or supply a vector file.",
            suggestion_tr=(
                "--vision on ile (ANTHROPIC_API_KEY tanımlıyken) yeniden çalıştırın veya vektörel "
                "bir dosya sağlayın."
            ),
            sheet_index=target.index,
            agent=AGENT,
        )


@register
class EmptyDocumentRule(Rule):
    meta = RuleMeta(
        id="CON006",
        title="Nothing could be extracted from the file",
        title_tr="Dosyadan hiçbir veri çıkarılamadı",
        severity=Severity.CRITICAL,
        category=Category.EXTRACTION,
        standards=(),
    )
    scope = "document"

    def check(self, target: DrawingDocument, ctx: RuleContext) -> Iterable[Finding]:
        if not target.sheets or all(sheet.is_empty() for sheet in target.sheets):
            yield self.finding(
                message=f"No drawing content was extracted from '{target.source_path.name}'.",
                message_tr=f"'{target.source_path.name}' dosyasından hiçbir çizim içeriği çıkarılamadı.",
                suggestion="Check the file: it may be empty, encrypted, or in an unsupported flavour.",
                suggestion_tr="Dosyayı kontrol edin: boş, şifreli veya desteklenmeyen bir türde olabilir.",
                sheet_index=0,
                agent=AGENT,
            )
