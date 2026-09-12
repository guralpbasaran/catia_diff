"""Constraint graph: reference nodes, components, cycles."""

import pytest
from conftest import box, make_circle, make_dimension, make_sheet

from catia_diff.config import AuditConfig
from catia_diff.models.drawing import GeometryFeature, GeometryKind
from catia_diff.models.geometry import BBox, Point2D
from catia_diff.rules.constraints import (
    axis_label,
    build_axis_coverage,
    has_interval_data,
    project,
    reference_nodes,
    sheet_coverage,
    view_axes,
)
from catia_diff.rules.views import segment_views


def outline(feature_id="OUT1", points=((0, 0), (80, 0), (80, 40), (0, 40), (0, 0)), layer="PART"):
    pts = [Point2D(x=float(x), y=float(y)) for x, y in points]
    return GeometryFeature(
        id=feature_id,
        kind=GeometryKind.POLYLINE,
        points=pts,
        closed=True,
        bbox=BBox.from_points(pts),
        layer=layer,
    )


def linear(dim_id, nominal, axis, interval, **kwargs):
    return make_dimension(
        dim_id, nominal=nominal, extra={"axis": axis, "interval": list(interval)}, **kwargs
    )


# ------------------------------------------------------------------ basics
def test_axis_label_and_projection():
    assert axis_label(0.0) == "X"
    assert axis_label(90.0) == "Y"
    assert axis_label(30.0) == "30°"
    assert project(Point2D(x=3, y=4), 0.0) == 3
    assert project(Point2D(x=3, y=4), 90.0) == pytest.approx(4)


def test_view_axes_include_oblique_directions():
    dims = [linear("D1", 10, 0.0, (0, 10)), linear("D2", 10, 30.0, (0, 10))]
    assert view_axes(dims) == [0.0, 30.0, 90.0]


def test_has_interval_data_distinguishes_sources():
    assert has_interval_data([linear("D1", 10, 0.0, (0, 10))])
    assert not has_interval_data([make_dimension("D1")])


# ------------------------------------------------------------------- nodes
def test_reference_nodes_use_extents_and_perpendicular_edges():
    nodes = reference_nodes([outline()], 0.0, 0.01)
    assert [round(n.coordinate, 3) for n in nodes] == [0.0, 80.0]

    with_hole = reference_nodes([outline(), make_circle("F1", x=12, y=10)], 0.0, 0.01)
    assert [round(n.coordinate, 3) for n in with_hole] == [0.0, 12.0, 80.0]
    assert with_hole[1].locates_feature
    assert with_hole[1].feature_ids == ("F1",)


def test_reference_nodes_merge_within_tolerance():
    close = [make_circle("F1", x=12.0, y=0), make_circle("F2", x=12.0005, y=0)]
    nodes = reference_nodes(close, 0.0, 0.01)
    assert len(nodes) == 1
    assert set(nodes[0].feature_ids) == {"F1", "F2"}


def test_strict_mode_keeps_every_vertex():
    stepped = outline(points=((0, 0), (30, 0), (30, 10), (80, 10), (80, 40), (0, 40), (0, 0)))
    default = reference_nodes([stepped], 90.0, 0.01)
    strict = reference_nodes([stepped], 90.0, 0.01, strict=True)
    assert len(strict) >= len(default)
    assert [round(n.coordinate, 2) for n in strict] == [0.0, 10.0, 40.0]


def test_centerline_layers_are_still_reference_nodes():
    center = outline("CL1", points=((40, -5), (40, 45)), layer="CENTER")
    nodes = reference_nodes([center], 0.0, 0.01)
    assert [round(n.coordinate, 2) for n in nodes] == [40.0]
    assert "centerline" in nodes[0].kinds


# ------------------------------------------------------------------- graph
def test_spanning_tree_is_complete():
    features = [outline(), make_circle("F1", x=12, y=10)]
    dims = [
        linear("D1", 80, 0.0, (0, 80)),
        linear("D2", 12, 0.0, (0, 12)),
    ]
    coverage = build_axis_coverage(features, dims, 0.0, 0.01)
    assert coverage.is_complete()
    assert coverage.missing == 0
    assert coverage.cycles == 0
    assert coverage.edge_count == 2


