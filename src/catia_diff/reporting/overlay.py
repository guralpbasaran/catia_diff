"""Overlay rendering: findings drawn on top of the sheet."""

from __future__ import annotations

import math
from functools import cache
from pathlib import Path

from catia_diff.config import AuditConfig
from catia_diff.errors import MissingDependencyError
from catia_diff.extract.raster import render_pdf_page
from catia_diff.models.drawing import DrawingDocument, GeometryKind, Sheet, SourceFormat
from catia_diff.models.findings import (
    SEVERITY_COLORS,
    AuditReport,
    CoverageGap,
    Finding,
    Severity,
)
from catia_diff.models.geometry import BBox, CoordinateSpace, Point2D
from catia_diff.reporting.coverage import COVERAGE_RULES
from catia_diff.reporting.normalize import overlay_numbers

MAX_CANVAS_PX = 2200
MIN_CANVAS_PX = 900

#: Dash pattern of the missing-dimension guide, in pixels.
DASH_PX = (9, 6)


#: Fonts that carry the Turkish alphabet, tried in order.  Pillow's own bundled
#: face does not - "ö" and "ş" come out as hollow .notdef boxes - so a real one
#: has to be found, and if none is, the label is transliterated instead.  A box
#: is never the better answer.
FONT_CANDIDATES = (
    "DejaVuSans.ttf",  # searched in Pillow's own font directories
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:/Windows/Fonts/arial.ttf",
)

#: Case-preserving ASCII stand-ins, for the fallback above.  Deliberately not
#: ``fields.fold``: that one lowercases and strips punctuation for matching.
_ASCII_FALLBACK = str.maketrans(
    {
        "ı": "i", "İ": "I", "ş": "s", "Ş": "S", "ğ": "g", "Ğ": "G",
        "ü": "u", "Ü": "U", "ö": "o", "Ö": "O", "ç": "c", "Ç": "C",
    }
)


@cache
def _font(size: int = 12):
    from PIL import ImageFont

    for candidate in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 has no scalable default
        return ImageFont.load_default()


#: The letters the labels need beyond ASCII.
_TURKISH = "çğıöşüÇĞİÖŞÜ"


def _label(text: str) -> str:
    """``text`` as the chosen font can actually draw it."""
    return text.translate(_ASCII_FALLBACK) if _needs_ascii() else text


@cache
def _needs_ascii() -> bool:
    """True when the chosen font has no Turkish glyphs.

    FreeType draws every character it lacks as the same .notdef box, so drawing
    a codepoint Unicode guarantees is unassigned gives us that box to compare
    against.  The answer depends only on the font, so it is settled once.
    """
    font = _font()
    notdef = _stamp("\uffff", font)
    return any(_stamp(letter, font) == notdef for letter in _TURKISH)


def _stamp(char: str, font) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("L", (24, 24), 0)
    ImageDraw.Draw(image).text((2, 2), char, fill=255, font=font)
    return image.tobytes()


