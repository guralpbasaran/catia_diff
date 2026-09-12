"""Surface finish and welding symbol checks (ISO 1302 / ISO 2553 / ASME Y14.36)."""

from __future__ import annotations

from collections.abc import Iterable

from catia_diff.models.drawing import Sheet, SurfaceSymbolKind
from catia_diff.models.findings import Category, Finding, Severity
from catia_diff.rules.base import Rule, RuleContext, RuleMeta, register

AGENT = "symbols"

#: Plausible Ra window for mechanical parts, in µm.
RA_MIN_UM = 0.01
RA_MAX_UM = 50.0


@register
class SurfaceFinishWithoutValueRule(Rule):
    meta = RuleMeta(
        id="SYM001",
        title="Surface finish symbol without a value",
        title_tr="Değeri olmayan yüzey pürüzlülüğü sembolü",
        severity=Severity.MAJOR,
        category=Category.SYMBOLS,
        standards=("ISO 1302", "ASME Y14.36M"),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for surface in target.surface_finishes:
            if surface.ra is not None or surface.rz is not None:
                continue
            if surface.symbol_kind is SurfaceSymbolKind.MACHINING_PROHIBITED:
                continue  # "as cast" legitimately carries no roughness value
            yield self.finding(
                message=f"Surface finish symbol {surface.id} carries no Ra/Rz value.",
                message_tr=f"{surface.id} yüzey sembolünde Ra/Rz değeri yok.",
                suggestion="Add the roughness value (e.g. Ra 3.2) or a general surface note.",
                suggestion_tr="Pürüzlülük değerini (örn. Ra 3,2) veya genel yüzey notunu ekleyin.",
                sheet_index=target.index,
                bbox=surface.bbox,
                object_ids=[surface.id],
                snippet=surface.raw,
                agent=AGENT,
            )


@register
class ImplausibleRoughnessRule(Rule):
    meta = RuleMeta(
        id="SYM002",
        title="Implausible roughness value",
        title_tr="Gerçekçi olmayan pürüzlülük değeri",
        severity=Severity.MINOR,
        category=Category.SYMBOLS,
        standards=("ISO 1302",),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for surface in target.surface_finishes:
            for label, value in (("Ra", surface.ra), ("Rz", surface.rz)):
                if value is None:
                    continue
                if RA_MIN_UM <= value <= RA_MAX_UM:
                    continue
                yield self.finding(
                    message=f"{label} {value:g} µm on {surface.id} is outside the usual range.",
                    message_tr=f"{surface.id} üzerindeki {label} {value:g} µm değeri olağan aralığın dışında.",
                    suggestion=f"Check the value; typical machining is {RA_MIN_UM}-{RA_MAX_UM} µm Ra.",
                    suggestion_tr=f"Değeri kontrol edin; tipik işleme aralığı {RA_MIN_UM}-{RA_MAX_UM} µm Ra'dır.",
                    sheet_index=target.index,
                    bbox=surface.bbox,
                    object_ids=[surface.id],
                    confidence=0.8,
                    agent=AGENT,
                )


@register
class WeldWithoutSizeRule(Rule):
    meta = RuleMeta(
        id="SYM003",
        title="Weld symbol without a size",
        title_tr="Ölçüsü olmayan kaynak sembolü",
        severity=Severity.MAJOR,
        category=Category.SYMBOLS,
        standards=("ISO 2553 §6", "AWS A2.4"),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for weld in target.welds:
            if weld.size is not None and weld.size > 0:
                continue
            yield self.finding(
                message=f"Weld symbol {weld.id} does not state a throat/leg size.",
                message_tr=f"{weld.id} kaynak sembolünde boğaz/dikiş ölçüsü belirtilmemiş.",
                suggestion="Add the size to the left of the symbol, e.g. a4 or z6.",
                suggestion_tr="Ölçüyü sembolün soluna yazın, örn. a4 veya z6.",
                sheet_index=target.index,
                bbox=weld.bbox,
                object_ids=[weld.id],
                snippet=weld.raw,
                agent=AGENT,
            )


@register
class IntermittentWeldRule(Rule):
    meta = RuleMeta(
        id="SYM004",
        title="Intermittent weld without pitch",
        title_tr="Adımı belirtilmemiş aralıklı kaynak",
        severity=Severity.MINOR,
        category=Category.SYMBOLS,
        standards=("ISO 2553 §6.4",),
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        for weld in target.welds:
            if weld.length is None or weld.pitch is not None:
                continue
            yield self.finding(
                message=f"Weld {weld.id} specifies a length ({weld.length:g}) but no pitch.",
                message_tr=f"{weld.id} kaynağında uzunluk ({weld.length:g}) var ancak adım yok.",
                suggestion="Write the intermittent weld as size-length x pitch, e.g. a4-50x100.",
                suggestion_tr="Aralıklı kaynağı ölçü-uzunluk x adım biçiminde yazın, örn. a4-50x100.",
                sheet_index=target.index,
                bbox=weld.bbox,
                object_ids=[weld.id],
                snippet=weld.raw,
                agent=AGENT,
            )


@register
class NoSurfaceSpecificationRule(Rule):
    meta = RuleMeta(
        id="SYM005",
        title="No surface roughness specified anywhere",
        title_tr="Hiçbir yerde yüzey pürüzlülüğü belirtilmemiş",
        severity=Severity.MINOR,
        category=Category.SYMBOLS,
        standards=("ISO 1302 §4",),
        description="Machined parts normally carry at least a general roughness note.",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        if target.surface_finishes or target.is_empty():
            return
        if target.title_block.has("surface_roughness") or target.title_block.has("surface_treatment"):
            return
        if "RA" in target.notes_text().upper():
            return
        yield self.finding(
            message="The sheet specifies no surface roughness, neither per surface nor as a general note.",
            message_tr="Sayfada ne yüzey bazında ne de genel not olarak yüzey pürüzlülüğü belirtilmiş.",
            suggestion="Add a general roughness note next to the title block, e.g. '√ Ra 3.2'.",
            suggestion_tr="Antedin yanına genel pürüzlülük notu ekleyin, örn. '√ Ra 3,2'.",
            sheet_index=target.index,
            confidence=0.8,
            agent=AGENT,
        )
