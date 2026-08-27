"""כתיבת DXF — מהפריסה שתומחרה אל המכונה.

**שני קבצים, ולא אחד, כי אלה שתי מכונות.** הלייזר והמכבש בימיש הם
מכשירים נפרדים, ולכן קובץ החיתוך כאן מכיל **גיאומטריה בלבד**: מתאר
חיצוני, חורים וחיתוכים פנימיים, בשכבה אחת. אין בו קווי כיפוף, אין
בו טקסט, ואין בו שכבת מידות. זו אינה קפדנות אסתטית — **קו כיפוף
שנשלח ללייזר נחתך**, והחלק יוצא משם חצוי. מי שמייצא DXF "שלם" עם
שכבת BEND ומזין אותו לתוכנת חיתוך שאינה מסננת שכבות, הורס את החומר
ואת הזמן ומגלה את זה רק כשהוא מרים את החלק.

דף הכיפוף הוא קובץ שני ונפרד: אותה פריסה כמתאר עזר, קווי הכיפוף
עליה, וטקסט שאומר לאיזו זווית ולאיזה כיוון. הוא נועד למכבש ולעין
של המפעיל, לא ללייזר.

**מה שלא נכנס לקובץ, ואין על זה משא ומתן:** שם לקוח, שם משתמש, שם
הקובץ שהועלה, מספר הצעה, ומחיר. DXF נוסע — בוואטסאפ, בדיסק-און-קי,
אצל קבלן משנה — והוא הדבר הכי קל בעולם לפתוח בפנקס רשימות. הוא
נושא צורה, ותו לא.
"""

from __future__ import annotations

import io
from typing import Any

import ezdxf

CUT_LAYER = "CUT"
BEND_LAYER = "BEND"
OUTLINE_LAYER = "OUTLINE"
TEXT_LAYER = "BEND_TEXT"

DXF_VERSION = "R2010"
"""**לא הגרסה החדשה ביותר, בכוונה.**

R2010 היא הגרסה שכל תוכנת CAM שראיתי קוראת בלי תלונה, כולל בקרים
ישנים. גרסה חדשה יותר אינה מוסיפה כאן דבר — אנחנו כותבים קווים,
קשתות ועיגולים — והיא כן מוסיפה סיכוי שהמכונה תגיד "קובץ לא נתמך"
בבוקר שבו צריך לחתוך.
"""


class DxfError(ValueError):
    """פריסה שאי אפשר לכתוב ממנה קובץ."""


def _new_doc() -> Any:
    doc = ezdxf.new(DXF_VERSION, setup=True)
    # **4 = מילימטרים.** בלי זה תוכנות מסוימות מניחות אינץ' ומקבלות
    # חלק גדול פי 25.4 — שגיאה שנראית מגוחכת ובכל זאת קורית, כי
    # הקובץ עצמו תקין לחלוטין.
    doc.units = 4
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1
    return doc


def _outline_points(spec: dict) -> list[tuple[float, float]]:
    shape = spec.get("shape", "rect")
    if shape == "polygon":
        points = [(float(x), float(y)) for x, y in spec["points"]]
        if len(points) < 3:
            raise DxfError("מצולע עם פחות משלוש נקודות אינו מתאר.")
        return points
    if shape == "rect":
        w, h = float(spec["width_mm"]), float(spec["height_mm"])
        return [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)]
    if shape == "circle":
        return []
    raise DxfError(f"צורה שאין ממנה קובץ: {shape}")


def _draw_geometry(msp: Any, spec: dict, layer: str) -> None:
    """המתאר, החורים והחיתוכים הפנימיים — כולם באותה שכבה.

    **חור נכתב כ-`CIRCLE` ולא כמצולע.** הלייזר חותך קשת בצורה חלקה
    יותר ממאה מיתרים, והקובץ יוצא קטן פי כמה. מצולע שמתחזה לעיגול
    נראה זהה על המסך ומשאיר פאות על החלק.
    """
    attribs = {"layer": layer}
    points = _outline_points(spec)
    if points:
        msp.add_lwpolyline(points, close=True, dxfattribs=attribs)
    elif spec.get("shape") == "circle":
        msp.add_circle((0.0, 0.0), float(spec["diameter_mm"]) / 2.0, dxfattribs=attribs)

    for hole in spec.get("holes", []) or []:
        msp.add_circle(
            (float(hole["x_mm"]), float(hole["y_mm"])),
            float(hole["diameter_mm"]) / 2.0,
            dxfattribs=attribs,
        )
    for cutout in spec.get("cutouts", []) or []:
        msp.add_lwpolyline(
            [(float(x), float(y)) for x, y in cutout], close=True, dxfattribs=attribs
        )