def render_overlays(
    document: DrawingDocument, report: AuditReport, config: AuditConfig
) -> list[Path]:
    """Draw every located finding on its sheet; returns the written images."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise MissingDependencyError("pillow", "overlay rendering", extra="raster") from exc

    out_dir = Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for sheet in document.sheets:
        findings = [f for f in report.findings if f.evidence.sheet_index == sheet.index]
        if not findings:
            continue
        base, scale = _base_image(document, sheet, config, Image)
        if base is None:
            continue
        draw = ImageDraw.Draw(base, "RGBA")
        _draw_context(draw, sheet, scale, base.size)

        # The gaps go under the finding boxes: they illustrate a finding, and
        # the box that names it has to stay on top.
        guides = _draw_gaps(draw, report, findings, sheet, scale, base.size)

        numbers = overlay_numbers(findings)
        for finding in findings:
            number = numbers.get(finding.id)
            if number is not None:
                _draw_finding(draw, finding, number, sheet, scale, base.size)
        _draw_legend(draw, findings, base.size, config.language, guides=guides)

        path = out_dir / f"{document.source_path.stem}_sheet{sheet.index + 1}_overlay.png"
        base.save(path)
        written.append(path)
    return written


# --------------------------------------------------------------------------
def _base_image(document: DrawingDocument, sheet: Sheet, config: AuditConfig, Image):
    """Return ``(image, scale)`` where scale converts sheet units to pixels."""
    if sheet.raster_path and Path(sheet.raster_path).exists():
        image = Image.open(sheet.raster_path).convert("RGB")
        scale = image.width / sheet.width if sheet.width else 1.0
        return image, scale
    if document.source_format in {SourceFormat.PDF_VECTOR, SourceFormat.PDF_RASTER}:
        try:
            rendered = render_pdf_page(
                document.source_path, sheet.index, config.raster_dpi, Path(config.output_dir) / "pages"
            )
        except Exception:
            rendered = None
        if rendered is not None:
            image = Image.open(rendered).convert("RGB")
            scale = image.width / sheet.width if sheet.width else 1.0
            return image, scale
    if sheet.width <= 0 or sheet.height <= 0:
        return None, 1.0
    scale = max(MIN_CANVAS_PX, min(MAX_CANVAS_PX, 1600)) / max(sheet.width, sheet.height)
    size = (max(1, int(sheet.width * scale)), max(1, int(sheet.height * scale)))
    return Image.new("RGB", size, "white"), scale


def _to_pixels(box: BBox, sheet: Sheet, scale: float, size: tuple[int, int]) -> tuple[int, int, int, int]:
    local = sheet.to_local(box)
    if sheet.coordinate_space is CoordinateSpace.Y_UP:
        local = local.flipped_y(sheet.height)
    x0 = max(0, min(size[0] - 1, int(local.x0 * scale)))
    x1 = max(0, min(size[0] - 1, int(local.x1 * scale)))
    y0 = max(0, min(size[1] - 1, int(local.y0 * scale)))
    y1 = max(0, min(size[1] - 1, int(local.y1 * scale)))
    if x1 - x0 < 3:
        x1 = min(size[0] - 1, x0 + 3)
    if y1 - y0 < 3:
        y1 = min(size[1] - 1, y0 + 3)
    return x0, y0, x1, y1


def _draw_context(draw, sheet: Sheet, scale: float, size: tuple[int, int]) -> None:
    """Draw the extracted geometry so a synthetic canvas is readable.

    Only used when there is no page raster to draw on (DXF input): lines and
    polylines are drawn as-is, circles and arcs as ellipses, and every other
    object as a faint box so the reader sees where the callouts sit.
    """
    if sheet.raster_path:
        return
    ink = (70, 78, 88)
    for feature in sheet.features:
        if feature.points and len(feature.points) >= 2:
            pixels = [_point_to_pixels(point, sheet, scale, size) for point in feature.points]
            draw.line(pixels, fill=ink, width=1, joint="curve")
        elif feature.kind in {GeometryKind.CIRCLE, GeometryKind.ARC} and feature.bbox is not None:
            draw.ellipse(_to_pixels(feature.bbox, sheet, scale, size), outline=ink, width=1)
    for obj in (*sheet.dimensions, *sheet.geometric_tolerances, *sheet.annotations,
                *sheet.datums, *sheet.surface_finishes, *sheet.welds):
        if obj.bbox is None:
            continue
        draw.rectangle(_to_pixels(obj.bbox, sheet, scale, size), outline=(198, 203, 209), width=1)


def _point_to_pixels(point, sheet: Sheet, scale: float, size: tuple[int, int]) -> tuple[int, int]:
    x = point.x - sheet.origin.x
    y = point.y - sheet.origin.y
    if sheet.coordinate_space is CoordinateSpace.Y_UP:
        y = sheet.height - y
    return (
        max(0, min(size[0] - 1, int(x * scale))),
        max(0, min(size[1] - 1, int(y * scale))),
    )


def _draw_finding(draw, finding: Finding, number: int, sheet: Sheet, scale: float, size) -> None:
    anchor = finding.evidence.bbox
    if anchor is None:
        return  # only located findings are numbered, and only numbered ones are drawn
    color = SEVERITY_COLORS[finding.severity]
    rgb = _hex_to_rgb(color)
    box = _to_pixels(anchor, sheet, scale, size)
    padded = (max(0, box[0] - 4), max(0, box[1] - 4), min(size[0] - 1, box[2] + 4), min(size[1] - 1, box[3] + 4))
    draw.rectangle(padded, outline=rgb, width=3)
    draw.rectangle(padded, fill=(*rgb, 28))

    label = str(number)
    badge_w, badge_h = 9 * len(label) + 10, 20
    bx0 = padded[0]
    by0 = max(0, padded[1] - badge_h)
    draw.rectangle((bx0, by0, bx0 + badge_w, by0 + badge_h), fill=rgb)
    draw.text((bx0 + 5, by0 + 4), label, fill="white", font=_font())


def _draw_gaps(draw, report: AuditReport, findings, sheet: Sheet, scale: float, size) -> int:
    """Draw each missing dimension as the dimension it would be.

    A box saying "not located along Y" tells the reader there is a problem; a
    dashed line running from the edge the drawing does control to the hole it
    does not tells them what to draw.  Both ends get a tick, so it reads as a
    dimension rather than as a leader.  Returns how many were drawn, so the
    legend can name the dashed line only when there is one.
    """
    drawn = 0
    for view in report.coverage:
        if view.sheet_index != sheet.index:
            continue
        for axis in view.axes:
            if axis.axis not in {"X", "Y"}:
                continue  # an oblique direction has no simple guide to draw
            for gap in axis.gaps:
                ends = _gap_ends(gap, axis.axis, sheet, scale, size)
                if ends is None:
                    continue
                rgb = _hex_to_rgb(SEVERITY_COLORS[_gap_severity(gap, findings)])
                _draw_guide(draw, ends, axis.axis, rgb)
                drawn += 1
    return drawn


def _gap_severity(gap: CoverageGap, findings) -> Severity:
    """The severity of the finding that raised this gap.

    Matched on feature ids, the same way the summary was built - so the guide
    is never coloured by a severity no finding claimed.
    """
    wanted = set(gap.feature_ids)
    for finding in findings:
        if finding.rule_id in COVERAGE_RULES and wanted.intersection(finding.evidence.object_ids):
            return finding.severity
    return Severity.MAJOR


def _gap_ends(gap: CoverageGap, axis: str, sheet: Sheet, scale: float, size):
    """The guide's two endpoints in pixels, or ``None`` if it cannot be placed."""
    if gap.anchor is None or gap.bbox is None:
        return None
    centre = gap.bbox.center
    if axis == "Y":
        start, end = Point2D(x=centre.x, y=gap.anchor), Point2D(x=centre.x, y=gap.coordinate)
    else:
        start, end = Point2D(x=gap.anchor, y=centre.y), Point2D(x=gap.coordinate, y=centre.y)
    a = _point_to_pixels(start, sheet, scale, size)
    b = _point_to_pixels(end, sheet, scale, size)
    return None if a == b else (a, b)


