"""Title-block checks (ISO 7200 / ASME Y14.1 data fields)."""

from __future__ import annotations

import re
from collections.abc import Iterable

from catia_diff import fields as F
from catia_diff.models.drawing import DrawingDocument, ProjectionMethod, Sheet
from catia_diff.models.findings import Category, Finding, Severity
from catia_diff.rules.base import Rule, RuleContext, RuleMeta, register

AGENT = "title_block"

#: Field -> severity when the field is missing (ISO 7200 mandatory data fields
#: are MAJOR or worse; administrative fields are MINOR).
REQUIRED_FIELDS: dict[str, Severity] = {
    F.DRAWING_NUMBER: Severity.CRITICAL,
    F.TITLE: Severity.MAJOR,
    F.SCALE: Severity.MAJOR,
    F.MATERIAL: Severity.MAJOR,
    F.REVISION: Severity.MAJOR,
    F.DRAWN_BY: Severity.MINOR,
    F.APPROVED_BY: Severity.MINOR,
    F.SHEET: Severity.MINOR,
    F.COMPANY: Severity.INFO,
}

_SCALE_RE = re.compile(
    r"^\s*(?:1\s*[:/]\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*[:/]\s*1|N\.?T\.?S\.?)\s*$",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"\d{1,4}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2}")


