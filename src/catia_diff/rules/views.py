"""View segmentation.

A sheet holds several projections of the same part, and a dimension only
constrains the view it belongs to: the front view's 80 mm says nothing about
the side view's geometry.  Before any coverage analysis can run, the objects on
a sheet have to be split into views.

Drawings do not record that grouping - it is implied by *space*: the geometry
of one view sits together and is separated from the next by a visible gap.  So
the segmentation clusters geometry by proximity (union-find over a uniform
grid, near-linear), then attaches every callout to the view it annotates.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from catia_diff.config import AuditConfig
from catia_diff.extract.title_block_scan import title_block_region
from catia_diff.models.drawing import DrawingObject, GeometryFeature, Sheet, View
from catia_diff.models.geometry import BBox

#: A cluster with fewer objects than this is noise (a stray symbol, a leader).
MIN_CLUSTER_OBJECTS = 2


@dataclass
class _Cluster:
    bbox: BBox
    member_ids: list[str] = field(default_factory=list)

    def add(self, obj: DrawingObject) -> None:
        assert obj.bbox is not None
        self.bbox = self.bbox.union(obj.bbox)
        self.member_ids.append(obj.id)


class _UnionFind:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, item: int) -> int:
        while self._parent[item] != item:
            self._parent[item] = self._parent[self._parent[item]]
            item = self._parent[item]
        return item

    def union(self, a: int, b: int) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self._parent[root_a] = root_b


def segment_views(sheet: Sheet, config: AuditConfig | None = None) -> list[View]:
    """Split ``sheet`` into geometric views and stamp ``view_id`` on its objects.

    Returns the views in reading order (top-left first).  Caption views that the
    extractor produced from text such as ``SECTION A-A`` are consumed: their
    label is transferred to the geometric view they sit on.
    """
    config = config or AuditConfig()
    features = [f for f in sheet.features if f.bbox is not None and f.bbox.area >= 0.0]
    captions = [view for view in sheet.views if not view.is_geometric]
    if not features:
        return captions

    gap = max(sheet.width, sheet.height) * config.view_gap_ratio
    clusters = _cluster_features(features, gap)
    excluded = title_block_region(sheet.bbox, sheet.coordinate_space)
    clusters = [
        cluster
        for cluster in clusters
        if len(cluster.member_ids) >= MIN_CLUSTER_OBJECTS or not excluded.contains(cluster.bbox)
    ]
    if not clusters:
        return captions

    views = _build_views(sheet, clusters)
    _assign_callouts(sheet, views, gap)
    _apply_captions(views, captions)
    sheet.views = views
    return views


# --------------------------------------------------------------------------
def _cluster_features(features: list[GeometryFeature], gap: float) -> list[_Cluster]:
    """Union features whose boxes are closer than ``gap``.

    Buckets the boxes on a grid of cell size ``gap`` so only neighbouring cells
    are compared - an O(n²) sweep would not survive a real drawing.
    """
    union = _UnionFind(len(features))
    cell = max(gap, 1e-9)
    grid: dict[tuple[int, int], list[int]] = {}
    for index, feature in enumerate(features):
        box = feature.bbox
        assert box is not None
        for cx in range(int(box.x0 // cell), int(box.x1 // cell) + 1):
            for cy in range(int(box.y0 // cell), int(box.y1 // cell) + 1):
                grid.setdefault((cx, cy), []).append(index)

    for (cx, cy), members in grid.items():
        neighbours: list[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neighbours.extend(grid.get((cx + dx, cy + dy), ()))
        for first in members:
            box_a = features[first].bbox.expanded(gap / 2)
            for second in neighbours:
                if first >= second:
                    continue
                if box_a.intersects(features[second].bbox.expanded(gap / 2)):
                    union.union(first, second)

    clusters: dict[int, _Cluster] = {}
    for index, feature in enumerate(features):
        root = union.find(index)
        cluster = clusters.get(root)
        if cluster is None:
            clusters[root] = _Cluster(bbox=feature.bbox, member_ids=[feature.id])
        else:
            cluster.add(feature)
    return list(clusters.values())


def _build_views(sheet: Sheet, clusters: list[_Cluster]) -> list[View]:
    """Turn clusters into views, ordered the way a reader scans the sheet."""
    flip = sheet.coordinate_space.value == "y_up"
    clusters.sort(key=lambda c: (-c.bbox.center.y if flip else c.bbox.center.y, c.bbox.center.x))
    views: list[View] = []
    for index, cluster in enumerate(clusters, start=1):
        view = View(
            id=f"VIEW{index:02d}",
            bbox=cluster.bbox,
            member_ids=list(cluster.member_ids),
            confidence=0.8,
        )
        for member_id in cluster.member_ids:
            obj = sheet.find_object(member_id)
            if obj is not None:
                obj.view_id = view.id
        views.append(view)
    return views


def _assign_callouts(sheet: Sheet, views: list[View], gap: float) -> None:
    """Attach dimensions and symbols to the view they annotate.

    A callout is placed outside the geometry it belongs to, so containment
    alone is not enough: the nearest view within a reasonable reach wins.
    """
    callouts: Iterable[DrawingObject] = (
        *sheet.dimensions,
        *sheet.geometric_tolerances,
        *sheet.datums,
        *sheet.surface_finishes,
        *sheet.welds,
    )
    reach = gap * 4.0
    for obj in callouts:
        if obj.bbox is None:
            continue
        best: tuple[float, View] | None = None
        for view in views:
            assert view.bbox is not None
            distance = _distance(view.bbox, obj.bbox)
            if best is None or distance < best[0]:
                best = (distance, view)
        if best is not None and best[0] <= reach:
            obj.view_id = best[1].id
            best[1].member_ids.append(obj.id)


def _apply_captions(views: list[View], captions: list[View]) -> None:
    """Move 'SECTION A-A' style labels onto the view they caption."""
    for caption in captions:
        if caption.bbox is None or not views:
            continue
        nearest = min(views, key=lambda view: _distance(view.bbox, caption.bbox))
        if nearest.label is None:
            nearest.label = caption.label
            upper = (caption.label or "").upper()
            nearest.is_section = "SECTION" in upper or "KESIT" in upper or "KESİT" in upper
            nearest.is_detail = "DETAIL" in upper or "DETAY" in upper


def _distance(a: BBox, b: BBox) -> float:
    """Gap between two boxes; 0 when they touch or overlap."""
    dx = max(a.x0 - b.x1, b.x0 - a.x1, 0.0)
    dy = max(a.y0 - b.y1, b.y0 - a.y1, 0.0)
    return (dx * dx + dy * dy) ** 0.5
