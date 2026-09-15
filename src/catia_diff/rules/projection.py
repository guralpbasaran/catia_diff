"""Orthographic alignment between the views of one sheet.

Two views of the same part are not independent drawings.  In an orthographic
layout the top view sits directly under the front view and **shares its
width**; the side view sits beside it and shares its height.  That shared
extent is the only thing on a flat sheet that ties two views together without
3D data - and it is enough to answer the two questions a per-view analysis
cannot:

* Is an axis this view does not dimension already fixed by the view it is
  aligned with?  Orthographic practice dimensions a feature once, in the view
  that shows it best, so demanding it again here would be a false positive -
  and a loud one, since it fires on every correctly drawn multi-view sheet.
* Do the two views actually agree about that shared extent?  If they do not,
  one of them is wrong and the shop reads whichever it happens to look at.

Pure functions over the extracted model; no rule logic lives here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from catia_diff.config import AuditConfig
from catia_diff.models.drawing import Dimension, GeometryFeature, Sheet, View
from catia_diff.models.geometry import BBox
from catia_diff.rules.analysis import is_imaginary
from catia_diff.rules.constraints import AxisCoverage, axis_label, project

#: Measuring directions two views can share.
AXIS_X = 0.0
AXIS_Y = 90.0

#: Fraction of the smaller view that the projected overlap must cover before
#: the pair counts as aligned.  Well below this the views merely happen to sit
#: near each other.
MIN_OVERLAP = 0.7

#: Two aligned views are "the same size" when their shared extents differ by
#: less than this fraction.  Above it they are at different scales (a detail at
#: 2:1, a broken view) and nothing may be inherited or compared between them.
SAME_SIZE_RATIO = 0.02

#: A mismatch wider than this is a different view type, not a defect: a
#: half-section, a detail, a broken view.  Between the two ratios sits the case
#: worth reporting - two views that were meant to match and no longer do.
SUSPICIOUS_RATIO = 0.10


@dataclass(frozen=True)
class ViewAlignment:
    """Two views that share one measuring direction."""

    a: View
    b: View
    axis: float
    overlap: float
    extent_a: tuple[float, float]
    extent_b: tuple[float, float]

    @property
    def label(self) -> str:
        return axis_label(self.axis)

    @property
    def length_a(self) -> float:
        return self.extent_a[1] - self.extent_a[0]

    @property
    def length_b(self) -> float:
        return self.extent_b[1] - self.extent_b[0]

    @property
    def difference(self) -> float:
        return abs(self.length_a - self.length_b)

    @property
    def ratio(self) -> float:
        """Size mismatch as a fraction of the larger extent."""
        longer = max(self.length_a, self.length_b)
        return self.difference / longer if longer > 0 else 0.0

    @property
    def same_size(self) -> bool:
        return self.ratio <= SAME_SIZE_RATIO

    @property
    def comparable(self) -> bool:
        """Close enough that the two views were meant to show the same extent.

        Beyond this they are different things - a detail at 2:1, a broken view -
        and neither the comparison nor the inheritance below applies.
        """
        return self.ratio <= SUSPICIOUS_RATIO

    @property
    def suspicious(self) -> bool:
        """Mismatched, but by an amount an edit could plausibly explain."""
        return SAME_SIZE_RATIO < self.ratio <= SUSPICIOUS_RATIO

    def views(self) -> tuple[View, View]:
        return (self.a, self.b)


# ---------------------------------------------------------------------------
def aligned_views(sheet: Sheet, config: AuditConfig | None = None) -> list[ViewAlignment]:
    """Every pair of views that share a measuring direction.

    Detail views are left out: a magnified fragment shares no extent with its
    parent, so neither inheritance nor comparison is meaningful for it.
    """
    config = config or AuditConfig()
    views = [v for v in sheet.views if v.is_geometric and v.bbox is not None and not v.is_detail]
    out: list[ViewAlignment] = []
    for index, first in enumerate(views):
        for second in views[index + 1 :]:
            alignment = _align(sheet, first, second)
            if alignment is not None:
                out.append(alignment)
    return out


def _align(sheet: Sheet, a: View, b: View) -> ViewAlignment | None:
    assert a.bbox is not None and b.bbox is not None
    for axis, along, across in (
        (AXIS_X, _x_range, _y_range),
        (AXIS_Y, _y_range, _x_range),
    ):
        overlap = _overlap_ratio(along(a.bbox), along(b.bbox))
        if overlap < MIN_OVERLAP or _overlap_ratio(across(a.bbox), across(b.bbox)) > 0.0:
            continue
        extent_a = geometry_extent(sheet, a, axis)
        extent_b = geometry_extent(sheet, b, axis)
        if extent_a is None or extent_b is None:
            continue
        return ViewAlignment(a, b, axis, overlap, extent_a, extent_b)
    return None


def _x_range(box: BBox) -> tuple[float, float]:
    return (box.x0, box.x1)


def _y_range(box: BBox) -> tuple[float, float]:
    return (box.y0, box.y1)


def _overlap_ratio(first: tuple[float, float], second: tuple[float, float]) -> float:
    """Shared length as a fraction of the shorter range."""
    low = max(first[0], second[0])
    high = min(first[1], second[1])
    shorter = min(first[1] - first[0], second[1] - second[0])
    if shorter <= 0:
        return 0.0
    return max(0.0, high - low) / shorter


# ---------------------------------------------------------------------------
def view_features(sheet: Sheet, view: View) -> list[GeometryFeature]:
    """The part geometry of a view.

    A cutting plane drawn across the view belongs to the *reference* it marks,
    not to the part: letting it stretch the view's extent would make two
    correctly drawn views look like they disagree.
    """
    members = set(view.member_ids)
    return [
        f
        for f in sheet.features
        if f.id in members and (f.points or f.center) and not is_imaginary(f)
    ]


def view_dimensions(sheet: Sheet, view: View) -> list[Dimension]:
    members = set(view.member_ids)
    return [d for d in sheet.dimensions if d.id in members]


def geometry_extent(sheet: Sheet, view: View, axis: float) -> tuple[float, float] | None:
    """How far the view's geometry reaches along ``axis``."""
    values: list[float] = []
    for feature in view_features(sheet, view):
        if feature.points:
            values.extend(project(point, axis) for point in feature.points)
        elif feature.center is not None:
            values.append(project(feature.center, axis))
    if not values:
        return None
    return (min(values), max(values))


