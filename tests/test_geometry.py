"""בדיקות גיאומטריה טהורה."""

from __future__ import annotations

import math

import pytest

from laser_pricing.domain.geometry import BoundingBox, PartGeometry, circle, rectangle


def test_rectangle_area_and_perimeter():
    r = rectangle(400, 250)
    assert r.area == pytest.approx(100_000)
    assert r.length == pytest.approx(1300)


def test_circle_approximation_is_accurate_enough():
    c = circle(25, segments=512)
    assert c.area == pytest.approx(math.pi * 25**2, rel=1e-4)
    assert c.length == pytest.approx(2 * math.pi * 25, rel=1e-4)


def test_hole_is_subtracted_from_net_area():
    geo = PartGeometry([rectangle(400, 250), circle(25, center=(200, 125), segments=256)])
    assert geo.body_count == 1
    assert geo.hole_count == 1
    assert geo.net_area == pytest.approx(100_000 - math.pi * 25**2, rel=1e-4)
    # אורך החיתוך כולל את החור — הלייזר חותך גם אותו
    assert geo.cut_length == pytest.approx(1300 + 2 * math.pi * 25, rel=1e-4)


def test_each_closed_contour_costs_one_pierce():
    geo = PartGeometry(
        [rectangle(400, 250)] + [circle(10, center=(50 + 60 * i, 125), segments=64) for i in range(4)]
    )
    assert geo.pierce_count == 5
    assert geo.hole_count == 4


def test_two_separate_bodies_are_not_holes():
    """שני מלבנים זה לצד זה הם שני גופים, לא גוף עם חור."""
    geo = PartGeometry([rectangle(100, 100), rectangle(100, 100, origin=(200, 0))])
    assert geo.body_count == 2
    assert geo.hole_count == 0
    assert geo.net_area == pytest.approx(20_000)
    assert geo.bbox.width == pytest.approx(300)


def test_geometry_without_closed_contour_is_rejected():
    from laser_pricing.domain.geometry import Contour

    with pytest.raises(ValueError):
        PartGeometry([Contour([(0, 0), (10, 0)], closed=False)])


def test_bounding_box_merge():
    a = BoundingBox(0, 0, 10, 5)
    b = BoundingBox(20, -5, 30, 0)
    merged = a.merge(b)
    assert (merged.min_x, merged.min_y, merged.max_x, merged.max_y) == (0, -5, 30, 5)
