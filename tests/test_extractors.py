import pytest

from catia_diff.config import AuditConfig
from catia_diff.errors import UnsupportedFormatError
from catia_diff.extract.registry import get_extractor
from catia_diff.extract.title_block_scan import TextItem, scan_title_block, title_block_region
from catia_diff.fields import canonical_field, fold
from catia_diff.models.drawing import DimensionKind, SourceFormat, ToleranceKind
from catia_diff.models.geometry import BBox, CoordinateSpace


# ------------------------------------------------------------------ registry
def test_registry_picks_the_extractor_by_suffix(tmp_path):
    assert get_extractor(tmp_path / "a.dxf").name == "dxf"
    assert get_extractor(tmp_path / "a.pdf").name == "pdf"
    assert get_extractor(tmp_path / "a.PNG").name == "image"


def test_registry_explains_dwg_and_unknown_formats(tmp_path):
    with pytest.raises(UnsupportedFormatError, match="ODA File Converter"):
        get_extractor(tmp_path / "a.dwg")
    with pytest.raises(UnsupportedFormatError):
        get_extractor(tmp_path / "a.step")


# ------------------------------------------------------------------- fields
def test_canonical_field_handles_languages_and_noise():
    assert canonical_field("ÖLÇEK") == "scale"
    assert canonical_field("Malzeme") == "material"
    assert canonical_field("DRAWING NO.") == "drawing_number"
    assert canonical_field("ÇİZEN") == "drawn_by"
    assert canonical_field("MALZEME / MATERIAL") == "material"
    assert canonical_field("random text") is None
    assert fold("ÖLÇEK") == "olcek"


# --------------------------------------------------------- title block scan
def test_scan_title_block_reads_inline_pairs():
    sheet_box = BBox(x0=0, y0=0, x1=400, y1=300)
    items = [
        TextItem(text="OLCEK: 1:2", bbox=BBox(x0=250, y0=260, x1=330, y1=270)),
        TextItem(text="MALZEME: S235JR", bbox=BBox(x0=250, y0=275, x1=340, y1=285)),
    ]
    block = scan_title_block(items, sheet_box, CoordinateSpace.Y_DOWN)
    assert block.value("scale") == "1:2"
    assert block.value("material") == "S235JR"
    assert block.detected


def test_scan_title_block_pairs_label_with_neighbour():
    sheet_box = BBox(x0=0, y0=0, x1=400, y1=300)
    items = [
        TextItem(text="MALZEME", bbox=BBox(x0=250, y0=260, x1=290, y1=270)),
        TextItem(text="S235JR", bbox=BBox(x0=295, y0=260, x1=340, y1=270)),
        TextItem(text="1 / 2", bbox=BBox(x0=350, y0=280, x1=380, y1=290)),
    ]
    block = scan_title_block(items, sheet_box, CoordinateSpace.Y_DOWN)
    assert block.value("material") == "S235JR"
    assert block.value("sheet") == "1 / 2"


def test_title_block_region_follows_the_axis_direction():
    sheet_box = BBox(x0=0, y0=0, x1=100, y1=100)
    down = title_block_region(sheet_box, CoordinateSpace.Y_DOWN)
    up = title_block_region(sheet_box, CoordinateSpace.Y_UP)
    assert down.y1 == 100 and up.y0 == 0
    assert down.x0 == up.x0 == 55


