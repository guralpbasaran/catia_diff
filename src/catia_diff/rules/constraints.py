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


@dataclass(frozen=True)
class DimensionCycle:
    """A set of dimensions that constrain the same distance twice.

    Every edge that closes a cycle in the constraint graph is exactly one
    redundant dimension: the drawing can reach both of its endpoints already,
    so the value it states must agree with the rest by construction.
    """

    axis: float
    dimensions: tuple[Dimension, ...]
    closing: Dimension
    exact: bool = True

    @property
    def overall(self) -> Dimension:
        """The widest dimension of the cycle - the one a chain adds up to."""
        return max(self.dimensions, key=_dimension_span)

    @property
    def parts(self) -> tuple[Dimension, ...]:
        overall = self.overall
        return tuple(dim for dim in self.dimensions if dim is not overall)

    @property
    def label(self) -> str:
        return axis_label(self.axis)

    @property
    def confidence(self) -> float:
        return 0.95 if self.exact else 0.6


def _dimension_span(dim: Dimension) -> float:
    interval = dim.extra.get("interval")
    if interval and len(interval) == 2:
        return abs(float(interval[1]) - float(interval[0]))
    return abs(dim.nominal or 0.0)


class _Graph:
    """Union-find over reference coordinates that also remembers its tree.

    Keeping the spanning forest lets a cycle be reported as the dimensions it
    actually runs through, instead of only as a count.
    """

    def __init__(self, tolerance: float) -> None:
        self.tolerance = tolerance
        self.nodes: list[ReferenceNode] = []
        self._parent: list[int] = []
        self._tree: dict[int, list[tuple[int, Dimension]]] = {}

    def add_node(self, node: ReferenceNode) -> int:
        self.nodes.append(node)
        self._parent.append(len(self._parent))
        return len(self.nodes) - 1

    def index_of(self, value: float, *, kind: str = "dimension") -> int:
        for index, node in enumerate(self.nodes):
            if abs(node.coordinate - value) <= self.tolerance:
                return index
        return self.add_node(
            ReferenceNode(coordinate=value, feature_ids=(), kinds=frozenset({kind}))
        )

    def find(self, item: int) -> int:
        while self._parent[item] != item:
            self._parent[item] = self._parent[self._parent[item]]
            item = self._parent[item]
        return item

    def add_edge(self, first: int, second: int, dim: Dimension, axis: float) -> DimensionCycle | None:
        """Link two coordinates; returns the cycle when the edge closes one."""
        root_a, root_b = self.find(first), self.find(second)
        if root_a != root_b:
            self._parent[root_a] = root_b
            self._tree.setdefault(first, []).append((second, dim))
            self._tree.setdefault(second, []).append((first, dim))
            return None
        path = self._path(first, second)
        return DimensionCycle(
            axis=axis, dimensions=(*path, dim), closing=dim, exact=True
        )

    def _path(self, start: int, goal: int) -> tuple[Dimension, ...]:
        """Dimensions along the existing route between two connected nodes."""
        queue: list[tuple[int, tuple[Dimension, ...]]] = [(start, ())]
        seen = {start}
        while queue:
            node, dims = queue.pop(0)
            if node == goal:
                return dims
            for neighbour, dim in self._tree.get(node, ()):
                if neighbour in seen:
                    continue
                seen.add(neighbour)
                queue.append((neighbour, (*dims, dim)))
        return ()

    def components(self) -> list[list[int]]:
        groups: dict[int, list[int]] = {}
        for index in range(len(self.nodes)):
            groups.setdefault(self.find(index), []).append(index)
        return sorted(groups.values(), key=len, reverse=True)


