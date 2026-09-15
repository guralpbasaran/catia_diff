"""Reference integrity: does every pointer on the drawing land on something?

A drawing is a small network of cross-references.  A cutting plane points at a
section view; a note points at another note; a caption points back at the marker
it came from.  Each of those is a *use* that must resolve to exactly one
*declaration* - and when it does not, the reader cannot finish the job: they are
sent to a view that is not drawn, or told to obey a note that was never written.

Nothing here guesses.  Where the drawing's style puts a check out of reach - no
marker could be recognised at all, a note is referred to in prose rather than by
number - the rule stays silent instead of firing on every sheet of that style.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from catia_diff.extract.text_parsing import (
    declared_notes,
    parse_view_caption,
    referenced_notes,
    referenced_sheets,
    view_references,
)
from catia_diff.models.drawing import DrawingDocument, Sheet, View
from catia_diff.models.findings import Category, Finding, Severity
from catia_diff.models.geometry import BBox
from catia_diff.rules.base import Rule, RuleContext, RuleMeta, register
from catia_diff.rules.markers import SECTION, Marker

AGENT = "consistency"

#: "1 / 3" in the title block: the set has three sheets even if this file holds
#: one, so a pointer to sheet 3 is not dangling.
_SHEET_TOTAL_RE = re.compile(r"(\d+)\s*(?:/|of|de)\s*(\d+)", re.IGNORECASE)

_KIND_TR = {"section": "kesit", "detail": "detay", "view": "görünüş"}
_KIND_EN = {"section": "section", "detail": "detail", "view": "view"}


@dataclass(frozen=True)
class Caption:
    """A view title: the declaration half of a view reference."""

    kind: str
    label: str
    sheet_index: int
    view_id: str
    bbox: BBox | None


def sheet_captions(sheet: Sheet) -> list[Caption]:
    """Titles this sheet declares.  A text that merely *mentions* a view is not one."""
    out: list[Caption] = []
    for view in sheet.views:
        parsed = parse_view_caption(view.label)
        if parsed is None:
            continue
        out.append(
            Caption(
                kind=parsed.kind,
                label=parsed.label,
                sheet_index=sheet.index,
                view_id=view.id,
                bbox=view.bbox,
            )
        )
    return out


def document_captions(document: DrawingDocument) -> list[Caption]:
    return [caption for sheet in document.sheets for caption in sheet_captions(sheet)]


def resolves(marker_kind: str, marker_label: str, caption: Caption) -> bool:
    """A marker and a caption are two ends of the same reference."""
    if caption.label != marker_label:
        return False
    return caption.kind == marker_kind or caption.kind == "view"


def sheet_text(sheet: Sheet) -> str:
    """Everything written on the sheet as prose, titles included.

    View labels are part of it because the extractor files any text containing
    "DETAY" as a view, mentions included.
    """
    parts = [sheet.notes_text()]
    parts.extend(view.label or "" for view in sheet.views)
    return "\n".join(part for part in parts if part)


def _view_word(kind: str, lang: str) -> str:
    return (_KIND_TR if lang == "tr" else _KIND_EN).get(kind, kind)


def _orphan_captions(sheet: Sheet) -> list[View]:
    """Captions that reached no view during segmentation."""
    return [view for view in sheet.views if view.label and not view.is_geometric]


# ---------------------------------------------------------------------------
@register
class DanglingMarkerRule(Rule):
    meta = RuleMeta(
        id="REF001",
        title="Section or detail marker without a view",
        title_tr="Görünüşü olmayan kesit/detay işareti",
        severity=Severity.CRITICAL,
        category=Category.REFERENCE,
        standards=("ISO 128-3 §6",),
        description=(
            "The drawing marks a cut or a detail and never shows it: the reader "
            "is sent to a view that does not exist."
        ),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        captions = document_captions(ctx.document)
        for marker in ctx.markers(target):
            if any(resolves(marker.kind, marker.label, caption) for caption in captions):
                continue
            yield self._finding(target, marker)

    def _finding(self, sheet: Sheet, marker: Marker) -> Finding:
        word_tr = "kesit" if marker.kind == SECTION else "detay"
        word_en = "section" if marker.kind == SECTION else "detail"
        title_tr = f"KESİT {marker.label}" if marker.kind == SECTION else f"DETAY {marker.label}"
        title_en = (
            f"SECTION {marker.label}" if marker.kind == SECTION else f"DETAIL {marker.label}"
        )
        return self.finding(
            message=(
                f"The drawing marks {word_en} {marker.label} but no view is titled "
                f"'{title_en}' anywhere in the document."
            ),
            message_tr=(
                f"Resimde {marker.label} {word_tr} işareti var ancak belgede "
                f"'{title_tr}' başlıklı bir görünüş yok."
            ),
            suggestion=f"Draw the {word_en} view, or remove the marker.",
            suggestion_tr=f"{word_tr.capitalize()} görünüşünü çizin ya da işareti kaldırın.",
            sheet_index=sheet.index,
            bbox=marker.bbox,
            object_ids=list(marker.object_ids),
            confidence=marker.confidence,
            agent=AGENT,
        )


@register
class UnmarkedViewRule(Rule):
    meta = RuleMeta(
        id="REF002",
        title="Section or detail view without a marker",
        title_tr="İşareti olmayan kesit/detay görünüşü",
        severity=Severity.MAJOR,
        category=Category.REFERENCE,
        standards=("ISO 128-3 §6",),
        description=(
            "A section shown without its cutting plane leaves the reader unable "
            "to tell where the cut was taken."
        ),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        markers = ctx.all_markers()
        if not markers:
            # No marker could be recognised anywhere: this drawing's style is
            # out of the detector's reach, so every caption would look unmarked.
            return
        orphans = {view.id for view in _orphan_captions(target)}
        for caption in sheet_captions(target):
            if caption.kind not in {"section", "detail"} or caption.view_id in orphans:
                continue  # a title that belongs to no view at all is REF007's
            if any(resolves(marker.kind, marker.label, caption) for _, marker in markers):
                continue
            yield self.finding(
                message=(
                    f"View '{_view_word(caption.kind, 'en')} {caption.label}' is drawn, but "
                    "nothing on the drawing marks where it is taken from."
                ),
                message_tr=(
                    f"'{_view_word(caption.kind, 'tr').capitalize()} {caption.label}' görünüşü "
                    "çizilmiş ancak nereden alındığını gösteren işaret yok."
                ),
                suggestion="Add the cutting plane or the detail bubble with the same letter.",
                suggestion_tr="Aynı harfi taşıyan kesit düzlemini veya detay balonunu ekleyin.",
                sheet_index=target.index,
                bbox=caption.bbox,
                object_ids=[caption.view_id],
                agent=AGENT,
            )


@register
class DuplicateViewLabelRule(Rule):
    meta = RuleMeta(
        id="REF003",
        title="The same view label is used twice",
        title_tr="Aynı görünüş etiketi iki kez kullanılmış",
        severity=Severity.MAJOR,
        category=Category.REFERENCE,
        standards=("ISO 128-3 §6",),
        description="A letter that titles two views makes every reference to it ambiguous.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        seen: dict[tuple[str, str], Caption] = {}
        for caption in document_captions(ctx.document):
            key = (caption.kind, caption.label)
            first = seen.setdefault(key, caption)
            if first is caption or caption.sheet_index != target.index:
                continue
            where_en = (
                "on this sheet"
                if first.sheet_index == caption.sheet_index
                else f"on sheet {first.sheet_index + 1}"
            )
            where_tr = (
                "bu sayfada"
                if first.sheet_index == caption.sheet_index
                else f"{first.sheet_index + 1}. sayfada"
            )
            yield self.finding(
                message=(
                    f"'{_view_word(caption.kind, 'en')} {caption.label}' titles two views "
                    f"({where_en} as well); a reference to it cannot be resolved."
                ),
                message_tr=(
                    f"'{_view_word(caption.kind, 'tr').capitalize()} {caption.label}' iki "
                    f"görünüşü birden adlandırıyor ({where_tr} da var); referans çözülemez."
                ),
                suggestion="Give one of them the next free letter.",
                suggestion_tr="Birine sıradaki boş harfi verin.",
                sheet_index=target.index,
                bbox=caption.bbox,
                object_ids=[caption.view_id, first.view_id],
                agent=AGENT,
            )


@register
class UndefinedNoteReferenceRule(Rule):
    meta = RuleMeta(
        id="REF004",
        title="Reference to a note that is not written",
        title_tr="Yazılmamış bir nota gönderme",
        severity=Severity.MAJOR,
        category=Category.REFERENCE,
        description="A callout that defers to note 5 is incomplete when there is no note 5.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        declared: set[int] = set()
        for sheet in ctx.document.sheets:
            declared |= declared_notes(sheet.notes_text())
        missing = sorted(referenced_notes(sheet_text(target)) - declared)
        if not missing:
            return
        numbers = ", ".join(str(number) for number in missing)
        yield self.finding(
            message=(
                f"The sheet refers to note(s) {numbers}, which are not written on this drawing."
            ),
            message_tr=(
                f"Sayfa {numbers} numaralı nota gönderme yapıyor ancak bu resimde böyle bir "
                "not yazılmamış."
            ),
            suggestion="Write the note, or correct the number.",
            suggestion_tr="Notu yazın ya da numarayı düzeltin.",
            sheet_index=target.index,
            agent=AGENT,
        )


@register
class UndefinedSheetReferenceRule(Rule):
    meta = RuleMeta(
        id="REF005",
        title="Reference to a sheet that does not exist",
        title_tr="Var olmayan bir sayfaya gönderme",
        severity=Severity.MAJOR,
        category=Category.REFERENCE,
        description="'See sheet 4' on a two-sheet drawing sends the reader nowhere.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        total = _sheet_total(ctx.document)
        referenced = referenced_sheets(sheet_text(target))
        missing = sorted(number for number in referenced if number > total)
        if not missing:
            return
        numbers = ", ".join(str(number) for number in missing)
        yield self.finding(
            message=f"The sheet refers to sheet(s) {numbers}; the drawing has {total}.",
            message_tr=f"Sayfa {numbers}. sayfaya gönderme yapıyor; resimde {total} sayfa var.",
            suggestion="Correct the number, or supply the missing sheet.",
            suggestion_tr="Numarayı düzeltin ya da eksik sayfayı ekleyin.",
            sheet_index=target.index,
            agent=AGENT,
        )


@register
class DanglingViewReferenceRule(Rule):
    meta = RuleMeta(
        id="REF006",
        title="Text points at a view that does not exist",
        title_tr="Metin var olmayan bir görünüşe gönderiyor",
        severity=Severity.MAJOR,
        category=Category.REFERENCE,
        standards=("ISO 128-3 §6",),
        description="'See detail C' is an instruction the reader cannot follow without a detail C.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        captions = document_captions(ctx.document)
        seen: set[tuple[str, str]] = set()
        for text in _texts(target):
            for kind, label in view_references(text):
                if (kind, label) in seen:
                    continue
                if any(resolves(kind, label, caption) for caption in captions):
                    continue
                seen.add((kind, label))
                yield self.finding(
                    message=(
                        f"The sheet refers to '{_view_word(kind, 'en')} {label}', which is not "
                        "drawn anywhere in the document."
                    ),
                    message_tr=(
                        f"Sayfa '{_view_word(kind, 'tr')} {label}' göndermesi yapıyor ancak "
                        "belgede böyle bir görünüş yok."
                    ),
                    suggestion="Draw the view, or correct the reference.",
                    suggestion_tr="Görünüşü çizin ya da göndermeyi düzeltin.",
                    sheet_index=target.index,
                    snippet=text[:80],
                    agent=AGENT,
                )


@register
class OrphanCaptionRule(Rule):
    meta = RuleMeta(
        id="REF007",
        title="View title sits on no view",
        title_tr="Hiçbir görünüşe oturmayan görünüş başlığı",
        severity=Severity.MINOR,
        category=Category.REFERENCE,
        description=(
            "A title too far from any geometry titles nothing - the view it names "
            "was deleted, or the title was left behind."
        ),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for view in _orphan_captions(target):
            yield self.finding(
                message=(
                    f"The title '{view.label}' sits too far from any geometry to belong to a view."
                ),
                message_tr=(
                    f"'{view.label}' başlığı hiçbir geometriye ait olamayacak kadar uzakta duruyor."
                ),
                suggestion="Move the title under its view, or delete it with the view it named.",
                suggestion_tr="Başlığı görünüşünün altına taşıyın ya da adlandırdığı görünüşle "
                "birlikte silin.",
                sheet_index=target.index,
                bbox=view.bbox,
                object_ids=[view.id],
                agent=AGENT,
            )


# ---------------------------------------------------------------------------
def _texts(sheet: Sheet) -> list[str]:
    out = [annotation.text for annotation in sheet.annotations if annotation.text]
    out.extend(view.label for view in sheet.views if view.label)
    return out


def _sheet_total(document: DrawingDocument) -> int:
    """How many sheets the set has - the title block knows better than the file."""
    total = len(document.sheets)
    for sheet in document.sheets:
        stated = sheet.title_block.value("sheet")
        match = _SHEET_TOTAL_RE.search(stated) if stated else None
        if match:
            total = max(total, int(match.group(2)))
    return total
