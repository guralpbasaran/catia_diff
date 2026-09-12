"""ISO 2768 general tolerances as data plus lookup.

ISO 2768-1 (linear and angular dimensions) and ISO 2768-2 (geometrical
tolerances) let a drawing replace hundreds of individual tolerances with one
note such as ``ISO 2768-mK``.  This module turns that note back into numbers so
the checkers can reason about it: what a dimension is actually allowed to
deviate, whether a nominal size is covered by the table at all, and whether an
individually indicated tolerance is tighter or looser than the general one.

Tables are reproduced as data with their range semantics intact ("over 30 up
to 120"), and every value that the standard leaves undefined is ``None``
rather than a guess - a missing value must stay visible to the caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from catia_diff.models.drawing import Dimension, DimensionKind, GDTCharacteristic, Units

STANDARD_NAME = "ISO 2768"
#: Designations that reference the same tables.
EQUIVALENT_STANDARDS = ("ISO 2768", "DIN 7168", "EN 22768", "TS 1990")


class LinearClass(str, Enum):
    """ISO 2768-1 tolerance classes for linear and angular dimensions."""

    FINE = "f"
    MEDIUM = "m"
    COARSE = "c"
    VERY_COARSE = "v"

    @property
    def label(self) -> str:
        return {
            LinearClass.FINE: "fine",
            LinearClass.MEDIUM: "medium",
            LinearClass.COARSE: "coarse",
            LinearClass.VERY_COARSE: "very coarse",
        }[self]


class GeometricClass(str, Enum):
    """ISO 2768-2 tolerance classes for geometrical tolerances."""

    H = "H"
    K = "K"
    L = "L"


Row = tuple[float, float, dict[str, float | None]]

#: ISO 2768-1 table 1 - linear dimensions except broken edges (mm).
#: (over, up to and including, permissible deviation per class)
LINEAR_TABLE: tuple[Row, ...] = (
    (0.5, 3.0, {"f": 0.05, "m": 0.1, "c": 0.2, "v": None}),
    (3.0, 6.0, {"f": 0.05, "m": 0.1, "c": 0.3, "v": 0.5}),
    (6.0, 30.0, {"f": 0.1, "m": 0.2, "c": 0.5, "v": 1.0}),
    (30.0, 120.0, {"f": 0.15, "m": 0.3, "c": 0.8, "v": 1.5}),
    (120.0, 400.0, {"f": 0.2, "m": 0.5, "c": 1.2, "v": 2.5}),
    (400.0, 1000.0, {"f": 0.3, "m": 0.8, "c": 2.0, "v": 4.0}),
    (1000.0, 2000.0, {"f": 0.5, "m": 1.2, "c": 3.0, "v": 6.0}),
    (2000.0, 4000.0, {"f": None, "m": 2.0, "c": 4.0, "v": 8.0}),
)
#: The first row starts *at* 0.5 mm (inclusive); below that the standard
#: requires the deviation to be indicated on the dimension itself.
LINEAR_MIN_SIZE = 0.5
LINEAR_MAX_SIZE = 4000.0

#: ISO 2768-1 table 2 - external radii and chamfer heights (mm).
BROKEN_EDGE_TABLE: tuple[Row, ...] = (
    (0.5, 3.0, {"f": 0.2, "m": 0.2, "c": 0.4, "v": 0.4}),
    (3.0, 6.0, {"f": 0.5, "m": 0.5, "c": 1.0, "v": 1.0}),
    (6.0, float("inf"), {"f": 1.0, "m": 1.0, "c": 2.0, "v": 2.0}),
)

#: ISO 2768-1 table 3 - angular dimensions, by the shorter side of the angle
#: (mm), permissible deviation in degrees.
ANGULAR_TABLE: tuple[Row, ...] = (
    (0.0, 10.0, {"f": 1.0, "m": 1.0, "c": 1.5, "v": 3.0}),
    (10.0, 50.0, {"f": 0.5, "m": 0.5, "c": 1.0, "v": 2.0}),
    (50.0, 120.0, {"f": 1 / 3, "m": 1 / 3, "c": 0.5, "v": 1.0}),
    (120.0, 400.0, {"f": 1 / 6, "m": 1 / 6, "c": 0.25, "v": 0.5}),
    (400.0, float("inf"), {"f": 1 / 12, "m": 1 / 12, "c": 1 / 6, "v": 1 / 3}),
)

#: ISO 2768-2 table 1 - straightness and flatness, by nominal length (mm).
STRAIGHTNESS_FLATNESS_TABLE: tuple[Row, ...] = (
    (0.0, 10.0, {"H": 0.02, "K": 0.05, "L": 0.1}),
    (10.0, 30.0, {"H": 0.05, "K": 0.1, "L": 0.2}),
    (30.0, 100.0, {"H": 0.1, "K": 0.2, "L": 0.4}),
    (100.0, 300.0, {"H": 0.2, "K": 0.4, "L": 0.8}),
    (300.0, 1000.0, {"H": 0.3, "K": 0.6, "L": 1.2}),
    (1000.0, 3000.0, {"H": 0.4, "K": 0.8, "L": 1.6}),
)

#: ISO 2768-2 table 2 - perpendicularity, by the shorter side (mm).
PERPENDICULARITY_TABLE: tuple[Row, ...] = (
    (0.0, 100.0, {"H": 0.2, "K": 0.4, "L": 0.6}),
    (100.0, 300.0, {"H": 0.3, "K": 0.6, "L": 1.0}),
    (300.0, 1000.0, {"H": 0.4, "K": 0.8, "L": 1.5}),
    (1000.0, 3000.0, {"H": 0.5, "K": 1.0, "L": 2.0}),
)

#: ISO 2768-2 table 3 - symmetry, by nominal length (mm).
SYMMETRY_TABLE: tuple[Row, ...] = (
    (0.0, 100.0, {"H": 0.5, "K": 0.6, "L": 0.6}),
    (100.0, 300.0, {"H": 0.5, "K": 0.6, "L": 1.0}),
    (300.0, 1000.0, {"H": 0.5, "K": 0.8, "L": 1.5}),
    (1000.0, 3000.0, {"H": 0.5, "K": 1.0, "L": 2.0}),
)

#: ISO 2768-2 table 4 - circular run-out, independent of size (mm).
CIRCULAR_RUNOUT: dict[str, float] = {"H": 0.1, "K": 0.2, "L": 0.5}

MM_PER_INCH = 25.4


# --------------------------------------------------------------------------
# Table lookup
# --------------------------------------------------------------------------
def _lookup(table: tuple[Row, ...], size: float | None, key: str) -> float | None:
    """Row whose range contains ``size`` (ranges are 'over lower, up to upper')."""
    if size is None:
        return None
    size = abs(size)
    for index, (lower, upper, values) in enumerate(table):
        within_lower = size >= lower if index == 0 else size > lower
        if within_lower and size <= upper:
            return values.get(key)
    return None


def linear_deviation(nominal_mm: float | None, cls: LinearClass) -> float | None:
    """Permissible deviation (±, mm) for a linear dimension, ISO 2768-1 table 1."""
    return _lookup(LINEAR_TABLE, nominal_mm, cls.value)


def broken_edge_deviation(nominal_mm: float | None, cls: LinearClass) -> float | None:
    """Permissible deviation (±, mm) for external radii and chamfer heights."""
    return _lookup(BROKEN_EDGE_TABLE, nominal_mm, cls.value)


def angular_deviation(shorter_side_mm: float | None, cls: LinearClass) -> float | None:
    """Permissible angular deviation in degrees, ISO 2768-1 table 3.

    The table is indexed by the length of the *shorter side* of the angle,
    which a 2D callout does not state.  When it is unknown the first row is
    used: it carries the largest deviation, so "is this explicit tolerance
    looser than the general one?" stays conservative.
    """
    if shorter_side_mm is None:
        return ANGULAR_TABLE[0][2].get(cls.value)
    return _lookup(ANGULAR_TABLE, shorter_side_mm, cls.value)


def straightness_flatness(length_mm: float | None, cls: GeometricClass) -> float | None:
    return _lookup(STRAIGHTNESS_FLATNESS_TABLE, length_mm, cls.value)


def perpendicularity(shorter_side_mm: float | None, cls: GeometricClass) -> float | None:
    return _lookup(PERPENDICULARITY_TABLE, shorter_side_mm, cls.value)


def symmetry(length_mm: float | None, cls: GeometricClass) -> float | None:
    return _lookup(SYMMETRY_TABLE, length_mm, cls.value)


def circular_runout(cls: GeometricClass) -> float:
    return CIRCULAR_RUNOUT[cls.value]


def geometric_limit(
    characteristic: GDTCharacteristic,
    size_mm: float | None,
    cls: GeometricClass,
    *,
    size_tolerance_mm: float | None = None,
) -> float | None:
    """General geometrical tolerance (mm) for ``characteristic``.

    Returns ``None`` where ISO 2768-2 defines no general tolerance
    (cylindricity, angularity, profile, position, total run-out): those must
    always be indicated individually.
    """
    if characteristic in {GDTCharacteristic.STRAIGHTNESS, GDTCharacteristic.FLATNESS}:
        return straightness_flatness(size_mm, cls)
    if characteristic is GDTCharacteristic.PERPENDICULARITY:
        return perpendicularity(size_mm, cls)
    if characteristic is GDTCharacteristic.SYMMETRY:
        return symmetry(size_mm, cls)
    if characteristic is GDTCharacteristic.CIRCULAR_RUNOUT:
        return circular_runout(cls)
    if characteristic is GDTCharacteristic.CIRCULARITY:
        # "equal to the diameter tolerance, but not greater than circular run-out"
        runout = circular_runout(cls)
        if size_tolerance_mm is None:
            return runout
        return min(size_tolerance_mm, runout)
    if characteristic is GDTCharacteristic.PARALLELISM:
        # "the greater of the size tolerance and the flatness/straightness tolerance"
        form = straightness_flatness(size_mm, cls)
        candidates = [value for value in (form, size_tolerance_mm) if value is not None]
        return max(candidates) if candidates else None
    if characteristic is GDTCharacteristic.CONCENTRICITY:
        # coaxiality is not defined; it may be as large as the run-out tolerance
        return circular_runout(cls)
    return None


# --------------------------------------------------------------------------
# Designation parsing
# --------------------------------------------------------------------------
_STANDARD_RE = re.compile(
    r"(ISO\s*2768|DIN\s*7168|EN\s*22768|TS\s*1990)", re.IGNORECASE
)
_PART_RE = re.compile(r"^\s*[-–/]?\s*(?:part\s*)?[12]\b", re.IGNORECASE)
_CLASS_RE = re.compile(r"^\s*[-–/]?\s*([A-Za-z]{1,2})(?:\s*[-–/]?\s*([A-Za-z]))?\b")

_LINEAR_LETTERS = {cls.value: cls for cls in LinearClass}
_GEOMETRIC_LETTERS = {cls.value: cls for cls in GeometricClass}


@dataclass(frozen=True)
class GeneralToleranceSpec:
    """A parsed general tolerance note.

    ``linear`` and ``geometric`` are independent: ``ISO 2768-m`` only fixes
    linear/angular tolerances, ``ISO 2768-mK`` fixes both.
    """

    standard: str = STANDARD_NAME
    linear: LinearClass | None = None
    geometric: GeometricClass | None = None
    unknown_letters: tuple[str, ...] = ()
    raw: str = ""

    @property
    def designation(self) -> str:
        suffix = f"{self.linear.value if self.linear else ''}{self.geometric.value if self.geometric else ''}"
        return f"{self.standard}-{suffix}" if suffix else self.standard

    @property
    def is_usable(self) -> bool:
        """True when at least one class letter was recognised."""
        return self.linear is not None or self.geometric is not None

    # -- linear / angular -------------------------------------------------
    def deviation_for(self, dim: Dimension) -> float | None:
        """Permissible deviation for ``dim``, in the dimension's own unit."""
        if self.linear is None:
            return None
        if _is_angular(dim):
            return angular_deviation(None, self.linear)
        nominal_mm = to_mm(dim.nominal, dim.units)
        if nominal_mm is None:
            return None
        table = broken_edge_deviation if _is_broken_edge(dim) else linear_deviation
        value_mm = table(nominal_mm, self.linear)
        return from_mm(value_mm, dim.units)

    def covers(self, dim: Dimension) -> bool:
        """False when the tables do not reach this nominal size or class.

        Class ``f`` above 2000 mm, anything below 0.5 mm and anything above
        4000 mm fall outside ISO 2768-1 and must be toleranced individually.
        """
        return self.deviation_for(dim) is not None

    def tolerance_for(self, dim: Dimension):
        """The general tolerance as a :class:`Tolerance`, or ``None``."""
        from catia_diff.models.drawing import Tolerance, ToleranceKind

        deviation = self.deviation_for(dim)
        if deviation is None:
            return None
        return Tolerance(
            kind=ToleranceKind.GENERAL,
            upper=deviation,
            lower=-deviation,
            general_class=self.designation,
            raw=self.raw or self.designation,
        )

    # -- geometrical ------------------------------------------------------
    def geometric_limit_mm(
        self,
        characteristic: GDTCharacteristic,
        size_mm: float | None,
        *,
        size_tolerance_mm: float | None = None,
    ) -> float | None:
        if self.geometric is None:
            return None
        return geometric_limit(
            characteristic, size_mm, self.geometric, size_tolerance_mm=size_tolerance_mm
        )


