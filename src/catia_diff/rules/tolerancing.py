"""Tolerance checks (ISO 286 / ISO 2768 / ASME Y14.5 §2)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from catia_diff.models.drawing import DimensionKind, Sheet, ToleranceKind
from catia_diff.models.findings import Category, Finding, Severity
from catia_diff.rules.base import Rule, RuleContext, RuleMeta, register

AGENT = "tolerancing"

#: Tolerance spans below this (in mm) are unusual for general machining.
UNREALISTIC_SPAN_MM = 1e-3


@register
class MissingToleranceRule(Rule):
    meta = RuleMeta(
        id="TOL001",
        title="Dimension without tolerance",
        title_tr="Toleranssız ölçü",
        severity=Severity.MAJOR,
        category=Category.TOLERANCING,
        standards=("ISO 2768-1", "ASME Y14.5-2018 §2.1"),
        description=(
            "Every dimension needs a tolerance: individually, through a general tolerance "
            "note, or by being basic/reference."
        ),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        general = ctx.general_tolerance(target)
        severity = Severity.MINOR if general else Severity.MAJOR
        untoleranced = [
            dim
            for dim in target.dimensions
            if not dim.has_tolerance and dim.nominal is not None and not dim.extra.get("thread")
        ]
        if not untoleranced:
            return
        if general:
            # With a general tolerance note the drawing is valid; only fitting
            # features (holes/shafts) and tight decimals deserve a mention.
            candidates = [
                dim
                for dim in untoleranced
                if dim.kind is DimensionKind.DIAMETER or (dim.decimals or 0) >= 2
            ]
            if not candidates:
                return
            for dim in candidates:
                yield self.finding(
                    message=(
                        f"Dimension {dim.id} ({dim.label()}) relies on the general tolerance "
                        f"'{general}'. Verify that this is intended for a fitting feature."
                    ),
                    message_tr=(
                        f"{dim.id} ({dim.label()}) ölçüsü '{general}' genel toleransına "
                        f"dayanıyor. Geçme/uyum gerektiren bir unsur için bunun yeterli "
                        f"olduğunu doğrulayın."
                    ),
                    suggestion="Add an explicit tolerance or an ISO fit class (e.g. H7) to the callout.",
                    suggestion_tr="Gösterime açık bir tolerans veya ISO geçme sınıfı (örn. H7) ekleyin.",
                    sheet_index=target.index,
                    bbox=dim.bbox,
                    object_ids=[dim.id],
                    severity=severity,
                    confidence=0.6,
                    agent=AGENT,
                )
            return

        for dim in untoleranced:
            yield self.finding(
                message=(
                    f"Dimension {dim.id} ({dim.label()}) has no tolerance and the sheet states "
                    "no general tolerance."
                ),
                message_tr=(
                    f"{dim.id} ({dim.label()}) ölçüsünde tolerans yok ve sayfada genel tolerans "
                    "notu da bulunmuyor."
                ),
                suggestion=(
                    "Add a tolerance to the dimension, or add a general tolerance note "
                    "(e.g. 'ISO 2768-mK') to the title block."
                ),
                suggestion_tr=(
                    "Ölçüye tolerans ekleyin veya antete genel tolerans notu "
                    "(örn. 'ISO 2768-mK') girin."
                ),
                sheet_index=target.index,
                bbox=dim.bbox,
                object_ids=[dim.id],
                severity=severity,
                agent=AGENT,
            )


@register
class InvalidToleranceRule(Rule):
    meta = RuleMeta(
        id="TOL002",
        title="Invalid tolerance range",
        title_tr="Geçersiz tolerans aralığı",
        severity=Severity.CRITICAL,
        category=Category.TOLERANCING,
        standards=("ISO 286-1", "ASME Y14.5-2018 §2.2"),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for dim in target.dimensions:
            tol = dim.tolerance
            if not tol.is_specified:
                continue
            if tol.is_inverted:
                yield self.finding(
                    message=(
                        f"Dimension {dim.id}: upper deviation ({tol.upper:g}) is below the lower "
                        f"deviation ({tol.lower:g}) - the tolerance zone is empty."
                    ),
                    message_tr=(
                        f"{dim.id}: üst sapma ({tol.upper:g}) alt sapmanın ({tol.lower:g}) "
                        f"altında - tolerans aralığı boş."
                    ),
                    suggestion="Swap the deviations so that upper > lower.",
                    suggestion_tr="Sapmaların yerini değiştirin; üst sapma alt sapmadan büyük olmalı.",
                    sheet_index=target.index,
                    bbox=dim.bbox,
                    object_ids=[dim.id],
                    snippet=dim.text,
                    agent=AGENT,
                )
            elif tol.is_zero_width and tol.kind is not ToleranceKind.BASIC:
                yield self.finding(
                    message=f"Dimension {dim.id} has a zero-width tolerance zone; it cannot be manufactured.",
                    message_tr=f"{dim.id} ölçüsünün tolerans aralığı sıfır; bu şekilde imal edilemez.",
                    suggestion="Give the dimension a real tolerance, or mark it as basic ([20]).",
                    suggestion_tr="Ölçüye gerçek bir tolerans verin veya teorik ölçü ([20]) olarak işaretleyin.",
                    sheet_index=target.index,
                    bbox=dim.bbox,
                    object_ids=[dim.id],
                    snippet=dim.text,
                    severity=Severity.MAJOR,
                    agent=AGENT,
                )
            elif _nominal_outside_limits(dim, tol):
                yield self.finding(
                    message=(
                        f"Dimension {dim.id}: the nominal value {dim.nominal:g} lies "
                        f"outside its own limits [{tol.lower:g}, {tol.upper:g}]."
                    ),
                    message_tr=(
                        f"{dim.id}: nominal değer {dim.nominal:g}, kendi sınırlarının "
                        f"[{tol.lower:g}, {tol.upper:g}] dışında."
                    ),
                    suggestion="Correct the limits or the nominal value.",
                    suggestion_tr="Sınır değerlerini veya nominal değeri düzeltin.",
                    sheet_index=target.index,
                    bbox=dim.bbox,
                    object_ids=[dim.id],
                    agent=AGENT,
                )


def _nominal_outside_limits(dim, tol) -> bool:
    """True when a limits tolerance does not contain the printed nominal value."""
    if tol.kind is not ToleranceKind.LIMITS:
        return False
    if dim.nominal is None or tol.upper is None or tol.lower is None:
        return False
    return not tol.lower <= dim.nominal <= tol.upper


@register
class PrecisionMismatchRule(Rule):
    meta = RuleMeta(
        id="TOL003",
        title="Decimal places inconsistent with the tolerance",
        title_tr="Ondalık hane sayısı toleransla uyumsuz",
        severity=Severity.MINOR,
        category=Category.TOLERANCING,
        standards=("ISO 129-1 §5.3", "ASME Y14.5-2018 §2.3.2"),
        description="Value and deviations must be written with the same number of decimals.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for dim in target.dimensions:
            tol = dim.tolerance
            if tol.kind not in {ToleranceKind.SYMMETRIC, ToleranceKind.DEVIATION, ToleranceKind.LIMITS}:
                continue
            value_decimals = dim.decimals or 0
            tol_decimals = tol.decimals()
            if tol_decimals <= value_decimals:
                continue
            yield self.finding(
                message=(
                    f"Dimension {dim.id} is written with {value_decimals} decimal(s) but its "
                    f"tolerance uses {tol_decimals}."
                ),
                message_tr=(
                    f"{dim.id} ölçüsü {value_decimals} ondalık haneyle yazılmış ancak toleransı "
                    f"{tol_decimals} hane kullanıyor."
                ),
                suggestion=f"Write the value with {tol_decimals} decimals as well.",
                suggestion_tr=f"Nominal değeri de {tol_decimals} ondalık haneyle yazın.",
                sheet_index=target.index,
                bbox=dim.bbox,
                object_ids=[dim.id],
                snippet=dim.text,
                confidence=0.8,
                agent=AGENT,
            )


@register
class UnrealisticToleranceRule(Rule):
    meta = RuleMeta(
        id="TOL004",
        title="Unrealistically tight tolerance",
        title_tr="Gerçekçi olmayan dar tolerans",
        severity=Severity.MAJOR,
        category=Category.TOLERANCING,
        standards=("ISO 286-1",),
        description="A tolerance below ~1 µm is rarely producible outside precision grinding.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for dim in target.dimensions:
            span = dim.tolerance.span
            if span is None or span <= 0 or dim.units.value == "in":
                continue
            if span >= UNREALISTIC_SPAN_MM:
                continue
            yield self.finding(
                message=(
                    f"Dimension {dim.id} has a total tolerance of {span * 1000:.3g} µm, which "
                    "is beyond ordinary machining capability."
                ),
                message_tr=(
                    f"{dim.id} ölçüsünün toplam toleransı {span * 1000:.3g} µm; bu değer "
                    "olağan imalat kabiliyetinin dışındadır."
                ),
                suggestion="Confirm the process capability or widen the tolerance.",
                suggestion_tr="Proses yeterliliğini doğrulayın veya toleransı genişletin.",
                sheet_index=target.index,
                bbox=dim.bbox,
                object_ids=[dim.id],
                confidence=0.7,
                agent=AGENT,
            )


@register
class MixedToleranceStyleRule(Rule):
    meta = RuleMeta(
        id="TOL005",
        title="Mixed tolerance notations",
        title_tr="Karışık tolerans gösterimleri",
        severity=Severity.MINOR,
        category=Category.TOLERANCING,
        standards=("ISO 129-1 §5.3",),
        description="One sheet should use one notation style for direct tolerances.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        styles = Counter(
            dim.tolerance.kind
            for dim in target.dimensions
            if dim.tolerance.kind
            in {ToleranceKind.SYMMETRIC, ToleranceKind.DEVIATION, ToleranceKind.LIMITS}
        )
        if len(styles) < 2:
            return
        rendered = ", ".join(f"{kind.value} ({count})" for kind, count in styles.most_common())
        yield self.finding(
            message=f"The sheet mixes tolerance notations: {rendered}.",
            message_tr=f"Sayfada farklı tolerans gösterimleri karışık kullanılmış: {rendered}.",
            suggestion="Pick one notation (± / deviations / limits) and apply it consistently.",
            suggestion_tr="Tek bir gösterim (± / sapma / sınır değer) seçip tutarlı biçimde uygulayın.",
            sheet_index=target.index,
            confidence=0.8,
            agent=AGENT,
        )
