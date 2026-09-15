"""Section and detail markers: the half of a view reference that lives on the
part, not under it.

A view title ("KESİT A-A") is only one end of a reference.  The other end is the
**marker** on the parent view: the cutting plane with its arrows and letters, or
the bubble drawn around the region a detail enlarges.  A drawing where one end
exists without the other cannot be read - either the reader is sent to a view
that is not there, or shown a view without being told where it comes from.

Detection is deliberately conservative, because drawing offices mark sections
very differently:

* a **cutting plane** is recognised from its letter pair ("A-A").  Nothing else
  on a drawing is written that way, so the text alone is enough; a line on a
  non-part layer nearby only raises the confidence.
* a **detail bubble** needs its geometry.  Its text is a single letter, which is
  also how a datum feature symbol is written, so a bare letter counts only when
  it sits on a circle that is not part geometry.
"""

from __future__ import annotations

from dataclasses import dataclass

from catia_diff.config import AuditConfig
from catia_diff.extract.text_parsing import parse_bubble_label, parse_marker_label
from catia_diff.models.drawing import GeometryFeature, GeometryKind, Sheet
from catia_diff.models.geometry import BBox
from catia_diff.rules.analysis import _NON_FEATURE_LAYER_RE

#: How far from the text its geometry may sit, as a fraction of the sheet's
#: larger side.  A marker letter is written next to its line or bubble.
REACH_RATIO = 0.05

SECTION = "section"
DETAIL = "detail"


@dataclass(frozen=True)
class Marker:
    """One end of a view reference, drawn on the parent view."""

    kind: str  # SECTION | DETAIL
    label: str  # "A-A" for a section, "B" for a detail
    bbox: BBox
    object_ids: tuple[str, ...] = ()
    confidence: float = 1.0


def detect_markers(sheet: Sheet, config: AuditConfig | None = None) -> list[Marker]:
    """Every cutting-plane and detail marker the sheet draws."""
    config = config or AuditConfig()
    reach = max(sheet.width, sheet.height) * REACH_RATIO
    non_part = [f for f in sheet.features if _is_annotation_geometry(f)]

    markers: list[Marker] = []
    for annotation in sheet.annotations:
        if annotation.bbox is None:
            continue
        pair = parse_marker_label(annotation.text)
        if pair is not None:
            line = _nearest(annotation.bbox, non_part, reach, {GeometryKind.LINE,
                                                              GeometryKind.POLYLINE})
            markers.append(
                Marker(
                    kind=SECTION,
                    label=pair,
                    bbox=annotation.bbox,
                    object_ids=(annotation.id,) + ((line.id,) if line else ()),
                    # The letter pair is the evidence; the line corroborates it.
                    confidence=1.0 if line is not None else 0.8,
                )
            )
            continue
        letter = parse_bubble_label(annotation.text)
        if letter is None:
            continue
        bubble = _nearest(annotation.bbox, non_part, reach, {GeometryKind.CIRCLE,
                                                            GeometryKind.ARC})
        if bubble is None:
            continue  # a bare letter with no bubble is a datum symbol, not a marker
        markers.append(
            Marker(
                kind=DETAIL,
                label=letter,
                bbox=annotation.bbox.union(bubble.bbox) if bubble.bbox else annotation.bbox,
                object_ids=(annotation.id, bubble.id),
            )
        )
    return markers


def _is_annotation_geometry(feature: GeometryFeature) -> bool:
    """True for geometry that annotates rather than describes the part."""
    return bool(feature.layer and _NON_FEATURE_LAYER_RE.search(feature.layer))


def _nearest(
    box: BBox, features: list[GeometryFeature], reach: float, kinds: set[GeometryKind]
) -> GeometryFeature | None:
    best: tuple[float, GeometryFeature] | None = None
    for feature in features:
        if feature.kind not in kinds or feature.bbox is None:
            continue
        distance = _distance(box, feature.bbox)
        if distance <= reach and (best is None or distance < best[0]):
            best = (distance, feature)
    return best[1] if best else None


def _distance(a: BBox, b: BBox) -> float:
    dx = max(b.x0 - a.x1, a.x0 - b.x1, 0.0)
    dy = max(b.y0 - a.y1, a.y0 - b.y1, 0.0)
    return (dx * dx + dy * dy) ** 0.5


__all__ = ["DETAIL", "REACH_RATIO", "SECTION", "Marker", "detect_markers"]
