"""גיאומטריה בסיסית של חלק שטוח.

כל היחידות במילימטרים. שטחים במ"מ רבועים.
המודול הזה טהור — בלי קלט/פלט, בלי תלות ב-ezdxf — כדי שיהיה בר-בדיקה
וכדי שקלט ידני, DXF ומודל תלת-ממד יזרמו כולם לאותם טיפוסים.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Point = tuple[float, float]

# סובלנות מספרית לסגירת מתאר: שתי נקודות קרובות מזה נחשבות לאותה נקודה.
CLOSURE_TOLERANCE_MM = 0.05


@dataclass(frozen=True)
class BoundingBox:
    """תיבה חוסמת ישרת-צירים."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def long_side(self) -> float:
        return max(self.width, self.height)

    @property
    def short_side(self) -> float:
        return min(self.width, self.height)

    @staticmethod
    def from_points(points: list[Point]) -> "BoundingBox":
        if not points:
            raise ValueError("אי אפשר לחשב תיבה חוסמת בלי נקודות")
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return BoundingBox(min(xs), min(ys), max(xs), max(ys))

    def merge(self, other: "BoundingBox") -> "BoundingBox":
        return BoundingBox(
            min(self.min_x, other.min_x),
            min(self.min_y, other.min_y),
            max(self.max_x, other.max_x),
            max(self.max_y, other.max_y),
        )


@dataclass
class Contour:
    """מתאר אחד — פוליגון (עקומות כבר עברו דיסקרטיזציה לקטעים)."""

    points: list[Point]
    closed: bool = True
    layer: str = ""

    # מטמון. `points` נקבע ב-`__post_init__` ואינו משתנה אחר כך (נבדק:
    # אין בקוד שום השמה או append ל-`points` מחוץ לבנאי), ולכן שטח,
    # אורך ותיבה חוסמת הם קבועים של האובייקט. בלי זה כל אחד מהם חושב
    # מחדש בכל גישה — ו-`_enclosing` ניגש ל-`area` של כל מתאר, לכל
    # מתאר. `compare=False` כדי ששוויון בין מתארים יישאר על הנקודות.
    _bbox: "BoundingBox | None" = field(default=None, init=False, repr=False, compare=False)
    _area: float | None = field(default=None, init=False, repr=False, compare=False)
    _length: float | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if len(self.points) < 2:
            raise ValueError("מתאר חייב לפחות שתי נקודות")
        # מתאר שנסגר על עצמו — מסירים את הנקודה הכפולה כדי שהחישובים יהיו נקיים
        if len(self.points) > 2 and self._distance(self.points[0], self.points[-1]) <= CLOSURE_TOLERANCE_MM:
            self.points = self.points[:-1]
            self.closed = True

    @staticmethod
    def _distance(a: Point, b: Point) -> float:
        return math.hypot(b[0] - a[0], b[1] - a[1])

    @property
    def signed_area(self) -> float:
        """שטח מסומן (נוסחת שרוך הנעל). חיובי = נגד כיוון השעון."""
        if not self.closed or len(self.points) < 3:
            return 0.0
        total = 0.0
        n = len(self.points)
        for i in range(n):
            x1, y1 = self.points[i]
            x2, y2 = self.points[(i + 1) % n]
            total += x1 * y2 - x2 * y1
        return total / 2.0

    @property
    def area(self) -> float:
        if self._area is None:
            self._area = abs(self.signed_area)
        return self._area

    @property
    def length(self) -> float:
        """אורך המתאר — זה אורך החיתוך בפועל שהלייזר עובר."""
        if self._length is not None:
            return self._length
        total = 0.0
        n = len(self.points)
        last = n if self.closed else n - 1
        for i in range(last):
            total += self._distance(self.points[i], self.points[(i + 1) % n])
        self._length = total
        return total

    @property
    def bbox(self) -> BoundingBox:
        if self._bbox is None:
            self._bbox = BoundingBox.from_points(self.points)
        return self._bbox

    def contains_point(self, point: Point) -> bool:
        """בדיקת ray casting — משמשת לזיהוי חורים בתוך המתאר החיצוני."""
        x, y = point
        inside = False
        n = len(self.points)
        for i in range(n):
            x1, y1 = self.points[i]
            x2, y2 = self.points[(i + 1) % n]
            if (y1 > y) != (y2 > y):
                x_cross = (x2 - x1) * (y - y1) / (y2 - y1) + x1
                if x < x_cross:
                    inside = not inside
        return inside


