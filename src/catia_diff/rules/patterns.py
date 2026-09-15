"""Feature patterns a drawing dimensions as a group, not one by one.

Two of them matter for dimensional completeness:

* a **bolt circle** - three or more equal holes spaced evenly around a common
  centre.  A drawing does not locate those with x/y pairs; it states the pitch
  circle diameter and the spacing.  Without the PCD the holes have no position,
  however completely each one is dimensioned for size.
* a **chamfer** - a short oblique edge.  It is dimensioned as a leg and an
  angle ("1x45°") or covered by a blanket note, never by the coordinates of its
  two ends.

Both are recognised from geometry alone, and both are deliberately strict: four
holes at the corners of a rectangle lie on a circle too, but they are not a bolt
circle, and calling them one would replace a precise finding with a vague one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from catia_diff.models.drawing import GeometryFeature, GeometryKind
from catia_diff.models.geometry import Point2D

#: How far a hole centre may sit from the fitted circle, as a fraction of its
#: radius, before the group is not concyclic.
CONCYCLIC_RATIO = 0.01

#: How far the spacing may drift from 360°/n before the holes are not "evenly
#: spaced" - and therefore not a bolt circle.
SPACING_TOLERANCE_DEG = 2.0

#: An edge is a chamfer when it is oblique and no longer than this fraction of
#: the view.  Longer oblique edges are shape, and `DIM015` asks for their angle.
CHAMFER_RATIO = 0.10

#: How far from a cardinal direction an edge counts as oblique.
OBLIQUE_TOLERANCE_DEG = 5.0


@dataclass(frozen=True)
class BoltCircle:
    """Equal holes spaced evenly around a common centre."""

    center: Point2D
    radius: float
    hole_radius: float
    feature_ids: tuple[str, ...]

    @property
    def diameter(self) -> float:
        return self.radius * 2.0

    @property
    def count(self) -> int:
        return len(self.feature_ids)


@dataclass(frozen=True)
class ChamferEdge:
    """A short oblique edge: a broken corner, not a shape."""

    feature_id: str
    start: Point2D
    end: Point2D

    @property
    def length(self) -> float:
        return math.hypot(self.end.x - self.start.x, self.end.y - self.start.y)

    @property
    def angle(self) -> float:
        """Direction in degrees, folded into 0-180."""
        raw = math.degrees(math.atan2(self.end.y - self.start.y, self.end.x - self.start.x))
        return raw % 180.0


def bolt_circles(features: list[GeometryFeature], tolerance: float = 0.0) -> list[BoltCircle]:
    """Every group of equal, evenly spaced holes around one centre."""
    holes = [
        feature
        for feature in features
        if feature.kind is GeometryKind.CIRCLE and feature.center is not None and feature.radius
    ]
    out: list[BoltCircle] = []
    for group in _by_radius(holes, tolerance):
        circle = _fit(group, tolerance)
        if circle is not None:
            out.append(circle)
    return out


def _by_radius(holes: list[GeometryFeature], tolerance: float) -> list[list[GeometryFeature]]:
    """Holes of the same size; a bolt circle is drilled with one drill."""
    groups: list[list[GeometryFeature]] = []
    for hole in sorted(holes, key=lambda f: f.radius or 0.0):
        radius = hole.radius or 0.0
        gap = max(tolerance, radius * CONCYCLIC_RATIO, 1e-9)
        if groups and abs((groups[-1][0].radius or 0.0) - radius) <= gap:
            groups[-1].append(hole)
        else:
            groups.append([hole])
    return [group for group in groups if len(group) >= 3]


def _fit(group: list[GeometryFeature], tolerance: float) -> BoltCircle | None:
    centres = [feature.center for feature in group if feature.center is not None]
    if len(centres) < 3:
        return None
    cx = sum(point.x for point in centres) / len(centres)
    cy = sum(point.y for point in centres) / len(centres)
    radii = [math.hypot(point.x - cx, point.y - cy) for point in centres]
    radius = sum(radii) / len(radii)
    if radius <= 0:
        return None
    if max(abs(value - radius) for value in radii) > max(radius * CONCYCLIC_RATIO, tolerance):
        return None  # on no common circle

    angles = sorted(
        math.degrees(math.atan2(point.y - cy, point.x - cx)) % 360.0 for point in centres
    )
    gaps = [
        (angles[(index + 1) % len(angles)] - angle) % 360.0 for index, angle in enumerate(angles)
    ]
    expected = 360.0 / len(angles)
    if max(abs(gap - expected) for gap in gaps) > SPACING_TOLERANCE_DEG:
        # Concyclic but not evenly spaced - the corners of a rectangle, say.
        return None

    return BoltCircle(
        center=Point2D(x=cx, y=cy),
        radius=radius,
        hole_radius=group[0].radius or 0.0,
        feature_ids=tuple(feature.id for feature in group),
    )


def chamfer_edges(features: list[GeometryFeature], max_length: float) -> list[ChamferEdge]:
    """Short oblique segments of the contours in ``features``."""
    out: list[ChamferEdge] = []
    for feature in features:
        points = feature.points or []
        if len(points) < 2:
            continue
        for start, end in zip(points, points[1:], strict=False):
            edge = ChamferEdge(feature_id=feature.id, start=start, end=end)
            if 0 < edge.length <= max_length and _is_oblique(edge.angle):
                out.append(edge)
    return out


def _is_oblique(angle: float) -> bool:
    return all(
        abs(angle - cardinal) > OBLIQUE_TOLERANCE_DEG for cardinal in (0.0, 90.0, 180.0)
    )


__all__ = [
    "CHAMFER_RATIO",
    "BoltCircle",
    "ChamferEdge",
    "bolt_circles",
    "chamfer_edges",
]
