"""פריסת חלקים על פלטות + חישוב הבזבוז המדורג.

כאן מתממש העיקרון: אחרי שעברנו את האילוץ הפיזי, שום דבר אינו בינארי.
הפלט הוא אחוז ניצולת רציף לכל פלטה, ודירוג איכות לשארית שנשארה —
כי שארית של 800x1400 חוזרת למלאי ושארית של 3000x40 היא גרוטאה.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .packer import MaxRectsPacker, Placement, Rect
from .plate import STANDARD_PLATE, Plate, check_manufacturability


@dataclass(frozen=True)
class NestItem:
    """מופע בודד לפריסה. חלק בכמות 5 מייצר 5 מופעים."""

    item_id: str
    width_mm: float
    height_mm: float
    net_area_mm2: float
    part_name: str = ""


@dataclass(frozen=True)
class RemnantPolicy:
    """מדיניות שארית — ערכים שינון קובע, לא המנוע.

    min_usable_short_side_mm: שער חד — מתחת לזה השארית היא רצועה חסרת ערך.
    full_value_area_mm2: השטח שממנו ומעלה שארית שווה את מלוא ערכה. חייב
        להיות גדול משמעותית מריבוע הצד הקצר, אחרת הדירוג מתקפל לבינארי:
        כל שארית שעוברת את השער מקבלת אוטומטית ציון מלא.
    max_credit_ratio: איזה חלק מערך השארית באמת מזוכה ללקוח.
    """

    min_usable_short_side_mm: float = 200.0
    full_value_area_mm2: float = 800.0 * 800.0
    max_credit_ratio: float = 0.5

    def __post_init__(self) -> None:
        if self.full_value_area_mm2 <= self.min_usable_short_side_mm**2:
            raise ValueError(
                "full_value_area_mm2 חייב להיות גדול מריבוע הצד הקצר המינימלי, "
                "אחרת דירוג השארית מאבד את הרציפות שלו."
            )


@dataclass(frozen=True)
class RemnantGrade:
    """ציון איכות רציף לשארית — 0 = גרוטאה, 1 = חוזרת למלאי כמו שהיא.

    reusable_area_mm2 הוא השדה היחיד שמותר להשתמש בו לחישוב: הוא כבר
    כולל גם את האיכות וגם את max_credit_ratio. שימוש ב-area_mm2 הגולמי
    לחישוב בזבוז מתעלם מהדיאל של ינון ומזכה יתר על המידה.
    """

    width_mm: float
    height_mm: float
    area_mm2: float
    quality: float
    reusable_area_mm2: float
    credit_fraction_of_plate: float

    @property
    def usable(self) -> bool:
        return self.quality > 0.0


EMPTY_REMNANT = RemnantGrade(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def grade_remnant(rect: Rect | None, plate: Plate, policy: RemnantPolicy) -> RemnantGrade:
    """מדרג שארית אחת.

    רצועה צרה היא גרוטאה בלי קשר לשטחה — ולכן הצד הקצר הוא שער חד.
    מעבר לשער, האיכות עולה רציפות עם השטח עד לרוויה.
    """
    if rect is None or rect.area <= 0:
        return EMPTY_REMNANT

    if rect.short_side < policy.min_usable_short_side_mm:
        return RemnantGrade(rect.w, rect.h, rect.area, 0.0, 0.0, 0.0)

    quality = min(1.0, rect.area / policy.full_value_area_mm2)
    reusable = rect.area * quality * policy.max_credit_ratio
    credit = reusable / plate.area_mm2 if plate.area_mm2 else 0.0
    return RemnantGrade(rect.w, rect.h, rect.area, quality, reusable, credit)


@dataclass
class PlateLayout:
    """פלטה אחת אחרי פריסה."""

    index: int
    plate: Plate
    placements: list[Placement] = field(default_factory=list)
    net_area_mm2: float = 0.0
    remnants: list[RemnantGrade] = field(default_factory=list)

    @property
    def reusable_area_mm2(self) -> float:
        """שטח שחוזר למלאי, אחרי איכות ואחרי מקדם הזיכוי של ינון."""
        return sum(r.reusable_area_mm2 for r in self.remnants)

    @property
    def largest_remnant(self) -> RemnantGrade:
        return max(self.remnants, key=lambda r: r.area_mm2, default=EMPTY_REMNANT)

    @property
    def bbox_area_mm2(self) -> float:
        return sum(p.rect.area for p in self.placements)

    @property
    def utilization(self) -> float:
        """ניצולת אמיתית — שטח מתכת נטו חלקי שטח הפלטה."""
        return self.net_area_mm2 / self.plate.area_mm2 if self.plate.area_mm2 else 0.0

    @property
    def waste_ratio(self) -> float:
        return max(0.0, 1.0 - self.utilization)

    @property
    def part_count(self) -> int:
        return len(self.placements)


@dataclass
class NestingResult:
    layouts: list[PlateLayout]
    unplaced: list[NestItem] = field(default_factory=list)

    @property
    def remnants(self) -> list[RemnantGrade]:
        """כל השאריות השמישות מכל הפלטות."""
        return [r for layout in self.layouts for r in layout.remnants]

    @property
    def largest_remnant(self) -> RemnantGrade:
        return max(self.remnants, key=lambda r: r.area_mm2, default=EMPTY_REMNANT)

    @property
    def reusable_area_mm2(self) -> float:
        return sum(layout.reusable_area_mm2 for layout in self.layouts)

    @property
    def consumed_area_mm2(self) -> float:
        """החומר שנצרך בפועל — פלטות פחות מה שחוזר למלאי."""
        return max(0.0, self.total_plate_area_mm2 - self.reusable_area_mm2)

    @property
    def remnant_credit_fraction(self) -> float:
        """זיכוי השאריות ביחידות של פלטות שלמות."""
        if not self.layouts:
            return 0.0
        plate_area = self.layouts[0].plate.area_mm2
        return self.reusable_area_mm2 / plate_area if plate_area else 0.0

    @property
    def plates_used(self) -> int:
        return len(self.layouts)

    @property
    def total_net_area_mm2(self) -> float:
        return sum(layout.net_area_mm2 for layout in self.layouts)

    @property
    def total_plate_area_mm2(self) -> float:
        return sum(layout.plate.area_mm2 for layout in self.layouts)

    @property
    def utilization(self) -> float:
        total = self.total_plate_area_mm2
        return self.total_net_area_mm2 / total if total else 0.0

    @property
    def waste_ratio(self) -> float:
        """אחוז הבזבוז שהאלגוריתם המדורג מתומחר לפיו."""
        return max(0.0, 1.0 - self.utilization)

    @property
    def effective_plates(self) -> float:
        """פלטות בפועל פחות זיכוי השאריות — זה מה שנצרך באמת."""
        return max(0.0, self.plates_used - self.remnant_credit_fraction)


def nest(
    items: list[NestItem],
    plate: Plate = STANDARD_PLATE,
    part_gap_mm: float = 5.0,
    policy: RemnantPolicy | None = None,
) -> NestingResult:
    """פורס את כל המופעים על פלטות ומחזיר ניצולת ובזבוז.

    part_gap_mm — מרווח בין חלקים (רוחב חתך + מרווח בטיחות). מתווסף לכל
    תיבה חוסמת, ולכן נכלל אוטומטית בחישוב הבזבוז.
    """
    if part_gap_mm < 0:
        raise ValueError("מרווח בין חלקים לא יכול להיות שלילי")
    policy = policy or RemnantPolicy()

    # סדר יורד לפי שטח התיבה — הכלל שנותן את הניצולת הטובה ביותר ב-MaxRects.
    ordered = sorted(items, key=lambda i: i.width_mm * i.height_mm, reverse=True)

    layouts: list[PlateLayout] = []
    packers: list[MaxRectsPacker] = []
    unplaced: list[NestItem] = []

    for item in ordered:
        # האילוץ הקשיח נבדק שוב כאן: מופע שלא נכנס לפלטה לעולם לא ייכנס.
        padded_w = item.width_mm + part_gap_mm
        padded_h = item.height_mm + part_gap_mm
        if not _fits(padded_w, padded_h, plate):
            unplaced.append(item)
            continue

        placed = False
        for layout, packer in zip(layouts, packers):
            placement = packer.insert(padded_w, padded_h, item_id=item.item_id)
            if placement is not None:
                layout.placements.append(placement)
                layout.net_area_mm2 += item.net_area_mm2
                placed = True
                break

        if not placed:
            packer = MaxRectsPacker(plate.usable_width, plate.usable_height)
            placement = packer.insert(padded_w, padded_h, item_id=item.item_id)
            if placement is None:
                unplaced.append(item)
                continue
            layout = PlateLayout(index=len(layouts), plate=plate)
            layout.placements.append(placement)
            layout.net_area_mm2 += item.net_area_mm2
            layouts.append(layout)
            packers.append(packer)

    # דירוג שאריות בכל הפלטות, לא רק באחרונה: פלטה מלאה ממילא לא תניב
    # שאריות שעוברות את שער הצד הקצר, ולכן אין צורך בכלל מיוחד עבורה.
    for layout, packer in zip(layouts, packers):
        rects = packer.extract_remnants(policy.min_usable_short_side_mm)
        layout.remnants = [grade_remnant(r, plate, policy) for r in rects]

    return NestingResult(layouts=layouts, unplaced=unplaced)


def _fits(w: float, h: float, plate: Plate) -> bool:
    from ..domain.geometry import BoundingBox

    return check_manufacturability(BoundingBox(0.0, 0.0, w, h), plate).ok
