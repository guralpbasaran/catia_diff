"""Completeness classes the per-axis constraint graph cannot judge.

A drawing can look fully dimensioned on every axis and still be unbuildable:
its thickness is nowhere, its bolt circle has no pitch diameter, its broken
corners have no size.
"""

from __future__ import annotations

import math

import pytest

from catia_diff.config import AuditConfig
from catia_diff.extract.text_parsing import parse_thickness
from catia_diff.models.drawing import (
    Annotation,
    GeometryFeature,
    GeometryKind,
    Sheet,
    View,
)
from catia_diff.models.findings import Severity
from catia_diff.models.geometry import BBox, Point2D
from catia_diff.rules.base import rules_for, run_rules
from catia_diff.rules.patterns import bolt_circles, chamfer_edges
from tests.conftest import box, make_context, make_dimension, make_document, make_sheet, titled


def hole(index: int, x: float, y: float, radius: float = 4.5) -> GeometryFeature:
    return GeometryFeature(
        id=f"F{index}",
        kind=GeometryKind.CIRCLE,
        layer="PART",
        center=Point2D(x=x, y=y),
        radius=radius,
        bbox=BBox(x0=x - radius, y0=y - radius, x1=x + radius, y1=y + radius),
    )


def ring(count: int, pcd: float, radius: float = 4.5, cx: float = 60.0, cy: float = 40.0):
    return [
        hole(
            index,
            cx + pcd / 2 * math.cos(math.radians(360 / count * index)),
            cy + pcd / 2 * math.sin(math.radians(360 / count * index)),
            radius,
        )
        for index in range(count)
    ]


def linear(dim_id: str, axis: float, low: float, high: float, **kwargs):
    """A dimension the constraint graph can use: it knows what it measured."""
    return make_dimension(
        dim_id,
        nominal=kwargs.pop("nominal", high - low),
        bbox=box(low, -12),
        extra={"axis": axis, "interval": [low, high]},
        **kwargs,
    )


def located_plate(*features, dimensions=None, **kwargs):
    """A 120x80 plate whose own outline is dimensioned on both axes."""
    body = outline([(0, 0), (120, 0), (120, 80), (0, 80)])
    dims = [linear("DIMX", 0.0, 0.0, 120.0), linear("DIMY", 90.0, 0.0, 80.0)]
    dims.extend(dimensions or [])
    return plate_sheet(body, *features, dimensions=dims, **kwargs)


def outline(points: list[tuple[float, float]], feature_id: str = "OUT") -> GeometryFeature:
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return GeometryFeature(
        id=feature_id,
        kind=GeometryKind.POLYLINE,
        layer="PART",
        closed=True,
        points=[Point2D(x=x, y=y) for x, y in [*points, points[0]]],
        bbox=BBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys)),
    )


def plate_sheet(*features, dimensions=None, annotations=None, **kwargs) -> Sheet:
    members = [f.id for f in features] + [d.id for d in (dimensions or [])]
    return make_sheet(
        features=list(features),
        dimensions=list(dimensions or []),
        annotations=list(annotations or []),
        views=[View(id="VIEW01", bbox=BBox(x0=0, y0=0, x1=120, y1=80), member_ids=members)],
        **kwargs,
    )


def run(sheet: Sheet, prefix: str = "DIM01"):
    document = make_document(sheet)
    ctx = make_context(document)
    rules = [
        rule
        for rule in rules_for(AuditConfig(language="en"))
        if rule.meta.id in {"DIM016", "DIM017", "DIM018", "DIM011", "DIM012"}
    ]
    return list(run_rules(rules, document, ctx))


# ---------------------------------------------------------------------------
# patterns
def test_evenly_spaced_holes_are_a_bolt_circle():
    circles = bolt_circles(ring(6, 90.0))
    assert len(circles) == 1
    assert circles[0].count == 6
    assert circles[0].diameter == pytest.approx(90.0)


