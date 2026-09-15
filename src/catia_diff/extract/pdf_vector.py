"""Vector recovery for PDF drawings: geometry, measured intervals, scale.

A CAD-exported PDF carries everything the checkers need, but not in the shape
they need it: there are no entities, only stroked paths, and no units, only
points on a page.  Three steps close that gap.

1. **Flatten** every path into segments and circles.
2. **Claim the dimension lines.**  A dimension is a line with arrows, two
   extension lines and a number beside it.  Those strokes are *not* part
   geometry - leaving them in would invent edges the part does not have - so
   they are consumed here, and each one hands its dimension the interval it
   measures.
3. **Calibrate.**  The page says 240 points where the drawing says 80, so one
   point is a third of a millimetre.  The ratio is taken from every matched
   dimension and reduced by median, which means one wrong callout cannot move
   the scale - it becomes an outlier instead, which is exactly what a dimension
   whose text was overridden *is*.

Nothing here guesses: a dimension whose line cannot be found keeps no interval,
and a page where too few dimensions match is left in points and reported.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from catia_diff.models.drawing import Dimension, GeometryFeature, GeometryKind
from catia_diff.models.geometry import BBox, Point2D

#: How far from a cardinal direction a stroke still counts as axis aligned.
AXIS_TOLERANCE_DEG = 1.0

#: A stroke shorter than this many points is an arrow head, not a line.
ARROWHEAD_MAX_PT = 8.0

#: How far a dimension's text may sit from the middle of its line, relative to
#: the text height.  A number is written on or just above its dimension line.
TEXT_REACH_RATIO = 3.0

#: Matched dimensions needed before the page scale is trusted.
MIN_CALIBRATION_SAMPLES = 2

#: A ratio this far from the median is not a different scale but a different
#: number: the callout disagrees with the line it is attached to.
OUTLIER_RATIO = 0.05


@dataclass(frozen=True)
class Segment:
    """One straight stroke of a path, in page points."""

    start: Point2D
    end: Point2D
    dashed: bool = False

    @property
    def length(self) -> float:
        return math.hypot(self.end.x - self.start.x, self.end.y - self.start.y)

    @property
    def angle(self) -> float:
        """Direction in degrees, folded into 0-180."""
        return math.degrees(math.atan2(self.end.y - self.start.y, self.end.x - self.start.x)) % 180.0

    @property
    def midpoint(self) -> Point2D:
        return Point2D(x=(self.start.x + self.end.x) / 2, y=(self.start.y + self.end.y) / 2)

    @property
    def axis(self) -> float | None:
        """0 for a horizontal stroke, 90 for a vertical one, else ``None``."""
        angle = self.angle
        if angle <= AXIS_TOLERANCE_DEG or angle >= 180 - AXIS_TOLERANCE_DEG:
            return 0.0
        if abs(angle - 90.0) <= AXIS_TOLERANCE_DEG:
            return 90.0
        return None


@dataclass(frozen=True)
class Circle:
    center: Point2D
    radius: float
    dashed: bool = False


@dataclass
class Match:
    """A dimension and the line it was found to measure."""

    dimension: Dimension
    axis: float
    interval: tuple[float, float]
    consumed: set[int] = field(default_factory=set)

    @property
    def span(self) -> float:
        return abs(self.interval[1] - self.interval[0])


# ---------------------------------------------------------------------------
def flatten(drawings: Sequence[dict[str, Any]]) -> tuple[list[Segment], list[Circle]]:
    """Every path reduced to straight strokes and circles."""
    segments: list[Segment] = []
    circles: list[Circle] = []
    for path in drawings:
        dashed = _is_dashed(path.get("dashes"))
        curves: list[tuple] = []
        for item in path.get("items", ()):
            kind = item[0]
            if kind == "l":
                segments.append(_segment(item[1], item[2], dashed))
            elif kind == "re":
                segments.extend(_rect_segments(item[1], dashed))
            elif kind == "qu":
                segments.extend(_quad_segments(item[1], dashed))
            elif kind == "c":
                curves.append(item)
        circles.extend(_circles_from_curves(curves, dashed))
    return segments, circles


def _is_dashed(dashes: str | None) -> bool:
    if not dashes:
        return False
    return dashes.strip() not in {"[] 0", "[]0", ""}


def _point(raw: Any) -> Point2D:
    return Point2D(x=float(raw.x), y=float(raw.y))


def _segment(start: Any, end: Any, dashed: bool) -> Segment:
    return Segment(start=_point(start), end=_point(end), dashed=dashed)


def _rect_segments(rect: Any, dashed: bool) -> list[Segment]:
    corners = [
        Point2D(x=float(rect.x0), y=float(rect.y0)),
        Point2D(x=float(rect.x1), y=float(rect.y0)),
        Point2D(x=float(rect.x1), y=float(rect.y1)),
        Point2D(x=float(rect.x0), y=float(rect.y1)),
    ]
    return _ring(corners, dashed)


def _quad_segments(quad: Any, dashed: bool) -> list[Segment]:
    corners = [_point(getattr(quad, name)) for name in ("ul", "ur", "lr", "ll")]
    return _ring(corners, dashed)


def _ring(corners: list[Point2D], dashed: bool) -> list[Segment]:
    out: list[Segment] = []
    for index, corner in enumerate(corners):
        nxt = corners[(index + 1) % len(corners)]
        segment = Segment(start=corner, end=nxt, dashed=dashed)
        if segment.length > 0:
            out.append(segment)
    return out


def _circles_from_curves(curves: list[tuple], dashed: bool) -> list[Circle]:
    """Four consecutive beziers in a square box are how a PDF draws a circle."""
    out: list[Circle] = []
    for index in range(0, len(curves) - 3, 4):
        points = [_point(value) for item in curves[index : index + 4] for value in item[1:]]
        xs = [point.x for point in points]
        ys = [point.y for point in points]
        width, height = max(xs) - min(xs), max(ys) - min(ys)
        if width <= 0 or height <= 0 or abs(width - height) > width * 0.1:
            continue
        out.append(
            Circle(
                center=Point2D(x=(max(xs) + min(xs)) / 2, y=(max(ys) + min(ys)) / 2),
                radius=(width + height) / 4,
                dashed=dashed,
            )
        )
    return out


# ---------------------------------------------------------------------------
def claim_dimension_lines(dimensions: Sequence[Dimension], segments: Sequence[Segment]) -> list[Match]:
    """Attach each dimension to the stroke it measures, and claim that stroke.

    The claim matters as much as the measurement: an extension line left in the
    geometry becomes an edge of a part that has no such edge.
    """
    matches: list[Match] = []
    taken: set[int] = set()
    for dimension in dimensions:
        if dimension.bbox is None or dimension.nominal is None:
            continue
        index = _dimension_line(dimension.bbox, segments, taken)
        if index is None:
            continue
        line = segments[index]
        axis = line.axis
        assert axis is not None  # guaranteed by _dimension_line
        low, high = sorted(
            (_project(line.start, axis), _project(line.end, axis)),
        )
        consumed = {index} | _attendant_strokes(line, segments, taken)
        taken |= consumed
        matches.append(Match(dimension=dimension, axis=axis, interval=(low, high), consumed=consumed))
    return matches


def _project(point: Point2D, axis: float) -> float:
    return point.x if axis == 0.0 else point.y


def _dimension_line(box: BBox, segments: Sequence[Segment], taken: set[int]) -> int | None:
    """The axis-aligned stroke this number is written against."""
    centre = box.center
    reach = max(box.height, 1.0) * TEXT_REACH_RATIO
    best: tuple[float, int] | None = None
    for index, segment in enumerate(segments):
        if index in taken or segment.axis is None or segment.length <= max(box.width, ARROWHEAD_MAX_PT):
            continue
        midpoint = segment.midpoint
        distance = math.hypot(midpoint.x - centre.x, midpoint.y - centre.y)
        if distance <= reach and (best is None or distance < best[0]):
            best = (distance, index)
    return best[1] if best else None


def _attendant_strokes(line: Segment, segments: Sequence[Segment], taken: set[int]) -> set[int]:
    """Arrow heads and extension lines belong to the dimension, not the part."""
    out: set[int] = set()
    ends = (line.start, line.end)
    for index, segment in enumerate(segments):
        if index in taken or segment is line:
            continue
        near_end = any(
            math.hypot(point.x - end.x, point.y - end.y) <= ARROWHEAD_MAX_PT
            for point in (segment.start, segment.end, segment.midpoint)
            for end in ends
        )
        if not near_end:
            continue
        if segment.length <= ARROWHEAD_MAX_PT or segment.axis != line.axis:
            out.add(index)
    return out


# ---------------------------------------------------------------------------
def calibrate(matches: Sequence[Match]) -> float | None:
    """Millimetres per point, from the drawing's own dimensions.

    One overridden callout cannot move a median, so it stands out as an outlier
    instead - which is what it is.
    """
    ratios = [
        match.dimension.nominal / match.span
        for match in matches
        if match.dimension.nominal and match.span > 0
    ]
    if len(ratios) < MIN_CALIBRATION_SAMPLES:
        return None
    value = median(ratios)
    return value if value > 0 else None


def features_from(
    segments: Sequence[Segment],
    circles: Sequence[Circle],
    claimed: set[int],
    id_factory,
) -> list[GeometryFeature]:
    """Everything the dimensions did not claim is part geometry."""
    out: list[GeometryFeature] = []
    for index, segment in enumerate(segments):
        if index in claimed or segment.length <= 0:
            continue
        out.append(
            GeometryFeature(
                id=id_factory(),
                kind=GeometryKind.LINE,
                layer="CENTER" if segment.dashed else "PART",
                points=[segment.start, segment.end],
                bbox=_bbox([segment.start, segment.end]),
                confidence=0.8,
            )
        )
    for circle in circles:
        out.append(
            GeometryFeature(
                id=id_factory(),
                kind=GeometryKind.CIRCLE,
                layer="CENTER" if circle.dashed else "PART",
                center=circle.center,
                radius=circle.radius,
                closed=True,
                bbox=BBox(
                    x0=circle.center.x - circle.radius,
                    y0=circle.center.y - circle.radius,
                    x1=circle.center.x + circle.radius,
                    y1=circle.center.y + circle.radius,
                ),
                confidence=0.8,
            )
        )
    return out


def _bbox(points: Sequence[Point2D]) -> BBox:
    xs = [point.x for point in points]
    ys = [point.y for point in points]
    return BBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))


# ---------------------------------------------------------------------------
def apply_matches(matches: Sequence[Match], mm_per_point: float | None) -> None:
    """Hand each dimension the interval it measures, in millimetres.

    With a scale, the measured length is comparable with the printed one - which
    is how "the text says 25 and the geometry is 30" becomes visible on a PDF
    too.  A callout that disagrees with its own line by more than the median
    tolerates is marked as overridden; that is the same defect the DXF path
    reads straight out of the file.
    """
    for match in matches:
        dimension = match.dimension
        dimension.extra["axis"] = match.axis
        dimension.extra["interval"] = [match.interval[0], match.interval[1]]
        if mm_per_point is None or match.span <= 0:
            continue
        measured = match.span * mm_per_point
        dimension.measured = measured
        nominal = dimension.nominal
        if nominal and abs(measured - nominal) > nominal * OUTLIER_RATIO:
            dimension.is_text_override = True


def rescale(sheet, factor: float) -> None:
    """Redraw the whole sheet in millimetres.

    The page is in points and the part is in millimetres; once the ratio is
    known there is no reason to keep two systems, and the rules - which compare
    a node at x=12 with a dimension that measured 12 - need one.
    """
    if factor <= 0 or factor == 1.0:
        return
    sheet.width *= factor
    sheet.height *= factor
    for obj in sheet.objects():
        if obj.bbox is not None:
            obj.bbox = _scaled_box(obj.bbox, factor)
    for feature in sheet.features:
        if feature.center is not None:
            feature.center = _scaled_point(feature.center, factor)
        if feature.radius:
            feature.radius *= factor
        if feature.points:
            feature.points = [_scaled_point(point, factor) for point in feature.points]
        if feature.length:
            feature.length *= factor
    for dimension in sheet.dimensions:
        interval = dimension.extra.get("interval")
        if interval:
            dimension.extra["interval"] = [value * factor for value in interval]
    for field_ in sheet.title_block.fields.values():
        if field_.bbox is not None:
            field_.bbox = _scaled_box(field_.bbox, factor)


def _scaled_box(box: BBox, factor: float) -> BBox:
    return BBox(x0=box.x0 * factor, y0=box.y0 * factor, x1=box.x1 * factor, y1=box.y1 * factor)


def _scaled_point(point: Point2D, factor: float) -> Point2D:
    return Point2D(x=point.x * factor, y=point.y * factor)


__all__ = [
    "MIN_CALIBRATION_SAMPLES",
    "OUTLIER_RATIO",
    "Circle",
    "Match",
    "Segment",
    "apply_matches",
    "calibrate",
    "claim_dimension_lines",
    "features_from",
    "flatten",
    "rescale",
]
