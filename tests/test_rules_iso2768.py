"""Rules that turn the ISO 2768 tables into findings."""

import pytest
from conftest import box, make_context, make_dimension, make_document, make_gtol, make_sheet, titled

from catia_diff.models.drawing import (
    Annotation,
    DimensionKind,
    GDTCharacteristic,
    Tolerance,
    ToleranceKind,
    Units,
)
from catia_diff.models.findings import Severity
from catia_diff.rules.base import get_rule


def run(rule_id: str, sheet, config=None):
    document = make_document(sheet)
    ctx = make_context(document, config)
    rule = get_rule(rule_id)
    target = document if rule.scope == "document" else sheet
    return list(rule.check(target, ctx))


def note(text: str) -> Annotation:
    return Annotation(id="N1", text=text, category="general_tolerance", bbox=box(0, 100))


def symmetric(value: float) -> Tolerance:
    return Tolerance(kind=ToleranceKind.SYMMETRIC, upper=value, lower=-value)


# --------------------------------------------------------------------- TOL006
def test_tol006_note_without_a_class():
    sheet = make_sheet(dimensions=[make_dimension("DIM1")], annotations=[note("ISO 2768")])
    findings = run("TOL006", sheet)
    assert len(findings) == 1
    assert findings[0].severity is Severity.MAJOR
    assert "no tolerance class" in findings[0].message


def test_tol006_unknown_class_letter():
    sheet = make_sheet(dimensions=[make_dimension("DIM1")], annotations=[note("ISO 2768-mX")])
    findings = run("TOL006", sheet)
    assert len(findings) == 1
    assert findings[0].severity is Severity.MINOR
    assert "X" in findings[0].message


def test_tol006_quiet_for_a_complete_designation():
    sheet = make_sheet(dimensions=[make_dimension("DIM1")], annotations=[note("ISO 2768-mK")])
    assert run("TOL006", sheet) == []


def test_tol006_quiet_without_any_note():
    assert run("TOL006", make_sheet(dimensions=[make_dimension("DIM1")])) == []


# --------------------------------------------------------------------- TOL007
@pytest.mark.parametrize("nominal", [0.3, 5000.0])
def test_tol007_size_outside_the_table(nominal):
    sheet = make_sheet(
        dimensions=[make_dimension("DIM1", nominal=nominal)], annotations=[note("ISO 2768-m")]
    )
    findings = run("TOL007", sheet)
    assert len(findings) == 1
    assert findings[0].severity is Severity.MAJOR
    assert "not covered" in findings[0].message


def test_tol007_class_specific_gap():
    # class f has no row above 2000 mm, class m has
    fine = make_sheet(
        dimensions=[make_dimension("DIM1", nominal=3000.0)], annotations=[note("ISO 2768-f")]
    )
    assert len(run("TOL007", fine)) == 1

    medium = make_sheet(
        dimensions=[make_dimension("DIM1", nominal=3000.0)], annotations=[note("ISO 2768-m")]
    )
    assert run("TOL007", medium) == []


def test_tol007_skips_toleranced_and_angular_dimensions():
    sheet = make_sheet(
        dimensions=[
            make_dimension("DIM1", nominal=0.3, tolerance=symmetric(0.02)),
            make_dimension("DIM2", nominal=30.0, kind=DimensionKind.ANGULAR, units=Units.DEG),
        ],
        annotations=[note("ISO 2768-m")],
    )
    assert run("TOL007", sheet) == []


# --------------------------------------------------------------------- TOL008
def test_tol008_explicit_tolerance_wider_than_general():
    sheet = make_sheet(
        dimensions=[make_dimension("DIM1", nominal=40.0, tolerance=symmetric(1.5))],
        annotations=[note("ISO 2768-mK")],
    )
    findings = run("TOL008", sheet)
    assert len(findings) == 1
    assert "±0.3" in findings[0].message
    assert "+1.5/-1.5" in findings[0].message


def test_tol008_detects_one_sided_excess():
    # +0.5/-0 is narrower than ±0.3 in width but still leaves the general zone
    asymmetric = Tolerance(kind=ToleranceKind.DEVIATION, upper=0.5, lower=0.0)
    sheet = make_sheet(
        dimensions=[make_dimension("DIM1", nominal=40.0, tolerance=asymmetric)],
        annotations=[note("ISO 2768-m")],
    )
    assert len(run("TOL008", sheet)) == 1


