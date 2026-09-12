"""DXF ingestion (vector path).

DXF is the highest-fidelity input this tool accepts: dimensions carry their
own measurement, so the extractor can compare *what the drawing says* with
*what the geometry actually is* - the check no raster pipeline can do.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from catia_diff.config import AuditConfig
from catia_diff.errors import ExtractionError, MissingDependencyError
from catia_diff.extract.base import SourceExtractor
from catia_diff.extract.text_parsing import (
    classify_annotation,
    detect_general_tolerance,
    detect_units_note,
    normalize_drawing_text,
    parse_datum_feature,
    parse_dimension_text,
    parse_feature_control_frame,
    parse_surface_finish,
    parse_weld_symbol,
)
from catia_diff.extract.title_block_scan import TextItem, scan_title_block
from catia_diff.fields import canonical_field
from catia_diff.models.drawing import (
    Annotation,
    DatumFeature,
    DatumRef,
    Dimension,
    DimensionKind,
    DrawingDocument,
    GeometricTolerance,
    GeometryFeature,
    GeometryKind,
    ProjectionMethod,
    Sheet,
    SourceFormat,
    SurfaceFinish,
    TitleBlock,
    Tolerance,
    ToleranceKind,
    Units,
    View,
    WeldSymbol,
)
from catia_diff.models.geometry import BBox, CoordinateSpace, Point2D

#: DXF group code 70 (masked with 7) -> dimension kind.
_DIMTYPE_MAP: dict[int, DimensionKind] = {
    0: DimensionKind.LINEAR,
    1: DimensionKind.ALIGNED,
    2: DimensionKind.ANGULAR,
    3: DimensionKind.DIAMETER,
    4: DimensionKind.RADIAL,
    5: DimensionKind.ANGULAR,
    6: DimensionKind.ORDINATE,
}

#: $INSUNITS -> Units
_INSUNITS: dict[int, Units] = {
    1: Units.INCH,
    2: Units.INCH,  # feet, reported as imperial
    4: Units.MM,
    5: Units.CM,
    6: Units.M,
}

#: Upper bound on stored outline entities - they are only used for previews.
MAX_OUTLINE_FEATURES = 5000

_VIEW_LABEL_PATTERNS = (
    "SECTION",
    "DETAIL",
    "VIEW",
    "KESIT",
    "KESİT",
    "DETAY",
    "GÖRÜNÜŞ",
    "GORUNUS",
)


class DxfExtractor(SourceExtractor):
    name = "dxf"
    suffixes = (".dxf",)

    def extract(self, path: Path, config: AuditConfig) -> DrawingDocument:
        try:
            import ezdxf
            from ezdxf import recover
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise MissingDependencyError("ezdxf", "DXF extraction", extra="dxf") from exc

        warnings: list[str] = []
        try:
            doc = ezdxf.readfile(str(path))
        except ezdxf.DXFStructureError:
            doc, auditor = recover.readfile(str(path))
            warnings.append(
                f"DXF recovered with {len(auditor.errors)} structural error(s); "
                "extraction may be incomplete."
            )
        except OSError as exc:
            raise ExtractionError(f"cannot read {path}: {exc}") from exc

        units = _INSUNITS.get(int(doc.header.get("$INSUNITS", 0) or 0), Units.UNKNOWN)
        if units is Units.UNKNOWN:
            units = Units(config.default_units)
            warnings.append("$INSUNITS is not set in the DXF header; assuming " + units.value)

        document = DrawingDocument(
            source_path=path,
            source_format=SourceFormat.DXF,
            units=units,
            extractor=self.name,
            warnings=warnings,
            metadata={
                "dxf_version": doc.dxfversion,
                "acad_release": getattr(doc, "acad_release", "?"),
                "layers": sorted(layer.dxf.name for layer in doc.layers),
                "lu_precision": int(doc.header.get("$LUPREC", 4) or 4),
            },
        )

        for index, (name, entities) in enumerate(_iter_layouts(doc)):
            document.sheets.append(self._build_sheet(index, name, entities, units, config))
        if not document.sheets:
            document.warnings.append("the DXF file contains no drawable entity")
        return document

    # ------------------------------------------------------------------
    def _build_sheet(
        self, index: int, name: str, entities: list[Any], units: Units, config: AuditConfig
    ) -> Sheet:
        sheet = Sheet(
            index=index,
            name=name,
            units=units,
            coordinate_space=CoordinateSpace.Y_UP,
        )
        next_dim = self.id_factory("DIM")
        next_gtol = self.id_factory("GTOL")
        next_datum = self.id_factory("DATUM")
        next_note = self.id_factory("NOTE")
        next_feature = self.id_factory("FEAT")
        next_surface = self.id_factory("SURF")
        next_weld = self.id_factory("WELD")
        next_view = self.id_factory("VIEW")

        text_items: list[TextItem] = []
        title_block: TitleBlock | None = None
        boxes: list[BBox] = []

        for entity in entities:
            dxftype = entity.dxftype()
            try:
                if dxftype == "DIMENSION":
                    dim = _dimension_from_entity(entity, next_dim(), units, config)
                    if dim is not None:
                        sheet.dimensions.append(dim)
                        if dim.bbox:
                            boxes.append(dim.bbox)
                elif dxftype in {"TEXT", "MTEXT", "ATTRIB", "MULTILEADER", "LEADER"}:
                    raw, box = _text_and_box(entity)
                    if not raw:
                        continue
                    boxes.append(box)
                    text_items.append(TextItem(text=normalize_drawing_text(raw), bbox=box))
                    self._classify_text(
                        raw,
                        box,
                        sheet,
                        units,
                        config,
                        next_gtol,
                        next_datum,
                        next_note,
                        next_surface,
                        next_weld,
                        next_view,
                    )
                elif dxftype == "TOLERANCE":
                    raw = getattr(entity.dxf, "content", "")
                    box = _tolerance_box(entity)
                    boxes.append(box)
                    gtol = _gtol_from_text(raw, next_gtol(), box, entity)
                    if gtol is not None:
                        sheet.geometric_tolerances.append(gtol)
                elif dxftype in {"CIRCLE", "ARC"}:
                    feature = _feature_from_entity(entity, next_feature())
                    if feature is not None:
                        sheet.features.append(feature)
                        if feature.bbox:
                            boxes.append(feature.bbox)
                elif dxftype == "INSERT":
                    block, insert_boxes, insert_texts = _insert_content(entity)
                    boxes.extend(insert_boxes)
                    text_items.extend(insert_texts)
                    if block is not None and len(block.fields) >= 2:
                        title_block = _merge_title_blocks(title_block, block)
                elif dxftype in {"LINE", "LWPOLYLINE", "POLYLINE", "SPLINE", "ELLIPSE", "HATCH"}:
                    box = _entity_box(entity)
                    if box is not None:
                        boxes.append(box)
                    outline = _outline_from_entity(entity, next_feature)
                    if outline is not None and len(sheet.features) < MAX_OUTLINE_FEATURES:
                        sheet.features.append(outline)
            except Exception as exc:  # keep going: one bad entity must not kill the run
                sheet.warnings.append(f"{dxftype} skipped: {exc}")

        if boxes:
            extents = boxes[0]
            for box in boxes[1:]:
                extents = extents.union(box)
            margin = max(extents.width, extents.height) * 0.02 or 1.0
            extents = extents.expanded(margin)
            sheet.origin = Point2D(x=extents.x0, y=extents.y0)
            sheet.width = extents.width
            sheet.height = extents.height

        if title_block is None or len(title_block.fields) < 3:
            scanned = scan_title_block(text_items, sheet.bbox, CoordinateSpace.Y_UP)
            title_block = _merge_title_blocks(title_block, scanned)
        sheet.title_block = title_block or TitleBlock()
        _post_process_sheet(sheet, config)
        return sheet

    # ------------------------------------------------------------------
    def _classify_text(
        self,
        raw: str,
        box: BBox,
        sheet: Sheet,
        units: Units,
        config: AuditConfig,
        next_gtol,
        next_datum,
        next_note,
        next_surface,
        next_weld,
        next_view,
    ) -> None:
        """Route a text entity to the right bucket (FCF, datum, note, ...)."""
        text = normalize_drawing_text(raw)
        if not text:
            return

        gtol = _gtol_from_text(raw, next_gtol(), box, None)
        if gtol is not None:
            sheet.geometric_tolerances.append(gtol)
            return

        datum_label = parse_datum_feature(raw)
        if datum_label:
            sheet.datums.append(DatumFeature(id=next_datum(), label=datum_label, bbox=box))
            return

        surface = parse_surface_finish(raw)
        if surface is not None:
            sheet.surface_finishes.append(
                SurfaceFinish(
                    id=next_surface(),
                    bbox=box,
                    symbol_kind=surface.symbol_kind,
                    ra=surface.ra,
                    rz=surface.rz,
                    process=surface.process,
                    raw=surface.raw,
                )
            )
            return

        upper = text.upper()
        if any(pattern in upper for pattern in _VIEW_LABEL_PATTERNS):
            sheet.views.append(View(id=next_view(), label=text, bbox=box))
            return

        weld = parse_weld_symbol(raw)
        if weld is not None and weld.size is not None:
            sheet.welds.append(
                WeldSymbol(
                    id=next_weld(),
                    bbox=box,
                    weld_type=weld.weld_type,
                    size=weld.size,
                    length=weld.length,
                    pitch=weld.pitch,
                    all_around=weld.all_around,
                    field_weld=weld.field_weld,
                    raw=weld.raw,
                )
            )
            return

        sheet.annotations.append(
            Annotation(id=next_note(), text=text, bbox=box, category=classify_annotation(text))
        )


# --------------------------------------------------------------------------
# Entity helpers
# --------------------------------------------------------------------------
def _iter_layouts(doc: Any) -> list[tuple[str, list[Any]]]:
    """Return (layout name, entities); paper space layouts win when populated."""
    layouts: list[tuple[str, list[Any]]] = []
    for layout in doc.layouts:
        if layout.name.lower() == "model":
            continue
        entities = list(layout)
        if entities:
            layouts.append((layout.name, entities))
    if layouts:
        return layouts
    msp = list(doc.modelspace())
    return [("Model", msp)] if msp else []


def _point(value: Any) -> Point2D:
    return Point2D(x=float(value[0]), y=float(value[1]))


def _entity_box(entity: Any) -> BBox | None:
    try:
        from ezdxf import bbox as ezbbox

        extents = ezbbox.extents([entity], fast=True)
        if not extents.has_data:
            return None
        return BBox(
            x0=extents.extmin.x, y0=extents.extmin.y, x1=extents.extmax.x, y1=extents.extmax.y
        )
    except Exception:
        return None


def _text_and_box(entity: Any) -> tuple[str, BBox]:
    dxftype = entity.dxftype()
    if dxftype == "MTEXT":
        raw = entity.text
        insert = _point(entity.dxf.insert)
        height = float(getattr(entity.dxf, "char_height", 2.5) or 2.5)
        width = float(getattr(entity.dxf, "width", 0.0) or 0.0)
        lines = max(1, raw.count("\\P") + raw.count("\n") + 1)
        est_width = width or max(len(normalize_drawing_text(raw)), 1) * height * 0.62
        return raw, BBox(
            x0=insert.x, y0=insert.y - height * lines, x1=insert.x + est_width, y1=insert.y
        )
    if dxftype in {"TEXT", "ATTRIB"}:
        raw = entity.dxf.text
        insert = _point(entity.dxf.insert)
        height = float(getattr(entity.dxf, "height", 2.5) or 2.5)
        est_width = max(len(normalize_drawing_text(raw)), 1) * height * 0.62
        return raw, BBox(x0=insert.x, y0=insert.y, x1=insert.x + est_width, y1=insert.y + height)
    if dxftype == "MULTILEADER":
        raw = ""
        try:
            context = entity.context
            if context.mtext is not None:
                raw = context.mtext.default_content
        except Exception:
            raw = ""
        box = _entity_box(entity) or BBox(x0=0, y0=0, x1=1, y1=1)
        return raw, box
    if dxftype == "LEADER":
        box = _entity_box(entity) or BBox(x0=0, y0=0, x1=1, y1=1)
        return "", box
    return "", BBox(x0=0, y0=0, x1=1, y1=1)


def _tolerance_box(entity: Any) -> BBox:
    box = _entity_box(entity)
    if box is not None:
        return box
    insert = _point(getattr(entity.dxf, "insert", (0, 0, 0)))
    return BBox.around(insert, 10.0, 4.0)


def _dimension_from_entity(
    entity: Any, dim_id: str, units: Units, config: AuditConfig
) -> Dimension | None:
    kind = _DIMTYPE_MAP.get(int(entity.dimtype) & 7, DimensionKind.UNKNOWN)
    try:
        measured = float(entity.get_measurement())
    except Exception:
        measured = None
    if isinstance(measured, (tuple, list)):  # ordinate dimensions return a vector
        measured = float(measured[0])

    override_text = getattr(entity.dxf, "text", "") or ""
    style = _dimstyle_values(entity)
    prefix_from_style = _dimpost_prefix(style.get("dimpost"))

    display_text = override_text
    if display_text.strip() in {"", "<>"}:
        display_text = _format_measurement(measured, style, kind)
    parsed = parse_dimension_text(
        display_text, default_units=units if kind is not DimensionKind.ANGULAR else Units.DEG,
        measured=measured,
    )

    text_point = getattr(entity.dxf, "text_midpoint", None) or getattr(entity.dxf, "defpoint", None)
    anchor = _point(text_point) if text_point is not None else Point2D(x=0.0, y=0.0)
    text_height = float(style.get("dimtxt", 2.5) or 2.5)
    label_len = max(len(display_text.strip()) or 4, 4)
    bbox = BBox.around(anchor, label_len * text_height * 0.36, text_height * 0.9)

    tolerance = _tolerance_from_style(style)
    if parsed is not None and parsed.tolerance.is_specified:
        tolerance = parsed.tolerance

    nominal = parsed.nominal if parsed else measured
    prefix = (parsed.prefix if parsed and parsed.prefix else prefix_from_style)
    if kind is DimensionKind.DIAMETER and not prefix:
        prefix = "⌀"
    if kind is DimensionKind.RADIAL and not prefix:
        prefix = "R"

    axis_interval = _measurement_interval(entity, kind)
    dim = Dimension(
        id=dim_id,
        kind=kind,
        nominal=nominal,
        measured=measured,
        text=normalize_drawing_text(display_text),
        units=Units.DEG if kind is DimensionKind.ANGULAR else units,
        tolerance=tolerance,
        bbox=bbox,
        layer=getattr(entity.dxf, "layer", None),
        source_handle=getattr(entity.dxf, "handle", None),
        prefix=prefix,
        decimals=parsed.decimals if parsed else None,
        is_reference=bool(parsed and parsed.is_reference),
        is_basic=bool(parsed and parsed.is_basic),
        extra={
            "dimstyle": getattr(entity.dxf, "dimstyle", None),
            "multiplicity": parsed.multiplicity if parsed else 1,
            "thread": parsed.thread if parsed else None,
            "override_text": override_text,
            **(
                {"axis": axis_interval[0], "interval": list(axis_interval[1])}
                if axis_interval
                else {}
            ),
        },
    )
    # A literal text override that disagrees with the geometry is a classic,
    # high-impact defect - flag it here so the checker can report it.
    if override_text.strip() not in {"", "<>"} and dim.nominal is not None and measured is not None:
        rounded = _round_to(measured, style)
        dim.is_text_override = not math.isclose(
            dim.nominal, rounded, rel_tol=1e-4, abs_tol=max(1e-6, 10 ** -_decimals(style) / 2)
        )
    return dim


def _measurement_interval(entity: Any, kind: DimensionKind) -> tuple[float, tuple[float, float]] | None:
    """Project a linear dimension onto its own measurement axis.

    Returns ``(axis angle in degrees mod 180, (start, end))``.  This is what
    makes exact chain analysis possible: two dimensions belong to the same
    chain when their intervals are adjacent on the same axis.
    """
    if kind not in {DimensionKind.LINEAR, DimensionKind.ALIGNED}:
        return None
    try:
        first = _point(entity.dxf.defpoint2)
        second = _point(entity.dxf.defpoint3)
    except Exception:
        return None
    if kind is DimensionKind.LINEAR:
        angle = math.radians(float(getattr(entity.dxf, "angle", 0.0) or 0.0))
    else:
        angle = math.atan2(second.y - first.y, second.x - first.x)
    dx, dy = math.cos(angle), math.sin(angle)
    start = first.x * dx + first.y * dy
    end = second.x * dx + second.y * dy
    if math.isclose(start, end):
        return None
    return round(math.degrees(angle) % 180.0, 1), (min(start, end), max(start, end))


def _dimstyle_values(entity: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    try:
        override = entity.override()
    except Exception:
        return values
    for key in (
        "dimtol", "dimlim", "dimtp", "dimtm", "dimtxt", "dimpost", "dimdec", "dimrnd",
        "dimtdec", "dimlfac", "dimscale",
    ):
        try:
            value = override.get(key)
        except Exception:
            value = None
        if value is not None:
            values[key] = value
    return values


def _decimals(style: dict[str, Any]) -> int:
    for key in ("dimdec", "dimtdec"):
        value = style.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 2


def _round_to(value: float | None, style: dict[str, Any]) -> float | None:
    if value is None:
        return None
    rnd = style.get("dimrnd")
    try:
        rnd = float(rnd) if rnd else 0.0
    except (TypeError, ValueError):
        rnd = 0.0
    if rnd > 0:
        value = round(value / rnd) * rnd
    return round(value, _decimals(style))


def _format_measurement(
    measured: float | None, style: dict[str, Any], kind: DimensionKind
) -> str:
    if measured is None:
        return ""
    value = _round_to(measured, style)
    text = f"{value:.{_decimals(style)}f}"
    if kind is DimensionKind.ANGULAR:
        return f"{text}°"
    prefix = _dimpost_prefix(style.get("dimpost"))
    return f"{prefix}{text}" if prefix else text


def _dimpost_prefix(dimpost: Any) -> str | None:
    if not dimpost:
        return None
    text = normalize_drawing_text(str(dimpost))
    head = text.split("<>")[0].strip()
    return head or None


def _tolerance_from_style(style: dict[str, Any]) -> Tolerance:
    def as_float(key: str) -> float | None:
        value = style.get(key)
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    upper, lower = as_float("dimtp"), as_float("dimtm")
    if style.get("dimlim") and (upper is not None or lower is not None):
        return Tolerance(
            kind=ToleranceKind.LIMITS,
            upper=upper,
            lower=None if lower is None else -lower,
            raw="dimlim",
        )
    if style.get("dimtol") and (upper or lower):
        upper = 0.0 if upper is None else upper
        lower = 0.0 if lower is None else -abs(lower)
        kind = ToleranceKind.SYMMETRIC if abs(upper) == abs(lower) and upper else ToleranceKind.DEVIATION
        return Tolerance(kind=kind, upper=upper, lower=lower, raw="dimtol")
    return Tolerance()


def _gtol_from_text(raw: str, gtol_id: str, box: BBox, entity: Any) -> GeometricTolerance | None:
    parsed = parse_feature_control_frame(raw)
    if parsed is None:
        return None
    return GeometricTolerance(
        id=gtol_id,
        bbox=box,
        characteristic=parsed.characteristic,
        value=parsed.value,
        diametral_zone=parsed.diametral_zone,
        material_condition=parsed.material_condition,
        datums=[DatumRef(label=label, material_condition=mc) for label, mc in parsed.datums],
        layer=getattr(getattr(entity, "dxf", None), "layer", None) if entity is not None else None,
        raw=parsed.raw,
    )


def _feature_from_entity(entity: Any, feature_id: str) -> GeometryFeature | None:
    dxftype = entity.dxftype()
    center = _point(entity.dxf.center)
    radius = float(entity.dxf.radius)
    if radius <= 0:
        return None
    if dxftype == "CIRCLE":
        return GeometryFeature(
            id=feature_id,
            kind=GeometryKind.CIRCLE,
            center=center,
            radius=radius,
            closed=True,
            bbox=BBox.around(center, radius),
            layer=getattr(entity.dxf, "layer", None),
            source_handle=getattr(entity.dxf, "handle", None),
        )
    start = float(entity.dxf.start_angle)
    end = float(entity.dxf.end_angle)
    sweep = (end - start) % 360.0
    return GeometryFeature(
        id=feature_id,
        kind=GeometryKind.ARC,
        center=center,
        radius=radius,
        length=math.radians(sweep) * radius,
        closed=False,
        bbox=BBox.around(center, radius),
        layer=getattr(entity.dxf, "layer", None),
        source_handle=getattr(entity.dxf, "handle", None),
        extra={"start_angle": start, "end_angle": end, "sweep": sweep},
    )


def _outline_from_entity(entity: Any, next_feature) -> GeometryFeature | None:
    """Store line/polyline vertices so the report can draw the sheet."""
    dxftype = entity.dxftype()
    points: list[Point2D] = []
    kind = GeometryKind.POLYLINE
    closed = False
    if dxftype == "LINE":
        points = [_point(entity.dxf.start), _point(entity.dxf.end)]
        kind = GeometryKind.LINE
    elif dxftype == "LWPOLYLINE":
        points = [Point2D(x=float(p[0]), y=float(p[1])) for p in entity.get_points("xy")]
        closed = bool(entity.closed)
    elif dxftype == "POLYLINE":
        points = [_point(vertex.dxf.location) for vertex in entity.vertices]
        closed = bool(entity.is_closed)
    else:
        return None
    if len(points) < 2:
        return None
    if closed:
        points.append(points[0])
    return GeometryFeature(
        id=next_feature(),
        kind=kind,
        points=points,
        closed=closed,
        bbox=BBox.from_points(points),
        layer=getattr(entity.dxf, "layer", None),
        source_handle=getattr(entity.dxf, "handle", None),
    )


def _insert_content(entity: Any) -> tuple[TitleBlock | None, list[BBox], list[TextItem]]:
    """Read block attributes; a block with named fields is usually the title block."""
    boxes: list[BBox] = []
    texts: list[TextItem] = []
    block = TitleBlock(source="block_attributes")
    for attrib in getattr(entity, "attribs", []):
        raw, box = _text_and_box(attrib)
        value = normalize_drawing_text(raw)
        boxes.append(box)
        if value:
            texts.append(TextItem(text=value, bbox=box))
        tag = getattr(attrib.dxf, "tag", "")
        field = canonical_field(tag)
        if field and value:
            block.set(field, value, bbox=box, confidence=0.95)
    if block.fields:
        block.detected = True
        merged = boxes[0]
        for box in boxes[1:]:
            merged = merged.union(box)
        block.bbox = merged
        return block, boxes, texts
    return None, boxes, texts


def _merge_title_blocks(primary: TitleBlock | None, secondary: TitleBlock | None) -> TitleBlock:
    if primary is None:
        return secondary or TitleBlock()
    if secondary is None:
        return primary
    merged = primary.model_copy(deep=True)
    for name, field in secondary.fields.items():
        current = merged.get(name)
        if current is None or current.is_empty or current.confidence < field.confidence:
            merged.fields[name] = field
    merged.detected = merged.detected or secondary.detected
    if merged.bbox and secondary.bbox:
        merged.bbox = merged.bbox.union(secondary.bbox)
    else:
        merged.bbox = merged.bbox or secondary.bbox
    return merged


def _post_process_sheet(sheet: Sheet, config: AuditConfig) -> None:
    """Derive sheet-level facts from the extracted annotations."""
    notes = sheet.notes_text()
    general = detect_general_tolerance(notes) or detect_general_tolerance(
        sheet.title_block.value("general_tolerance") or ""
    )
    if general and not sheet.title_block.has("general_tolerance"):
        sheet.title_block.set("general_tolerance", general, confidence=0.7)

    units_note = detect_units_note(notes)
    if units_note is not None:
        sheet.units = units_note
        if not sheet.title_block.has("units"):
            sheet.title_block.set("units", units_note.value, confidence=0.7)

    upper = notes.upper()
    if "FIRST ANGLE" in upper or "1. AÇI" in upper or "1.ACI" in upper:
        sheet.projection = ProjectionMethod.FIRST_ANGLE
    elif "THIRD ANGLE" in upper or "3. AÇI" in upper or "3.ACI" in upper:
        sheet.projection = ProjectionMethod.THIRD_ANGLE

    stated = sheet.title_block.value("scale")
    if stated:
        sheet.stated_scale = stated
        sheet.scale = _parse_scale(stated)


def _parse_scale(text: str) -> float | None:
    import re

    match = re.search(r"(\d+(?:[.,]\d+)?)\s*[:/]\s*(\d+(?:[.,]\d+)?)", text)
    if not match:
        return None
    try:
        num = float(match.group(1).replace(",", "."))
        den = float(match.group(2).replace(",", "."))
    except ValueError:
        return None
    return num / den if den else None
