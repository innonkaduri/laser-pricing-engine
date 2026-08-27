"""בדיקות הקיפול — מהפריסה אל הגוף.

השאלה היחידה שנשאלת כאן: **האם הגוף שהלקוח רואה הוא באמת החלק
שתומחר.** קיפול הוא איזומטריה, ולכן כל מידה חייבת לשרוד אותו.
"""

from __future__ import annotations

import math

import pytest

from laser_pricing.api import catalog as catalog_mod
from laser_pricing.api.app import ManualPartSpec, _build_manual_geometry
from laser_pricing.domain.folding import FlatPanel, bounds, fold


def _square(x: float, y: float, w: float, h: float) -> list[tuple[float, float]]:
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def _length(points: list[tuple[float, float, float]]) -> float:
    total = 0.0
    for i in range(len(points)):
        a, b = points[i], points[(i + 1) % len(points)]
        total += math.dist(a, b)
    return total


class TestFoldingIsAnIsometry:
    """קיפול מסובב. הוא לא מותח, לא מקצר ולא מוסיף חומר."""

    def test_every_panel_keeps_its_perimeter(self):
        panels = [
            FlatPanel(_square(0, 0, 100, 60)),
            FlatPanel(_square(100, 0, 40, 60), 0, (100, 0, 100, 60), 90),
        ]
        faces = fold(panels, [])
        for panel, face in zip(panels, faces):
            flat = _length([(x, y, 0.0) for x, y in panel.outline])
            assert _length(face.outline) == pytest.approx(flat)

    def test_a_hole_keeps_its_diameter_after_the_fold(self):
        """חור שהתקצר בקיפול הוא חור שמישהו יגלה במקדחה."""
        hole = [
            (120 + 5 * math.cos(2 * math.pi * i / 32), 30 + 5 * math.sin(2 * math.pi * i / 32))
            for i in range(32)
        ]
        panels = [
            FlatPanel(_square(0, 0, 100, 60)),
            FlatPanel(_square(100, 0, 40, 60), 0, (100, 0, 100, 60), 90),
        ]
        faces = fold(panels, [hole])
        folded = next(f for f in faces if f.holes)
        assert _length(folded.holes[0]) == pytest.approx(_length([(x, y, 0.0) for x, y in hole]))

    def test_a_hole_lands_on_the_panel_it_was_drawn_in(self):
        panels = [
            FlatPanel(_square(0, 0, 100, 60), label="בסיס"),
            FlatPanel(_square(100, 0, 40, 60), 0, (100, 0, 100, 60), 90, "דופן"),
        ]
        in_base = _square(20, 20, 10, 10)
        in_wall = _square(110, 20, 10, 10)
        faces = fold(panels, [in_base, in_wall])
        assert len(faces[0].holes) == 1 and len(faces[1].holes) == 1

    def test_a_ninety_degree_fold_turns_the_normal_by_ninety_degrees(self):
        panels = [
            FlatPanel(_square(0, 0, 100, 60)),
            FlatPanel(_square(100, 0, 40, 60), 0, (100, 0, 100, 60), 90),
        ]
        faces = fold(panels, [])
        a, b = faces[0].normal, faces[1].normal
        dot = sum(x * y for x, y in zip(a, b))
        assert dot == pytest.approx(0.0, abs=1e-9)


class TestTheFoldDirectionFollowsTheParentAndNotTheWorld:
    """הבאג האמיתי שנתפס כאן ב-27.8.2026.

    מדף צף הוא גב, משטח ושפה. הכיפוף הראשון נכון בשני הכללים; השני
    הוא זה שמפריד ביניהם, כי הוא יוצא מלוח שכבר אנכי. הכלל "כלפי
    מעלה בציר z" המשיך את השפה עוד 25 מ"מ למעלה במקום להחזיר אותה
    פנימה, והעומק יצא **105 במקום 80**.
    """

    def test_the_second_fold_returns_toward_the_first(self):
        loaded = catalog_mod.load()
        shelf = loaded.get("floating-shelf")
        blank = catalog_mod.build(shelf, shelf.defaults())
        low, high = bounds(fold(blank.panels, []))
        size = sorted(round(high[i] - low[i], 1) for i in range(3))
        # אורך 600 · עומק המשטח 200 · גובה הגב 80. השפה 25 מתקפלת
        # פנימה ולכן אינה מוסיפה דבר לתיבה החוסמת.
        assert size == [80.0, 200.0, 600.0]

    @pytest.mark.parametrize(
        "product_id,expected",
        [
            ("tray-3-sides", [40.0, 200.0, 300.0]),
            ("tray-4-sides", [50.0, 200.0, 300.0]),
            ("enclosure", [80.0, 200.0, 300.0]),
            ("angle-bracket", [50.0, 60.0, 80.0]),
            ("u-channel", [40.0, 100.0, 300.0]),
        ],
    )
    def test_each_bent_product_folds_to_the_body_its_sliders_describe(self, product_id, expected):
        """המידות שהמחוונים מבטיחים הן המידות של הגוף שיוצא."""
        loaded = catalog_mod.load()
        product = loaded.get(product_id)
        blank = catalog_mod.build(product, product.defaults())
        low, high = bounds(fold(blank.panels, []))
        assert sorted(round(high[i] - low[i], 1) for i in range(3)) == expected


class TestTheSolidIsTheSamePartThatWasPriced:
    def test_the_panels_cover_the_flat_outline_without_overlap(self):
        """סכום שטחי הלוחות = שטח הפריסה. **הפרש פירושו חומר מומצא.**"""
        loaded = catalog_mod.load()
        for product in loaded.products:
            blank = catalog_mod.build(product, product.defaults())
            if not blank.panels:
                continue
            geometry = _build_manual_geometry(ManualPartSpec(name=product.name, **blank.spec))
            outer = geometry.outer_contours[0].area
            panels = sum(_area(p.outline) for p in blank.panels)
            assert panels == pytest.approx(outer, rel=1e-6), product.id

    def test_every_bent_product_declares_one_panel_more_than_its_bends(self):
        """עץ קיפול: כל כיפוף מוסיף לוח אחד לשורש."""
        loaded = catalog_mod.load()
        for product in loaded.products:
            blank = catalog_mod.build(product, product.defaults())
            if blank.bend_count:
                assert len(blank.panels) == blank.bend_count + 1, product.id

    def test_a_panel_folded_onto_a_later_parent_is_refused(self):
        """אב שמוגדר אחרי הילד היה נותן מטריצה שטרם חושבה."""
        panels = [
            FlatPanel(_square(0, 0, 10, 10), 1, (10, 0, 10, 10), 90),
            FlatPanel(_square(10, 0, 10, 10)),
        ]
        with pytest.raises(ValueError):
            fold(panels, [])


def _area(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0
