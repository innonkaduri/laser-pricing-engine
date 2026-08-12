"""פיצול חלק שגדול מפלטה אחת לחתיכות שמולחמות זו לזו.

החלטת ינון (2026-08-12) שינתה כלל יסוד: חלק שאינו נכנס לפלטה אחת
**אינו נפסל עוד**. חותכים אותו בשתי פלטות נפרדות ומלחימים. לכן האילוץ
של 3x1.5 מ' חדל להיות שער בינארי של "אפשר/אי אפשר", והפך לגורם עלות:
כמה פלטות נצרכות וכמה מטרים של ריתוך נוספים.

מה המודול הזה כן עושה, ומה במפורש לא:

הפיצול מחושב על ה**תיבה החוסמת** — הרשת נחתכת לחתיכות שוות. הוא לא
חותך את המתאר הגיאומטרי עצמו, ולכן:
  • שטח המתכת מחולק בין החתיכות באופן אחיד (שבר שווה לכל חתיכה),
    בעוד שבפועל חתיכה אחת עשויה להכיל יותר מתכת מחברתה.
  • תפר הריתוך עשוי לעבור דרך חור בחלק האמיתי.
המספרים הרלוונטיים לתמחור — כמה פלטות נצרכות, כמה אורך תפר, כמה
ניקובים נוספים — נכונים; החלוקה הפנימית של השטח היא קירוב. זה מספיק
לתמחור ולא מספיק לייצור: את קו החיתוך בפועל קובע מי שמכין את הקובץ.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..domain.geometry import BoundingBox
from .plate import FIT_TOLERANCE_MM, STANDARD_PLATE, Plate


@dataclass(frozen=True)
class SplitPiece:
    """חתיכה אחת מתוך חלק מפוצל."""

    index: int
    width_mm: float
    height_mm: float
    area_fraction: float
    """איזה חלק משטח המתכת של החלק המקורי מיוחס לחתיכה הזאת."""


@dataclass(frozen=True)
class SplitPlan:
    """תוכנית הפיצול של חלק אחד.

    seam_length_mm הוא סך אורך התפרים הפנימיים — כלומר כמה מטרים צריך
    להלחים כדי להחזיר את החלק לגוף אחד.
    """

    pieces: tuple[SplitPiece, ...]
    columns: int
    rows: int
    seam_length_mm: float
    rotated: bool
    fits_single_plate: bool

    @property
    def piece_count(self) -> int:
        return len(self.pieces)

    @property
    def is_split(self) -> bool:
        return self.piece_count > 1

    @property
    def extra_cut_length_mm(self) -> float:
        """אורך החיתוך שנוסף בגלל הפיצול.

        כל תפר נחתך **פעמיים**: החתיכות יושבות על פלטות שונות, ולכן כל
        אחת מהן מקבלת את הקצה שלה בחיתוך נפרד.
        """
        return 2.0 * self.seam_length_mm

    @property
    def extra_pierces(self) -> int:
        """כל חתיכה נוספת פותחת מתאר חיצוני חדש, ולכן ניקוב נוסף."""
        return self.piece_count - 1


def plan_split(bbox: BoundingBox, plate: Plate = STANDARD_PLATE) -> SplitPlan:
    """מחשב לכמה חתיכות צריך לפצל את החלק, ובאיזה כיוון.

    נבחנים שני הסיבובים. הקריטריון הראשון הוא מספר החתיכות — כל חתיכה
    נוספת היא תפר ריתוך, עבודת הרכבה ונקודת כשל. רק בין אפשרויות עם
    אותו מספר חתיכות נבחר התפר הקצר יותר.
    """
    w, h = bbox.width, bbox.height
    if w <= 0 or h <= 0:
        raise ValueError("אי אפשר לפצל חלק ללא מידות")

    candidates = [_grid_for(w, h, plate, rotated=False)]
    if abs(w - h) > FIT_TOLERANCE_MM:
        candidates.append(_grid_for(h, w, plate, rotated=True))

    # (מספר חתיכות, אורך תפר) — סדר לקסיקוגרפי שמעדיף פחות חתיכות.
    cols, rows, seam, rotated = min(candidates, key=lambda c: (c[0] * c[1], c[2]))

    span_w, span_h = (h, w) if rotated else (w, h)
    piece_w = span_w / cols
    piece_h = span_h / rows
    fraction = 1.0 / (cols * rows)

    pieces = tuple(
        SplitPiece(index=i, width_mm=piece_w, height_mm=piece_h, area_fraction=fraction)
        for i in range(cols * rows)
    )

    return SplitPlan(
        pieces=pieces,
        columns=cols,
        rows=rows,
        seam_length_mm=seam,
        rotated=rotated,
        fits_single_plate=(cols * rows == 1),
    )


def _grid_for(span_w: float, span_h: float, plate: Plate, rotated: bool) -> tuple[int, int, float, bool]:
    """כמה עמודות ושורות דרושות לכיסוי המידות, ומה אורך התפר שנוצר."""
    cols = _tiles(span_w, plate.usable_width)
    rows = _tiles(span_h, plate.usable_height)
    # תפר אנכי חוצה את כל הגובה, תפר אופקי את כל הרוחב.
    seam = (cols - 1) * span_h + (rows - 1) * span_w
    return cols, rows, seam, rotated


def _tiles(span: float, limit: float) -> int:
    """כמה פעמים צריך את limit כדי לכסות את span, עם סובלנות מדידה."""
    if span <= limit + FIT_TOLERANCE_MM:
        return 1
    return max(1, math.ceil((span - FIT_TOLERANCE_MM) / limit))