def overall_dimensions(
    sheet: Sheet, view: View, axis: float, tolerance: float
) -> list[Dimension]:
    """Dimensions that state the view's full extent along ``axis``.

    A dimension qualifies when its measured interval reaches both ends of the
    geometry - that is what "the overall dimension of this view" means, and it
    is the one value two aligned views must agree on.
    """
    extent = geometry_extent(sheet, view, axis)
    if extent is None:
        return []
    out: list[Dimension] = []
    for dim in view_dimensions(sheet, view):
        interval = _interval_on_axis(dim, axis)
        if interval is None:
            continue
        if abs(interval[0] - extent[0]) <= tolerance and abs(interval[1] - extent[1]) <= tolerance:
            out.append(dim)
    return out


def _interval_on_axis(dim: Dimension, axis: float) -> tuple[float, float] | None:
    interval = dim.extra.get("interval")
    dim_axis = dim.extra.get("axis")
    if not interval or dim_axis is None or abs(float(dim_axis) - axis) > 1.0:
        return None
    low, high = float(interval[0]), float(interval[1])
    return (min(low, high), max(low, high))


# ---------------------------------------------------------------------------
def inherited_axes(
    sheet: Sheet,
    coverages: Sequence[tuple[View, list[AxisCoverage]]],
    config: AuditConfig | None = None,
) -> dict[str, set[str]]:
    """Axes a view may leave undimensioned because an aligned view fixes them.

    ``{view id: {"X", "Y"}}``.  An axis is inherited when the partner view has
    it fully constrained: a partner that is itself incomplete passes nothing on,
    so a real gap is still reported exactly once instead of disappearing between
    the two views.

    Inheritance does not require the two extents to *match*: when they disagree
    the conflict is the finding (CRV001/CRV002), and repeating it as "this view
    is missing two dimensions" would only bury the cause under its consequences.
    A pair too far apart to have been meant as the same extent is not aligned
    for this purpose at all.
    """
    config = config or AuditConfig()
    complete: dict[str, set[float]] = {}
    for view, axes in coverages:
        complete[view.id] = {axis.axis for axis in axes if axis.is_complete() and axis.edge_count}

    out: dict[str, set[str]] = {}
    for alignment in aligned_views(sheet, config):
        if not alignment.comparable:
            continue
        for view, partner in ((alignment.a, alignment.b), (alignment.b, alignment.a)):
            if alignment.axis in complete.get(partner.id, set()):
                out.setdefault(view.id, set()).add(axis_label(alignment.axis))
    return out


__all__ = [
    "AXIS_X",
    "AXIS_Y",
    "ViewAlignment",
    "aligned_views",
    "geometry_extent",
    "inherited_axes",
    "overall_dimensions",
    "view_dimensions",
    "view_features",
]
