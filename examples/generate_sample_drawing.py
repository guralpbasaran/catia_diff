"""Generate a sample DXF that contains deliberate, well-known drafting defects.

Run::

    python examples/generate_sample_drawing.py examples/sample_plate.dxf
    python examples/generate_sample_drawing.py examples/sample_plate_2768.dxf --iso2768
    python examples/generate_sample_drawing.py examples/sample_plate_ok.dxf --complete
    python examples/generate_sample_drawing.py examples/sample_plate_fits.dxf --fits
    python examples/generate_sample_drawing.py examples/sample_plate_views.dxf --views
    python examples/generate_sample_drawing.py examples/sample_plate_refs.dxf --refs
    python examples/generate_sample_drawing.py examples/sample_plate_flange.dxf --flange

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
one view, no thickness      DIM016 the third dimension is stated nowhere
==========================  ======================================================
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import ezdxf
from ezdxf.enums import TextEntityAlignment

PLATE_W, PLATE_H = 80.0, 40.0
HOLES = [(12.0, 10.0), (12.0, 30.0), (68.0, 10.0), (68.0, 30.0)]
HOLE_R = 3.25


def build(
    with_general_tolerance: bool = False,
    complete: bool = False,
    fits: bool = False,
    views: bool = False,
    refs: bool = False,
    flange: bool = False,
) -> ezdxf.document.Drawing:
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 4  # millimetres
    doc.header["$LUPREC"] = 2
    msp = doc.modelspace()
    for name in ("PART", "DIMS", "GDT", "TEXT", "TITLE", "CENTER", "PHANTOM"):
        if name not in doc.layers:
            doc.layers.add(name)

    if flange:
        _flange_plate(msp, correct=complete)
        _title_block(doc, msp, complete=True)
        return doc

    if refs:
        _reference_plate(msp, correct=complete)
        _title_block(doc, msp, complete=True)
        return doc

    if views:
        _multiview_plate(msp, correct=complete)
        _title_block(doc, msp, complete=True)
        return doc

    if fits:
        _fitted_plate(msp)
        _title_block(doc, msp, complete=True)
        return doc

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


def _fitted_plate(msp) -> None:
    """A bearing plate whose bores carry ISO 286 fit classes.

    Fully dimensioned on purpose, so the only findings are the ones the fit
    classes themselves raise:

    ==========================  ==================================================
    Callout                     Expected rule
    ==========================  ==================================================
    ⌀25 H7/p6                   TOL012 interference fit - needs a press
    ⌀40 u6                      TOL011 letter outside the implemented tables
    ==========================  ==================================================
    """
    width, height = 120.0, 60.0
    bores = [((30.0, 30.0), 12.5, "%%c25 H7/p6"), ((85.0, 30.0), 20.0, "%%c40 u6")]
    style = {"layer": "DIMS"}

    msp.add_lwpolyline(
        [(0, 0), (width, 0), (width, height), (0, height)],
        close=True,
        dxfattribs={"layer": "PART"},
    )
    for center, radius, text in bores:
        msp.add_circle(center, radius, dxfattribs={"layer": "PART"})
        msp.add_diameter_dim(
            center=center, radius=radius, angle=135, text=text, dxfattribs=style
        ).render()

    # Spanning tree on both axes: every centre is reachable, nothing is doubled.
    for base, p1, p2 in (
        ((0, -22), (0, 0), (width, 0)),
        ((0, -12), (0, 0), (30, 0)),
        ((85, -12), (85, 0), (width, 0)),
    ):
        msp.add_linear_dim(base=base, p1=p1, p2=p2, dxfattribs=style).render()
    for base, p1, p2 in (((-22, 0), (0, 0), (0, height)), ((-12, 0), (0, 0), (0, 30))):
        msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=90, dxfattribs=style).render()

    msp.add_mtext(
        "GENEL TOLERANSLAR ISO 2768-mK\PÖLÇÜLER MM CİNSİNDENDİR\P3. AÇI İZDÜŞÜMÜ"
        "\PKALINLIK 12",
        dxfattribs={"layer": "TEXT", "char_height": 2.5},
    ).set_location((0, 72))
    msp.add_mtext("√ Ra 1.6", dxfattribs={"layer": "TEXT", "char_height": 3.0}).set_location(
        (100, 66)
    )


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
        "GENEL TOLERANSLAR ISO 2768-mK\PÖLÇÜLER MM CİNSİNDENDİR\P3. AÇI İZDÜŞÜMÜ"
        "\PKALINLIK 5",
        dxfattribs={"layer": "TEXT", "char_height": 2.5},
    ).set_location((0, 52))
    msp.add_mtext("√ Ra 3.2", dxfattribs={"layer": "TEXT", "char_height": 3.0}).set_location(
        (70, 46)
    )


def _multiview_plate(msp, correct: bool = False) -> None:
    """The plate in three orthographic views, with two cross-view defects.

    The layout is the ordinary one: the top view sits under the front view and
    shares its width, the side view sits beside it and shares its height.  Each
    extent is dimensioned once, in the view that shows it best - which is why
    the top and side views are silent on the axis they inherit, and why a rule
    that demanded a dimension there would be wrong.

    ==========================  ==================================================
    Defect                      Expected rule
    ==========================  ==================================================
    top view drawn 76 wide      CRV001 aligned views state 80 and 76
    side view drawn 38 tall     CRV002 aligned geometry differs by 2 mm
    ==========================  ==================================================

    ``correct=True`` (``--views --complete``) builds the same three views with
    both defects removed.  That variant is the false-positive net for this
    family *and* for the coverage rules: a correctly dimensioned multi-view
    sheet leaves each shared axis dimensioned once, so anything that fires on
    it is a bug.
    """
    style = {"layer": "DIMS"}
    top_y, depth = -70.0, 20.0
    top_w = PLATE_W if correct else 76.0  # defect: the front view is 80 wide
    side_x = 100.0
    side_h = PLATE_H if correct else 38.0  # defect: the front view is 40 tall

    # -- front view: fully dimensioned ----------------------------------
    msp.add_lwpolyline(
        [(0, 0), (PLATE_W, 0), (PLATE_W, PLATE_H), (0, PLATE_H)],
        close=True,
        dxfattribs={"layer": "PART"},
    )
    for center in HOLES:
        msp.add_circle(center, HOLE_R, dxfattribs={"layer": "PART"})
    for base, p1, p2 in (
        ((0, 52), (0, PLATE_H), (PLATE_W, PLATE_H)),
        ((0, 44), (0, PLATE_H), (12, PLATE_H)),
        ((68, 44), (68, PLATE_H), (80, PLATE_H)),
    ):
        msp.add_linear_dim(base=base, p1=p1, p2=p2, dxfattribs=style).render()
    for base, p1, p2 in (
        ((-22, 0), (0, 0), (0, PLATE_H)),
        ((-12, 0), (0, 0), (0, 10)),
        ((-12, 30), (0, 30), (0, PLATE_H)),
    ):
        msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=90, dxfattribs=style).render()
    msp.add_diameter_dim(
        center=HOLES[0], radius=HOLE_R, angle=135, text="4x %%c6.5", dxfattribs=style
    ).render()

    # -- top view: aligned on X, dimensions its own (wrong) width -------
    msp.add_lwpolyline(
        [(0, top_y), (top_w, top_y), (top_w, top_y + depth), (0, top_y + depth)],
        close=True,
        dxfattribs={"layer": "PART"},
    )
    for x in (12.0, 68.0):
        msp.add_line((x, top_y - 4), (x, top_y + depth + 4), dxfattribs={"layer": "CENTER"})
    msp.add_linear_dim(
        base=(-22, top_y), p1=(0, top_y), p2=(0, top_y + depth), angle=90, dxfattribs=style
    ).render()
    if not correct:
        # The defect is visible precisely because this view states the width
        # itself; a correct sheet dimensions that extent once, in the front view.
        msp.add_linear_dim(
            base=(0, top_y - 12), p1=(0, top_y), p2=(top_w, top_y), dxfattribs=style
        ).render()

    # -- side view: aligned on Y, inherits the height it should share ---
    msp.add_lwpolyline(
        [(side_x, 0), (side_x + depth, 0), (side_x + depth, side_h), (side_x, side_h)],
        close=True,
        dxfattribs={"layer": "PART"},
    )
    msp.add_linear_dim(
        base=(side_x, 52), p1=(side_x, side_h), p2=(side_x + depth, side_h), dxfattribs=style
    ).render()

    msp.add_mtext(
        "GENEL TOLERANSLAR ISO 2768-mK\PÖLÇÜLER MM CİNSİNDENDİR\P3. AÇI İZDÜŞÜMÜ",
        dxfattribs={"layer": "TEXT", "char_height": 2.5},
    ).set_location((0, 62))
    msp.add_mtext("√ Ra 3.2", dxfattribs={"layer": "TEXT", "char_height": 3.0}).set_location(
        (70, 58)
    )


def _flange_plate(msp, correct: bool = False) -> None:
    """A flange whose completeness the per-axis graph cannot judge.

    Three ways a drawing can be incomplete without any axis looking short:

    ==========================  ==================================================
    Defect                      Expected rule
    ==========================  ==================================================
    one view, no thickness      DIM016 the third dimension is written nowhere
    6 holes on ⌀90, no PCD      DIM017 bolt circle without its pitch circle
    6x6 corner, no callout      DIM018 chamfer without a size
    ==========================  ==================================================

    Holes spaced around a centre are not located by x/y pairs, so the constraint
    graph would only say "these centres are not reached" - true, but the drawing
    is not missing two ordinary dimensions, it is missing one pitch circle.

    ``correct=True`` (``--flange --complete``) states all three and must be silent.
    """
    style = {"layer": "DIMS"}
    width, height = 120.0, 80.0
    chamfer = 6.0
    pcd, hole_r, count = 90.0, 4.5, 6
    cx, cy = width / 2, height / 2

    msp.add_lwpolyline(
        [
            (0, 0),
            (width, 0),
            (width, height - chamfer),
            (width - chamfer, height),
            (0, height),
        ],
        close=True,
        dxfattribs={"layer": "PART"},
    )
    for index in range(count):
        angle = math.radians(360.0 / count * index)
        msp.add_circle(
            (cx + pcd / 2 * math.cos(angle), cy + pcd / 2 * math.sin(angle)),
            hole_r,
            dxfattribs={"layer": "PART"},
        )

    for base, p1, p2 in (((0, -22), (0, 0), (width, 0)),):
        msp.add_linear_dim(base=base, p1=p1, p2=p2, dxfattribs=style).render()
    msp.add_linear_dim(
        base=(-22, 0), p1=(0, 0), p2=(0, height), angle=90, dxfattribs=style
    ).render()

    notes = [
        "GENEL TOLERANSLAR ISO 2768-mK",
        "ÖLÇÜLER MM CİNSİNDENDİR",
        "3. AÇI İZDÜŞÜMÜ",
    ]
    if correct:
        # The pitch circle, the spacing, the chamfer and the thickness.
        msp.add_diameter_dim(
            center=(cx, cy), radius=pcd / 2, angle=30, text="%%c90 DELİK DAİRESİ",
            dxfattribs=style,
        ).render()
        msp.add_diameter_dim(
            center=(cx + pcd / 2, cy), radius=hole_r, angle=135,
            text=f"{count}x %%c{hole_r * 2:g} EŞİT BÖLÜNMÜŞ", dxfattribs=style,
        ).render()
        msp.add_mtext(
            "6x45°", dxfattribs={"layer": "TEXT", "char_height": 2.5}
        ).set_location((width - chamfer + 1, height - chamfer + 1))
        notes.append("KALINLIK 10")
    msp.add_mtext(
        "\\P".join(notes), dxfattribs={"layer": "TEXT", "char_height": 2.5}
    ).set_location((0, height + 16))


def _reference_plate(msp, correct: bool = False) -> None:
    """A plate whose cross-references do not resolve.

    Every pointer on a drawing has to land on something: a cutting plane on a
    section view, a note number on a note, a sheet number on a sheet.  This
    variant breaks four of them at once, which is how they show up in practice -
    a view gets deleted and the things pointing at it stay behind.

    ==========================  ==================================================
    Defect                      Expected rule
    ==========================  ==================================================
    A-A cut, no section view    REF001 marker without a view
    "BKZ NOT 7", notes are 1-3  REF004 reference to a note that is not written
    "MONTAJ İÇİN SAYFA 3"       REF005 reference to a sheet that does not exist
    "DETAY C'YE BAKINIZ"        REF006 text points at a view that does not exist
    stray "KESİT B-B" title     REF007 view title sits on no view
    ==========================  ==================================================

    ``correct=True`` (``--refs --complete``) draws the section view, points the
    note at a note that exists and drops the stray title: the whole family must
    then stay silent.
    """
    style = {"layer": "DIMS"}
    _complete_plate(msp)

    # -- the cutting plane: a phantom line and its letter pair ----------
    msp.add_line((40, -8), (40, 48), dxfattribs={"layer": "PHANTOM"})
    msp.add_mtext("A-A", dxfattribs={"layer": "TEXT", "char_height": 3.0}).set_location((42, 49))

    if correct:
        # The section it promises, drawn beside the view it is cut from.
        msp.add_lwpolyline(
            [(100, 0), (120, 0), (120, PLATE_H), (100, PLATE_H)],
            close=True,
            dxfattribs={"layer": "PART"},
        )
        msp.add_linear_dim(
            base=(100, 52), p1=(100, PLATE_H), p2=(120, PLATE_H), dxfattribs=style
        ).render()
        msp.add_mtext(
            "KESİT A-A", dxfattribs={"layer": "TEXT", "char_height": 3.0}
        ).set_location((100, -10))

    notes = [
        "NOTLAR:",
        "1. KESKİN KÖŞELER KIRILACAK.",
        "2. ÇAPAK ALINACAK.",
        "3. BOYA RAL 7016.",
    ]
    notes.append("YÜZEY İŞLEMİ İÇİN BKZ NOT 2." if correct else "YÜZEY İŞLEMİ İÇİN BKZ NOT 7.")
    if not correct:
        notes.append("KANAL İÇİN DETAY C'YE BAKINIZ.")
        notes.append("MONTAJ İÇİN SAYFA 3.")
    msp.add_mtext(
        "\\P".join(notes), dxfattribs={"layer": "TEXT", "char_height": 2.5}
    ).set_location((0, 70))

    if not correct:
        # A title left behind when its view was deleted.
        msp.add_mtext(
            "KESİT B-B", dxfattribs={"layer": "TEXT", "char_height": 3.0}
        ).set_location((-60, 100))


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
    fits = "--fits" in flags
    views = "--views" in flags
    refs = "--refs" in flags
    flange = "--flange" in flags
    target = Path(args[0]) if args else Path("examples/sample_plate.dxf")
    target.parent.mkdir(parents=True, exist_ok=True)
    build(
        with_general_tolerance=variant,
        complete=complete,
        fits=fits,
        views=views,
        refs=refs,
        flange=flange,
    ).saveas(target)
    note = (
        " (flange, correct)"
        if flange and complete
        else " (flange)"
        if flange
        else " (cross-references, correct)"
        if refs and complete
        else " (cross-references)"
        if refs
        else " (three orthographic views, correct)"
        if views and complete
        else " (three orthographic views)"
        if views
        else " (ISO 286 fit classes)"
        if fits
        else " (fully dimensioned)"
        if complete
        else " (with ISO 2768-mK note)"
        if variant
        else ""
    )
    print(f"wrote {target}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