def test_tol008_quiet_for_a_tighter_tolerance():
    sheet = make_sheet(
        dimensions=[make_dimension("DIM1", nominal=40.0, tolerance=symmetric(0.05))],
        annotations=[note("ISO 2768-m")],
    )
    assert run("TOL008", sheet) == []


def test_tol008_uses_the_limits_notation_too():
    limits = Tolerance(kind=ToleranceKind.LIMITS, upper=41.0, lower=39.0)
    sheet = make_sheet(
        dimensions=[make_dimension("DIM1", nominal=40.0, tolerance=limits)],
        annotations=[note("ISO 2768-m")],
    )
    assert len(run("TOL008", sheet)) == 1


def test_tol008_ignores_fit_classes():
    fit = Tolerance(kind=ToleranceKind.FIT_CLASS, fit_class="H7")
    sheet = make_sheet(
        dimensions=[make_dimension("DIM1", nominal=40.0, tolerance=fit)],
        annotations=[note("ISO 2768-m")],
    )
    assert run("TOL008", sheet) == []


# --------------------------------------------------------------------- TOL009
def test_tol009_redundant_tolerance():
    sheet = make_sheet(
        dimensions=[make_dimension("DIM1", nominal=56.0, tolerance=symmetric(0.3))],
        annotations=[note("ISO 2768-mK")],
    )
    findings = run("TOL009", sheet)
    assert len(findings) == 1
    assert findings[0].severity is Severity.INFO


def test_tol009_quiet_when_the_value_differs():
    sheet = make_sheet(
        dimensions=[make_dimension("DIM1", nominal=56.0, tolerance=symmetric(0.25))],
        annotations=[note("ISO 2768-mK")],
    )
    assert run("TOL009", sheet) == []


# --------------------------------------------------------------------- TOL010
def chain_sheet(overall_tolerance: Tolerance | None = None, note_text="ISO 2768-m"):
    dims = [
        make_dimension("DIM1", nominal=12.0, extra={"axis": 0.0, "interval": [0.0, 12.0]}),
        make_dimension("DIM2", nominal=56.0, extra={"axis": 0.0, "interval": [12.0, 68.0]}),
        make_dimension("DIM3", nominal=12.0, extra={"axis": 0.0, "interval": [68.0, 80.0]}),
        make_dimension(
            "DIM4",
            nominal=80.0,
            tolerance=overall_tolerance or Tolerance(),
            extra={"axis": 0.0, "interval": [0.0, 80.0]},
        ),
    ]
    return make_sheet(dimensions=dims, annotations=[note(note_text)])


def test_tol010_stack_exceeds_the_overall_tolerance():
    findings = run("TOL010", chain_sheet())
    assert len(findings) == 1
    # 0.2 + 0.3 + 0.2 = ±0.7 against the overall ±0.3
    assert "±0.7" in findings[0].message and "±0.3" in findings[0].message
    assert findings[0].severity is Severity.MAJOR


def test_tol010_quiet_when_the_overall_tolerance_absorbs_the_stack():
    findings = run("TOL010", chain_sheet(overall_tolerance=symmetric(1.0)))
    assert findings == []


def test_tol010_needs_numbers_to_compare():
    # no general tolerance note and no explicit tolerances -> nothing to add up
    dims = [
        make_dimension("DIM1", nominal=12.0, extra={"axis": 0.0, "interval": [0.0, 12.0]}),
        make_dimension("DIM2", nominal=56.0, extra={"axis": 0.0, "interval": [12.0, 68.0]}),
        make_dimension("DIM3", nominal=12.0, extra={"axis": 0.0, "interval": [68.0, 80.0]}),
        make_dimension("DIM4", nominal=80.0, extra={"axis": 0.0, "interval": [0.0, 80.0]}),
    ]
    assert run("TOL010", make_sheet(dimensions=dims)) == []


# --------------------------------------------------------------------- GDT011
def test_gdt011_frame_looser_than_the_general_geometric_tolerance():
    sheet = make_sheet(
        dimensions=[make_dimension("DIM1", nominal=80.0)],
        geometric_tolerances=[
            make_gtol("GTOL1", characteristic=GDTCharacteristic.FLATNESS, value=0.8)
        ],
        annotations=[note("ISO 2768-mK")],
    )
    findings = run("GDT011", sheet)
    assert len(findings) == 1
    assert "0.2" in findings[0].message  # class K flatness for 80 mm
    assert findings[0].severity is Severity.MINOR


