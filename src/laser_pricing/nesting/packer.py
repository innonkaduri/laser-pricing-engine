"""אריזת מלבנים על הפלטה — MaxRects עם Best-Short-Side-Fit וסיבוב 90°.

חשוב להיות כן לגבי מה זה: האריזה היא על **התיבות החוסמות** של החלקים,
לא על המתאר הגיאומטרי האמיתי. לכן הניצולת שמתקבלת היא הערכה שמרנית —
נסטינג אמיתי יכול לקנן חלקים קעורים זה בתוך זה ולשפר. השמרנות הזאת
מכוונת: עדיף להעריך בזבוז ביתר מאשר לתמחר בחסר.

היתרון של MaxRects לצרכים שלנו: רשימת המלבנים הפנויים נשמרת לאורך
כל הריצה, ובסוף היא בדיוק מפת השאריות של הפלטה — בלי חישוב נוסף.
"""

from __future__ import annotations

from dataclasses import dataclass

EPS = 1e-6


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def top(self) -> float:
        return self.y + self.h

    @property
    def area(self) -> float:
        return self.w * self.h

    @property
    def short_side(self) -> float:
        return min(self.w, self.h)

    @property
    def long_side(self) -> float:
        return max(self.w, self.h)

    def contains(self, other: "Rect") -> bool:
        return (
            other.x >= self.x - EPS
            and other.y >= self.y - EPS
            and other.right <= self.right + EPS
            and other.top <= self.top + EPS
        )


@dataclass(frozen=True)
class Placement:
    """מיקום סופי של חלק על הפלטה."""

    rect: Rect
    rotated: bool
    item_id: str = ""


class MaxRectsPacker:
    """אורז מלבנים בודד לפלטה אחת."""

    def __init__(self, width: float, height: float) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("מידות אזור אריזה חייבות להיות חיוביות")
        self.width = width
        self.height = height
        self.free: list[Rect] = [Rect(0.0, 0.0, width, height)]
        self.placements: list[Placement] = []

    # ---- API ציבורי ----

    def insert(self, w: float, h: float, item_id: str = "", allow_rotation: bool = True) -> Placement | None:
        """מנסה למקם מלבן. מחזיר None אם אין מקום — הקורא פותח פלטה חדשה."""
        best_score: tuple[float, float] | None = None
        best_rect: Rect | None = None
        best_rotated = False

        orientations = [(w, h, False)]
        if allow_rotation and abs(w - h) > EPS:
            orientations.append((h, w, True))

        for free in self.free:
            for rw, rh, rotated in orientations:
                if rw > free.w + EPS or rh > free.h + EPS:
                    continue
                leftover_horizontal = abs(free.w - rw)
                leftover_vertical = abs(free.h - rh)
                score = (min(leftover_horizontal, leftover_vertical), max(leftover_horizontal, leftover_vertical))
                if best_score is None or score < best_score:
                    best_score = score
                    best_rect = Rect(free.x, free.y, rw, rh)
                    best_rotated = rotated

        if best_rect is None:
            return None

        self._occupy(best_rect)
        placement = Placement(rect=best_rect, rotated=best_rotated, item_id=item_id)
        self.placements.append(placement)
        return placement

    @property
    def used_area(self) -> float:
        """שטח התיבות החוסמות שהונחו — לא שטח המתכת נטו."""
        return sum(p.rect.area for p in self.placements)

    @property
    def largest_free_rect(self) -> Rect | None:
        """השארית הגדולה ביותר על הפלטה."""
        if not self.free:
            return None
        return max(self.free, key=lambda r: r.area)

    def extract_remnants(self, min_short_side: float, max_count: int = 16) -> list[Rect]:
        """מחלץ את כל השאריות השמישות כמלבנים **זרים זה לזה**.

        למה זה הכרחי: ב-MaxRects המלבנים הפנויים חופפים זה לזה במכוון,
        ולכן אי אפשר לסכום את שטחם. מצד שני, להסתמך רק על הגדול ביותר
        גורם לאחוז הבזבוז לקפוץ הלוך ושוב בכל תוספת חלק — ואז 3 חלקים
        יוצאים יקרים יותר מ-2, מה שאי אפשר להסביר ללקוח.

        הפתרון מחקה את מה שעושים במפעל: חותכים את החתיכה השמישה הגדולה
        ביותר, ומה שנשאר סביבה נבדק שוב. כך מתקבלת קבוצה זרה שאפשר
        לסכם, והמדד יציב.
        """
        saved_free = list(self.free)
        saved_placements = list(self.placements)
        remnants: list[Rect] = []
        try:
            while len(remnants) < max_count:
                candidates = [r for r in self.free if r.short_side >= min_short_side]
                if not candidates:
                    break
                best = max(candidates, key=lambda r: r.area)
                remnants.append(best)
                self._occupy(best)
        finally:
            self.free = saved_free
            self.placements = saved_placements
        return remnants

    # ---- פנימי ----

    def _occupy(self, used: Rect) -> None:
        next_free: list[Rect] = []
        for free in self.free:
            splits = self._split(free, used)
            if splits is None:
                next_free.append(free)
            else:
                next_free.extend(splits)
        self.free = self._prune(next_free)

    @staticmethod
    def _split(free: Rect, used: Rect) -> list[Rect] | None:
        """מחזיר את שברי free שנותרו פנויים, או None אם אין חפיפה בכלל."""
        no_overlap = (
            used.x >= free.right - EPS
            or used.right <= free.x + EPS
            or used.y >= free.top - EPS
            or used.top <= free.y + EPS
        )
        if no_overlap:
            return None

        pieces: list[Rect] = []
        # פסים אופקיים מתחת ומעל השטח התפוס
        if used.y > free.y + EPS:
            pieces.append(Rect(free.x, free.y, free.w, used.y - free.y))
        if used.top < free.top - EPS:
            pieces.append(Rect(free.x, used.top, free.w, free.top - used.top))
        # פסים אנכיים משמאל ומימין
        if used.x > free.x + EPS:
            pieces.append(Rect(free.x, free.y, used.x - free.x, free.h))
        if used.right < free.right - EPS:
            pieces.append(Rect(used.right, free.y, free.right - used.right, free.h))
        return pieces

    @staticmethod
    def _prune(rects: list[Rect]) -> list[Rect]:
        """מסיר מלבנים פנויים שמוכלים באחרים — אחרת הרשימה מתפוצצת."""
        kept: list[Rect] = []
        for i, candidate in enumerate(rects):
            if candidate.w <= EPS or candidate.h <= EPS:
                continue
            dominated = False
            for j, other in enumerate(rects):
                if i == j or not other.contains(candidate):
                    continue
                # מלבנים זהים מכילים זה את זה — שומרים רק את הראשון
                if candidate.contains(other) and i < j:
                    continue
                dominated = True
                break
            if not dominated:
                kept.append(candidate)
        return kept
