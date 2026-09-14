"""Dimensioning checks (ISO 129-1 / ASME Y14.5 completeness and clarity)."""

from __future__ import annotations

from collections.abc import Iterable

from catia_diff.extract.text_parsing import BLANKET_RADII
from catia_diff.models.drawing import (
    DimensionKind,
    GDTCharacteristic,
    GeometryFeature,
    GeometryKind,
    Sheet,
    View,
)
from catia_diff.models.findings import Category, Finding, Severity
from catia_diff.rules import analysis
from catia_diff.rules.base import Rule, RuleContext, RuleMeta, register
from catia_diff.rules.constraints import AxisCoverage, ReferenceNode

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
        if BLANKET_RADII in ctx.blanket_notes(target):
            # "ALL FILLETS R3" covers exactly these callouts.
            undimensioned = [f for f in undimensioned if f.kind is not GeometryKind.ARC]
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
        description=(
            "A chain plus its overall dimension over-constrains the part: in the "
            "constraint graph that is a cycle, and every cycle is one dimension too many."
        ),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for cycle in analysis.find_closed_chains(target):
            overall, parts = cycle.overall, cycle.parts
            where = f" along {cycle.label}" if cycle.exact else ""
            where_tr = f"{cycle.label} ekseninde " if cycle.exact else ""
            if len(parts) == 1:
                # A cycle of length two is the same distance measured twice.
                twin = parts[0]
                message = (
                    f"{overall.label()} duplicates {twin.id}{where}: both dimension the "
                    "same distance."
                )
                message_tr = (
                    f"{where_tr}{overall.label()} ölçüsü {twin.id} ile aynı mesafeyi "
                    "ölçüyor; ölçü tekrar edilmiş."
                )
            else:
                part_labels = " + ".join(dim.label() for dim in parts)
                message = (
                    f"{overall.label()} is redundant{where}: {part_labels} already fixes the "
                    "same distance, so the drawing constrains it twice."
                )
                message_tr = (
                    f"{where_tr}{overall.label()} ölçüsü fazla: {part_labels} zaten aynı "
                    "mesafeyi belirliyor, yani resim bu mesafeyi iki kez kısıtlıyor."
                )
            yield self.finding(
                message=message,
                message_tr=message_tr,
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
                object_ids=[dim.id for dim in cycle.dimensions],
                confidence=cycle.confidence,
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


# --------------------------------------------------------------------------
# Dimensional coverage: positions the drawing does not let you derive
# --------------------------------------------------------------------------
def _feature(sheet: Sheet, feature_id: str) -> GeometryFeature | None:
    return sheet.feature_by_id(feature_id)


def _position_controlled(sheet: Sheet, view: View) -> bool:
    """True when GD&T locates the features of this view instead of dimensions.

    A hole placed by basic dimensions plus a position frame is located - by a
    different mechanism, but located; reporting it as free would be wrong.
    """
    members = set(view.member_ids)
    has_position = any(
        gtol.characteristic is GDTCharacteristic.POSITION
        for gtol in sheet.geometric_tolerances
        if gtol.id in members or gtol.view_id == view.id
    )
    has_basic = any(dim.is_basic for dim in sheet.dimensions if dim.id in members)
    return has_position and has_basic


def _pattern_located(sheet: Sheet, view: View, features: list[GeometryFeature]) -> bool:
    """True when a pattern note ("4x ⌀6.5 EQUALLY SPACED") places the features."""
    if not features:
        return False
    members = set(view.member_ids)
    for dim in sheet.dimensions:
        if dim.id not in members:
            continue
        multiplicity = int(dim.extra.get("multiplicity", 1) or 1)
        if multiplicity < len(features):
            continue
        text = dim.text.upper()
        if "EŞİT" in text or "ESIT" in text or "EQUALLY" in text or "SPACED" in text:
            return True
    return False


def _node_features(sheet: Sheet, nodes: list[ReferenceNode]) -> list[GeometryFeature]:
    out: list[GeometryFeature] = []
    for node in nodes:
        for feature_id in node.feature_ids:
            feature = _feature(sheet, feature_id)
            if feature is not None and feature.center is not None:
                out.append(feature)
    return out


def _describe_feature(feature: GeometryFeature) -> str:
    if feature.radius:
        return f"⌀{feature.radius * 2:g}"
    return feature.kind.value


@register
class UnlocatedFeatureRule(Rule):
    meta = RuleMeta(
        id="DIM011",
        title="Feature is not located",
        title_tr="Unsurun konumu belirlenmemiş",
        severity=Severity.CRITICAL,
        category=Category.DIMENSIONING,
        standards=("ISO 129-1 §4.1", "ASME Y14.5-2018 §1.4(b)"),
        description=(
            "A hole may carry its size and still be unbuildable: no dimension chain "
            "reaches its centre along one of the axes."
        ),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for view, axes in ctx.coverage(target):
            if _position_controlled(target, view):
                continue
            for coverage in axes:
                if ctx.axis_inherited(target, view, coverage.label):
                    continue  # an aligned view already fixes this axis
                for group in coverage.unconstrained:
                    features = _node_features(target, group)
                    if not features or _pattern_located(target, view, features):
                        continue
                    yield self._finding(target, view, coverage, group, features)

    def _finding(self, sheet, view, coverage: AxisCoverage, group, features) -> Finding:
        names = ", ".join(feature.id for feature in features)
        sizes = ", ".join(sorted({_describe_feature(f) for f in features}))
        position = ", ".join(
            f"({f.center.x:.1f}, {f.center.y:.1f})" for f in features[:3] if f.center
        )
        box = features[0].bbox
        for feature in features[1:]:
            if feature.bbox and box:
                box = box.union(feature.bbox)
        return self.finding(
            message=(
                f"{sizes} at {position} is not located along {coverage.label}: no dimension "
                f"reaches coordinate {group[0].coordinate:g} from the rest of the view."
            ),
            message_tr=(
                f"{position} konumundaki {sizes} unsuru {coverage.label} ekseninde "
                f"konumlandırılmamış: {group[0].coordinate:g} koordinatına hiçbir ölçü ulaşmıyor."
            ),
            suggestion=(
                f"Add a {coverage.label} dimension from a datum edge (or an existing dimension) "
                "to this feature."
            ),
            suggestion_tr=(
                f"Bu unsura, bir referans kenardan (veya mevcut bir ölçüden) {coverage.label} "
                "ekseninde ölçü ekleyin."
            ),
            sheet_index=sheet.index,
            bbox=box,
            object_ids=[feature.id for feature in features],
            snippet=names,
            confidence=0.9,
            agent=AGENT,
        )


@register
class UnconstrainedGeometryRule(Rule):
    meta = RuleMeta(
        id="DIM012",
        title="Geometry is not tied into the dimension chain",
        title_tr="Geometri ölçü zincirine bağlanmamış",
        severity=Severity.MAJOR,
        category=Category.DIMENSIONING,
        standards=("ISO 129-1 §6", "ASME Y14.5-2018 §1.4"),
        description="A coordinate no dimension reaches cannot be manufactured to.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for view, axes in ctx.coverage(target):
            for coverage in axes:
                if ctx.axis_inherited(target, view, coverage.label):
                    continue  # an aligned view already fixes this axis
                for group in coverage.unconstrained:
                    if _node_features(target, group):
                        continue  # DIM011 names the feature instead
                    coordinates = ", ".join(f"{node.coordinate:g}" for node in group[:4])
                    yield self.finding(
                        message=(
                            f"View {view.label or view.id}: coordinate(s) {coordinates} are not "
                            f"connected to the rest of the drawing along {coverage.label}; "
                            f"{coverage.missing} dimension(s) are missing."
                        ),
                        message_tr=(
                            f"{view.label or view.id} görünüşü: {coordinates} koordinat(lar)ı "
                            f"{coverage.label} ekseninde resmin geri kalanına bağlı değil; "
                            f"{coverage.missing} ölçü eksik."
                        ),
                        suggestion="Dimension this geometry from an existing reference.",
                        suggestion_tr="Bu geometriyi mevcut bir referanstan ölçülendirin.",
                        sheet_index=target.index,
                        bbox=view.bbox,
                        object_ids=[
                            feature_id for node in group for feature_id in node.feature_ids
                        ][:10],
                        confidence=0.8,
                        agent=AGENT,
                    )


@register
class ViewWithoutDimensionsRule(Rule):
    meta = RuleMeta(
        id="DIM013",
        title="View carries no dimensions at all",
        title_tr="Görünüşte hiç ölçü yok",
        severity=Severity.CRITICAL,
        category=Category.DIMENSIONING,
        standards=("ISO 129-1 §4.1",),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        if not ctx.coverage(target):
            return
        for view in target.views:
            if not view.is_geometric:
                continue
            members = set(view.member_ids)
            if any(dim.id in members for dim in target.dimensions):
                continue
            features = [f for f in target.features if f.id in members]
            if len(features) < 2:
                continue  # a stray symbol is not a view
            yield self.finding(
                message=(
                    f"View {view.label or view.id} contains {len(features)} geometric entities "
                    "but not a single dimension."
                ),
                message_tr=(
                    f"{view.label or view.id} görünüşünde {len(features)} geometri nesnesi var "
                    "ancak tek bir ölçü bile yok."
                ),
                suggestion=(
                    "Dimension the view, or mark it as a reference/illustrative view if it is "
                    "intentionally undimensioned."
                ),
                suggestion_tr=(
                    "Görünüşü ölçülendirin; bilinçli olarak ölçüsüz bırakıldıysa referans/"
                    "açıklayıcı görünüş olarak işaretleyin."
                ),
                sheet_index=target.index,
                bbox=view.bbox,
                object_ids=[view.id],
                agent=AGENT,
            )


@register
class MissingOverallSizeRule(Rule):
    meta = RuleMeta(
        id="DIM014",
        title="Overall size not dimensioned",
        title_tr="Toplam ölçü verilmemiş",
        severity=Severity.MINOR,
        category=Category.DIMENSIONING,
        standards=("ISO 129-1 §6.3",),
        description=(
            "Even a fully chained view should state its overall size so stock and "
            "inspection do not have to add the chain up."
        ),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for view, axes in ctx.coverage(target):
            members = set(view.member_ids)
            for coverage in axes:
                extent = coverage.extent
                if extent is None or not coverage.is_complete() or len(coverage.nodes) < 3:
                    continue
                if ctx.axis_inherited(target, view, coverage.label):
                    continue  # the overall size is stated in the aligned view
                span = extent[1] - extent[0]
                if span <= 0:
                    continue
                tolerance = max(span * 1e-3, 1e-9)
                spanned = any(
                    _spans(dim.extra.get("interval"), extent, tolerance)
                    for dim in target.dimensions
                    if dim.id in members and _same_axis(dim, coverage.axis)
                )
                if spanned:
                    continue
                yield self.finding(
                    message=(
                        f"View {view.label or view.id} has no overall {coverage.label} dimension "
                        f"({span:g} across the whole view)."
                    ),
                    message_tr=(
                        f"{view.label or view.id} görünüşünde toplam {coverage.label} ölçüsü yok "
                        f"(görünüş boyunca {span:g})."
                    ),
                    suggestion=f"Add the overall {coverage.label} dimension, as an auxiliary one if needed.",
                    suggestion_tr=f"Toplam {coverage.label} ölçüsünü, gerekirse yardımcı ölçü olarak ekleyin.",
                    sheet_index=target.index,
                    bbox=view.bbox,
                    object_ids=[view.id],
                    confidence=0.8,
                    agent=AGENT,
                )


def _same_axis(dim, axis: float) -> bool:
    value = dim.extra.get("axis")
    return value is not None and abs(float(value) % 180.0 - axis) <= 0.5


def _spans(interval, extent: tuple[float, float], tolerance: float) -> bool:
    if not interval or len(interval) != 2:
        return False
    low, high = min(interval), max(interval)
    return abs(low - extent[0]) <= tolerance and abs(high - extent[1]) <= tolerance


@register
class ObliqueEdgeWithoutAngleRule(Rule):
    meta = RuleMeta(
        id="DIM015",
        title="Oblique edge without an angular dimension",
        title_tr="Açısı verilmemiş eğik kenar",
        severity=Severity.MINOR,
        category=Category.DIMENSIONING,
        standards=("ISO 129-1 §9", "ASME Y14.5-2018 §3.3.4"),
        description=(
            "A slanted edge is defined by its angle or by coordinates; when the drawing "
            "states neither, the angle can only be back-calculated."
        ),
    )
    #: Only edges longer than this fraction of the view are worth an angle.
    MIN_LENGTH_RATIO = 0.10

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        import math

        for view, axes in ctx.coverage(target):
            members = set(view.member_ids)
            dims = [dim for dim in target.dimensions if dim.id in members]
            if any(dim.is_angular for dim in dims):
                continue
            oblique_axes = {round(cov.axis, 1) for cov in axes} - {0.0, 90.0}
            view_size = max(view.bbox.width, view.bbox.height) if view.bbox else 0.0
            if view_size <= 0:
                continue

            longest: tuple[float, float, str] | None = None  # (length, angle, feature id)
            for feature in target.features:
                if feature.id not in members or not feature.points:
                    continue
                for start, end in zip(feature.points, feature.points[1:], strict=False):
                    length = math.hypot(end.x - start.x, end.y - start.y)
                    if length < view_size * self.MIN_LENGTH_RATIO:
                        continue
                    angle = math.degrees(math.atan2(end.y - start.y, end.x - start.x)) % 180.0
                    if min(angle, abs(angle - 90.0), abs(angle - 180.0)) <= 1.0:
                        continue  # axis-parallel
                    if any(abs(angle - axis) <= 1.0 for axis in oblique_axes):
                        continue  # measured along its own direction
                    if longest is None or length > longest[0]:
                        longest = (length, angle, feature.id)
            if longest is None:
                continue
            _, angle, feature_id = longest
            yield self.finding(
                message=(
                    f"View {view.label or view.id} has a {angle:.1f}° edge but no angular "
                    "dimension; the angle has to be calculated from the geometry."
                ),
                message_tr=(
                    f"{view.label or view.id} görünüşünde {angle:.1f}°'lik bir kenar var ancak "
                    "açı ölçüsü yok; açı geometriden hesaplanmak zorunda."
                ),
                suggestion="State the angle, or locate both endpoints with coordinate dimensions.",
                suggestion_tr="Açıyı belirtin ya da iki uç noktayı koordinat ölçüleriyle konumlandırın.",
                sheet_index=target.index,
                bbox=target.feature_by_id(feature_id).bbox if target.feature_by_id(feature_id) else view.bbox,
                object_ids=[feature_id],
                confidence=0.6,
                agent=AGENT,
            )
