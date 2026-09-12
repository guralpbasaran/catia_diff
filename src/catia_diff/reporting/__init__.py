"""Report generation: normalisation, overlays and output formats."""

from catia_diff.reporting.normalize import dedupe, normalize_findings, sort_findings
from catia_diff.reporting.overlay import render_overlays
from catia_diff.reporting.render import to_html, to_json, to_markdown, write_reports

__all__ = [
    "dedupe",
    "normalize_findings",
    "render_overlays",
    "sort_findings",
    "to_html",
    "to_json",
    "to_markdown",
    "write_reports",
]
