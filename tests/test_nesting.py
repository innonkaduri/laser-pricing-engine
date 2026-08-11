"""בדיקות האילוץ הפיזי והבזבוז המדורג."""

from __future__ import annotations

import pytest

from laser_pricing.cad import read_dxf
from laser_pricing.domain.geometry import BoundingBox
from laser_pricing.nesting import Plate, check_manufacturability, nest
from laser_pricing.nesting.nester import NestItem, RemnantPolicy, grade_remnant
from laser_pricing.nesting.packer import Rect
from laser_pricing.nesting.plate import NotManufacturableError

FULL_PLATE = Plate(edge_margin_mm=0.0)


class TestHardConstraint:
    """האילוץ הפיזי — הבדיקה הבינארית היחידה במערכת."""

    def test_oversized_part_is_rejected(self):
        result = check_manufacturability(BoundingBox(0, 0, 3200, 1000), FULL_PLATE)
        assert not result.ok
        assert "לא ניתן לייצור" in result.reason

    def test_part_that_only_fits_rotated_is_accepted(self):
        result = check_manufacturability(BoundingBox(0, 0, 1400, 2900), FULL_PLATE)
        assert result.ok
        assert result.rotated

    def test_exact_plate_size_fits(self):
        assert check_manufacturability(BoundingBox(0, 0, 3000, 1500), FULL_PLATE).ok

    def test_measurement_noise_does_not_reject_a_valid_part(self):
        """3000.02 מ"מ בקובץ הוא חלק של 3000 עם רעש נומרי, לא חריגה."""
        assert check_manufacturability(BoundingBox(0, 0, 3000.02, 1500), FULL_PLATE).ok

    def test_default_margin_shrinks_the_usable_area(self):
        """ברירת המחדל של ינון: 20 מ"מ מכל צד, כלומר 2960x1460."""
        plate = Plate()
        assert plate.usable_width == pytest.approx(2960)
        assert plate.usable_height == pytest.approx(1460)
        assert not check_manufacturability(BoundingBox(0, 0, 3000, 1500), plate).ok

    def test_margin_is_configurable_not_hardcoded(self):
        assert check_manufacturability(BoundingBox(0, 0, 3000, 1500), Plate(edge_margin_mm=0)).ok

    def test_oversized_dxf_is_blocked_end_to_end(self, oversized_dxf):
        geo = read_dxf(oversized_dxf).geometry
        with pytest.raises(NotManufacturableError):
            check_manufacturability(geo.bbox, FULL_PLATE).raise_if_impossible()


class TestGradedWaste:
    """מעבר לאילוץ — הכל רציף."""

    def test_utilization_is_continuous_not_binary(self):
        items = [NestItem(f"i{n}", 500, 300, 500 * 300) for n in range(40)]
        result = nest(items, plate=FULL_PLATE, part_gap_mm=5.0)
        assert result.plates_used == 2
        assert 0.0 < result.utilization < 1.0
        assert result.waste_ratio == pytest.approx(1 - result.utilization)

    def test_fewer_parts_leave_more_waste(self):
        """הבדיקה שמוכיחה שהתמחור מדורג: פחות חלקים = יותר בזבוז לחלק."""
        few = nest([NestItem("a", 500, 300, 150_000)], plate=FULL_PLATE)
        many = nest(
            [NestItem(f"i{n}", 500, 300, 150_000) for n in range(20)], plate=FULL_PLATE
        )
        assert few.waste_ratio > many.waste_ratio
        assert few.plates_used == many.plates_used == 1

    def test_part_gap_is_counted_as_waste(self):
        tight = nest([NestItem(f"i{n}", 300, 300, 90_000) for n in range(20)], plate=FULL_PLATE, part_gap_mm=0)
        spaced = nest([NestItem(f"i{n}", 300, 300, 90_000) for n in range(20)], plate=FULL_PLATE, part_gap_mm=20)
        assert spaced.plates_used >= tight.plates_used

    def test_item_larger_than_plate_is_reported_unplaced(self):
        result = nest([NestItem("huge", 4000, 100, 400_000)], plate=FULL_PLATE)
        assert result.plates_used == 0
        assert [i.item_id for i in result.unplaced] == ["huge"]


class TestRemnantGrading:
    """שארית — לא בינארית, אבל רצועה צרה היא באמת גרוטאה."""

    POLICY = RemnantPolicy(
        min_usable_short_side_mm=200.0, full_value_area_mm2=800 * 800, max_credit_ratio=0.5
    )

    def test_policy_rejects_settings_that_collapse_the_ramp(self):
        """סף שטח ששווה לריבוע הצד הקצר הופך את הדירוג חזרה לבינארי."""
        with pytest.raises(ValueError):
            RemnantPolicy(min_usable_short_side_mm=200.0, full_value_area_mm2=200 * 200)

    def test_narrow_strip_is_worthless(self):
        grade = grade_remnant(Rect(0, 0, 3000, 40), FULL_PLATE, self.POLICY)
        assert grade.quality == 0.0
        assert grade.credit_fraction_of_plate == 0.0
        assert not grade.usable

    def test_large_remnant_returns_to_stock(self):
        grade = grade_remnant(Rect(0, 0, 1000, 900), FULL_PLATE, self.POLICY)
        assert grade.quality == 1.0
        assert grade.usable
        assert grade.credit_fraction_of_plate == pytest.approx(0.5 * (1000 * 900) / (3000 * 1500))

    def test_quality_ramps_with_area_above_the_gate(self):
        """שארית 250x220 עוברת את השער אבל קטנה — ערך חלקי, לא מלא."""
        small = grade_remnant(Rect(0, 0, 250, 220), FULL_PLATE, self.POLICY)
        assert 0.0 < small.quality < 1.0
        bigger = grade_remnant(Rect(0, 0, 500, 400), FULL_PLATE, self.POLICY)
        assert small.quality < bigger.quality < 1.0

    def test_no_remnant_when_nothing_was_nested(self):
        assert nest([], plate=FULL_PLATE).largest_remnant.quality == 0.0

    def test_remnants_are_disjoint_and_summable(self):
        """שאריות חופפות אי אפשר לסכם — החילוץ חייב להחזיר קבוצה זרה."""
        result = nest([NestItem("a", 500, 300, 150_000)], plate=FULL_PLATE)
        layout = result.layouts[0]
        total_remnant_area = sum(r.area_mm2 for r in layout.remnants)
        occupied = layout.bbox_area_mm2
        assert total_remnant_area + occupied <= layout.plate.area_mm2 * 1.001

    def test_credit_ratio_actually_dampens_the_reusable_area(self):
        """max_credit_ratio הוא הדיאל של ינון וחייב להשפיע על החישוב עצמו."""
        items = [NestItem("a", 500, 300, 150_000)]
        generous = nest(items, plate=FULL_PLATE, policy=RemnantPolicy(max_credit_ratio=1.0))
        stingy = nest(items, plate=FULL_PLATE, policy=RemnantPolicy(max_credit_ratio=0.25))
        assert generous.reusable_area_mm2 > stingy.reusable_area_mm2
        assert generous.consumed_area_mm2 < stingy.consumed_area_mm2

    def test_credit_reduces_effective_plates(self):
        result = nest([NestItem("a", 500, 300, 150_000)], plate=FULL_PLATE)
        assert result.effective_plates < result.plates_used