def test_the_corners_of_a_rectangle_are_not_a_bolt_circle():
    """They are concyclic, but a drawing locates them with x/y - not a PCD."""
    corners = [hole(i, x, y, 3.25) for i, (x, y) in enumerate([(12, 10), (12, 30), (68, 10), (68, 30)])]
    assert bolt_circles(corners) == []


def test_a_bolt_circle_needs_three_holes_of_one_size():
    assert bolt_circles(ring(2, 90.0)) == []
    mixed = ring(3, 90.0)
    mixed[0] = hole(0, mixed[0].center.x, mixed[0].center.y, radius=9.0)
    assert bolt_circles(mixed) == []


def test_holes_off_the_circle_are_not_a_bolt_circle():
    group = ring(4, 90.0)
    moved = group[0]
    group[0] = hole(0, moved.center.x + 8, moved.center.y, moved.radius or 4.5)
    assert bolt_circles(group) == []


def test_only_short_oblique_edges_are_chamfers():
    part = outline([(0, 0), (120, 0), (120, 74), (114, 80), (0, 80)])
    assert [round(edge.length, 1) for edge in chamfer_edges([part], 12.0)] == [8.5]
    assert chamfer_edges([part], 4.0) == []  # too short a limit: nothing qualifies


# ---------------------------------------------------------------------------
# DIM016 - thickness
def test_dim016_a_single_view_without_a_thickness():
    sheet = plate_sheet(outline([(0, 0), (120, 0), (120, 80), (0, 80)]), dimensions=[make_dimension()])
    findings = [f for f in run(sheet) if f.rule_id == "DIM016"]
    assert len(findings) == 1
    assert findings[0].severity is Severity.CRITICAL


@pytest.mark.parametrize("text", ["KALINLIK 5", "t=5", "THK 5", "5 mm SAC"])
def test_dim016_is_satisfied_by_a_note(text):
    sheet = plate_sheet(
        outline([(0, 0), (120, 0), (120, 80), (0, 80)]),
        dimensions=[make_dimension()],
        annotations=[Annotation(id="N1", text=text, bbox=box(0, 90))],
    )
    assert [f for f in run(sheet) if f.rule_id == "DIM016"] == []


def test_dim016_is_satisfied_by_the_material_field():
    sheet = plate_sheet(
        outline([(0, 0), (120, 0), (120, 80), (0, 80)]),
        dimensions=[make_dimension()],
        title_block=titled(material="S235JR t=3"),
    )
    assert [f for f in run(sheet) if f.rule_id == "DIM016"] == []


def test_dim016_leaves_a_turned_part_alone():
    """A shaft's third dimension is its diameter, not a thickness."""
    sheet = plate_sheet(
        outline([(0, 0), (120, 0), (120, 80), (0, 80)]),
        dimensions=[make_dimension("DIM1", nominal=80.0, prefix="⌀")],
    )
    assert [f for f in run(sheet) if f.rule_id == "DIM016"] == []


def test_dim016_leaves_a_multi_view_drawing_alone():
    sheet = plate_sheet(outline([(0, 0), (120, 0), (120, 80), (0, 80)]), dimensions=[make_dimension()])
    sheet.views.append(View(id="VIEW02", bbox=box(200, 0, 40, 80), member_ids=["OUT"]))
    assert [f for f in run(sheet) if f.rule_id == "DIM016"] == []


# ---------------------------------------------------------------------------
# DIM017 - bolt circle
def test_dim017_a_bolt_circle_without_its_pitch_circle():
    sheet = located_plate(*ring(6, 90.0))
    findings = [f for f in run(sheet) if f.rule_id == "DIM017"]
    assert len(findings) == 1
    assert "⌀90" in findings[0].message
    assert len(findings[0].evidence.object_ids) == 6


def test_dim017_is_satisfied_by_the_dimension():
    sheet = located_plate(
        *ring(6, 90.0), dimensions=[make_dimension("DIM1", nominal=90.0, prefix="⌀")]
    )
    assert [f for f in run(sheet) if f.rule_id == "DIM017"] == []


