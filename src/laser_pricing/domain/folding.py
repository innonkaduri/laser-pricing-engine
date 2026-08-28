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


ARC_SEGMENTS = 6
"""לכמה מרובעים לפרק את קשת הכיפוף.

שישה מספיקים ל-90°: ההפרש בין הקשת האמיתית למיתר יורד מתחת לחצי
מ"מ ברדיוסים שאנחנו מדברים עליהם, וזה מתחת לרוחב הקו שהדפדפן
מצייר. יותר מזה זה נקודות שנשלחות ואיש אינו רואה.
"""


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
    flip: bool = False
    """להפוך את כיוון הקיפול שהמדידה בחרה.

    `fold` בוחרת סימן שמרים את הלוח ביחס לנורמל של האב, וזו ברירת
    מחדל נכונה לקטלוג: מגש מתקפל למעלה. **בעורך זו לא ברירת מחדל
    אלא הכרעה** — אותו קו בדיוק הוא מגש או גג, ואלה שני מוצרים.
    """

    bend_index: int = -1
    """**איזה כיפוף במסמך יצר את הלוח הזה.**

    בלי המספר הזה אי אפשר ללחוץ על דופן בתלת-ממד ולדעת מה לשנות:
    התווית היא טקסט לבני אדם, ובנייה שגוזרת ממנה מספר נשברת ברגע
    שמישהו מתרגם מסך. **בסוף רשימת השדות ולא באמצעה** — בנאי
    הקטלוג בונה `FlatPanel` לפי מיקום, ושדה שנדחף באמצע הזיז את
    `fold_axis` ל-`parent` והפיל 23 בדיקות בבת אחת.
    """
    bend_gap: float = 0.0
    """רוחב אזור הכיפוף בפריסה — קצבת הכיפוף.

    **אפס פירושו פריסת אפקס**, כלומר לוח שנוגע בקו הכיפוף ופינה
    חדה. אחרי `catalog.deduct` הלוח נגמר בנקודת ההשקה, בין שני
    לוחות סמוכים יש רווח, ובלי המספר הזה `fold` הייתה מציירת בין
    כל שתי דפנות **חריץ** — פגם שנראה כמו חור בחלק.
    """


@dataclass(frozen=True)
class SolidFace:
    """לוח אחרי הקיפול: מתאר תלת-ממדי, חורים, ונורמל."""

    outline: list[Point3]
    holes: list[list[Point3]] = field(default_factory=list)
    normal: Point3 = (0.0, 0.0, 1.0)
    label: str = ""
    bend_index: int = -1
    axis3d: tuple[Point3, Point3] | None = None
    """ציר הכיפוף **במרחב**, אחרי שהאב כבר סובב.

    הידית שגוררים בתלת-ממד צריכה לדעת סביב מה הלוח מסתובב. ציר
    בקואורדינטות הפריסה אינו עונה על זה ברגע שהאב עצמו מוטה.
    """
    """**איזה כיפוף במסמך יצר את הלוח הזה.**

    בלי המספר הזה אי אפשר ללחוץ על דופן בתלת-ממד ולדעת מה לשנות:
    התווית היא טקסט לבני אדם, ובנייה שגוזרת ממנה מספר נשברת ברגע
    שמישהו מתרגם מסך.
    """


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
    signs: list[float] = []
    for index, panel in enumerate(panels):
        if panel.parent < 0 or panel.fold_axis is None:
            transforms.append(Matrix())
            signs.append(1.0)
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
        sign = 1.0
        if _dot(moved, parent_normal) < 0:
            sign = -1.0
        if panel.flip:
            sign = -sign
        if sign < 0:
            candidate = parent @ _fold_matrix(panel.fold_axis, -panel.angle_deg)
        transforms.append(candidate)
        signs.append(sign)

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
                bend_index=panel.bend_index,
                axis3d=(
                    transforms[panel.parent].apply((panel.fold_axis[0], panel.fold_axis[1], 0.0)),
                    transforms[panel.parent].apply((panel.fold_axis[2], panel.fold_axis[3], 0.0)),
                )
                if panel.parent >= 0 and panel.fold_axis is not None
                else None,
            )
        )
        faces.extend(_arc_faces(panel, transforms[panel.parent] if panel.parent >= 0 else None, signs[index]))
    return faces


