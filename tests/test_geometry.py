from catia_diff.models.geometry import BBox, CoordinateSpace, Point2D


def test_bbox_normalises_corner_order():
    box = BBox(x0=10, y0=8, x1=2, y1=1)
    assert (box.x0, box.y0, box.x1, box.y1) == (2, 1, 10, 8)
    assert box.width == 8 and box.height == 7


def test_bbox_from_sequence_and_points():
    assert BBox.model_validate([1, 2, 3, 4]).as_tuple() == (1, 2, 3, 4)
    built = BBox.from_points([Point2D(x=0, y=0), Point2D(x=5, y=2), (3, -1)])
    assert built.as_tuple() == (0, -1, 5, 2)


def test_intersection_and_iou():
    a = BBox(x0=0, y0=0, x1=10, y1=10)
    b = BBox(x0=5, y0=5, x1=15, y1=15)
    assert a.intersection(b).as_tuple() == (5, 5, 10, 10)
    assert a.iou(b) == 25 / 175
    assert a.iou(BBox(x0=20, y0=20, x1=30, y1=30)) == 0.0
    assert a.intersects(b)


def test_contains_and_transforms():
    outer = BBox(x0=0, y0=0, x1=10, y1=10)
    assert outer.contains(BBox(x0=1, y0=1, x1=2, y1=2))
    assert not outer.contains(BBox(x0=1, y0=1, x1=20, y1=2))
    assert outer.contains_point(Point2D(x=5, y=5))
    assert outer.translated(2, 3).as_tuple() == (2, 3, 12, 13)
    assert outer.scaled(2).as_tuple() == (0, 0, 20, 20)
    assert outer.expanded(1).as_tuple() == (-1, -1, 11, 11)


def test_flip_y_round_trips():
    box = BBox(x0=0, y0=2, x1=4, y1=6)
    assert box.flipped_y(10).flipped_y(10) == box
    assert box.flipped_y(10).as_tuple() == (0, 4, 4, 8)


def test_from_normalised():
    box = BBox.from_normalised([0.1, 0.2, 0.5, 0.6], 200, 100)
    assert box.as_tuple() == (20.0, 20.0, 100.0, 60.0)


def test_coordinate_space_values():
    assert CoordinateSpace.Y_UP.value == "y_up"
