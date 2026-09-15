"""Reference integrity: every pointer on the drawing must land on something."""

from __future__ import annotations

import pytest

from catia_diff.config import AuditConfig
from catia_diff.extract.text_parsing import (
    declared_notes,
    parse_bubble_label,
    parse_marker_label,
    parse_view_caption,
    referenced_notes,
    referenced_sheets,
    view_references,
)
from catia_diff.models.drawing import Annotation, GeometryFeature, GeometryKind, Sheet, View
from catia_diff.models.findings import Severity
from catia_diff.models.geometry import BBox, Point2D
from catia_diff.rules.base import rules_for, run_rules
from catia_diff.rules.markers import DETAIL, SECTION, detect_markers
from tests.conftest import box, make_context, make_document, make_sheet


# ---------------------------------------------------------------------------
# grammar
@pytest.mark.parametrize(
    ("text", "kind", "label", "scale"),
    [
        ("KESİT A-A", "section", "A-A", None),
        ("SECTION A-A (2:1)", "section", "A-A", "2:1"),
        ("SECTION B-B SCALE 2:1", "section", "B-B", "2:1"),
        ("KESIT A", "section", "A-A", None),  # one letter, same reference
        ("DETAY B", "detail", "B", None),
        ("DETAIL C 2:1", "detail", "C", "2:1"),
        ("GÖRÜNÜŞ D", "view", "D", None),
    ],
)
def test_a_title_is_read_with_its_kind_letter_and_scale(text, kind, label, scale):
    parsed = parse_view_caption(text)
    assert parsed is not None
    assert (parsed.kind, parsed.label, parsed.scale) == (kind, label, scale)


@pytest.mark.parametrize(
    "text",
    ["DETAY C'YE BAKINIZ", "BKZ KESİT A-A", "SEE DETAIL D FOR THE GROOVE", "NOTLAR:", ""],
)
def test_prose_that_mentions_a_view_is_not_a_title(text):
    """The extractor files any text containing 'DETAY' as a view; only a title is one."""
    assert parse_view_caption(text) is None


def test_a_mention_is_read_as_a_reference():
    assert view_references("KANAL İÇİN DETAY C'YE BAKINIZ") == {("detail", "C")}
    assert view_references("SEE SECTION B-B AND DETAIL D") == {("section", "B-B"), ("detail", "D")}
    assert view_references("KESİT A-A") == set()  # a title points at nothing


def test_marker_letters():
    assert parse_marker_label("A-A") == "A-A"
    assert parse_marker_label("A") is None  # indistinguishable from a datum
    assert parse_bubble_label("B") == "B"
    assert parse_bubble_label("A-A") is None


def test_notes_are_counted_and_resolved():
    notes = "NOTLAR:\n1. KESKİN KÖŞELER KIRILACAK.\n2. ÇAPAK ALINACAK.\n3. BOYA RAL 7016."
    assert declared_notes(notes) == {1, 2, 3}
    assert referenced_notes("YÜZEY İŞLEMİ İÇİN BKZ NOT 3") == {3}
    assert referenced_notes("SEE NOTE 12") == {12}
    assert referenced_notes("hiçbir gönderme yok") == set()


def test_a_sheet_number_in_the_title_block_is_not_a_reference():
    assert referenced_sheets("MONTAJ İÇİN SAYFA 3") == {3}
    assert referenced_sheets("SAYFA 1/2") == set()


# ---------------------------------------------------------------------------
# markers
def line(feature_id: str, layer: str, x: float, y0: float, y1: float) -> GeometryFeature:
    return GeometryFeature(
        id=feature_id,
        kind=GeometryKind.LINE,
        layer=layer,
        bbox=BBox(x0=x, y0=y0, x1=x, y1=y1),
        points=[Point2D(x=x, y=y0), Point2D(x=x, y=y1)],
    )


def circle(feature_id: str, layer: str, x: float, y: float, r: float = 8.0) -> GeometryFeature:
    return GeometryFeature(
        id=feature_id,
        kind=GeometryKind.CIRCLE,
        layer=layer,
        center=Point2D(x=x, y=y),
        radius=r,
        bbox=BBox(x0=x - r, y0=y - r, x1=x + r, y1=y + r),
    )


def note(text: str, x: float = 0.0, y: float = 0.0, note_id: str = "NOTE0001") -> Annotation:
    return Annotation(id=note_id, text=text, bbox=box(x, y, 6, 3))


