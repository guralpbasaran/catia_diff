import pytest

from catia_diff.extract.text_parsing import (
    classify_annotation,
    detect_general_tolerance,
    detect_units_note,
    normalize_drawing_text,
    parse_datum_feature,
    parse_dimension_text,
    parse_feature_control_frame,
    parse_surface_finish,
    parse_weld_symbol,
    to_float,
)
from catia_diff.models.drawing import (
    DimensionKind,
    GDTCharacteristic,
    MaterialCondition,
    ToleranceKind,
    Units,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("%%c12.5", "⌀12.5"),
        ("30%%d", "30°"),
        ("20%%p0.1", "20±0.1"),
        ("A\\PB", "A\nB"),
        ("{\\H0.7x;40}", "40"),
        ("40\\S+0.05^-0.05;", "40 +0.05 -0.05"),
    ],
)
def test_normalize_drawing_text(raw, expected):
    assert normalize_drawing_text(raw) == expected


@pytest.mark.parametrize(
    ("token", "expected"),
    [("12,5", 12.5), ("12.5", 12.5), ("1,234.5", 1234.5), ("", None), ("abc", None)],
)
def test_to_float(token, expected):
    assert to_float(token) == expected


def test_parse_plain_dimension():
    parsed = parse_dimension_text("⌀25 H7")
    assert parsed.nominal == 25.0
    assert parsed.prefix == "⌀"
    assert parsed.kind_hint is DimensionKind.DIAMETER
    assert parsed.tolerance.kind is ToleranceKind.FIT_CLASS
    assert parsed.tolerance.fit_class == "H7"


def test_parse_symmetric_and_deviation_tolerances():
    symmetric = parse_dimension_text("20±0,05")
    assert symmetric.tolerance.kind is ToleranceKind.SYMMETRIC
    assert (symmetric.tolerance.upper, symmetric.tolerance.lower) == (0.05, -0.05)

    deviation = parse_dimension_text("20 +0.2/-0.1")
    assert deviation.tolerance.kind is ToleranceKind.DEVIATION
    assert (deviation.tolerance.upper, deviation.tolerance.lower) == (0.2, -0.1)
    assert deviation.tolerance.span == pytest.approx(0.3)


def test_parse_limits_and_fit_pair():
    limits = parse_dimension_text("12.5/12.3")
    assert limits.tolerance.kind is ToleranceKind.LIMITS
    assert (limits.tolerance.upper, limits.tolerance.lower) == (12.5, 12.3)

    fit = parse_dimension_text("20 H7/g6")
    assert fit.tolerance.fit_class == "H7/g6"


def test_parse_reference_basic_and_multiplicity():
    assert parse_dimension_text("(45,5)").is_reference
    assert parse_dimension_text("[30]").is_basic
    assert parse_dimension_text("[30]").tolerance.kind is ToleranceKind.BASIC
    assert parse_dimension_text("4x ⌀6.5").multiplicity == 4
    assert parse_dimension_text("12.5 REF").is_reference


def test_parse_angular_thread_and_units():
    angular = parse_dimension_text("30°")
    assert angular.units is Units.DEG and angular.kind_hint is DimensionKind.ANGULAR
    thread = parse_dimension_text("M8x1.25")
    assert thread.thread == "M8x1.25" and thread.kind_hint is DimensionKind.THREAD
    assert parse_dimension_text('2.500 in').units is Units.INCH
    assert parse_dimension_text("40").decimals == 0
    assert parse_dimension_text("40.250").decimals == 3


def test_measurement_placeholder_uses_geometry():
    parsed = parse_dimension_text("<>", measured=41.0)
    assert parsed.nominal == 41.0
    assert parsed.has_measurement_placeholder


def test_parse_dimension_returns_none_for_prose():
    assert parse_dimension_text("") is None
    assert parse_dimension_text("NOTES") is None


def test_parse_feature_control_frame_from_gdt_font():
    fcf = parse_feature_control_frame("{\\Fgdt;j}%%v{\\Fgdt;m}0.1{\\Fgdt;n}%%vA%%vB")
    assert fcf.characteristic is GDTCharacteristic.POSITION
    assert fcf.value == 0.1
    assert fcf.diametral_zone
    assert [label for label, _ in fcf.datums] == ["A", "B"]


def test_parse_feature_control_frame_unicode_and_flat():
    frame = parse_feature_control_frame("⌖|⌀0.2Ⓜ|A|B|C")
    assert frame.material_condition is MaterialCondition.MMC
    assert [label for label, _ in frame.datums] == ["A", "B", "C"]

    flat = parse_feature_control_frame("⊥ 0.1 A")
    assert flat.characteristic is GDTCharacteristic.PERPENDICULARITY
    assert flat.value == 0.1
    assert [label for label, _ in flat.datums] == ["A"]

    assert parse_feature_control_frame("just a note") is None


@pytest.mark.parametrize(("raw", "label"), [("-A-", "A"), ("[B]", "B"), ("C", "C")])
def test_parse_datum_feature(raw, label):
    assert parse_datum_feature(raw) == label


def test_parse_datum_feature_rejects_text():
    assert parse_datum_feature("SECTION A-A") is None


def test_parse_surface_finish_and_weld():
    surface = parse_surface_finish("Ra 3.2")
    assert surface.ra == 3.2
    assert parse_surface_finish("√1,6").ra == 1.6
    assert parse_surface_finish("40") is None

    weld = parse_weld_symbol("a5-50x100")
    assert (weld.weld_type, weld.size, weld.length, weld.pitch) == ("throat", 5.0, 50.0, 100.0)


def test_notes_classification():
    assert detect_general_tolerance("GENEL TOLERANSLAR ISO 2768-mK") is not None
    assert detect_general_tolerance("nothing here") is None
    assert classify_annotation("ISO 2768-mK") == "general_tolerance"
    assert classify_annotation("REV A") == "revision"
    assert classify_annotation("THIRD ANGLE PROJECTION") == "projection"
    assert detect_units_note("ALL DIMENSIONS IN MM") is Units.MM
    assert detect_units_note("ÖLÇÜLER MM CİNSİNDENDİR") is Units.MM


def test_surface_marker_must_stand_alone():
    """Regression: 'TOLERANSLAR' contains 'RA' but is not a roughness callout."""
    assert parse_surface_finish("GENEL TOLERANSLAR ISO 2768-mK") is None
    assert parse_surface_finish("GENERAL TOLERANCES") is None
    assert parse_surface_finish("RA6.3").ra == 6.3
    assert parse_surface_finish("Rz 12,5").rz == 12.5


def test_weld_size_must_be_written_against_the_letter():
    """Regression: 'DETAIL A 2:1' is a view caption, not an a2 weld."""
    assert parse_weld_symbol("DETAIL A 2:1") is None
    assert parse_weld_symbol("SECTION A-A") is None
    assert parse_weld_symbol("z6").size == 6.0
