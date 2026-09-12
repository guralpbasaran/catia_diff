import pytest

from catia_diff.extract.raster import (
    IMAGE_SUFFIXES,
    encode_image,
    image_size,
    preprocess,
    render_pdf_page,
    tile_image,
)


@pytest.fixture
def sheet_png(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    path = tmp_path / "sheet.png"
    Image.new("RGB", (1200, 800), "white").save(path)
    return path


def test_image_size_and_encoding(sheet_png):
    assert image_size(sheet_png) == (1200, 800)
    media_type, data = encode_image(sheet_png)
    assert media_type == "image/png"
    assert data and "\n" not in data
    assert ".tif" in IMAGE_SUFFIXES


def test_preprocess_always_returns_a_usable_image(sheet_png, tmp_path):
    prepared = preprocess(sheet_png, tmp_path / "prep")
    assert prepared.exists()
    assert image_size(prepared)[0] > 0


def test_tiling_covers_the_sheet(sheet_png, tmp_path):
    tiles = tile_image(sheet_png, tmp_path / "tiles", max_tiles=4, overlap=0.05)
    assert 1 < len(tiles) <= 4
    assert all(tile.path.exists() for tile in tiles)

    union = tiles[0].source_box
    for tile in tiles[1:]:
        union = union.union(tile.source_box)
    assert union.as_tuple() == (0.0, 0.0, 1200.0, 800.0)
    assert any(a.source_box.intersects(b.source_box) for a in tiles for b in tiles if a is not b)


def test_single_tile_mode(sheet_png, tmp_path):
    tiles = tile_image(sheet_png, tmp_path / "one", max_tiles=1)
    assert len(tiles) == 1 and tiles[0].path == sheet_png


def test_render_pdf_page(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    pdf = tmp_path / "doc.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=420)
    page.insert_text((100, 100), "TD-1001", fontsize=12)
    doc.save(pdf)
    doc.close()

    rendered = render_pdf_page(pdf, 0, 120, tmp_path / "pages")
    assert rendered.exists()
    width, height = image_size(rendered)
    assert width > 595 and height > 420  # 120 dpi is larger than 72 dpi points
