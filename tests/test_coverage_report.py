"""What the report says about coverage, and what it must not say.

The overlay, the HTML table and the document stats all answer "is this drawing
fully dimensioned?".  They read one summary so they cannot drift apart, and that
summary is filtered by the findings so it can never claim a gap the audit chose
not to raise - a report contradicting itself is worse than a report saying less.
"""

from __future__ import annotations

import pytest

from catia_diff.models.findings import (
    SEVERITY_COLORS,
    AxisCoverage,
    CoverageGap,
    Severity,
    ViewCoverage,
)
from catia_diff.reporting.coverage import (
    coverage_rows,
    describe_axis,
    summarize_coverage,
    unconstrained_axes,
)
from tests.conftest import make_circle, make_dimension, make_document, make_sheet


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def gap(coordinate: float = 10.0, **kwargs) -> CoverageGap:
    kwargs.setdefault("anchor", 0.0)
    kwargs.setdefault("feature_ids", ("FEAT0002",))
    return CoverageGap(coordinate=coordinate, **kwargs)


def audit(path, tmp_path):
    from catia_diff import AuditConfig as Config
    from catia_diff import audit_file

    return audit_file(
        path, Config(language="tr", formats=(), render_overlay=False, output_dir=tmp_path)
    )


# ---------------------------------------------------------------------------
# the phrases
def test_an_axis_states_what_is_wrong_with_it():
    assert describe_axis(AxisCoverage(axis="Y", gaps=(gap(),)), "tr") == "Y: 1 eksik"
    assert describe_axis(AxisCoverage(axis="X", redundant=1), "tr") == "X: 1 fazla"
    assert describe_axis(AxisCoverage(axis="X"), "tr") == "X ✔"
    assert describe_axis(AxisCoverage(axis="Y", gaps=(gap(),)), "en") == "Y: 1 missing"


def test_an_inherited_axis_is_complete_and_says_where_from():
    axis = AxisCoverage(axis="Y", inherited=True)
    assert axis.is_complete
    assert describe_axis(axis, "tr") == "Y ✔ (komşu görünüşten)"
    assert describe_axis(axis, "en") == "Y ✔ (from an aligned view)"


def test_a_view_without_a_label_is_numbered_in_both_languages():
    view = ViewCoverage(view_id="VIEW02")
    assert view.name("tr") == "Görünüş 2"
    assert view.name("en") == "View 2"


def test_a_labelled_view_keeps_its_own_name():
    assert ViewCoverage(view_id="VIEW02", label="KESIT A-A").name("tr") == "KESIT A-A"


def test_rows_join_every_axis_of_a_view():
    view = ViewCoverage(
        view_id="VIEW01",
        axes=(AxisCoverage(axis="X", redundant=1), AxisCoverage(axis="Y", gaps=(gap(),))),
    )
    assert coverage_rows([view], "tr") == [("Görünüş 1", "X: 1 fazla · Y: 1 eksik", False)]


def test_only_missing_dimensions_count_as_unconstrained_axes():
    """A redundant dimension is a different defect; it does not leave an axis free."""
    redundant = ViewCoverage(view_id="V1", axes=(AxisCoverage(axis="X", redundant=2),))
    missing = ViewCoverage(view_id="V2", axes=(AxisCoverage(axis="Y", gaps=(gap(),)),))
    assert unconstrained_axes([redundant]) == 0
    assert unconstrained_axes([redundant, missing]) == 1


# ---------------------------------------------------------------------------
# on the real drawings
def test_the_sample_plate_reports_its_one_real_gap(sample_dxf, tmp_path):
    report = audit(sample_dxf, tmp_path)
    assert report.document_stats["unconstrained_axes"] == 1
    assert coverage_rows(report.coverage, "tr") == [("Görünüş 1", "X: 1 fazla · Y: 1 eksik", False)]


def test_the_gap_knows_where_the_missing_dimension_would_run(sample_dxf, tmp_path):
    report = audit(sample_dxf, tmp_path)
    (found,) = [a for v in report.coverage for a in v.axes if a.gaps]
    (hole_row,) = found.gaps
    # From the bottom edge the drawing does control, up to the hole row it does not.
    assert hole_row.anchor == pytest.approx(0.0)
    assert hole_row.coordinate == pytest.approx(10.0)
    assert hole_row.bbox is not None


