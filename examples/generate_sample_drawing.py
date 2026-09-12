"""Generate a sample DXF that contains deliberate, well-known drafting defects.

Run::

    python examples/generate_sample_drawing.py examples/sample_plate.dxf
    python examples/generate_sample_drawing.py examples/sample_plate_2768.dxf --iso2768
    python examples/generate_sample_drawing.py examples/sample_plate_ok.dxf --complete

The produced sheet is a 80 x 40 plate with four holes and the following
*intentional* problems, one per rule family:

==========================  ======================================================

With ``--iso2768`` the sheet additionally carries a ``ISO 2768-mK`` note and the
defects that only a numeric general tolerance can expose:

==========================  ======================================================
Defect                      Expected rule
==========================  ======================================================
±1.5 on a 40 mm feature     TOL008 indicated tolerance looser than ISO 2768-m (±0.3)
±0.3 on a 56 mm feature     TOL009 indicated tolerance repeats the general one
0.3 mm dimension            TOL007 nominal size below the 0.5 mm table start
12 + 56 + 12 = 80 chain     TOL010 stack ±0.7 against an overall ±0.3
flatness 0.8                GDT011 looser than the ISO 2768-K general flatness
==========================  ======================================================

The two tolerance demos above dimension a span the base drawing already
dimensions, so the variant is over-dimensioned as well: the constraint graph
reports both duplicates as ``DIM003`` (redundant dimension). That is deliberate -
it shows the graph catching a redundancy the old contiguous-chain heuristic
could not see, because a duplicate is a cycle of length two.
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


def build(
    with_general_tolerance: bool = False, complete: bool = False
) -> ezdxf.document.Drawing:
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 4  # millimetres
    doc.header["$LUPREC"] = 2
    msp = doc.modelspace()
    for name in ("PART", "DIMS", "GDT", "TEXT", "TITLE"):
        if name not in doc.layers:
            doc.layers.add(name)

    if complete:
        _complete_plate(msp)
        _title_block(doc, msp, complete=True)
        return doc

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

    if with_general_tolerance:
        _iso2768_variant(msp)

    # -- title block ------------------------------------------------------
    _title_block(doc, msp)
    return doc


def _iso2768_variant(msp) -> None:
    """Add an ISO 2768-mK note plus the defects it makes measurable."""
    msp.add_mtext(
        "GENEL TOLERANSLAR ISO 2768-mK",
        dxfattribs={"layer": "TEXT", "char_height": 2.5},
    ).set_location((0, 56))

    # ±1.5 on a 40 mm feature: ISO 2768-m allows ±0.3 -> TOL008
    msp.add_linear_dim(
        base=(-26, 0),
        p1=(0, 0),
        p2=(0, PLATE_H),
        angle=90,
        override={"dimtol": 1, "dimtp": 1.5, "dimtm": 1.5},
        dxfattribs={"layer": "DIMS"},
    ).render()

    # ±0.3 on a 56 mm feature: exactly the general tolerance -> TOL009
    msp.add_linear_dim(
        base=(12, -34),
        p1=(12, 0),
        p2=(68, 0),
        override={"dimtol": 1, "dimtp": 0.3, "dimtm": 0.3},
        dxfattribs={"layer": "DIMS"},
    ).render()

    # A 0.3 mm step: below the 0.5 mm start of the ISO 2768-1 table -> TOL007
    msp.add_line((0, PLATE_H), (0, PLATE_H + 0.3), dxfattribs={"layer": "PART"})
    msp.add_linear_dim(
        base=(-34, PLATE_H),
        p1=(0, PLATE_H),
        p2=(0, PLATE_H + 0.3),
        angle=90,
        dxfattribs={"layer": "DIMS"},
    ).render()

    # Flatness 0.8 where class K allows 0.2 for this size -> GDT011
    msp.add_mtext("⏥|0.8", dxfattribs={"layer": "GDT", "char_height": 3.0}).set_location((60, 52))


def _complete_plate(msp) -> None:
    """The same plate, dimensioned so that every position is derivable.

    Along each axis the dimensions form a spanning tree over the reference
    coordinates - no coordinate is unreachable (eksik) and none is reached
    twice (fazla):

        X: 0-80 (overall), 0-12, 68-80        nodes {0, 12, 68, 80}
        Y: 0-40 (overall), 0-10, 30-40        nodes {0, 10, 30, 40}
    """
    style = {"layer": "DIMS"}
    msp.add_lwpolyline(
        [(0, 0), (PLATE_W, 0), (PLATE_W, PLATE_H), (0, PLATE_H)],
        close=True,
        dxfattribs={"layer": "PART"},
    )
    for center in HOLES:
        msp.add_circle(center, HOLE_R, dxfattribs={"layer": "PART"})

    for base, p1, p2 in (
        ((0, -22), (0, 0), (PLATE_W, 0)),
        ((0, -12), (0, 0), (12, 0)),
        ((68, -12), (68, 0), (80, 0)),
    ):
        msp.add_linear_dim(base=base, p1=p1, p2=p2, dxfattribs=style).render()
    for base, p1, p2 in (
        ((-22, 0), (0, 0), (0, PLATE_H)),
        ((-12, 0), (0, 0), (0, 10)),
        ((-12, 30), (0, 30), (0, PLATE_H)),
    ):
        msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=90, dxfattribs=style).render()

    # One pattern callout covers the size of all four holes.
    msp.add_diameter_dim(
        center=HOLES[0], radius=HOLE_R, angle=135, text="4x %%c6.5", dxfattribs=style
    ).render()
    msp.add_mtext(
        "GENEL TOLERANSLAR ISO 2768-mK\PÖLÇÜLER MM CİNSİNDENDİR\P3. AÇI İZDÜŞÜMÜ",
        dxfattribs={"layer": "TEXT", "char_height": 2.5},
    ).set_location((0, 52))
    msp.add_mtext("√ Ra 3.2", dxfattribs={"layer": "TEXT", "char_height": 3.0}).set_location(
        (70, 46)
    )


def _title_block(doc: ezdxf.document.Drawing, msp, complete: bool = False) -> None:
    block = doc.blocks.new(name="TITLEBLOCK")
    rows = [
        ("RESIM_NO", "TD-1001", 0.0),
        ("PARCA_ADI", "BAGLANTI PLAKASI", 6.0),
        ("MALZEME", "S235JR" if complete else "TBD", 12.0),
        ("OLCEK", "1:2", 18.0),
        ("CIZEN", "G. BASARAN", 24.0),
        ("ONAYLAYAN", "M. DEMIR" if complete else "G. BASARAN", 30.0),
        ("SAYFA", "1 / 1", 36.0),
    ]
    if complete:
        rows.extend([("REVIZYON", "A", 42.0), ("REVIZYON_TARIHI", "2026-02-11", 48.0),
                     ("FIRMA", "ORNEK MAKINA", 54.0), ("BIRIM", "mm", 60.0)])
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
    args = [arg for arg in argv[1:] if not arg.startswith("--")]
    flags = argv[1:]
    variant = "--iso2768" in flags
    complete = "--complete" in flags
    target = Path(args[0]) if args else Path("examples/sample_plate.dxf")
    target.parent.mkdir(parents=True, exist_ok=True)
    build(with_general_tolerance=variant, complete=complete).saveas(target)
    note = " (fully dimensioned)" if complete else (" (with ISO 2768-mK note)" if variant else "")
    print(f"wrote {target}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