def _arc_faces(panel: FlatPanel, parent: Matrix | None, sign: float) -> list[SolidFace]:
    """קשת הכיפוף בין הלוח לאב שלו.

    **הקשת נבנית סביב קו הכיפוף ולא בין שני מתארים.** שתי נקודות
    ההשקה יושבות בדיוק במרחק `bend_gap/2` מהקו, ולכן שתיהן על
    מעגל אחד שמרכזו הקו — וסיבוב פשוט מהאחת אל השנייה עובר בדיוק
    דרך שתיהן. ניסיון לחבר אותן במשטח שנמתח בין שני מתארים היה
    נשען על סדר הנקודות שהבנאי כתב, וזה נשבר בשקט.
    """
    if parent is None or panel.fold_axis is None or panel.bend_gap <= 0:
        return []
    origin, normal, length = _axis_frame(panel.fold_axis)
    direction = (-normal[1], normal[0])
    radius = panel.bend_gap / 2.0
    end = math.radians(sign * panel.angle_deg)

    def at(fraction: float) -> list[Point3]:
        angle = math.pi + fraction * (end - math.pi)
        s, z = radius * math.cos(angle), radius * math.sin(angle)
        return [
            parent.apply((origin[0] + normal[0] * s + direction[0] * t,
                          origin[1] + normal[1] * s + direction[1] * t,
                          z))
            for t in (0.0, length)
        ]

    strips: list[SolidFace] = []
    previous = at(0.0)
    for step in range(1, ARC_SEGMENTS + 1):
        current = at(step / ARC_SEGMENTS)
        quad = [previous[0], previous[1], current[1], current[0]]
        # נורמל הפס עצמו, כדי שההצללה תעקוב אחרי העיגול ולא תשטח אותו.
        u = tuple(quad[1][i] - quad[0][i] for i in range(3))
        v = tuple(quad[3][i] - quad[0][i] for i in range(3))
        nrm = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        scale = math.sqrt(sum(c * c for c in nrm)) or 1.0
        strips.append(
            SolidFace(outline=quad, normal=tuple(c / scale for c in nrm), label="קשת")
        )
        previous = current
    return strips


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


__all__ = [
    "DEFAULT_K_FACTOR",
    "BendSpec",
    "FlatPanel",
    "Fold",
    "SolidFace",
    "bend_allowance",
    "bend_deduction",
    "bounds",
    "contains",
    "fold",
    "folds_of",
    "outside_setback",
    "panel_tangents",
    "shift_flat",
]


# ---- ניכוי כיפוף ----
#
# **עד 27.8.2026 הפריסה כאן הייתה סכום המקטעים, בלי ניכוי.** זה נכון
# לתמחור — הוא שוגה לכיוון של *יותר* חומר — ושגוי לחלוטין ברגע
# שמייצאים קובץ לחיתוך: פח שנחתך לפי המידות החיצוניות ומכופף יוצא
# גדול מדי בכל כיוון, כי החומר בקשת הכיפוף נמתח.
#
# הנוסחאות כאן הן התקן: קצבת כיפוף היא אורך הסיב הנייטרלי בקשת,
# הנסיגה היא המרחק מנקודת ההשקה לקודקוד החד, והניכוי הוא ההפרש.


DEFAULT_K_FACTOR = 0.2732
"""מיקום הסיב הנייטרלי כשבר מהעובי. **נמדד, לא נבחר.**

זאמיט נמדדו 27.8.2026 על אותו מגש בדיוק (300×200 דופן 50): פריסה
**396×296** ב-1 מ"מ ו-**388×288** ב-3 מ"מ, מול פריסת אפקס 400×300.
הצבת שתי המדידות בנוסחה עם `radius = thickness` נותנת **אותו** K
בשני העוביים — 0.2732 — ושני עוביים בלתי תלויים שנופלים על קבוע
אחד הם התאמה, לא צירוף מקרים.

הוא גם מסתדר עם כלל האצבע של בתי מלאכה: ניכוי של פעמיים העובי
לכיפוף 90° בפח דק. ‎4−(π/2)(1+0.2732) = 2.000 בדיוק.

**אבל זה עדיין הרדיוס של הכלי של זאמיט ולא של המכבש של ימיש.**
`bend_radius_mm` ו-`k_factor` ניתנים לדריסה לכל שורה בטבלה, וברגע
שיימדד רדיוס הכלי האמיתי — הוא גובר.
"""


