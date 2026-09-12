"""Generate a sample DXF that contains deliberate, well-known drafting defects.

Run::

    python examples/generate_sample_drawing.py examples/sample_plate.dxf

The produced sheet is a 80 x 40 plate with four holes and the following
*intentional* problems, one per rule family:

==========================  ======================================================
Defect                      Expected rule
==========================  ======================================================
hole D4 never dimensioned   DIM001 undimensioned feature
20 + 40 + 20 = 80 chain     DIM003 closed dimension chain (over-dimensioning)
"25" printed on a 30 edge   DIM004 dimension text vs. geometry
no tolerance on ⌀6.5        TOL001 missing tolerance (no general tolerance note)
+0.05 / -0.10 swapped       TOL002 inverted tolerance zone
position frame -> datum B   GDT001 undefined datum reference
flatness with datum A       GDT004 form tolerance with a datum
surface symbol without Ra   SYM001 surface finish without value
MALZEME = "TBD"             TB003 placeholder left in the title block
no revision, no ISO 2768    TB002 / TB008 missing mandatory field, no general tol.
==========================  ======================================================
"""

from __future__ import annotations

import sys
from pathlib import Path

import ezdxf
from ezdxf.enums import TextEntityAlignment

PLATE_W, PLATE_H = 80.0, 40.0
HOLES = [(12.0, 10.0), (12.0, 30.0), (68.0, 10.0), (68.0, 30.0)]
HOLE_R = 3.25


def build() -> ezdxf.document.Drawing:
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 4  # millimetres
    doc.header["$LUPREC"] = 2
    msp = doc.modelspace()
    for name in ("PART", "DIMS", "GDT", "TEXT", "TITLE"):
        if name not in doc.layers:
            doc.layers.add(name)

    # -- geometry -------------------------------------------------------
    msp.add_lwpolyline(
        [(0, 0), (PLATE_W, 0), (PLATE_W, PLATE_H), (0, PLATE_H)],
        close=True,
        dxfattribs={"layer": "PART"},
    )
    for center in HOLES:
        msp.add_circle(center, HOLE_R, dxfattribs={"layer": "PART"})

    # -- dimensions -----------------------------------------------------
    style = {"layer": "DIMS"}
    # Closed chain: 12 + 56 + 12 = 80 plus the overall dimension.
    for p1, p2, y in ((( 0, 0), (12, 0), -12.0), ((12, 0), (68, 0), -12.0), ((68, 0), (80, 0), -12.0)):
        msp.add_linear_dim(base=(0, y), p1=p1, p2=p2, dxfattribs=style).render()
    msp.add_linear_dim(base=(0, -22), p1=(0, 0), p2=(PLATE_W, 0), dxfattribs=style).render()

    # Height, with an inverted tolerance zone (upper < lower).
    msp.add_linear_dim(
        base=(-14, 0),
        p1=(0, 0),
        p2=(0, PLATE_H),
        angle=90,
        override={"dimtol": 1, "dimtp": -0.10, "dimtm": -0.05},
        dxfattribs=style,
    ).render()

    # Text override: the drawing prints 25 while the geometry measures 30.
    msp.add_linear_dim(
        base=(92, 0), p1=(80, 0), p2=(80, 30), angle=90, text="25", dxfattribs=style
    ).render()

    # Hole callouts: three holes are covered, D4 (68, 30) is not.
    msp.add_diameter_dim(center=HOLES[0], radius=HOLE_R, angle=135, dxfattribs=style).render()
    msp.add_diameter_dim(center=HOLES[1], radius=HOLE_R, angle=135, dxfattribs=style).render()
    msp.add_diameter_dim(center=HOLES[2], radius=HOLE_R, angle=45, dxfattribs=style).render()

    # -- GD&T -----------------------------------------------------------
    msp.add_mtext("-A-", dxfattribs={"layer": "GDT", "char_height": 3.0}).set_location((-6, 20))
    msp.add_mtext(
        "{\\Fgdt;j}%%v{\\Fgdt;m}0.2{\\Fgdt;n}%%vA%%vB",
        dxfattribs={"layer": "GDT", "char_height": 3.0},
    ).set_location((30, 46))
    msp.add_mtext("⏥|0.05|A", dxfattribs={"layer": "GDT", "char_height": 3.0}).set_location((30, 52))

    # -- symbols and notes ----------------------------------------------
    msp.add_mtext("√", dxfattribs={"layer": "TEXT", "char_height": 3.0}).set_location((70, 46))
    msp.add_mtext(
        "NOTLAR:\\P1. KESKİN KÖŞELER KIRILACAK.\\P2. ÇAPAK ALINACAK.",
        dxfattribs={"layer": "TEXT", "char_height": 2.5},
    ).set_location((0, 60))

    # -- title block ------------------------------------------------------
    _title_block(doc, msp)
    return doc


def _title_block(doc: ezdxf.document.Drawing, msp) -> None:
    block = doc.blocks.new(name="TITLEBLOCK")
    rows = [
        ("RESIM_NO", "TD-1001", 0.0),
        ("PARCA_ADI", "BAGLANTI PLAKASI", 6.0),
        ("MALZEME", "TBD", 12.0),
        ("OLCEK", "1:2", 18.0),
        ("CIZEN", "G. BASARAN", 24.0),
        ("ONAYLAYAN", "G. BASARAN", 30.0),
        ("SAYFA", "1 / 1", 36.0),
    ]
    for tag, _value, offset in rows:
        block.add_attdef(
            tag=tag,
            insert=(offset, 0),
            height=2.5,
            dxfattribs={"layer": "TITLE"},
        ).set_placement((offset, 0), align=TextEntityAlignment.LEFT)
        block.add_text(
            tag.replace("_", " "), height=2.0, dxfattribs={"layer": "TITLE"}
        ).set_placement((offset, 4), align=TextEntityAlignment.LEFT)

    insert = msp.add_blockref("TITLEBLOCK", (95, -30), dxfattribs={"layer": "TITLE"})
    insert.add_auto_attribs({tag: value for tag, value, _ in rows})


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else Path("examples/sample_plate.dxf")
    target.parent.mkdir(parents=True, exist_ok=True)
    build().saveas(target)
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
