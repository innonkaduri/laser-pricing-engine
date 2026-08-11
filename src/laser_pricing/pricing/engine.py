"""מנוע התמחור.

זרימת החישוב:
  1. אילוץ פיזי — חלק שחורג מהפלטה נפסל. אין לו מחיר.
  2. קיבוץ לפי חומר+עובי — חלקים מחומרים שונים לא חולקים פלטה.
  3. נסטינג לכל קבוצה → אחוז בזבוז רציף.
  4. הפחתת שארית שמישה מהבזבוז — מה שחוזר למלאי אינו בזבוז.
  5. מדרגת הבזבוז של ינון → מכפיל שטח מחויב.
  6. חומר + חיתוך + ניקוב + הקמה → מרווח → מע"מ.

כל מספר כספי בשרשרת הזאת מגיע מהטבלה. המנוע קובע רק *כמה* יחידות
של כל דבר צריך, לא *מה מחירן*.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..domain.part import Part
from ..nesting.nester import NestingResult, NestItem, nest
from ..nesting.plate import check_manufacturability
from .tariff import MaterialRate, Tariff, WasteTier

MM2_PER_M2 = 1_000_000.0
MM_PER_M = 1000.0


@dataclass
class RejectedPart:
    """חלק שאי אפשר לייצר. אין לו מחיר, ואי אפשר להזמין אותו."""

    part_name: str
    reason: str
    width_mm: float
    height_mm: float


@dataclass
class MaterialGroup:
    """כל החלקים מאותו חומר ועובי — הם שחולקים פלטות."""

    material_key: str
    material_name: str
    thickness_mm: float
    rate: MaterialRate
    nesting: NestingResult
    raw_waste_pct: float
    effective_waste_pct: float
    tier: WasteTier
    part_names: list[str] = field(default_factory=list)

    @property
    def plates_used(self) -> int:
        return self.nesting.plates_used

    @property
    def utilization_pct(self) -> float:
        return self.nesting.utilization * 100.0


@dataclass
class QuoteLine:
    """שורה בטבלה שהלקוח רואה."""

    part_name: str
    material_name: str
    thickness_mm: float
    quantity: int
    unit_price: float
    line_total: float
    material_cost: float
    cutting_cost: float
    piercing_cost: float
    setup_cost: float
    min_charge_applied: bool
    net_area_mm2: float
    billed_area_mm2: float
    cut_length_mm: float
    pierces: int
    weight_kg: float
    width_mm: float
    height_mm: float
    waste_pct: float
    waste_tier_label: str


@dataclass
class Quote:
    """הצעת המחיר המלאה."""

    lines: list[QuoteLine]
    groups: list[MaterialGroup]
    rejected: list[RejectedPart]
    parts_subtotal: float
    order_setup_fee: float
    subtotal: float
    margin_amount: float
    total_before_vat: float
    vat_amount: float
    total: float
    currency: str
    min_order_applied: bool
    warnings: list[str] = field(default_factory=list)
    tariff_source: str = ""

    @property
    def has_rejections(self) -> bool:
        """אם True — אסור לאשר את ההזמנה כמו שהיא."""
        return bool(self.rejected)

    @property
    def is_quotable(self) -> bool:
        return bool(self.lines) and not self.has_rejections


class PricingError(Exception):
    """התמחור לא יכול להתבצע."""


def price_order(
    parts: list[Part],
    tariff: Tariff,
    *,
    strict: bool = False,
) -> Quote:
    """מתמחר הזמנה שלמה.

    strict=True — חלק שאינו ניתן לייצור מפיל את כל התמחור. ברירת המחדל
    False כדי שהממשק יוכל להציג ללקוח *איזה* חלק בעייתי ולמה.
    """
    if not parts:
        raise PricingError("אין חלקים לתמחור.")

    warnings: list[str] = []
    rejected: list[RejectedPart] = []
    accepted: list[Part] = []

    # שלב 1 — האילוץ הפיזי, לפני כל חישוב כספי.
    for part in parts:
        verdict = check_manufacturability(part.bbox, tariff.plate)
        if verdict.ok:
            accepted.append(part)
            continue
        rejected.append(
            RejectedPart(
                part_name=part.name,
                reason=verdict.reason,
                width_mm=part.bbox.width,
                height_mm=part.bbox.height,
            )
        )

    if rejected and strict:
        raise PricingError(rejected[0].reason)
    if not accepted:
        raise PricingError("אף חלק בהזמנה אינו ניתן לייצור על הפלטה.")

    # שלב 2 — קיבוץ. חומרים שונים לא חולקים פלטה, ולכן גם לא בזבוז.
    grouped: dict[tuple[str, float], list[Part]] = defaultdict(list)
    for part in accepted:
        grouped[(part.material_key, round(part.thickness_mm, 3))].append(part)

    groups: list[MaterialGroup] = []
    lines: list[QuoteLine] = []
    parts_subtotal = 0.0

    for (material_key, thickness), group_parts in grouped.items():
        rate = tariff.rate_for(material_key, thickness)
        group = _nest_group(group_parts, rate, tariff)
        groups.append(group)

        group_billed_area = 0.0
        for part in group_parts:
            line = _price_part(part, rate, group, tariff)
            lines.append(line)
            parts_subtotal += line.line_total
            group_billed_area += line.billed_area_mm2 * part.quantity

        # שפיות: חיוב על יותר חומר ממה שנרכש בפועל הוא כמעט תמיד טעות
        # הקלדה במדרגות. לא חוסמים ולא מתקנים — הטבלה היא מקור האמת —
        # אבל ינון חייב לראות את זה.
        purchased_area = group.nesting.total_plate_area_mm2
        if purchased_area > 0 and group_billed_area > purchased_area * 1.001:
            warnings.append(
                f"{rate.material_name} {thickness} מ\"מ: החיוב הוא על "
                f"{group_billed_area / MM2_PER_M2:.2f} מ\"ר בעוד שנרכשו "
                f"{purchased_area / MM2_PER_M2:.2f} מ\"ר. בדוק את מכפילי מדרגות הבזבוז."
            )

        if group.nesting.unplaced:
            names = ", ".join(sorted({i.part_name for i in group.nesting.unplaced}))
            warnings.append(f"מופעים שלא נכנסו לפריסה ({names}) — בדוק את המידות.")

    # שלב 6 — עלויות ברמת ההזמנה.
    order_setup = tariff.setup_fee_per_order
    subtotal = parts_subtotal + order_setup

    min_order_applied = False
    if tariff.min_order_total > 0 and subtotal < tariff.min_order_total:
        subtotal = tariff.min_order_total
        min_order_applied = True
        warnings.append(
            f"הופעל מינימום הזמנה של {tariff.min_order_total:.2f} {tariff.currency}."
        )

    margin_amount = subtotal * (tariff.margin_pct / 100.0)
    total_before_vat = subtotal + margin_amount
    vat_amount = total_before_vat * (tariff.vat_pct / 100.0)
    total = total_before_vat + vat_amount

    if rejected:
        warnings.append(
            f"{len(rejected)} חלקים אינם ניתנים לייצור וההצעה אינה כוללת אותם — "
            f"אי אפשר לאשר את ההזמנה לפני שהם מתוקנים."
        )

    return Quote(
        lines=lines,
        groups=groups,
        rejected=rejected,
        parts_subtotal=_money(parts_subtotal),
        order_setup_fee=_money(order_setup),
        subtotal=_money(subtotal),
        margin_amount=_money(margin_amount),
        total_before_vat=_money(total_before_vat),
        vat_amount=_money(vat_amount),
        total=_money(total),
        currency=tariff.currency,
        min_order_applied=min_order_applied,
        warnings=warnings,
        tariff_source=tariff.source,
    )


# ---- שלבים פנימיים ----


def _nest_group(parts: list[Part], rate: MaterialRate, tariff: Tariff) -> MaterialGroup:
    """פורס קבוצה אחת ומחשב את הבזבוז שממנו נגזרת המדרגה."""
    items: list[NestItem] = []
    for part in parts:
        box = part.bbox
        for copy_index in range(part.quantity):
            items.append(
                NestItem(
                    item_id=f"{part.name}#{copy_index + 1}",
                    width_mm=box.width,
                    height_mm=box.height,
                    net_area_mm2=part.net_area_mm2,
                    part_name=part.name,
                )
            )

    result = nest(
        items,
        plate=tariff.plate,
        part_gap_mm=tariff.part_gap_mm,
        policy=tariff.remnant_policy,
    )

    raw_waste_pct = result.waste_ratio * 100.0
    effective_waste_pct = _effective_waste_pct(result)
    tier = tariff.waste_tier_for(effective_waste_pct)

    return MaterialGroup(
        material_key=rate.material_key,
        material_name=rate.material_name,
        thickness_mm=rate.thickness_mm,
        rate=rate,
        nesting=result,
        raw_waste_pct=raw_waste_pct,
        effective_waste_pct=effective_waste_pct,
        tier=tier,
        part_names=[p.name for p in parts],
    )


def _effective_waste_pct(result: NestingResult) -> float:
    """בזבוז ביחס לחומר שנצרך בפועל.

    שני צעדים, ושניהם הכרחיים:

    1. שארית גדולה ונקייה חוזרת למלאי ולכן אינה נצרכת בכלל — מפחיתים
       אותה משטח הפלטות.
    2. המכנה הוא מה שנצרך, **לא** שטח הפלטה המלא. זו הנקודה העדינה:
       חלוקה בפלטה המלאה הופכת חלק בודד לנראה יעיל להפליא, כי כמעט כל
       הפלטה מזוכה כשארית — והדירוג מתהפך. חלק בודד צורך את הרצועה
       שסביבו ומנצל ממנה מעט, ולכן הבזבוז שלו גבוה. וזה גם הנכון עסקית.
    """
    consumed = result.consumed_area_mm2
    if consumed <= 0:
        return 0.0
    chargeable_waste = max(0.0, consumed - result.total_net_area_mm2)
    return min(100.0, chargeable_waste / consumed * 100.0)


def _price_part(part: Part, rate: MaterialRate, group: MaterialGroup, tariff: Tariff) -> QuoteLine:
    """מתמחר חלק בודד לפי המדרגה של הקבוצה שלו."""
    qty = part.quantity
    multiplier = group.tier.multiplier

    net_area_mm2 = part.net_area_mm2
    billed_area_mm2 = net_area_mm2 * multiplier

    price_per_m2 = tariff.price_per_m2(rate)
    material_unit = (billed_area_mm2 / MM2_PER_M2) * price_per_m2
    cutting_unit = (part.cut_length_mm / MM_PER_M) * rate.cut_rate_per_m
    piercing_unit = part.pierce_count * rate.pierce_price
    setup_unit = tariff.setup_fee_per_part

    unit_price = material_unit + cutting_unit + piercing_unit + setup_unit

    min_charge_applied = False
    if rate.min_charge_per_part > 0 and unit_price < rate.min_charge_per_part:
        unit_price = rate.min_charge_per_part
        min_charge_applied = True

    weight_kg = (net_area_mm2 * part.thickness_mm / 1e9) * rate.density_kg_m3

    box = part.bbox
    return QuoteLine(
        part_name=part.name,
        material_name=rate.material_name,
        thickness_mm=part.thickness_mm,
        quantity=qty,
        unit_price=_money(unit_price),
        line_total=_money(unit_price * qty),
        material_cost=_money(material_unit * qty),
        cutting_cost=_money(cutting_unit * qty),
        piercing_cost=_money(piercing_unit * qty),
        setup_cost=_money(setup_unit * qty),
        min_charge_applied=min_charge_applied,
        net_area_mm2=round(net_area_mm2, 2),
        billed_area_mm2=round(billed_area_mm2, 2),
        cut_length_mm=round(part.cut_length_mm, 2),
        pierces=part.pierce_count,
        weight_kg=round(weight_kg * qty, 3),
        width_mm=round(box.width, 2),
        height_mm=round(box.height, 2),
        waste_pct=round(group.effective_waste_pct, 2),
        waste_tier_label=group.tier.label or f"עד {group.tier.max_waste_pct:g}% בזבוז",
    )


def _money(value: float) -> float:
    return round(value + 1e-9, 2)
