"""מתאר + קווי כיפוף → עץ לוחות. **זה מה שהופך עורך חופשי לאפשרי.**

בקטלוג, כל בנאי יודע מראש איזה לוח מתקפל על איזה: הוא כתב את שניהם.
בעורך אין בנאי — יש מצולע שמישהו גרר, וקווים שמישהו מתח עליו — ולכן
צריך **לגזור** את המבנה מהגיאומטריה עצמה.

**ההנחה היחידה כאן היא הנחה של מכבש, לא של תוכנה:** קו כיפוף הוא
ישר וחוצה את החלק מקצה לקצה. מכבש מכופף לאורך סכין ישרה; אין דבר
כזה "כיפוף שנעצר באמצע". לכן קו שהמשתמש מתח באמצע **מוארך** לישר
שלם — הוא לא נדחה ולא נחתך. זה נראה כמו נדיבות ואינו: קו שהיה
נשאר קצר היה מייצר לוח שהמכונה אינה יכולה לכופף, והמסך היה מציג
מוצר שאי אפשר להזמין.

**ציר הכיפוף עצמו כן מוגבל** לקטע שבו שני הלוחות באמת נוגעים זה
בזה. זה ההבדל בין הקו שמותחים לבין הכיפוף שמתבצע, והוא זה שנכנס
לדף הכיפוף ולחישוב אורך הכיפוף.

**האב נבחר לפי שטח ולא לפי סדר.** הלוח הגדול ביותר הוא זה שנשאר
מונח על השולחן בזמן שהשאר מתרוממים, וזו גם הכוונה של מי שמצייר.
בחירה לפי סדר הקווים הייתה תלויה בסדר שבו המשתמש גרר אותם, כלומר
משתנה בכל פעם שהוא מוחק קו ומצייר אותו מחדש.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .folding import FlatPanel, _clip_halfplane

Point2 = tuple[float, float]

MIN_PANEL_AREA_MM2 = 4.0
"""שטח שמתחתיו "לוח" הוא רסיס של דיוק מספרי ולא חלק מהחלק."""

OVERLAP_TOL_MM = 0.05


class PanelError(ValueError):
    """קווי כיפוף שאי אפשר לגזור מהם מבנה."""


@dataclass(frozen=True)
class BendLine:
    x1: float
    y1: float
    x2: float
    y2: float
    angle_deg: float = 90.0
    flip: bool = False
    """לאיזה כיוון מתרומם הלוח. **החלטה של המשתמש ולא של הגיאומטריה.**

    `fold` יודעת לבחור סימן שמרים את הלוח ביחס לנורמל של האב, וזה
    נכון לקטלוג שבו כל מוצר מתקפל לכיוון אחד הגיוני. בעורך, אותו
    קו בדיוק יכול להיות מגש או גג — וזה שני מוצרים שונים.
    """

    def frame(self) -> tuple[Point2, Point2, float]:
        dx, dy = self.x2 - self.x1, self.y2 - self.y1
        length = math.hypot(dx, dy)
        if length < 1e-9:
            raise PanelError("קו כיפוף באורך אפס.")
        return (self.x1, self.y1), (dy / length, -dx / length), length


def _area(polygon: list[Point2]) -> float:
    total = 0.0
    for i in range(len(polygon)):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % len(polygon)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _centroid(polygon: list[Point2]) -> Point2:
    n = len(polygon)
    return (sum(p[0] for p in polygon) / n, sum(p[1] for p in polygon) / n)


def _side(point: Point2, origin: Point2, normal: Point2) -> float:
    return (point[0] - origin[0]) * normal[0] + (point[1] - origin[1]) * normal[1]


def _span(polygon: list[Point2], origin: Point2, direction: Point2) -> tuple[float, float]:
    values = [
        (p[0] - origin[0]) * direction[0] + (p[1] - origin[1]) * direction[1] for p in polygon
    ]
    return min(values), max(values)


MAX_BENDS = 10
"""תקרת קווים. האזורים נמנים על **צירופי הסימנים** של הקווים, ולכן
העלות מכפילה את עצמה בכל קו נוסף. עשרה כיפופים הם 1024 חיתוכים
זולים — ומעבר לזה זה כבר לא חלק שמכבש מכופף בהעברה אחת.
"""


def split_regions(outline: list[Point2], bends: list[BendLine]) -> list[list[Point2]]:
    """חותך את המתאר לאזורים — **תא אחד לכל צירוף צדדים.**

    **הדרך הישירה כאן שגויה, וזה נמדד.** חיתוך חוזר "אזור אחרי אזור,
    קו אחרי קו" נשען על סאתרלנד–הודג'מן, שנכון רק כשהתוצאה מחוברת.
    מתאר של מגש הוא **צלב, כלומר קעור**: חיתוכו בקו אופקי מתחת לזרוע
    מייצר מלבן אחד — אבל האלגוריתם מחזיר אותו עם "גשר" לאורך קו
    החיתוך שמגיע עד קצות הזרועות. דופן המגש יצאה ברוחב **400 מ"מ
    במקום 300**, הגוף המקופל נראה סביר, והמידה הייתה שגויה בשקט.

    לכן כל אזור נחתך כאן מול **כל** הקווים בבת אחת: הוא מוגבל מכל
    צדדיו, הגשרים נחתכים החוצה, והתוצאה מחוברת — כלומר בדיוק המקרה
    שבו האלגוריתם נכון.
    """
    if len(bends) > MAX_BENDS:
        raise PanelError(f"יותר מ-{MAX_BENDS} קווי כיפוף על חלק אחד.")
    frames = [bend.frame() for bend in bends]
    regions: list[list[Point2]] = []
    for mask in range(1 << len(bends)):
        piece = list(outline)
        for index, (origin, normal, _) in enumerate(frames):
            if mask >> index & 1:
                normal = (-normal[0], -normal[1])
            piece = _clip_halfplane(piece, origin, normal, 0.0)
            if len(piece) < 3 or _area(piece) <= MIN_PANEL_AREA_MM2:
                piece = []
                break
        if piece and _area(piece) > MIN_PANEL_AREA_MM2:
            regions.append(_dedupe(piece))
    return regions


def _dedupe(polygon: list[Point2], tol: float = 1e-7) -> list[Point2]:
    """מוריד נקודות כפולות שהחיתוך משאיר על קו החיתוך."""
    out: list[Point2] = []
    for point in polygon:
        if not out or abs(point[0] - out[-1][0]) > tol or abs(point[1] - out[-1][1]) > tol:
            out.append(point)
    while len(out) > 1 and abs(out[0][0] - out[-1][0]) <= tol and abs(out[0][1] - out[-1][1]) <= tol:
        out.pop()
    return out


def panels_from_bends(outline: list[Point2], bends: list[BendLine]) -> list[FlatPanel]:
    """מתאר + קווים → לוחות עם אב, ציר וזווית.

    **קו שאינו חוצה נכשל במפורש ואינו מתעלם בשקט.** קו שנעצר באמצע
    החלק היה מייצר אזור אחד במקום שניים, החלק היה נראה שטוח, והמשתמש
    היה מסיק שהכיפוף "לא נשמר" — במקום לדעת שהוא לא אפשרי.
    """
    if not bends:
        return []

    regions = split_regions(outline, bends)
    if len(regions) < 2:
        raise PanelError(
            "אף קו כיפוף אינו חוצה את החלק. הזז את הקו כך שיעבור בתוך המתאר."
        )

    # מי גובל במי, ודרך איזה קו. השכנות נמדדת על הקו עצמו: שני
    # אזורים שנמצאים משני צדדיו אך אינם חופפים לאורכו אינם מחוברים,
    # והחיבור ביניהם היה מקפל לוח סביב ציר שאינו נוגע בו.
    links: dict[tuple[int, int], tuple[int, tuple[float, float]]] = {}
    for index, bend in enumerate(bends):
        origin, normal, _ = bend.frame()
        direction = (-normal[1], normal[0])
        for a in range(len(regions)):
            for b in range(a + 1, len(regions)):
                sa = _side(_centroid(regions[a]), origin, normal)
                sb = _side(_centroid(regions[b]), origin, normal)
                if (sa > 0) == (sb > 0):
                    continue
                if not _touches(regions[a], origin, normal) or not _touches(
                    regions[b], origin, normal
                ):
                    continue
                lo_a, hi_a = _span(regions[a], origin, direction)
                lo_b, hi_b = _span(regions[b], origin, direction)
                lo, hi = max(lo_a, lo_b), min(hi_a, hi_b)
                if hi - lo <= OVERLAP_TOL_MM:
                    continue
                links[(a, b)] = (index, (lo, hi))

    root = max(range(len(regions)), key=lambda i: _area(regions[i]))
    order: list[int] = [root]
    parent_of: dict[int, tuple[int, int, tuple[float, float]]] = {}
    seen = {root}
    queue = [root]
    while queue:
        current = queue.pop(0)
        for (a, b), (bend_index, overlap) in links.items():
            for near, far in ((a, b), (b, a)):
                if near == current and far not in seen:
                    seen.add(far)
                    parent_of[far] = (current, bend_index, overlap)
                    order.append(far)
                    queue.append(far)

    if len(seen) != len(regions):
        raise PanelError("יש חלק שאינו מחובר לשאר — בדוק שקווי הכיפוף חוצים את החלק.")

    position = {region: slot for slot, region in enumerate(order)}
    panels: list[FlatPanel] = []
    for region in order:
        if region == root:
            panels.append(FlatPanel(outline=regions[region], label="בסיס"))
            continue
        parent, bend_index, (lo, hi) = parent_of[region]
        bend = bends[bend_index]
        origin, normal, _ = bend.frame()
        direction = (-normal[1], normal[0])
        axis = (
            origin[0] + direction[0] * lo,
            origin[1] + direction[1] * lo,
            origin[0] + direction[0] * hi,
            origin[1] + direction[1] * hi,
        )
        panels.append(
            FlatPanel(
                outline=regions[region],
                parent=position[parent],
                fold_axis=axis,
                angle_deg=bend.angle_deg,
                flip=bend.flip,
                bend_index=bend_index,
                label=f"כיפוף {bend_index + 1}",
            )
        )
    return panels


def _touches(polygon: list[Point2], origin: Point2, normal: Point2) -> bool:
    """האם למצולע יש שפה **על** הקו, ולא רק צד שלו."""
    return any(abs(_side(p, origin, normal)) <= OVERLAP_TOL_MM for p in polygon)


__all__ = ["BendLine", "MIN_PANEL_AREA_MM2", "PanelError", "panels_from_bends", "split_regions"]
