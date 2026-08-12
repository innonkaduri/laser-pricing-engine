"""בדיקות מנוע התמחור.

המספרים בטבלה כאן נבחרו כדי שהחישוב יהיה נוח לאימות ידני:
פלטה 3000x1500 = 4.5 מ"ר במחיר 450 => 100 ש"ח למ"ר בדיוק.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from laser_pricing.domain.geometry import PartGeometry, circle, rectangle
from laser_pricing.domain.part import Part
from laser_pricing.pricing import (
    InvalidTariffError,
    MissingTariffError,
    PricingError,
    load_tariff,
    price_order,
    tariff_from_dict,
)

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"

BASE_TARIFF = {
    "currency": "ILS",
    "vat_pct": 0.0,
    "margin_pct": 0.0,
    "plate": {"width_mm": 3000.0, "height_mm": 1500.0, "edge_margin_mm": 0.0},
    "part_gap_mm": 5.0,
    "waste_tiers": [
        {"max_waste_pct": 20.0, "multiplier": 1.0, "label": "מצוינת"},
        {"max_waste_pct": 60.0, "multiplier": 1.5, "label": "בינונית"},
        {"max_waste_pct": 100.0, "multiplier": 2.0, "label": "מקסימלי"},
    ],
    "rates": [
        {
            "material_key": "st37",
            "material_name": "פלדה שחורה St37",
            "thickness_mm": 3.0,
            "plate_price": 450.0,  # 4.5 מ"ר => 100 ש"ח למ"ר
            "cut_rate_per_m": 10.0,
            "pierce_price": 2.0,
        }
    ],
}


def make_tariff(**overrides):
    raw = json.loads(json.dumps(BASE_TARIFF))
    # שדות ברמת שורת החומר מנותבים לשורה, לא לשורש הטבלה.
    for rate_field in ("weld_rate_per_m", "min_charge_per_part", "pierce_price"):
        if rate_field in overrides:
            raw["rates"][0][rate_field] = overrides.pop(rate_field)
    raw.update(overrides)
    return tariff_from_dict(raw, source="test")


def square_part(side: float = 500.0, qty: int = 1, name: str = "לוח") -> Part:
    return Part(
        name=name,
        geometry=PartGeometry([rectangle(side, side)]),
        material_key="st37",
        thickness_mm=3.0,
        quantity=qty,
    )


class TestTableIsTheSourceOfTruth:
    """הדרישה המרכזית: המנוע לא ממציא מחיר."""

    def test_unknown_material_fails_loudly(self):
        part = square_part()
        part.material_key = "טיטניום"
        with pytest.raises(MissingTariffError, match="אינו קיים בטבלת התמחור"):
            price_order([part], make_tariff())

    def test_known_material_unknown_thickness_lists_what_exists(self):
        part = square_part()
        part.thickness_mm = 7.0
        with pytest.raises(MissingTariffError) as exc:
            price_order([part], make_tariff())
        assert "3.0" in str(exc.value)
        assert "לא ננחש מחיר" in str(exc.value)

    def test_no_interpolation_between_thicknesses(self):
        """עובי 4 בין 3 ל-5 בטבלה — אסור לחשב ממוצע."""
        tariff = make_tariff(
            rates=BASE_TARIFF["rates"]
            + [
                {
                    "material_key": "st37",
                    "material_name": "פלדה שחורה St37",
                    "thickness_mm": 5.0,
                    "plate_price": 750.0,
                    "cut_rate_per_m": 16.0,
                    "pierce_price": 3.0,
                }
            ]
        )
        part = square_part()
        part.thickness_mm = 4.0
        with pytest.raises(MissingTariffError):
            price_order([part], tariff)


class TestTariffValidation:
    def test_tiers_must_reach_100_pct(self):
        with pytest.raises(InvalidTariffError, match="עד 100%"):
            make_tariff(waste_tiers=[{"max_waste_pct": 50.0, "multiplier": 1.2}])

    def test_multiplier_below_one_is_rejected(self):
        with pytest.raises(InvalidTariffError, match="קטן מ-1"):
            make_tariff(waste_tiers=[{"max_waste_pct": 100.0, "multiplier": 0.8}])

    def test_duplicate_rows_are_rejected(self):
        with pytest.raises(InvalidTariffError, match="כפולה"):
            make_tariff(rates=BASE_TARIFF["rates"] * 2)

    def test_shipped_example_tariff_is_structurally_valid(self):
        """קובץ הדוגמה חייב להיטען — הוא התבנית שינון עורך."""
        tariff = load_tariff(CONFIG_DIR / "tariff.example.json")
        assert tariff.rates
        assert tariff.waste_tiers[-1].max_waste_pct == 100.0

    def test_example_tariff_carries_no_real_prices(self):
        """הגנה מפני שליחת מחירי דוגמה ללקוח אמיתי."""
        tariff = load_tariff(CONFIG_DIR / "tariff.example.json")
        assert all(r.plate_price == 0.0 for r in tariff.rates.values())


class TestArithmetic:
    def test_line_breakdown_adds_up(self):
        """500x500, פלטה אחת, בזבוז גבוה => מדרגה עליונה."""
        quote = price_order([square_part()], make_tariff())
        line = quote.lines[0]
        assert line.material_cost + line.cutting_cost + line.piercing_cost + line.setup_cost == pytest.approx(
            line.line_total, abs=0.02
        )

    def test_cutting_cost_follows_cut_length(self):
        """היקף 2 מטר בתעריף 10 ש"ח/מטר = 20 ש"ח."""
        quote = price_order([square_part()], make_tariff())
        assert quote.lines[0].cut_length_mm == pytest.approx(2000)
        assert quote.lines[0].cutting_cost == pytest.approx(20.0)

    def test_pierce_cost_counts_every_closed_contour(self):
        part = Part(
            name="עם 4 חורים",
            geometry=PartGeometry(
                [rectangle(500, 500)] + [circle(10, center=(100 + 100 * i, 250)) for i in range(4)]
            ),
            material_key="st37",
            thickness_mm=3.0,
        )
        quote = price_order([part], make_tariff())
        assert quote.lines[0].pierces == 5
        assert quote.lines[0].piercing_cost == pytest.approx(10.0)

    def test_quantity_multiplies_the_line(self):
        one = price_order([square_part(qty=1)], make_tariff()).lines[0]
        five = price_order([square_part(qty=5)], make_tariff()).lines[0]
        assert five.line_total == pytest.approx(five.unit_price * 5)
        assert five.quantity == 5
        # מחיר היחידה יורד כי הבזבוז לחלק קטן יותר
        assert five.unit_price < one.unit_price

    def test_vat_and_margin_are_applied_in_order(self):
        tariff = make_tariff(margin_pct=10.0, vat_pct=18.0)
        quote = price_order([square_part()], tariff)
        assert quote.margin_amount == pytest.approx(quote.subtotal * 0.10, abs=0.02)
        assert quote.total_before_vat == pytest.approx(quote.subtotal + quote.margin_amount, abs=0.02)
        assert quote.vat_amount == pytest.approx(quote.total_before_vat * 0.18, abs=0.02)
        assert quote.total == pytest.approx(quote.total_before_vat + quote.vat_amount, abs=0.02)

    def test_weight_uses_density_from_the_table(self):
        """500x500x3 פלדה: 0.25 מ"ר * 0.003 מ' * 7850 = 5.8875 ק"ג."""
        quote = price_order([square_part()], make_tariff())
        assert quote.lines[0].weight_kg == pytest.approx(5.8875, rel=1e-3)


class TestGradedWasteAffectsPrice:
    """הדרישה: מדורג, לא בינארי."""

    def test_more_parts_land_in_a_cheaper_tier(self):
        few = price_order([square_part(qty=1)], make_tariff())
        many = price_order([square_part(qty=15)], make_tariff())
        assert many.lines[0].waste_pct < few.lines[0].waste_pct
        assert many.lines[0].unit_price <= few.lines[0].unit_price

    def test_waste_falls_monotonically_while_filling_one_plate(self):
        """בתוך פלטה אחת הבזבוז חייב לרדת ברציפות.

        הבדיקה הזאת נולדה מבאג אמיתי: זיכוי לפי המלבן הפנוי הגדול ביותר
        בלבד גרם לבזבוז לקפוץ הלוך ושוב, ואז 3 חלקים יצאו יקרים מ-2.
        """
        seen = [price_order([square_part(qty=q)], make_tariff()).lines[0].waste_pct for q in (1, 2, 3, 4, 6, 8, 10)]
        assert seen == sorted(seen, reverse=True)
        assert len(set(seen)) == len(seen)

    def test_unit_price_falls_with_quantity_within_one_plate(self):
        """בתוך פלטה אחת ההבטחה נשמרת: יותר יחידות = יחידה זולה יותר."""
        prices = [
            price_order([square_part(qty=q)], make_tariff()).lines[0].unit_price
            for q in (1, 2, 5, 10)
        ]
        assert prices == sorted(prices, reverse=True)

    def test_plate_floor_can_raise_the_unit_price_at_a_plate_boundary(self):
        """התנגשות מתועדת בין שני כללים של ינון, ולא באג.

        רצפת הפלטה (2026-08-12) אומרת: לעולם לא לחייב על פחות חומר ממה
        שנצרך. פלטה שנייה שנפתחת כמעט ריקה נצרכת כמעט במלואה, ולכן
        מחיר היחידה *עולה* בדיוק בגבול הפלטה, ויורד שוב כשהיא מתמלאת.

        זה סותר את ההבטחה "להזמין יותר לעולם לא מייקר את היחידה" —
        הבטחה שהמנוע הסיק ולא כלל שינון קבע. ההוראה המפורשת גוברת,
        אבל הבדיקה הזאת קיימת כדי שההתנהגות תישאר גלויה: אם ינון יחליט
        להעדיף מונוטוניות, כאן מתחיל התיקון.
        """
        at_10 = price_order([square_part(qty=10)], make_tariff())
        at_15 = price_order([square_part(qty=15)], make_tariff())
        at_20 = price_order([square_part(qty=20)], make_tariff())

        assert at_10.groups[0].plates_used == 1
        assert at_15.groups[0].plates_used == 2

        # הקפיצה מגיעה מהרצפה, ולא ממקום אחר
        assert at_15.groups[0].plate_floor_applied
        assert not at_10.groups[0].plate_floor_applied
        assert at_15.lines[0].unit_price > at_10.lines[0].unit_price

        # וכשהפלטה השנייה מתמלאת, המחיר חוזר לרדת
        assert at_20.lines[0].unit_price < at_15.lines[0].unit_price

    def test_opening_a_second_plate_is_an_honest_step_up(self):
        """מעבר לפלטה שנייה מעלה את הבזבוז — זו פיזיקה, לא באג."""
        full = price_order([square_part(qty=10)], make_tariff())
        spill = price_order([square_part(qty=12)], make_tariff())
        assert full.groups[0].plates_used == 1
        assert spill.groups[0].plates_used == 2
        assert spill.lines[0].waste_pct > full.lines[0].waste_pct

    def test_usable_remnant_lowers_the_charged_waste(self):
        """שארית גדולה חוזרת למלאי ולכן אינה בזבוז שהלקוח משלם עליו."""
        line = price_order([square_part()], make_tariff()).lines[0]
        # בזבוז גולמי הוא כמעט 100%, אבל שארית ענקית מקזזת אותו
        assert line.waste_pct < 99.0

    def test_tier_label_is_reported_to_the_customer(self):
        quote = price_order([square_part(qty=20)], make_tariff())
        assert quote.lines[0].waste_tier_label


def giant_part(name: str = "ענק", w: float = 3200.0, h: float = 1000.0) -> Part:
    return Part(
        name=name,
        geometry=PartGeometry([rectangle(w, h)]),
        material_key="st37",
        thickness_mm=3.0,
    )


class TestOversizedPartsAreSplitNotRejected:
    """החלטת ינון 2026-08-12 — חלק גדול מפלטה נחתך ומולחם."""

    def test_oversized_part_gets_a_price(self):
        quote = price_order([giant_part()], make_tariff())
        assert not quote.rejected
        assert quote.is_quotable
        assert quote.lines[0].line_total > 0

    def test_oversized_part_is_reported_as_split(self):
        quote = price_order([giant_part()], make_tariff())
        line = quote.lines[0]
        assert line.pieces == 2
        assert line.is_split
        assert quote.has_split_parts
        assert line.weld_length_mm == pytest.approx(1000)

    def test_split_adds_cut_length_and_pierces(self):
        whole = price_order([square_part(name="רגיל")], make_tariff()).lines[0]
        split = price_order([giant_part()], make_tariff()).lines[0]
        # מתאר אחד הופך לשניים, ולכן ניקוב נוסף
        assert split.pieces == 2
        assert split.pierces == 2
        assert whole.pierces == 1

    def test_split_consumes_more_than_one_plate(self):
        quote = price_order([giant_part(w=4000, h=1400)], make_tariff())
        assert quote.groups[0].plates_used == 2

    def test_user_is_warned_that_the_part_was_split(self):
        quote = price_order([giant_part()], make_tariff())
        assert any("ריתוך" in w for w in quote.warnings)

    def test_zero_weld_rate_is_announced_not_silently_free(self):
        """ריתוך בחינם בשקט הוא בדיוק סוג הטעות שהמנוע אמור לא לעשות."""
        quote = price_order([giant_part()], make_tariff())
        assert quote.lines[0].welding_cost == 0.0
        assert any("weld_rate_per_m" in w for w in quote.warnings)

    def test_weld_rate_from_the_table_is_charged(self):
        tariff = make_tariff(weld_rate_per_m=50.0)
        quote = price_order([giant_part()], tariff)
        # תפר אחד באורך מטר, 50 ש"ח למטר
        assert quote.lines[0].welding_cost == pytest.approx(50.0, abs=0.01)

    def test_more_than_two_pieces_needs_explicit_approval(self):
        quote = price_order([giant_part(w=9000, h=1400)], make_tariff())
        assert quote.lines[0].pieces > 2
        assert any("אישור מפורש" in w for w in quote.warnings)

    def test_explicit_cap_still_rejects(self):
        """מי שרוצה לחסום פיצול מעבר ל-N מציב max_pieces_per_part."""
        tariff = make_tariff(max_pieces_per_part=1)
        quote = price_order([square_part(name="תקין"), giant_part()], tariff)
        assert [line.part_name for line in quote.lines] == ["תקין"]
        assert [r.part_name for r in quote.rejected] == ["ענק"]
        assert not quote.is_quotable

    def test_strict_mode_fails_the_whole_order_when_capped(self):
        tariff = make_tariff(max_pieces_per_part=1)
        with pytest.raises(PricingError, match="חתיכות"):
            price_order([square_part(name="תקין"), giant_part()], tariff, strict=True)


class TestFullPlateFloor:
    """חלק שהורס את שארית הפלטה מחויב כפלטה שלמה — ינון, 2026-08-12."""

    def _frame(self, name: str = "מסגרת") -> Part:
        """מסגרת ענקית: תופסת פלטה שלמה, אבל שטח המתכת שלה קטן."""
        outer = rectangle(2900, 1400)
        hole = rectangle(2700, 1200, origin=(100, 100))
        return Part(
            name=name,
            geometry=PartGeometry([outer, hole]),
            material_key="st37",
            thickness_mm=3.0,
        )

    def test_billing_never_falls_below_the_material_consumed(self):
        tariff = make_tariff()
        quote = price_order([self._frame()], tariff)
        group = quote.groups[0]
        assert group.plate_floor_applied
        assert group.billed_area_mm2 == pytest.approx(group.consumed_area_mm2, rel=1e-6)

    def test_a_plate_destroying_part_is_charged_a_whole_plate(self):
        """אין שארית שמישה — ולכן החיוב הוא מחיר הפלטה המלא."""
        tariff = make_tariff()
        quote = price_order([self._frame()], tariff)
        group = quote.groups[0]
        assert group.nesting.reusable_area_mm2 == 0.0
        plate_price = tariff.rate_for("st37", 3.0).plate_price
        assert quote.lines[0].material_cost == pytest.approx(plate_price, rel=1e-3)

    def test_floor_is_announced(self):
        quote = price_order([self._frame()], make_tariff())
        assert any("רצפת פלטה" in w for w in quote.warnings)
        assert quote.lines[0].plate_floor_applied

    def test_efficient_order_is_untouched_by_the_floor(self):
        """הרצפה היא רשת ביטחון, לא תוספת מחיר לכולם."""
        parts = [square_part(name="רגיל", qty=40)]
        quote = price_order(parts, make_tariff())
        assert not quote.groups[0].plate_floor_applied
        assert not quote.lines[0].plate_floor_applied

    def test_floor_is_shared_across_parts_in_the_group(self):
        """הפלטה משותפת, ולכן גם הרצפה — היא לא נגבית פעמיים."""
        quote = price_order([self._frame("א"), self._frame("ב")], make_tariff())
        group = quote.groups[0]
        billed = sum(line.billed_area_mm2 * line.quantity for line in quote.lines)
        assert billed == pytest.approx(group.consumed_area_mm2, rel=1e-6)


class TestGrouping:
    def test_different_materials_do_not_share_a_plate(self):
        tariff = make_tariff(
            rates=BASE_TARIFF["rates"]
            + [
                {
                    "material_key": "ss304",
                    "material_name": "נירוסטה 304",
                    "thickness_mm": 3.0,
                    "plate_price": 1200.0,
                    "cut_rate_per_m": 25.0,
                    "pierce_price": 5.0,
                }
            ]
        )
        steel = square_part(name="פלדה")
        stainless = square_part(name="נירוסטה")
        stainless.material_key = "ss304"
        quote = price_order([steel, stainless], tariff)
        assert len(quote.groups) == 2
        assert all(g.plates_used == 1 for g in quote.groups)

    def test_same_material_different_thickness_are_separate_groups(self):
        tariff = make_tariff(
            rates=BASE_TARIFF["rates"]
            + [
                {
                    "material_key": "st37",
                    "material_name": "פלדה שחורה St37",
                    "thickness_mm": 5.0,
                    "plate_price": 750.0,
                    "cut_rate_per_m": 16.0,
                    "pierce_price": 3.0,
                }
            ]
        )
        thin = square_part(name="דק")
        thick = square_part(name="עבה")
        thick.thickness_mm = 5.0
        quote = price_order([thin, thick], tariff)
        assert len(quote.groups) == 2


class TestOrderLevelRules:
    def test_min_order_total_is_applied(self):
        quote = price_order([square_part()], make_tariff(min_order_total=5000.0))
        assert quote.min_order_applied
        assert quote.subtotal == pytest.approx(5000.0)
        assert any("מינימום הזמנה" in w for w in quote.warnings)

    def test_min_charge_per_part_is_applied(self):
        tariff = make_tariff(
            rates=[dict(BASE_TARIFF["rates"][0], min_charge_per_part=9999.0)]
        )
        line = price_order([square_part()], tariff).lines[0]
        assert line.min_charge_applied
        assert line.unit_price == pytest.approx(9999.0)

    def test_setup_fees_reach_the_total(self):
        plain = price_order([square_part()], make_tariff())
        with_fees = price_order(
            [square_part()], make_tariff(setup_fee_per_order=100.0, setup_fee_per_part=50.0)
        )
        assert with_fees.subtotal == pytest.approx(plain.subtotal + 150.0, abs=0.02)

    def test_empty_order_is_rejected(self):
        with pytest.raises(PricingError):
            price_order([], make_tariff())

    def test_absurd_multiplier_warns_instead_of_silently_overcharging(self):
        """מכפיל שגורם לחיוב על יותר חומר ממה שנרכש = כנראה טעות הקלדה."""
        tariff = make_tariff(
            waste_tiers=[{"max_waste_pct": 100.0, "multiplier": 50.0, "label": "טעות"}]
        )
        quote = price_order([square_part(qty=10)], tariff)
        assert any("מדרגות הבזבוז" in w for w in quote.warnings)
