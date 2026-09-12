"""Convert a :class:`VisionSheetExtraction` into domain objects."""

from __future__ import annotations

import itertools

from catia_diff.extract.text_parsing import (
    classify_annotation,
    parse_datum_feature,
    parse_dimension_text,
    parse_feature_control_frame,
    parse_surface_finish,
    parse_weld_symbol,
)
from catia_diff.extract.title_block_scan import set_field
from catia_diff.fields import canonical_field
from catia_diff.llm.schemas import VisionBox, VisionSheetExtraction
from catia_diff.models.drawing import (
    Annotation,
    DatumFeature,
    DatumRef,
    Dimension,
    DimensionKind,
    GeometricTolerance,
    Sheet,
    SurfaceFinish,
    SurfaceSymbolKind,
    Units,
    View,
    WeldSymbol,
)
from catia_diff.models.geometry import BBox

_VISION_KIND: dict[str, DimensionKind] = {
    "linear": DimensionKind.LINEAR,
    "angular": DimensionKind.ANGULAR,
    "diameter": DimensionKind.DIAMETER,
    "radial": DimensionKind.RADIAL,
    "thread": DimensionKind.THREAD,
    "ordinate": DimensionKind.ORDINATE,
    "other": DimensionKind.UNKNOWN,
}

#: Vision output is inherently less certain than a parsed DXF entity.
VISION_CONFIDENCE = 0.7