@register
class TitleBlockMissingRule(Rule):
    meta = RuleMeta(
        id="TB001",
        title="Title block not found",
        title_tr="Antet bulunamadı",
        severity=Severity.CRITICAL,
        category=Category.TITLE_BLOCK,
        standards=("ISO 7200", "ASME Y14.1"),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        if target.title_block.detected and target.title_block.fields:
            return
        if target.is_empty():
            return
        yield self.finding(
            message="No title block could be identified on this sheet.",
            message_tr="Bu sayfada antet tespit edilemedi.",
            suggestion=(
                "Add a title block per ISO 7200. If one exists, check that its fields are real "
                "text/attributes rather than exploded geometry."
            ),
            suggestion_tr=(
                "ISO 7200'e uygun bir antet ekleyin. Antet varsa, alanlarının patlatılmış "
                "geometri değil gerçek metin/öznitelik olduğunu kontrol edin."
            ),
            sheet_index=target.index,
            agent=AGENT,
        )


@register
class MissingFieldRule(Rule):
    meta = RuleMeta(
        id="TB002",
        title="Mandatory title-block field is empty",
        title_tr="Zorunlu antet alanı boş",
        severity=Severity.MAJOR,
        category=Category.TITLE_BLOCK,
        standards=("ISO 7200 §5", "ASME Y14.1"),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        block = target.title_block
        if not block.detected:
            return  # TB001 already reports the missing block
        for field, severity in REQUIRED_FIELDS.items():
            if block.has(field):
                continue
            label_en = F.field_label(field, "en")
            label_tr = F.field_label(field, "tr")
            yield self.finding(
                message=f"Title-block field '{label_en}' is missing or empty.",
                message_tr=f"'{label_tr}' antet alanı eksik veya boş.",
                suggestion=f"Fill in '{label_en}' in the title block.",
                suggestion_tr=f"Antetteki '{label_tr}' alanını doldurun.",
                sheet_index=target.index,
                bbox=block.bbox,
                object_ids=[f"titleblock.{field}"],
                severity=severity,
                confidence=0.85,
                agent=AGENT,
            )


@register
class PlaceholderValueRule(Rule):
    meta = RuleMeta(
        id="TB003",
        title="Placeholder left in the title block",
        title_tr="Antette doldurulmamış yer tutucu",
        severity=Severity.MAJOR,
        category=Category.TITLE_BLOCK,
        standards=("ISO 7200 §5",),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for name, field in target.title_block.fields.items():
            if not field.is_placeholder:
                continue
            yield self.finding(
                message=f"Field '{F.field_label(name, 'en')}' still holds the placeholder '{field.value}'.",
                message_tr=f"'{F.field_label(name, 'tr')}' alanında hâlâ '{field.value}' yer tutucusu var.",
                suggestion="Replace the placeholder with the real value before release.",
                suggestion_tr="Yayınlamadan önce yer tutucuyu gerçek değerle değiştirin.",
                sheet_index=target.index,
                bbox=field.bbox or target.title_block.bbox,
                object_ids=[f"titleblock.{name}"],
                agent=AGENT,
            )


@register
class ScaleFormatRule(Rule):
    meta = RuleMeta(
        id="TB004",
        title="Scale is not written in a standard form",
        title_tr="Ölçek standart biçimde yazılmamış",
        severity=Severity.MINOR,
        category=Category.TITLE_BLOCK,
        standards=("ISO 5455", "ASME Y14.1 §3.3"),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        scale = target.title_block.value(F.SCALE)
        if not scale or _SCALE_RE.match(scale):
            return
        yield self.finding(
            message=f"Scale '{scale}' is not a standard ratio such as 1:1, 1:2 or 2:1.",
            message_tr=f"'{scale}' ölçeği 1:1, 1:2 veya 2:1 gibi standart bir orana uymuyor.",
            suggestion="Write the scale as a ratio (ISO 5455), e.g. 1:2.",
            suggestion_tr="Ölçeği oran biçiminde yazın (ISO 5455), örn. 1:2.",
            sheet_index=target.index,
            bbox=target.title_block.bbox,
            object_ids=["titleblock.scale"],
            agent=AGENT,
        )


@register
class RevisionWithoutDateRule(Rule):
    meta = RuleMeta(
        id="TB005",
        title="Revision without a date",
        title_tr="Tarihi olmayan revizyon",
        severity=Severity.MINOR,
        category=Category.TITLE_BLOCK,
        standards=("ISO 7200 §5.4",),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        block = target.title_block
        revision = block.value(F.REVISION)
        if not revision:
            return
        if revision.strip() in {"0", "-", "00", "A0"}:
            return
        if block.value(F.REVISION_DATE) or block.value(F.DRAWN_DATE):
            return
        if _DATE_RE.search(target.notes_text()):
            return
        yield self.finding(
            message=f"Revision '{revision}' is stated without a revision date.",
            message_tr=f"'{revision}' revizyonu için revizyon tarihi belirtilmemiş.",
            suggestion="Add the revision date so the change history stays traceable.",
            suggestion_tr="Değişiklik geçmişinin izlenebilir olması için revizyon tarihini ekleyin.",
            sheet_index=target.index,
            bbox=block.bbox,
            object_ids=["titleblock.revision"],
            agent=AGENT,
        )


@register
class ApproverEqualsDrafterRule(Rule):
    meta = RuleMeta(
        id="TB006",
        title="Drawing approved by its own author",
        title_tr="Resmi çizen kişi onaylamış",
        severity=Severity.MINOR,
        category=Category.TITLE_BLOCK,
        standards=("ISO 7200 §5.3",),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        block = target.title_block
        drawn = (block.value(F.DRAWN_BY) or "").strip().lower()
        approved = (block.value(F.APPROVED_BY) or "").strip().lower()
        if not drawn or not approved or drawn != approved:
            return
        yield self.finding(
            message=f"'{block.value(F.DRAWN_BY)}' appears as both the author and the approver.",
            message_tr=f"'{block.value(F.DRAWN_BY)}' hem çizen hem onaylayan olarak görünüyor.",
            suggestion="Have a second person check and approve the drawing.",
            suggestion_tr="Resmi ikinci bir kişinin kontrol edip onaylaması gerekir.",
            sheet_index=target.index,
            bbox=block.bbox,
            object_ids=["titleblock.approved_by"],
            agent=AGENT,
        )


@register
class ProjectionMethodRule(Rule):
    meta = RuleMeta(
        id="TB007",
        title="Projection method not stated",
        title_tr="İzdüşüm yöntemi belirtilmemiş",
        severity=Severity.MAJOR,
        category=Category.TITLE_BLOCK,
        standards=("ISO 128-30", "ASME Y14.3"),
        description="First- and third-angle drawings are mirror images of each other.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        if target.projection is not ProjectionMethod.UNKNOWN:
            return
        if target.title_block.has(F.PROJECTION):
            return
        if len(target.views) < 2 and len(target.dimensions) < 3:
            return  # single-view sheets do not need the symbol
        yield self.finding(
            message="The sheet does not state whether it uses first- or third-angle projection.",
            message_tr="Sayfada birinci mi yoksa üçüncü açı izdüşümü mü kullanıldığı belirtilmemiş.",
            suggestion="Place the projection symbol (ISO 128-30) next to the title block.",
            suggestion_tr="İzdüşüm sembolünü (ISO 128-30) antedin yanına yerleştirin.",
            sheet_index=target.index,
            bbox=target.title_block.bbox,
            confidence=0.8,
            agent=AGENT,
        )


@register
class GeneralToleranceNoteRule(Rule):
    meta = RuleMeta(
        id="TB008",
        title="General tolerance note missing",
        title_tr="Genel tolerans notu eksik",
        severity=Severity.MAJOR,
        category=Category.TITLE_BLOCK,
        standards=("ISO 2768-1 §4", "ASME Y14.5-2018 §2.1"),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        if ctx.has_general_tolerance(target) or target.is_empty():
            return
        if all(dim.has_tolerance for dim in target.dimensions) and target.dimensions:
            return  # every dimension carries its own tolerance
        yield self.finding(
            message="The sheet states no general tolerance for untoleranced dimensions.",
            message_tr="Sayfada toleranssız ölçüler için genel tolerans notu yok.",
            suggestion="Add a note such as 'General tolerances ISO 2768-mK' to the title block area.",
            suggestion_tr="Antet bölgesine 'Genel toleranslar ISO 2768-mK' gibi bir not ekleyin.",
            sheet_index=target.index,
            bbox=target.title_block.bbox,
            agent=AGENT,
        )


@register
class UnitsNotStatedRule(Rule):
    meta = RuleMeta(
        id="TB009",
        title="Units of measurement not stated",
        title_tr="Ölçü birimi belirtilmemiş",
        severity=Severity.MAJOR,
        category=Category.TITLE_BLOCK,
        standards=("ISO 7200 §5", "ASME Y14.5-2018 §1.6"),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        if target.title_block.has(F.UNITS) or target.is_empty():
            return
        notes = target.notes_text().upper()
        if "MM" in notes or "INCH" in notes or "MILLIMET" in notes:
            return
        yield self.finding(
            message="Neither the title block nor the notes state the unit of measurement.",
            message_tr="Ne antette ne de notlarda ölçü birimi belirtilmiş.",
            suggestion="Add 'ALL DIMENSIONS IN MM' (or the equivalent) to the notes.",
            suggestion_tr="Notlara 'ÖLÇÜLER MM CİNSİNDENDİR' ifadesini ekleyin.",
            sheet_index=target.index,
            bbox=target.title_block.bbox,
            agent=AGENT,
        )


@register
class SheetNumberingRule(Rule):
    meta = RuleMeta(
        id="TB010",
        title="Sheet numbering inconsistent",
        title_tr="Sayfa numaralandırması tutarsız",
        severity=Severity.MINOR,
        category=Category.TITLE_BLOCK,
        standards=("ISO 7200 §5.2",),
    )
    scope = "document"

    def check(self, target: DrawingDocument, ctx: RuleContext) -> Iterable[Finding]:
        total = len(target.sheets)
        for sheet in target.sheets:
            stated = sheet.title_block.value(F.SHEET)
            if not stated:
                continue
            match = re.search(r"(\d+)\s*(?:/|of|de)\s*(\d+)", stated, re.IGNORECASE)
            if not match:
                continue
            current, declared_total = int(match.group(1)), int(match.group(2))
            problems = []
            if declared_total != total:
                problems.append(
                    (
                        f"declares {declared_total} sheets while the file contains {total}",
                        f"{declared_total} sayfa olduğunu belirtiyor ancak dosyada {total} sayfa var",
                    )
                )
            if current != sheet.index + 1:
                problems.append(
                    (
                        f"is numbered {current} but is page {sheet.index + 1} of the file",
                        f"{current} olarak numaralanmış ancak dosyanın {sheet.index + 1}. sayfası",
                    )
                )
            for message_en, message_tr in problems:
                yield self.finding(
                    message=f"Sheet '{stated}' {message_en}.",
                    message_tr=f"'{stated}' sayfası {message_tr}.",
                    suggestion="Renumber the sheets as 'n / total'.",
                    suggestion_tr="Sayfaları 'n / toplam' biçiminde yeniden numaralandırın.",
                    sheet_index=sheet.index,
                    bbox=sheet.title_block.bbox,
                    object_ids=["titleblock.sheet"],
                    agent=AGENT,
                )


@register
class DrawingNumberFilenameRule(Rule):
    meta = RuleMeta(
        id="TB011",
        title="Drawing number does not match the file name",
        title_tr="Resim numarası dosya adıyla uyuşmuyor",
        severity=Severity.MINOR,
        category=Category.TITLE_BLOCK,
        standards=("ISO 7200 §5.1",),
    )
    scope = "document"

    def check(self, target: DrawingDocument, ctx: RuleContext) -> Iterable[Finding]:
        stem = _normalise_id(target.source_path.stem)
        if not stem:
            return
        for sheet in target.sheets:
            number = sheet.title_block.value(F.DRAWING_NUMBER)
            if not number:
                continue
            normalised = _normalise_id(number)
            if not normalised or normalised in stem or stem in normalised:
                return
        numbers = {
            sheet.title_block.value(F.DRAWING_NUMBER)
            for sheet in target.sheets
            if sheet.title_block.value(F.DRAWING_NUMBER)
        }
        if not numbers:
            return
        yield self.finding(
            message=(
                f"Drawing number {', '.join(sorted(numbers))} does not appear in the file name "
                f"'{target.source_path.name}'."
            ),
            message_tr=(
                f"{', '.join(sorted(numbers))} resim numarası '{target.source_path.name}' dosya "
                "adında geçmiyor."
            ),
            suggestion="Align the file name with the drawing number to keep releases traceable.",
            suggestion_tr="İzlenebilirlik için dosya adını resim numarasıyla eşleştirin.",
            sheet_index=0,
            confidence=0.7,
            agent=AGENT,
        )


def _normalise_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())
