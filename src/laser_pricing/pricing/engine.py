"""מנוע התמחור.

זרימת החישוב:
  1. תכנון פיצול — חלק שגדול מפלטה נחתך בכמה חתיכות ומולחם.
  2. קיבוץ לפי חומר+עובי — חלקים מחומרים שונים לא חולקים פלטה.
  3. נסטינג לכל קבוצה → אחוז בזבוז רציף.
  4. הפחתת שארית שמישה מהבזבוז — מה שחוזר למלאי אינו בזבוז.
  5. מדרגת הבזבוז של ינון → מכפיל שטח מחויב.
  6. רצפת פלטה — אי אפשר לחייב על פחות חומר ממה שנצרך בפועל.
  7. חומר + חיתוך + ניקוב + ריתוך + כיפוף + הקמה → מרווח → מע"מ.

כל מספר כספי בשרשרת הזאת מגיע מהטבלה. המנוע קובע רק *כמה* יחידות
של כל דבר צריך, לא *מה מחירן*.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..domain.part import Part
from ..nesting.nester import NestingResult, NestItem, nest
from ..nesting.splitting import SplitPlan, plan_split
from .tariff import MaterialRate, Tariff, WasteTier

MM2_PER_M2 = 1_000_000.0
MM_PER_M = 1000.0


@dataclass
class RejectedPart:
    """חלק שאין לו מחיר.

    מאז החלטת הפיצול (2026-08-12) חריגה מהפלטה **אינה** מגיעה לכאן —
    היא מתומחרת כחלק מפוצל. נשאר רק המקרה שבו ינון הציב תקרת חתיכות
    מפורשת (`max_pieces_per_part`) והחלק חורג ממנה.
    """

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
    billed_area_mm2: float = 0.0
    consumed_area_mm2: float = 0.0
    plate_floor_applied: bool = False

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
    welding_cost: float
    bending_cost: float
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
    pieces: int = 1
    weld_length_mm: float = 0.0
    plate_floor_applied: bool = False
    bend_count: int = 0
    bend_length_mm: float = 0.0

    @property
    def is_split(self) -> bool:
        return self.pieces > 1


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

    @property
    def has_split_parts(self) -> bool:
        return any(line.is_split for line in self.lines)


class PricingError(Exception):
    """התמחור לא יכול להתבצע."""


def price_order(
    parts: list[Part],
    tariff: Tariff,
    *,
    strict: bool = False,
) -> Quote:
    """מתמחר הזמנה שלמה.

    strict=True — חלק שנדחה מפיל את כל התמחור. ברירת המחדל False כדי
    שהממשק יוכל להציג ללקוח *איזה* חלק בעייתי ולמה.
    """
    if not parts:
        raise PricingError("אין חלקים לתמחור.")

    warnings: list[str] = []
    rejected: list[RejectedPart] = []
    accepted: list[tuple[Part, SplitPlan]] = []

    # שלב 1 — תכנון הפיצול. חלק גדול מפלטה נחתך ומולחם, לא נפסל.
    cap = tariff.max_pieces_per_part
    for part in parts:
        plan = plan_split(part.bbox, tariff.plate)
        if cap > 0 and plan.piece_count > cap:
            rejected.append(
                RejectedPart(
                    part_name=part.name,
                    reason=(
                        f'החלק דורש {plan.piece_count} חתיכות, והתקרה שהוגדרה היא {cap}. '
                        f'מידותיו {part.bbox.width:.0f}x{part.bbox.height:.0f} מ"מ.'
                    ),
                    width_mm=part.bbox.width,
                    height_mm=part.bbox.height,
                )
            )
            continue
        accepted.append((part, plan))

    if rejected and strict:
        raise PricingError(rejected[0].reason)
    if not accepted:
        raise PricingError("אף חלק בהזמנה אינו ניתן לתמחור.")

    # שלב 2 — קיבוץ. חומרים שונים לא חולקים פלטה, ולכן גם לא בזבוז.
    grouped: dict[tuple[str, float], list[tuple[Part, SplitPlan]]] = defaultdict(list)
    for part, plan in accepted:
        grouped[(part.material_key, round(part.thickness_mm, 3))].append((part, plan))

    groups: list[MaterialGroup] = []
    lines: list[QuoteLine] = []
    parts_subtotal = 0.0

    for (material_key, thickness), entries in grouped.items():
        rate = tariff.rate_for(material_key, thickness)
        group = _nest_group(entries, rate, tariff)
        groups.append(group)

        group_lines = _price_group(entries, rate, group, tariff)
        lines.extend(group_lines)
        parts_subtotal += sum(line.line_total for line in group_lines)

        _collect_group_warnings(entries, group, rate, tariff, warnings)

    # שלב 7 — עלויות ברמת ההזמנה.
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
            f"{len(rejected)} חלקים אינם ניתנים לתמחור וההצעה אינה כוללת אותם — "
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


def _nest_group(
    entries: list[tuple[Part, SplitPlan]], rate: MaterialRate, tariff: Tariff
) -> MaterialGroup:
    """פורס קבוצה אחת ומחשב את הבזבוז שממנו נגזרת המדרגה.

    חלק מפוצל נכנס לפריסה כמה חתיכות נפרדות — הן באמת תופסות מקום
    נפרד על הפלטות, ולעתים אף על פלטות שונות.
    """
    items: list[NestItem] = []
    for part, plan in entries:
        for copy_index in range(part.quantity):
            for piece in plan.pieces:
                suffix = f".{piece.index + 1}" if plan.is_split else ""
                items.append(
                    NestItem(
                        item_id=f"{part.name}#{copy_index + 1}{suffix}",
                        width_mm=piece.width_mm,
                        height_mm=piece.height_mm,
                        net_area_mm2=part.net_area_mm2 * piece.area_fraction,
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
        part_names=[p.name for p, _ in entries],
        consumed_area_mm2=result.consumed_area_mm2,
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


def _price_group(
    entries: list[tuple[Part, SplitPlan]],
    rate: MaterialRate,
    group: MaterialGroup,
    tariff: Tariff,
) -> list[QuoteLine]:
    """מתמחר קבוצה שלמה, כולל רצפת הפלטה.

    למה הרצפה חייבת להיות ברמת הקבוצה ולא ברמת החלק: פלטה משותפת לכל
    החלקים מאותו חומר ועובי, ולכן "כמה חומר נצרך" הוא נתון של הקבוצה.
    """
    drafts = [_draft_part(part, plan, rate, group, tariff) for part, plan in entries]

    # שלב 6 — רצפת הפלטה, ההחלטה של ינון (2026-08-12).
    #
    # מכפיל הבזבוז לבדו יכול לחייב על פחות חומר ממה שנקנה: חלק ענק עם
    # חלל פנימי גדול הורס פלטה שלמה ומשאיר שטח נטו קטן, ומכפיל של 2.00
    # על שטח קטן עדיין נמוך ממחיר הפלטה. מכאן והלאה שטח החיוב של הקבוצה
    # לא יורד מתחת לחומר שנצרך בפועל — פלטות שנקנו, בניכוי שאריות
    # שחוזרות למלאי.
    billed_total = sum(d.billed_area_mm2 * d.quantity for d in drafts)
    consumed = group.consumed_area_mm2
    floor_applied = False
    if billed_total > 0 and consumed > billed_total * (1 + 1e-9):
        scale = consumed / billed_total
        for draft in drafts:
            draft.billed_area_mm2 *= scale
        billed_total = consumed
        floor_applied = True

    group.billed_area_mm2 = billed_total
    group.plate_floor_applied = floor_applied

    return [_finalize_line(d, rate, group, tariff, floor_applied) for d in drafts]


@dataclass
class _Draft:
    """ערכי ביניים של חלק אחד, לפני שרצפת הפלטה נקבעה."""

    part: Part
    plan: SplitPlan
    quantity: int
    net_area_mm2: float
    billed_area_mm2: float
    cut_length_mm: float
    pierces: int
    weld_length_mm: float
    bend_count: int
    bend_length_mm: float


def _draft_part(
    part: Part, plan: SplitPlan, rate: MaterialRate, group: MaterialGroup, tariff: Tariff
) -> _Draft:
    """כמויות פיזיות של חלק אחד. בלי כסף — הכסף נקבע אחרי הרצפה."""
    net_area_mm2 = part.net_area_mm2
    return _Draft(
        part=part,
        plan=plan,
        quantity=part.quantity,
        net_area_mm2=net_area_mm2,
        billed_area_mm2=net_area_mm2 * group.tier.multiplier,
        cut_length_mm=part.cut_length_mm + plan.extra_cut_length_mm,
        pierces=part.pierce_count + plan.extra_pierces,
        weld_length_mm=plan.seam_length_mm,
        # הכיפופים מגיעים מהצהרת המזמין ולא מהגיאומטריה, ולכן הם עוברים
        # דרך החלק כמו שהם. הפיצול לא נוגע בהם: חלק שנחתך לשתיים ומולחם
        # עדיין מתכופף אותו מספר פעמים.
        bend_count=part.bend_count,
        bend_length_mm=part.bend_length_mm,
    )


def _finalize_line(
    draft: _Draft,
    rate: MaterialRate,
    group: MaterialGroup,
    tariff: Tariff,
    floor_applied: bool,
) -> QuoteLine:
    """הופך כמויות פיזיות למחיר, לפי הטבלה בלבד."""
    part = draft.part
    qty = draft.quantity

    price_per_m2 = tariff.price_per_m2(rate)
    material_unit = (draft.billed_area_mm2 / MM2_PER_M2) * price_per_m2
    cutting_unit = (draft.cut_length_mm / MM_PER_M) * rate.cut_rate_per_m
    piercing_unit = draft.pierces * rate.pierce_price
    welding_unit = (draft.weld_length_mm / MM_PER_M) * rate.weld_rate_per_m
    # כיפוף: מספר ההכאות ואורך הקו מתחברים. מי שמתמחר רק באחת מהשיטות
    # משאיר את השדה השני 0, וכך הרכיב שלו פשוט לא קיים.
    bending_unit = draft.bend_count * rate.bend_price + (
        draft.bend_length_mm / MM_PER_M
    ) * rate.bend_rate_per_m
    setup_unit = tariff.setup_fee_per_part

    unit_price = (
        material_unit + cutting_unit + piercing_unit + welding_unit + bending_unit + setup_unit
    )

    min_charge_applied = False
    if rate.min_charge_per_part > 0 and unit_price < rate.min_charge_per_part:
        unit_price = rate.min_charge_per_part
        min_charge_applied = True

    # מעגלים את מחיר היחידה *לפני* הכפל בכמות, כדי שלקוח שמכפיל את
    # המספר שהוא רואה יקבל בדיוק את סכום השורה שהוא רואה. עיגול אחרי
    # הכפל יוצר פער של אגורות שנראה כמו טעות חישוב.
    unit_price = _money(unit_price)

    weight_kg = (draft.net_area_mm2 * part.thickness_mm / 1e9) * rate.density_kg_m3

    box = part.bbox
    return QuoteLine(
        part_name=part.name,
        material_name=rate.material_name,
        thickness_mm=part.thickness_mm,
        quantity=qty,
        unit_price=unit_price,
        line_total=_money(unit_price * qty),
        material_cost=_money(material_unit * qty),
        cutting_cost=_money(cutting_unit * qty),
        piercing_cost=_money(piercing_unit * qty),
        welding_cost=_money(welding_unit * qty),
        bending_cost=_money(bending_unit * qty),
        setup_cost=_money(setup_unit * qty),
        min_charge_applied=min_charge_applied,
        net_area_mm2=round(draft.net_area_mm2, 2),
        billed_area_mm2=round(draft.billed_area_mm2, 2),
        cut_length_mm=round(draft.cut_length_mm, 2),
        pierces=draft.pierces,
        weight_kg=round(weight_kg * qty, 3),
        width_mm=round(box.width, 2),
        height_mm=round(box.height, 2),
        waste_pct=round(group.effective_waste_pct, 2),
        waste_tier_label=group.tier.label or f"עד {group.tier.max_waste_pct:g}% בזבוז",
        pieces=draft.plan.piece_count,
        weld_length_mm=round(draft.weld_length_mm, 2),
        plate_floor_applied=floor_applied,
        bend_count=draft.bend_count,
        bend_length_mm=round(draft.bend_length_mm, 2),
    )


def _collect_group_warnings(
    entries: list[tuple[Part, SplitPlan]],
    group: MaterialGroup,
    rate: MaterialRate,
    tariff: Tariff,
    warnings: list[str],
) -> None:
    """מה שינון חייב לראות גם אם התמחור עצמו הצליח."""
    label = f'{rate.material_name} {group.thickness_mm} מ"מ'

    if group.plate_floor_applied:
        warnings.append(
            f"{label}: הופעלה רצפת פלטה — החיוב הועלה לשטח החומר שנצרך בפועל "
            f"({group.consumed_area_mm2 / MM2_PER_M2:.2f} מ\"ר), כי מכפיל הבזבוז לבדו "
            f"חייב על פחות מזה."
        )

    # כיפוף שהוצהר ולא תומחר הוא בדיוק המקרה של הריתוך: העבודה תיעשה
    # במפעל, והלקוח לא ישלם עליה — ואף אחד לא יראה את זה בהצעה, כי
    # רכיב של 0 נראה בדיוק כמו רכיב שלא קיים.
    bent = [part for part, _ in entries if part.bend_count > 0 or part.bend_length_mm > 0]
    if bent and rate.bend_price <= 0 and rate.bend_rate_per_m <= 0:
        names = ", ".join(part.name for part in bent)
        warnings.append(
            f"{label}: {names} הוצהרו עם כיפופים, ומחיר הכיפוף בטבלה הוא 0 — "
            f"הכיפוף אינו מחויב. הוסף bend_price (או bend_rate_per_m) לשורת החומר."
        )

    split_parts = [(part, plan) for part, plan in entries if plan.is_split]
    if split_parts:
        names = ", ".join(part.name for part, _ in split_parts)
        warnings.append(
            f"{label}: {names} גדולים מפלטה ויוצרו בכמה חתיכות עם ריתוך."
        )
        if rate.weld_rate_per_m <= 0:
            warnings.append(
                f"{label}: מחיר הריתוך למטר הוא 0 בטבלה, ולכן תפרי הריתוך "
                f"אינם מחויבים. הוסף weld_rate_per_m לשורת החומר."
            )
        over_two = [part.name for part, plan in split_parts if plan.piece_count > 2]
        if over_two:
            warnings.append(
                f"{label}: {', '.join(over_two)} דורשים יותר משתי חתיכות. "
                f"ינון אישר פיצול לשתי פלטות — צריך אישור מפורש למעבר לזה."
            )

    # שפיות: חיוב על יותר חומר ממה שנרכש בפועל הוא כמעט תמיד טעות
    # הקלדה במדרגות. לא חוסמים ולא מתקנים — הטבלה היא מקור האמת —
    # אבל ינון חייב לראות את זה.
    purchased_area = group.nesting.total_plate_area_mm2
    if purchased_area > 0 and group.billed_area_mm2 > purchased_area * 1.001:
        warnings.append(
            f"{label}: החיוב הוא על {group.billed_area_mm2 / MM2_PER_M2:.2f} מ\"ר "
            f"בעוד שנרכשו {purchased_area / MM2_PER_M2:.2f} מ\"ר. "
            f"בדוק את מכפילי מדרגות הבזבוז."
        )

    if group.nesting.unplaced:
        names = ", ".join(sorted({i.part_name for i in group.nesting.unplaced}))
        warnings.append(f"מופעים שלא נכנסו לפריסה ({names}) — בדוק את המידות.")


def _money(value: float) -> float:
    return round(value + 1e-9, 2)
