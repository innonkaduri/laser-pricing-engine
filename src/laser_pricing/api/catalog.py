"""קטלוג מוצרים פרמטרי — מוצר, מידות, ומחיר מהטבלה.

**למה זה קיים.** עד היום המערכת ידעה לענות רק על שאלה אחת: "כמה עולה
*הקובץ הזה*". זו שאלה של מהנדס שכבר שרטט. מי שיודע מה הוא צריך אבל
לא שרטט — ורוב הלקוחות כאלה — לא היה לו על מה ללחוץ. זאמיט פתרו את
זה בקטלוג של מוצרים עם מחוונים, וקנו לשם כך את DriveWorks.

**מה שנבנה כאן אינו קונפיגורטור CAD.** המוצר בוחר **בנאי** — פונקציה
שמקבלת מידות ומחזירה בדיוק את אותו `ManualPartSpec` שמישהו היה מקליד
ביד. כלומר: הקטלוג הוא **נתונים שנוחתים על קלט המנוע הקיים**, ולא
מסלול תמחור שני. אין דרך שמוצר מהקטלוג יתומחר אחרת מחלק שהוקלד, כי
זה אותו קוד בדיוק משורת `PartGeometry` והלאה.

**המחיר לעולם לא נולד כאן.** אין בקובץ הזה שקל אחד. הוא מייצר
גיאומטריה; המחיר מגיע מ-`price_order` ומהטבלה, וצירוף שאינו בטבלה
מחזיר 422 כמו תמיד.

**גבולות המידות אינם קישוט.** מחוון שמאפשר חור בקוטר 80 מ"מ בפלטה
ברוחב 60 מייצר 400 מהמנוע — שגיאה נכונה, ומסך שבור. לכן כל בנאי
בודק גם את הקשרים *בין* הפרמטרים, ולא רק את הטווח של כל אחד לחוד.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.folding import FlatPanel

CATALOG_PATH = Path(__file__).resolve().parents[3] / "config" / "catalog.json"
"""הקטלוג יושב **בעץ ה-git ולא ב-`/var/lib/laser`**, בכוונה.

`tariff.json` נמצא שם כי אבא עורך אותו מהטלפון והוא בלתי ניתן
לשחזור. הקטלוג הוא ההפך: הוא חלק מהמוצר, ינון עורך אותו בקומיט,
וגרסה שלו היא בדיוק מה ש-`git log` צריך לזכור.
"""

EDGE_CLEARANCE_MM = 2.0
"""המרחק המינימלי בין חור לקצה, ובין חור לחור.

