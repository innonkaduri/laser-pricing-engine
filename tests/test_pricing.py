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

    def test_unit_price_never_rises_with_quantity(self):
        """ההבטחה ללקוח: להזמין יותר לעולם לא מייקר את היחידה."""
        prices = [
            price_order([square_part(qty=q)], make_tariff()).lines[0].unit_price
            for q in (1, 2, 5, 10, 15, 20, 30, 40)
        ]
        assert prices == sorted(prices, reverse=True)

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


class TestPhysicalConstraintBlocksPricing:
    def test_oversized_part_gets_no_price(self):
        part = Part(
            name="ענק",
            geometry=PartGeometry([rectangle(3200, 1000)]),
            material_key="st37",
            thickness_mm=3.0,
        )
        with pytest.raises(PricingError):
            price_order([part], make_tariff())

    def test_rejected_part_is_reported_next_to_valid_ones(self):
        parts = [square_part(name="תקין"), Part(
            name="ענק",
            geometry=PartGeometry([rectangle(3200, 1000)]),
            material_key="st37",
            thickness_mm=3.0,
        )]
        quote = price_order(parts, make_tariff())
        assert [line.part_name for line in quote.lines] == ["תקין"]
        assert [r.part_name for r in quote.rejected] == ["ענק"]
        assert quote.has_rejections
        assert not quote.is_quotable

    def test_strict_mode_fails_the_whole_order(self):
        parts = [square_part(name="תקין"), Part(
            name="ענק",
            geometry=PartGeometry([rectangle(3200, 1000)]),
            material_key="st37",
            thickness_mm=3.0,
        )]
        with pytest.raises(PricingError, match="לא ניתן לייצור"):
            price_order(parts, make_tariff(), strict=True)


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