# ---------------------------------------------------------------------- DXF
def test_dxf_extraction_of_the_sample_drawing(sample_dxf):
    document = get_extractor(sample_dxf).extract(sample_dxf, AuditConfig())
    assert document.source_format is SourceFormat.DXF
    sheet = document.sheets[0]

    assert sheet.coordinate_space is CoordinateSpace.Y_UP
    assert len(sheet.dimensions) == 9
    assert {dim.kind for dim in sheet.dimensions} >= {
        DimensionKind.LINEAR,
        DimensionKind.DIAMETER,
    }
    circles = [f for f in sheet.features if f.radius]
    assert len(circles) == 4

    override = next(dim for dim in sheet.dimensions if dim.is_text_override)
    assert override.nominal == 25.0 and override.measured == pytest.approx(30.0)

    inverted = next(
        dim for dim in sheet.dimensions if dim.tolerance.kind is not ToleranceKind.NONE
    )
    assert inverted.tolerance.is_inverted

    assert {gtol.characteristic.value for gtol in sheet.geometric_tolerances} == {
        "position",
        "flatness",
    }
    assert [datum.label for datum in sheet.datums] == ["A"]
    assert sheet.surface_finishes and sheet.surface_finishes[0].ra is None

    assert sheet.title_block.detected
    assert sheet.title_block.value("drawing_number") == "TD-1001"
    assert sheet.title_block.value("material") == "TBD"
    assert sheet.title_block.value("scale") == "1:2"


def test_dxf_dimensions_carry_their_measurement_interval(sample_dxf):
    document = get_extractor(sample_dxf).extract(sample_dxf, AuditConfig())
    horizontal = [
        dim
        for dim in document.sheets[0].dimensions
        if dim.extra.get("axis") == 0.0 and dim.extra.get("interval")
    ]
    intervals = sorted(tuple(dim.extra["interval"]) for dim in horizontal)
    assert (0.0, 80.0) in intervals
    assert (0.0, 12.0) in intervals


# ---------------------------------------------------------------------- PDF
@pytest.fixture
def vector_pdf(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "TD-2002.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=420)
    page.insert_text((80, 120), "20 +0.2/-0.1", fontsize=9)
    page.insert_text((160, 120), "R5", fontsize=9)
    page.insert_text((240, 120), "Ra 3.2", fontsize=9)
    page.insert_text((80, 160), "ALL DIMENSIONS IN MM", fontsize=8)
    page.insert_text((400, 360), "SCALE: 1:2", fontsize=8)
    page.insert_text((400, 375), "MATERIAL: S235JR", fontsize=8)
    page.insert_text((400, 390), "DRAWING NO: TD-2002", fontsize=8)
    page.draw_circle((300, 250), 20)
    doc.save(path)
    doc.close()
    return path


def test_pdf_extraction_reads_text_and_title_block(vector_pdf):
    document = get_extractor(vector_pdf).extract(vector_pdf, AuditConfig())
    sheet = document.sheets[0]
    assert not sheet.needs_vision
    assert sheet.coordinate_space is CoordinateSpace.Y_DOWN
    assert sheet.width == pytest.approx(595, abs=1)

    values = {dim.nominal for dim in sheet.dimensions}
    assert 20.0 in values and 5.0 in values
    toleranced = next(dim for dim in sheet.dimensions if dim.nominal == 20.0)
    assert toleranced.tolerance.kind is ToleranceKind.DEVIATION

    assert sheet.surface_finishes and sheet.surface_finishes[0].ra == 3.2
    assert sheet.title_block.value("scale") == "1:2"
    assert sheet.title_block.value("material") == "S235JR"
    assert sheet.units.value == "mm"


def test_pdf_without_text_is_marked_for_vision(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=420)
    page.draw_rect(pymupdf.Rect(50, 50, 500, 350))
    doc.save(path)
    doc.close()

    document = get_extractor(path).extract(path, AuditConfig())
    assert document.sheets[0].needs_vision
    assert document.source_format is SourceFormat.PDF_RASTER


# -------------------------------------------------------------------- image
def test_image_extractor_marks_the_sheet_for_vision(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    path = tmp_path / "scan.png"
    Image.new("RGB", (1200, 800), "white").save(path)

    document = get_extractor(path).extract(path, AuditConfig())
    sheet = document.sheets[0]
    assert sheet.needs_vision and sheet.raster_path == path
    assert (sheet.width, sheet.height) == (1200.0, 800.0)
