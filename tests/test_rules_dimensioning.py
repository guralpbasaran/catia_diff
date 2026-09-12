import pytest
from conftest import (
    box,
    make_circle,
    make_context,
    make_dimension,
    make_document,
    make_sheet,
)

from catia_diff.models.drawing import DimensionKind, GeometryKind, Tolerance, ToleranceKind
from catia_diff.models.findings import Severity
from catia_diff.rules import analysis
from catia_diff.rules.base import get_rule


def run(rule_id: str, sheet, config=None):
    document = make_document(sheet)
    ctx = make_context(document, config)
    rule = get_rule(rule_id)
    target = document if rule.scope == "document" else sheet
    return list(rule.check(target, ctx))


def test_dim001_reports_holes_without_a_dimension():
    sheet = make_sheet(
        features=[make_circle("FEAT1", x=10, y=10), make_circle("FEAT2", x=60, y=10)],
        dimensions=[make_dimension("DIM1", nominal=6.5, kind=DimensionKind.DIAMETER)],
    )
    findings = run("DIM001", sheet)
    assert len(findings) == 1
    assert findings[0].evidence.object_ids == ("FEAT2",)
    assert findings[0].severity is Severity.MAJOR


def test_dim001_respects_multiplicity_callouts():
    sheet = make_sheet(
        features=[make_circle(f"FEAT{i}", x=10 * i, y=10) for i in range(1, 5)],
        dimensions=[
            make_dimension(
                "DIM1", nominal=6.5, kind=DimensionKind.DIAMETER, extra={"multiplicity": 4}
            )
        ],
    )
    assert run("DIM001", sheet) == []


def test_dim003_detects_a_closed_chain_from_intervals():
    chain = [
        make_dimension("DIM1", nominal=12.0, extra={"axis": 0.0, "interval": [0.0, 12.0]}),
        make_dimension("DIM2", nominal=56.0, extra={"axis": 0.0, "interval": [12.0, 68.0]}),
        make_dimension("DIM3", nominal=12.0, extra={"axis": 0.0, "interval": [68.0, 80.0]}),
        make_dimension("DIM4", nominal=80.0, extra={"axis": 0.0, "interval": [0.0, 80.0]}),
    ]
    findings = run("DIM003", make_sheet(dimensions=chain))
    assert len(findings) == 1
    assert set(findings[0].evidence.object_ids) == {"DIM1", "DIM2", "DIM3", "DIM4"}


def test_dim003_ignores_an_open_chain():
    dims = [
        make_dimension("DIM1", nominal=12.0, extra={"axis": 0.0, "interval": [0.0, 12.0]}),
        make_dimension("DIM2", nominal=56.0, extra={"axis": 0.0, "interval": [12.0, 68.0]}),
        make_dimension("DIM3", nominal=80.0, extra={"axis": 0.0, "interval": [0.0, 80.0]}),
    ]
    assert run("DIM003", make_sheet(dimensions=dims)) == []


def test_dim003_names_the_redundant_dimension():
    chain = [
        make_dimension("DIM1", nominal=12.0, extra={"axis": 0.0, "interval": [0.0, 12.0]}),
        make_dimension("DIM2", nominal=68.0, extra={"axis": 0.0, "interval": [12.0, 80.0]}),
        make_dimension("DIM3", nominal=80.0, extra={"axis": 0.0, "interval": [0.0, 80.0]}),
    ]
    finding = run("DIM003", make_sheet(dimensions=chain))[0]
    assert "80" in finding.message  # the overall is the redundant one
    assert "12 + 68" in finding.message or "68 + 12" in finding.message
    assert "X" in finding.message
    assert finding.confidence > 0.9  # exact, not heuristic


def test_dim003_detects_a_duplicated_span():
    """Two dimensions over the same distance are a cycle of length two.

    The old contiguous-chain heuristic could not see this: it looked for a run
    of smaller dimensions adding up to a larger one.
    """
    duplicated = [
        make_dimension("DIM1", nominal=40.0, extra={"axis": 90.0, "interval": [0.0, 40.0]}),
        make_dimension("DIM2", nominal=40.0, extra={"axis": 90.0, "interval": [0.0, 40.0]}),
    ]
    findings = run("DIM003", make_sheet(dimensions=duplicated))
    assert len(findings) == 1
    assert set(findings[0].evidence.object_ids) == {"DIM1", "DIM2"}
    assert "Y" in findings[0].message
    # a two-cycle is phrased as a duplicate and names its twin
    assert "duplicates DIM" in findings[0].message
    assert any(name in findings[0].localized_message("tr") for name in ("DIM1", "DIM2"))


def test_dim003_counts_one_finding_per_redundant_dimension():
    dims = [
        make_dimension("DIM1", nominal=12.0, extra={"axis": 0.0, "interval": [0.0, 12.0]}),
        make_dimension("DIM2", nominal=68.0, extra={"axis": 0.0, "interval": [12.0, 80.0]}),
        make_dimension("DIM3", nominal=80.0, extra={"axis": 0.0, "interval": [0.0, 80.0]}),
        make_dimension("DIM4", nominal=80.0, extra={"axis": 0.0, "interval": [0.0, 80.0]}),
    ]
    assert len(run("DIM003", make_sheet(dimensions=dims))) == 2


