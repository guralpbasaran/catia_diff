"""Heuristic title-block reconstruction from loose text items.

Used by the PDF extractor (text spans), by the DXF extractor when the title
block is drawn with plain TEXT instead of a block with attributes, and by the
vision agent as a post-processing step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from catia_diff.fields import canonical_field, fold
from catia_diff.models.drawing import TitleBlock
from catia_diff.models.geometry import BBox, CoordinateSpace

#: Values that can be recognised without a label next to them.
#:
#: ``1:2`` and ``1/2`` are read as a scale, ``1 / 2`` and ``2 of 3`` as sheet
#: numbering - the spacing is the only signal a drawing gives for this
#: genuinely ambiguous pair, and the patterns are ordered accordingly.
_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("scale", re.compile(r"^\s*(?:1\s*:\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*:\s*1|1/\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?/1|NTS|N\.T\.S\.)\s*$", re.IGNORECASE)),
    ("sheet", re.compile(r"^\s*(?:sheet|sayfa|pafta)?\s*\d+\s*(?:/|of|de)\s*\d+\s*$", re.IGNORECASE)),
    ("sheet_size", re.compile(r"^\s*(A[0-4]|B[0-4]|ANSI\s*[A-E]|LETTER|LEDGER)\s*$", re.IGNORECASE)),
    ("general_tolerance", re.compile(r"^\s*(?:ISO\s*2768|DIN\s*7168|EN\s*22768)[\s-]*[a-zA-Z]{0,2}\s*$", re.IGNORECASE)),
    ("revision", re.compile(r"^\s*(?:rev|revizyon)\.?\s*[:.]?\s*([A-Z]|\d{1,2})\s*$", re.IGNORECASE)),
)

_DATE_RE = re.compile(r"^\s*\d{1,4}[./-]\d{1,2}[./-]\d{2,4}\s*$")
_INLINE_RE = re.compile(r"^\s*([^:=]{2,40}?)\s*[:=]\s*(.+?)\s*$")


@dataclass(frozen=True)
class TextItem:
    """A piece of text with its placement on the sheet."""

    text: str
    bbox: BBox

    @property
    def clean(self) -> str:
        return re.sub(r"\s+", " ", self.text).strip()


def scan_title_block(
    items: list[TextItem],
    sheet_bbox: BBox,
    space: CoordinateSpace = CoordinateSpace.Y_DOWN,
    *,
    source: str = "text_scan",
) -> TitleBlock:
    """Rebuild a title block from loose text items."""
    block = TitleBlock(source=source)
    items = [item for item in items if item.clean]
    if not items:
        return block

    region = title_block_region(sheet_bbox, space)
    in_region = [item for item in items if region.intersects(item.bbox)]
    used: list[TextItem] = []

    # 1) "LABEL: VALUE" inside a single text item - the most reliable signal,
    #    accepted anywhere on the sheet.
    for item in items:
        match = _INLINE_RE.match(item.clean)
        if not match:
            continue
        field = canonical_field(match.group(1))
        value = match.group(2).strip()
        if field and value and canonical_field(value) is None:
            set_field(block, field, value, item.bbox, 0.9)
            used.append(item)

    # 2) label item + neighbouring value item, restricted to the title block area.
    labels = [(item, canonical_field(item.clean)) for item in in_region]
    for item, field in labels:
        if not field or block.has(field):
            continue
        value_item = _nearest_value(item, in_region, space)
        if value_item is None:
            continue
        set_field(block, field, value_item.clean, item.bbox.union(value_item.bbox), 0.75)
        used.extend([item, value_item])

    # 3) self-describing values (scale, sheet numbering, paper size, ISO 2768).
    for item in in_region:
        text = item.clean
        for field, pattern in _VALUE_PATTERNS:
            if block.has(field) or not pattern.match(text):
                continue
            value = text
            if field == "revision":
                value = re.sub(r"^\s*(?:rev|revizyon)\.?\s*[:.]?\s*", "", text, flags=re.IGNORECASE)
            set_field(block, field, value.strip(), item.bbox, 0.6)
            used.append(item)
            break
        if not block.has("drawn_date") and _DATE_RE.match(text):
            set_field(block, "drawn_date", text, item.bbox, 0.4)
            used.append(item)

    if block.fields:
        block.detected = True
        boxes = [item.bbox for item in used] or [region]
        merged = boxes[0]
        for box in boxes[1:]:
            merged = merged.union(box)
        block.bbox = merged
    return block


def title_block_region(sheet_bbox: BBox, space: CoordinateSpace) -> BBox:
    """Conventional title-block area: bottom-right ~45% x 30% of the sheet."""
    width, height = sheet_bbox.width, sheet_bbox.height
    x0 = sheet_bbox.x1 - 0.45 * width
    if space is CoordinateSpace.Y_DOWN:  # y grows downwards -> bottom is y1
        return BBox(x0=x0, y0=sheet_bbox.y1 - 0.30 * height, x1=sheet_bbox.x1, y1=sheet_bbox.y1)
    return BBox(x0=x0, y0=sheet_bbox.y0, x1=sheet_bbox.x1, y1=sheet_bbox.y0 + 0.30 * height)


def _nearest_value(
    label: TextItem, items: list[TextItem], space: CoordinateSpace
) -> TextItem | None:
    """Find the value that belongs to ``label``: to its right, or just below."""
    row_tol = max(label.bbox.height * 0.8, 1e-6)
    max_dx = max(label.bbox.width * 6.0, label.bbox.height * 12.0)
    max_dy = max(label.bbox.height * 3.0, 1e-6)

    best: tuple[float, TextItem] | None = None
    for candidate in items:
        if candidate is label or not candidate.clean:
            continue
        if canonical_field(candidate.clean) is not None:
            continue  # another label, not a value
        if fold(candidate.clean) == fold(label.clean):
            continue
        dx = candidate.bbox.x0 - label.bbox.x1
        dy_center = abs(candidate.bbox.center.y - label.bbox.center.y)
        if 0 <= dx <= max_dx and dy_center <= row_tol:
            score = dx + dy_center
        else:
            # below the label (title blocks stack label over value)
            if space is CoordinateSpace.Y_DOWN:
                gap = candidate.bbox.y0 - label.bbox.y1
            else:
                gap = label.bbox.y0 - candidate.bbox.y1
            overlap = min(candidate.bbox.x1, label.bbox.x1) - max(candidate.bbox.x0, label.bbox.x0)
            if not (0 <= gap <= max_dy and overlap > 0):
                continue
            score = gap * 1.5 + max(0.0, -overlap)
        if best is None or score < best[0]:
            best = (score, candidate)
    return best[1] if best else None


def set_field(
    block: TitleBlock, field: str, value: str, bbox: BBox | None, confidence: float
) -> None:
    """Write a field unless a more confident value is already present."""
    existing = block.get(field)
    if existing is not None and not existing.is_empty and existing.confidence >= confidence:
        return
    block.set(field, value, bbox=bbox, confidence=confidence)
