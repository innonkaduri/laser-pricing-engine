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


class TestClassificationIsComputedOnce:
    """המיון גוף-מול-חור הוא O(n²), ולכן מותר לו לקרות **פעם אחת**.

    עד 23.8.2026 כל תכונה חישבה אותו מחדש, ו-`geometry_to_json` ניגש
    לשש מהן. נמדד על הקופסה: DXF של 54KB עם 401 מתארים לקח **21.4
    שניות** ל-`POST /api/upload` — מתוכן 17 בסריאליזציה ו-4.7 בשתי
    גישות ל-`bbox`. אחרי התיקון: 0.09 שניות. חלק עם 2,001 מתארים לא
    סיים בעשר דקות לפני, ולוקח 1.02 שניות אחרי.

    הבדיקה סופרת קריאות ל-`contains_point` ואינה מודדת זמן: בדיקת
    זמן על קופסה משותפת בשתי ליבות מהבהבת, וספירה היא בדיוק הדבר
    שהתקלקל.
    """

    def _grid(self, holes: int):
        from laser_pricing.domain.geometry import PartGeometry, circle, rectangle

        side = int(holes**0.5) + 1
        contours = [rectangle(side * 60 + 60, side * 60 + 60)]
        for i in range(holes):
            contours.append(
                circle(12, center=(i % side * 60 + 30, i // side * 60 + 30), segments=32)
            )
        return PartGeometry(contours=contours)

    def test_touching_every_property_classifies_once(self, monkeypatch):
        from laser_pricing.domain.geometry import Contour

        geometry = self._grid(40)
        calls = []
        original = Contour.contains_point
        monkeypatch.setattr(
            Contour,
            "contains_point",
            lambda self, point: (calls.append(1), original(self, point))[1],
        )

        geometry.hole_contours
        first = len(calls)
        assert first > 0, "המיון לא רץ בכלל — הבדיקה אינה בודקת כלום"

        # בדיוק מה ש-geometry_to_json נוגע בו, בזה אחר זה.
        geometry.outer_contours
        geometry.net_area
        geometry.gross_area
        geometry.bbox
        geometry.hole_count
        geometry.body_count
        geometry.pierce_count

        assert len(calls) == first, (
            f"המיון חושב מחדש: {len(calls)} קריאות במקום {first}. "
            "זה מה שהפך העלאה של 54KB ל-21 שניות."
        )

    def test_the_bounding_box_prefilter_skips_far_contours(self):
        """סינון התיבה החוסמת חוסך את ה-ray casting על מה שרחוק.

        בלעדיו כל חור נבדק מול כל חור גדול ממנו — וזה החלק הריבועי.
        """
        from laser_pricing.domain.geometry import Contour

        geometry = self._grid(60)
        calls = []
        original = Contour.contains_point
        Contour.contains_point = lambda self, point: (calls.append(1), original(self, point))[1]
        try:
            geometry.hole_contours
        finally:
            Contour.contains_point = original

        # 61 מתארים. בלי סינון זה עשרות אלפי בדיקות; עם סינון, כל חור
        # מוצא רק את המלבן החיצוני שמכיל אותו.
        assert len(calls) <= 61 * 2, f"{len(calls)} בדיקות הכלה — הסינון אינו עובד"

    def test_classification_is_still_correct(self):
        geometry = self._grid(25)
        assert geometry.hole_count == 25
        assert geometry.body_count == 1
        assert geometry.pierce_count == 26
        assert geometry.net_area < geometry.gross_area
