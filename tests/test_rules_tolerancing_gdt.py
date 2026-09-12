import pytest
from conftest import (
    box,
    make_context,
    make_dimension,
    make_document,
    make_gtol,
    make_sheet,
    titled,
)

from catia_diff.models.drawing import (
    Annotation,
    DatumFeature,
    DimensionKind,
    GDTCharacteristic,
    MaterialCondition,
    SurfaceFinish,
    SurfaceSymbolKind,
    Tolerance,
    ToleranceKind,
    WeldSymbol,
)
from catia_diff.models.findings import Severity
from catia_diff.rules.base import get_rule


def run(rule_id: str, sheet, config=None):
    document = make_document(sheet)
    ctx = make_context(document, config)
    rule = get_rule(rule_id)
    target = document if rule.scope == "document" else sheet
    return list(rule.check(target, ctx))


# --------------------------------------------------------------------- TOL
def test_tol001_major_without_a_general_tolerance_note():
    sheet = make_sheet(dimensions=[make_dimension("DIM1")])
    findings = run("TOL001", sheet)
    assert len(findings) == 1 and findings[0].severity is Severity.MAJOR


def test_tol001_downgrades_when_a_general_note_exists():
    sheet = make_sheet(
        dimensions=[make_dimension("DIM1", nominal=20.5, decimals=2, kind=DimensionKind.DIAMETER)],
        annotations=[
            Annotation(id="N1", text="GENEL TOLERANSLAR ISO 2768-mK", category="general_tolerance")
        ],
    )
    findings = run("TOL001", sheet)
    assert len(findings) == 1
    assert findings[0].severity is Severity.MINOR
    assert "ISO 2768" in findings[0].message


def test_tol001_silent_for_plain_dimensions_under_a_general_note():
    sheet = make_sheet(
        dimensions=[make_dimension("DIM1", nominal=20.0, decimals=0)],
        title_block=titled(general_tolerance="ISO 2768-mK"),
    )
    assert run("TOL001", sheet) == []


def test_tol002_inverted_and_zero_width_zones():
    inverted = make_dimension(
        "DIM1", tolerance=Tolerance(kind=ToleranceKind.DEVIATION, upper=-0.10, lower=-0.05)
    )
    findings = run("TOL002", make_sheet(dimensions=[inverted]))
    assert len(findings) == 1 and findings[0].severity is Severity.CRITICAL

    zero = make_dimension(
        "DIM2", tolerance=Tolerance(kind=ToleranceKind.DEVIATION, upper=0.0, lower=0.0)
    )
    findings = run("TOL002", make_sheet(dimensions=[zero]))
    assert len(findings) == 1 and findings[0].severity is Severity.MAJOR


def test_tol002_nominal_outside_its_own_limits():
    dim = make_dimension(
        "DIM1", nominal=30.0, tolerance=Tolerance(kind=ToleranceKind.LIMITS, upper=20.2, lower=19.9)
    )
    assert len(run("TOL002", make_sheet(dimensions=[dim]))) == 1


def test_tol003_precision_mismatch():
    dim = make_dimension(
        "DIM1",
        nominal=20.0,
        decimals=0,
        tolerance=Tolerance(kind=ToleranceKind.SYMMETRIC, upper=0.05, lower=-0.05),
    )
    assert len(run("TOL003", make_sheet(dimensions=[dim]))) == 1


def test_tol004_unrealistic_tolerance():
    dim = make_dimension(
        "DIM1", tolerance=Tolerance(kind=ToleranceKind.SYMMETRIC, upper=0.0002, lower=-0.0002)
    )
    assert len(run("TOL004", make_sheet(dimensions=[dim]))) == 1


def test_tol005_mixed_notations():
    sheet = make_sheet(
        dimensions=[
            make_dimension("DIM1", tolerance=Tolerance(kind=ToleranceKind.SYMMETRIC, upper=0.1, lower=-0.1)),
            make_dimension("DIM2", tolerance=Tolerance(kind=ToleranceKind.LIMITS, upper=20.2, lower=19.9)),
        ]
    )
    assert len(run("TOL005", sheet)) == 1


# --------------------------------------------------------------------- GDT
def test_gdt001_undefined_datum():
    sheet = make_sheet(
        geometric_tolerances=[make_gtol(datums=["A", "B"])],
        datums=[DatumFeature(id="D1", label="A", bbox=box(0, 0))],
    )
    findings = run("GDT001", sheet)
    assert len(findings) == 1 and "B" in findings[0].message