def test_dim003_separates_axes():
    """A horizontal and a vertical dimension over the same numbers are not a cycle."""
    dims = [
        make_dimension("DIM1", nominal=40.0, extra={"axis": 0.0, "interval": [0.0, 40.0]}),
        make_dimension("DIM2", nominal=40.0, extra={"axis": 90.0, "interval": [0.0, 40.0]}),
    ]
    assert run("DIM003", make_sheet(dimensions=dims)) == []


def test_dim003_falls_back_to_collinear_clusters_without_intervals():
    """PDF and vision input carry no intervals; the weaker path still reports."""
    dims = [
        make_dimension("DIM1", nominal=10.0, bbox=box(0, 0)),
        make_dimension("DIM2", nominal=15.0, bbox=box(20, 0)),
        make_dimension("DIM3", nominal=25.0, bbox=box(40, 0)),
    ]
    findings = run("DIM003", make_sheet(dimensions=dims))
    assert len(findings) == 1
    assert findings[0].confidence == pytest.approx(0.6)  # marked inexact
    assert set(findings[0].evidence.object_ids) == {"DIM1", "DIM2", "DIM3"}


def test_dim004_flags_a_text_override():
    dim = make_dimension("DIM1", nominal=25.0, text="25")
    dim.measured = 30.0
    dim.is_text_override = True
    findings = run("DIM004", make_sheet(dimensions=[dim]))
    assert len(findings) == 1
    assert findings[0].severity is Severity.CRITICAL
    assert "30" in findings[0].message


def test_dim005_missing_diameter_symbol():
    sheet = make_sheet(
        features=[make_circle("FEAT1", r=3.25)],
        dimensions=[make_dimension("DIM1", nominal=6.5, kind=DimensionKind.LINEAR)],
    )
    findings = run("DIM005", sheet)
    assert [f.evidence.object_ids for f in findings] == [("DIM1", "FEAT1")]


def test_dim006_non_positive_value():
    sheet = make_sheet(dimensions=[make_dimension("DIM1", nominal=0.0)])
    assert len(run("DIM006", sheet)) == 1


def test_dim007_callout_outside_the_frame():
    sheet = make_sheet(dimensions=[make_dimension("DIM1", bbox=box(900, 900))])
    findings = run("DIM007", sheet)
    assert len(findings) == 1


def test_dim008_overlapping_callouts():
    sheet = make_sheet(
        dimensions=[
            make_dimension("DIM1", bbox=box(10, 10)),
            make_dimension("DIM2", nominal=30.0, bbox=box(11, 10)),
        ]
    )
    assert len(run("DIM008", sheet)) == 1


def test_dim009_sheet_without_dimensions():
    sheet = make_sheet(features=[make_circle()])
    assert len(run("DIM009", sheet)) == 1
    assert run("DIM009", make_sheet()) == []


def test_dim010_radius_on_a_full_circle():
    sheet = make_sheet(
        features=[make_circle("FEAT1", r=5.0)],
        dimensions=[make_dimension("DIM1", nominal=5.0, kind=DimensionKind.RADIAL, prefix="R")],
    )
    findings = run("DIM010", sheet)
    assert len(findings) == 1
    assert "⌀" in findings[0].suggestion


def test_dim002_duplicate_only_when_boxes_overlap():
    overlapping = make_sheet(
        dimensions=[
            make_dimension("DIM1", nominal=20.0, bbox=box(0, 0)),
            make_dimension("DIM2", nominal=20.0, bbox=box(0.5, 0)),
        ]
    )
    assert len(run("DIM002", overlapping)) == 1

    apart = make_sheet(
        dimensions=[
            make_dimension("DIM1", nominal=20.0, bbox=box(0, 0)),
            make_dimension("DIM2", nominal=20.0, bbox=box(80, 60)),
        ]
    )
    assert run("DIM002", apart) == []


def test_feature_association_helpers():
    feature = make_circle("FEAT1", r=3.25)
    diameter = make_dimension("DIM1", nominal=6.5, kind=DimensionKind.DIAMETER)
    radius = make_dimension("DIM2", nominal=3.25, kind=DimensionKind.RADIAL, prefix="R")
    tapped = make_dimension("DIM3", nominal=8.0, kind=DimensionKind.THREAD)
    assert analysis.dimension_matches_feature(diameter, feature)
    assert analysis.dimension_matches_feature(radius, feature)
    assert analysis.dimension_matches_feature(tapped, feature)
    assert not analysis.dimension_matches_feature(make_dimension("DIM4", nominal=40.0), feature)


def test_candidate_features_skips_construction_layers():
    sheet = make_sheet(
        features=[
            make_circle("FEAT1"),
            make_circle("FEAT2"),
        ]
    )
    sheet.features[1].layer = "CENTER-LINES"
    sheet.features[1].kind = GeometryKind.CIRCLE
    assert [f.id for f in analysis.candidate_features(sheet)] == ["FEAT1"]


def test_basic_dimension_counts_as_toleranced():
    dim = make_dimension("DIM1", tolerance=Tolerance(kind=ToleranceKind.BASIC), is_basic=True)
    assert dim.has_tolerance
