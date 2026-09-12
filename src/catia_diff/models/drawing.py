"""Structured representation of a 2D technical drawing.

This module is the contract between the extraction agents (which know about
DXF entities, PDF text spans and Claude Vision responses) and the checker
agents (which only reason about dimensions, tolerances and symbols).  Nothing
below imports a CAD library on purpose.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from catia_diff.models.geometry import BBox, CoordinateSpace, Point2D


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------
class SourceFormat(str, Enum):
    DXF = "dxf"
    DWG = "dwg"
    PDF_VECTOR = "pdf_vector"
    PDF_RASTER = "pdf_raster"
    IMAGE = "image"
    SYNTHETIC = "synthetic"


class Units(str, Enum):
    MM = "mm"
    CM = "cm"
    M = "m"
    INCH = "in"
    DEG = "deg"
    UNKNOWN = "unknown"

    @property
    def is_length(self) -> bool:
        return self in {Units.MM, Units.CM, Units.M, Units.INCH}


class DimensionKind(str, Enum):
    LINEAR = "linear"
    ALIGNED = "aligned"
    ANGULAR = "angular"
    RADIAL = "radial"
    DIAMETER = "diameter"
    ORDINATE = "ordinate"
    ARC_LENGTH = "arc_length"
    CHAMFER = "chamfer"
    THREAD = "thread"
    UNKNOWN = "unknown"

    @property
    def is_linear_like(self) -> bool:
        return self in {DimensionKind.LINEAR, DimensionKind.ALIGNED, DimensionKind.ORDINATE}


class ToleranceKind(str, Enum):
    NONE = "none"  # no tolerance carried by the dimension itself
    SYMMETRIC = "symmetric"  # 20 ±0.1
    DEVIATION = "deviation"  # 20 +0.2 / -0.1
    LIMITS = "limits"  # 20.2 / 19.9
    FIT_CLASS = "fit_class"  # 20 H7
    GENERAL = "general"  # covered by a general tolerance note
    BASIC = "basic"  # theoretically exact, [20]
    REFERENCE = "reference"  # auxiliary, (20)


class GDTCharacteristic(str, Enum):
    STRAIGHTNESS = "straightness"
    FLATNESS = "flatness"
    CIRCULARITY = "circularity"
    CYLINDRICITY = "cylindricity"
    PROFILE_LINE = "profile_line"
    PROFILE_SURFACE = "profile_surface"
    PERPENDICULARITY = "perpendicularity"
    ANGULARITY = "angularity"
    PARALLELISM = "parallelism"
    POSITION = "position"
    CONCENTRICITY = "concentricity"
    SYMMETRY = "symmetry"
    CIRCULAR_RUNOUT = "circular_runout"
    TOTAL_RUNOUT = "total_runout"

    @property
    def is_form(self) -> bool:
        """Form characteristics are datum-less by definition (ISO 1101 / Y14.5)."""
        return self in {
            GDTCharacteristic.STRAIGHTNESS,
            GDTCharacteristic.FLATNESS,
            GDTCharacteristic.CIRCULARITY,
            GDTCharacteristic.CYLINDRICITY,
        }

    @property
    def requires_datum(self) -> bool:
        """Orientation, location and runout characteristics need a datum reference."""
        return self in {
            GDTCharacteristic.PERPENDICULARITY,
            GDTCharacteristic.ANGULARITY,
            GDTCharacteristic.PARALLELISM,
            GDTCharacteristic.POSITION,
            GDTCharacteristic.CONCENTRICITY,
            GDTCharacteristic.SYMMETRY,
            GDTCharacteristic.CIRCULAR_RUNOUT,
            GDTCharacteristic.TOTAL_RUNOUT,
        }

    @property
    def symbol(self) -> str:
        return _GDT_SYMBOLS[self]


_GDT_SYMBOLS: dict[GDTCharacteristic, str] = {
    GDTCharacteristic.STRAIGHTNESS: "—",
    GDTCharacteristic.FLATNESS: "⏥",
    GDTCharacteristic.CIRCULARITY: "○",
    GDTCharacteristic.CYLINDRICITY: "⌭",
    GDTCharacteristic.PROFILE_LINE: "⌒",
    GDTCharacteristic.PROFILE_SURFACE: "⌓",
    GDTCharacteristic.PERPENDICULARITY: "⊥",
    GDTCharacteristic.ANGULARITY: "∠",
    GDTCharacteristic.PARALLELISM: "∥",
    GDTCharacteristic.POSITION: "⌖",
    GDTCharacteristic.CONCENTRICITY: "◎",
    GDTCharacteristic.SYMMETRY: "⌯",
    GDTCharacteristic.CIRCULAR_RUNOUT: "↗",
    GDTCharacteristic.TOTAL_RUNOUT: "⌰",
}


class MaterialCondition(str, Enum):
    MMC = "mmc"  # Ⓜ
    LMC = "lmc"  # Ⓛ
    RFS = "rfs"  # regardless of feature size (default)


class ProjectionMethod(str, Enum):
    FIRST_ANGLE = "first_angle"
    THIRD_ANGLE = "third_angle"
    UNKNOWN = "unknown"


class GeometryKind(str, Enum):
    CIRCLE = "circle"
    ARC = "arc"
    LINE = "line"
    POLYLINE = "polyline"
    SPLINE = "spline"
    ELLIPSE = "ellipse"
    HATCH = "hatch"
    OTHER = "other"


class SurfaceSymbolKind(str, Enum):
    BASIC = "basic"  # material removal unspecified
    MACHINING_REQUIRED = "machining_required"
    MACHINING_PROHIBITED = "machining_prohibited"


# --------------------------------------------------------------------------
# Value objects
# --------------------------------------------------------------------------
class Tolerance(BaseModel):
    """Tolerance attached to a dimension."""

    model_config = ConfigDict(frozen=True)

    kind: ToleranceKind = ToleranceKind.NONE
    upper: float | None = None
    lower: float | None = None
    fit_class: str | None = None  # H7, g6, ...
    general_class: str | None = None  # ISO 2768-mK, ...
    raw: str | None = None

    @property
    def is_specified(self) -> bool:
        return self.kind not in {ToleranceKind.NONE}

    @property
    def span(self) -> float | None:
        """Total tolerance zone width, when numerically known."""
        if self.upper is None or self.lower is None:
            return None
        return self.upper - self.lower

    @property
    def is_inverted(self) -> bool:
        return self.upper is not None and self.lower is not None and self.upper < self.lower

    @property
    def is_zero_width(self) -> bool:
        span = self.span
        return span is not None and abs(span) < 1e-12

    def decimals(self) -> int:
        """Number of decimals used by the tolerance literal."""
        best = 0
        for value in (self.upper, self.lower):
            if value is None:
                continue
            text = f"{value!r}"
            if "." in text:
                best = max(best, len(text.split(".")[1].rstrip("0")))
        return best


class DatumRef(BaseModel):
    """Reference to a datum inside a feature control frame."""

    model_config = ConfigDict(frozen=True)

    label: str
    material_condition: MaterialCondition = MaterialCondition.RFS

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        suffix = {"mmc": "Ⓜ", "lmc": "Ⓛ", "rfs": ""}[self.material_condition.value]
        return f"{self.label}{suffix}"


class DrawingObject(BaseModel):
    """Common base for anything that is placed on a sheet."""

    id: str
    bbox: BBox | None = None
    layer: str | None = None
    view_id: str | None = None
    source_handle: str | None = None  # DXF handle / PDF span index / vision id
    confidence: float = 1.0
    extra: dict[str, Any] = Field(default_factory=dict)


class Dimension(DrawingObject):
    """A dimension callout (value + tolerance + placement)."""

    kind: DimensionKind = DimensionKind.UNKNOWN
    nominal: float | None = None
    measured: float | None = None  # geometric measurement, when the source knows it
    text: str = ""
    units: Units = Units.UNKNOWN
    tolerance: Tolerance = Field(default_factory=Tolerance)
    prefix: str | None = None  # ⌀, R, SR, □, M
    decimals: int | None = None
    is_reference: bool = False  # (20)
    is_basic: bool = False  # [20]
    is_text_override: bool = False  # dimension text does not follow the geometry
    feature_ref: str | None = None  # id of the GeometryFeature it dimensions

    @property
    def has_tolerance(self) -> bool:
        return self.tolerance.is_specified or self.is_basic or self.is_reference

    @property
    def is_angular(self) -> bool:
        return self.kind is DimensionKind.ANGULAR or self.units is Units.DEG

    @property
    def override_delta(self) -> float | None:
        """Absolute difference between the printed value and the measured value."""
        if self.nominal is None or self.measured is None:
            return None
        return abs(self.nominal - self.measured)

    def label(self) -> str:
        if self.text:
            return self.text
        if self.nominal is None:
            return self.id
        return f"{self.prefix or ''}{self.nominal:g}"


class GeometricTolerance(DrawingObject):
    """A feature control frame (GD&T)."""

    characteristic: GDTCharacteristic
    value: float | None = None
    diametral_zone: bool = False  # ⌀ inside the tolerance compartment
    material_condition: MaterialCondition = MaterialCondition.RFS
    datums: list[DatumRef] = Field(default_factory=list)
    feature_ref: str | None = None
    attached_dimension_id: str | None = None
    raw: str = ""

    def label(self) -> str:
        datums = "|".join(str(d) for d in self.datums)
        value = "?" if self.value is None else f"{self.value:g}"
        zone = "⌀" if self.diametral_zone else ""
        base = f"{self.characteristic.symbol}|{zone}{value}"
        return f"{base}|{datums}" if datums else base


class DatumFeature(DrawingObject):
    """A datum feature symbol (-A-, [A])."""

    label: str
    attached_to: str | None = None


class SurfaceFinish(DrawingObject):
    symbol_kind: SurfaceSymbolKind = SurfaceSymbolKind.BASIC
    ra: float | None = None
    rz: float | None = None
    process: str | None = None
    raw: str = ""


class WeldSymbol(DrawingObject):
    weld_type: str | None = None  # fillet, square, V, ...
    size: float | None = None
    length: float | None = None
    pitch: float | None = None
    all_around: bool = False
    field_weld: bool = False
    raw: str = ""


class Annotation(DrawingObject):
    """Free text / general note."""

    text: str = ""
    category: str = "note"  # note, general_tolerance, revision, unknown


class GeometryFeature(DrawingObject):
    """A geometric entity that may require dimensioning (hole, arc, edge...)."""

    kind: GeometryKind = GeometryKind.OTHER
    center: Point2D | None = None
    radius: float | None = None
    length: float | None = None
    closed: bool = False
    #: vertices of a line/polyline; used to draw the sheet preview
    points: list[Point2D] = Field(default_factory=list)

    @property
    def diameter(self) -> float | None:
        return None if self.radius is None else self.radius * 2.0


class View(DrawingObject):
    label: str | None = None
    scale: float | None = None
    is_section: bool = False
    is_detail: bool = False
    #: ids of the objects that belong to this view (filled by view segmentation)
    member_ids: list[str] = Field(default_factory=list)

    @property
    def is_geometric(self) -> bool:
        """True when the view came from geometry clustering, not from a caption."""
        return bool(self.member_ids)


class TitleBlockField(BaseModel):
    name: str
    value: str | None = None
    bbox: BBox | None = None
    confidence: float = 1.0

    @property
    def is_empty(self) -> bool:
        return self.value is None or not self.value.strip()

    @property
    def is_placeholder(self) -> bool:
        if self.is_empty:
            return False
        token = re.sub(r"[\s._-]", "", str(self.value)).upper()
        return token in _PLACEHOLDER_TOKENS or bool(re.fullmatch(r"[X?*]{2,}", token))


_PLACEHOLDER_TOKENS = {
    "TBD",
    "TBA",
    "NA",
    "N/A",
    "XXX",
    "XX",
    "???",
    "??",
    "TODO",
    "DUMMY",
    "SAMPLE",
    "YOK",
    "BELIRSIZ",
    "DOLDURULACAK",
}


class TitleBlock(BaseModel):
    """Normalised title block: canonical field name -> field."""

    fields: dict[str, TitleBlockField] = Field(default_factory=dict)
    bbox: BBox | None = None
    detected: bool = False
    source: str = "unknown"  # block_attributes, text_scan, vision

    def get(self, name: str) -> TitleBlockField | None:
        return self.fields.get(name)

    def value(self, name: str) -> str | None:
        field = self.fields.get(name)
        if field is None or field.is_empty:
            return None
        return str(field.value).strip()

    def has(self, name: str) -> bool:
        return self.value(name) is not None

    def set(self, name: str, value: str | None, bbox: BBox | None = None, confidence: float = 1.0):
        self.fields[name] = TitleBlockField(
            name=name, value=value, bbox=bbox, confidence=confidence
        )


class Sheet(BaseModel):
    """One sheet / page / layout of a drawing."""

    index: int = 0
    name: str = ""
    width: float = 0.0
    height: float = 0.0
    units: Units = Units.UNKNOWN
    coordinate_space: CoordinateSpace = CoordinateSpace.Y_DOWN
    #: lower-left corner of the sheet expressed in the source coordinate system
    #: (non-zero for DXF model space, where drawings sit anywhere on the plane)
    origin: Point2D = Point2D(x=0.0, y=0.0)
    scale: float | None = None
    stated_scale: str | None = None
    projection: ProjectionMethod = ProjectionMethod.UNKNOWN
    raster_path: Path | None = None
    raster_dpi: float | None = None
    #: set by the extractors when a page carries no usable text/vector layer
    needs_vision: bool = False
    text_char_count: int = 0

    title_block: TitleBlock = Field(default_factory=TitleBlock)
    views: list[View] = Field(default_factory=list)
    dimensions: list[Dimension] = Field(default_factory=list)
    geometric_tolerances: list[GeometricTolerance] = Field(default_factory=list)
    datums: list[DatumFeature] = Field(default_factory=list)
    surface_finishes: list[SurfaceFinish] = Field(default_factory=list)
    welds: list[WeldSymbol] = Field(default_factory=list)
    annotations: list[Annotation] = Field(default_factory=list)
    features: list[GeometryFeature] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def bbox(self) -> BBox:
        return BBox(
            x0=self.origin.x,
            y0=self.origin.y,
            x1=self.origin.x + self.width,
            y1=self.origin.y + self.height,
        )

    def to_local(self, box: BBox) -> BBox:
        """Translate a sheet-space box so the sheet origin becomes (0, 0)."""
        return box.translated(-self.origin.x, -self.origin.y)

    def objects(self) -> Iterator[DrawingObject]:
        yield from self.dimensions
        yield from self.geometric_tolerances
        yield from self.datums
        yield from self.surface_finishes
        yield from self.welds
        yield from self.annotations
        yield from self.features
        yield from self.views

    def find_object(self, object_id: str) -> DrawingObject | None:
        return next((obj for obj in self.objects() if obj.id == object_id), None)

    def objects_of_view(self, view_id: str) -> list[DrawingObject]:
        """Every object assigned to ``view_id`` (see :mod:`catia_diff.rules.views`)."""
        return [obj for obj in self.objects() if obj.view_id == view_id]

    def feature_by_id(self, feature_id: str) -> GeometryFeature | None:
        return next((f for f in self.features if f.id == feature_id), None)

    def datum_labels(self) -> set[str]:
        return {d.label.strip().upper() for d in self.datums if d.label}

    def notes_text(self) -> str:
        return "\n".join(a.text for a in self.annotations if a.text)

    def is_empty(self) -> bool:
        return not (
            self.dimensions
            or self.geometric_tolerances
            or self.annotations
            or self.features
            or self.title_block.fields
        )


class DrawingDocument(BaseModel):
    """A whole drawing file, after extraction."""

    source_path: Path
    source_format: SourceFormat
    sheets: list[Sheet] = Field(default_factory=list)
    units: Units = Units.UNKNOWN
    metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    extractor: str = "unknown"
    vision_used: bool = False

    @property
    def name(self) -> str:
        return self.source_path.name

    def sheet(self, index: int) -> Sheet:
        return self.sheets[index]

    def all_dimensions(self) -> Iterator[tuple[Sheet, Dimension]]:
        for sheet in self.sheets:
            for dim in sheet.dimensions:
                yield sheet, dim

    def counts(self) -> dict[str, int]:
        return {
            "sheets": len(self.sheets),
            "dimensions": sum(len(s.dimensions) for s in self.sheets),
            "geometric_tolerances": sum(len(s.geometric_tolerances) for s in self.sheets),
            "datums": sum(len(s.datums) for s in self.sheets),
            "surface_finishes": sum(len(s.surface_finishes) for s in self.sheets),
            "welds": sum(len(s.welds) for s in self.sheets),
            "annotations": sum(len(s.annotations) for s in self.sheets),
            # circles and arcs are the features a drawing must dimension;
            # lines and polylines are only kept to draw the sheet preview
            "features": sum(
                1
                for sheet in self.sheets
                for feature in sheet.features
                if feature.kind in {GeometryKind.CIRCLE, GeometryKind.ARC}
            ),
            "outline_entities": sum(
                1
                for sheet in self.sheets
                for feature in sheet.features
                if feature.kind not in {GeometryKind.CIRCLE, GeometryKind.ARC}
            ),
            "views": sum(1 for sheet in self.sheets for view in sheet.views if view.is_geometric),
        }