@dataclass(frozen=True)
class BendSpec:
    """הפרמטרים שהופכים קו כיפוף לגיאומטריה: רדיוס פנימי וגורם K.

    `radius_mm=None` פירושו "רדיוס שווה לעובי" — ההנחה המקובלת
    לכיפוף אוויר בפח דק, וזו שממנה נמדד `DEFAULT_K_FACTOR`.
    """

    radius_mm: float | None = None
    k_factor: float = DEFAULT_K_FACTOR

    def radius_for(self, thickness: float) -> float:
        return thickness if self.radius_mm is None else self.radius_mm


def bend_allowance(angle_deg: float, radius: float, thickness: float, k: float) -> float:
    """אורך הסיב הנייטרלי בתוך הקשת. זה החומר ש**נשאר** בכיפוף."""
    return math.radians(abs(angle_deg)) * (radius + k * thickness)


def outside_setback(angle_deg: float, radius: float, thickness: float) -> float:
    """מנקודת ההשקה עד הקודקוד החד — כלומר עד המידה החיצונית."""
    return math.tan(math.radians(abs(angle_deg)) / 2.0) * (radius + thickness)


def bend_deduction(angle_deg: float, radius: float, thickness: float, k: float) -> float:
    """כמה לקצר את הפריסה לכל כיפוף.

    שתי הנסיגות הן המרחק שהמידות החיצוניות "מבטיחות" ואינו קיים
    בחומר; קצבת הכיפוף היא מה שכן קיים. ההפרש הוא מה שמורידים.
    """
    ba = bend_allowance(angle_deg, radius, thickness, k)
    return 2.0 * outside_setback(angle_deg, radius, thickness) - ba


def _axis_frame(axis: tuple[float, float, float, float]) -> tuple[Point2, Point2, float]:
    """(נקודה על הציר, נורמל יחידה, אורך הציר)."""
    x1, y1, x2, y2 = axis
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length == 0:
        raise ValueError("קו כיפוף באורך אפס.")
    return (x1, y1), (dy / length, -dx / length), length


def _side(point: Point2, origin: Point2, normal: Point2) -> float:
    return (point[0] - origin[0]) * normal[0] + (point[1] - origin[1]) * normal[1]


def _along(point: Point2, origin: Point2, normal: Point2) -> float:
    """היטל על כיוון הציר עצמו — כדי לדעת אם הנקודה בתוך מוטת הכיפוף."""
    return (point[0] - origin[0]) * (-normal[1]) + (point[1] - origin[1]) * normal[0]


@dataclass(frozen=True)
class Fold:
    """קו כיפוף אחד, מוכן לחישוב: מסגרת, כמה מנכים, וכמה קשת נשארת."""

    origin: Point2
    direction: Point2
    normal: Point2
    """מנורמל, ומצביע **מהאב אל הבן**. הכיוון נקבע במדידה של מרכז
    הלוח ולא מסדר הנקודות שהבנאי כתב — בדיוק כמו סימן הזווית."""
    length: float
    angle_deg: float
    deduction: float
    allowance: float
    setback: float

    def side(self, point: Point2) -> float:
        return _side(point, self.origin, self.normal)

    def spans(self, point: Point2, tol: float = 1e-6) -> bool:
        """האם הנקודה בתוך מוטת הכיפוף.

        **בלי הבדיקה הזאת פינת מגרעת נוסעת עם כיפוף שאינו נוגע בה.**
        זרוע של מגש אינה מתקפלת סביב הצירים האנכיים, וקודקוד שלה
        שנמצא במקרה מעברם היה מוזז פעמיים והפריסה הייתה יוצאת עקומה.
        """
        t = (point[0] - self.origin[0]) * self.direction[0] + (
            point[1] - self.origin[1]
        ) * self.direction[1]
        return -tol <= t <= self.length + tol


