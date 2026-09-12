"""ISO 286 limits and fits."""

import math

import pytest
from conftest import make_dimension

from catia_diff.models.drawing import Tolerance, ToleranceKind, Units
from catia_diff.rules.analysis import fit_class_of, tolerance_deviations, tolerance_width
from catia_diff.standards import iso286
from catia_diff.standards.iso286 import (
    IT_GRADES,
    SHAFT_DEVIATIONS,
    SIZE_RANGES,
    FitClass,
    FitPair,
    clearance,
    coverage_gap,
    deviations,
    fit_character,
    it_value,
    parse_fit,
    size_index,
    standard_tolerance_factor,
)

UM = 1e-3


def um(value: float | None) -> float | None:
    """Round a millimetre value to whole micrometres for comparison."""
    return None if value is None else round(value / UM, 3)


# ------------------------------------------------- tables vs the ISO formula
#: IT grade -> multiple of the standard tolerance factor i (ISO 286-1).
_GRADE_MULTIPLIER = {5: 7, 6: 10, 7: 16, 8: 25, 9: 40, 10: 64, 11: 100, 12: 160, 13: 250, 14: 400}


@pytest.mark.parametrize("grade", sorted(IT_GRADES))
def test_it_table_follows_the_standard_formula(grade):
    """Every tabulated IT value must sit on the curve ISO generated it from.

    The tables stay authoritative (ISO rounds them to preferred numbers), but a
    transcription slip would show up here as a value off the curve.  The first
    size step is excluded: ISO fixes its values directly rather than from the
    formula.
    """
    for index, (lower, upper) in enumerate(SIZE_RANGES):
        if (lower, upper) == (0.0, 3.0):
            continue
        nominal = math.sqrt(lower * upper)
        expected = _GRADE_MULTIPLIER[grade] * standard_tolerance_factor(nominal)
        tabulated = IT_GRADES[grade][index]
        # tolerate ISO rounding: 1 µm absolute or 8% relative, whichever is larger
        # (at 7 µm a single rounding step is already 14%, so percentage alone
        # cannot separate rounding from a transcription slip)
        assert abs(tabulated - expected) <= max(1.0, expected * 0.08), (
            f"IT{grade} for {lower}-{upper} mm: table {tabulated}, formula {expected:.1f}"
        )


@pytest.mark.parametrize(
    ("letter", "formula"),
    [
        ("d", lambda d, it: -16 * d**0.44),
        ("e", lambda d, it: -11 * d**0.41),
        ("f", lambda d, it: -5.5 * d**0.41),
        ("g", lambda d, it: -2.5 * d**0.34),
        ("n", lambda d, it: 5 * d**0.34),
        ("m", lambda d, it: it(7) - it(6)),
    ],
)
def test_shaft_deviation_tables_follow_their_formulas(letter, formula):
    """Same transcription check for the fundamental deviations."""
    for index, (lower, upper) in enumerate(SIZE_RANGES):
        if (lower, upper) == (0.0, 3.0):
            continue
        nominal = math.sqrt(lower * upper)
        expected = formula(nominal, lambda g, row=index: IT_GRADES[g][row])
        tabulated = SHAFT_DEVIATIONS[letter][index]
        # tolerate ISO rounding: 1 µm absolute or 12% relative, whichever is larger
        assert abs(abs(tabulated) - abs(expected)) <= max(1.0, abs(expected) * 0.12), (
            f"{letter} for {lower}-{upper} mm: table {tabulated}, formula {expected:.1f}"
        )


def test_h_and_zero_letters():
    assert SHAFT_DEVIATIONS["h"] == (0,) * len(SIZE_RANGES)
    assert um(deviations(25.0, FitClass("H", 7))[1]) == 0
    assert um(deviations(25.0, FitClass("h", 6))[0]) == 0


# ----------------------------------------------------------- classic values
@pytest.mark.parametrize(
    ("code", "upper_um", "lower_um"),
    [
        ("H7", 21, 0),
        ("H8", 33, 0),
        ("h6", 0, -13),
        ("g6", -7, -20),
        ("f7", -20, -41),
        ("e8", -40, -73),
        ("d9", -65, -117),
        ("k6", 15, 2),
        ("m6", 21, 8),
        ("n6", 28, 15),
        ("p6", 35, 22),
    ],
)
def test_classic_fits_at_25mm(code, upper_um, lower_um):
    resolved = deviations(25.0, parse_fit(code))
    assert (um(resolved[0]), um(resolved[1])) == (upper_um, lower_um)


