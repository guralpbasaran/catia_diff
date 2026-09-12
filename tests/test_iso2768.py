import pytest
from conftest import make_dimension

from catia_diff.models.drawing import DimensionKind, GDTCharacteristic, ToleranceKind, Units
from catia_diff.standards import iso2768
from catia_diff.standards.iso2768 import (
    GeneralToleranceSpec,
    GeometricClass,
    LinearClass,
    angular_deviation,
    broken_edge_deviation,
    circular_runout,
    geometric_limit,
    linear_deviation,
    parse_designation,
    perpendicularity,
    straightness_flatness,
    symmetry,
)


# ------------------------------------------------------------------ tables
@pytest.mark.parametrize(
    ("nominal", "cls", "expected"),
    [
        (0.5, LinearClass.FINE, 0.05),       # first row starts at 0.5 inclusive
        (3.0, LinearClass.MEDIUM, 0.1),      # "up to and including 3"
        (3.5, LinearClass.MEDIUM, 0.1),      # "over 3 up to 6"
        (6.0, LinearClass.COARSE, 0.3),
        (6.5, LinearClass.COARSE, 0.5),
        (30.0, LinearClass.MEDIUM, 0.2),
        (56.0, LinearClass.MEDIUM, 0.3),
        (120.0, LinearClass.FINE, 0.15),
        (400.0, LinearClass.VERY_COARSE, 2.5),
        (1000.0, LinearClass.COARSE, 2.0),
        (2000.0, LinearClass.FINE, 0.5),
        (4000.0, LinearClass.MEDIUM, 2.0),
    ],
)
def test_linear_table_boundaries(nominal, cls, expected):
    assert linear_deviation(nominal, cls) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("nominal", "cls"),
    [
        (0.4, LinearClass.MEDIUM),          # below the table
        (4001.0, LinearClass.MEDIUM),       # above the table
        (2.0, LinearClass.VERY_COARSE),     # class v has no 0.5-3 row
        (3000.0, LinearClass.FINE),         # class f stops at 2000
    ],
)
def test_linear_table_gaps_return_none(nominal, cls):
    assert linear_deviation(nominal, cls) is None


def test_broken_edge_and_angular_tables():
    assert broken_edge_deviation(1.0, LinearClass.MEDIUM) == 0.2
    assert broken_edge_deviation(5.0, LinearClass.COARSE) == 1.0
    assert broken_edge_deviation(50.0, LinearClass.FINE) == 1.0  # last row is open-ended

    assert angular_deviation(5.0, LinearClass.MEDIUM) == 1.0
    assert angular_deviation(60.0, LinearClass.MEDIUM) == pytest.approx(1 / 3)  # 0°20'
    assert angular_deviation(500.0, LinearClass.COARSE) == pytest.approx(1 / 6)  # 0°10'
    # unknown side length -> the largest (most permissive) row
    assert angular_deviation(None, LinearClass.MEDIUM) == 1.0


def test_geometric_tables():
    assert straightness_flatness(80.0, GeometricClass.K) == 0.2
    assert straightness_flatness(10.0, GeometricClass.H) == 0.02
    assert straightness_flatness(5000.0, GeometricClass.L) is None
    assert perpendicularity(250.0, GeometricClass.H) == 0.3
    assert symmetry(50.0, GeometricClass.H) == 0.5
    assert circular_runout(GeometricClass.L) == 0.5


def test_derived_geometric_characteristics():
    # roundness: the diameter tolerance, capped at the run-out value
    assert geometric_limit(
        GDTCharacteristic.CIRCULARITY, 50.0, GeometricClass.K, size_tolerance_mm=0.05
    ) == 0.05
    assert geometric_limit(
        GDTCharacteristic.CIRCULARITY, 50.0, GeometricClass.K, size_tolerance_mm=0.9
    ) == 0.2
    # parallelism: the greater of size tolerance and flatness
    assert geometric_limit(
        GDTCharacteristic.PARALLELISM, 50.0, GeometricClass.K, size_tolerance_mm=0.5
    ) == 0.5
    assert geometric_limit(GDTCharacteristic.PARALLELISM, 50.0, GeometricClass.K) == 0.2
    # undefined by ISO 2768-2
    assert geometric_limit(GDTCharacteristic.CYLINDRICITY, 50.0, GeometricClass.K) is None
    assert geometric_limit(GDTCharacteristic.POSITION, 50.0, GeometricClass.H) is None
    assert geometric_limit(GDTCharacteristic.TOTAL_RUNOUT, 50.0, GeometricClass.H) is None


