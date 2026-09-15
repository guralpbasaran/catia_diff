"""Recovering a drawing from a PDF page: strokes in, constrained geometry out.

A vector PDF states none of what the checkers need.  There is no dimension
entity, so the interval a number measures has to be read off the stroke it is
written against; there are no units, so the scale has to come from the drawing's
own callouts; and there is no layer telling an extension line apart from an edge
of the part, so the dimension lines have to be claimed before what is left can
be called geometry.  These tests pin all three, and then check that the page
produces the same findings the DXF of the same plate does.
"""

from __future__ import annotations

import math

import pytest

from catia_diff.extract import pdf_vector
from catia_diff.extract.pdf_vector import Segment
from catia_diff.models.geometry import Point2D
from tests.conftest import box, make_dimension

pymupdf = pytest.importorskip("pymupdf")


def point(x: float, y: float) -> Point2D:
    return Point2D(x=x, y=y)


def segment(x0: float, y0: float, x1: float, y1: float, dashed: bool = False) -> Segment:
    return Segment(start=point(x0, y0), end=point(x1, y1), dashed=dashed)


# ---------------------------------------------------------------------------
# strokes
def test_a_segment_knows_which_axis_it_runs_along():
    assert segment(0, 0, 10, 0).axis == 0.0
    assert segment(0, 0, 0, 10).axis == 90.0
    assert segment(0, 0, 10, 10).axis is None
    # A plotter's rounding is not a slope.
    assert segment(0, 0, 100, 0.5).axis == 0.0


def test_four_beziers_in_a_square_box_are_a_circle():
    doc = pymupdf.open()
    page = doc.new_page()
    shape = page.new_shape()
    shape.draw_circle(pymupdf.Point(100, 100), 20)
    shape.finish(width=0.5)
    shape.commit()

    _, circles = pdf_vector.flatten(page.get_drawings())
    assert len(circles) == 1
    assert circles[0].radius == pytest.approx(20, abs=0.5)
    assert circles[0].center.x == pytest.approx(100, abs=0.5)


def test_a_rectangle_path_becomes_four_strokes():
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(10, 10, 60, 40), width=0.5)
    segments, _ = pdf_vector.flatten(page.get_drawings())
    assert len(segments) == 4
    assert sorted(round(item.length) for item in segments) == [30, 30, 50, 50]


# ---------------------------------------------------------------------------
# claiming
def dimension_page(distance: float = 60.0):
    """One horizontal dimension: extension lines, arrows, line, number."""
    segments = [
        segment(100, 200, 100, 240),  # extension line, left
        segment(100 + distance, 200, 100 + distance, 240),  # extension line, right
        segment(100, 238, 100 + distance, 238),  # the dimension line itself
        segment(100, 238, 103, 236),  # arrow head
        segment(100 + distance, 238, 100 + distance - 3, 236),  # arrow head
    ]
    return segments


def test_a_dimension_claims_its_line_and_everything_attending_it():
    segments = dimension_page()
    dim = make_dimension(nominal=20.0, bbox=box(120, 230, w=16, h=8))
    matches = pdf_vector.claim_dimension_lines([dim], segments)

    assert len(matches) == 1
    assert matches[0].axis == 0.0
    assert matches[0].span == pytest.approx(60.0)
    # All five strokes belong to the dimension - none of them is part geometry.
    assert matches[0].consumed == {0, 1, 2, 3, 4}


def test_what_the_dimensions_did_not_claim_is_the_part():
    segments = [*dimension_page(), segment(100, 100, 160, 100)]  # an edge
    dim = make_dimension(nominal=20.0, bbox=box(120, 230, w=16, h=8))
    matches = pdf_vector.claim_dimension_lines([dim], segments)
    claimed = set().union(*(match.consumed for match in matches))

    features = pdf_vector.features_from(segments, [], claimed, lambda: "F1")
    assert len(features) == 1
    assert features[0].points[0].y == 100


def test_a_number_with_no_line_near_it_is_not_measured():
    """A leader note - '4x Ø6.5' - has no dimension line, and must not borrow one."""
    dim = make_dimension(nominal=6.5, bbox=box(10, 10, w=20, h=8))
    assert pdf_vector.claim_dimension_lines([dim], dimension_page()) == []


def test_a_dashed_stroke_is_filed_as_a_centre_line():
    features = pdf_vector.features_from([segment(0, 0, 10, 0, dashed=True)], [], set(), lambda: "F")
    assert features[0].layer == "CENTER"


