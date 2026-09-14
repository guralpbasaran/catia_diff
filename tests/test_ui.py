"""Dashboard: view models, upload handling and the audit round trip.

The presentation layer is pure Python, so most of this runs without Dash; the
few tests that need it are skipped when the optional extra is missing.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from catia_diff.errors import UnsupportedFormatError
from catia_diff.models.findings import (
    SEVERITY_COLORS,
    AuditReport,
    Category,
    Evidence,
    Finding,
    Severity,
)
from catia_diff.models.geometry import BBox
from catia_diff.reporting.normalize import overlay_numbers
from catia_diff.ui import presenters, service


def finding(
    rule_id: str = "DIM001",
    *,
    severity: Severity = Severity.MAJOR,
    category: Category = Category.DIMENSIONING,
    sheet: int = 0,
    located: bool = True,
    message: str = "message",
) -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=severity,
        category=category,
        title=f"{rule_id} title",
        title_tr=f"{rule_id} başlık",
        message=message,
        message_tr=f"{message} (tr)",
        suggestion="fix it",
        suggestion_tr="düzeltin",
        standards=("ISO 129-1 §4.1",),
        evidence=Evidence(
            sheet_index=sheet,
            bbox=BBox(x0=0, y0=0, x1=5, y1=5) if located else None,
            object_ids=("DIM0001",),
        ),
    )


def report(*findings: Finding, **kwargs) -> AuditReport:
    payload = {
        "document": Path("part.dxf"),
        "source_format": "dxf",
        "findings": list(findings),
        "document_stats": {"sheets": 1, "dimensions": 9},
        "rules_executed": 60,
        "duration_ms": 1234.0,
    }
    payload.update(kwargs)
    return AuditReport(**payload)


# ---------------------------------------------------------------------------
# presenters
def test_severity_cards_keep_the_zeros():
    cards = presenters.severity_cards(report(finding(severity=Severity.CRITICAL)), "tr")
    assert [card.severity for card in cards] == list(Severity)
    assert [card.count for card in cards] == [1, 0, 0, 0]
    assert cards[0].label == "Kritik"
    assert cards[0].color == SEVERITY_COLORS[Severity.CRITICAL]


def test_row_numbers_are_the_overlay_box_numbers():
    """Row n in the table must be box n on the marked-up sheet."""
    located_a = finding("DIM001", sheet=0)
    unlocated = finding("TB002", sheet=0, located=False)
    located_b = finding("DIM003", sheet=0)
    second_sheet = finding("DIM004", sheet=1)
    audit = report(located_a, unlocated, located_b, second_sheet)

    rows = presenters.finding_rows(audit, "tr")
    numbers = {row.rule_id: row.number for row in rows}

    assert numbers == {"DIM001": 1, "TB002": None, "DIM003": 2, "DIM004": 1}
    assert numbers == {
        f.rule_id: overlay_numbers(audit.findings).get(f.id) for f in audit.findings
    }


def test_rows_are_localised_and_keep_report_order():
    rows = presenters.finding_rows(report(finding(message="hello")), "tr")
    assert rows[0].message == "hello (tr)"
    assert rows[0].severity_label == "Majör"
    assert rows[0].category_label == "Ölçülendirme"
    assert presenters.finding_rows(report(finding(message="hello")), "en")[0].message == "hello"


def test_rows_filter_by_severity_and_category():
    audit = report(
        finding("DIM001", severity=Severity.CRITICAL),
        finding("TB002", severity=Severity.MINOR, category=Category.TITLE_BLOCK),
    )
    only_critical = presenters.finding_rows(audit, "tr", severities=[Severity.CRITICAL])
    only_title = presenters.finding_rows(audit, "tr", categories=[Category.TITLE_BLOCK])

    assert [row.rule_id for row in only_critical] == ["DIM001"]
    assert [row.rule_id for row in only_title] == ["TB002"]
    assert len(presenters.finding_rows(audit, "tr")) == 2


def test_chart_sorts_by_volume_and_drops_unused_severities():
    audit = report(
        finding("DIM001", severity=Severity.CRITICAL),
        finding("DIM002", severity=Severity.MAJOR),
        finding("TB002", severity=Severity.MAJOR, category=Category.TITLE_BLOCK),
    )
    chart = presenters.category_chart(presenters.finding_rows(audit, "tr"), "tr")

    assert chart.categories == ("Ölçülendirme", "Antet")
    assert [series.severity for series in chart.series] == [Severity.CRITICAL, Severity.MAJOR]
    assert [series.values for series in chart.series] == [(1, 0), (1, 1)]


def test_chart_is_empty_without_rows():
    assert presenters.category_chart([], "tr").is_empty


def test_gate_mirrors_the_cli_exit_codes():
    blocked = presenters.gate_status(report(finding(severity=Severity.CRITICAL)), Severity.CRITICAL)
    passed = presenters.gate_status(report(finding(severity=Severity.MINOR)), Severity.CRITICAL)
    unread = presenters.gate_status(report(extraction_failed=True), Severity.CRITICAL)

    assert (blocked.ok, blocked.exit_code) == (False, 1)
    assert (passed.ok, passed.exit_code) == (True, 0)
    assert (unread.ok, unread.exit_code) == (False, 2)
    assert "1" in blocked.text


def test_gate_threshold_is_honoured():
    minor_only = report(finding(severity=Severity.MINOR))
    assert presenters.gate_status(minor_only, Severity.MINOR).exit_code == 1
    assert presenters.gate_status(minor_only, Severity.MAJOR).exit_code == 0


def test_text_falls_back_to_english_and_passes_unknown_keys_through():
    assert presenters.t("tab_findings", "tr") == "Bulgular"
    assert presenters.t("tab_findings", "de") == "Findings"
    assert presenters.t("no-such-key", "tr") == "no-such-key"


def test_document_stats_are_labelled_and_carry_the_run_facts():
    rows = dict(presenters.document_stats(report(), "tr"))
    assert rows["Ölçü"] == "9"
    assert rows["Çalıştırılan kural"] == "60"
    assert rows["Süre"] == "1234 ms"


# ---------------------------------------------------------------------------
# service
def upload_payload(blob: bytes, mime: str = "application/octet-stream") -> str:
    return f"data:{mime};base64," + base64.b64encode(blob).decode("ascii")


def test_decode_upload_writes_the_file(tmp_path):
    path = service.decode_upload(upload_payload(b"0\nSECTION\n"), "part.dxf", tmp_path)
    assert path.read_bytes() == b"0\nSECTION\n"
    assert path.name == "part.dxf"


def test_decode_upload_strips_directories_from_the_name(tmp_path):
    path = service.decode_upload(upload_payload(b"x"), "../../etc/part.dxf", tmp_path)
    assert path.parent == tmp_path
    assert path.name == "part.dxf"


def test_decode_upload_rejects_an_unsupported_format(tmp_path):
    with pytest.raises(UnsupportedFormatError):
        service.decode_upload(upload_payload(b"x"), "model.stp", tmp_path)


def test_decode_upload_explains_dwg(tmp_path):
    with pytest.raises(UnsupportedFormatError, match="DXF"):
        service.decode_upload(upload_payload(b"x"), "part.dwg", tmp_path)


def test_decode_upload_rejects_oversize(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "MAX_UPLOAD_BYTES", 4)
    with pytest.raises(service.UploadError, match="limit"):
        service.decode_upload(upload_payload(b"much too long"), "part.dxf", tmp_path)


def test_decode_upload_rejects_broken_payloads(tmp_path):
    with pytest.raises(service.UploadError):
        service.decode_upload("data:text/plain;base64,not base64!", "part.dxf", tmp_path)
    with pytest.raises(service.UploadError):
        service.decode_upload("", "part.dxf", tmp_path)
    with pytest.raises(service.UploadError):
        service.decode_upload(upload_payload(b""), "part.dxf", tmp_path)


def test_prune_runs_keeps_the_newest(tmp_path):
    for index in range(4):
        run = tmp_path / f"run{index}"
        run.mkdir()
        (run / "f").write_text("x")
    service.prune_runs(tmp_path, keep=2)
    assert len(list(tmp_path.iterdir())) == 2


def test_run_audit_reports_a_missing_file(tmp_path):
    outcome = service.run_audit(path=tmp_path / "nope.dxf", root=tmp_path)
    assert not outcome.ok
    assert outcome.report_json is None
    assert "nope.dxf" in outcome.message


def test_run_audit_without_input_is_an_error(tmp_path):
    outcome = service.run_audit(root=tmp_path)
    assert not outcome.ok
    assert outcome.report is None


def test_run_audit_rejects_an_unsupported_upload(tmp_path):
    outcome = service.run_audit(
        contents=upload_payload(b"x"), filename="model.stp", root=tmp_path
    )
    assert not outcome.ok
    assert "stp" in outcome.message


def test_report_download_renders_every_format():
    audit = report(finding())
    name, content, mime = service.report_download(audit, "json")
    assert name == "part_audit.json" and "findings" in content and mime == "application/json"

    name, content, _ = service.report_download(audit, "md", "tr")
    assert name.endswith(".md") and "DIM001" in content

    name, content, _ = service.report_download(audit, "html", "tr")
    assert name.endswith(".html") and "<" in content

    with pytest.raises(ValueError):
        service.report_download(audit, "pdf")


def test_report_survives_the_round_trip_through_the_browser_store():
    audit = report(finding("DIM011", severity=Severity.CRITICAL))
    restored = service.parse_report(audit.model_dump_json())
    assert restored is not None
    assert [f.id for f in restored.findings] == [f.id for f in audit.findings]
    assert restored.findings[0].severity is Severity.CRITICAL
    assert service.parse_report(None) is None


# ---------------------------------------------------------------------------
# end to end, with the real pipeline
def test_run_audit_on_the_sample_drawing(tmp_path, sample_dxf):
    pytest.importorskip("PIL")
    outcome = service.run_audit(path=sample_dxf, language="tr", root=tmp_path)

    assert outcome.ok
    assert outcome.report is not None
    assert outcome.overlay_uri.startswith("data:image/png;base64,")

    rows = presenters.finding_rows(outcome.report, "tr")
    assert any(row.rule_id == "DIM011" for row in rows)
    assert presenters.gate_status(outcome.report, Severity.CRITICAL, "tr").exit_code == 1
    # The audit must not litter the drawing's own folder.
    assert not list(sample_dxf.parent.glob("*_audit.*"))


def test_run_audit_is_quiet_on_a_complete_drawing(tmp_path, sample_dxf_complete):
    outcome = service.run_audit(path=sample_dxf_complete, language="tr", root=tmp_path)
    assert outcome.ok
    rows = presenters.finding_rows(outcome.report, "tr")
    assert not [row for row in rows if row.rule_id in {"DIM011", "DIM012", "DIM013"}]


# ---------------------------------------------------------------------------
# Dash wiring
def collect_ids(component, found: set[str] | None = None) -> set[str]:
    found = set() if found is None else found
    element_id = getattr(component, "id", None)
    if isinstance(element_id, str):
        found.add(element_id)
    children = getattr(component, "children", None)
    if isinstance(children, (list, tuple)):
        for child in children:
            collect_ids(child, found)
    elif children is not None:
        collect_ids(children, found)
    return found


def test_layout_contains_every_id_the_callbacks_address():
    pytest.importorskip("dash")
    from catia_diff.ui.app import CHROME, create_app

    app = create_app(language="tr")
    ids = collect_ids(app.layout)

    assert CHROME.keys() <= ids
    assert {"upload", "table", "chart", "store-report", "download", "label-drop-hint"} <= ids
    for callback in app.callback_map.values():
        outputs = callback["output"]
        outputs = outputs if isinstance(outputs, list) else [outputs]
        wired = (
            [dependency.component_id for dependency in outputs]
            + [dependency["id"] for dependency in callback["inputs"]]
            + [dependency["id"] for dependency in callback["state"]]
        )
        assert set(wired) <= ids


def test_figure_has_one_trace_per_severity_present():
    pytest.importorskip("plotly")
    from catia_diff.ui.charts import category_figure

    audit = report(
        finding("DIM001", severity=Severity.CRITICAL),
        finding("TB002", severity=Severity.MAJOR, category=Category.TITLE_BLOCK),
    )
    chart = presenters.category_chart(presenters.finding_rows(audit, "tr"), "tr")
    figure = category_figure(chart)

    assert [trace.name for trace in figure.data] == ["Kritik", "Majör"]
    assert figure.layout.barmode == "stack"
    assert figure.data[0].marker.color == SEVERITY_COLORS[Severity.CRITICAL]


# ---------------------------------------------------------------------------
# views: the functions the callbacks delegate to
def output_counts(app) -> dict[str, int]:
    """How many values each callback must return, keyed by its first output."""
    counts: dict[str, int] = {}
    for callback in app.callback_map.values():
        outputs = callback["output"]
        outputs = outputs if isinstance(outputs, list) else [outputs]
        counts[outputs[0].component_id] = len(outputs)
    return counts


def test_views_return_exactly_as_many_values_as_they_have_outputs():
    """A miscounted tuple only fails in the browser otherwise."""
    pytest.importorskip("dash")
    from catia_diff.ui import app as ui_app

    counts = output_counts(ui_app.create_app())
    assert len(ui_app.chrome_view("tr")) == counts["filter-severity"]
    assert len(ui_app.results_view(None, None, None, "tr", "critical", None)) == counts["cards"]
    assert len(ui_app.panel_view("findings")) == counts["gate"]


def test_results_view_without_a_report_clears_the_screen():
    pytest.importorskip("dash")
    from catia_diff.ui.app import results_view

    cards, _figure, chart_style, rows, row_ids, gate, overlay, stats, warnings = results_view(
        None, None, None, "tr", "critical", None
    )
    assert (cards, rows, row_ids) == ([], [], [])
    assert chart_style == {"display": "none"}
    assert (gate, overlay, stats, warnings) == (None, None, None, None)


def test_results_view_fills_the_table_and_respects_the_filters():
    pytest.importorskip("dash")
    from catia_diff.ui.app import results_view

    payload = report(
        finding("DIM011", severity=Severity.CRITICAL),
        finding("TB002", severity=Severity.MINOR, category=Category.TITLE_BLOCK),
    ).model_dump_json()

    _cards, _figure, _style, rows, row_ids, _gate, _overlay, _stats, _warn = results_view(
        payload, None, None, "tr", "critical", None
    )
    assert [row["rule_id"] for row in rows] == ["DIM011", "TB002"]
    assert rows[0]["severity"] == "Kritik"
    assert len(row_ids) == 2

    _c, _f, _s, filtered, _ids, _g, _o, _st, _w = results_view(
        payload, ["critical"], None, "tr", "critical", None
    )
    assert [row["rule_id"] for row in filtered] == ["DIM011"]


def test_detail_view_prompts_until_a_row_is_picked():
    pytest.importorskip("dash")
    from catia_diff.ui.app import detail_view

    audit = report(finding("DIM011"))
    payload = audit.model_dump_json()
    prompt = detail_view(None, "tr", payload, [audit.findings[0].id])
    assert "seçin" in str(prompt.children)

    panel = detail_view({"row": 0}, "tr", payload, [audit.findings[0].id])
    rendered = str(panel.children)
    assert "DIM011" in rendered
    assert "düzeltin" in rendered  # the suggestion, localised


def test_detail_view_ignores_a_stale_selection():
    pytest.importorskip("dash")
    from dash import no_update

    from catia_diff.ui.app import detail_view

    payload = report(finding()).model_dump_json()
    assert detail_view({"row": 7}, "tr", payload, ["only-one"]) is no_update


def test_download_view_hands_dash_a_named_file():
    pytest.importorskip("dash")
    from catia_diff.ui.app import download_view

    payload = report(finding()).model_dump_json()
    data = download_view("dl-md", payload, "tr")
    assert data["filename"] == "part_audit.md"
    assert data["type"] == "text/markdown"
    assert "DIM001" in data["content"]


def test_audit_view_runs_the_bundled_sample():
    pytest.importorskip("dash")
    pytest.importorskip("ezdxf")
    from catia_diff.ui.app import audit_view

    if service.sample_drawing() is None:
        pytest.skip("examples/sample_plate.dxf is not part of this install")

    report_json, overlay, status, style = audit_view("sample-btn", None, None, "ISO", "tr")
    assert report_json and "DIM011" in report_json
    assert overlay is None or overlay.startswith("data:image/png;base64,")
    assert style == {}
    assert "sample_plate.dxf" in str(status.children)


def test_audit_view_reports_a_bad_upload_without_showing_results():
    pytest.importorskip("dash")
    from catia_diff.ui.app import audit_view

    report_json, overlay, status, style = audit_view(
        "upload", upload_payload(b"x"), "model.stp", "ISO", "tr"
    )
    assert report_json is None and overlay is None
    assert style == {"display": "none"}
    assert "⚠" in str(status.children)


def test_the_package_says_how_to_install_dash_when_it_is_missing(monkeypatch):
    """Importing the dashboard without the extra must not raise ImportError."""
    import builtins
    import sys

    from catia_diff import ui
    from catia_diff.errors import MissingDependencyError

    monkeypatch.delitem(sys.modules, "catia_diff.ui.app", raising=False)
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "catia_diff.ui" and "app" in (fromlist or ()):
            raise ImportError("No module named 'dash'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(MissingDependencyError, match=r"catia-diff\[ui\]"):
        ui.create_app()


def test_the_package_builds_the_app_when_dash_is_there():
    pytest.importorskip("dash")
    from catia_diff import ui

    assert type(ui.create_app(language="en")).__name__ == "Dash"
