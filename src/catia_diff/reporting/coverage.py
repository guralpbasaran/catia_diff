"""Dimensional coverage, summarised once for every surface that shows it.

The constraint graph already knows which axis of which view is short a
dimension.  Three surfaces want to say so - the overlay draws the missing
dimension, the HTML report prints a per-view table, and the document stats
count the axes - and if each derived it separately they would eventually
disagree.  They read this instead.

The summary is filtered by the findings, not by the graph alone.  That is the
whole point: a hole on a bolt circle is left free by the graph but is *not*
reported (``DIM017`` names the pitch circle instead), and an axis a neighbouring
view already fixes is inherited rather than missing.  Publishing the raw graph
numbers would put gaps in the report that the audit deliberately does not
raise - a report contradicting itself is worse than a report saying less.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from catia_diff.config import AuditConfig
from catia_diff.models.drawing import DrawingDocument, Sheet
from catia_diff.models.findings import AxisCoverage, CoverageGap, Finding, ViewCoverage
from catia_diff.models.geometry import BBox
from catia_diff.rules import constraints
from catia_diff.rules.projection import inherited_axes

#: The rules that report a missing dimension.  A gap is drawn only when one of
#: them raised it, so the overlay can never show a gap the report does not.
COVERAGE_RULES = ("DIM011", "DIM012")


def summarize_coverage(
    document: DrawingDocument,
    findings: Sequence[Finding],
    config: AuditConfig | None = None,
) -> list[ViewCoverage]:
    """Coverage per view, for every sheet the graph could analyse."""
    config = config or AuditConfig()
    reported = _reported_features(findings)
    out: list[ViewCoverage] = []
    for sheet in document.sheets:
        graph = constraints.sheet_coverage(sheet, config)
        if not graph:
            continue  # no interval data: the graph did not run, so we say nothing
        inherited = inherited_axes(sheet, graph, config)
        for view, axes in graph:
            out.append(
                ViewCoverage(
                    sheet_index=sheet.index,
                    view_id=view.id,
                    label=view.label,
                    axes=tuple(
                        _axis(sheet, axis, inherited.get(view.id, set()), reported)
                        for axis in axes
                    ),
                )
            )
    return out


def _reported_features(findings: Iterable[Finding]) -> set[str]:
    return {
        object_id
        for finding in findings
        if finding.rule_id in COVERAGE_RULES
        for object_id in finding.evidence.object_ids
    }


def _axis(
    sheet: Sheet,
    axis: constraints.AxisCoverage,
    inherited: set[str],
    reported: set[str],
) -> AxisCoverage:
    if axis.label in inherited:
        # An aligned view dimensions this direction; nothing is missing here.
        return AxisCoverage(axis=axis.label, redundant=axis.cycles, inherited=True)
    return AxisCoverage(
        axis=axis.label,
        gaps=tuple(_gaps(sheet, axis, reported)),
        redundant=axis.cycles,
    )


def _gaps(
    sheet: Sheet, axis: constraints.AxisCoverage, reported: set[str]
) -> Iterable[CoverageGap]:
    anchors = [axis.nodes[index].coordinate for index in axis.components[0]] if axis.components else []
    for group in axis.unconstrained:
        feature_ids = tuple(fid for node in group for fid in node.feature_ids)
        if not reported.intersection(feature_ids):
            continue  # the audit did not raise this one - see the module docstring
        coordinate = group[0].coordinate
        yield CoverageGap(
            coordinate=coordinate,
            anchor=min(anchors, key=lambda value: abs(value - coordinate), default=None),
            bbox=_span(sheet, feature_ids),
            feature_ids=feature_ids,
        )


def _span(sheet: Sheet, feature_ids: tuple[str, ...]) -> BBox | None:
    wanted = set(feature_ids)
    box: BBox | None = None
    for feature in sheet.features:
        if feature.id in wanted and feature.bbox is not None:
            box = feature.bbox if box is None else box.union(feature.bbox)
    return box


#: How each axis state is written out, in both languages.  The HTML report, the
#: Markdown report and the dashboard all print these, so they are worded once.
_AXIS_TEXT = {
    "en": {
        "missing": "{n} missing",
        "redundant": "{n} redundant",
        "inherited": "from an aligned view",
        "complete": "complete",
        "heading": "Dimensional coverage",
        "view": "View",
        "state": "State",
    },
    "tr": {
        "missing": "{n} eksik",
        "redundant": "{n} fazla",
        "inherited": "komşu görünüşten",
        "complete": "tam",
        "heading": "Ölçülendirme kapsamı",
        "view": "Görünüş",
        "state": "Durum",
    },
}


def coverage_text(lang: str = "en") -> dict[str, str]:
    """The captions a coverage table needs."""
    return _AXIS_TEXT[lang if lang in _AXIS_TEXT else "en"]


def describe_axis(axis: AxisCoverage, lang: str = "en") -> str:
    """One axis as a phrase: 'Y: 1 eksik', 'X ✔', 'X ✔ (komşu görünüşten)'."""
    t = coverage_text(lang)
    if axis.gaps:
        return f"{axis.axis}: {t['missing'].format(n=axis.missing)}"
    if axis.redundant:
        return f"{axis.axis}: {t['redundant'].format(n=axis.redundant)}"
    if axis.inherited:
        return f"{axis.axis} ✔ ({t['inherited']})"
    return f"{axis.axis} ✔"


def coverage_rows(
    coverage: Sequence[ViewCoverage], lang: str = "en"
) -> list[tuple[str, str, bool]]:
    """``(view name, state, is_complete)`` per view, ready to print."""
    return [
        (
            view.name(lang),
            " · ".join(describe_axis(axis, lang) for axis in view.axes)
            or coverage_text(lang)["complete"],
            view.is_complete,
        )
        for view in coverage
    ]


def unconstrained_axes(coverage: Sequence[ViewCoverage]) -> int:
    """How many (view, axis) pairs the drawing leaves unconstrained."""
    return sum(1 for view in coverage for axis in view.axes if axis.gaps)


__all__ = [
    "COVERAGE_RULES",
    "coverage_rows",
    "coverage_text",
    "describe_axis",
    "summarize_coverage",
    "unconstrained_axes",
]
