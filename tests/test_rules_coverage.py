"""Rules built on the constraint graph (missing dimensioning)."""

from conftest import (
    box,
    make_circle,
    make_context,
    make_dimension,
    make_document,
    make_gtol,
    make_sheet,
)

from catia_diff.config import AuditConfig
from catia_diff.models.drawing import (
    Annotation,
    GDTCharacteristic,
    GeometryFeature,
    GeometryKind,
)
from catia_diff.models.findings import Severity
from catia_diff.models.geometry import BBox, Point2D
from catia_diff.rules.base import get_rule
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
    extra = {"axis": axis, "interval": list(interval)}
    extra.update(kwargs.pop("extra", {}))
    return make_dimension(dim_id, nominal=nominal, extra=extra, **kwargs)


def plate_sheet(dimensions, features=None, **kwargs):
    """A 80x40 plate with a hole at (12, 10), plus the given dimensions."""
    sheet = make_sheet(
        features=features if features is not None else [outline(), make_circle("F1", x=12, y=10)],
        dimensions=dimensions,
        **kwargs,
    )
    segment_views(sheet, AuditConfig())
    return sheet


def run(rule_id: str, sheet, config=None):
    document = make_document(sheet)
    ctx = make_context(document, config)
    rule = get_rule(rule_id)
    target = document if rule.scope == "document" else sheet
    return list(rule.check(target, ctx))


FULL_X = [linear("DX1", 80, 0.0, (0, 80)), linear("DX2", 12, 0.0, (0, 12))]
FULL_Y = [linear("DY1", 40, 90.0, (0, 40)), linear("DY2", 10, 90.0, (0, 10))]


# --------------------------------------------------------------------- DIM011
def test_dim011_reports_a_hole_that_is_not_located():
    sheet = plate_sheet([*FULL_X, linear("DY1", 40, 90.0, (0, 40))])
    findings = run("DIM011", sheet)
    assert len(findings) == 1
    finding = findings[0]
    # a feature whose position cannot be derived makes the part unbuildable
    assert finding.severity is Severity.CRITICAL
    assert "F1" in finding.evidence.object_ids
    assert "Y" in finding.message


def test_dim011_quiet_when_both_axes_reach_the_hole():
    assert run("DIM011", plate_sheet([*FULL_X, *FULL_Y])) == []


def test_dim011_accepts_gdt_position_with_basic_dimensions():
    sheet = plate_sheet(
        [*FULL_X, linear("DY1", 40, 90.0, (0, 40)), make_dimension("DB1", nominal=10.0, is_basic=True)],
        geometric_tolerances=[make_gtol("G1", characteristic=GDTCharacteristic.POSITION, datums=["A"])],
    )
    assert run("DIM011", sheet) == []


def test_dim011_accepts_an_equally_spaced_pattern():
    features = [outline(), make_circle("F1", x=12, y=10), make_circle("F2", x=68, y=10)]
    pattern = linear(
        "DP1", 6.5, 0.0, (0, 6.5), text="4x ⌀6.5 EŞİT BÖLÜNMÜŞ", extra={"multiplicity": 4}
    )
    sheet = plate_sheet([*FULL_X, linear("DY1", 40, 90.0, (0, 40)), pattern], features=features)
    assert run("DIM011", sheet) == []


def test_dim011_needs_interval_data():
    """PDF and vision input carry no measured intervals - the rule stays silent."""
    sheet = plate_sheet([make_dimension("D1", nominal=80.0)])
    assert run("DIM011", sheet) == []


# --------------------------------------------------------------------- DIM012
def test_dim012_reports_geometry_without_a_feature():
    lonely = outline("OUT2", points=((100, 0), (140, 0), (140, 20), (100, 20), (100, 0)))
    sheet = plate_sheet([*FULL_X, *FULL_Y], features=[outline(), make_circle("F1", x=12, y=10), lonely])
    findings = run("DIM012", sheet)
    assert findings
    assert all("F1" not in f.evidence.object_ids for f in findings)


def test_dim012_defers_to_dim011_for_features():
    sheet = plate_sheet([*FULL_X, linear("DY1", 40, 90.0, (0, 40))])
    assert run("DIM012", sheet) == []  # the free node holds a hole -> DIM011 owns it


# --------------------------------------------------------------------- DIM013
def test_dim013_view_without_any_dimension():
    sheet = plate_sheet([linear("DX1", 80, 0.0, (0, 80))])
    # a second, undimensioned view far away
    far = outline("OUT2", points=((300, 0), (340, 0), (340, 20), (300, 20), (300, 0)))
    far2 = make_circle("F9", x=320, y=10)
    sheet.features.extend([far, far2])
    segment_views(sheet, AuditConfig())
    findings = run("DIM013", sheet)
    assert len(findings) == 1
    assert findings[0].severity is Severity.CRITICAL


def test_dim013_quiet_for_a_dimensioned_view():
    assert run("DIM013", plate_sheet([*FULL_X, *FULL_Y])) == []


# --------------------------------------------------------------------- DIM014
def test_dim014_missing_overall_dimension():
    chained = [
        linear("DX1", 12, 0.0, (0, 12)),
        linear("DX2", 68, 0.0, (12, 80)),
        *FULL_Y,
    ]
    findings = run("DIM014", plate_sheet(chained))
    assert len(findings) == 1
    assert "X" in findings[0].message
    assert findings[0].severity is Severity.MINOR


def test_dim014_quiet_when_the_overall_is_given():
    assert run("DIM014", plate_sheet([*FULL_X, *FULL_Y])) == []


# --------------------------------------------------------------------- DIM015
def test_dim015_oblique_edge_without_an_angle():
    trapezoid = outline(points=((0, 0), (80, 0), (60, 40), (0, 40), (0, 0)))
    sheet = plate_sheet([*FULL_X, *FULL_Y], features=[trapezoid])
    findings = run("DIM015", sheet)
    assert len(findings) == 1
    assert "°" in findings[0].message


def test_dim015_quiet_when_an_angle_is_given():
    trapezoid = outline(points=((0, 0), (80, 0), (60, 40), (0, 40), (0, 0)))
    from catia_diff.models.drawing import DimensionKind, Units

    angular = make_dimension(
        "DA1", nominal=116.6, kind=DimensionKind.ANGULAR, units=Units.DEG
    )
    sheet = plate_sheet([*FULL_X, *FULL_Y, angular], features=[trapezoid])
    assert run("DIM015", sheet) == []


def test_dim015_ignores_short_edges():
    chamfered = outline(points=((0, 0), (78, 0), (80, 2), (80, 40), (0, 40), (0, 0)))
    sheet = plate_sheet([*FULL_X, *FULL_Y], features=[chamfered])
    assert run("DIM015", sheet) == []


# ------------------------------------------------------- blanket note support
def test_dim001_is_silenced_by_a_blanket_radius_note():
    arc = GeometryFeature(
        id="ARC1",
        kind=GeometryKind.ARC,
        center=Point2D(x=40.0, y=20.0),
        radius=3.0,
        bbox=box(37, 17, 6, 6),
        extra={"sweep": 90.0},
    )
    without_note = make_sheet(features=[arc], dimensions=[make_dimension("D1", nominal=99.0)])
    assert len(run("DIM001", without_note)) == 1

    with_note = make_sheet(
        features=[arc],
        dimensions=[make_dimension("D1", nominal=99.0)],
        annotations=[Annotation(id="N1", text="TÜM RADYÜSLER R3", bbox=box(0, 100))],
    )
    assert run("DIM001", with_note) == []