def parse_designation(text: str | None) -> GeneralToleranceSpec | None:
    """Parse ``ISO 2768-mK`` and its relatives out of a note.

    Class letters are unambiguous across the two parts (``f m c v`` are linear,
    ``H K L`` geometric), so the letters may appear in any order or case.
    """
    if not text:
        return None
    match = _STANDARD_RE.search(text)
    if match is None:
        return None
    standard = re.sub(r"\s+", " ", match.group(1)).upper()
    tail = text[match.end(): match.end() + 16]
    tail = _PART_RE.sub("", tail, count=1)

    linear: LinearClass | None = None
    geometric: GeometricClass | None = None
    unknown: list[str] = []
    letters_match = _CLASS_RE.match(tail)
    if letters_match:
        letters = "".join(group for group in letters_match.groups() if group)
        for letter in letters:
            if letter.lower() in _LINEAR_LETTERS and linear is None:
                linear = _LINEAR_LETTERS[letter.lower()]
            elif letter.upper() in _GEOMETRIC_LETTERS and geometric is None:
                geometric = _GEOMETRIC_LETTERS[letter.upper()]
            else:
                unknown.append(letter)
    return GeneralToleranceSpec(
        standard=standard,
        linear=linear,
        geometric=geometric,
        unknown_letters=tuple(unknown),
        raw=re.sub(r"\s+", " ", text).strip(),
    )


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------
def to_mm(value: float | None, units: Units) -> float | None:
    """Convert a length to millimetres; ``None`` for non-length units."""
    if value is None:
        return None
    if units is Units.MM:
        return value
    if units is Units.CM:
        return value * 10.0
    if units is Units.M:
        return value * 1000.0
    if units is Units.INCH:
        return value * MM_PER_INCH
    return None


def from_mm(value_mm: float | None, units: Units) -> float | None:
    if value_mm is None:
        return None
    if units is Units.MM:
        return value_mm
    if units is Units.CM:
        return value_mm / 10.0
    if units is Units.M:
        return value_mm / 1000.0
    if units is Units.INCH:
        return value_mm / MM_PER_INCH
    return value_mm


def _is_angular(dim: Dimension) -> bool:
    return dim.kind is DimensionKind.ANGULAR or dim.units is Units.DEG


def _is_broken_edge(dim: Dimension) -> bool:
    """Radii and chamfers follow ISO 2768-1 table 2.

    The standard names "external radii and chamfer heights"; whether a radius
    is external is not knowable from a callout, so every radius/chamfer is
    evaluated against that table.
    """
    return dim.kind in {DimensionKind.RADIAL, DimensionKind.CHAMFER} or dim.prefix in {"R", "SR"}