def folds_of(panels: list[FlatPanel], thickness: float, bend: BendSpec) -> list[Fold]:
    """הופך את עץ הלוחות לרשימת כיפופים עם המספרים של העובי הזה."""
    radius = bend.radius_for(thickness)
    out: list[Fold] = []
    for panel in panels:
        if panel.parent < 0 or panel.fold_axis is None:
            continue
        origin, normal, length = _axis_frame(panel.fold_axis)
        direction = (-normal[1], normal[0])
        # הנורמל מצביע לאן שהבן יושב — נמדד, לא מונח.
        if _side(_centroid(panel.outline), origin, normal) < 0:
            normal = (-normal[0], -normal[1])
        out.append(
            Fold(
                origin=origin,
                direction=direction,
                normal=normal,
                length=length,
                angle_deg=panel.angle_deg,
                deduction=bend_deduction(panel.angle_deg, radius, thickness, bend.k_factor),
                allowance=bend_allowance(panel.angle_deg, radius, thickness, bend.k_factor),
                setback=outside_setback(panel.angle_deg, radius, thickness),
            )
        )
    return out


def shift_flat(point: Point2, folds: list[Fold], tol: float = 1e-9) -> Point2:
    """נקודה בפריסת האפקס → מקומה בפריסה המנוכה.

    **זה כל הניכוי, בשורה אחת של רעיון:** נקודה שיושבת מעבר לקו
    כיפוף מתקרבת אליו בדיוק בשיעור הניכוי. נקודה שיושבת *על* הקו
    אינה זזה, ולכן הלוח מתקצר במקום להיגרר — וזה ההבדל בין פריסה
    שמתכווצת נכון לבין פריסה שרק מוזזת.
    """
    x, y = point
    for fold in folds:
        if fold.side(point) > tol and fold.spans(point):
            x -= fold.normal[0] * fold.deduction
            y -= fold.normal[1] * fold.deduction
    return (x, y)


def _clip_halfplane(polygon: list[Point2], origin: Point2, normal: Point2, offset: float) -> list[Point2]:
    """סאתרלנד–הודג'מן: משאיר את מה שבצד `(p-origin)·normal <= offset`."""
    if not polygon:
        return []
    out: list[Point2] = []
    n = len(polygon)
    for i in range(n):
        a = polygon[i]
        b = polygon[(i + 1) % n]
        da = _side(a, origin, normal) - offset
        db = _side(b, origin, normal) - offset
        if da <= 0:
            out.append(a)
        if (da > 0) != (db > 0):
            t = da / (da - db)
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def panel_tangents(panels: list[FlatPanel], folds: list[Fold]) -> list[list[Point2]]:
    """מתאר כל לוח, חתוך עד **נקודת ההשקה** של כל כיפוף שנוגע בו.

    לוח אמיתי אינו מגיע עד קו הכיפוף — הוא נגמר שם שהקשת מתחילה.
    בלי החיתוך הזה ההצגה התלת-ממדית מציירת פינה חדה, והמידה
    החיצונית של הגוף יוצאת קטנה מהמוצר שהוזמן.
    """
    by_parent: dict[int, list[Fold]] = {}
    cursor = 0
    for index, panel in enumerate(panels):
        if panel.parent < 0 or panel.fold_axis is None:
            continue
        by_parent.setdefault(panel.parent, []).append(folds[cursor])
        by_parent.setdefault(index, []).append(folds[cursor])
        cursor += 1

    out: list[list[Point2]] = []
    for index, panel in enumerate(panels):
        shape = list(panel.outline)
        for fold in by_parent.get(index, []):
            inward = fold.normal if fold.side(_centroid(panel.outline)) < 0 else (
                -fold.normal[0],
                -fold.normal[1],
            )
            shape = _clip_halfplane(shape, fold.origin, inward, -fold.setback)
        out.append(shape)
    return out
