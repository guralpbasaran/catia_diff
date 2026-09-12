"""View segmentation."""

from conftest import box, make_circle, make_dimension, make_sheet

from catia_diff.config import AuditConfig
from catia_diff.models.drawing import GeometryFeature, GeometryKind, View
from catia_diff.models.geometry import BBox, Point2D
from catia_diff.rules.views import segment_views


def rect(feature_id, x0, y0, width=40.0, height=20.0):
    pts = [
        Point2D(x=x0, y=y0),
        Point2D(x=x0 + width, y=y0),
        Point2D(x=x0 + width, y=y0 + height),
        Point2D(x=x0, y=y0 + height),
        Point2D(x=x0, y=y0),
    ]
    return GeometryFeature(
        id=feature_id, kind=GeometryKind.POLYLINE, points=pts, bbox=BBox.from_points(pts)
    )


def test_single_view_collects_its_callouts():
    sheet = make_sheet(
        features=[rect("OUT1", 0, 0), make_circle("F1", x=10, y=10)],
        dimensions=[make_dimension("D1", bbox=box(0, -12))],
    )
    views = segment_views(sheet, AuditConfig())
    assert len(views) == 1
    assert set(views[0].member_ids) == {"OUT1", "F1", "D1"}
    assert sheet.find_object("D1").view_id == views[0].id
    assert sheet.objects_of_view(views[0].id)


def test_distant_geometry_becomes_a_second_view():
    sheet = make_sheet(features=[rect("OUT1", 0, 0), rect("OUT2", 150, 90)])
    views = segment_views(sheet, AuditConfig())
    assert len(views) == 2
    assert {len(view.member_ids) for view in views} == {1}


def test_lone_geometry_in_the_title_block_corner_is_not_a_view():
    """The bottom-right corner holds the title block frame, not a projection."""
    sheet = make_sheet(features=[rect("OUT1", 0, 0), rect("FRAME", 150, 5, 45, 30)])
    views = segment_views(sheet, AuditConfig())
    assert [view.member_ids for view in views] == [["OUT1"]]


def test_touching_geometry_stays_one_view():
    sheet = make_sheet(features=[rect("OUT1", 0, 0), rect("OUT2", 41, 0)])
    assert len(segment_views(sheet, AuditConfig())) == 1


def test_dimension_is_attached_to_the_nearest_view():
    sheet = make_sheet(
        features=[rect("OUT1", 0, 0), rect("OUT2", 150, 90)],
        dimensions=[make_dimension("D1", bbox=box(150, 85, 40, 3))],
    )
    views = segment_views(sheet, AuditConfig())
    owner = next(view for view in views if "D1" in view.member_ids)
    assert "OUT2" in owner.member_ids


def test_caption_is_transferred_to_the_geometric_view():
    sheet = make_sheet(
        features=[rect("OUT1", 0, 0)],
        views=[View(id="LABEL1", label="SECTION A-A", bbox=box(5, 25, 30, 5))],
    )
    views = segment_views(sheet, AuditConfig())
    assert len(views) == 1
    assert views[0].label == "SECTION A-A"
    assert views[0].is_section
    assert views[0].is_geometric


def test_sheet_without_geometry_keeps_caption_views():
    sheet = make_sheet(views=[View(id="LABEL1", label="DETAIL B", bbox=box(0, 0))])
    views = segment_views(sheet, AuditConfig())
    assert [view.id for view in views] == ["LABEL1"]


def test_views_are_ordered_for_reading():
    # y_up sheet: the upper view comes first
    sheet = make_sheet(features=[rect("LOW", 0, 0), rect("HIGH", 0, 100)])
    views = segment_views(sheet, AuditConfig())
    assert views[0].member_ids == ["HIGH"]
    assert views[1].member_ids == ["LOW"]
