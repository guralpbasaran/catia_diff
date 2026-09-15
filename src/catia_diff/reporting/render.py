"""Report rendering: JSON, Markdown and a self-contained HTML page."""

from __future__ import annotations

import html
import json
from pathlib import Path

from catia_diff.config import AuditConfig
from catia_diff.models.findings import SEVERITY_COLORS, AuditReport, Finding, Severity
from catia_diff.reporting.coverage import coverage_rows, coverage_text

TEMPLATE_DIR = Path(__file__).parent / "templates"


def write_reports(report: AuditReport, config: AuditConfig) -> list[Path]:
    """Write every requested format; returns the written paths."""
    out_dir = Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = report.document.stem or "drawing"
    written: list[Path] = []

    for fmt in config.formats:
        fmt = fmt.lower().strip()
        if fmt == "json":
            path = out_dir / f"{stem}_audit.json"
            path.write_text(to_json(report), encoding="utf-8")
        elif fmt in {"md", "markdown"}:
            path = out_dir / f"{stem}_audit.md"
            path.write_text(to_markdown(report, config.language), encoding="utf-8")
        elif fmt == "html":
            path = out_dir / f"{stem}_audit.html"
            path.write_text(to_html(report, config.language), encoding="utf-8")
        else:
            continue
        written.append(path)
    return written


# --------------------------------------------------------------------------
def to_json(report: AuditReport) -> str:
    payload = report.model_dump(mode="json")
    payload["summary"] = {
        "by_severity": {sev.value: count for sev, count in report.counts_by_severity().items()},
        "by_category": {cat.value: count for cat, count in report.counts_by_category().items()},
        "worst_severity": report.worst_severity.value if report.worst_severity else None,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def to_markdown(report: AuditReport, lang: str = "en") -> str:
    t = _TEXT[lang if lang in _TEXT else "en"]
    counts = report.counts_by_severity()
    lines = [
        f"# {t['title']}: {report.document.name}",
        "",
        f"- {t['profile']}: **{report.profile}**",
        f"- {t['format']}: `{report.source_format}`",
        f"- {t['generated']}: {report.generated_at:%Y-%m-%d %H:%M UTC}",
        f"- {t['rules']}: {report.rules_executed}",
        f"- {t['duration']}: {report.duration_ms:.0f} ms",
        "",
        f"## {t['summary']}",
        "",
        f"| {t['severity']} | {t['count']} |",
        "| --- | ---: |",
    ]
    for severity in Severity:
        lines.append(f"| {severity.label(lang)} | {counts[severity]} |")
    lines.extend(["", f"**{t['total']}: {len(report.findings)}**", ""])

    stats = ", ".join(f"{key}: {value}" for key, value in report.document_stats.items())
    if stats:
        lines.extend([f"## {t['extracted']}", "", stats, ""])

    lines.extend(_markdown_coverage(report, lang))

    for severity in Severity:
        group = report.by_severity(severity)
        if not group:
            continue
        lines.extend([f"## {severity.label(lang)} ({len(group)})", ""])
        for finding in group:
            lines.extend(_markdown_finding(finding, lang, t))
    if report.overlays:
        lines.extend([f"## {t['overlays']}", ""])
        lines.extend(f"- `{path}`" for path in report.overlays)
        lines.append("")
    if report.warnings:
        lines.extend([f"## {t['warnings']}", ""])
        lines.extend(f"- {warning}" for warning in report.warnings)
        lines.append("")
    return "\n".join(lines)


def _markdown_coverage(report: AuditReport, lang: str) -> list[str]:
    """Per-view coverage, so the report says what is dimensioned, not only what is wrong."""
    rows = coverage_rows(report.coverage, lang)
    if not rows:
        return []
    c = coverage_text(lang)
    lines = [f"## {c['heading']}", "", f"| {c['view']} | {c['state']} |", "| --- | --- |"]
    lines.extend(f"| {name} | {state} |" for name, state, _ in rows)
    lines.append("")
    return lines


def _markdown_finding(finding: Finding, lang: str, t: dict[str, str]) -> list[str]:
    location = f"{t['sheet']} {finding.evidence.sheet_index + 1}"
    objects = ", ".join(finding.evidence.object_ids)
    lines = [
        f"### `{finding.rule_id}` {finding.localized_title(lang)}",
        "",
        f"- **{t['where']}:** {location}" + (f" - `{objects}`" if objects else ""),
        f"- **{t['finding']}:** {finding.localized_message(lang)}",
    ]
    suggestion = finding.localized_suggestion(lang)
    if suggestion:
        lines.append(f"- **{t['fix']}:** {suggestion}")
    if finding.standards:
        lines.append(f"- **{t['standard']}:** {', '.join(finding.standards)}")
    if finding.confidence < 1.0:
        lines.append(f"- **{t['confidence']}:** {finding.confidence:.0%}")
    lines.append("")
    return lines


def to_html(report: AuditReport, lang: str = "en") -> str:
    context = _html_context(report, lang)
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=select_autoescape(["html"]),
        )
        return env.get_template("report.html.j2").render(**context)
    except ImportError:
        return _fallback_html(context)


