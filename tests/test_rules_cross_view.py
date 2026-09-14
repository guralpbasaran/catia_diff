"""Cross-view rules: two views of one part have to agree."""

from __future__ import annotations

import pytest

from catia_diff.config import AuditConfig
from catia_diff.models.findings import Severity
from catia_diff.rules.base import rules_for, run_rules
from tests.conftest import make_context, make_document
from tests.test_projection import linear, stacked_sheet


def run(sheet, rule_ids: set[str] | None = None):
    document = make_document(sheet)
    ctx = make_context(document)
    rules = [
        rule
        for rule in rules_for(AuditConfig(language="en"))
        if rule.meta.id.startswith("CRV") and (rule_ids is None or rule.meta.id in rule_ids)
    ]
    return list(run_rules(rules, document, ctx))


def dimensioned(top_width: float, top_value: float | None, front_value: float = 80.0):
    """Front view dimensioned 80; the top view optionally states its own width."""
    sheet = stacked_sheet(top_width)
    sheet.dimensions = [linear("DIM1", 0.0, 0.0, 80.0, front_value)]
    sheet.views[0].member_ids.append("DIM1")
    if top_value is not None:
        sheet.dimensions.append(linear("DIM2", 0.0, 0.0, top_width, top_value))
        sheet.views[1].member_ids.append("DIM2")
    return sheet


def test_crv001_reports_views_that_state_different_sizes():
    findings = run(dimensioned(76.0, 76.0))
    assert [f.rule_id for f in findings] == ["CRV001"]
    finding = findings[0]
    assert finding.severity is Severity.CRITICAL
    assert "80" in finding.message and "76" in finding.message
    assert set(finding.evidence.object_ids) == {"DIM1", "DIM2"}
    assert "Görünüş" in finding.message_tr


def test_crv002_reports_geometry_that_drifted_apart():
    """Only the front view dimensions X, but the two views are drawn differently."""
    findings = run(dimensioned(76.0, None))
    assert [f.rule_id for f in findings] == ["CRV002"]
    assert findings[0].severity is Severity.MAJOR
    assert "Δ=4" in findings[0].message


def test_crv002_defers_to_crv001_when_both_views_state_the_extent():
    assert [f.rule_id for f in run(dimensioned(76.0, 76.0))] == ["CRV001"]


def test_crv003_reports_the_same_extent_dimensioned_twice():
    findings = run(dimensioned(80.0, 80.0))
    assert [f.rule_id for f in findings] == ["CRV003"]
    assert findings[0].severity is Severity.MINOR
    assert "80" in findings[0].message


def test_consistent_views_are_silent():
    """Each extent dimensioned once, both views the same size: nothing to say."""
    assert run(dimensioned(80.0, None)) == []


def test_a_view_at_another_scale_is_not_compared():
    """A 2:1 fragment is not a contradiction, so no rule fires."""
    assert run(dimensioned(40.0, 40.0)) == []


def test_unaligned_views_are_not_compared():
    sheet = dimensioned(76.0, 76.0)
    sheet.views[1].bbox = sheet.views[1].bbox.model_copy(update={"x0": 200.0, "x1": 276.0})
    assert run(sheet) == []


# ---------------------------------------------------------------------------
# On the real sample drawings
def test_sample_multiview_reports_both_defects(sample_dxf_views):
    from catia_diff import AuditConfig as Config
    from catia_diff import audit_file

    report = audit_file(sample_dxf_views, Config(language="tr", formats=(), render_overlay=False))
    ids = [f.rule_id for f in report.findings]
    assert "CRV001" in ids and "CRV002" in ids


def test_sample_multiview_correct_is_silent(sample_dxf_views_correct, tmp_path):
    """The false-positive net: a correct multi-view sheet raises nothing here.

    It also proves the coverage rules inherit across views - without that, the
    top and side views would each report the axis the front view dimensions.
    """
    from catia_diff import AuditConfig as Config
    from catia_diff import audit_file

    report = audit_file(
        sample_dxf_views_correct,
        Config(language="tr", formats=(), render_overlay=False, output_dir=tmp_path),
    )
    noisy = [
        f.rule_id
        for f in report.findings
        if f.rule_id.startswith("CRV") or f.rule_id in {"DIM011", "DIM012", "DIM013", "DIM014"}
    ]
    assert noisy == []


def test_centre_line_length_is_not_a_reference_coordinate(sample_dxf_views_correct):
    """A centre line marks a position; how far past the part it is drawn is style.

    Before this, the 4 mm overrun of every centre line became two coordinates
    the drawing was required to dimension.
    """
    from catia_diff import AuditConfig as Config
    from catia_diff.extract.registry import get_extractor
    from catia_diff.rules.constraints import sheet_coverage
    from catia_diff.rules.views import segment_views

    config = Config()
    document = get_extractor(sample_dxf_views_correct, config).extract(
        sample_dxf_views_correct, config
    )
    sheet = document.sheets[0]
    segment_views(sheet, config)
    top = next(v for v in sheet.views if v.bbox and v.bbox.y1 < 0)
    vertical = next(
        axes for view, axes in sheet_coverage(sheet, config) if view.id == top.id
    )
    y_axis = next(axis for axis in vertical if axis.label == "Y")
    assert [round(node.coordinate, 1) for node in y_axis.nodes] == [-70.0, -50.0]
    assert y_axis.missing == 0


@pytest.mark.parametrize("rule_id", ["CRV001", "CRV002", "CRV003"])
def test_every_cross_view_rule_is_registered(rule_id):
    assert any(rule.meta.id == rule_id for rule in rules_for(AuditConfig()))
