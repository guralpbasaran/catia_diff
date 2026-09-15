"""Generate a sample *vector* PDF - a CAD export, not a scan.

Run::

    python examples/generate_sample_pdf.py examples/sample_plate.pdf
    python examples/generate_sample_pdf.py examples/sample_plate_ok.pdf --complete

The point of this fixture is that a PDF page carries no entities: there is no
"dimension" object, only stroked paths and a text layer, and no millimetres,
only points.  It is drawn the way a CAD exporter draws - extension lines, a
dimension line between two solid arrow heads, the number written above it - so
that :mod:`catia_diff.extract.pdf_vector` has to recover the same drawing the
DXF sample states outright.

The sheet is the same 80 x 40 plate as ``sample_plate.dxf``:

==========================  ======================================================
Defect                      Expected rule
==========================  ======================================================
holes at y=10 and y=30      DIM011 feature position never dimensioned (Y axis)
12 + 56 + 12 = 80 chain     DIM003 closed dimension chain (over-dimensioning)
same chain against ±0.3     TOL010 stack-up exceeds the overall tolerance
==========================  ======================================================

``--complete`` dimensions the plate properly - 12 / 56 from the left edge plus
the overall 80, and 10 / 10 off both ends of the 40 - and is the silence net:
the coverage rules must report nothing on it.  It is the harder half of the
fixture, because every stroke that produced a finding above is still on the
page; only their arrangement changed.

Needs ``pymupdf``: ``pip install -e ".[pdf]"``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

#: Points per millimetre - a 1:1 drawing plotted at 3x, so the recovered scale
#: has to come out at exactly one third of a millimetre per point.
SCALE_PT_PER_MM = 3.0

#: Where the part's origin sits on the page.  PDF y grows downwards.
ORIGIN_PT = (140.0, 420.0)

#: Plate outline and hole pattern, in millimetres.
PLATE = (80.0, 40.0)
HOLES = ((12.0, 10.0), (12.0, 30.0), (68.0, 10.0), (68.0, 30.0))
HOLE_RADIUS = 3.25

_ARROW = 3.0  # arrow head half-length, points
_TICK = 2.0   # how far an extension line overshoots the dimension line


def _page_point(x_mm: float, y_mm: float) -> pymupdf.Point:
    return pymupdf.Point(
        ORIGIN_PT[0] + x_mm * SCALE_PT_PER_MM,
        ORIGIN_PT[1] - y_mm * SCALE_PT_PER_MM,
    )


class _Dimensioner:
    """Draws ISO-style linear dimensions and remembers where the text goes.

    The text cannot be written while the shape is open - a ``Shape`` has to be
    committed before ``insert_text`` - so the callouts are collected here and
    written once the geometry is on the page.
    """

    def __init__(self, shape: pymupdf.Shape) -> None:
        self.shape = shape
        self.texts: list[tuple[pymupdf.Point, str]] = []

    def horizontal(self, x0: float, x1: float, y: float, offset: float, text: str) -> None:
        start, end = _page_point(x0, y - offset), _page_point(x1, y - offset)
        for x in (x0, x1):  # extension lines, from the part out past the line
            self.shape.draw_line(_page_point(x, y), _page_point(x, y - offset - _TICK))
        self._between(start, end, text, vertical=False)

    def vertical(self, y0: float, y1: float, x: float, offset: float, text: str) -> None:
        start, end = _page_point(x - offset, y0), _page_point(x - offset, y1)
        for y in (y0, y1):
            self.shape.draw_line(_page_point(x, y), _page_point(x - offset - _TICK, y))
        self._between(start, end, text, vertical=True)

    def _between(
        self, start: pymupdf.Point, end: pymupdf.Point, text: str, *, vertical: bool
    ) -> None:
        self.shape.draw_line(start, end)
        for tip, direction in ((start, 1), (end, -1)):
            self.shape.draw_polyline(_arrow_head(tip, direction, vertical=vertical))
        middle = pymupdf.Point((start.x + end.x) / 2, (start.y + end.y) / 2)
        self.texts.append((pymupdf.Point(middle.x - 9, middle.y - 3), text))


def _arrow_head(tip: pymupdf.Point, direction: int, *, vertical: bool) -> list[pymupdf.Point]:
    if vertical:
        back = tip.y + _ARROW * direction
        wing = (pymupdf.Point(tip.x - 2, back), pymupdf.Point(tip.x + 2, back))
    else:
        back = tip.x + _ARROW * direction
        wing = (pymupdf.Point(back, tip.y - 2), pymupdf.Point(back, tip.y + 2))
    return [tip, wing[0], wing[1], tip]


def build(complete: bool = False) -> pymupdf.Document:
    """The sample plate as a vector PDF page."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)  # A4 portrait
    shape = page.new_shape()
    width, height = PLATE

    for a, b in (
        ((0.0, 0.0), (width, 0.0)),
        ((width, 0.0), (width, height)),
        ((width, height), (0.0, height)),
        ((0.0, height), (0.0, 0.0)),
    ):
        shape.draw_line(_page_point(*a), _page_point(*b))
    for centre in HOLES:
        shape.draw_circle(_page_point(*centre), HOLE_RADIUS * SCALE_PT_PER_MM)

    dims = _Dimensioner(shape)
    dims.horizontal(0.0, 12.0, 0.0, 12.0, "12.00")
    dims.horizontal(12.0, 68.0, 0.0, 12.0, "56.00")
    dims.vertical(0.0, height, 0.0, 14.0, "40.00")
    if complete:
        # 12 and 56 already reach the second hole column; adding the overall 80
        # closes the graph without repeating the third step of the chain.
        dims.horizontal(0.0, width, 0.0, 24.0, "80.00")
        dims.vertical(0.0, 10.0, 0.0, 26.0, "10.00")
        dims.vertical(30.0, height, 0.0, 26.0, "10.00")
    else:
        # The third step *and* the overall: one dimension too many on X, and
        # nothing at all locating the holes on Y.
        dims.horizontal(68.0, width, 0.0, 12.0, "12.00")
        dims.horizontal(0.0, width, 0.0, 24.0, "80.00")

    shape.finish(width=0.6, color=(0, 0, 0))
    shape.commit()

    for point, text in dims.texts:
        page.insert_text(point, text, fontsize=8)
    # A CAD exporter writes the diameter sign as U+00D8, which every PDF base
    # font can encode; U+2300 usually cannot be embedded and drops out.
    page.insert_text(_page_point(20.0, 34.0), "4x Ø6.5", fontsize=8)
    for offset, note in enumerate(
        ("GENEL TOLERANSLAR ISO 2768-mK", "OLCULER MM CINSINDENDIR", "KALINLIK 5")
    ):
        page.insert_text(_page_point(0.0, 52.0 - offset * 4.0), note, fontsize=8)

    _title_block(page)
    return doc


def _title_block(page: pymupdf.Page) -> None:
    frame = pymupdf.Rect(330, 690, 560, 800)
    page.draw_rect(frame, width=0.6)
    fields = (
        ("RESIM NO", "TD-1001"),
        ("PARCA ADI", "BAGLANTI PLAKASI"),
        ("MALZEME", "S235JR"),
        ("OLCEK", "1:2"),
        ("BIRIM", "mm"),
        ("REVIZYON", "A"),
        ("REVIZYON TARIHI", "2024-03-11"),
        ("CIZEN", "G.B."),
    )
    for index, (label, value) in enumerate(fields):
        page.insert_text(pymupdf.Point(336, 702 + index * 14), f"{label}: {value}", fontsize=8)


def main(argv: list[str]) -> int:
    flags = argv[1:]
    positional = [arg for arg in flags if not arg.startswith("--")]
    complete = "--complete" in flags
    target = Path(positional[0]) if positional else Path("examples/sample_plate.pdf")
    target.parent.mkdir(parents=True, exist_ok=True)
    build(complete=complete).save(target)
    print(f"wrote {target}{' (fully dimensioned)' if complete else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
