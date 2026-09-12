"""Dimensioning checks (ISO 129-1 / ASME Y14.5 completeness and clarity)."""

from __future__ import annotations

from collections.abc import Iterable

from catia_diff.models.drawing import DimensionKind, GeometryKind, Sheet
from catia_diff.models.findings import Category, Finding, Severity
from catia_diff.rules import analysis
from catia_diff.rules.base import Rule, RuleContext, RuleMeta, register

AGENT = "dimensioning"


@register
class UndimensionedFeatureRule(Rule):
    meta = RuleMeta(
        id="DIM001",
        title="Undimensioned feature",
        title_tr="Ölçülendirilmemiş unsur",
        severity=Severity.MAJOR,
        category=Category.DIMENSIONING,
        standards=("ISO 129-1 §4.1", "ASME Y14.5-2018 §1.4"),
        description="Every hole, radius and arc must carry a size dimension.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        _, undimensioned = analysis.associate_features(target)
        limit = ctx.config.undimensioned_feature_limit
        for feature in undimensioned[:limit]:
            size = (feature.radius or 0.0) * 2.0
            kind_en = "hole/circle" if feature.kind is GeometryKind.CIRCLE else "arc"
            kind_tr = "delik/daire" if feature.kind is GeometryKind.CIRCLE else "yay"
            yield self.finding(
                message=(
                    f"No dimension describes the {kind_en} at "
                    f"({feature.center.x:.2f}, {feature.center.y:.2f}) with ⌀{size:g}."
                ),
                message_tr=(
                    f"({feature.center.x:.2f}, {feature.center.y:.2f}) konumundaki ⌀{size:g} "
                    f"{kind_tr} için hiçbir ölçü bulunamadı."
                ),
                suggestion=f"Add a ⌀{size:g} (or R{size / 2:g}) callout, or a 'n x ⌀' pattern note.",
                suggestion_tr=f"⌀{size:g} (veya R{size / 2:g}) ölçüsü ya da 'n x ⌀' çoklu ölçü notu ekleyin.",
                sheet_index=target.index,
                bbox=feature.bbox,
                object_ids=[feature.id],
                confidence=0.7,
                agent=AGENT,
            )
        if len(undimensioned) > limit:
            yield self.finding(
                message=f"{len(undimensioned) - limit} further undimensioned features were not listed.",
                message_tr=f"{len(undimensioned) - limit} adet ölçülendirilmemiş unsur daha listelenmedi.",
                sheet_index=target.index,
                severity=Severity.INFO,
                confidence=0.7,
                agent=AGENT,
            )


@register
class DuplicateDimensionRule(Rule):
    meta = RuleMeta(
        id="DIM002",
        title="Duplicate dimension",
        title_tr="Tekrarlanmış ölçü",
        severity=Severity.MINOR,
        category=Category.DIMENSIONING,
        standards=("ISO 129-1 §4.3", "ASME Y14.5-2018 §1.4(e)"),
        description="The same value must not be dimensioned twice at the same place.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for first, second in analysis.duplicate_dimension_pairs(
            target, iou_threshold=ctx.config.duplicate_iou_threshold
        ):
            yield self.finding(
                message=(
                    f"Dimensions {first.id} and {second.id} repeat the same value "
                    f"({first.label()}) at the same location."
                ),
                message_tr=(
                    f"{first.id} ve {second.id} ölçüleri aynı konumda aynı değeri "
                    f"({first.label()}) tekrar ediyor."
                ),
                suggestion="Delete the duplicate, or mark one of them as reference - e.g. (20).",
                suggestion_tr="Fazla olanı silin veya birini referans olarak işaretleyin - örn. (20).",
                sheet_index=target.index,
                bbox=first.bbox.union(second.bbox) if first.bbox and second.bbox else None,
                object_ids=[first.id, second.id],
                confidence=0.8,
                agent=AGENT,
            )


@register
class ClosedChainRule(Rule):
    meta = RuleMeta(
        id="DIM003",
        title="Closed dimension chain (over-dimensioning)",
        title_tr="Kapalı ölçü zinciri (aşırı ölçülendirme)",
        severity=Severity.MAJOR,
        category=Category.DIMENSIONING,
        standards=("ISO 129-1 §6.4", "ASME Y14.5-2018 §1.4(m)"),
        description="A chain plus its overall dimension over-constrains the part.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for overall, parts in analysis.find_closed_chains(target):
            part_labels = " + ".join(dim.label() for dim in parts)
            yield self.finding(
                message=(
                    f"Closed chain: {part_labels} equals the overall dimension "
                    f"{overall.label()}. The same distance is dimensioned twice."
                ),
                message_tr=(
                    f"Kapalı zincir: {part_labels} toplamı, toplam ölçü {overall.label()} "
                    f"değerine eşit. Aynı mesafe iki kez ölçülendirilmiş."
                ),
                suggestion=(
                    "Remove one dimension of the chain or show it as auxiliary/reference, "
                    "so tolerances do not accumulate against a fixed overall size."
                ),
                suggestion_tr=(
                    "Zincirdeki ölçülerden birini kaldırın veya yardımcı/referans ölçü olarak "
                    "gösterin; aksi hâlde toleranslar toplam ölçüye karşı birikir."
                ),
                sheet_index=target.index,
                bbox=overall.bbox,
                object_ids=[overall.id, *(dim.id for dim in parts)],
                confidence=0.6,
                agent=AGENT,
            )


@register
class TextOverrideRule(Rule):
    meta = RuleMeta(
        id="DIM004",
        title="Dimension text does not match the geometry",
        title_tr="Ölçü metni geometriyle uyuşmuyor",
        severity=Severity.CRITICAL,
        category=Category.DIMENSIONING,
        standards=("ISO 129-1 §5.1",),
        description="A manually overridden dimension value hides the real model size.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for dim in target.dimensions:
            if not dim.is_text_override or dim.measured is None or dim.nominal is None:
                continue
            delta = dim.override_delta or 0.0
            yield self.finding(
                message=(
                    f"Dimension {dim.id} prints {dim.nominal:g} but the geometry measures "
                    f"{dim.measured:g} (Δ={delta:g}). The text was overridden manually."
                ),
                message_tr=(
                    f"{dim.id} ölçüsü {dim.nominal:g} yazıyor ancak geometri {dim.measured:g} "
                    f"ölçüyor (Δ={delta:g}). Ölçü metni elle değiştirilmiş."
                ),
                suggestion=(
                    "Restore the associative value (<>) and correct the model, or document why "
                    "the override is intentional."
                ),
                suggestion_tr=(
                    "İlişkili değeri (<>) geri getirip modeli düzeltin ya da değişikliğin neden "
                    "bilinçli yapıldığını belgeleyin."
                ),
                sheet_index=target.index,
                bbox=dim.bbox,
                object_ids=[dim.id],
                snippet=dim.text,
                agent=AGENT,
            )


@register
class MissingDiameterSymbolRule(Rule):
    meta = RuleMeta(
        id="DIM005",
        title="Diameter symbol missing",
        title_tr="Çap sembolü eksik",
        severity=Severity.MAJOR,
        category=Category.DIMENSIONING,
        standards=("ISO 129-1 §8.2", "ASME Y14.5-2018 §3.3.7"),
        description="A size dimension on a circular feature must carry ⌀ (or R).",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        covered, _ = analysis.associate_features(target)
        dims = {dim.id: dim for dim in target.dimensions}
        reported: set[str] = set()
        for feature_id, dim_ids in covered.items():
            feature = target.feature_by_id(feature_id)
            if feature is None or feature.kind is not GeometryKind.CIRCLE:
                continue
            for dim_id in dim_ids:
                dim = dims.get(dim_id)
                if dim is None or dim_id in reported:
                    continue
                if dim.kind in {DimensionKind.DIAMETER, DimensionKind.RADIAL, DimensionKind.THREAD}:
                    continue
                if dim.prefix or dim.extra.get("thread"):
                    continue
                reported.add(dim_id)
                yield self.finding(
                    message=(
                        f"Dimension {dim.id} ({dim.label()}) sizes a circular feature but has no "
                        "⌀ prefix."
                    ),
                    message_tr=(
                        f"{dim.id} ({dim.label()}) ölçüsü dairesel bir unsuru ölçüyor ancak "
                        "başında ⌀ sembolü yok."
                    ),
                    suggestion="Add the ⌀ prefix (or R for a radius) to the dimension text.",
                    suggestion_tr="Ölçü metninin başına ⌀ (yarıçap içinse R) sembolünü ekleyin.",
                    sheet_index=target.index,
                    bbox=dim.bbox,
                    object_ids=[dim.id, feature_id],
                    confidence=0.7,
                    agent=AGENT,
                )


@register
class NonPositiveDimensionRule(Rule):
    meta = RuleMeta(
        id="DIM006",
        title="Non-positive dimension value",
        title_tr="Sıfır veya negatif ölçü değeri",
        severity=Severity.CRITICAL,
        category=Category.DIMENSIONING,
        standards=("ISO 129-1 §5.1",),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for dim in target.dimensions:
            if dim.nominal is None or dim.nominal > 0:
                continue
            if dim.kind is DimensionKind.ORDINATE:  # ordinates may legitimately be 0
                continue
            yield self.finding(
                message=f"Dimension {dim.id} has a non-positive value ({dim.nominal:g}).",
                message_tr=f"{dim.id} ölçüsü pozitif olmayan bir değere sahip ({dim.nominal:g}).",
                suggestion="Correct the value; a size or distance cannot be zero or negative.",
                suggestion_tr="Değeri düzeltin; bir boyut veya mesafe sıfır ya da negatif olamaz.",
                sheet_index=target.index,
                bbox=dim.bbox,
                object_ids=[dim.id],
                snippet=dim.text,
                agent=AGENT,
            )


@register
class DimensionOutsideSheetRule(Rule):
    meta = RuleMeta(
        id="DIM007",
        title="Callout outside the sheet frame",
        title_tr="Çizim çerçevesi dışında kalan gösterim",
        severity=Severity.MAJOR,
        category=Category.DIMENSIONING,
        standards=("ISO 5457",),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for object_id in analysis.outside_sheet(target):
            obj = target.find_object(object_id)
            yield self.finding(
                message=f"Object {object_id} lies outside the sheet frame and will not print.",
                message_tr=f"{object_id} nesnesi çizim çerçevesinin dışında kalıyor ve baskıda görünmez.",
                suggestion="Move the callout inside the frame.",
                suggestion_tr="Gösterimi çerçevenin içine taşıyın.",
                sheet_index=target.index,
                bbox=obj.bbox if obj else None,
                object_ids=[object_id],
                agent=AGENT,
            )


@register
class OverlappingCalloutsRule(Rule):
    meta = RuleMeta(
        id="DIM008",
        title="Overlapping callouts",
        title_tr="Üst üste binen gösterimler",
        severity=Severity.MINOR,
        category=Category.DIMENSIONING,
        standards=("ISO 129-1 §4.2",),
        description="Dimension text must stay legible and must not be crossed by other text.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for first_id, second_id, iou in analysis.overlapping_labels(target):
            first = target.find_object(first_id)
            second = target.find_object(second_id)
            box = None
            if first is not None and first.bbox and second is not None and second.bbox:
                box = first.bbox.union(second.bbox)
            yield self.finding(
                message=f"Callouts {first_id} and {second_id} overlap ({iou:.0%} of their area).",
                message_tr=f"{first_id} ve {second_id} gösterimleri üst üste biniyor (alanın %{iou * 100:.0f}'i).",
                suggestion="Reposition one of the callouts so both stay readable.",
                suggestion_tr="Her ikisi de okunabilir kalacak şekilde birini kaydırın.",
                sheet_index=target.index,
                bbox=box,
                object_ids=[first_id, second_id],
                confidence=0.7,
                agent=AGENT,
            )


@register
class NoDimensionsRule(Rule):
    meta = RuleMeta(
        id="DIM009",
        title="Sheet carries no dimensions",
        title_tr="Sayfada hiç ölçü yok",
        severity=Severity.CRITICAL,
        category=Category.DIMENSIONING,
        standards=("ISO 129-1 §4.1",),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        if target.dimensions or target.is_empty():
            return
        yield self.finding(
            message="No dimension was found on this sheet, but it contains geometry.",
            message_tr="Bu sayfada geometri var ancak hiçbir ölçü bulunamadı.",
            suggestion=(
                "Check that the drawing is fully dimensioned; if the sheet is a scan, run the "
                "audit with --vision on."
            ),
            suggestion_tr=(
                "Resmin tam ölçülendirildiğini doğrulayın; sayfa taranmışsa denetimi "
                "--vision on ile çalıştırın."
            ),
            sheet_index=target.index,
            agent=AGENT,
        )


@register
class RadiusDiameterMismatchRule(Rule):
    meta = RuleMeta(
        id="DIM010",
        title="Radius/diameter callout mismatch",
        title_tr="Yarıçap/çap gösterimi uyumsuz",
        severity=Severity.MINOR,
        category=Category.DIMENSIONING,
        standards=("ISO 129-1 §8.3", "ASME Y14.5-2018 §3.3.6"),
        description="Full circles are dimensioned with ⌀, arcs with R.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        covered, _ = analysis.associate_features(target)
        dims = {dim.id: dim for dim in target.dimensions}
        for feature_id, dim_ids in covered.items():
            feature = target.feature_by_id(feature_id)
            if feature is None:
                continue
            for dim_id in dim_ids:
                dim = dims.get(dim_id)
                if dim is None:
                    continue
                radial = dim.kind is DimensionKind.RADIAL or dim.prefix in {"R", "SR"}
                diametral = dim.kind is DimensionKind.DIAMETER or (dim.prefix or "").endswith("⌀")
                if feature.kind is GeometryKind.CIRCLE and radial:
                    yield self._mismatch(dim, feature_id, target.index, to_diameter=True)
                elif feature.kind is GeometryKind.ARC and diametral:
                    sweep = float(feature.extra.get("sweep", 0.0) or 0.0)
                    if sweep < 300.0:  # a nearly-closed arc reads as a circle
                        yield self._mismatch(dim, feature_id, target.index, to_diameter=False)

    def _mismatch(self, dim, feature_id: str, sheet_index: int, *, to_diameter: bool) -> Finding:
        if to_diameter:
            message = f"Dimension {dim.id} uses R on a full circle; full circles take ⌀."
            message_tr = f"{dim.id} ölçüsü tam daire üzerinde R kullanıyor; tam daireler ⌀ ile ölçülendirilir."
            suggestion = "Replace the R prefix with ⌀ and double the value."
            suggestion_tr = "R sembolünü ⌀ ile değiştirip değeri iki katına çıkarın."
        else:
            message = f"Dimension {dim.id} uses ⌀ on an arc; arcs take R."
            message_tr = f"{dim.id} ölçüsü yay üzerinde ⌀ kullanıyor; yaylar R ile ölçülendirilir."
            suggestion = "Replace the ⌀ prefix with R and halve the value."
            suggestion_tr = "⌀ sembolünü R ile değiştirip değeri yarıya indirin."
        return self.finding(
            message=message,
            message_tr=message_tr,
            suggestion=suggestion,
            suggestion_tr=suggestion_tr,
            sheet_index=sheet_index,
            bbox=dim.bbox,
            object_ids=[dim.id, feature_id],
            confidence=0.7,
            agent=AGENT,
        )
