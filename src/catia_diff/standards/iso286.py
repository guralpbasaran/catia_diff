"""ISO 286 limits and fits, resolved to numbers.

A drawing writes ``⌀25 H7`` instead of ``⌀25 +0.021/0`` because the code system
carries the same information in three characters.  Until this module existed
the checkers had to treat a fit class as an unknown quantity; now it resolves
to real deviations and every numeric rule - inverted zones, precision,
ISO 2768 comparison, tolerance stacks - works on fitted dimensions too.

Two tables drive everything:

* **standard tolerance grades** (IT), which fix the *width* of the zone, and
* **fundamental deviations**, the letter, which fix where that zone sits
  relative to the nominal size.

Both are reproduced here as data.  ISO fixes them as tabulated values, but the
standard also states the formulas they were generated from, and
``tests/test_iso286.py`` checks every cell against those formulas: a
transcription slip shows up as a value that no longer follows the curve.

Coverage is deliberately partial - see :data:`SUPPORTED_SHAFT_LETTERS`.  A
letter, grade or size outside it returns ``None`` rather than a guess, and
``TOL011`` reports that so silence is never mistaken for approval.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

MICRON = 1e-3  # µm -> mm

#: ISO 286-1 nominal size steps (mm), "over lower, up to and including upper".
SIZE_RANGES: tuple[tuple[float, float], ...] = (
    (0.0, 3.0),
    (3.0, 6.0),
    (6.0, 10.0),
    (10.0, 18.0),
    (18.0, 30.0),
    (30.0, 50.0),
    (50.0, 80.0),
    (80.0, 120.0),
    (120.0, 180.0),
    (180.0, 250.0),
    (250.0, 315.0),
    (315.0, 400.0),
    (400.0, 500.0),
)
MAX_SIZE_MM = 500.0

#: Standard tolerance grades IT5-IT14 in µm, one row per size range.
IT_GRADES: dict[int, tuple[int, ...]] = {
    #      0-3  3-6 6-10 10-18 18-30 30-50 50-80 80-120 120-180 180-250 250-315 315-400 400-500
    5:  (    4,   5,   6,    8,    9,   11,   13,    15,     18,     20,     23,     25,     27),
    6:  (    6,   8,   9,   11,   13,   16,   19,    22,     25,     29,     32,     36,     40),
    7:  (   10,  12,  15,   18,   21,   25,   30,    35,     40,     46,     52,     57,     63),
    8:  (   14,  18,  22,   27,   33,   39,   46,    54,     63,     72,     81,     89,     97),
    9:  (   25,  30,  36,   43,   52,   62,   74,    87,    100,    115,    130,    140,    155),
    10: (   40,  48,  58,   70,   84,  100,  120,   140,    160,    185,    210,    230,    250),
    11: (   60,  75,  90,  110,  130,  160,  190,   220,    250,    290,    320,    360,    400),
    12: (  100, 120, 150,  180,  210,  250,  300,   350,    400,    460,    520,    570,    630),
    13: (  140, 180, 220,  270,  330,  390,  460,   540,    630,    720,    810,    890,    970),
    14: (  250, 300, 360,  430,  520,  620,  740,   870,   1000,   1150,   1300,   1400,   1550),
}

#: Fundamental deviation of a shaft, µm.  Negative letters carry the upper
#: deviation (es), positive ones the lower deviation (ei); ``h`` is zero.
SHAFT_DEVIATIONS: dict[str, tuple[int, ...]] = {
    "d": (-20, -30, -40, -50, -65, -80, -100, -120, -145, -170, -190, -210, -230),
    "e": (-14, -20, -25, -32, -40, -50, -60, -72, -85, -100, -110, -125, -135),
    "f": (-6, -10, -13, -16, -20, -25, -30, -36, -43, -50, -56, -62, -68),
    "g": (-2, -4, -5, -6, -7, -9, -10, -12, -14, -15, -17, -18, -20),
    "h": (0,) * 13,
    "k": (0, 1, 1, 1, 2, 2, 2, 3, 3, 4, 4, 4, 5),
    "m": (2, 4, 6, 7, 8, 9, 11, 13, 15, 17, 20, 21, 23),
    "n": (4, 8, 10, 12, 15, 17, 20, 23, 27, 31, 34, 37, 40),
    "p": (6, 12, 15, 18, 22, 26, 32, 37, 43, 50, 56, 62, 68),
}

#: Letters whose deviation is an upper deviation (es); the rest are lower (ei).
_UPPER_DEVIATION_LETTERS = frozenset("abcdefgh")

SUPPORTED_SHAFT_LETTERS = frozenset(SHAFT_DEVIATIONS) | {"js"}
SUPPORTED_HOLE_LETTERS = frozenset(letter.upper() for letter in SUPPORTED_SHAFT_LETTERS)
SUPPORTED_GRADES = frozenset(IT_GRADES)

#: Above these grades the hole deviation loses the Δ correction (ISO 286-1).
_DELTA_MAX_GRADE = {"K": 8, "M": 8, "N": 8, "P": 7}


@dataclass(frozen=True)
class FitClass:
    """One ISO 286 code, e.g. ``H7`` (hole) or ``g6`` (shaft)."""

    letter: str
    grade: int

    @property
    def is_hole(self) -> bool:
        return self.letter[0].isupper()

    @property
    def designation(self) -> str:
        return f"{self.letter}{self.grade}"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.designation


@dataclass(frozen=True)
class FitPair:
    """A mating pair written on one callout, e.g. ``H7/g6``."""

    hole: FitClass
    shaft: FitClass

    @property
    def designation(self) -> str:
        return f"{self.hole.designation}/{self.shaft.designation}"


_FIT_RE = re.compile(r"(?<![A-Za-z])(JS|js|[A-Za-z])\s*(\d{1,2})(?![\d.,])")


def parse_fit(text: str | None) -> FitClass | FitPair | None:
    """Parse ``H7``, ``g6``, ``H7/g6`` or ``JS9`` out of a callout."""
    if not text:
        return None
    matches = _FIT_RE.findall(str(text))
    classes: list[FitClass] = []
    for letter, grade in matches:
        try:
            grade_value = int(grade)
        except ValueError:
            continue
        if not 1 <= grade_value <= 18:
            continue
        classes.append(FitClass(letter=letter, grade=grade_value))
    if not classes:
        return None
    if len(classes) == 1:
        return classes[0]
    hole = next((item for item in classes if item.is_hole), None)
    shaft = next((item for item in classes if not item.is_hole), None)
    if hole is not None and shaft is not None:
        return FitPair(hole=hole, shaft=shaft)
    return classes[0]


# --------------------------------------------------------------------------
# Table lookup
# --------------------------------------------------------------------------
def size_index(nominal_mm: float | None) -> int | None:
    """Index of the size range containing ``nominal_mm``."""
    if nominal_mm is None:
        return None
    size = abs(nominal_mm)
    if size <= 0 or size > MAX_SIZE_MM:
        return None
    for index, (lower, upper) in enumerate(SIZE_RANGES):
        within_lower = size >= lower if index == 0 else size > lower
        if within_lower and size <= upper:
            return index
    return None


def it_value(nominal_mm: float | None, grade: int) -> float | None:
    """Standard tolerance IT``grade`` for this size, in mm."""
    index = size_index(nominal_mm)
    row = IT_GRADES.get(grade)
    if index is None or row is None:
        return None
    return row[index] * MICRON


def standard_tolerance_factor(nominal_mm: float) -> float:
    """ISO 286-1 tolerance factor ``i`` in µm - the curve the tables follow.

    Used by the tests to verify the tabulated grades; the tables themselves
    stay authoritative because ISO rounds them to preferred numbers.
    """
    index = size_index(nominal_mm)
    if index is None:
        raise ValueError(f"{nominal_mm} mm is outside ISO 286")
    lower, upper = SIZE_RANGES[index]
    geometric_mean = math.sqrt(max(lower, 1.0) * upper)
    return 0.45 * geometric_mean ** (1 / 3) + 0.001 * geometric_mean


def fundamental_deviation(nominal_mm: float | None, fit: FitClass) -> float | None:
    """Fundamental deviation of ``fit`` in mm, or ``None`` when uncovered.

    Holes follow from shafts by the standard's own rules: the general rule
    (``EI = -es``) for the clearance letters, and the Δ rule
    (``ES = -ei + Δ``) for K, M, N and P.
    """
    index = size_index(nominal_mm)
    if index is None or fit.grade not in SUPPORTED_GRADES:
        return None
    letter = fit.letter
    if letter in {"js", "JS"}:
        return None  # symmetric: handled directly in deviations()

    shaft_letter = letter.lower()
    row = SHAFT_DEVIATIONS.get(shaft_letter)
    if row is None:
        return None
    value = row[index] * MICRON
    if not fit.is_hole:
        return value

    # Hole: mirror the shaft, with the Δ correction where the standard applies it.
    if shaft_letter in _UPPER_DEVIATION_LETTERS:
        return -value  # EI = -es
    delta_limit = _DELTA_MAX_GRADE.get(letter.upper())
    delta = 0.0
    if delta_limit is not None and fit.grade <= delta_limit:
        finer = it_value(nominal_mm, fit.grade - 1)
        coarser = it_value(nominal_mm, fit.grade)
        if finer is None or coarser is None:
            return None
        delta = coarser - finer
    return -value + delta  # ES


def _zero_normalised(pair: tuple[float, float]) -> tuple[float, float]:
    """Turn IEEE negative zero into plain zero so reports never print "-0"."""
    return (pair[0] + 0.0, pair[1] + 0.0)


def deviations(nominal_mm: float | None, fit: FitClass) -> tuple[float, float] | None:
    """``(upper, lower)`` deviations of ``fit`` in mm, relative to nominal."""
    tolerance = it_value(nominal_mm, fit.grade)
    if tolerance is None:
        return None
    if fit.letter in {"js", "JS"}:
        half = tolerance / 2.0
        return (half, -half)


    deviation = fundamental_deviation(nominal_mm, fit)
    if deviation is None:
        return None
    shaft_letter = fit.letter.lower()
    if fit.is_hole:
        if shaft_letter in _UPPER_DEVIATION_LETTERS:
            lower = deviation  # EI
            return _zero_normalised((lower + tolerance, lower))
        upper = deviation  # ES
        return _zero_normalised((upper, upper - tolerance))
    if shaft_letter in _UPPER_DEVIATION_LETTERS:
        upper = deviation  # es
        return _zero_normalised((upper, upper - tolerance))
    lower = deviation  # ei
    return _zero_normalised((lower + tolerance, lower))


def is_covered(nominal_mm: float | None, fit: FitClass) -> bool:
    return deviations(nominal_mm, fit) is not None


def coverage_gap(nominal_mm: float | None, fit: FitClass) -> str | None:
    """Why ``fit`` cannot be resolved: ``size``, ``letter``, ``grade`` or ``None``.

    Naming the reason matters: "we do not tabulate that letter" and "the
    standard stops at 500 mm" call for different fixes on the drawing.
    """
    if deviations(nominal_mm, fit) is not None:
        return None
    if size_index(nominal_mm) is None:
        return "size"
    supported = SUPPORTED_HOLE_LETTERS if fit.is_hole else SUPPORTED_SHAFT_LETTERS
    if fit.letter not in supported:
        return "letter"
    if fit.grade not in SUPPORTED_GRADES:
        return "grade"
    return "letter"


# --------------------------------------------------------------------------
# Mating pairs
# --------------------------------------------------------------------------
def clearance(nominal_mm: float | None, pair: FitPair) -> tuple[float, float] | None:
    """``(minimum, maximum)`` clearance of the pair in mm.

    A negative value is interference - the shaft is larger than the hole.
    """
    hole = deviations(nominal_mm, pair.hole)
    shaft = deviations(nominal_mm, pair.shaft)
    if hole is None or shaft is None:
        return None
    return _zero_normalised((hole[1] - shaft[0], hole[0] - shaft[1]))


def fit_character(nominal_mm: float | None, pair: FitPair) -> str | None:
    """``clearance`` / ``transition`` / ``interference``, or ``None``."""
    limits = clearance(nominal_mm, pair)
    if limits is None:
        return None
    minimum, maximum = limits
    if minimum >= 0:
        return "clearance"
    if maximum <= 0:
        return "interference"
    return "transition"
