"""Geometric tolerancing checks (ISO 1101 / ISO 5459 / ASME Y14.5)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from catia_diff.config import Profile
from catia_diff.models.drawing import MaterialCondition, Sheet
from catia_diff.models.findings import Category, Finding, Severity
from catia_diff.rules.base import Rule, RuleContext, RuleMeta, register

AGENT = "gdt"


@register
class UndefinedDatumRule(Rule):
    meta = RuleMeta(
        id="GDT001",
        title="Feature control frame references an undefined datum",
        title_tr="Tanımsız datuma atıf yapan tolerans çerçevesi",
        severity=Severity.CRITICAL,
        category=Category.GDT,
        standards=("ISO 5459 §5", "ASME Y14.5-2018 §7.2"),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        defined = target.datum_labels()
        for gtol in target.geometric_tolerances:
            missing = [ref.label for ref in gtol.datums if ref.label.upper() not in defined]
            if not missing:
                continue
            labels = ", ".join(sorted(set(missing)))
            yield self.finding(
                message=(
                    f"{gtol.label()} references datum(s) {labels}, but no datum feature symbol "
                    "defines them on this sheet."
                ),
                message_tr=(
                    f"{gtol.label()} çerçevesi {labels} datumuna atıf yapıyor ancak bu sayfada "
                    "bunları tanımlayan datum sembolü yok."
                ),
                suggestion=f"Place the datum feature symbol(s) {labels} on the relevant surfaces.",
                suggestion_tr=f"{labels} datum sembol(ler)ini ilgili yüzeylere yerleştirin.",
                sheet_index=target.index,
                bbox=gtol.bbox,
                object_ids=[gtol.id],
                snippet=gtol.raw,
                agent=AGENT,
            )


@register
class UnusedDatumRule(Rule):
    meta = RuleMeta(
        id="GDT002",
        title="Datum feature never referenced",
        title_tr="Hiç kullanılmayan datum",
        severity=Severity.MINOR,
        category=Category.GDT,
        standards=("ISO 5459 §5",),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        referenced = {
            ref.label.upper() for gtol in target.geometric_tolerances for ref in gtol.datums
        }
        for datum in target.datums:
            if datum.label.upper() in referenced:
                continue
            yield self.finding(
                message=f"Datum {datum.label} is defined but never used by a feature control frame.",
                message_tr=f"{datum.label} datumu tanımlanmış ancak hiçbir tolerans çerçevesinde kullanılmamış.",
                suggestion="Either reference the datum or remove the symbol.",
                suggestion_tr="Datuma bir çerçevede atıf yapın ya da sembolü kaldırın.",
                sheet_index=target.index,
                bbox=datum.bbox,
                object_ids=[datum.id],
                confidence=0.9,
                agent=AGENT,
            )


@register
class DuplicateDatumRule(Rule):
    meta = RuleMeta(
        id="GDT003",
        title="Datum label used more than once",
        title_tr="Aynı datum etiketi birden fazla kez kullanılmış",
        severity=Severity.MAJOR,
        category=Category.GDT,
        standards=("ISO 5459 §5.2", "ASME Y14.5-2018 §7.3"),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        counts = Counter(datum.label.upper() for datum in target.datums)
        for label, count in counts.items():
            if count < 2:
                continue
            ids = [datum.id for datum in target.datums if datum.label.upper() == label]
            yield self.finding(
                message=f"Datum {label} is attached to {count} different features.",
                message_tr=f"{label} datumu {count} farklı unsura atanmış.",
                suggestion="Use a distinct letter per datum feature, or a common datum (A-B).",
                suggestion_tr="Her datum unsuru için farklı harf kullanın ya da ortak datum (A-B) tanımlayın.",
                sheet_index=target.index,
                object_ids=ids,
                agent=AGENT,
            )


@register
class FormToleranceWithDatumRule(Rule):
    meta = RuleMeta(
        id="GDT004",
        title="Form tolerance with a datum reference",
        title_tr="Datum içeren biçim toleransı",
        severity=Severity.MAJOR,
        category=Category.GDT,
        standards=("ISO 1101 §6", "ASME Y14.5-2018 §5.4"),
        description="Straightness, flatness, circularity and cylindricity take no datum.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for gtol in target.geometric_tolerances:
            if not gtol.characteristic.is_form or not gtol.datums:
                continue
            labels = ", ".join(ref.label for ref in gtol.datums)
            yield self.finding(
                message=(
                    f"{gtol.characteristic.value} is a form tolerance but references datum(s) "
                    f"{labels}."
                ),
                message_tr=(
                    f"{gtol.characteristic.value} bir biçim toleransıdır ancak {labels} "
                    "datumuna atıf yapıyor."
                ),
                suggestion=(
                    "Remove the datum, or switch to an orientation/location characteristic "
                    "(parallelism, perpendicularity, profile) if a reference is intended."
                ),
                suggestion_tr=(
                    "Datumu kaldırın veya referans gerekiyorsa konum/yönelim karakteristiğine "
                    "(paralellik, diklik, profil) geçin."
                ),
                sheet_index=target.index,
                bbox=gtol.bbox,
                object_ids=[gtol.id],
                snippet=gtol.raw,
                agent=AGENT,
            )


@register
class MissingDatumRule(Rule):
    meta = RuleMeta(
        id="GDT005",
        title="Orientation/location tolerance without a datum",
        title_tr="Datumsuz yönelim/konum toleransı",
        severity=Severity.CRITICAL,
        category=Category.GDT,
        standards=("ISO 1101 §7", "ASME Y14.5-2018 §7.2"),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for gtol in target.geometric_tolerances:
            if not gtol.characteristic.requires_datum or gtol.datums:
                continue
            yield self.finding(
                message=(
                    f"{gtol.characteristic.value} requires at least one datum reference, but the "
                    "frame has none."
                ),
                message_tr=(
                    f"{gtol.characteristic.value} en az bir datum referansı gerektirir ancak "
                    "çerçevede hiç datum yok."
                ),
                suggestion="Add the datum reference frame (e.g. |A|B|C) to the third compartment.",
                suggestion_tr="Üçüncü bölmeye datum referans çerçevesini (örn. |A|B|C) ekleyin.",
                sheet_index=target.index,
                bbox=gtol.bbox,
                object_ids=[gtol.id],
                snippet=gtol.raw,
                agent=AGENT,
            )


@register
class NonPositiveGtolRule(Rule):
    meta = RuleMeta(
        id="GDT006",
        title="Geometric tolerance value missing or non-positive",
        title_tr="Geometrik tolerans değeri eksik veya pozitif değil",
        severity=Severity.CRITICAL,
        category=Category.GDT,
        standards=("ISO 1101 §5.2",),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for gtol in target.geometric_tolerances:
            if gtol.value is not None and gtol.value > 0:
                continue
            shown = "missing" if gtol.value is None else f"{gtol.value:g}"
            shown_tr = "eksik" if gtol.value is None else f"{gtol.value:g}"
            yield self.finding(
                message=f"Feature control frame {gtol.id} has a tolerance value that is {shown}.",
                message_tr=f"{gtol.id} tolerans çerçevesinin tolerans değeri {shown_tr}.",
                suggestion="Enter a positive tolerance zone value in the second compartment.",
                suggestion_tr="İkinci bölmeye pozitif bir tolerans bölgesi değeri girin.",
                sheet_index=target.index,
                bbox=gtol.bbox,
                object_ids=[gtol.id],
                snippet=gtol.raw,
                agent=AGENT,
            )


@register
class PositionWithoutBasicRule(Rule):
    meta = RuleMeta(
        id="GDT007",
        title="Position tolerance without basic dimensions",
        title_tr="Teorik ölçüsü olmayan konum toleransı",
        severity=Severity.MAJOR,
        category=Category.GDT,
        standards=("ASME Y14.5-2018 §7.2", "ISO 1101 §8"),
        description="True position is measured from theoretically exact (basic) dimensions.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        from catia_diff.models.drawing import GDTCharacteristic

        positional = [
            gtol
            for gtol in target.geometric_tolerances
            if gtol.characteristic
            in {GDTCharacteristic.POSITION, GDTCharacteristic.SYMMETRY, GDTCharacteristic.CONCENTRICITY}
        ]
        if not positional:
            return
        if any(dim.is_basic for dim in target.dimensions):
            return
        yield self.finding(
            message=(
                f"{len(positional)} positional tolerance(s) are specified, but the sheet has no "
                "basic dimension to locate the feature from."
            ),
            message_tr=(
                f"{len(positional)} adet konum toleransı tanımlanmış ancak unsurun yerini "
                "belirleyecek hiçbir teorik (basic) ölçü yok."
            ),
            suggestion="Box the locating dimensions to make them basic, e.g. [30].",
            suggestion_tr="Konumlandırma ölçülerini kutu içine alarak teorik hâle getirin, örn. [30].",
            sheet_index=target.index,
            bbox=positional[0].bbox,
            object_ids=[gtol.id for gtol in positional],
            confidence=0.8,
            agent=AGENT,
        )


@register
class PositionZoneShapeRule(Rule):
    meta = RuleMeta(
        id="GDT008",
        title="Position tolerance on a hole without a diametral zone",
        title_tr="Çap bölgesi belirtilmemiş konum toleransı",
        severity=Severity.MINOR,
        category=Category.GDT,
        standards=("ASME Y14.5-2018 §7.3.2", "ISO 1101 §8.2"),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        from catia_diff.models.drawing import GDTCharacteristic

        for gtol in target.geometric_tolerances:
            if gtol.characteristic is not GDTCharacteristic.POSITION or gtol.diametral_zone:
                continue
            yield self.finding(
                message=(
                    f"Position frame {gtol.id} specifies a non-diametral zone; a cylindrical "
                    "feature normally needs ⌀ in front of the tolerance value."
                ),
                message_tr=(
                    f"{gtol.id} konum çerçevesi çap bölgesi belirtmiyor; silindirik unsurlarda "
                    "tolerans değerinin önünde ⌀ bulunmalıdır."
                ),
                suggestion="Add ⌀ to the tolerance compartment when the feature is cylindrical.",
                suggestion_tr="Unsur silindirikse tolerans bölmesine ⌀ sembolünü ekleyin.",
                sheet_index=target.index,
                bbox=gtol.bbox,
                object_ids=[gtol.id],
                snippet=gtol.raw,
                confidence=0.6,
                agent=AGENT,
            )


@register
class ModifierOnFormToleranceRule(Rule):
    meta = RuleMeta(
        id="GDT009",
        title="Material modifier on a surface form tolerance",
        title_tr="Yüzey biçim toleransında malzeme koşulu",
        severity=Severity.MAJOR,
        category=Category.GDT,
        standards=("ASME Y14.5-2018 §5.4", "ISO 2692"),
        description="Ⓜ/Ⓛ apply to features of size only.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        from catia_diff.models.drawing import GDTCharacteristic

        surface_form = {GDTCharacteristic.FLATNESS, GDTCharacteristic.CIRCULARITY}
        for gtol in target.geometric_tolerances:
            if gtol.characteristic not in surface_form:
                continue
            if gtol.material_condition is MaterialCondition.RFS:
                continue
            yield self.finding(
                message=(
                    f"{gtol.characteristic.value} carries a {gtol.material_condition.value.upper()} "
                    "modifier, which only applies to features of size."
                ),
                message_tr=(
                    f"{gtol.characteristic.value} toleransında yalnızca boyutlu unsurlarda "
                    f"geçerli olan {gtol.material_condition.value.upper()} koşulu kullanılmış."
                ),
                suggestion="Remove the modifier, or apply the tolerance to the feature axis instead.",
                suggestion_tr="Koşulu kaldırın veya toleransı unsurun eksenine uygulayın.",
                sheet_index=target.index,
                bbox=gtol.bbox,
                object_ids=[gtol.id],
                snippet=gtol.raw,
                agent=AGENT,
            )


@register
class RepeatedDatumInFrameRule(Rule):
    meta = RuleMeta(
        id="GDT010",
        title="Datum repeated inside one frame",
        title_tr="Aynı çerçevede tekrarlanan datum",
        severity=Severity.MAJOR,
        category=Category.GDT,
        standards=("ASME Y14.5-2018 §7.5", "ISO 5459 §6"),
        profiles=frozenset({Profile.ISO, Profile.ASME}),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for gtol in target.geometric_tolerances:
            labels = [ref.label.upper() for ref in gtol.datums]
            duplicates = [label for label, count in Counter(labels).items() if count > 1]
            if not duplicates:
                continue
            yield self.finding(
                message=(
                    f"Frame {gtol.id} lists datum {', '.join(duplicates)} more than once; a datum "
                    "reference frame must use each datum once."
                ),
                message_tr=(
                    f"{gtol.id} çerçevesinde {', '.join(duplicates)} datumu birden fazla kez "
                    "geçiyor; datum referans çerçevesinde her datum bir kez kullanılır."
                ),
                suggestion="Remove the repeated reference or replace it with the missing datum.",
                suggestion_tr="Tekrarlanan atıfı kaldırın veya eksik datumla değiştirin.",
                sheet_index=target.index,
                bbox=gtol.bbox,
                object_ids=[gtol.id],
                snippet=gtol.raw,
                agent=AGENT,
            )
