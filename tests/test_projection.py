"""Orthographic alignment: which views share an extent, and what that implies."""

from __future__ import annotations

import pytest

from catia_diff.config import AuditConfig
from catia_diff.models.drawing import Sheet, View
from catia_diff.models.geometry import BBox
from catia_diff.rules import projection
from catia_diff.rules.constraints import sheet_coverage
from tests.conftest import box, make_circle, make_dimension, make_sheet


def view(view_id: str, x0: float, y0: float, x1: float, y1: float, **kwargs) -> View:
    return View(id=view_id, bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1), **kwargs)


def outline(feature_id: str, x0: float, y0: float, x1: float, y1: float):
    from catia_diff.models.drawing import GeometryFeature, GeometryKind
    from catia_diff.models.geometry import Point2D

    return GeometryFeature(
        id=feature_id,
        kind=GeometryKind.POLYLINE,
        layer="PART",
        bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
        closed=True,
        points=[
            Point2D(x=x0, y=y0),
            Point2D(x=x1, y=y0),
            Point2D(x=x1, y=y1),
            Point2D(x=x0, y=y1),
            Point2D(x=x0, y=y0),
        ],
    )


def linear(dim_id: str, axis: float, low: float, high: float, nominal: float | None = None):
    return make_dimension(
        dim_id,
        nominal=nominal if nominal is not None else high - low,
        bbox=box(low, 0),
        extra={"axis": axis, "interval": [low, high]},
    )


def stacked_sheet(top_width: float = 80.0, **kwargs) -> Sheet:
    """Front view over a top view: the classic pair aligned on X."""
    front = outline("F1", 0, 0, 80, 40)
    top = outline("T1", 0, -70, top_width, -50)
    sheet = make_sheet(
        features=[front, top],
        dimensions=kwargs.pop("dimensions", []),
        views=[
            view("VIEW01", 0, 0, 80, 40, member_ids=["F1"]),
            view("VIEW02", 0, -70, top_width, -50, member_ids=["T1"]),
        ],
        **kwargs,
    )
    return sheet


def test_stacked_views_are_aligned_on_x():
    alignments = projection.aligned_views(stacked_sheet())
    assert len(alignments) == 1
    pair = alignments[0]
    assert pair.label == "X"
    assert pair.overlap == pytest.approx(1.0)
    assert (pair.length_a, pair.length_b) == (80.0, 80.0)
    assert pair.same_size and pair.comparable and not pair.suspicious


def test_side_by_side_views_are_aligned_on_y():
    sheet = make_sheet(
        features=[outline("F1", 0, 0, 80, 40), outline("S1", 100, 0, 120, 40)],
        views=[
            view("VIEW01", 0, 0, 80, 40, member_ids=["F1"]),
            view("VIEW02", 100, 0, 120, 40, member_ids=["S1"]),
        ],
    )
    alignments = projection.aligned_views(sheet)
    assert [a.label for a in alignments] == ["Y"]


def test_views_that_merely_sit_near_each_other_are_not_aligned():
    """A view offset sideways shares no projected extent - no pair."""
    sheet = make_sheet(
        features=[outline("F1", 0, 0, 80, 40), outline("D1", 90, -70, 110, -50)],
        views=[
            view("VIEW01", 0, 0, 80, 40, member_ids=["F1"]),
            view("VIEW02", 90, -70, 110, -50, member_ids=["D1"]),
        ],
    )
    assert projection.aligned_views(sheet) == []


def test_detail_views_are_left_out():
    sheet = stacked_sheet()
    sheet.views[1].is_detail = True
    assert projection.aligned_views(sheet) == []


def test_size_bands_separate_an_edit_from_a_different_view():
    assert projection.aligned_views(stacked_sheet(80.0))[0].same_size
    suspicious = projection.aligned_views(stacked_sheet(76.0))[0]
    assert suspicious.suspicious and suspicious.comparable and not suspicious.same_size
    scaled = projection.aligned_views(stacked_sheet(40.0))[0]  # a 2:1 fragment
    assert not scaled.comparable and not scaled.suspicious


def test_overall_dimension_is_the_one_that_spans_the_view():
    sheet = stacked_sheet()
    sheet.dimensions = [linear("DIM1", 0.0, 0.0, 80.0), linear("DIM2", 0.0, 0.0, 12.0)]
    sheet.views[0].member_ids.extend(["DIM1", "DIM2"])
    found = projection.overall_dimensions(sheet, sheet.views[0], projection.AXIS_X, 0.05)
    assert [d.id for d in found] == ["DIM1"]


def test_an_axis_is_inherited_from_the_aligned_view_that_fixes_it():
    """The top view may stay silent on X: the front view dimensions it."""
    sheet = stacked_sheet()
    sheet.dimensions = [linear("DIM1", 0.0, 0.0, 80.0)]
    sheet.views[0].member_ids.append("DIM1")
    inherited = projection.inherited_axes(sheet, sheet_coverage(sheet), AuditConfig())
    assert inherited == {"VIEW02": {"X"}}


def test_nothing_is_inherited_from_a_view_that_is_itself_incomplete():
    """Otherwise a real gap would vanish between two views."""
    sheet = stacked_sheet()
    sheet.features.append(make_circle("C1", x=40, y=20, r=3))
    sheet.views[0].member_ids.append("C1")
    inherited = projection.inherited_axes(sheet, sheet_coverage(sheet), AuditConfig())
    assert inherited == {}


def test_a_view_at_another_scale_passes_nothing_on():
    sheet = stacked_sheet(40.0)
    sheet.dimensions = [linear("DIM1", 0.0, 0.0, 80.0)]
    sheet.views[0].member_ids.append("DIM1")
    assert projection.inherited_axes(sheet, sheet_coverage(sheet), AuditConfig()) == {}