def _html_context(report: AuditReport, lang: str) -> dict:
    t = _TEXT[lang if lang in _TEXT else "en"]
    counts = report.counts_by_severity()
    rows = []
    for index, finding in enumerate(report.findings, start=1):
        rows.append(
            {
                "number": index,
                "rule_id": finding.rule_id,
                "severity": finding.severity.value,
                "severity_label": finding.severity.label(lang),
                "color": SEVERITY_COLORS[finding.severity],
                "category": finding.category.label(lang),
                "title": finding.localized_title(lang),
                "message": finding.localized_message(lang),
                "suggestion": finding.localized_suggestion(lang) or "",
                "standards": ", ".join(finding.standards),
                "sheet": finding.evidence.sheet_index + 1,
                "objects": ", ".join(finding.evidence.object_ids),
                "confidence": f"{finding.confidence:.0%}",
                "snippet": finding.evidence.snippet or "",
            }
        )
    return {
        "t": t,
        "lang": lang,
        "report": report,
        "document_name": report.document.name,
        "profile": report.profile,
        "source_format": report.source_format,
        "generated_at": f"{report.generated_at:%Y-%m-%d %H:%M UTC}",
        "duration_ms": f"{report.duration_ms:.0f}",
        "rules_executed": report.rules_executed,
        "counts": [
            {
                "key": severity.value,
                "label": severity.label(lang),
                "count": counts[severity],
                "color": SEVERITY_COLORS[severity],
            }
            for severity in Severity
        ],
        "categories": [
            {"label": category.label(lang), "count": count}
            for category, count in report.counts_by_category().items()
        ],
        "findings": rows,
        "stats": report.document_stats,
        "coverage": [
            {"view": name, "state": state, "complete": complete}
            for name, state, complete in coverage_rows(report.coverage, lang)
        ],
        "coverage_text": coverage_text(lang),
        "overlays": [str(path.name) for path in report.overlays],
        "warnings": report.warnings,
        "traces": report.agent_traces,
        "total": len(report.findings),
    }


def _fallback_html(context: dict) -> str:
    """Minimal HTML used when jinja2 is not installed."""
    t = context["t"]
    esc = html.escape
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{esc(t['title'])} - {esc(context['document_name'])}</title>",
        "<style>body{font-family:system-ui,sans-serif;margin:2rem;color:#1b1f23}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:.5rem;"
        "text-align:left;vertical-align:top}th{background:#f4f5f7}.sev{color:#fff;padding:.1rem .5rem;"
        "border-radius:.5rem;font-size:.8rem}</style></head><body>",
        f"<h1>{esc(t['title'])}: {esc(context['document_name'])}</h1>",
        f"<p>{esc(t['profile'])}: <b>{esc(context['profile'])}</b> &middot; "
        f"{esc(t['format'])}: <code>{esc(context['source_format'])}</code> &middot; "
        f"{esc(t['generated'])}: {esc(context['generated_at'])}</p>",
        "<p>" + " &middot; ".join(
            f"<span class='sev' style='background:{item['color']}'>{esc(item['label'])}: {item['count']}</span>"
            for item in context["counts"]
        ) + "</p>",
        f"<table><tr><th>#</th><th>{esc(t['severity'])}</th><th>{esc(t['rule'])}</th>"
        f"<th>{esc(t['finding'])}</th><th>{esc(t['fix'])}</th><th>{esc(t['sheet'])}</th></tr>",
    ]
    for row in context["findings"]:
        parts.append(
            f"<tr><td>{row['number']}</td>"
            f"<td><span class='sev' style='background:{row['color']}'>{esc(row['severity_label'])}</span></td>"
            f"<td><code>{esc(row['rule_id'])}</code><br><small>{esc(row['standards'])}</small></td>"
            f"<td><b>{esc(row['title'])}</b><br>{esc(row['message'])}</td>"
            f"<td>{esc(row['suggestion'])}</td><td>{row['sheet']}</td></tr>"
        )
    parts.append("</table>")
    if context["coverage"]:
        c = context["coverage_text"]
        parts.append(
            f"<h2>{esc(c['heading'])}</h2><table>"
            f"<tr><th>{esc(c['view'])}</th><th>{esc(c['state'])}</th></tr>"
        )
        parts.extend(
            f"<tr><th>{esc(row['view'])}</th><td>{esc(row['state'])}</td></tr>"
            for row in context["coverage"]
        )
        parts.append("</table>")
    for overlay in context["overlays"]:
        parts.append(f"<h2>{esc(t['overlays'])}</h2><img src='{esc(overlay)}' style='max-width:100%'>")
    parts.append("</body></html>")
    return "".join(parts)


_TEXT: dict[str, dict[str, str]] = {
    "en": {
        "title": "Technical drawing audit",
        "profile": "Standard profile",
        "format": "Source format",
        "generated": "Generated",
        "rules": "Rules executed",
        "duration": "Duration",
        "summary": "Summary",
        "severity": "Severity",
        "count": "Count",
        "total": "Total findings",
        "extracted": "Extracted objects",
        "overlays": "Marked-up sheets",
        "warnings": "Warnings",
        "sheet": "Sheet",
        "where": "Location",
        "finding": "Finding",
        "fix": "Suggested fix",
        "standard": "Standard",
        "confidence": "Confidence",
        "rule": "Rule",
        "filter_all": "All",
        "no_findings": "No findings - the drawing passed every enabled check.",
        "agents": "Agent trace",
    },
    "tr": {
        "title": "Teknik resim denetim raporu",
        "profile": "Standart profili",
        "format": "Kaynak format",
        "generated": "Oluşturulma",
        "rules": "Çalıştırılan kural",
        "duration": "Süre",
        "summary": "Özet",
        "severity": "Önem",
        "count": "Adet",
        "total": "Toplam bulgu",
        "extracted": "Çıkarılan nesneler",
        "overlays": "İşaretlenmiş sayfalar",
        "warnings": "Uyarılar",
        "sheet": "Sayfa",
        "where": "Konum",
        "finding": "Bulgu",
        "fix": "Önerilen düzeltme",
        "standard": "Standart",
        "confidence": "Güven",
        "rule": "Kural",
        "filter_all": "Tümü",
        "no_findings": "Bulgu yok - resim etkin tüm denetimlerden geçti.",
        "agents": "Ajan izi",
    },
}
