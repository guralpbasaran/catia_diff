"""Cross-view rules: do the projections of one part agree with each other?

Every other rule family reads one view at a time.  These read *pairs*: an
orthographic layout ties aligned views to a shared extent, so when the front
view says 80 and the top view says 76, one of them is wrong - and nothing
inside either view can tell you that.  The pairing itself is worked out in
:mod:`catia_diff.rules.projection`.
"""

from __future__ import annotations

from collections.abc import Iterable

from catia_diff.models.drawing import Dimension, Sheet, View
from catia_diff.models.findings import Category, Finding, Severity
from catia_diff.rules import projection
from catia_diff.rules.base import Rule, RuleContext, RuleMeta, register

AGENT = "dimensioning"

#: Printed values closer than this are the same number written twice.
VALUE_EPS = 1e-3


def _tolerance(length: float) -> float:
    return max(length * 1e-4, VALUE_EPS)


def _view_names(sheet: Sheet, lang: str, *views: View) -> list[str]:
    """How to call a view in a sentence: its caption, or its position."""
    order = {view.id: position for position, view in enumerate(sheet.views, start=1)}
    word = "Görünüş" if lang == "tr" else "View"
    return [view.label or f"{word} {order.get(view.id, 0)}" for view in views]


def _value(dim: Dimension) -> float | None:
    return dim.nominal if dim.nominal is not None else dim.measured