def _to_bytes(doc: Any) -> bytes:
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


def cut_dxf(spec: dict) -> bytes:
    """קובץ החיתוך. **גיאומטריה בלבד, שכבה אחת, בלי קווי כיפוף.**

    זה הקובץ שנכנס ללייזר. כל מה שאינו חיתוך נשאר בחוץ — לא בגלל
    ניקיון אלא בגלל שכל ישות שנשארת בקובץ היא ישות שמכונה כלשהי
    עלולה לחתוך.
    """
    doc = _new_doc()
    doc.layers.add(CUT_LAYER, color=1)
    _draw_geometry(doc.modelspace(), spec, CUT_LAYER)
    return _to_bytes(doc)


def bend_dxf(
    spec: dict,
    fold_lines: list[list[float]],
    *,
    title: str = "",
    thickness_mm: float | None = None,
    angle_deg: float = 90.0,
) -> bytes:
    """דף הכיפוף למכבש — פריסה כעזר, קווי כיפוף, וטקסט.

    המתאר כאן הוא **עזר ולא הוראת חיתוך**, ולכן הוא בשכבה נפרדת
    בשם `OUTLINE`. מי שיזין את הקובץ הזה בטעות ללייזר עדיין יראה
    שכבה בשם BEND ויידע שמשהו לא במקומו; זו לא הגנה, אבל זה ההבדל
    בין טעות שנתפסת לטעות שנחתכת.
    """
    doc = _new_doc()
    doc.layers.add(OUTLINE_LAYER, color=8)
    doc.layers.add(BEND_LAYER, color=5)
    doc.layers.add(TEXT_LAYER, color=3)
    msp = doc.modelspace()
    _draw_geometry(msp, spec, OUTLINE_LAYER)

    height = _text_height(spec)
    for index, line in enumerate(fold_lines, start=1):
        x1, y1, x2, y2 = (float(v) for v in line[:4])
        msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": BEND_LAYER})
        label = f"BEND {index}  {angle_deg:g}deg  UP"
        msp.add_text(
            label,
            height=height,
            dxfattribs={"layer": TEXT_LAYER},
        ).set_placement(((x1 + x2) / 2.0, (y1 + y2) / 2.0 + height * 0.4))

    # **הכותרת באנגלית ובלי שם לקוח.** עברית ב-DXF נשענת על גופן
    # שקיים אצל הקורא, ובקר של מכבש אינו דפדפן; מחרוזת שנשברת שם
    # הופכת להוראה בלתי קריאה. המידע שחייב לעבור הוא מספרי.
    facts = [f"{len(fold_lines)} bends"]
    if thickness_mm:
        facts.append(f"t={thickness_mm:g}mm")
    if title:
        facts.append(title)
    msp.add_text(
        "  |  ".join(facts),
        height=height,
        dxfattribs={"layer": TEXT_LAYER},
    ).set_placement((0.0, -height * 2.2))
    return _to_bytes(doc)


def _text_height(spec: dict) -> float:
    """גובה טקסט ביחס לחלק, כדי שכיתוב לא יבלע לוחית 40 מ"מ."""
    points = _outline_points(spec)
    if not points:
        return 3.0
    span = max(
        max(p[0] for p in points) - min(p[0] for p in points),
        max(p[1] for p in points) - min(p[1] for p in points),
    )
    return max(2.0, min(12.0, span / 40.0))


__all__ = ["BEND_LAYER", "CUT_LAYER", "DxfError", "bend_dxf", "cut_dxf"]