def _draw_guide(draw, ends, axis: str, rgb: tuple[int, int, int]) -> None:
    (x0, y0), (x1, y1) = ends
    _dashed_line(draw, (x0, y0), (x1, y1), rgb)
    tick = 6
    for x, y in ((x0, y0), (x1, y1)):
        if axis == "Y":
            draw.line((x - tick, y, x + tick, y), fill=rgb, width=2)
        else:
            draw.line((x, y - tick, x, y + tick), fill=rgb, width=2)

    label = axis
    badge_w, badge_h = 9 * len(label) + 10, 18
    bx = (x0 + x1) // 2 + (6 if axis == "Y" else -badge_w // 2)
    by = (y0 + y1) // 2 + (-badge_h // 2 if axis == "Y" else 6)
    draw.rectangle((bx, by, bx + badge_w, by + badge_h), fill=rgb)
    draw.text((bx + 5, by + 3), label, fill="white")


def _dashed_line(draw, start, end, rgb: tuple[int, int, int], width: int = 2) -> None:
    (x0, y0), (x1, y1) = start, end
    length = math.hypot(x1 - x0, y1 - y0)
    if length <= 0:
        return
    on, off = DASH_PX
    step, position = on + off, 0.0
    while position < length:
        head = min(position + on, length)
        draw.line(
            (
                x0 + (x1 - x0) * position / length,
                y0 + (y1 - y0) * position / length,
                x0 + (x1 - x0) * head / length,
                y0 + (y1 - y0) * head / length,
            ),
            fill=rgb,
            width=width,
        )
        position += step


def _draw_legend(
    draw,
    findings: list[Finding],
    size: tuple[int, int],
    lang: str = "en",
    *,
    guides: int = 0,
) -> None:
    counts: dict = {}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    lines = list(counts.items())
    if not lines:
        return
    font = _font()
    pad, row = 10, 18
    rows = len(lines) + (1 if guides else 0)
    width, height = 190, row * rows + 2 * pad
    x0, y0 = 12, 12
    draw.rectangle((x0, y0, x0 + width, y0 + height), fill=(255, 255, 255, 235), outline=(120, 120, 120))
    for index, (severity, count) in enumerate(lines):
        y = y0 + pad + index * row
        rgb = _hex_to_rgb(SEVERITY_COLORS[severity])
        draw.rectangle((x0 + pad, y + 3, x0 + pad + 12, y + 13), fill=rgb)
        text = _label(f"{severity.label(lang)}: {count}")
        draw.text((x0 + pad + 20, y + 2), text, fill=(30, 30, 30), font=font)
    if not guides:
        return
    # A dashed line on a drawing already means something (a hidden edge), so it
    # is named here rather than left to be guessed.
    y = y0 + pad + len(lines) * row
    ink = (90, 96, 104)
    _dashed_line(draw, (x0 + pad, y + 8), (x0 + pad + 12, y + 8), ink, width=2)
    caption = _label(_GUIDE_LABEL.get(lang, _GUIDE_LABEL["en"]))
    draw.text((x0 + pad + 20, y + 2), caption, fill=(30, 30, 30), font=font)


#: Legend caption for the dashed missing-dimension guide.
_GUIDE_LABEL = {"en": "missing dimension", "tr": "eksik ölçü"}


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i: i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