def test_an_inherited_axis_is_not_reported_as_a_gap(sample_dxf_views, tmp_path):
    """The three-view sample leaves axes free that an aligned view already fixes.

    The graph sees them; the rules stay silent about them; the report must side
    with the rules, or it prints gaps nobody was told to fix.
    """
    report = audit(sample_dxf_views, tmp_path)
    inherited = [a for v in report.coverage for a in v.axes if a.inherited]

    assert inherited, "the sample is built so that two axes are inherited"
    assert all(not axis.gaps for axis in inherited)
    assert report.document_stats["unconstrained_axes"] == 0


def test_the_summary_never_claims_a_gap_the_findings_do_not(sample_dxf, tmp_path):
    report = audit(sample_dxf, tmp_path)
    raised = {
        object_id
        for finding in report.findings
        if finding.rule_id in {"DIM011", "DIM012"}
        for object_id in finding.evidence.object_ids
    }
    for view in report.coverage:
        for axis in view.axes:
            for hole in axis.gaps:
                assert raised.intersection(hole.feature_ids)


def test_a_source_without_interval_data_says_nothing():
    """No graph, no claim - the same discipline the rules follow.

    A scan gives text and boxes but never says what a dimension measured, so
    the constraint graph cannot run and the report stays quiet about coverage.
    """
    sheet = make_sheet(features=[make_circle()], dimensions=[make_dimension()])
    assert summarize_coverage(make_document(sheet), []) == []


def test_the_reports_carry_the_table(sample_dxf, tmp_path):
    from catia_diff.reporting.render import to_html, to_markdown

    report = audit(sample_dxf, tmp_path)
    assert "Ölçülendirme kapsamı" in to_markdown(report, "tr")
    assert "| Görünüş 1 | X: 1 fazla · Y: 1 eksik |" in to_markdown(report, "tr")
    assert "Dimensional coverage" in to_markdown(report, "en")
    assert "Ölçülendirme kapsamı" in to_html(report, "tr")


# ---------------------------------------------------------------------------
# what the overlay draws
def test_the_overlay_draws_the_missing_dimension(sample_dxf, tmp_path):
    """The dashed guide is the point of the whole feature: it shows what to draw."""
    pytest.importorskip("PIL")
    from catia_diff import AuditConfig as Config
    from catia_diff import audit_file

    report = audit_file(
        sample_dxf,
        Config(language="tr", formats=(), render_overlay=True, output_dir=tmp_path),
    )
    assert report.overlays, "the sample has findings, so a sheet should be marked up"

    from PIL import Image

    image = Image.open(report.overlays[0]).convert("RGB")
    critical = _hex_to_rgb(SEVERITY_COLORS[Severity.CRITICAL])
    # The guide runs along X = the hole row's centre, between y = 0 and y = 10.
    column = [image.getpixel((image.width // 2, y)) for y in range(image.height)]
    assert sum(1 for pixel in column if pixel == critical) > 10


def test_a_font_without_turkish_glyphs_degrades_to_ascii_not_to_boxes():
    """Pillow's bundled face has no 'ö'; a transliteration beats a .notdef box."""
    from catia_diff.reporting import overlay

    assert overlay._label("Majör") in {"Majör", "Major"}
    assert "�" not in overlay._label("eksik ölçü")


def test_the_guide_needs_both_ends_before_it_is_drawn():
    """Without an anchor there is nothing to measure from, so nothing is drawn."""
    from catia_diff.models.geometry import BBox
    from catia_diff.reporting import overlay

    sheet = make_sheet()  # 200 x 140, y-up
    canvas = (int(sheet.width), int(sheet.height))
    box = BBox(x0=20, y0=8, x1=24, y1=12)

    loose = CoverageGap(coordinate=10.0, anchor=None, bbox=box)
    assert overlay._gap_ends(loose, "Y", sheet, 1.0, canvas) is None
    assert overlay._gap_ends(CoverageGap(coordinate=10.0, anchor=0.0), "Y", sheet, 1.0, canvas) is None

    ends = overlay._gap_ends(gap(bbox=box), "Y", sheet, 1.0, canvas)
    assert ends is not None
    (x0, y0), (x1, y1) = ends
    assert x0 == x1 == 22  # drawn at the free geometry's centre
    # y-up flipped: the anchor at y=0 lands on the last row, the hole at y=10 ten above
    assert {y0, y1} == {canvas[1] - 1, 130}