def test_a_letter_pair_is_a_cutting_plane_even_without_its_line():
    """Nothing else on a drawing is written 'A-A'; the line only corroborates it."""
    sheet = make_sheet(annotations=[note("A-A", 40, 44)])
    markers = detect_markers(sheet)
    assert [(m.kind, m.label) for m in markers] == [(SECTION, "A-A")]
    assert markers[0].confidence == 0.8


def test_a_cutting_plane_line_raises_the_confidence():
    sheet = make_sheet(
        annotations=[note("A-A", 40, 44)],
        features=[line("F1", "PHANTOM", 40, -8, 48)],
    )
    marker = detect_markers(sheet)[0]
    assert marker.confidence == 1.0
    assert "F1" in marker.object_ids


def test_a_bare_letter_needs_its_bubble():
    """A lone letter is also how a datum symbol is written, so geometry decides."""
    without = make_sheet(annotations=[note("B", 30, 30)])
    assert detect_markers(without) == []

    with_bubble = make_sheet(
        annotations=[note("B", 30, 30)],
        features=[circle("F1", "ANNOT", 26, 30)],
    )
    markers = detect_markers(with_bubble)
    assert [(m.kind, m.label) for m in markers] == [(DETAIL, "B")]


def test_a_letter_on_a_part_circle_is_not_a_detail_bubble():
    sheet = make_sheet(
        annotations=[note("B", 30, 30)],
        features=[circle("F1", "PART", 26, 30)],
    )
    assert detect_markers(sheet) == []


# ---------------------------------------------------------------------------
# rules
def run(sheet: Sheet, *sheets: Sheet):
    document = make_document(sheet)
    document.sheets.extend(sheets)
    for index, member in enumerate(document.sheets):
        member.index = index
    ctx = make_context(document)
    rules = [rule for rule in rules_for(AuditConfig(language="en")) if rule.meta.id.startswith("REF")]
    return list(run_rules(rules, document, ctx))


def caption_view(label: str, view_id: str = "VIEW01", *, geometric: bool = True) -> View:
    return View(
        id=view_id,
        label=label,
        bbox=box(0, 0, 40, 30),
        member_ids=["F1"] if geometric else [],
    )


def test_ref001_a_cut_that_leads_nowhere():
    sheet = make_sheet(annotations=[note("A-A", 40, 44)])
    findings = run(sheet)
    assert [f.rule_id for f in findings] == ["REF001"]
    assert findings[0].severity is Severity.CRITICAL
    assert "A-A" in findings[0].message


def test_ref001_is_satisfied_by_a_view_on_another_sheet():
    """A section is often cut on one sheet and drawn on the next."""
    first = make_sheet(annotations=[note("A-A", 40, 44)])
    second = make_sheet(index=1, views=[caption_view("KESİT A-A")])
    assert [f.rule_id for f in run(first, second)] == []


def test_ref002_a_view_nobody_says_where_to_cut():
    sheet = make_sheet(
        annotations=[note("A-A", 40, 44)],  # a marker exists, so the check applies
        views=[caption_view("KESİT A-A"), caption_view("KESİT C-C", "VIEW02")],
    )
    findings = [f for f in run(sheet) if f.rule_id == "REF002"]
    assert len(findings) == 1
    assert "C-C" in findings[0].message


def test_ref002_stays_silent_when_no_marker_could_be_found_at_all():
    """Out of the detector's reach is not the same as missing."""
    sheet = make_sheet(views=[caption_view("KESİT C-C")])
    assert [f.rule_id for f in run(sheet) if f.rule_id == "REF002"] == []


def test_ref003_the_same_letter_twice():
    sheet = make_sheet(views=[caption_view("KESİT A-A"), caption_view("KESİT A-A", "VIEW02")])
    findings = [f for f in run(sheet) if f.rule_id == "REF003"]
    assert len(findings) == 1
    assert set(findings[0].evidence.object_ids) == {"VIEW01", "VIEW02"}


def test_ref004_a_note_that_was_never_written(annotation_factory):
    sheet = make_sheet(
        annotations=[note("NOTLAR:\n1. ÇAPAK ALINACAK.\n2. BOYA RAL 7016."), note("BKZ NOT 7", note_id="NOTE0002")]
    )
    findings = [f for f in run(sheet) if f.rule_id == "REF004"]
    assert len(findings) == 1
    assert "7" in findings[0].message