זו אינה החמרה שרירותית: פס דק מדי בין חור לשפה נקרע בכיפוף ומתעוות
בחיתוך. 2 מ"מ הוא המינימום שאפשר לבטוח בו בפח דק; בפח עבה הכלל
המקצועי גדול יותר, והמספר כאן נשאר שמרני-לרעה כדי שהמחוון לא ייצור
חלק שאי אפשר לייצר.
"""


class CatalogError(ValueError):
    """מידה שאינה בטווח, או צירוף מידות שאי אפשר לייצר."""


@dataclass(frozen=True)
class Param:
    """מחוון אחד. `min`/`max` הם מה שהמסך מצייר **ומה שהשרת אוכף.**"""

    key: str
    name: str
    unit: str
    min: float
    max: float
    step: float
    default: float
    help: str = ""

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "unit": self.unit,
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "default": self.default,
            "help": self.help,
        }


@dataclass(frozen=True)
class Blank:
    """הפריסה השטוחה — מה שנחתך בפועל, לפני שמכופפים אותו.

    `fold_lines` אינן נחתכות ואינן משפיעות על אורך החיתוך; הן קיימות
    כדי שהתצוגה תצייר את קו הכיפוף. **חלק שמצייר את עצמו בלי לסמן
    איפה הוא מתקפל נראה כמו פלטה שטוחה שמחירה גבוה בלי סיבה.**
    """

    spec: dict
    bend_count: int = 0
    bend_length_mm: float = 0.0
    fold_lines: list[list[float]] = field(default_factory=list)
    panels: list[FlatPanel] = field(default_factory=list)
    """פירוק הפריסה ללוחות, בשביל ההצגה התלת-ממדית.

    **ריק פירושו "לוח אחד שטוח"** ולא "אין מודל": פלטה, מסגרת
    ומשרבייה הן גוף תלת-ממדי לכל דבר — לוח אחד בעובי החומר. רק חלק
    מכופף צריך לומר איזה לוח מתקפל סביב איזה ציר, ולכן רק הוא ממלא
    את השדה.

    השדה חי לצד `fold_lines` ואינו מחליף אותו: הקווים המקווקווים
    בתצוגה הדו-ממדית הם עדיין הדבר הנכון להראות על **פריסה**, וזו
    התצוגה שבית המלאכה חותך לפיה.
    """


@dataclass(frozen=True)
class Product:
    id: str
    category: str
    name: str
    summary: str
    description: str
    builder: str
    params: list[Param]
    bends: bool

    def defaults(self) -> dict[str, float]:
        return {p.key: p.default for p in self.params}


@dataclass(frozen=True)
class Category:
    key: str
    name: str
    summary: str


@dataclass(frozen=True)
class Catalog:
    version: int
    categories: list[Category]
    products: list[Product]

    def get(self, product_id: str) -> Product | None:
        for product in self.products:
            if product.id == product_id:
                return product
        return None


# ---- בנאים ----
#
# כל בנאי מקבל מידות בדוקות-טווח ומחזיר `Blank`. הבדיקות כאן הן
# **בין** הפרמטרים: הטווח של כל מחוון לחוד כבר נאכף לפני הקריאה.


def _hole(diameter: float, x: float, y: float) -> dict:
    return {"diameter_mm": round(diameter, 4), "x_mm": round(x, 4), "y_mm": round(y, 4)}


def _demand(condition: bool, message: str) -> None:
    if not condition:
        raise CatalogError(message)


def _rect_plate(v: dict[str, float]) -> Blank:
    """מלבן עם ארבעה חורי עיגון בפינות."""
    width, height = v["width_mm"], v["height_mm"]
    dia, inset = v["hole_dia_mm"], v["hole_inset_mm"]

    _demand(
        inset >= dia / 2 + EDGE_CLEARANCE_MM,
        f'מרחק החור מהשפה ({inset:g} מ"מ) קטן מדי לחור בקוטר {dia:g} מ"מ. '
        f'נדרש לפחות {dia / 2 + EDGE_CLEARANCE_MM:g} מ"מ.',
    )
    _demand(
        min(width, height) >= 2 * inset + dia + EDGE_CLEARANCE_MM,
        f'הפלטה קטנה מכדי להכיל ארבעה חורים בקוטר {dia:g} מ"מ במרחק {inset:g} מ"מ מהשפה.',
    )

    holes = [
        _hole(dia, x, y)
        for x in (inset, width - inset)
        for y in (inset, height - inset)
    ]
    return Blank(spec={"shape": "rect", "width_mm": width, "height_mm": height, "holes": holes})


def _name_plate(v: dict[str, float]) -> Blank:
    """לוחית עם שני חורי תלייה על ציר האורך."""
    width, height = v["width_mm"], v["height_mm"]
    dia, inset = v["hole_dia_mm"], v["hole_inset_mm"]

    _demand(
        inset >= dia / 2 + EDGE_CLEARANCE_MM,
        f'מרחק החור מהשפה ({inset:g} מ"מ) קטן מדי לחור בקוטר {dia:g} מ"מ.',
    )
    _demand(
        height >= dia + 2 * EDGE_CLEARANCE_MM,
        f'גובה הלוחית ({height:g} מ"מ) קטן מדי לחור בקוטר {dia:g} מ"מ.',
    )
    _demand(
        width >= 2 * inset + dia + EDGE_CLEARANCE_MM,
        f'רוחב הלוחית ({width:g} מ"מ) קטן מכדי להכיל שני חורים במרחק {inset:g} מ"מ מהשפה.',
    )

    holes = [_hole(dia, inset, height / 2), _hole(dia, width - inset, height / 2)]
    return Blank(spec={"shape": "rect", "width_mm": width, "height_mm": height, "holes": holes})


def _disc_flange(v: dict[str, float]) -> Blank:
    """דיסקית עם קדח מרכזי וחורי ברגים על מעגל."""
    outer, bore = v["outer_dia_mm"], v["bore_dia_mm"]
    count, bolt, bcd = int(v["bolt_count"]), v["bolt_dia_mm"], v["bolt_circle_dia_mm"]

    radius = outer / 2.0
    _demand(bore + 2 * EDGE_CLEARANCE_MM < outer, "הקדח המרכזי גדול כמעט כמו הדיסקית.")
    _demand(
        bcd / 2 + bolt / 2 + EDGE_CLEARANCE_MM <= radius,
        f'מעגל הברגים ({bcd:g} מ"מ) מוציא את החורים מהדיסקית. '
        f'המקסימום לקוטר חיצוני {outer:g} וחור {bolt:g} הוא {2 * (radius - bolt / 2 - EDGE_CLEARANCE_MM):g} מ"מ.',
    )
    _demand(
        bcd / 2 - bolt / 2 >= bore / 2 + EDGE_CLEARANCE_MM,
        f'מעגל הברגים ({bcd:g} מ"מ) חופף לקדח המרכזי ({bore:g} מ"מ).',
    )
    # מרחק המיתר בין שני חורי ברגים סמוכים.
    pitch = 2 * (bcd / 2) * math.sin(math.pi / count)
    _demand(
        pitch >= bolt + EDGE_CLEARANCE_MM,
        f'{count} ברגים בקוטר {bolt:g} מ"מ צפופים מדי על מעגל {bcd:g} מ"מ — '
        f'המרווח ביניהם {pitch:.1f} מ"מ.',
    )

    center = radius  # המנוע ממקם עיגול כך שה-bbox מתחיל ב-(0,0)
    holes = [_hole(bore, center, center)]
    for index in range(count):
        angle = 2 * math.pi * index / count
        holes.append(
            _hole(
                bolt,
                center + (bcd / 2) * math.cos(angle),
                center + (bcd / 2) * math.sin(angle),
            )
        )
    return Blank(spec={"shape": "circle", "diameter_mm": outer, "holes": holes})


def _square_flange(v: dict[str, float]) -> Blank:
    """אוגן מרובע: קדח מרכזי וארבעה ברגים בפינות."""
    side, bore = v["side_mm"], v["bore_dia_mm"]
    bolt, inset = v["bolt_dia_mm"], v["bolt_inset_mm"]

    _demand(
        inset >= bolt / 2 + EDGE_CLEARANCE_MM,
        f'מרחק הבורג מהשפה ({inset:g} מ"מ) קטן מדי לחור בקוטר {bolt:g} מ"מ.',
    )
    _demand(
        bore + 2 * EDGE_CLEARANCE_MM < side,
        f'הקדח ({bore:g} מ"מ) גדול כמעט כמו האוגן ({side:g} מ"מ).',
    )
    # מרחק פינה-למרכז מול רדיוס הקדח.
    corner = math.hypot(side / 2 - inset, side / 2 - inset)
    _demand(
        corner - bolt / 2 >= bore / 2 + EDGE_CLEARANCE_MM,
        f'הברגים בפינות חופפים לקדח המרכזי ({bore:g} מ"מ).',
    )

    holes = [_hole(bore, side / 2, side / 2)]
    holes += [
        _hole(bolt, x, y) for x in (inset, side - inset) for y in (inset, side - inset)
    ]
    return Blank(spec={"shape": "rect", "width_mm": side, "height_mm": side, "holes": holes})


def _perforated_strip(v: dict[str, float]) -> Blank:
    """פס עם שורת חורים במרווחים שווים."""
    length, width = v["length_mm"], v["width_mm"]
    count, dia, margin = int(v["hole_count"]), v["hole_dia_mm"], v["end_margin_mm"]

    _demand(
        width >= dia + 2 * EDGE_CLEARANCE_MM,
        f'רוחב הפס ({width:g} מ"מ) קטן מדי לחור בקוטר {dia:g} מ"מ.',
    )
    _demand(
        margin >= dia / 2 + EDGE_CLEARANCE_MM,
        f'שוליים של {margin:g} מ"מ קטנים מדי לחור בקוטר {dia:g} מ"מ.',
    )
    span = length - 2 * margin
    _demand(span > 0, f'השוליים ({margin:g} מ"מ מכל צד) גדולים מאורך הפס.')
    if count > 1:
        pitch = span / (count - 1)
        _demand(
            pitch >= dia + EDGE_CLEARANCE_MM,
            f'{count} חורים בקוטר {dia:g} מ"מ צפופים מדי לאורך {length:g} מ"מ — '
            f'המרווח ביניהם {pitch:.1f} מ"מ.',
        )

    if count == 1:
        centers = [length / 2]
    else:
        centers = [margin + span * i / (count - 1) for i in range(count)]
    holes = [_hole(dia, x, width / 2) for x in centers]
    return Blank(spec={"shape": "rect", "width_mm": length, "height_mm": width, "holes": holes})


def _angle_bracket(v: dict[str, float]) -> Blank:
    """זווית 90°: פריסה שטוחה באורך שתי השוקיים, וכיפוף אחד.

    **הפריסה אינה a+b המדויק** — פח מתקפל סביב הסיב הנייטרלי ומאבד
    מהאורך. הניכוי אינו מחושב כאן מפני שהוא תלוי בעובי וברדיוס הכלי
    של המכונה, ומספר שהומצא כאן היה נראה בדיוק כמו מספר שנמדד. a+b
    הוא הצד הבטוח: הוא **מוסיף** חומר, ולכן טועה לטובת הלקוח בייצור
    ולא לרעתו.
    """
    leg_a, leg_b = v["leg_a_mm"], v["leg_b_mm"]
    width, dia = v["width_mm"], v["hole_dia_mm"]
    per_leg = int(v["holes_per_leg"])

    _demand(
        width >= dia + 2 * EDGE_CLEARANCE_MM,
        f'רוחב הזווית ({width:g} מ"מ) קטן מדי לחור בקוטר {dia:g} מ"מ.',
    )
    holes: list[dict] = []
    for start, leg in ((0.0, leg_a), (leg_a, leg_b)):
        _demand(
            leg >= per_leg * (dia + EDGE_CLEARANCE_MM) + EDGE_CLEARANCE_MM,
            f'שוק באורך {leg:g} מ"מ צרה מדי ל-{per_leg} חורים בקוטר {dia:g} מ"מ.',
        )
        for index in range(per_leg):
            offset = leg * (index + 1) / (per_leg + 1)
            holes.append(_hole(dia, start + offset, width / 2))

    axis = (leg_a, 0.0, leg_a, width)
    return Blank(
        spec={"shape": "rect", "width_mm": leg_a + leg_b, "height_mm": width, "holes": holes},
        bend_count=1,
        bend_length_mm=width,
        fold_lines=[list(axis)],
        panels=[
            FlatPanel([(0.0, 0.0), (leg_a, 0.0), (leg_a, width), (0.0, width)], label="שוק א׳"),
            FlatPanel(
                [(leg_a, 0.0), (leg_a + leg_b, 0.0), (leg_a + leg_b, width), (leg_a, width)],
                0, axis, 90, "שוק ב׳",
            ),
        ],
    )


def _u_channel(v: dict[str, float]) -> Blank:
    """תעלת U: פריסה של שתי דפנות ובסיס, ושני כיפופים.

    אותו כלל כמו בזווית — הפריסה היא סכום המקטעים, בלי ניכוי כיפוף
    שאיש כאן לא מדד.
    """
    base, leg, length = v["base_mm"], v["leg_mm"], v["length_mm"]
    dia, per_side = v["hole_dia_mm"], int(v["holes_per_side"])

    flat = base + 2 * leg
    _demand(
        leg >= dia + 2 * EDGE_CLEARANCE_MM,
        f'דופן בגובה {leg:g} מ"מ נמוכה מדי לחור בקוטר {dia:g} מ"מ.',
    )
    _demand(
        length >= per_side * (dia + EDGE_CLEARANCE_MM) + EDGE_CLEARANCE_MM,
        f'אורך {length:g} מ"מ קצר מדי ל-{per_side} חורים בקוטר {dia:g} מ"מ.',
    )

    holes: list[dict] = []
    for center in (leg / 2, flat - leg / 2):  # מרכז כל דופן בפריסה
        for index in range(per_side):
            offset = length * (index + 1) / (per_side + 1)
            holes.append(_hole(dia, center, offset))

    fold_a = (leg, 0.0, leg, length)
    fold_b = (flat - leg, 0.0, flat - leg, length)
    return Blank(
        # הפריסה רחבה כאורך התעלה וגבוהה כסכום המקטעים.
        spec={"shape": "rect", "width_mm": flat, "height_mm": length, "holes": holes},
        bend_count=2,
        bend_length_mm=2 * length,
        fold_lines=[list(fold_a), list(fold_b)],
        panels=[
            FlatPanel([(leg, 0.0), (flat - leg, 0.0), (flat - leg, length), (leg, length)], label="בסיס"),
            FlatPanel([(0.0, 0.0), (leg, 0.0), (leg, length), (0.0, length)], 0, fold_a, 90, "דופן"),
            FlatPanel(
                [(flat - leg, 0.0), (flat, 0.0), (flat, length), (flat - leg, length)],
                0, fold_b, 90, "דופן",
            ),
        ],
    )


# ---- פח מכופף: הפריסה היא צלב, והמגרעות הן מה שמאפשר לקפל ----
#
# **הפריסה אינה סכום המקטעים המדויק.** פח מתקפל סביב הסיב הנייטרלי
# ומאבד מהאורך, והניכוי תלוי בעובי וברדיוס הכלי של המכונה. מספר
# שהומצא כאן היה נראה בדיוק כמו מספר שנמדד, ולכן הפריסה היא הסכום
# הפשוט: הוא **מוסיף** חומר, כלומר טועה לטובת מי שמייצר ולא לרעתו.


def _tray_outline(width: float, depth: float, wall: float, four_sides: bool) -> list[tuple]:
    """מתאר הפריסה של מגש. ארבע דפנות = צלב; שלוש = צלב בלי הדופן העליונה.

    המגרעות בפינות אינן קישוט: בלי להסיר אותן שתי דפנות סמוכות היו
    חופפות באותו חומר בזווית, והחלק פשוט אינו מתקפל.
    """
    w, d, t = width, depth, wall
    if four_sides:
        return [
            (t, 0.0), (t + w, 0.0), (t + w, t), (w + 2 * t, t),
            (w + 2 * t, t + d), (t + w, t + d), (t + w, d + 2 * t), (t, d + 2 * t),
            (t, t + d), (0.0, t + d), (0.0, t), (t, t),
        ]
    return [
        (t, 0.0), (t + w, 0.0), (t + w, t), (w + 2 * t, t),
        (w + 2 * t, t + d), (0.0, t + d), (0.0, t), (t, t),
    ]


def _tray(v: dict[str, float], four_sides: bool) -> Blank:
    width, depth, wall = v["width_mm"], v["depth_mm"], v["wall_mm"]
    _demand(
        wall * 2 < min(width, depth),
        f'דופן בגובה {wall:g} מ"מ גבוהה מדי לבסיס {width:g}×{depth:g} מ"מ.',
    )
    outline = _tray_outline(width, depth, wall, four_sides)
    folds = [
        [wall, wall, wall + width, wall],
        [wall, wall, wall, wall + depth],
        [wall + width, wall, wall + width, wall + depth],
    ]
    bends, bend_length = 3, width + 2 * depth
    if four_sides:
        folds.append([wall, wall + depth, wall + width, wall + depth])
        bends, bend_length = 4, 2 * width + 2 * depth

    t, w, d = wall, width, depth
    base = [(t, t), (t + w, t), (t + w, t + d), (t, t + d)]
    panels = [FlatPanel(outline=base, label="בסיס")]
    # דופן תחתונה, ימנית, שמאלית — ובארבע דפנות גם העליונה.
    panels.append(FlatPanel([(t, 0.0), (t + w, 0.0), (t + w, t), (t, t)], 0, tuple(folds[0]), 90, "דופן"))
    panels.append(FlatPanel([(0.0, t), (t, t), (t, t + d), (0.0, t + d)], 0, tuple(folds[1]), 90, "דופן"))
    panels.append(
        FlatPanel([(t + w, t), (w + 2 * t, t), (w + 2 * t, t + d), (t + w, t + d)], 0, tuple(folds[2]), 90, "דופן")
    )
    if four_sides:
        panels.append(
            FlatPanel([(t, t + d), (t + w, t + d), (t + w, d + 2 * t), (t, d + 2 * t)], 0, tuple(folds[3]), 90, "דופן")
        )
    return Blank(
        spec={"shape": "polygon", "points": outline},
        bend_count=bends,
        bend_length_mm=bend_length,
        fold_lines=folds,
        panels=panels,
    )


def _tray_3_sides(v: dict[str, float]) -> Blank:
    """מגש פתוח בצד אחד — מדף, מגירה, תעלת כבלים."""
    return _tray(v, four_sides=False)


def _tray_4_sides(v: dict[str, float]) -> Blank:
    """קופסה פתוחה מלמעלה: בסיס וארבע דפנות."""
    return _tray(v, four_sides=True)


def _enclosure(v: dict[str, float]) -> Blank:
    """מארז: מגש ארבע דפנות עם חורי כניסה לכבלים בבסיס."""
    blank = _tray(v, four_sides=True)
    width, depth, wall = v["width_mm"], v["depth_mm"], v["wall_mm"]
    dia, count = v["gland_dia_mm"], int(v["gland_count"])

    _demand(
        depth >= dia + 2 * EDGE_CLEARANCE_MM,
        f'עומק הבסיס ({depth:g} מ"מ) קטן מדי לחור בקוטר {dia:g} מ"מ.',
    )
    span = width / (count + 1)
    _demand(
        span >= dia + EDGE_CLEARANCE_MM,
        f'{count} חורים בקוטר {dia:g} מ"מ צפופים מדי לרוחב {width:g} מ"מ.',
    )
    # החורים בבסיס בלבד — הבסיס מתחיל ב-(wall, wall) בפריסה.
    holes = [
        _hole(dia, wall + width * (i + 1) / (count + 1), wall + depth / 2)
        for i in range(count)
    ]
    return Blank(
        spec=blank.spec | {"holes": holes},
        bend_count=blank.bend_count,
        bend_length_mm=blank.bend_length_mm,
        fold_lines=blank.fold_lines,
        panels=blank.panels,
    )


def _floating_shelf(v: dict[str, float]) -> Blank:
    """מדף צף: גב לתלייה, משטח, ושפה קדמית. שני כיפופים."""
    width, depth = v["width_mm"], v["depth_mm"]
    back, lip = v["back_mm"], v["lip_mm"]
    dia, count = v["hole_dia_mm"], int(v["hole_count"])

    flat = back + depth + lip
    _demand(
        back >= dia + 2 * EDGE_CLEARANCE_MM,
        f'גובה הגב ({back:g} מ"מ) קטן מדי לחור תלייה בקוטר {dia:g} מ"מ.',
    )
    span = width / (count + 1)
    _demand(
        span >= dia + EDGE_CLEARANCE_MM,
        f'{count} חורי תלייה בקוטר {dia:g} מ"מ צפופים מדי לרוחב {width:g} מ"מ.',
    )
    holes = [
        _hole(dia, width * (i + 1) / (count + 1), back / 2) for i in range(count)
    ]
    fold_a = (0.0, back, width, back)
    fold_b = (0.0, back + depth, width, back + depth)
    return Blank(
        spec={"shape": "rect", "width_mm": width, "height_mm": flat, "holes": holes},
        bend_count=2,
        bend_length_mm=2 * width,
        fold_lines=[list(fold_a), list(fold_b)],
        # הגב הוא השורש: הוא נצמד לקיר, וממנו יוצא המשטח ואז השפה.
        panels=[
            FlatPanel([(0.0, 0.0), (width, 0.0), (width, back), (0.0, back)], label="גב"),
            FlatPanel(
                [(0.0, back), (width, back), (width, back + depth), (0.0, back + depth)],
                0, fold_a, 90, "משטח",
            ),
            FlatPanel(
                [(0.0, back + depth), (width, back + depth), (width, flat), (0.0, flat)],
                1, fold_b, 90, "שפה",
            ),
        ],
    )


def _rect_frame(v: dict[str, float]) -> Blank:
    """מסגרת: מלבן עם מלבן שנחתך מתוכו.

    **החיתוך הפנימי אינו עיגול, וזה כל החידוש כאן.** עד 27.8.2026
    "חור" בקלט הידני היה עיגול בלבד, ולכן מסגרת — המוצר הבסיסי ביותר
    בחיתוך לייזר אחרי הפלטה — לא הייתה ניתנת להזמנה.
    """
    width, height, border = v["width_mm"], v["height_mm"], v["border_mm"]
    _demand(
        2 * border + 20 <= min(width, height),
        f'מסגרת ברוחב {border:g} מ"מ אינה משאירה פתח במלבן {width:g}×{height:g} מ"מ. '
        f'המקסימום כאן הוא {(min(width, height) - 20) / 2:g} מ"מ.',
    )
    inner = [
        (border, border),
        (width - border, border),
        (width - border, height - border),
        (border, height - border),
    ]
    return Blank(
        spec={"shape": "rect", "width_mm": width, "height_mm": height, "cutouts": [inner]}
    )


# ---- לוחות מנוקבים: משרבייה, גדר, חיפוי ----


def _grid_centers(size: float, count: int, margin: float, opening: float, axis: str) -> list[float]:
    """מרכזי הפתחים על ציר אחד, ובדיקה שהם לא נוגעים זה בזה."""
    _demand(
        margin >= opening / 2 + EDGE_CLEARANCE_MM,
        f'שוליים של {margin:g} מ"מ קטנים מדי לפתח של {opening:g} מ"מ.',
    )
    if count == 1:
        return [size / 2]
    span = size - 2 * margin
    _demand(span > 0, f'השוליים גדולים מהלוח בציר ה{axis}.')
    pitch = span / (count - 1)
    _demand(
        pitch >= opening + EDGE_CLEARANCE_MM,
        f'{count} פתחים בציר ה{axis} צפופים מדי על {size:g} מ"מ — '
        f'המרווח ביניהם {pitch:.1f} מ"מ, והפתח {opening:g} מ"מ.',
    )
    return [margin + span * i / (count - 1) for i in range(count)]


def _panel_grid(v: dict[str, float], opening_w: float, opening_h: float):
    width, height, margin = v["width_mm"], v["height_mm"], v["margin_mm"]
    cols, rows = int(v["cols"]), int(v["rows"])
    xs = _grid_centers(width, cols, margin, opening_w, "רוחב")
    ys = _grid_centers(height, rows, margin, opening_h, "גובה")
    return width, height, xs, ys


def _panel_round_grid(v: dict[str, float]) -> Blank:
    """לוח מנוקב בעיגולים — משרבייה, גדר, חיפוי מאוורר."""
    dia = v["opening_mm"]
    width, height, xs, ys = _panel_grid(v, dia, dia)
    holes = [_hole(dia, x, y) for x in xs for y in ys]
    return Blank(
        spec={"shape": "rect", "width_mm": width, "height_mm": height, "holes": holes}
    )


def _panel_square_grid(v: dict[str, float]) -> Blank:
    """לוח מנוקב בריבועים."""
    side = v["opening_mm"]
    width, height, xs, ys = _panel_grid(v, side, side)
    half = side / 2
    cutouts = [
        [(x - half, y - half), (x + half, y - half), (x + half, y + half), (x - half, y + half)]
        for x in xs
        for y in ys
    ]
    return Blank(
        spec={"shape": "rect", "width_mm": width, "height_mm": height, "cutouts": cutouts}
    )


def _panel_slot_grid(v: dict[str, float]) -> Blank:
    """לוח מנוקב בחריצים אנכיים — הדגם שנראה כמו סורג."""
    slot_w, slot_h = v["opening_mm"], v["slot_height_mm"]
    width, height, xs, ys = _panel_grid(v, slot_w, slot_h)
    hw, hh = slot_w / 2, slot_h / 2
    cutouts = [
        [(x - hw, y - hh), (x + hw, y - hh), (x + hw, y + hh), (x - hw, y + hh)]
        for x in xs
        for y in ys
    ]
    return Blank(
        spec={"shape": "rect", "width_mm": width, "height_mm": height, "cutouts": cutouts}
    )


BUILDERS = {
    "rect_plate": _rect_plate,
    "name_plate": _name_plate,
    "disc_flange": _disc_flange,
    "square_flange": _square_flange,
    "perforated_strip": _perforated_strip,
    "angle_bracket": _angle_bracket,
    "u_channel": _u_channel,
    "tray_3_sides": _tray_3_sides,
    "tray_4_sides": _tray_4_sides,
    "enclosure": _enclosure,
    "floating_shelf": _floating_shelf,
    "rect_frame": _rect_frame,
    "panel_round_grid": _panel_round_grid,
    "panel_square_grid": _panel_square_grid,
    "panel_slot_grid": _panel_slot_grid,
}
"""**רשימה סגורה.** הקטלוג בוחר בנאי בשם, ולא מתאר חישוב.