@register
class ViewExtentConflictRule(Rule):
    meta = RuleMeta(
        id="CRV001",
        title="Aligned views state different sizes for the same extent",
        title_tr="Hizalı görünüşler aynı ölçüyü farklı veriyor",
        severity=Severity.CRITICAL,
        category=Category.CROSS_VIEW,
        standards=("ISO 128-3 §5", "ASME Y14.3 §4"),
        description=(
            "Two views aligned on an axis project the same extent; if their "
            "dimensions disagree, the part cannot be made from this drawing."
        ),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for alignment in ctx.alignments(target):
            if not alignment.comparable:
                continue  # too far apart to have been meant as the same extent
            first, second = _overall_pair(target, alignment)
            if first is None or second is None:
                continue
            value_a, value_b = _value(first), _value(second)
            if value_a is None or value_b is None:
                continue
            if abs(value_a - value_b) <= _tolerance(max(value_a, value_b)):
                continue
            en_a, en_b = _view_names(target, "en", alignment.a, alignment.b)
            tr_a, tr_b = _view_names(target, "tr", alignment.a, alignment.b)
            yield self.finding(
                message=(
                    f"{en_a} dimensions the shared {alignment.label} extent as {value_a:g} "
                    f"but {en_b} states {value_b:g}. Aligned views project the same extent, "
                    "so one of them is wrong."
                ),
                message_tr=(
                    f"{tr_a} ortak {alignment.label} ölçüsünü {value_a:g} veriyor, "
                    f"{tr_b} ise {value_b:g} diyor. Hizalı görünüşler aynı uzunluğu gösterir; "
                    "ikisinden biri yanlış."
                ),
                suggestion=(
                    "Check the model and correct the view that no longer matches it."
                ),
                suggestion_tr=(
                    "Modeli kontrol edip artık ona uymayan görünüşü düzeltin."
                ),
                sheet_index=target.index,
                bbox=second.bbox,
                object_ids=[first.id, second.id],
                snippet=f"{first.text} ≠ {second.text}",
                agent=AGENT,
            )


@register
class ViewGeometryMismatchRule(Rule):
    meta = RuleMeta(
        id="CRV002",
        title="Aligned views are drawn to different sizes",
        title_tr="Hizalı görünüşlerin geometrisi uyuşmuyor",
        severity=Severity.MAJOR,
        category=Category.CROSS_VIEW,
        standards=("ISO 128-3 §5", "ASME Y14.3 §4"),
        description=(
            "The geometry of two aligned views differs by a small amount - the "
            "signature of one view edited and the other left behind."
        ),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for alignment in ctx.alignments(target):
            if not alignment.suspicious:
                continue
            first, second = _overall_pair(target, alignment)
            if first is not None and second is not None:
                # Both views state the extent: CRV001 reports the conflict in
                # the numbers the shop actually reads.
                continue
            en_a, en_b = _view_names(target, "en", alignment.a, alignment.b)
            tr_a, tr_b = _view_names(target, "tr", alignment.a, alignment.b)
            yield self.finding(
                message=(
                    f"{en_a} is drawn {alignment.length_a:g} long on {alignment.label} but the "
                    f"aligned {en_b} is {alignment.length_b:g} (Δ={alignment.difference:g}). "
                    "Aligned views share that extent."
                ),
                message_tr=(
                    f"{tr_a} {alignment.label} ekseninde {alignment.length_a:g} çizilmiş, "
                    f"hizalı {tr_b} ise {alignment.length_b:g} (Δ={alignment.difference:g}). "
                    "Hizalı görünüşler bu uzunluğu paylaşır."
                ),
                suggestion=(
                    "One view was probably updated and the other was not; redraw it from the "
                    "current model."
                ),
                suggestion_tr=(
                    "Büyük olasılıkla bir görünüş güncellenip diğeri unutulmuş; güncel modelden "
                    "yeniden türetin."
                ),
                sheet_index=target.index,
                bbox=alignment.b.bbox,
                object_ids=[alignment.a.id, alignment.b.id],
                agent=AGENT,
                confidence=0.9,
            )


@register
class RepeatedAcrossViewsRule(Rule):
    meta = RuleMeta(
        id="CRV003",
        title="The same extent is dimensioned in two views",
        title_tr="Aynı ölçü iki görünüşte tekrarlanmış",
        severity=Severity.MINOR,
        category=Category.CROSS_VIEW,
        standards=("ISO 129-1 §4.3", "ASME Y14.5-2018 §1.4(e)"),
        description=(
            "A feature is dimensioned once, in the view that shows it best; a "
            "repeat has to be maintained twice and drifts apart on the first edit."
        ),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for alignment in ctx.alignments(target):
            if not alignment.comparable:
                continue
            first, second = _overall_pair(target, alignment)
            if first is None or second is None:
                continue
            value_a, value_b = _value(first), _value(second)
            if value_a is None or value_b is None:
                continue
            if abs(value_a - value_b) > _tolerance(max(value_a, value_b)):
                continue  # they disagree - that is CRV001, a different defect
            en_a, en_b = _view_names(target, "en", alignment.a, alignment.b)
            tr_a, tr_b = _view_names(target, "tr", alignment.a, alignment.b)
            yield self.finding(
                message=(
                    f"The {alignment.label} extent {value_a:g} is dimensioned in {en_a} and "
                    f"again in {en_b}."
                ),
                message_tr=(
                    f"{alignment.label} ekseninde {value_a:g} ölçüsü hem {tr_a} hem "
                    f"{tr_b} içinde verilmiş."
                ),
                suggestion="Keep the dimension in one view and delete the repeat.",
                suggestion_tr="Ölçüyü tek görünüşte bırakıp tekrarını silin.",
                sheet_index=target.index,
                bbox=second.bbox,
                object_ids=[first.id, second.id],
                snippet=second.text,
                agent=AGENT,
            )


def _overall_pair(
    sheet: Sheet, alignment: projection.ViewAlignment
) -> tuple[Dimension | None, Dimension | None]:
    """The dimension each aligned view gives for the shared extent, if any."""
    out: list[Dimension | None] = []
    for view, length in ((alignment.a, alignment.length_a), (alignment.b, alignment.length_b)):
        candidates = projection.overall_dimensions(
            sheet, view, alignment.axis, _tolerance(length) * 10
        )
        out.append(candidates[0] if candidates else None)
    return (out[0], out[1])
