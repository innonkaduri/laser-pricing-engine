"""הפלטה והאילוץ הפיזי הקשיח.

משטח העבודה הוא 3000x1500 מ"מ. חלק שחורג ממנו **לא ניתן לייצור** —
זו לא שאלה של מחיר אלא של אי-אפשרות טכנית. הבדיקה הזאת חוסמת
לפני כל חישוב תמחור, ואי אפשר לעקוף אותה בטבלת המחירים.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.geometry import BoundingBox

# סובלנות מדידה: חלק ברוחב 1500.02 מ"מ הוא חלק של 1500 עם רעש נומרי בקובץ.
FIT_TOLERANCE_MM = 0.1


@dataclass(frozen=True)
class Plate:
    """פלטת גלם.

    edge_margin_mm — שוליים בלתי שמישים בהיקף (אחיזת מלחציים / קצה גולמי).
    ברירת המחדל 20 מ"מ לכל צד לפי החלטת ינון (2026-08-11), ולכן שטח
    העבודה בפועל הוא 2960x1460. הערך פרמטרי ולא קבוע בקוד — אפשר לשנותו
    לכל פלטה בנפרד.
    """

    width_mm: float = 3000.0
    height_mm: float = 1500.0
    edge_margin_mm: float = 20.0
    label: str = "3000x1500"

    def __post_init__(self) -> None:
        if self.width_mm <= 0 or self.height_mm <= 0:
            raise ValueError("מידות פלטה חייבות להיות חיוביות")
        if self.edge_margin_mm < 0:
            raise ValueError("שוליים לא יכולים להיות שליליים")
        if self.usable_width <= 0 or self.usable_height <= 0:
            raise ValueError("השוליים גדולים מהפלטה עצמה")

    @property
    def usable_width(self) -> float:
        return self.width_mm - 2 * self.edge_margin_mm

    @property
    def usable_height(self) -> float:
        return self.height_mm - 2 * self.edge_margin_mm

    @property
    def area_mm2(self) -> float:
        return self.width_mm * self.height_mm

    @property
    def usable_area_mm2(self) -> float:
        return self.usable_width * self.usable_height


STANDARD_PLATE = Plate()
"""הפלטה של העסק. אילוץ פיזי, לא פרמטר תמחור."""


@dataclass(frozen=True)
class Manufacturability:
    """תשובה בינארית — ורק כאן התשובה בינארית."""

    ok: bool
    reason: str = ""
    required_width_mm: float = 0.0
    required_height_mm: float = 0.0
    rotated: bool = False

    def raise_if_impossible(self) -> None:
        if not self.ok:
            raise NotManufacturableError(self.reason)


class NotManufacturableError(Exception):
    """החלק חורג מהפלטה. אין מחיר — אין ייצור."""


def check_manufacturability(bbox: BoundingBox, plate: Plate = STANDARD_PLATE) -> Manufacturability:
    """האם החלק נכנס בכלל על הפלטה, בסיבוב 0° או 90°.

    זו הבדיקה הקשיחה היחידה במערכת. כל השאר — ניצולת, בזבוז, מחיר —
    הוא רציף ומדורג.
    """
    w, h = bbox.width, bbox.height
    limit_w = plate.usable_width + FIT_TOLERANCE_MM
    limit_h = plate.usable_height + FIT_TOLERANCE_MM

    if w <= limit_w and h <= limit_h:
        return Manufacturability(ok=True, required_width_mm=w, required_height_mm=h, rotated=False)
    if h <= limit_w and w <= limit_h:
        return Manufacturability(ok=True, required_width_mm=h, required_height_mm=w, rotated=True)

    return Manufacturability(
        ok=False,
        reason=(
            f'החלק במידות {w:.1f}x{h:.1f} מ"מ חורג ממשטח העבודה '
            f'{plate.usable_width:.0f}x{plate.usable_height:.0f} מ"מ בשני הסיבובים — '
            f'לא ניתן לייצור.'
        ),
        required_width_mm=w,
        required_height_mm=h,
    )