def merge_extraction(
    sheet: Sheet,
    extraction: VisionSheetExtraction,
    *,
    tile_box: BBox | None = None,
    image_size: tuple[float, float] | None = None,
    id_suffix: str = "V",
) -> int:
    """Merge vision output into ``sheet``; returns the number of objects added."""
    counter = itertools.count(1)

    def next_id(prefix: str) -> str:
        return f"{prefix}{id_suffix}{next(counter):04d}"

    def to_sheet_box(box: VisionBox) -> BBox:
        return _project(box, sheet, tile_box, image_size)

    added = 0
    for item in extraction.dimensions:
        parsed = parse_dimension_text(item.text, default_units=sheet.units)
        if parsed is None:
            continue
        kind = _VISION_KIND.get(item.kind, DimensionKind.UNKNOWN)
        if parsed.kind_hint is not DimensionKind.UNKNOWN:
            kind = parsed.kind_hint
        sheet.dimensions.append(
            Dimension(
                id=next_id("DIM"),
                kind=kind,
                nominal=parsed.nominal,
                text=parsed.text,
                units=parsed.units if parsed.units is not Units.UNKNOWN else sheet.units,
                tolerance=parsed.tolerance,
                bbox=to_sheet_box(item.box),
                prefix=parsed.prefix,
                decimals=parsed.decimals,
                is_reference=parsed.is_reference,
                is_basic=parsed.is_basic,
                confidence=VISION_CONFIDENCE,
                extra={"source": "vision", "multiplicity": parsed.multiplicity},
            )
        )
        added += 1

    for symbol in extraction.symbols:
        box = to_sheet_box(symbol.box)
        if symbol.kind == "feature_control_frame":
            fcf = parse_feature_control_frame(symbol.text)
            if fcf is None:
                continue
            sheet.geometric_tolerances.append(
                GeometricTolerance(
                    id=next_id("GTOL"),
                    bbox=box,
                    characteristic=fcf.characteristic,
                    value=fcf.value,
                    diametral_zone=fcf.diametral_zone,
                    material_condition=fcf.material_condition,
                    datums=[DatumRef(label=lbl, material_condition=mc) for lbl, mc in fcf.datums],
                    raw=fcf.raw,
                    confidence=VISION_CONFIDENCE,
                    extra={"source": "vision"},
                )
            )
        elif symbol.kind == "datum":
            label = parse_datum_feature(symbol.text) or symbol.text.strip(" -[]")[:1].upper()
            if not label:
                continue
            sheet.datums.append(
                DatumFeature(
                    id=next_id("DATUM"),
                    label=label,
                    bbox=box,
                    confidence=VISION_CONFIDENCE,
                    extra={"source": "vision"},
                )
            )
        elif symbol.kind == "surface_finish":
            surface = parse_surface_finish(symbol.text)
            sheet.surface_finishes.append(
                SurfaceFinish(
                    id=next_id("SURF"),
                    bbox=box,
                    symbol_kind=surface.symbol_kind if surface else SurfaceSymbolKind.BASIC,
                    ra=surface.ra if surface else None,
                    rz=surface.rz if surface else None,
                    process=surface.process if surface else None,
                    raw=symbol.text,
                    confidence=VISION_CONFIDENCE,
                    extra={"source": "vision"},
                )
            )
        elif symbol.kind == "weld":
            weld = parse_weld_symbol(symbol.text)
            sheet.welds.append(
                WeldSymbol(
                    id=next_id("WELD"),
                    bbox=box,
                    weld_type=weld.weld_type if weld else None,
                    size=weld.size if weld else None,
                    length=weld.length if weld else None,
                    pitch=weld.pitch if weld else None,
                    all_around=bool(weld and weld.all_around),
                    field_weld=bool(weld and weld.field_weld),
                    raw=symbol.text,
                    confidence=VISION_CONFIDENCE,
                    extra={"source": "vision"},
                )
            )
        else:
            sheet.annotations.append(
                Annotation(
                    id=next_id("NOTE"),
                    text=symbol.text,
                    bbox=box,
                    category=classify_annotation(symbol.text),
                    confidence=VISION_CONFIDENCE,
                    extra={"source": "vision"},
                )
            )
        added += 1

    for cell in extraction.title_block:
        field = canonical_field(cell.label)
        if field is None:
            continue
        set_field(sheet.title_block, field, (cell.value or "").strip(), to_sheet_box(cell.box), VISION_CONFIDENCE)
        sheet.title_block.detected = True
        sheet.title_block.source = "vision"
        added += 1

    for view in extraction.views:
        sheet.views.append(
            View(
                id=next_id("VIEW"),
                label=view.label,
                bbox=to_sheet_box(view.box),
                confidence=VISION_CONFIDENCE,
            )
        )
        added += 1

    for note in extraction.notes:
        sheet.annotations.append(
            Annotation(
                id=next_id("NOTE"),
                text=note.text,
                bbox=to_sheet_box(note.box),
                category=classify_annotation(note.text),
                confidence=VISION_CONFIDENCE,
                extra={"source": "vision"},
            )
        )
        added += 1

    for region in extraction.illegible_regions:
        sheet.warnings.append(f"illegible region at {to_sheet_box(region).as_tuple()}")
    sheet.warnings.extend(f"vision: {text}" for text in extraction.observations)
    return added


def _project(
    box: VisionBox,
    sheet: Sheet,
    tile_box: BBox | None,
    image_size: tuple[float, float] | None,
) -> BBox:
    """Map a normalised tile box onto sheet coordinates."""
    if tile_box is not None:
        local = BBox(
            x0=tile_box.x0 + box.x0 * tile_box.width,
            y0=tile_box.y0 + box.y0 * tile_box.height,
            x1=tile_box.x0 + box.x1 * tile_box.width,
            y1=tile_box.y0 + box.y1 * tile_box.height,
        )
        if image_size and image_size[0] and image_size[1]:
            sx = sheet.width / image_size[0]
            sy = sheet.height / image_size[1]
            local = local.scaled(sx, sy)
        return local.translated(sheet.origin.x, sheet.origin.y)
    return BBox(
        x0=sheet.origin.x + box.x0 * sheet.width,
        y0=sheet.origin.y + box.y0 * sheet.height,
        x1=sheet.origin.x + box.x1 * sheet.width,
        y1=sheet.origin.y + box.y1 * sheet.height,
    )