# ---------------------------------------------------------------------------
# calibration
def matched(nominal: float, span: float) -> pdf_vector.Match:
    return pdf_vector.Match(
        dimension=make_dimension(nominal=nominal), axis=0.0, interval=(0.0, span)
    )


def test_the_scale_comes_from_the_drawings_own_callouts():
    matches = [matched(80.0, 240.0), matched(40.0, 120.0), matched(12.0, 36.0)]
    assert pdf_vector.calibrate(matches) == pytest.approx(1 / 3)


def test_one_wrong_callout_cannot_move_the_scale():
    """A median is the point: an overridden number is an outlier, not a unit."""
    matches = [matched(80.0, 240.0), matched(40.0, 120.0), matched(25.0, 120.0)]
    assert pdf_vector.calibrate(matches) == pytest.approx(1 / 3)


def test_too_few_samples_means_no_scale_rather_than_a_guessed_one():
    assert pdf_vector.calibrate([matched(80.0, 240.0)]) is None
    assert pdf_vector.calibrate([]) is None


def test_a_callout_that_disagrees_with_its_line_is_marked_overridden():
    good, bad = matched(80.0, 240.0), matched(25.0, 90.0)
    pdf_vector.apply_matches([good, bad], 1 / 3)

    assert good.dimension.measured == pytest.approx(80.0)
    assert good.dimension.is_text_override is False
    assert bad.dimension.measured == pytest.approx(30.0)
    assert bad.dimension.is_text_override is True


# ---------------------------------------------------------------------------
# on the sample page
def extract(path):
    from catia_diff.config import AuditConfig
    from catia_diff.extract.pdf_extractor import PdfExtractor

    return PdfExtractor().extract(path, AuditConfig()).sheets[0]


def audit(path, tmp_path):
    from catia_diff import AuditConfig as Config
    from catia_diff import audit_file

    return audit_file(
        path, Config(language="tr", formats=(), render_overlay=False, output_dir=tmp_path)
    )


def test_the_sample_page_is_recovered_in_millimetres(sample_pdf):
    sheet = extract(sample_pdf)
    # Drawn at three points per millimetre, and nothing told the extractor so.
    assert float(sheet.metadata["mm_per_point"]) == pytest.approx(1 / 3, rel=1e-6)
    assert sheet.units.value == "mm"
    assert sheet.metadata["dimensions_matched"] == "5/6"


def test_the_part_is_four_edges_and_four_holes_once_the_dimensions_are_claimed(sample_pdf):
    sheet = extract(sample_pdf)
    circles = [f for f in sheet.features if f.kind.value == "circle"]
    lines = [f for f in sheet.features if f.kind.value == "line"]

    assert len(circles) == 4
    assert len(lines) == 4  # the outline; no extension line survived as an edge
    assert all(math.isclose(f.radius, 3.25, rel_tol=0.02) for f in circles)


def test_the_title_block_frame_is_not_a_view(sample_pdf):
    """Its rectangle would otherwise cluster into a view with no dimensions."""
    for feature in extract(sample_pdf).features:
        assert feature.bbox.x0 < 330 or feature.bbox.y0 < 690


def test_every_measured_dimension_agrees_with_its_line(sample_pdf):
    for dimension in extract(sample_pdf).dimensions:
        if dimension.measured is not None:
            assert dimension.measured == pytest.approx(dimension.nominal, rel=0.01)


def test_a_thickness_note_stays_a_note_on_a_pdf_page(sample_pdf):
    """'KALINLIK 5' is what DIM016 reads; as a dimension it would be a 5 mm size."""
    sheet = extract(sample_pdf)
    assert any("KALINLIK" in a.text for a in sheet.annotations)
    assert all(d.nominal != 5.0 for d in sheet.dimensions)


def test_the_pdf_finds_what_the_dxf_of_the_same_plate_finds(sample_pdf, tmp_path):
    findings = audit(sample_pdf, tmp_path).findings
    unlocated = [f for f in findings if f.rule_id == "DIM011"]

    assert len(unlocated) == 2  # the two hole rows, neither located on Y
    assert all("Y" in f.message_tr for f in unlocated)
    assert {"DIM003", "TOL010"} <= {f.rule_id for f in findings}


def test_the_dimensioned_page_is_silent(sample_pdf_complete, tmp_path):
    """The false-positive net: same strokes, arranged correctly."""
    found = {f.rule_id for f in audit(sample_pdf_complete, tmp_path).findings}
    assert not found & {"DIM001", "DIM003", "DIM011", "DIM012", "DIM013", "DIM016", "TOL010"}
