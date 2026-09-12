"""Geometric primitives shared by every agent.

All coordinates are expressed in *sheet space*: the coordinate system of the
sheet the object was extracted from.  Because DXF (y-up, model units) and
PDF/raster (y-down, points or pixels) disagree about the direction of the Y
axis, every :class:`~catia_diff.models.drawing.Sheet` declares its
:class:`CoordinateSpace` so the reporting layer can transform boxes exactly
once, at render time.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator


class CoordinateSpace(str, Enum):
    """Direction of the Y axis of a sheet."""

    Y_DOWN = "y_down"  # PDF points, raster pixels
    Y_UP = "y_up"  # DXF / CAD model space


class Point2D(BaseModel):
    model_config = ConfigDict(frozen=True)

    x: float
    y: float

    def distance_to(self, other: Point2D) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def as_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"({self.x:.3f}, {self.y:.3f})"


class BBox(BaseModel):
    """Axis aligned bounding box with normalised corner ordering."""

    model_config = ConfigDict(frozen=True)

    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, value: object) -> object:
        if isinstance(value, BBox):
            return value
        if isinstance(value, dict):
            data = dict(value)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            if len(value) != 4:
                raise ValueError("BBox sequence must contain exactly 4 numbers")
            data = dict(zip(("x0", "y0", "x1", "y1"), value, strict=True))
        else:
            return value
        try:
            x0, y0 = float(data["x0"]), float(data["y0"])
            x1, y1 = float(data["x1"]), float(data["y1"])
        except (KeyError, TypeError, ValueError):
            return data
        data["x0"], data["x1"] = min(x0, x1), max(x0, x1)
        data["y0"], data["y1"] = min(y0, y1), max(y0, y1)
        return data

    # -- derived quantities -------------------------------------------------
    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> Point2D:
        return Point2D(x=(self.x0 + self.x1) / 2.0, y=(self.y0 + self.y1) / 2.0)

    # -- operations ---------------------------------------------------------
    def expanded(self, margin: float) -> BBox:
        return BBox(
            x0=self.x0 - margin, y0=self.y0 - margin, x1=self.x1 + margin, y1=self.y1 + margin
        )

    def union(self, other: BBox) -> BBox:
        return BBox(
            x0=min(self.x0, other.x0),
            y0=min(self.y0, other.y0),
            x1=max(self.x1, other.x1),
            y1=max(self.y1, other.y1),
        )

    def intersection(self, other: BBox) -> BBox | None:
        x0, y0 = max(self.x0, other.x0), max(self.y0, other.y0)
        x1, y1 = min(self.x1, other.x1), min(self.y1, other.y1)
        if x0 >= x1 or y0 >= y1:
            return None
        return BBox(x0=x0, y0=y0, x1=x1, y1=y1)

    def intersects(self, other: BBox) -> bool:
        return self.intersection(other) is not None

    def iou(self, other: BBox) -> float:
        inter = self.intersection(other)
        if inter is None:
            return 0.0
        denom = self.area + other.area - inter.area
        return inter.area / denom if denom > 0 else 0.0

    def contains_point(self, point: Point2D) -> bool:
        return self.x0 <= point.x <= self.x1 and self.y0 <= point.y <= self.y1

    def contains(self, other: BBox) -> bool:
        return (
            self.x0 <= other.x0 and self.y0 <= other.y0 and self.x1 >= other.x1
            and self.y1 >= other.y1
        )

    def scaled(self, sx: float, sy: float | None = None) -> BBox:
        sy = sx if sy is None else sy
        return BBox(x0=self.x0 * sx, y0=self.y0 * sy, x1=self.x1 * sx, y1=self.y1 * sy)

    def translated(self, dx: float, dy: float) -> BBox:
        return BBox(x0=self.x0 + dx, y0=self.y0 + dy, x1=self.x1 + dx, y1=self.y1 + dy)

    def flipped_y(self, sheet_height: float) -> BBox:
        """Convert between Y_UP and Y_DOWN spaces of a sheet of ``sheet_height``."""
        return BBox(
            x0=self.x0, y0=sheet_height - self.y1, x1=self.x1, y1=sheet_height - self.y0
        )

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    # -- constructors -------------------------------------------------------
    @classmethod
    def from_points(cls, points: Iterable[Point2D | Sequence[float]]) -> BBox:
        xs: list[float] = []
        ys: list[float] = []
        for p in points:
            if isinstance(p, Point2D):
                xs.append(p.x)
                ys.append(p.y)
            else:
                xs.append(float(p[0]))
                ys.append(float(p[1]))
        if not xs:
            raise ValueError("cannot build a BBox from an empty point set")
        return cls(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))

    @classmethod
    def around(cls, point: Point2D, half_width: float, half_height: float | None = None) -> BBox:
        half_height = half_width if half_height is None else half_height
        return cls(
            x0=point.x - half_width,
            y0=point.y - half_height,
            x1=point.x + half_width,
            y1=point.y + half_height,
        )

    @classmethod
    def from_normalised(cls, box: Sequence[float], width: float, height: float) -> BBox:
        """Build a sheet-space box from a ``[0, 1]`` normalised box (vision output)."""
        if len(box) != 4:
            raise ValueError("normalised box must contain exactly 4 numbers")
        return cls(
            x0=float(box[0]) * width,
            y0=float(box[1]) * height,
            x1=float(box[2]) * width,
            y1=float(box[3]) * height,
        )