def test_ref004_resolves_a_note_written_on_another_sheet():
    first = make_sheet(annotations=[note("BKZ NOT 2")])
    second = make_sheet(index=1, annotations=[note("1. A.\n2. B.", note_id="NOTE0002")])
    assert [f.rule_id for f in run(first, second) if f.rule_id == "REF004"] == []


def test_ref005_a_sheet_that_does_not_exist():
    sheet = make_sheet(annotations=[note("MONTAJ İÇİN SAYFA 3")])
    findings = [f for f in run(sheet) if f.rule_id == "REF005"]
    assert len(findings) == 1


def test_ref005_trusts_the_title_block_about_the_set():
    """A drawing split across files still points at its own sheets."""
    from tests.conftest import titled

    sheet = make_sheet(annotations=[note("MONTAJ İÇİN SAYFA 3")], title_block=titled(sheet="1 / 3"))
    assert [f.rule_id for f in run(sheet) if f.rule_id == "REF005"] == []


def test_ref006_text_points_at_a_view_that_is_not_drawn():
    sheet = make_sheet(annotations=[note("KANAL İÇİN DETAY C'YE BAKINIZ")])
    findings = [f for f in run(sheet) if f.rule_id == "REF006"]
    assert len(findings) == 1
    assert "C" in findings[0].message


def test_ref006_is_silent_when_the_view_exists():
    sheet = make_sheet(
        annotations=[note("KANAL İÇİN DETAY C'YE BAKINIZ")],
        views=[caption_view("DETAY C")],
    )
    assert [f.rule_id for f in run(sheet) if f.rule_id == "REF006"] == []


def test_ref007_a_title_that_titles_nothing():
    sheet = make_sheet(views=[caption_view("KESİT B-B", geometric=False)])
    findings = [f for f in run(sheet) if f.rule_id == "REF007"]
    assert len(findings) == 1
    assert findings[0].severity is Severity.MINOR


def test_an_orphan_title_is_not_also_reported_as_unmarked():
    """One defect, one finding: REF007 owns a title that belongs to no view."""
    sheet = make_sheet(
        annotations=[note("A-A", 40, 44)],
        views=[caption_view("KESİT B-B", geometric=False)],
    )
    assert sorted({f.rule_id for f in run(sheet)}) == ["REF001", "REF007"]


# ---------------------------------------------------------------------------
# on the real sample drawings
def audit(path, tmp_path):
    from catia_diff import AuditConfig as Config
    from catia_diff import audit_file

    return audit_file(
        path, Config(language="tr", formats=(), render_overlay=False, output_dir=tmp_path)
    )


def test_the_reference_sample_breaks_five_references(sample_dxf_refs, tmp_path):
    report = audit(sample_dxf_refs, tmp_path)
    found = {f.rule_id for f in report.findings}
    assert {"REF001", "REF004", "REF005", "REF006", "REF007"} <= found


def test_the_corrected_reference_sample_is_silent(sample_dxf_refs_correct, tmp_path):
    """Nothing in the family - and nothing from the coverage rules either.

    The cutting plane crosses the view it is drawn on; counting it as geometry
    would make the view look under-dimensioned and its section look mismatched.
    """
    report = audit(sample_dxf_refs_correct, tmp_path)
    noisy = [
        f.rule_id
        for f in report.findings
        if f.rule_id.startswith(("REF", "CRV")) or f.rule_id in {"DIM011", "DIM012", "DIM014"}
    ]
    assert noisy == []


def test_a_cutting_plane_is_not_a_coordinate_to_dimension(sample_dxf_refs_correct):
    """Regression: the phantom line at x=40 must produce no reference node."""
    from catia_diff.config import AuditConfig as Config
    from catia_diff.extract.registry import get_extractor
    from catia_diff.rules.constraints import sheet_coverage
    from catia_diff.rules.views import segment_views

    config = Config()
    document = get_extractor(sample_dxf_refs_correct, config).extract(
        sample_dxf_refs_correct, config
    )
    sheet = document.sheets[0]
    segment_views(sheet, config)
    main = next(view for view, _ in sheet_coverage(sheet, config) if view.bbox.x0 < 50)
    x_axis = next(
        axis for view, axes in sheet_coverage(sheet, config) if view.id == main.id
        for axis in axes if axis.label == "X"
    )
    assert 40.0 not in [round(node.coordinate, 1) for node in x_axis.nodes]
    assert x_axis.missing == 0