זו ההחלטה שמונעת מהקטלוג להפוך לשפת תכנות: קובץ JSON שמכיל נוסחאות
היה דורש מפרש, ומפרש שמריץ מה שכתוב בקובץ הוא בדיוק הדבר שאסור
לחשוף לרשת. כאן הקובץ בוחר מבין פונקציות בדוקות ומספק להן מספרים —
ותו לא.

**ובנאי אחד משרת כמה מוצרים.** "גדר משרבייה" ו"משרבייה" הן אותה
פונקציה עם גבולות אחרים, ו"פרופיל ישר" הוא הפס המחורר בטווח אחר.
זה מה שהופך הרחבת קטלוג לעריכת JSON ולא לכתיבת קוד.
"""


# ---- טעינה ----


def _param_from_json(raw: dict) -> Param:
    return Param(
        key=str(raw["key"]),
        name=str(raw["name"]),
        unit=str(raw.get("unit", "")),
        min=float(raw["min"]),
        max=float(raw["max"]),
        step=float(raw.get("step", 1)),
        default=float(raw["default"]),
        help=str(raw.get("help", "")),
    )


def load(path: Path | None = None) -> Catalog:
    """קורא ומאמת. **נכשל בקול** — קטלוג שבור אינו קטלוג ריק.

    ההבדל אינו סגנוני: קטלוג ריק נראה במסך כמו "עדיין לא הוספנו
    מוצרים" ומזמין להמתין, בזמן שהסיבה האמיתית היא שגיאת הקלדה
    בקובץ. ההפעלה נופלת, הפריסה נעצרת, ומי שערך את הקובץ רואה את
    השורה שהוא שבר.
    """
    source = path or CATALOG_PATH
    raw = json.loads(source.read_text(encoding="utf-8"))

    categories = [
        Category(key=str(c["key"]), name=str(c["name"]), summary=str(c.get("summary", "")))
        for c in raw.get("categories", [])
    ]
    known = {c.key for c in categories}

    products: list[Product] = []
    seen: set[str] = set()
    for item in raw.get("products", []):
        product_id = str(item["id"])
        if product_id in seen:
            raise CatalogError(f'מזהה מוצר כפול בקטלוג: "{product_id}".')
        seen.add(product_id)

        builder = str(item["builder"])
        if builder not in BUILDERS:
            raise CatalogError(
                f'המוצר "{product_id}" מבקש בנאי "{builder}" שאינו קיים. '
                f"הבנאים הקיימים: {', '.join(sorted(BUILDERS))}."
            )
        category = str(item["category"])
        if category not in known:
            raise CatalogError(f'המוצר "{product_id}" משויך לקטגוריה "{category}" שאינה מוגדרת.')

        params = [_param_from_json(p) for p in item["params"]]
        product = Product(
            id=product_id,
            category=category,
            name=str(item["name"]),
            summary=str(item.get("summary", "")),
            description=str(item.get("description", "")),
            builder=builder,
            params=params,
            bends=bool(item.get("bends", False)),
        )
        _self_check(product)
        products.append(product)

    return Catalog(version=int(raw.get("version", 1)), categories=categories, products=products)


def _self_check(product: Product) -> None:
    """ברירות המחדל של כל מוצר חייבות לייצר חלק חוקי — בזמן טעינה.

    **זו הבדיקה שמונעת את הבאג שכבר קרה כאן פעם אחת בצורה אחרת:**
    מסך שנפתח על ערכי ברירת מחדל שנופלים מיד. אם המחוונים נפתחים על
    צירוף בלתי אפשרי, השירות לא יעלה — במקום שהמבקר הראשון יגלה את
    זה במקומנו.
    """
    for param in product.params:
        if not param.min <= param.default <= param.max:
            raise CatalogError(
                f'"{product.id}": ברירת המחדל של {param.key} ({param.default:g}) '
                f"מחוץ לטווח {param.min:g}–{param.max:g}."
            )
        if param.min > param.max:
            raise CatalogError(f'"{product.id}": {param.key} — מינימום גדול ממקסימום.')
    try:
        build(product, product.defaults())
    except CatalogError as exc:
        raise CatalogError(f'"{product.id}": ברירות המחדל אינן מייצרות חלק חוקי — {exc}') from exc


# ---- בנייה ----


def resolve(product: Product, values: dict) -> dict[str, float]:
    """מנקה ואוכף את הטווח. **חסר → ברירת מחדל; מחוץ לטווח → שגיאה.**

    השרת אוכף את אותם גבולות שהמחוון מצייר, כי מחוון אינו הגנה: הוא
    HTML שאפשר לערוך בשתי לחיצות, וקריאת API לא רואה אותו כלל.
    """
    clean: dict[str, float] = {}
    for param in product.params:
        if param.key not in values or values[param.key] is None:
            clean[param.key] = param.default
            continue
        try:
            number = float(values[param.key])
        except (TypeError, ValueError):
            raise CatalogError(f'{param.name}: "{values[param.key]}" אינו מספר.') from None
        if not math.isfinite(number):
            raise CatalogError(f"{param.name}: מספר לא חוקי.")
        if number < param.min or number > param.max:
            unit = f" {param.unit}" if param.unit else ""
            raise CatalogError(
                f"{param.name} חייב להיות בין {param.min:g} ל-{param.max:g}{unit}. "
                f"התקבל {number:g}."
            )
        clean[param.key] = number
    return clean


def build(product: Product, values: dict) -> Blank:
    """מידות → פריסה שטוחה. הערכים מנוקים כאן, לא אצל הקורא."""
    return BUILDERS[product.builder](resolve(product, values))


__all__ = [
    "BUILDERS",
    "Blank",
    "Catalog",
    "CatalogError",
    "Category",
    "Param",
    "Product",
    "build",
    "load",
    "resolve",
]
