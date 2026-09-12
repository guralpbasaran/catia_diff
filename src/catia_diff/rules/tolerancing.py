"""Tolerance checks (ISO 286 / ISO 2768 / ASME Y14.5 §2)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from catia_diff.models.drawing import Dimension, DimensionKind, Sheet, ToleranceKind
from catia_diff.models.findings import Category, Finding, Severity
from catia_diff.rules import analysis
from catia_diff.rules.base import Rule, RuleContext, RuleMeta, register
from catia_diff.standards.iso2768 import GeneralToleranceSpec

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
            spec = ctx.general_spec(target)
            # CAD writes every value with the same number of decimals
            # (DIMDEC), so decimals only signal intent when they go beyond what
            # the general tolerance can hold; fitting features always do.
            candidates = [
                dim
                for dim in untoleranced
                if dim.kind is DimensionKind.DIAMETER or (dim.decimals or 0) >= 3
            ]
            if not candidates:
                return
            for dim in candidates:
                derived = _derived_text(spec, dim)
                yield self.finding(
                    message=(
                        f"Dimension {dim.id} ({dim.label()}) relies on the general tolerance "
                        f"'{general}'{derived['en']}. Verify that this is enough for a fitting feature."
                    ),
                    message_tr=(
                        f"{dim.id} ({dim.label()}) ölçüsü '{general}' genel toleransına "
                        f"dayanıyor{derived['tr']}. Geçme/uyum gerektiren bir unsur için bunun "
                        f"yeterli olduğunu doğrulayın."
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


# --------------------------------------------------------------------------
# ISO 2768: the general tolerance note evaluated as numbers
# --------------------------------------------------------------------------
#: Relative slack used when comparing two tolerance values.
_EPS = 1e-9


def _derived_text(spec: GeneralToleranceSpec | None, dim: Dimension) -> dict[str, str]:
    """" -> ±0.3 mm" fragment appended to a message, when derivable."""
    if spec is None or not spec.is_usable:
        return {"en": "", "tr": ""}
    deviation = spec.deviation_for(dim)
    if deviation is None:
        return {"en": "", "tr": ""}
    unit = "°" if dim.is_angular else f" {dim.units.value}"
    rendered = f"±{deviation:g}{unit}"
    return {
        "en": f" ({spec.designation} gives {rendered})",
        "tr": f" ({spec.designation} → {rendered})",
    }


@register
class GeneralToleranceClassRule(Rule):
    meta = RuleMeta(
        id="TOL006",
        title="General tolerance note names no tolerance class",
        title_tr="Genel tolerans notunda tolerans sınıfı yok",
        severity=Severity.MAJOR,
        category=Category.TOLERANCING,
        standards=("ISO 2768-1 §5", "ISO 2768-2 §5"),
        description=(
            "Without a class letter (f/m/c/v, H/K/L) the note fixes no numbers, so "
            "untoleranced dimensions stay undefined."
        ),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        note = ctx.general_tolerance(target)
        if not note or not target.dimensions:
            return
        spec = ctx.general_spec(target)
        if spec is None:
            return  # a note that does not reference ISO 2768 at all
        if spec.unknown_letters:
            letters = ", ".join(spec.unknown_letters)
            yield self.finding(
                message=(
                    f"The general tolerance note '{spec.raw}' uses unknown class letter(s) "
                    f"{letters}; valid classes are f/m/c/v (linear) and H/K/L (geometric)."
                ),
                message_tr=(
                    f"'{spec.raw}' genel tolerans notunda tanınmayan sınıf harfi {letters} var; "
                    f"geçerli sınıflar f/m/c/v (boyut) ve H/K/L (geometrik)."
                ),
                suggestion="Correct the designation, e.g. 'ISO 2768-mK'.",
                suggestion_tr="Gösterimi düzeltin, örn. 'ISO 2768-mK'.",
                sheet_index=target.index,
                bbox=target.title_block.bbox,
                severity=Severity.MINOR,
                agent=AGENT,
            )
        if spec.is_usable:
            return
        yield self.finding(
            message=(
                f"The general tolerance note '{spec.raw}' names no tolerance class, so no "
                "permissible deviation can be derived for the untoleranced dimensions."
            ),
            message_tr=(
                f"'{spec.raw}' genel tolerans notu bir tolerans sınıfı belirtmiyor; bu nedenle "
                "toleranssız ölçüler için izin verilen sapma türetilemiyor."
            ),
            suggestion="State the class, e.g. 'ISO 2768-mK' (medium linear, class K geometric).",
            suggestion_tr="Sınıfı belirtin, örn. 'ISO 2768-mK' (orta boyut sınıfı, K geometrik sınıf).",
            sheet_index=target.index,
            bbox=target.title_block.bbox,
            agent=AGENT,
        )


@register
class SizeOutsideGeneralToleranceRule(Rule):
    meta = RuleMeta(
        id="TOL007",
        title="Nominal size not covered by the general tolerance table",
        title_tr="Genel tolerans tablosunun kapsamadığı nominal ölçü",
        severity=Severity.MAJOR,
        category=Category.TOLERANCING,
        standards=("ISO 2768-1 §4",),
        description=(
            "ISO 2768-1 starts at 0.5 mm and stops at 4000 mm, and class f has no row "
            "above 2000 mm; outside those ranges the deviation must be indicated."
        ),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        spec = ctx.general_spec(target)
        if spec is None or spec.linear is None:
            return
        for dim in target.dimensions:
            if dim.has_tolerance or dim.nominal is None or dim.extra.get("thread"):
                continue
            if not dim.units.is_length or spec.covers(dim):
                continue
            yield self.finding(
                message=(
                    f"Dimension {dim.id} ({dim.label()}) is not covered by "
                    f"{spec.designation}: the table gives no deviation for this nominal size."
                ),
                message_tr=(
                    f"{dim.id} ({dim.label()}) ölçüsü {spec.designation} kapsamında değil: "
                    f"tablo bu nominal ölçü için sapma vermiyor."
                ),
                suggestion="Indicate the deviation directly on this dimension.",
                suggestion_tr="Sapmayı doğrudan bu ölçünün üzerinde belirtin.",
                sheet_index=target.index,
                bbox=dim.bbox,
                object_ids=[dim.id],
                agent=AGENT,
            )


@register
class LooserThanGeneralRule(Rule):
    meta = RuleMeta(
        id="TOL008",
        title="Indicated tolerance is looser than the general tolerance",
        title_tr="Belirtilen tolerans genel toleranstan daha geniş",
        severity=Severity.MINOR,
        category=Category.TOLERANCING,
        standards=("ISO 2768-1 §4", "ISO 8015"),
        description=(
            "Permitted, but it relaxes the drawing: an indicated tolerance is normally "
            "there to tighten a feature, so a wider one is usually a mistake."
        ),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        spec = ctx.general_spec(target)
        if spec is None or spec.linear is None:
            return
        for dim in target.dimensions:
            deviations = analysis.tolerance_deviations(dim)
            general = spec.deviation_for(dim)
            if deviations is None or general is None:
                continue
            upper, lower = deviations
            slack = general * _EPS + _EPS
            if upper <= general + slack and lower >= -general - slack:
                continue
            unit = "°" if dim.is_angular else f" {dim.units.value}"
            yield self.finding(
                message=(
                    f"Dimension {dim.id} ({dim.label()}) is toleranced "
                    f"+{upper:g}/{lower:g}{unit}, wider than the general tolerance "
                    f"{spec.designation} (±{general:g}{unit})."
                ),
                message_tr=(
                    f"{dim.id} ({dim.label()}) ölçüsünün toleransı +{upper:g}/{lower:g}{unit}; "
                    f"bu, {spec.designation} genel toleransından (±{general:g}{unit}) daha geniş."
                ),
                suggestion=(
                    "Confirm the relaxation is intended; otherwise tighten it or drop it and "
                    "let the general tolerance apply."
                ),
                suggestion_tr=(
                    "Genişletmenin bilinçli olduğunu doğrulayın; değilse toleransı daraltın ya "
                    "da kaldırıp genel toleransın geçerli olmasını sağlayın."
                ),
                sheet_index=target.index,
                bbox=dim.bbox,
                object_ids=[dim.id],
                snippet=dim.text,
                agent=AGENT,
            )


@register
class RedundantGeneralToleranceRule(Rule):
    meta = RuleMeta(
        id="TOL009",
        title="Indicated tolerance repeats the general tolerance",
        title_tr="Belirtilen tolerans genel toleransı tekrar ediyor",
        severity=Severity.INFO,
        category=Category.TOLERANCING,
        standards=("ISO 2768-1 §4",),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        spec = ctx.general_spec(target)
        if spec is None or spec.linear is None:
            return
        for dim in target.dimensions:
            deviations = analysis.tolerance_deviations(dim)
            general = spec.deviation_for(dim)
            if deviations is None or general is None:
                continue
            upper, lower = deviations
            tolerance = max(abs(general) * 1e-6, 1e-9)
            if abs(upper - general) > tolerance or abs(lower + general) > tolerance:
                continue
            unit = "°" if dim.is_angular else f" {dim.units.value}"
            yield self.finding(
                message=(
                    f"Dimension {dim.id} ({dim.label()}) repeats the general tolerance "
                    f"±{general:g}{unit} of {spec.designation}."
                ),
                message_tr=(
                    f"{dim.id} ({dim.label()}) ölçüsü {spec.designation} genel toleransını "
                    f"(±{general:g}{unit}) tekrar ediyor."
                ),
                suggestion="Remove the indication; the general tolerance already covers it.",
                suggestion_tr="Gösterimi kaldırın; genel tolerans bu ölçüyü zaten kapsıyor.",
                sheet_index=target.index,
                bbox=dim.bbox,
                object_ids=[dim.id],
                snippet=dim.text,
                agent=AGENT,
            )


@register
class ToleranceStackRule(Rule):
    meta = RuleMeta(
        id="TOL010",
        title="Tolerance stack exceeds the overall dimension's tolerance",
        title_tr="Tolerans birikimi toplam ölçünün toleransını aşıyor",
        severity=Severity.MAJOR,
        category=Category.TOLERANCING,
        standards=("ISO 129-1 §6.4", "ISO 2768-1 §4", "ASME Y14.5-2018 §1.4(m)"),
        description=(
            "In a closed chain the worst-case sum of the individual tolerances must fit "
            "inside the overall tolerance, otherwise the part cannot be made to drawing."
        ),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        chains = analysis.find_closed_chains(target)
        if not chains:
            return
        spec = ctx.general_spec(target)
        for overall, parts in chains:
            overall_width = _effective_width(overall, spec)
            part_widths = [_effective_width(part, spec) for part in parts]
            if overall_width is None or any(width is None for width in part_widths):
                continue
            stack = sum(width for width in part_widths if width is not None)
            if stack <= overall_width * (1 + _EPS) + _EPS:
                continue
            unit = f" {overall.units.value}"
            yield self.finding(
                message=(
                    f"The chain {' + '.join(part.label() for part in parts)} accumulates "
                    f"±{stack / 2:g}{unit} while the overall dimension {overall.label()} allows "
                    f"only ±{overall_width / 2:g}{unit}."
                ),
                message_tr=(
                    f"{' + '.join(part.label() for part in parts)} zinciri ±{stack / 2:g}{unit} "
                    f"birikim üretiyor; toplam ölçü {overall.label()} ise yalnızca "
                    f"±{overall_width / 2:g}{unit} izin veriyor."
                ),
                suggestion=(
                    "Release one dimension of the chain (auxiliary/reference), tighten the "
                    "individual tolerances, or widen the overall one - see DIM003."
                ),
                suggestion_tr=(
                    "Zincirdeki bir ölçüyü serbest bırakın (yardımcı/referans), tek tek "
                    "toleransları daraltın ya da toplam ölçünün toleransını genişletin - "
                    "DIM003'e bakın."
                ),
                sheet_index=target.index,
                bbox=overall.bbox,
                object_ids=[overall.id, *(part.id for part in parts)],
                confidence=0.8,
                agent=AGENT,
            )


def _effective_width(dim: Dimension, spec: GeneralToleranceSpec | None) -> float | None:
    """Tolerance zone width of ``dim``: indicated if present, else general."""
    width = analysis.tolerance_width(dim)
    if width is not None:
        return width
    if dim.is_basic or dim.is_reference:
        return None  # basic dimensions carry no zone; reference ones are not binding
    if spec is None:
        return None
    deviation = spec.deviation_for(dim)
    return None if deviation is None else deviation * 2.0