def test_gdt002_unused_datum():
    sheet = make_sheet(
        geometric_tolerances=[make_gtol(datums=["A"])],
        datums=[
            DatumFeature(id="D1", label="A", bbox=box(0, 0)),
            DatumFeature(id="D2", label="C", bbox=box(0, 10)),
        ],
    )
    findings = run("GDT002", sheet)
    assert [f.evidence.object_ids for f in findings] == [("D2",)]


def test_gdt003_duplicate_datum_label():
    sheet = make_sheet(
        datums=[
            DatumFeature(id="D1", label="A", bbox=box(0, 0)),
            DatumFeature(id="D2", label="A", bbox=box(30, 0)),
        ]
    )
    assert len(run("GDT003", sheet)) == 1


def test_gdt004_form_tolerance_with_datum():
    sheet = make_sheet(
        geometric_tolerances=[
            make_gtol(characteristic=GDTCharacteristic.FLATNESS, value=0.05, datums=["A"])
        ],
        datums=[DatumFeature(id="D1", label="A", bbox=box(0, 0))],
    )
    assert len(run("GDT004", sheet)) == 1


@pytest.mark.parametrize(
    "characteristic",
    [GDTCharacteristic.PERPENDICULARITY, GDTCharacteristic.POSITION, GDTCharacteristic.TOTAL_RUNOUT],
)
def test_gdt005_missing_datum(characteristic):
    sheet = make_sheet(geometric_tolerances=[make_gtol(characteristic=characteristic, datums=[])])
    assert len(run("GDT005", sheet)) == 1


def test_gdt005_ignores_form_tolerances():
    sheet = make_sheet(
        geometric_tolerances=[make_gtol(characteristic=GDTCharacteristic.FLATNESS, datums=[])]
    )
    assert run("GDT005", sheet) == []


def test_gdt006_missing_value():
    sheet = make_sheet(geometric_tolerances=[make_gtol(value=None, datums=["A"])])
    assert len(run("GDT006", sheet)) == 1


def test_gdt007_position_without_basic_dimensions():
    sheet = make_sheet(
        geometric_tolerances=[make_gtol(datums=["A", "B"])],
        dimensions=[make_dimension("DIM1")],
    )
    assert len(run("GDT007", sheet)) == 1

    with_basic = make_sheet(
        geometric_tolerances=[make_gtol(datums=["A", "B"])],
        dimensions=[make_dimension("DIM1", is_basic=True)],
    )
    assert run("GDT007", with_basic) == []


def test_gdt008_position_zone_without_diameter():
    sheet = make_sheet(geometric_tolerances=[make_gtol(datums=["A"], diametral_zone=False)])
    assert len(run("GDT008", sheet)) == 1


def test_gdt009_modifier_on_surface_form_tolerance():
    gtol = make_gtol(
        characteristic=GDTCharacteristic.FLATNESS,
        value=0.05,
        material_condition=MaterialCondition.MMC,
    )
    assert len(run("GDT009", make_sheet(geometric_tolerances=[gtol]))) == 1


def test_gdt010_repeated_datum_in_one_frame():
    gtol = make_gtol(datums=["A", "B", "A"])
    assert len(run("GDT010", make_sheet(geometric_tolerances=[gtol]))) == 1


# ------------------------------------------------------------------ SYMBOLS
def test_sym001_surface_symbol_without_value():
    sheet = make_sheet(
        surface_finishes=[SurfaceFinish(id="S1", bbox=box(0, 0), raw="√")]
    )
    assert len(run("SYM001", sheet)) == 1


def test_sym001_allows_as_cast_surfaces():
    sheet = make_sheet(
        surface_finishes=[
            SurfaceFinish(
                id="S1", bbox=box(0, 0), symbol_kind=SurfaceSymbolKind.MACHINING_PROHIBITED
            )
        ]
    )
    assert run("SYM001", sheet) == []


def test_sym002_implausible_roughness():
    sheet = make_sheet(surface_finishes=[SurfaceFinish(id="S1", bbox=box(0, 0), ra=250.0)])
    assert len(run("SYM002", sheet)) == 1


def test_sym003_and_sym004_weld_rules():
    no_size = make_sheet(welds=[WeldSymbol(id="W1", bbox=box(0, 0), weld_type="throat")])
    assert len(run("SYM003", no_size)) == 1

    no_pitch = make_sheet(
        welds=[WeldSymbol(id="W1", bbox=box(0, 0), weld_type="throat", size=5.0, length=50.0)]
    )
    assert len(run("SYM004", no_pitch)) == 1


def test_sym005_no_roughness_anywhere():
    sheet = make_sheet(dimensions=[make_dimension("DIM1")])
    assert len(run("SYM005", sheet)) == 1
