"""Rasterisation and image pre-processing for the vision path.

Everything in this module degrades gracefully: OpenCV and Pillow are optional,
and when they are missing the pipeline still works with the untouched page
image (only the clean-up steps are skipped).
"""

from __future__ import annotations

import base64
import shutil
from dataclasses import dataclass
from pathlib import Path

from catia_diff.errors import MissingDependencyError
from catia_diff.models.geometry import BBox

MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}
IMAGE_SUFFIXES = tuple(MEDIA_TYPES)


@dataclass(frozen=True)
class Tile:
    """One image tile plus its placement inside the source image (pixels)."""

    path: Path
    source_box: BBox
    index: int = 0

    @property
    def width(self) -> float:
        return self.source_box.width

    @property
    def height(self) -> float:
        return self.source_box.height


def render_pdf_page(pdf_path: Path, page_index: int, dpi: int, out_dir: Path) -> Path:
    """Render one PDF page to PNG and return the written path."""
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - depends on the install
        try:
            import fitz as pymupdf  # type: ignore[no-redef]
        except ImportError as exc:
            raise MissingDependencyError("pymupdf", "PDF rasterisation", extra="pdf") from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{pdf_path.stem}_p{page_index + 1}_{dpi}dpi.png"
    with pymupdf.open(str(pdf_path)) as doc:
        page = doc[page_index]
        pixmap = page.get_pixmap(dpi=dpi, alpha=False)
        pixmap.save(str(out_path))
    return out_path


def image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise MissingDependencyError("pillow", "image handling", extra="raster") from exc
    with Image.open(path) as img:
        return img.width, img.height


def preprocess(path: Path, out_dir: Path, *, deskew: bool = True, denoise: bool = True) -> Path:
    """Clean up a scanned sheet (grayscale, denoise, deskew, binarise).

    Falls back to a plain copy when OpenCV is unavailable.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{path.stem}_prep.png"
    try:
        import cv2
        import numpy as np
    except ImportError:
        if out_path != path:
            shutil.copyfile(path, out_path)
        return out_path

    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        shutil.copyfile(path, out_path)
        return out_path
    if denoise:
        image = cv2.fastNlMeansDenoising(image, h=7)
    binary = cv2.adaptiveThreshold(
        image, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )
    if deskew:
        angle = _skew_angle(binary, cv2, np)
        if abs(angle) > 0.1:
            h, w = binary.shape
            matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
            binary = cv2.warpAffine(
                binary, matrix, (w, h), flags=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_REPLICATE,
            )
    cv2.imwrite(str(out_path), binary)
    return out_path


def _skew_angle(binary, cv2, np) -> float:
    inverted = 255 - binary
    coords = cv2.findNonZero(inverted)
    if coords is None or len(coords) < 100:
        return 0.0
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle += 90
    elif angle > 45:
        angle -= 90
    return float(angle) if abs(angle) < 15 else 0.0


def tile_image(path: Path, out_dir: Path, *, max_tiles: int = 4, overlap: float = 0.08) -> list[Tile]:
    """Split a large sheet into overlapping tiles so small text stays legible."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise MissingDependencyError("pillow", "image tiling", extra="raster") from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(path) as img:
        width, height = img.width, img.height
        if max_tiles <= 1:
            return [Tile(path=path, source_box=BBox(x0=0, y0=0, x1=width, y1=height), index=0)]
        cols, rows = _grid_for(max_tiles, width, height)
        if cols * rows <= 1:
            return [Tile(path=path, source_box=BBox(x0=0, y0=0, x1=width, y1=height), index=0)]

        tiles: list[Tile] = []
        tile_w, tile_h = width / cols, height / rows
        pad_x, pad_y = tile_w * overlap, tile_h * overlap
        index = 0
        for row in range(rows):
            for col in range(cols):
                x0 = max(0, int(col * tile_w - pad_x))
                y0 = max(0, int(row * tile_h - pad_y))
                x1 = min(width, int((col + 1) * tile_w + pad_x))
                y1 = min(height, int((row + 1) * tile_h + pad_y))
                crop = img.crop((x0, y0, x1, y1))
                tile_path = out_dir / f"{path.stem}_t{index}.png"
                crop.save(tile_path)
                tiles.append(
                    Tile(path=tile_path, source_box=BBox(x0=x0, y0=y0, x1=x1, y1=y1), index=index)
                )
                index += 1
        return tiles


def _grid_for(max_tiles: int, width: int, height: int) -> tuple[int, int]:
    aspect = width / height if height else 1.0
    best = (1, 1)
    for cols in range(1, max_tiles + 1):
        for rows in range(1, max_tiles + 1):
            if cols * rows > max_tiles:
                continue
            if cols * rows <= best[0] * best[1]:
                continue
            # prefer a grid whose cells stay close to square
            cell_aspect = (width / cols) / (height / rows)
            if 0.5 <= cell_aspect / max(aspect, 1e-6) <= 2.0 or cols * rows == max_tiles:
                best = (cols, rows)
    return best


def encode_image(path: Path) -> tuple[str, str]:
    """Return ``(media_type, base64_data)`` for an image file."""
    media_type = MEDIA_TYPES.get(path.suffix.lower(), "image/png")
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return media_type, data
