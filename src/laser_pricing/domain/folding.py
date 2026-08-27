"""קיפול הפריסה לגוף — מהמישור אל שלושת הממדים.

**זה לא מנוע CAD, וזה בכוונה.** פח מכופף אינו צורה חופשית: הוא אוסף
לוחות **שטוחים** שמתחברים בקווי כיפוף, וזה כל מה שצריך לתאר. כל לוח
נשאר מישורי בדיוק כפי שהוא בפריסה — הכיפוף רק מסובב אותו סביב הציר
שלו — ולכן ההמרה כאן היא **איזומטריה**: היא לא מותחת, לא מקצרת ולא
משנה שום מידה.

והנקודה החשובה: **המודל התלת-ממדי נבנה מאותה פריסה שתומחרה.** אין
כאן גיאומטריה שנייה. השטח, אורך החיתוך ומספר הניקובים חושבו על אותם
מתארים בדיוק, ולכן אין מצב שהלקוח רואה גוף אחד ומשלם על אחר.

**מה שהמודל הזה במפורש אינו מתאר:** רדיוס הכיפוף. פח אמיתי אינו
מתקפל בקו אלא בקשת שרדיוס שלה תלוי בעובי ובכלי של המכונה, ולכן
הפינות כאן חדות. זו הצגה של הגוף, לא שרטוט ייצור — בדיוק כמו
שהפריסה עצמה מחושבת כסכום המקטעים בלי ניכוי כיפוף.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Point2 = tuple[float, float]
Point3 = tuple[float, float, float]


@dataclass(frozen=True)
class FlatPanel:
    """לוח אחד בפריסה, ואיך הוא מתקפל ביחס לאב שלו.

    `fold_axis` הוא קטע **בקואורדינטות הפריסה** — אותן קואורדינטות
    שבהן מתואר המתאר. זו ההחלטה שמאפשרת לבנאים בקטלוג לתאר קיפול
    בלי לדעת דבר על מטריצות: הם כבר מציירים את קו הכיפוף בשביל
    התצוגה הדו-ממדית, וזה אותו קטע.
    """

    outline: list[Point2]
    parent: int = -1
    fold_axis: tuple[float, float, float, float] | None = None
    angle_deg: float = 0.0
    label: str = ""


@dataclass(frozen=True)
class SolidFace:
    """לוח אחרי הקיפול: מתאר תלת-ממדי, חורים, ונורמל."""

    outline: list[Point3]
    holes: list[list[Point3]] = field(default_factory=list)
    normal: Point3 = (0.0, 0.0, 1.0)
    label: str = ""


class Matrix:
    """מטריצת 4×4 מצומצמת — סיבוב והזזה בלבד.

    נכתבה כאן ולא נלקחה מספרייה: זה כל מה שדרוש, והתלות היחידה של
    הפרויקט בעיבוד גיאומטרי היא `ezdxf`. **הקופסה היא שתי ליבות
    המשותפות לחמישה פרויקטים** — הוראת האב — ותלות חדשה נכנסת רק
    כשהיא חוסכת יותר ממה שהיא עולה.
    """

    __slots__ = ("m",)

    def __init__(self, rows: tuple[tuple[float, ...], ...] | None = None) -> None:
        self.m = rows or (
            (1.0, 0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        )

    def __matmul__(self, other: "Matrix") -> "Matrix":
        a, b = self.m, other.m
        return Matrix(
            tuple(
                tuple(sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4))
                for r in range(4)
            )
        )

    def apply(self, point: Point3) -> Point3:
        x, y, z = point
        m = self.m
        return (
            m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3],
            m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3],
            m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3],
        )

    def rotate_only(self, vector: Point3) -> Point3:
        x, y, z = vector
        m = self.m
        return (
            m[0][0] * x + m[0][1] * y + m[0][2] * z,
            m[1][0] * x + m[1][1] * y + m[1][2] * z,
            m[2][0] * x + m[2][1] * y + m[2][2] * z,
        )


def _translation(dx: float, dy: float, dz: float) -> Matrix:
    return Matrix(
        ((1.0, 0.0, 0.0, dx), (0.0, 1.0, 0.0, dy), (0.0, 0.0, 1.0, dz), (0.0, 0.0, 0.0, 1.0))
    )


def _rotation(axis: Point3, degrees: float) -> Matrix:
    """נוסחת רודריגז. הציר חייב להיות מנורמל."""
    x, y, z = axis
    length = math.sqrt(x * x + y * y + z * z)
    if length == 0:
        return Matrix()
    x, y, z = x / length, y / length, z / length
    a = math.radians(degrees)
    c, s, t = math.cos(a), math.sin(a), 1 - math.cos(a)
    return Matrix(
        (
            (t * x * x + c, t * x * y - s * z, t * x * z + s * y, 0.0),
            (t * x * y + s * z, t * y * y + c, t * y * z - s * x, 0.0),
            (t * x * z - s * y, t * y * z + s * x, t * z * z + c, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        )
    )


def _fold_matrix(axis: tuple[float, float, float, float], degrees: float) -> Matrix:
    """סיבוב סביב קו הכיפוף, במקום שבו הוא נמצא בפריסה."""
    x1, y1, x2, y2 = axis
    direction = (x2 - x1, y2 - y1, 0.0)
    return (
        _translation(x1, y1, 0.0)
        @ _rotation(direction, degrees)
        @ _translation(-x1, -y1, 0.0)
    )


def _centroid(points: list[Point2]) -> Point2:
    n = len(points)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


def contains(polygon: list[Point2], point: Point2) -> bool:
    """ray casting. משמש לשיוך חור ללוח שהוא יושב בו."""
    x, y = point
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < cross:
                inside = not inside
    return inside


def _dot(a: Point3, b: Point3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def fold(panels: list[FlatPanel], holes: list[list[Point2]]) -> list[SolidFace]:
    """פריסה + חורים → לוחות מקופלים במרחב.

    **סימן הזווית נקבע במדידה ולא בהנחה.** כיוון קו הכיפוף בפריסה
    תלוי בסדר שבו הבנאי כתב את שתי הנקודות שלו, ואותו כיוון בדיוק
    קובע אם הדופן עולה או יורדת. במקום לדרוש מכל בנאי לזכור מוסכמה
    שתישבר בשקט במוצר הבא, הפונקציה מסובבת, מודדת לאן זז מרכז הלוח,
    ומתקנת.

    **וההשוואה היא מול הנורמל של האב, לא מול "מעלה" של העולם.**
    זו הייתה טעות אמיתית כאן: מדף צף הוא גב, משטח ושפה, והשפה
    מתקפלת מהמשטח — שהוא כבר אנכי. "כלפי מעלה בציר z" המשיך אותה
    עוד 25 מ"מ למעלה במקום להחזיר אותה פנימה, והגוף יצא **105 מ"מ
    עומק במקום 80**. הכיפוף הראשון היה נכון והשני לא, וזה בדיוק
    הזן שבדיקה על מוצר אחד לא תופסת.
    """
    transforms: list[Matrix] = []
    for index, panel in enumerate(panels):
        if panel.parent < 0 or panel.fold_axis is None:
            transforms.append(Matrix())
            continue
        if panel.parent >= index:
            raise ValueError("לוח חייב להתקפל על אב שהוגדר לפניו.")

        parent = transforms[panel.parent]
        parent_normal = parent.rotate_only((0.0, 0.0, 1.0))
        cx, cy = _centroid(panel.outline)
        before = parent.apply((cx, cy, 0.0))

        candidate = parent @ _fold_matrix(panel.fold_axis, panel.angle_deg)
        after = candidate.apply((cx, cy, 0.0))
        moved = (after[0] - before[0], after[1] - before[1], after[2] - before[2])
        if _dot(moved, parent_normal) < 0:
            candidate = parent @ _fold_matrix(panel.fold_axis, -panel.angle_deg)
        transforms.append(candidate)

    faces: list[SolidFace] = []
    for index, panel in enumerate(panels):
        matrix = transforms[index]
        mine = [h for h in holes if contains(panel.outline, _centroid(h))]
        faces.append(
            SolidFace(
                outline=[matrix.apply((x, y, 0.0)) for x, y in panel.outline],
                holes=[[matrix.apply((x, y, 0.0)) for x, y in h] for h in mine],
                normal=matrix.rotate_only((0.0, 0.0, 1.0)),
                label=panel.label,
            )
        )
    return faces


def bounds(faces: list[SolidFace]) -> tuple[Point3, Point3]:
    """תיבה חוסמת של הגוף המקופל — **המידות האמיתיות של המוצר**.

    זה המספר שהלקוח מחפש. הפריסה של מגש 300×200 עם דופן 50 היא
    400×300, וזה נכון למה שנחתך ומתומחר — אבל המגש עצמו הוא
    300×200×50, וזה מה שצריך להיכנס לו לארון.
    """
    xs = [p[0] for f in faces for p in f.outline]
    ys = [p[1] for f in faces for p in f.outline]
    zs = [p[2] for f in faces for p in f.outline]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


__all__ = ["FlatPanel", "SolidFace", "bounds", "contains", "fold"]