def test_dim017_is_satisfied_by_the_words():
    sheet = located_plate(
        *ring(6, 90.0), dimensions=[make_dimension("DIM1", nominal=42.0, text="⌀90 DELİK DAİRESİ")]
    )
    assert [f for f in run(sheet) if f.rule_id == "DIM017"] == []


def test_dim011_defers_to_dim017_for_the_same_holes():
    """One defect, one finding: the holes are missing a PCD, not two ordinary dimensions."""
    sheet = located_plate(*ring(6, 90.0))
    reported = {f.rule_id for f in run(sheet)}
    assert "DIM017" in reported
    assert "DIM011" not in reported


# ---------------------------------------------------------------------------
# DIM018 - chamfer
def chamfered_sheet(**kwargs) -> Sheet:
    return plate_sheet(
        outline([(0, 0), (120, 0), (120, 74), (114, 80), (0, 80)]),
        dimensions=[make_dimension()],
        annotations=kwargs.pop("annotations", None),
        **kwargs,
    )


def test_dim018_a_broken_corner_with_no_size():
    findings = [f for f in run(chamfered_sheet()) if f.rule_id == "DIM018"]
    assert len(findings) == 1
    assert findings[0].severity is Severity.MINOR


def test_dim018_is_satisfied_by_leader_text():
    sheet = chamfered_sheet(
        annotations=[Annotation(id="N1", text="6x45°", bbox=box(112, 74, 8, 4))]
    )
    assert [f for f in run(sheet) if f.rule_id == "DIM018"] == []


def test_dim018_is_satisfied_by_a_blanket_note():
    sheet = chamfered_sheet(
        annotations=[Annotation(id="N1", text="TÜM PAHLAR 1x45°", bbox=box(0, 90))]
    )
    assert [f for f in run(sheet) if f.rule_id == "DIM018"] == []


def test_dim018_ignores_a_long_oblique_edge():
    """A long slope is shape; DIM015 asks for its angle instead."""
    sheet = plate_sheet(
        outline([(0, 0), (120, 0), (120, 30), (60, 80), (0, 80)]),
        dimensions=[make_dimension()],
    )
    assert [f for f in run(sheet) if f.rule_id == "DIM018"] == []


# ---------------------------------------------------------------------------
# on the real sample drawings
def audit(path, tmp_path):
    from catia_diff import AuditConfig as Config
    from catia_diff import audit_file

    return audit_file(
        path, Config(language="tr", formats=(), render_overlay=False, output_dir=tmp_path)
    )


def test_the_flange_sample_misses_all_three(sample_dxf_flange, tmp_path):
    found = {f.rule_id for f in audit(sample_dxf_flange, tmp_path).findings}
    assert {"DIM016", "DIM017", "DIM018"} <= found


def test_the_corrected_flange_states_all_three(sample_dxf_flange_correct, tmp_path):
    found = {f.rule_id for f in audit(sample_dxf_flange_correct, tmp_path).findings}
    assert not {"DIM016", "DIM017", "DIM018"} & found


def test_thickness_is_read_from_the_shapes_a_drawing_uses():
    assert parse_thickness("KALINLIK 5") == 5.0
    assert parse_thickness("ET KALINLIĞI 5 mm") == 5.0
    assert parse_thickness("S235JR t=3") == 3.0
    assert parse_thickness("PLAKA 8") == 8.0
    assert parse_thickness("BOYA RAL 7016") is None
    assert parse_thickness("ÖLÇEK 1:2") is None


def test_dim017_stays_out_of_it_without_interval_data():
    """Whether the holes are already located cannot be established - so no claim."""
    sheet = plate_sheet(*ring(6, 90.0), dimensions=[make_dimension()])
    assert [f for f in run(sheet) if f.rule_id == "DIM017"] == []
