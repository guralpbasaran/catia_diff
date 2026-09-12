"""Geometric reasoning shared by several rules.

These helpers are heuristics on purpose - they answer questions ("is this hole
dimensioned?", "do these three dimensions form a closed chain?") that a 2D
drawing only answers implicitly.  Every helper documents its assumption so the
findings built on it can carry an honest confidence value.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from catia_diff.models.drawing import (
    Dimension,
    DimensionKind,
    GeometryFeature,
    GeometryKind,
    Sheet,
    ToleranceKind,
)
from catia_diff.rules.constraints import DimensionCycle, dimension_cycles

#: Layers whose circles are construction geometry rather than real features.
_NON_FEATURE_LAYER_RE = re.compile(
    r"(center|centre|axis|hidden|phantom|dim|text|annot|hatch|frame|border|title|"
    r"eksen|yaz|tarama|cerceve|çerçeve)",
    re.IGNORECASE,
)


def candidate_features(sheet: Sheet) -> list[GeometryFeature]:
    """Circles/arcs that a drawing is expected to dimension."""
    if not sheet.features:
        return []
    reference = max(sheet.width, sheet.height) or 1.0
    min_radius = reference * 0.002
    out: list[GeometryFeature] = []
    for feature in sheet.features:
        if feature.kind not in {GeometryKind.CIRCLE, GeometryKind.ARC}:
            continue
        if feature.radius is None or feature.radius < min_radius:
            continue
        if feature.layer and _NON_FEATURE_LAYER_RE.search(feature.layer):
            continue
        out.append(feature)
    return out


def dimension_matches_feature(dim: Dimension, feature: GeometryFeature) -> bool:
    """Does ``dim`` plausibly dimension ``feature``?

    Assumption: a dimension describes a circle when its value equals the
    diameter (⌀, thread or plain linear callout) or the radius (R), within a
    0.5% tolerance.  Position on the sheet is deliberately *not* used: leader
    lines routinely place a hole callout far from the hole.
    """
    if dim.nominal is None or feature.radius is None:
        return False
    diameter = feature.radius * 2.0
    slack = max(diameter * 5e-3, 1e-4)

    if dim.kind is DimensionKind.RADIAL or dim.prefix in {"R", "SR"}:
        return abs(dim.nominal - feature.radius) <= max(feature.radius * 5e-3, 1e-4)
    if dim.kind is DimensionKind.DIAMETER or (dim.prefix or "").endswith("⌀"):
        return abs(dim.nominal - diameter) <= slack
    if dim.kind is DimensionKind.THREAD or dim.extra.get("thread"):
        # A tapped hole is drawn at its tapping-drill diameter, always smaller
        # than the thread's nominal size.
        return 0.75 * dim.nominal <= diameter <= 1.05 * dim.nominal
    if dim.kind.is_linear_like:
        return abs(dim.nominal - diameter) <= slack
    return False


def associate_features(sheet: Sheet) -> tuple[dict[str, list[str]], list[GeometryFeature]]:
    """Map features to the dimensions that describe them.

    Returns ``(feature_id -> [dimension ids], undimensioned features)``.  A
    callout such as ``4x ⌀6.5`` covers up to four identical features.
    """
    features = candidate_features(sheet)
    budget: dict[str, int] = {}
    for dim in sheet.dimensions:
        try:
            budget[dim.id] = max(1, int(dim.extra.get("multiplicity", 1) or 1))
        except (TypeError, ValueError):
            budget[dim.id] = 1

    covered: dict[str, list[str]] = {}
    undimensioned: list[GeometryFeature] = []
    for feature in features:
        matches = [
            dim
            for dim in sheet.dimensions
            if budget.get(dim.id, 0) > 0 and dimension_matches_feature(dim, feature)
        ]
        if not matches:
            undimensioned.append(feature)
            continue
        used = matches[0]
        budget[used.id] -= 1
        covered[feature.id] = [dim.id for dim in matches]
    return covered, undimensioned


# --------------------------------------------------------------------------
# Dimension chains
# --------------------------------------------------------------------------
def _measured_value(dim: Dimension) -> float | None:
    return dim.nominal if dim.nominal is not None else dim.measured


def linear_dimensions(sheet: Sheet) -> list[Dimension]:
    return [
        dim
        for dim in sheet.dimensions
        if dim.kind.is_linear_like and _measured_value(dim) is not None and not dim.is_reference
    ]


def collinear_clusters(dims: Iterable[Dimension], axis: str) -> list[list[Dimension]]:
    """Group dimensions whose callouts sit on a common row (``axis='x'``) or
    column (``axis='y'``) - the usual layout of a dimension chain."""
    items = [dim for dim in dims if dim.bbox is not None]
    if len(items) < 3:
        return []
    key = (lambda d: d.bbox.center.y) if axis == "x" else (lambda d: d.bbox.center.x)
    order = (lambda d: d.bbox.center.x) if axis == "x" else (lambda d: d.bbox.center.y)
    spread = [d.bbox.height if axis == "x" else d.bbox.width for d in items]
    tolerance = max(max(spread) * 1.2, 1e-9)

    clusters: list[list[Dimension]] = []
    for dim in sorted(items, key=key):
        if clusters and abs(key(dim) - key(clusters[-1][-1])) <= tolerance:
            clusters[-1].append(dim)
        else:
            clusters.append([dim])
    return [sorted(cluster, key=order) for cluster in clusters if len(cluster) >= 3]


def find_closed_chains(
    sheet: Sheet, *, relative_tolerance: float = 2e-3
) -> list[DimensionCycle]:
    """Redundant dimensions on ``sheet`` (over-dimensioning).

    A drawing over-dimensions when it fixes the same distance twice.  In the
    constraint graph of :mod:`catia_diff.rules.constraints` that is exactly a
    cycle, so vector sources are analysed there - the result names the
    dimensions the cycle runs through, not just their count.

    Sources without measured intervals (PDF text, vision) cannot be analysed
    that way; for them the weaker fallback below compares sums within a group
    of collinear callouts and marks its findings as inexact.
    """
    dims = linear_dimensions(sheet)
    if any(dim.extra.get("interval") for dim in dims):
        return dimension_cycles(dims, relative_tolerance=relative_tolerance)
    return _cluster_chains(dims, relative_tolerance)


def _cluster_chains(
    dims: list[Dimension], relative_tolerance: float
) -> list[DimensionCycle]:
    """Fallback for sources that do not record what each dimension measures."""
    chains: list[DimensionCycle] = []
    for axis, degrees in (("x", 0.0), ("y", 90.0)):
        for cluster in collinear_clusters(dims, axis):
            values = [(dim, _measured_value(dim) or 0.0) for dim in cluster]
            overall_dim, overall_value = max(values, key=lambda pair: pair[1])
            parts = [pair for pair in values if pair[0] is not overall_dim]
            if len(parts) < 2 or overall_value <= 0:
                continue
            total = sum(value for _, value in parts)
            if abs(total - overall_value) <= overall_value * relative_tolerance:
                chains.append(
                    DimensionCycle(
                        axis=degrees,
                        dimensions=(overall_dim, *(dim for dim, _ in parts)),
                        closing=overall_dim,
                        exact=False,
                    )
                )
    return chains


# --------------------------------------------------------------------------
# Placement
# --------------------------------------------------------------------------
def duplicate_dimension_pairs(
    sheet: Sheet, *, iou_threshold: float = 0.6
) -> list[tuple[Dimension, Dimension]]:
    """Dimensions with the same value whose callouts overlap or repeat.

    Two identical values are only reported when their text boxes overlap
    (the same callout drawn twice) - repeating a value on two different
    features is perfectly normal.
    """
    pairs: list[tuple[Dimension, Dimension]] = []
    dims = [d for d in sheet.dimensions if d.bbox is not None and d.nominal is not None]
    for i, first in enumerate(dims):
        for second in dims[i + 1:]:
            if first.kind is not second.kind:
                continue
            slack = max(abs(first.nominal) * 1e-4, 1e-6)
            if abs(first.nominal - second.nominal) > slack:
                continue
            if first.bbox.iou(second.bbox) >= iou_threshold:
                pairs.append((first, second))
    return pairs


def overlapping_labels(sheet: Sheet, *, min_iou: float = 0.25) -> list[tuple[str, str, float]]:
    """Annotation/dimension text boxes that collide (illegible drawing)."""
    items = [
        (obj.id, obj.bbox)
        for obj in (*sheet.dimensions, *sheet.geometric_tolerances, *sheet.annotations)
        if obj.bbox is not None and obj.bbox.area > 0
    ]
    out: list[tuple[str, str, float]] = []
    for i, (first_id, first_box) in enumerate(items):
        for second_id, second_box in items[i + 1:]:
            iou = first_box.iou(second_box)
            if iou >= min_iou:
                out.append((first_id, second_id, iou))
    return out


def outside_sheet(sheet: Sheet, margin_ratio: float = 0.0) -> list[str]:
    """Objects whose box leaves the sheet frame."""
    frame = sheet.bbox
    if frame.width <= 0 or frame.height <= 0:
        return []
    margin = max(frame.width, frame.height) * margin_ratio
    inner = frame.expanded(-margin) if margin else frame
    return [
        obj.id
        for obj in sheet.objects()
        if obj.bbox is not None and not inner.contains(obj.bbox) and not inner.intersects(obj.bbox)
    ]


# --------------------------------------------------------------------------
# Tolerance arithmetic
# --------------------------------------------------------------------------
#: Tolerance notations that carry numbers we can compute with.  Fit classes
#: (H7, g6) do not: their numeric limits come from ISO 286, which this package
#: does not tabulate, so they are reported as "unknown" rather than guessed.
_NUMERIC_KINDS = {ToleranceKind.SYMMETRIC, ToleranceKind.DEVIATION, ToleranceKind.LIMITS}


def tolerance_deviations(dim: Dimension) -> tuple[float, float] | None:
    """Explicit deviations of ``dim`` as ``(upper, lower)`` around its nominal."""
    tol = dim.tolerance
    if tol.kind not in _NUMERIC_KINDS or tol.upper is None or tol.lower is None:
        return None
    if tol.kind is ToleranceKind.LIMITS:
        if dim.nominal is None:
            return None
        return (tol.upper - dim.nominal, tol.lower - dim.nominal)
    return (tol.upper, tol.lower)


def tolerance_width(dim: Dimension) -> float | None:
    """Width of the explicit tolerance zone of ``dim``."""
    deviations = tolerance_deviations(dim)
    if deviations is None:
        return None
    upper, lower = deviations
    return abs(upper - lower)