@dataclass
class AxisCoverage:
    """Constraint graph of one view along one axis."""

    axis: float
    nodes: list[ReferenceNode] = field(default_factory=list)
    edge_count: int = 0
    #: the redundant dimensions: one entry per cycle-closing edge
    redundant: list[DimensionCycle] = field(default_factory=list)
    #: node indices grouped per connected component, largest component first
    components: list[list[int]] = field(default_factory=list)

    @property
    def cycles(self) -> int:
        return len(self.redundant)

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
        if _is_imaginary(feature):
            continue  # a cutting plane is not a coordinate to dimension
        kind = "centerline" if _is_centerline(feature) else "outline"
        if feature.center is not None and feature.kind in {GeometryKind.CIRCLE, GeometryKind.ARC}:
            out.append((project(feature.center, axis), feature.id, "center"))
            continue
        if not feature.points:
            continue
        if kind == "centerline":
            out.extend(_centerline_coordinates(feature, axis))
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


def _centerline_coordinates(
    feature: GeometryFeature, axis: float
) -> list[tuple[float, str, str]]:
    """The single coordinate a centre line marks, if it marks one on this axis.

    A centre line stands for a position, and how far past the feature it is
    drawn is draughting style.  Its endpoints are therefore not coordinates a
    dimension has to reach: only the axis it is perpendicular to gets a node,
    which is the position it announces.
    """
    out: list[tuple[float, str, str]] = []
    for start, end in zip(feature.points, feature.points[1:], strict=False):
        if math.hypot(end.x - start.x, end.y - start.y) <= 0:
            continue
        direction = math.degrees(math.atan2(end.y - start.y, end.x - start.x))
        if abs(math.cos(math.radians(direction - axis))) <= PERPENDICULAR_EPS:
            out.append((project(start, axis), feature.id, "centerline"))
    return out


def _is_imaginary(feature: GeometryFeature) -> bool:
    from catia_diff.rules.analysis import is_imaginary

    return is_imaginary(feature)


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
    graph = _Graph(tolerance)
    for node in reference_nodes(features, axis, tolerance, strict=strict):
        graph.add_node(node)

    coverage = AxisCoverage(axis=axis, nodes=graph.nodes)
    for dim, low, high in _axis_edges(dimensions, axis):
        # A dimension may reference something the geometry does not expose
        # (a centre line we did not capture); it is still a real reference.
        cycle = graph.add_edge(graph.index_of(low), graph.index_of(high), dim, axis)
        coverage.edge_count += 1
        if cycle is not None:
            coverage.redundant.append(cycle)
    coverage.nodes = graph.nodes
    coverage.components = graph.components()
    return coverage


def _axis_edges(
    dimensions: Iterable[Dimension], axis: float
) -> list[tuple[Dimension, float, float]]:
    """Dimensions that measure along ``axis``, as (dimension, start, end)."""
    edges: list[tuple[Dimension, float, float]] = []
    for dim in dimensions:
        interval = dim.extra.get("interval")
        dim_axis = dim.extra.get("axis")
        if not interval or dim_axis is None or len(interval) != 2:
            continue
        if abs(float(dim_axis) % 180.0 - axis) > AXIS_TOLERANCE_DEG:
            continue
        if dim.is_reference:
            continue  # auxiliary dimensions constrain nothing
        low, high = float(interval[0]), float(interval[1])
        edges.append((dim, min(low, high), max(low, high)))
    return edges


def dimension_cycles(
    dimensions: Sequence[Dimension], *, relative_tolerance: float = 1e-3
) -> list[DimensionCycle]:
    """Redundant dimensions, found from the dimensions alone.

    Over-dimensioning is a property of the dimension set: it needs no geometry
    and no view segmentation, only the interval each dimension measures.  That
    makes this usable wherever the source records intervals, even on a sheet
    whose geometry was not captured.
    """
    cycles: list[DimensionCycle] = []
    for axis in view_axes(dimensions):
        edges = _axis_edges(dimensions, axis)
        if len(edges) < 2:
            continue
        values = [value for _, low, high in edges for value in (low, high)]
        span = max(values) - min(values)
        graph = _Graph(max(span * relative_tolerance, 1e-9))
        for dim, low, high in edges:
            cycle = graph.add_edge(graph.index_of(low), graph.index_of(high), dim, axis)
            if cycle is not None:
                cycles.append(cycle)
    return cycles


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