def test_disconnected_node_is_one_missing_dimension():
    features = [outline(), make_circle("F1", x=12, y=10)]
    coverage = build_axis_coverage(features, [linear("D1", 80, 0.0, (0, 80))], 0.0, 0.01)
    assert coverage.missing == 1
    free = coverage.unconstrained
    assert len(free) == 1 and free[0][0].feature_ids == ("F1",)


def test_extra_dimension_is_a_cycle():
    features = [outline(), make_circle("F1", x=12, y=10)]
    dims = [
        linear("D1", 12, 0.0, (0, 12)),
        linear("D2", 68, 0.0, (12, 80)),
        linear("D3", 80, 0.0, (0, 80)),
    ]
    coverage = build_axis_coverage(features, dims, 0.0, 0.01)
    assert coverage.is_complete()
    assert coverage.cycles == 1


def test_reference_dimensions_do_not_constrain():
    """An auxiliary (20) dimension carries no obligation, so it adds no edge."""
    features = [outline(), make_circle("F1", x=12, y=10)]
    auxiliary = linear("D1", 12, 0.0, (0, 12), is_reference=True)
    coverage = build_axis_coverage(features, [auxiliary], 0.0, 0.01)
    assert coverage.edge_count == 0
    assert coverage.missing == 2  # 0, 12 and 80 all stand alone

    binding = linear("D2", 12, 0.0, (0, 12))
    assert build_axis_coverage(features, [binding], 0.0, 0.01).missing == 1


def test_dimension_endpoint_without_geometry_becomes_a_node():
    features = [outline()]
    coverage = build_axis_coverage(features, [linear("D1", 50, 0.0, (0, 50))], 0.0, 0.01)
    coordinates = sorted(round(node.coordinate, 1) for node in coverage.nodes)
    assert coordinates == [0.0, 50.0, 80.0]
    assert coverage.missing == 1  # 80 is still unreached


def test_extent_and_labels():
    coverage = build_axis_coverage([outline()], [], 0.0, 0.01)
    assert coverage.extent == (0.0, 80.0)
    assert coverage.label == "X"


# ---------------------------------------------------------------- sheet API
def test_sheet_coverage_needs_interval_data():
    sheet = make_sheet(features=[outline()], dimensions=[make_dimension("D1")])
    segment_views(sheet, AuditConfig())
    assert sheet_coverage(sheet, AuditConfig()) == []


def test_sheet_coverage_per_view():
    sheet = make_sheet(
        features=[outline(), make_circle("F1", x=12, y=10)],
        dimensions=[linear("D1", 80, 0.0, (0, 80), bbox=box(0, -12))],
    )
    segment_views(sheet, AuditConfig())
    result = sheet_coverage(sheet, AuditConfig())
    assert len(result) == 1
    view, axes = result[0]
    assert view.is_geometric
    labels = {cov.label: cov.missing for cov in axes}
    assert labels["X"] == 1  # the hole centre is unreached


# ------------------------------------------------------------------ cycles
def test_dimension_cycles_need_no_geometry():
    """Over-dimensioning is a property of the dimension set alone."""
    from catia_diff.rules.constraints import dimension_cycles

    chain = [
        linear("D1", 12, 0.0, (0, 12)),
        linear("D2", 68, 0.0, (12, 80)),
        linear("D3", 80, 0.0, (0, 80)),
    ]
    cycles = dimension_cycles(chain)
    assert len(cycles) == 1
    cycle = cycles[0]
    assert cycle.closing.id == "D3"
    assert {dim.id for dim in cycle.dimensions} == {"D1", "D2", "D3"}
    assert cycle.overall.id == "D3"
    assert {dim.id for dim in cycle.parts} == {"D1", "D2"}
    assert cycle.exact and cycle.label == "X"


def test_dimension_cycles_are_silent_on_a_tree():
    from catia_diff.rules.constraints import dimension_cycles

    assert dimension_cycles([linear("D1", 12, 0.0, (0, 12)), linear("D2", 68, 0.0, (12, 80))]) == []


def test_coverage_reports_the_cycle_detail():
    features = [outline(), make_circle("F1", x=12, y=10)]
    dims = [
        linear("D1", 12, 0.0, (0, 12)),
        linear("D2", 68, 0.0, (12, 80)),
        linear("D3", 80, 0.0, (0, 80)),
    ]
    coverage = build_axis_coverage(features, dims, 0.0, 0.01)
    assert coverage.cycles == 1
    assert coverage.redundant[0].closing.id == "D3"
