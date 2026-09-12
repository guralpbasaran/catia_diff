"""Dimensional coverage: is every position on this drawing derivable?

A drawing dimensions *positions*, not pictures.  Along one axis, the geometry
of a view exposes a set of coordinates that a machinist has to be able to
reach - the edges of the part, the centre of every hole - and each dimension
connects two of them.  That is a graph, and the question "is this drawing
fully dimensioned?" is a question about its shape:

======================  ==================================================
Graph                   Meaning
======================  ==================================================
connected, no cycle     fully dimensioned (a spanning tree)
``components - 1``      that many dimensions are **missing**
``cycles``              that many dimensions are **redundant**
======================  ==================================================

The same structure therefore answers both directions of the question, and it
answers them exactly - no proximity heuristics - as long as the source gives
each dimension its measured interval (DXF does; see
``extract/dxf_extractor._measurement_interval``).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from catia_diff.config import AuditConfig
from catia_diff.models.drawing import (
    Dimension,
    GeometryFeature,
    GeometryKind,
    Sheet,
    View,
)
from catia_diff.models.geometry import Point2D

#: Two axes closer than this are the same measuring direction.
AXIS_TOLERANCE_DEG = 0.5
#: A segment counts as perpendicular to the axis below this |cos|.
PERPENDICULAR_EPS = 1e-3


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ReferenceNode:
    """One coordinate on an axis that the drawing has to reach."""

    coordinate: float
    feature_ids: tuple[str, ...] = ()
    kinds: frozenset[str] = frozenset()  # center | outline | centerline | dimension

    @property
    def locates_feature(self) -> bool:
        return "center" in self.kinds

    def describe(self) -> str:
        return f"{self.coordinate:g}"


@dataclass
class AxisCoverage:
    """Constraint graph of one view along one axis."""

    axis: float
    nodes: list[ReferenceNode] = field(default_factory=list)
    edge_count: int = 0
    cycles: int = 0
    #: node indices grouped per connected component, largest component first
    components: list[list[int]] = field(default_factory=list)

    @property
    def missing(self) -> int:
        """Number of dimensions needed to tie the view together."""
        return max(0, len(self.components) - 1)

    @property
    def unconstrained(self) -> list[list[ReferenceNode]]:
        """Components that are not part of the largest one."""
        return [[self.nodes[i] for i in group] for group in self.components[1:]]

    @property
    def label(self) -> str:
        return axis_label(self.axis)

    @property
    def extent(self) -> tuple[float, float] | None:
        if not self.nodes:
            return None
        values = [node.coordinate for node in self.nodes]
        return (min(values), max(values))

    def is_complete(self) -> bool:
        return len(self.components) <= 1


def axis_label(axis: float) -> str:
    """'X', 'Y' or the angle itself for an oblique measuring direction."""
    if abs(axis) <= AXIS_TOLERANCE_DEG or abs(axis - 180.0) <= AXIS_TOLERANCE_DEG:
        return "X"
    if abs(axis - 90.0) <= AXIS_TOLERANCE_DEG:
        return "Y"
    return f"{axis:g}°"


# --------------------------------------------------------------------------
# Geometry -> reference coordinates
# --------------------------------------------------------------------------
def project(point: Point2D, axis_deg: float) -> float:
    angle = math.radians(axis_deg)
    return point.x * math.cos(angle) + point.y * math.sin(angle)


def has_interval_data(dimensions: Iterable[Dimension]) -> bool:
    """True when the source recorded measured intervals (vector input)."""
    return any(dim.extra.get("interval") and dim.extra.get("axis") is not None for dim in dimensions)


def view_axes(dimensions: Iterable[Dimension]) -> list[float]:
    """The measuring directions to analyse: the cardinal pair plus any oblique
    direction the drawing itself uses."""
    axes = [0.0, 90.0]
    for dim in dimensions:
        axis = dim.extra.get("axis")
        if axis is None:
            continue
        value = round(float(axis) % 180.0, 1)
        if all(abs(value - existing) > AXIS_TOLERANCE_DEG for existing in axes):
            axes.append(value)
    return sorted(axes)


def _raw_coordinates(
    features: Sequence[GeometryFeature], axis: float, *, strict: bool
) -> list[tuple[float, str, str]]:
    """(coordinate, feature id, kind) triples the drawing must be able to reach.

    Feature centres always count.  For a contour, the coordinates that matter
    are its extents and the segments that run *perpendicular* to the axis -
    those sit at a single coordinate, which is exactly what a dimension has to
    fix.  Strict mode adds every vertex instead.
    """
    out: list[tuple[float, str, str]] = []
    for feature in features:
        kind = "centerline" if _is_centerline(feature) else "outline"
        if feature.center is not None and feature.kind in {GeometryKind.CIRCLE, GeometryKind.ARC}:
            out.append((project(feature.center, axis), feature.id, "center"))
            continue
        if not feature.points:
            continue
        values = [project(point, axis) for point in feature.points]
        if strict:
            out.extend((value, feature.id, kind) for value in values)
            continue
        out.append((min(values), feature.id, kind))
        out.append((max(values), feature.id, kind))
        for start, end in zip(feature.points, feature.points[1:], strict=False):
            length = math.hypot(end.x - start.x, end.y - start.y)
            if length <= 0:
                continue
            direction = math.degrees(math.atan2(end.y - start.y, end.x - start.x))
            if abs(math.cos(math.radians(direction - axis))) <= PERPENDICULAR_EPS:
                out.append((project(start, axis), feature.id, kind))
    return out


def _is_centerline(feature: GeometryFeature) -> bool:
    from catia_diff.rules.analysis import _NON_FEATURE_LAYER_RE

    return bool(feature.layer and _NON_FEATURE_LAYER_RE.search(feature.layer))


def reference_nodes(
    features: Sequence[GeometryFeature], axis: float, tolerance: float, *, strict: bool = False
) -> list[ReferenceNode]:
    """Cluster the raw coordinates into the nodes of the constraint graph."""
    raw = sorted(_raw_coordinates(features, axis, strict=strict))
    nodes: list[ReferenceNode] = []
    for value, feature_id, kind in raw:
        if nodes and abs(value - nodes[-1].coordinate) <= tolerance:
            previous = nodes[-1]
            nodes[-1] = ReferenceNode(
                coordinate=previous.coordinate,
                feature_ids=tuple(dict.fromkeys((*previous.feature_ids, feature_id))),
                kinds=previous.kinds | {kind},
            )
        else:
            nodes.append(
                ReferenceNode(coordinate=value, feature_ids=(feature_id,), kinds=frozenset({kind}))
            )
    return nodes


# --------------------------------------------------------------------------
# The graph
# --------------------------------------------------------------------------
def build_axis_coverage(
    features: Sequence[GeometryFeature],
    dimensions: Sequence[Dimension],
    axis: float,
    tolerance: float,
    *,
    strict: bool = False,
) -> AxisCoverage:
    """Constraint graph of one view along ``axis``."""
    nodes = reference_nodes(features, axis, tolerance, strict=strict)
    coverage = AxisCoverage(axis=axis, nodes=nodes)

    def node_index(value: float) -> int:
        for index, node in enumerate(coverage.nodes):
            if abs(node.coordinate - value) <= tolerance:
                return index
        # A dimension may reference something the geometry does not expose
        # (a centre line we did not capture); it is still a real reference.
        coverage.nodes.append(
            ReferenceNode(coordinate=value, feature_ids=(), kinds=frozenset({"dimension"}))
        )
        return len(coverage.nodes) - 1

    edges: list[tuple[int, int]] = []
    for dim in dimensions:
        interval = dim.extra.get("interval")
        dim_axis = dim.extra.get("axis")
        if not interval or dim_axis is None or len(interval) != 2:
            continue
        if abs(float(dim_axis) % 180.0 - axis) > AXIS_TOLERANCE_DEG:
            continue
        if dim.is_reference:
            continue  # auxiliary dimensions constrain nothing
        edges.append((node_index(float(interval[0])), node_index(float(interval[1]))))

    parent = list(range(len(coverage.nodes)))

    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for first, second in edges:
        coverage.edge_count += 1
        root_a, root_b = find(first), find(second)
        if root_a == root_b:
            coverage.cycles += 1
        else:
            parent[root_a] = root_b

    groups: dict[int, list[int]] = {}
    for index in range(len(coverage.nodes)):
        groups.setdefault(find(index), []).append(index)
    coverage.components = sorted(groups.values(), key=len, reverse=True)
    return coverage


def view_coverage(
    sheet: Sheet, view: View, config: AuditConfig | None = None
) -> list[AxisCoverage]:
    """Coverage of every relevant axis of ``view``."""
    config = config or AuditConfig()
    members = set(view.member_ids)
    features = [f for f in sheet.features if f.id in members and (f.points or f.center)]
    dimensions = [d for d in sheet.dimensions if d.id in members]
    if not features:
        return []
    tolerance = max(_view_size(view, sheet) * config.node_merge_ratio, 1e-9)
    return [
        build_axis_coverage(
            features, dimensions, axis, tolerance, strict=config.strict_dimensioning
        )
        for axis in view_axes(dimensions)
    ]


def _view_size(view: View, sheet: Sheet) -> float:
    if view.bbox is not None:
        return max(view.bbox.width, view.bbox.height)
    return max(sheet.width, sheet.height)


def sheet_coverage(
    sheet: Sheet, config: AuditConfig | None = None
) -> list[tuple[View, list[AxisCoverage]]]:
    """Coverage per view; empty when the source carries no interval data."""
    if not has_interval_data(sheet.dimensions):
        return []
    return [
        (view, view_coverage(sheet, view, config))
        for view in sheet.views
        if view.is_geometric
    ]