@pytest.mark.parametrize(
    ("code", "upper_um", "lower_um"),
    [("K7", 6, -15), ("M7", 0, -21), ("N7", -7, -28), ("P7", -14, -35)],
)
def test_hole_letters_derived_with_the_delta_rule(code, upper_um, lower_um):
    """K, M, N and P holes are derived (ES = -ei + Δ), not tabulated."""
    resolved = deviations(25.0, parse_fit(code))
    assert (um(resolved[0]), um(resolved[1])) == (upper_um, lower_um)


def test_js_is_symmetric():
    resolved = deviations(25.0, parse_fit("js6"))
    assert um(resolved[0]) == 6.5
    assert resolved[0] == -resolved[1]


def test_size_ranges_are_over_and_including():
    assert size_index(3.0) == 0  # "up to and including 3"
    assert size_index(3.001) == 1
    assert um(it_value(30.0, 7)) == 21  # 18-30 row
    assert um(it_value(30.001, 7)) == 25  # 30-50 row


# ------------------------------------------------------------------ parsing
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("H7", FitClass("H", 7)),
        ("g6", FitClass("g", 6)),
        ("js9", FitClass("js", 9)),
        ("⌀25 H7", FitClass("H", 7)),
    ],
)
def test_parse_single_fit(text, expected):
    assert parse_fit(text) == expected


def test_parse_pair():
    pair = parse_fit("H7/g6")
    assert isinstance(pair, FitPair)
    assert pair.hole == FitClass("H", 7)
    assert pair.shaft == FitClass("g", 6)
    assert pair.designation == "H7/g6"


def test_parse_rejects_nonsense():
    assert parse_fit(None) is None
    assert parse_fit("20") is None
    assert parse_fit("H99") is None


# -------------------------------------------------------------------- pairs
@pytest.mark.parametrize(
    ("code", "character"),
    [("H7/g6", "clearance"), ("H7/h6", "clearance"), ("H7/k6", "transition"), ("H7/p6", "interference")],
)
def test_fit_character(code, character):
    assert fit_character(25.0, parse_fit(code)) == character


def test_clearance_values():
    minimum, maximum = clearance(25.0, parse_fit("H7/g6"))
    assert (um(minimum), um(maximum)) == (7, 41)


# ----------------------------------------------------------------- coverage
def test_coverage_gap_names_the_reason():
    assert coverage_gap(25.0, FitClass("H", 7)) is None
    assert coverage_gap(900.0, FitClass("H", 7)) == "size"
    assert coverage_gap(25.0, FitClass("u", 6)) == "letter"
    assert coverage_gap(25.0, FitClass("H", 2)) == "grade"


def test_uncovered_fits_return_none_rather_than_a_guess():
    assert deviations(25.0, FitClass("u", 6)) is None
    assert deviations(900.0, FitClass("H", 7)) is None
    assert it_value(0.0, 7) is None
    assert iso286.is_covered(25.0, FitClass("H", 7))


# -------------------------------------------------- integration with rules
def fitted(nominal, fit, units=Units.MM):
    return make_dimension(
        "DIM1",
        nominal=nominal,
        units=units,
        tolerance=Tolerance(kind=ToleranceKind.FIT_CLASS, fit_class=fit),
    )


def test_fit_classes_reach_the_tolerance_helpers():
    resolved = tolerance_deviations(fitted(25.0, "H7"))
    assert (um(resolved[0]), um(resolved[1])) == (21, 0)
    assert um(tolerance_width(fitted(25.0, "g6"))) == 13


def test_inch_dimensions_convert_both_ways():
    # 1 in = 25.4 mm -> IT7 = 21 µm -> 0.021 / 25.4 in
    resolved = tolerance_deviations(fitted(1.0, "H7", units=Units.INCH))
    assert resolved[0] == pytest.approx(0.021 / 25.4, rel=1e-6)


def test_pair_yields_no_single_tolerance():
    """H7/g6 describes an assembly, not one part's tolerance."""
    assert tolerance_deviations(fitted(25.0, "H7/g6")) is None
    assert isinstance(fit_class_of(fitted(25.0, "H7/g6")), FitPair)


def test_uncovered_fit_is_unknown_to_the_helpers():
    assert tolerance_deviations(fitted(25.0, "u6")) is None
