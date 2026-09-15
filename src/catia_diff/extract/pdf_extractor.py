"""PDF ingestion.

A PDF exported from CATIA/SolidWorks/AutoCAD keeps its text and vector
geometry, so it can be parsed exactly.  A scanned PDF has neither: those pages
are rasterised and marked ``needs_vision`` so the extraction agent can hand
them to Claude Vision.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from catia_diff.config import AuditConfig
from catia_diff.errors import ExtractionError, MissingDependencyError
from catia_diff.extract import pdf_vector
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
    parse_thickness,
    parse_weld_symbol,
    to_float,
)
from catia_diff.extract.title_block_scan import TextItem, scan_title_block, title_block_region
from catia_diff.fields import canonical_field
from catia_diff.models.drawing import (
    Annotation,
    DatumFeature,
    DatumRef,
    Dimension,
    DimensionKind,
    DrawingDocument,
    GeometricTolerance,
    ProjectionMethod,
    Sheet,
    SourceFormat,
    SurfaceFinish,
    Tolerance,
    ToleranceKind,
    Units,
    View,
    WeldSymbol,
)
from catia_diff.models.geometry import BBox, CoordinateSpace

#: Below this many characters a page is treated as a scan.
MIN_TEXT_CHARS = 40
_VIEW_LABEL_PATTERNS = ("SECTION", "DETAIL", "VIEW", "KESIT", "KESİT", "DETAY", "GÖRÜNÜŞ")


def _import_pymupdf() -> Any:
    try:
        import pymupdf

        return pymupdf
    except ImportError:  # pragma: no cover - older wheels only expose `fitz`
        try:
            import fitz

            return fitz
        except ImportError as exc:
            raise MissingDependencyError("pymupdf", "PDF extraction", extra="pdf") from exc


class PdfExtractor(SourceExtractor):
    name = "pdf"
    suffixes = (".pdf",)

    def extract(self, path: Path, config: AuditConfig) -> DrawingDocument:
        pymupdf = _import_pymupdf()
        try:
            pdf = pymupdf.open(str(path))
        except Exception as exc:  # pragma: no cover - corrupt file
            raise ExtractionError(f"cannot open {path}: {exc}") from exc

        document = DrawingDocument(
            source_path=path,
            source_format=SourceFormat.PDF_VECTOR,
            units=Units(config.default_units),
            extractor=self.name,
            metadata={"page_count": pdf.page_count, **{k: v for k, v in (pdf.metadata or {}).items() if v}},
        )
        with pdf:
            for index in range(pdf.page_count):
                document.sheets.append(self._build_sheet(pdf[index], index, path, config))

        if all(sheet.needs_vision for sheet in document.sheets) and document.sheets:
            document.source_format = SourceFormat.PDF_RASTER
        return document

    # ------------------------------------------------------------------
    def _build_sheet(self, page: Any, index: int, path: Path, config: AuditConfig) -> Sheet:
        rect = page.rect
        sheet = Sheet(
            index=index,
            name=f"page {index + 1}",
            width=float(rect.width),
            height=float(rect.height),
            units=Units(config.default_units),
            coordinate_space=CoordinateSpace.Y_DOWN,
        )
        lines = _text_lines(page)
        sheet.text_char_count = sum(len(line.text) for line in lines)

        drawings = _safe_drawings(page)
        if sheet.text_char_count < MIN_TEXT_CHARS:
            sheet.needs_vision = True
            sheet.warnings.append(
                "page has no usable text layer (scanned drawing) - vision extraction required"
            )
        # The title block is read first: its cells are metadata, and reading
        # "MATERIAL: S235JR" as a 235 mm dimension would be worse than useless.
        sheet.title_block = scan_title_block(
            [TextItem(text=line.text, bbox=line.bbox) for line in lines],
            sheet.bbox,
            CoordinateSpace.Y_DOWN,
        )
        self._parse_lines(sheet, lines, config, consumed=_title_block_boxes(sheet))
        self._recover_vectors(sheet, drawings)
        _post_process(sheet)
        return sheet

    # ------------------------------------------------------------------
    def _recover_vectors(self, sheet: Sheet, drawings: list[dict[str, Any]]) -> None:
        """Geometry, measured intervals and the page scale, in that order.

        The order is the point: until the dimension lines are claimed, every
        extension line looks like an edge of the part.
        """
        segments, circles = pdf_vector.flatten(drawings)
        # The title block's own frame is not part geometry: its content was read
        # as fields already, and its rectangle would otherwise cluster into a
        # view of its own and be reported as undimensioned.  Only strokes that
        # lie *entirely* within the region go: a view that reaches into the
        # corner is still a view.
        if sheet.title_block.detected:
            region = title_block_region(sheet.bbox, sheet.coordinate_space)
            segments = [item for item in segments if not region.contains(_span(item))]
            circles = [item for item in circles if not region.contains(_disc(item))]
        if not segments and not circles:
            return
        matches = pdf_vector.claim_dimension_lines(sheet.dimensions, segments)
        mm_per_point = pdf_vector.calibrate(matches)
        pdf_vector.apply_matches(matches, mm_per_point)

        claimed: set[int] = set()
        for match in matches:
            claimed |= match.consumed
        next_feature = self.id_factory("FEAT")
        sheet.features.extend(
            pdf_vector.features_from(segments, circles, claimed, next_feature)
        )

        if mm_per_point is None:
            sheet.warnings.append(
                "the page scale could not be derived from its dimensions - lengths stay in "
                "points and the dimensional coverage rules do not run"
            )
            return
        pdf_vector.rescale(sheet, mm_per_point)
        sheet.units = Units.MM
        sheet.metadata["mm_per_point"] = f"{mm_per_point:.6f}"
        sheet.metadata["dimensions_matched"] = f"{len(matches)}/{len(sheet.dimensions)}"

    # ------------------------------------------------------------------
    def _parse_lines(
        self,
        sheet: Sheet,
        lines: list[TextItem],
        config: AuditConfig,
        *,
        consumed: list[BBox] | None = None,
    ) -> None:
        next_dim = self.id_factory("DIM")
        next_gtol = self.id_factory("GTOL")
        next_datum = self.id_factory("DATUM")
        next_note = self.id_factory("NOTE")
        next_surface = self.id_factory("SURF")
        next_weld = self.id_factory("WELD")
        next_view = self.id_factory("VIEW")

        used: set[int] = set()
        title_block_boxes = consumed or []
        # Stacked limit dimensions are two numeric lines sitting on top of each
        # other with the same left edge - detect them before the generic pass.
        for i, line in enumerate(lines):
            if i in used:
                continue
            partner = _stacked_partner(lines, i)
            if partner is None:
                continue
            j, high, low = partner
            box = line.bbox.union(lines[j].bbox)
            sheet.dimensions.append(
                Dimension(
                    id=next_dim(),
                    kind=DimensionKind.LINEAR,
                    nominal=(high + low) / 2.0,
                    text=f"{lines[i].text} / {lines[j].text}",
                    units=sheet.units,
                    tolerance=Tolerance(
                        kind=ToleranceKind.LIMITS, upper=high, lower=low, raw="stacked limits"
                    ),
                    bbox=box,
                    confidence=0.8,
                )
            )
            used.update({i, j})

        for i, line in enumerate(lines):
            if i in used or not line.text.strip():
                continue
            text = line.text

            fcf = parse_feature_control_frame(text)
            if fcf is not None:
                sheet.geometric_tolerances.append(
                    GeometricTolerance(
                        id=next_gtol(),
                        bbox=line.bbox,
                        characteristic=fcf.characteristic,
                        value=fcf.value,
                        diametral_zone=fcf.diametral_zone,
                        material_condition=fcf.material_condition,
                        datums=[DatumRef(label=lbl, material_condition=mc) for lbl, mc in fcf.datums],
                        raw=fcf.raw,
                        confidence=0.85,
                    )
                )
                continue

            datum = parse_datum_feature(text)
            if datum:
                sheet.datums.append(
                    DatumFeature(id=next_datum(), label=datum, bbox=line.bbox, confidence=0.7)
                )
                continue

            surface = parse_surface_finish(text)
            if surface is not None:
                sheet.surface_finishes.append(
                    SurfaceFinish(
                        id=next_surface(),
                        bbox=line.bbox,
                        symbol_kind=surface.symbol_kind,
                        ra=surface.ra,
                        rz=surface.rz,
                        process=surface.process,
                        raw=surface.raw,
                        confidence=0.85,
                    )
                )
                continue

            if any(pattern in text.upper() for pattern in _VIEW_LABEL_PATTERNS):
                sheet.views.append(View(id=next_view(), label=text, bbox=line.bbox))
                continue

            weld = parse_weld_symbol(text)
            if weld is not None and weld.size is not None:
                sheet.welds.append(
                    WeldSymbol(
                        id=next_weld(),
                        bbox=line.bbox,
                        weld_type=weld.weld_type,
                        size=weld.size,
                        length=weld.length,
                        pitch=weld.pitch,
                        all_around=weld.all_around,
                        field_weld=weld.field_weld,
                        raw=weld.raw,
                        confidence=0.6,
                    )
                )
                continue

            parsed = parse_dimension_text(text, default_units=sheet.units)
            in_title_block = any(box.contains(line.bbox) for box in title_block_boxes)
            if (
                parsed is not None
                and parsed.is_numeric
                and not in_title_block
                and _looks_like_dimension(text)
            ):
                kind = parsed.kind_hint
                if kind is DimensionKind.UNKNOWN:
                    kind = DimensionKind.LINEAR
                sheet.dimensions.append(
                    Dimension(
                        id=next_dim(),
                        kind=kind,
                        nominal=parsed.nominal,
                        text=parsed.text,
                        units=parsed.units,
                        tolerance=parsed.tolerance,
                        bbox=line.bbox,
                        prefix=parsed.prefix,
                        decimals=parsed.decimals,
                        is_reference=parsed.is_reference,
                        is_basic=parsed.is_basic,
                        confidence=0.85,
                        extra={
                            "multiplicity": parsed.multiplicity,
                            "thread": parsed.thread,
                        },
                    )
                )
                continue

            sheet.annotations.append(
                Annotation(
                    id=next_note(), text=text, bbox=line.bbox, category=classify_annotation(text)
                )
            )


# --------------------------------------------------------------------------
# PDF helpers
# --------------------------------------------------------------------------
def _text_lines(page: Any) -> list[TextItem]:
    """Collect text lines, merging the spans PDF writers split words into."""
    lines: list[TextItem] = []
    try:
        data = page.get_text("dict")
    except Exception:
        return lines
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(span.get("text", "") for span in spans)
            text = normalize_drawing_text(text)
            if not text:
                continue
            boxes = [span["bbox"] for span in spans if span.get("bbox")]
            if not boxes:
                continue
            bbox = BBox(
                x0=min(b[0] for b in boxes),
                y0=min(b[1] for b in boxes),
                x1=max(b[2] for b in boxes),
                y1=max(b[3] for b in boxes),
            )
            lines.append(TextItem(text=text, bbox=bbox))
    return lines


def _safe_drawings(page: Any) -> list[dict[str, Any]]:
    try:
        return list(page.get_drawings())
    except Exception:
        return []


_NUMERIC_ONLY = ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9")

#: Four letters or more in a row - long enough to be a word rather than a symbol.
_WORD_RE = re.compile(r"[^\W\d_]{4,}", re.UNICODE)

#: The words a dimension callout is still allowed to carry at that length.
_DIMENSION_WORDS = frozenset({"THRU", "DEEP", "DERIN", "TYPE", "ADET", "PLCS", "BOTH"})


def _looks_like_dimension(text: str) -> bool:
    """Reject prose that happens to contain a number (notes, labels, dates)."""
    stripped = text.strip()
    if len(stripped) > 40:
        return False
    if sum(ch.isalpha() for ch in stripped) > 12:
        return False
    if not any(ch in stripped for ch in _NUMERIC_ONLY):
        return False
    # "KALINLIK 5" states the material thickness; it is a note that DIM016 reads,
    # not a 5 mm dimension.  The DXF path gets this for free because MTEXT lands
    # in annotations - on a PDF page every string is a candidate, so the shared
    # grammar has to make the call.
    if parse_thickness(stripped) is not None:
        return False
    # Letters inside a callout come in short tokens: a prefix (M, R, SQ), a fit
    # (H7/g6), a multiplier (4x), a qualifier (REF).  A longer word is prose.
    if any(word.upper() not in _DIMENSION_WORDS for word in _WORD_RE.findall(stripped)):
        return False
    # "MATERIAL: S235JR" is a title-block cell, not a 235 mm dimension.
    label, _, _ = stripped.partition(":")
    if label != stripped and canonical_field(label) is not None:
        return False
    # A tolerance legitimately brings extra separators with it; without one,
    # repeated separators mean a date, a part number or a revision index.
    toleranced = any(marker in stripped for marker in ("±", "+", "/"))
    if stripped.count("/") >= 2:
        return False
    return not (not toleranced and (stripped.count("-") >= 2 or stripped.count(".") >= 2))


def _span(segment: Any) -> BBox:
    return BBox(
        x0=min(segment.start.x, segment.end.x),
        y0=min(segment.start.y, segment.end.y),
        x1=max(segment.start.x, segment.end.x),
        y1=max(segment.start.y, segment.end.y),
    )


def _disc(circle: Any) -> BBox:
    return BBox(
        x0=circle.center.x - circle.radius,
        y0=circle.center.y - circle.radius,
        x1=circle.center.x + circle.radius,
        y1=circle.center.y + circle.radius,
    )


def _title_block_boxes(sheet: Sheet) -> list[BBox]:
    boxes = [field.bbox for field in sheet.title_block.fields.values() if field.bbox]
    if sheet.title_block.bbox is not None and boxes:
        boxes.append(sheet.title_block.bbox)
    return boxes


def _stacked_partner(lines: list[TextItem], i: int) -> tuple[int, float, float] | None:
    """Detect an upper/lower limit pair such as ``20.15`` over ``20.05``."""
    first = lines[i]
    value = _pure_number(first.text)
    if value is None:
        return None
    for j in range(i + 1, min(i + 3, len(lines))):
        second = lines[j]
        other = _pure_number(second.text)
        if other is None:
            continue
        vertical_gap = second.bbox.y0 - first.bbox.y1
        aligned = abs(second.bbox.x0 - first.bbox.x0) <= max(first.bbox.height, 1.0)
        close = -first.bbox.height <= vertical_gap <= first.bbox.height * 1.2
        if not (aligned and close):
            continue
        high, low = max(value, other), min(value, other)
        if high == low or high <= 0:
            continue
        if (high - low) / abs(high) > 0.05:  # limits stay close together
            continue
        return j, high, low
    return None


def _pure_number(text: str) -> float | None:
    stripped = text.strip()
    if not stripped or len(stripped) > 12:
        return None
    if not all(ch.isdigit() or ch in ".,+- " for ch in stripped):
        return None
    return to_float(stripped)


def _post_process(sheet: Sheet) -> None:
    notes = sheet.notes_text()
    general = detect_general_tolerance(notes)
    if general and not sheet.title_block.has("general_tolerance"):
        sheet.title_block.set("general_tolerance", general, confidence=0.7)
    units_note = detect_units_note(notes)
    if units_note is not None:
        sheet.units = units_note
        for dim in sheet.dimensions:
            if dim.units is not Units.DEG:
                dim.units = units_note
    upper = notes.upper()
    if "FIRST ANGLE" in upper or "1. AÇI" in upper:
        sheet.projection = ProjectionMethod.FIRST_ANGLE
    elif "THIRD ANGLE" in upper or "3. AÇI" in upper:
        sheet.projection = ProjectionMethod.THIRD_ANGLE
    stated = sheet.title_block.value("scale")
    if stated:
        sheet.stated_scale = stated
        sheet.scale = _scale_value(stated)


def _scale_value(text: str) -> float | None:
    import re

    match = re.search(r"(\d+(?:[.,]\d+)?)\s*[:/]\s*(\d+(?:[.,]\d+)?)", text)
    if not match:
        return None
    num = to_float(match.group(1))
    den = to_float(match.group(2))
    if not num or not den:
        return None
    return num / den


def page_scale_hint(sheet: Sheet) -> float | None:  # pragma: no cover - convenience
    """Rough drawn-to-real ratio, when a sheet exposes both numbers."""
    if not sheet.dimensions or sheet.width <= 0:
        return None
    values = [d.nominal for d in sheet.dimensions if d.nominal]
    if not values:
        return None
    return math.fsum(values) / len(values) / sheet.width
