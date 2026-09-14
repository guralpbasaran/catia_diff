"""Overlay rendering: findings drawn on top of the sheet."""

from __future__ import annotations

from pathlib import Path

from catia_diff.config import AuditConfig
from catia_diff.errors import MissingDependencyError
from catia_diff.extract.raster import render_pdf_page
from catia_diff.models.drawing import DrawingDocument, GeometryKind, Sheet, SourceFormat
from catia_diff.models.findings import SEVERITY_COLORS, AuditReport, Finding
from catia_diff.models.geometry import BBox, CoordinateSpace
from catia_diff.reporting.normalize import overlay_numbers

MAX_CANVAS_PX = 2200
MIN_CANVAS_PX = 900


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

        numbers = overlay_numbers(findings)
        for finding in findings:
            number = numbers.get(finding.id)
            if number is not None:
                _draw_finding(draw, finding, number, sheet, scale, base.size)
        _draw_legend(draw, findings, base.size, config.language)

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
    color = SEVERITY_COLORS[finding.severity]
    rgb = _hex_to_rgb(color)
    box = _to_pixels(finding.evidence.bbox, sheet, scale, size)
    padded = (max(0, box[0] - 4), max(0, box[1] - 4), min(size[0] - 1, box[2] + 4), min(size[1] - 1, box[3] + 4))
    draw.rectangle(padded, outline=rgb, width=3)
    draw.rectangle(padded, fill=(*rgb, 28))

    label = str(number)
    badge_w, badge_h = 9 * len(label) + 10, 20
    bx0 = padded[0]
    by0 = max(0, padded[1] - badge_h)
    draw.rectangle((bx0, by0, bx0 + badge_w, by0 + badge_h), fill=rgb)
    draw.text((bx0 + 5, by0 + 4), label, fill="white")


def _draw_legend(draw, findings: list[Finding], size: tuple[int, int], lang: str = "en") -> None:
    counts: dict = {}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    lines = list(counts.items())
    if not lines:
        return
    pad, row = 10, 18
    width, height = 190, row * len(lines) + 2 * pad
    x0, y0 = 12, 12
    draw.rectangle((x0, y0, x0 + width, y0 + height), fill=(255, 255, 255, 235), outline=(120, 120, 120))
    for index, (severity, count) in enumerate(lines):
        y = y0 + pad + index * row
        rgb = _hex_to_rgb(SEVERITY_COLORS[severity])
        draw.rectangle((x0 + pad, y + 3, x0 + pad + 12, y + 13), fill=rgb)
        draw.text((x0 + pad + 20, y + 2), f"{severity.label(lang)}: {count}", fill=(30, 30, 30))


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i: i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
