"""Shared fixtures.

The suite runs without any optional dependency: tests that need ezdxf,
pymupdf or pillow are skipped instead of failing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from catia_diff.config import AuditConfig, VisionConfig, VisionMode  # noqa: E402
from catia_diff.models.drawing import (  # noqa: E402
    Annotation,
    DatumFeature,
    DatumRef,
    Dimension,
    DimensionKind,
    DrawingDocument,
    GDTCharacteristic,
    GeometricTolerance,
    GeometryFeature,
    GeometryKind,
    Sheet,
    SourceFormat,
    SurfaceFinish,
    TitleBlock,
    Tolerance,
    ToleranceKind,
    Units,
)
from catia_diff.models.geometry import BBox, CoordinateSpace, Point2D  # noqa: E402
from catia_diff.rules.base import RuleContext  # noqa: E402


def box(x0: float, y0: float, w: float = 10.0, h: float = 4.0) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x0 + w, y1=y0 + h)


def make_dimension(
    dim_id: str = "DIM0001",
    *,
    nominal: float | None = 20.0,
    kind: DimensionKind = DimensionKind.LINEAR,
    tolerance: Tolerance | None = None,
    bbox: BBox | None = None,
    **kwargs,
) -> Dimension:
    return Dimension(
        id=dim_id,
        kind=kind,
        nominal=nominal,
        text=kwargs.pop("text", f"{nominal:g}" if nominal is not None else ""),
        units=kwargs.pop("units", Units.MM),
        tolerance=tolerance or Tolerance(),
        bbox=bbox or box(0, 0),
        **kwargs,
    )


def make_gtol(
    gtol_id: str = "GTOL0001",
    *,
    characteristic: GDTCharacteristic = GDTCharacteristic.POSITION,
    value: float | None = 0.2,
    datums: list[str] | None = None,
    **kwargs,
) -> GeometricTolerance:
    return GeometricTolerance(
        id=gtol_id,
        characteristic=characteristic,
        value=value,
        datums=[DatumRef(label=label) for label in (datums or [])],
        bbox=kwargs.pop("bbox", box(0, 20)),
        **kwargs,
    )


def make_circle(feature_id: str = "FEAT0001", *, x=10.0, y=10.0, r=3.25) -> GeometryFeature:
    return GeometryFeature(
        id=feature_id,
        kind=GeometryKind.CIRCLE,
        center=Point2D(x=x, y=y),
        radius=r,
        closed=True,
        bbox=BBox.around(Point2D(x=x, y=y), r),
    )


def make_sheet(**kwargs) -> Sheet:
    defaults = {
        "index": 0,
        "name": "test",
        "width": 200.0,
        "height": 140.0,
        "units": Units.MM,
        "coordinate_space": CoordinateSpace.Y_UP,
    }
    defaults.update(kwargs)
    return Sheet(**defaults)


def make_document(sheet: Sheet | None = None, **kwargs) -> DrawingDocument:
    return DrawingDocument(
        source_path=kwargs.pop("source_path", Path("TD-1001.dxf")),
        source_format=kwargs.pop("source_format", SourceFormat.DXF),
        units=Units.MM,
        sheets=[sheet or make_sheet()],
        **kwargs,
    )


def make_context(document: DrawingDocument | None = None, config: AuditConfig | None = None):
    document = document or make_document()
    return RuleContext(document, config or AuditConfig(language="en"))


def titled(**values) -> TitleBlock:
    block = TitleBlock(detected=True, source="test", bbox=box(150, 0, 50, 20))
    for name, value in values.items():
        block.set(name, value)
    return block


@pytest.fixture
def config(tmp_path) -> AuditConfig:
    return AuditConfig(
        language="en",
        output_dir=tmp_path / "reports",
        formats=("json", "md", "html"),
        vision=VisionConfig(mode=VisionMode.OFF),
    )


def _sample_builder():
    pytest.importorskip("ezdxf")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
    from generate_sample_drawing import build  # noqa: PLC0415

    return build


@pytest.fixture(scope="session")
def sample_dxf(tmp_path_factory) -> Path:
    """The example drawing with its deliberate defects."""
    target = tmp_path_factory.mktemp("drawings") / "TD-1001_sample.dxf"
    _sample_builder()().saveas(target)
    return target


@pytest.fixture(scope="session")
def sample_dxf_complete(tmp_path_factory) -> Path:
    """The same plate, dimensioned so nothing is missing - the false-positive net."""
    target = tmp_path_factory.mktemp("drawings") / "TD-1001_complete.dxf"
    _sample_builder()(complete=True).saveas(target)
    return target


@pytest.fixture(scope="session")
def sample_dxf_fits(tmp_path_factory) -> Path:
    """A fully dimensioned plate whose bores carry ISO 286 fit classes."""
    target = tmp_path_factory.mktemp("drawings") / "TD-1001_fits.dxf"
    _sample_builder()(fits=True).saveas(target)
    return target


@pytest.fixture(scope="session")
def sample_dxf_views(tmp_path_factory) -> Path:
    """Three orthographic views carrying two cross-view defects."""
    target = tmp_path_factory.mktemp("drawings") / "TD-1001_views.dxf"
    _sample_builder()(views=True).saveas(target)
    return target


@pytest.fixture(scope="session")
def sample_dxf_views_correct(tmp_path_factory) -> Path:
    """The same three views, consistent - the cross-view false-positive net."""
    target = tmp_path_factory.mktemp("drawings") / "TD-1001_views_ok.dxf"
    _sample_builder()(views=True, complete=True).saveas(target)
    return target


@pytest.fixture(scope="session")
def sample_dxf_flange(tmp_path_factory) -> Path:
    """A flange missing its thickness, its pitch circle and its chamfer size."""
    target = tmp_path_factory.mktemp("drawings") / "TD-1001_flange.dxf"
    _sample_builder()(flange=True).saveas(target)
    return target


@pytest.fixture(scope="session")
def sample_dxf_flange_correct(tmp_path_factory) -> Path:
    """The same flange with all three stated - the silence net for DIM016-DIM018."""
    target = tmp_path_factory.mktemp("drawings") / "TD-1001_flange_ok.dxf"
    _sample_builder()(flange=True, complete=True).saveas(target)
    return target


@pytest.fixture(scope="session")
def sample_dxf_refs(tmp_path_factory) -> Path:
    """A plate whose cross-references do not resolve."""
    target = tmp_path_factory.mktemp("drawings") / "TD-1001_refs.dxf"
    _sample_builder()(refs=True).saveas(target)
    return target


@pytest.fixture(scope="session")
def sample_dxf_refs_correct(tmp_path_factory) -> Path:
    """The same sheet with every reference resolved - the family's silence net."""
    target = tmp_path_factory.mktemp("drawings") / "TD-1001_refs_ok.dxf"
    _sample_builder()(refs=True, complete=True).saveas(target)
    return target


@pytest.fixture(scope="session")
def sample_dxf_iso2768(tmp_path_factory) -> Path:
    """The same drawing plus an ISO 2768-mK note and the defects it exposes."""
    target = tmp_path_factory.mktemp("drawings") / "TD-1001_2768.dxf"
    _sample_builder()(with_general_tolerance=True).saveas(target)
    return target


@pytest.fixture
def annotation_factory():
    def _make(text: str, category: str = "note", **kwargs) -> Annotation:
        return Annotation(
            id=kwargs.pop("id", "NOTE0001"),
            text=text,
            category=category,
            bbox=kwargs.pop("bbox", box(0, 100)),
            **kwargs,
        )

    return _make


__all__ = [
    "Annotation",
    "DatumFeature",
    "Dimension",
    "DimensionKind",
    "SurfaceFinish",
    "Tolerance",
    "ToleranceKind",
    "box",
    "make_circle",
    "make_context",
    "make_dimension",
    "make_document",
    "make_gtol",
    "make_sheet",
    "titled",
]