def test_gdt011_quiet_for_a_tighter_frame():
    sheet = make_sheet(
        dimensions=[make_dimension("DIM1", nominal=80.0)],
        geometric_tolerances=[
            make_gtol("GTOL1", characteristic=GDTCharacteristic.FLATNESS, value=0.1)
        ],
        annotations=[note("ISO 2768-mK")],
    )
    assert run("GDT011", sheet) == []


def test_gdt011_silent_where_iso2768_defines_nothing():
    sheet = make_sheet(
        dimensions=[make_dimension("DIM1", nominal=80.0)],
        geometric_tolerances=[
            make_gtol("GTOL1", characteristic=GDTCharacteristic.POSITION, value=5.0, datums=["A"])
        ],
        annotations=[note("ISO 2768-mK")],
    )
    assert run("GDT011", sheet) == []


def test_gdt011_needs_a_geometric_class():
    sheet = make_sheet(
        dimensions=[make_dimension("DIM1", nominal=80.0)],
        geometric_tolerances=[
            make_gtol("GTOL1", characteristic=GDTCharacteristic.FLATNESS, value=0.8)
        ],
        annotations=[note("ISO 2768-m")],
    )
    assert run("GDT011", sheet) == []


# ------------------------------------------------------- note from title block
def test_general_tolerance_may_come_from_the_title_block():
    sheet = make_sheet(
        dimensions=[make_dimension("DIM1", nominal=40.0, tolerance=symmetric(1.5))],
        title_block=titled(general_tolerance="ISO 2768-mK"),
    )
    assert len(run("TOL008", sheet)) == 1


# --------------------------------------------------------- ISO 286 fit rules
def fitted(dim_id: str, nominal: float, fit: str, text: str):
    from catia_diff.models.drawing import DimensionKind

    return make_dimension(
        dim_id,
        nominal=nominal,
        kind=DimensionKind.DIAMETER,
        text=text,
        tolerance=Tolerance(kind=ToleranceKind.FIT_CLASS, fit_class=fit),
    )


def test_tol011_reports_a_fit_outside_the_tables():
    sheet = make_sheet(dimensions=[fitted("DIM1", 25.0, "u6", "⌀25 u6")])
    findings = run("TOL011", sheet)
    assert len(findings) == 1
    assert "letter" in findings[0].message
    assert findings[0].severity is Severity.MINOR


def test_tol011_names_a_size_outside_the_tables():
    sheet = make_sheet(dimensions=[fitted("DIM1", 900.0, "H7", "⌀900 H7")])
    findings = run("TOL011", sheet)
    assert len(findings) == 1
    assert "500" in findings[0].message


def test_tol011_quiet_for_a_resolvable_fit():
    assert run("TOL011", make_sheet(dimensions=[fitted("DIM1", 25.0, "H7", "⌀25 H7")])) == []
    assert run("TOL011", make_sheet(dimensions=[fitted("DIM1", 25.0, "H7/g6", "⌀25 H7/g6")])) == []


def test_tol012_flags_interference_and_transition_fits():
    interference = make_sheet(dimensions=[fitted("DIM1", 25.0, "H7/p6", "⌀25 H7/p6")])
    findings = run("TOL012", interference)
    assert len(findings) == 1
    assert "interference" in findings[0].message
    assert "press" in findings[0].message

    transition = make_sheet(dimensions=[fitted("DIM1", 25.0, "H7/k6", "⌀25 H7/k6")])
    findings = run("TOL012", transition)
    assert len(findings) == 1
    assert "transition" in findings[0].message


def test_tol012_quiet_for_a_clearance_fit():
    assert run("TOL012", make_sheet(dimensions=[fitted("DIM1", 25.0, "H7/g6", "⌀25 H7/g6")])) == []
    assert run("TOL012", make_sheet(dimensions=[fitted("DIM1", 25.0, "H7", "⌀25 H7")])) == []


def test_fit_classes_now_participate_in_the_iso2768_comparison():
    """A fit wider than the general tolerance is no longer invisible."""
    loose = make_sheet(
        dimensions=[fitted("DIM1", 25.0, "H13", "⌀25 H13")],
        annotations=[note("ISO 2768-f")],
    )
    assert len(run("TOL008", loose)) == 1

    tight = make_sheet(
        dimensions=[fitted("DIM1", 25.0, "H7", "⌀25 H7")],
        annotations=[note("ISO 2768-f")],
    )
    assert run("TOL008", tight) == []
