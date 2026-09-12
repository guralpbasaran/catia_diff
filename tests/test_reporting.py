import json

import pytest
from conftest import box, make_circle, make_dimension, make_document, make_sheet

from catia_diff.config import AuditConfig
from catia_diff.models.findings import AuditReport, Category, Evidence, Finding, Severity
from catia_diff.reporting.normalize import cap_per_rule, dedupe, normalize_findings, sort_findings
from catia_diff.reporting.overlay import render_overlays
from catia_diff.reporting.render import to_html, to_json, to_markdown, write_reports


def finding(rule_id="DIM001", severity=Severity.MAJOR, objects=("FEAT1",), sheet=0, **kwargs):
    return Finding(
        rule_id=rule_id,
        severity=severity,
        category=kwargs.pop("category", Category.DIMENSIONING),
        title="Undimensioned feature",
        title_tr="Ölçülendirilmemiş unsur",
        message=kwargs.pop("message", "no dimension for FEAT1"),
        message_tr="FEAT1 için ölçü yok",
        suggestion="add a ⌀ callout",
        suggestion_tr="⌀ ölçüsü ekleyin",
        standards=("ISO 129-1",),
        evidence=Evidence(sheet_index=sheet, object_ids=objects, bbox=kwargs.pop("bbox", box(10, 10))),
        **kwargs,
    )


def test_finding_ids_are_stable_and_distinct():
    assert finding().id == finding().id
    assert finding(objects=("FEAT1",)).id != finding(objects=("FEAT2",)).id


def test_dedupe_keeps_the_most_confident_duplicate():
    low = finding(confidence=0.5)
    high = finding(confidence=0.9)
    result = dedupe([low, high])
    assert len(result) == 1 and result[0].confidence == 0.9


def test_cap_per_rule_summarises_the_remainder():
    findings = [finding(objects=(f"FEAT{i}",)) for i in range(10)]
    capped = cap_per_rule(findings, 3)
    assert len(capped) == 4
    rollup = capped[-1]
    assert rollup.severity is Severity.INFO
    assert "7" in rollup.message and "7" in rollup.message_tr


def test_sort_order_is_severity_then_sheet():
    items = [
        finding(rule_id="TB002", severity=Severity.MINOR, sheet=1),
        finding(rule_id="DIM004", severity=Severity.CRITICAL, sheet=1),
        finding(rule_id="DIM001", severity=Severity.CRITICAL, sheet=0),
    ]
    assert [f.rule_id for f in sort_findings(items)] == ["DIM001", "DIM004", "TB002"]


def test_normalize_applies_the_severity_floor():
    config = AuditConfig(min_severity=Severity.MAJOR)
    findings = [finding(severity=Severity.MINOR), finding(severity=Severity.CRITICAL, objects=("F2",))]
    assert [f.severity for f in normalize_findings(findings, config)] == [Severity.CRITICAL]


def report_with_findings(tmp_path) -> AuditReport:
    return AuditReport(
        document=tmp_path / "TD-1001.dxf",
        source_format="dxf",
        profile="ISO",
        language="tr",
        findings=[finding(), finding(rule_id="TOL002", severity=Severity.CRITICAL, objects=("DIM2",))],
        document_stats={"dimensions": 9, "features": 4},
        rules_executed=47,
        agent_traces=[{"agent": "dimensioning", "status": "ok", "findings": 2, "duration_ms": 1.2}],
        warnings=["extraction: $INSUNITS not set"],
    )


def test_json_report_includes_a_summary(tmp_path):
    payload = json.loads(to_json(report_with_findings(tmp_path)))
    assert payload["summary"]["by_severity"]["critical"] == 1
    assert payload["summary"]["worst_severity"] == "critical"
    assert len(payload["findings"]) == 2
    assert payload["findings"][0]["message_tr"]


def test_markdown_report_is_localised(tmp_path):
    report = report_with_findings(tmp_path)
    turkish = to_markdown(report, "tr")
    english = to_markdown(report, "en")
    assert "Kritik" in turkish and "FEAT1 için ölçü yok" in turkish
    assert "Critical" in english and "no dimension for FEAT1" in english
    assert "ISO 129-1" in english


def test_html_report_renders_cards_and_filters(tmp_path):
    html = to_html(report_with_findings(tmp_path), "tr")
    assert "<!doctype html>" in html.lower()
    assert "data-severity=\"critical\"" in html
    assert "Ölçülendirilmemiş unsur" in html
    assert "prefers-color-scheme" in html  # theme-aware
    assert "TOL002" in html


def test_write_reports_writes_every_requested_format(tmp_path):
    config = AuditConfig(output_dir=tmp_path / "out", formats=("json", "md", "html"))
    written = write_reports(report_with_findings(tmp_path), config)
    assert {path.suffix for path in written} == {".json", ".md", ".html"}
    assert all(path.exists() and path.stat().st_size > 0 for path in written)


def test_overlay_rendering_marks_findings(tmp_path):
    pytest.importorskip("PIL")
    sheet = make_sheet(
        dimensions=[make_dimension("DIM1", bbox=box(20, 20))],
        features=[make_circle("FEAT1", x=40, y=40)],
    )
    document = make_document(sheet)
    report = AuditReport(document=tmp_path / "TD-1001.dxf", findings=[finding(bbox=box(20, 20))])
    config = AuditConfig(output_dir=tmp_path / "overlays")

    written = render_overlays(document, report, config)
    assert len(written) == 1 and written[0].exists()

    from PIL import Image

    with Image.open(written[0]) as image:
        assert image.width > 100 and image.height > 100


def test_overlay_is_skipped_when_no_finding_is_located(tmp_path):
    pytest.importorskip("PIL")
    document = make_document(make_sheet())
    report = AuditReport(document=tmp_path / "x.dxf", findings=[])
    assert render_overlays(document, report, AuditConfig(output_dir=tmp_path)) == []


def test_summary_line(tmp_path):
    report = report_with_findings(tmp_path)
    assert "Kritik: 1" in report.summary_line("tr")
    assert "Critical: 1" in report.summary_line("en")
    assert AuditReport(document=tmp_path / "x.dxf").summary_line("tr") == "Bulgu yok"