# ----------------------------------------------------------------- parsing
@pytest.mark.parametrize(
    ("text", "linear", "geometric"),
    [
        ("ISO 2768-mK", LinearClass.MEDIUM, GeometricClass.K),
        ("GENEL TOLERANSLAR ISO 2768-mK", LinearClass.MEDIUM, GeometricClass.K),
        ("ISO 2768 fH", LinearClass.FINE, GeometricClass.H),
        ("ISO 2768-m-K", LinearClass.MEDIUM, GeometricClass.K),
        ("ISO 2768-1 m", LinearClass.MEDIUM, None),
        ("iso2768-vL".replace("iso2768", "ISO 2768"), LinearClass.VERY_COARSE, GeometricClass.L),
        ("DIN 7168-c", LinearClass.COARSE, None),
        ("EN 22768-mK", LinearClass.MEDIUM, GeometricClass.K),
        ("ISO 2768", None, None),
        ("General tolerances ISO 2768 GENEL", None, None),
    ],
)
def test_parse_designation(text, linear, geometric):
    spec = parse_designation(text)
    assert spec is not None
    assert spec.linear is linear
    assert spec.geometric is geometric


def test_parse_designation_reports_unknown_letters():
    spec = parse_designation("ISO 2768-mX")
    assert spec.linear is LinearClass.MEDIUM
    assert spec.unknown_letters == ("X",)
    assert spec.is_usable


def test_parse_designation_ignores_unrelated_text():
    assert parse_designation("ASME Y14.5-2018") is None
    assert parse_designation(None) is None


def test_designation_round_trip():
    assert parse_designation("ISO 2768-mK").designation == "ISO 2768-mK"
    assert parse_designation("ISO 2768").designation == "ISO 2768"
    assert not parse_designation("ISO 2768").is_usable


# ------------------------------------------------------------ dimension API
def spec(text="ISO 2768-mK") -> GeneralToleranceSpec:
    return parse_designation(text)


def test_deviation_for_linear_radius_and_angle():
    linear = make_dimension("D1", nominal=56.0)
    assert spec().deviation_for(linear) == 0.3

    radius = make_dimension("D2", nominal=2.0, kind=DimensionKind.RADIAL, prefix="R")
    assert spec().deviation_for(radius) == 0.2  # broken-edge table, not 0.1

    angle = make_dimension("D3", nominal=30.0, kind=DimensionKind.ANGULAR, units=Units.DEG)
    assert spec().deviation_for(angle) == 1.0


def test_deviation_for_inches_converts_both_ways():
    dim = make_dimension("D1", nominal=2.0, units=Units.INCH)  # 50.8 mm -> ±0.3 mm
    deviation = spec().deviation_for(dim)
    assert deviation == pytest.approx(0.3 / 25.4)


def test_coverage_and_tolerance_object():
    covered = make_dimension("D1", nominal=56.0)
    assert spec().covers(covered)
    tolerance = spec().tolerance_for(covered)
    assert tolerance.kind is ToleranceKind.GENERAL
    assert (tolerance.upper, tolerance.lower) == (0.3, -0.3)
    assert tolerance.general_class == "ISO 2768-mK"

    tiny = make_dimension("D2", nominal=0.3)
    assert not spec().covers(tiny)
    assert spec().tolerance_for(tiny) is None


def test_geometric_only_spec_has_no_linear_answers():
    geometric_only = GeneralToleranceSpec(geometric=GeometricClass.K)
    assert geometric_only.deviation_for(make_dimension("D1", nominal=20.0)) is None
    assert geometric_only.geometric_limit_mm(GDTCharacteristic.FLATNESS, 80.0) == 0.2

    linear_only = parse_designation("ISO 2768-m")
    assert linear_only.geometric_limit_mm(GDTCharacteristic.FLATNESS, 80.0) is None


def test_unit_conversion_helpers():
    assert iso2768.to_mm(1.0, Units.INCH) == 25.4
    assert iso2768.to_mm(2.0, Units.CM) == 20.0
    assert iso2768.to_mm(1.0, Units.DEG) is None
    assert iso2768.from_mm(25.4, Units.INCH) == 1.0