@dataclass
class PartGeometry:
    """הגיאומטריה המלאה של חלק: מתאר/ים חיצוני/ים + חורים.

    מיון החיצוני מול החורים נעשה כאן ולא בקורא ה-DXF, כדי שקלט ידני
    ומודל תלת-ממד יקבלו בדיוק את אותה לוגיקה.
    """

    contours: list[Contour]
    open_contours: list[Contour] = field(default_factory=list)
    _classified: "tuple[list[Contour], list[Contour]] | None" = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        closed = [c for c in self.contours if c.closed and c.area > 0]
        opened = [c for c in self.contours if not (c.closed and c.area > 0)]
        if not closed:
            raise ValueError("החלק חייב לפחות מתאר סגור אחד כדי להיות בר-ייצור")
        self.contours = closed
        self.open_contours = list(self.open_contours) + opened
        self._classified = None

    @property
    def outer_contours(self) -> list[Contour]:
        """מתארים שאינם מוכלים באף מתאר אחר — הגופים עצמם."""
        return self._classify()[0]

    @property
    def hole_contours(self) -> list[Contour]:
        """מתארים המוכלים בתוך מתאר אחר — חורים."""
        return self._classify()[1]

    def _classify(self) -> tuple[list[Contour], list[Contour]]:
        """(גופים, חורים) — **פעם אחת לכל אובייקט.**

        המיון הוא O(n²) מטבעו: כל מתאר נבדק מול כל מתאר גדול ממנו. עד
        23.8.2026 הוא גם **חושב מחדש בכל גישה לתכונה** — ו-
        `geometry_to_json` ניגש ל-`hole_contours`, `net_area`,
        `gross_area`, `bbox`, `hole_count` ו-`body_count`, כלומר שישה
        מיונים מלאים על אותו אובייקט. **נמדד על הקופסה:** DXF של 54KB
        עם 401 מתארים לקח 21.4 שניות ל-`POST /api/upload`, מתוכן 17
        ב-`geometry_to_json` ועוד 4.7 בשתי גישות ל-`bbox`. זה לא סיכון
        תיאורטי אלא חלק אמיתי — לוח עם 400 חורים.

        המטמון בטוח כאן כי `contours` נקבע ב-`__post_init__` ואינו
        משתנה אחר כך; פיצול ושכפול יוצרים `PartGeometry` חדש.
        """
        if self._classified is None:
            outer: list[Contour] = []
            holes: list[Contour] = []
            for contour in self.contours:
                (holes if self._enclosing(contour) else outer).append(contour)
            self._classified = (outer, holes)
        return self._classified

    def _enclosing(self, contour: Contour) -> Contour | None:
        """המתאר הקטן ביותר שמכיל את הנתון, אם קיים.

        סינון בשני שלבים, וההבדל אינו קוסמטי: `contains_point` הוא ray
        casting על **כל** נקודות המתאר, ומעגל אחרי דיסקרטיזציה הוא
        עשרות עד מאות נקודות. בדיקת התיבה החוסמת היא ארבע השוואות,
        והיא פוסלת כמעט את כולם לפני שנגענו בנקודה אחת.
        """
        probe = contour.points[0]
        px, py = probe
        area = contour.area
        best: Contour | None = None
        best_area = 0.0
        for other in self.contours:
            if other is contour or other.area <= area:
                continue
            box = other.bbox
            if not (box.min_x <= px <= box.max_x and box.min_y <= py <= box.max_y):
                continue
            if best is not None and other.area >= best_area:
                continue
            if other.contains_point(probe):
                best, best_area = other, other.area
        return best

    @property
    def net_area(self) -> float:
        """שטח המתכת בפועל — גופים פחות חורים. זה מה שנצרך מהפלטה."""
        return sum(c.area for c in self.outer_contours) - sum(c.area for c in self.hole_contours)

    @property
    def gross_area(self) -> float:
        """שטח הגופים בלי הפחתת חורים."""
        return sum(c.area for c in self.outer_contours)

    @property
    def cut_length(self) -> float:
        """אורך החיתוך של המתארים הסגורים בלבד.

        **קווים פתוחים אינם כאן, וזו החלטה מ-20.8.2026.** קודם הם נספרו
        כחיתוך בשקט, ואז קו כיפוף אחד על מלבן 250x150 ניפח את אורך
        החיתוך ב-31% — מספר תקין לגמרי שאיש לא יכול לעמוד מאחוריו.
        קו פתוח בשרטוט פח הוא לרוב סימון: קו כיפוף, ציר, או הערה
        גיאומטרית. לפעמים הוא באמת חיתוך (חריץ, שריטה מכוונת).

        המנוע אינו יודע להבחין, ולכן הוא **אינו מנחש**: האורך יושב
        בנפרד ב-`open_length`, ומי שמזמין אומר אם זה חיתוך.
        """
        return sum(c.length for c in self.contours)

    @property
    def open_length(self) -> float:
        """אורך הקווים הפתוחים. חיתוך או סימון — המזמין מכריע."""
        return sum(c.length for c in self.open_contours)

    @property
    def has_open_lines(self) -> bool:
        return bool(self.open_contours)

    @property
    def pierce_count(self) -> int:
        """כל מתאר סגור מחייב ניקוב התחלתי אחד."""
        return len(self.contours)

    @property
    def hole_count(self) -> int:
        return len(self.hole_contours)

    @property
    def body_count(self) -> int:
        return len(self.outer_contours)

    @property
    def bbox(self) -> BoundingBox:
        """התיבה החוסמת של הגופים. נשענת על המיון המְמוּטָּב ב-`_classify`."""
        boxes = [c.bbox for c in self.outer_contours]
        result = boxes[0]
        for box in boxes[1:]:
            result = result.merge(box)
        return result


def rectangle(width: float, height: float, origin: Point = (0.0, 0.0)) -> Contour:
    """מלבן — הבסיס לקלט ידני ולבדיקות."""
    x, y = origin
    return Contour([(x, y), (x + width, y), (x + width, y + height), (x, y + height)])


def circle(radius: float, center: Point = (0.0, 0.0), segments: int = 64) -> Contour:
    """עיגול מקורב לפוליגון. segments גבוה = אורך חיתוך מדויק יותר."""
    if radius <= 0:
        raise ValueError("רדיוס חייב להיות חיובי")
    cx, cy = center
    points = [
        (cx + radius * math.cos(2 * math.pi * i / segments), cy + radius * math.sin(2 * math.pi * i / segments))
        for i in range(segments)
    ]
    return Contour(points)
